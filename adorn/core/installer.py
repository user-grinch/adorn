"""Asynchronous installation and uninstallation engine for Adorn."""

import json
import math
import os
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple
from gi.repository import GLib

from adorn.core.models import CustomizationItem, DownloadableFile, ItemCategory, LocalAsset
from adorn.core.task_manager import TaskEntry, TaskManager

if TYPE_CHECKING:
    from adorn.core.gsettings import GSettingsManager


# Global resilient DNS resolution for CDN77 Anycast fallback (fixes timeout on local edge caches)
_orig_getaddrinfo = socket.getaddrinfo
FALLBACK_CDN_IPS = ["207.211.213.90", "37.19.218.108"]

def _safe_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == "cdn.extensions.gnome.org":
        res = []
        for ip in FALLBACK_CDN_IPS:
            res.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
        return res
    return _orig_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = _safe_getaddrinfo


def format_bytes(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def compute_path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = Path(root) / f
            try:
                if not fp.is_symlink():
                    total += fp.stat().st_size
            except OSError:
                pass
    return total


class InstallationEngine:
    """
    Handles downloading, extracting, installing, and uninstalling themes, icons, cursors,
    wallpapers, and GNOME Shell extensions with real-time TaskManager progress reporting.
    """

    def __init__(self):
        self.home = Path.home()
        self.themes_dir = self.home / ".local" / "share" / "themes"
        self.icons_dir = self.home / ".local" / "share" / "icons"
        self.wallpapers_dir = self.home / "Pictures" / "Wallpapers"
        self.extensions_dir = self.home / ".local" / "share" / "gnome-shell" / "extensions"
        self.task_manager = TaskManager.get_default()

        for d in (self.themes_dir, self.icons_dir, self.wallpapers_dir, self.extensions_dir):
            d.mkdir(parents=True, exist_ok=True)

    def start_install_task(
        self,
        item: CustomizationItem,
        file_info: Optional[DownloadableFile] = None,
        progress_cb: Optional[Callable[[float, str], None]] = None,
        done_cb: Optional[Callable[[bool, str], None]] = None,
    ) -> TaskEntry:
        """Starts an asynchronous installation tracked by TaskManager."""
        existing = self.task_manager.get_task_for_item(item.item_id)
        if existing:
            return existing

        target_file = file_info or (item.download_files[0] if item.download_files else None)
        tid = f"dl_{item.item_id}_{int(time.time() * 1000)}"
        thumb = item.thumbnail_url or (item.screenshots[0] if item.screenshots else None)
        task = TaskEntry(
            task_id=tid,
            item_id=str(item.item_id),
            name=item.name,
            category=item.category,
            thumbnail_url=thumb,
            item=item,
            file_info=target_file,
            status="downloading",
            progress=0.04,
            status_text="Connecting…",
        )
        self.task_manager.tasks[tid] = task
        self.task_manager.notify(task)

        def worker():
            nonlocal target_file
            if not target_file or not target_file.url:
                if item.category == ItemCategory.EXTENSIONS and (item.uuid or item.item_id):
                    target_file = DownloadableFile(
                        file_id="remote",
                        name=item.uuid or item.item_id or item.name,
                        download_url=f"https://extensions.gnome.org/extension/{item.uuid or item.item_id}",
                    )
                else:
                    task.status = "failed"
                    task.status_text = "No download URL available"
                    task.error_message = task.status_text
                    self.task_manager.notify(task)
                    if done_cb:
                        GLib.idle_add(done_cb, False, task.status_text)
                    return

            def internal_progress(frac: float, msg: str, dl_bytes: int = 0, tot_bytes: int = 0):
                task.progress = max(0.02, min(1.0, frac))
                task.status_text = msg
                task.downloaded_bytes = dl_bytes
                task.total_bytes = tot_bytes
                prev_status = task.status
                if 0.90 <= frac < 0.99:
                    task.status = "extracting"
                elif frac >= 0.99:
                    task.status = "installing"

                if task.status != prev_status:
                    self.task_manager.notify_state_change(task)
                else:
                    now = time.time()
                    if now - getattr(task, "_last_progress_time", 0.0) >= 0.10:
                        task._last_progress_time = now
                        self.task_manager.notify_progress(task)

                if progress_cb:
                    GLib.idle_add(progress_cb, task.progress, msg)

            if item.category == ItemCategory.EXTENSIONS:
                ok, msg, paths, theme_name = self._install_extension_internal(
                    item, target_file, internal_progress, task.cancel_event
                )
            else:
                ok, msg, paths, theme_name = self._install_archive_or_media(
                    target_file, item.category, internal_progress, task.cancel_event, item.name
                )

            if task.cancel_event.is_set():
                task.status = "cancelled"
                task.status_text = "Cancelled"
                self.task_manager.notify(task)
                if done_cb:
                    GLib.idle_add(done_cb, False, "Installation cancelled")
                return

            if ok:
                task.status = "done"
                task.progress = 1.0
                task.status_text = f"Installed ({theme_name or item.name})"
                task.installed_paths = paths
                task.installed_theme_name = theme_name or item.name
                item.is_installed = True
                self.task_manager.register_installation(item, paths, task.installed_theme_name)
                try:
                    from adorn.core.gsettings import GSettingsManager
                    GSettingsManager.get_default().invalidate_asset_cache()
                except Exception:
                    pass
                self.task_manager.notify(task)
                if done_cb:
                    GLib.idle_add(done_cb, True, msg)
            else:
                task.status = "failed"
                task.status_text = msg
                task.error_message = msg
                self.task_manager.notify(task)
                if done_cb:
                    GLib.idle_add(done_cb, False, msg)

        threading.Thread(target=worker, daemon=True).start()
        return task

    def retry_task(self, failed_task: TaskEntry) -> Optional[TaskEntry]:
        """Retries a failed or cancelled download task."""
        if not failed_task.item:
            return None
        self.task_manager.dismiss_task(failed_task.task_id)
        return self.start_install_task(
            item=failed_task.item,
            file_info=failed_task.file_info,
        )

    def install_downloadable(
        self,
        file_info: DownloadableFile,
        category: ItemCategory,
        progress_cb: Optional[Callable[[float, str], None]] = None,
    ) -> Tuple[bool, str]:
        def wrap_cb(frac: float, msg: str, dl_b: int = 0, tot_b: int = 0):
            if progress_cb:
                progress_cb(frac, msg)

        ok, msg, _, _ = self._install_archive_or_media(
            file_info, category, wrap_cb, threading.Event(), file_info.name
        )
        return ok, msg

    def _install_extension_internal(
        self,
        item: CustomizationItem,
        file_info: DownloadableFile,
        progress_cb: Callable[[float, str, int, int], None],
        cancel_event: threading.Event,
    ) -> Tuple[bool, str, List[str], str]:
        ext_uuid = item.uuid or item.item_id or ""

        # 1. Primary method: Native dynamic GNOME Shell D-Bus installation
        # This matches GNOME Extension Manager / extensions.gnome.org behavior.
        # GNOME Shell directly downloads, verifies, compiles schemas, registers,
        # loads, and activates the extension in the live compositor runtime
        # without requiring the user to log out and log in.
        if ext_uuid:
            try:
                from adorn.core.gsettings import GSettingsManager
                gsm = GSettingsManager.get_default()
                proxy = gsm._get_shell_extensions_proxy()
                if proxy and proxy.get_name_owner():
                    progress_cb(0.15, "Confirm installation in GNOME Shell prompt…", 0, 0)
                    ok, res_msg = gsm.install_remote_extension(ext_uuid)
                    if ok:
                        dest = self.extensions_dir / ext_uuid
                        progress_cb(1.0, f"Installed {item.name}", 0, 0)
                        return True, f"Installed {item.name}", [str(dest)], ext_uuid
                    elif "cancelled" in res_msg.lower():
                        return False, "Installation cancelled by user", [], ""
                    else:
                        print(f"[Installer] InstallRemoteExtension failed ({res_msg}), falling back to direct package extraction")
            except Exception as e:
                print(f"[Installer] D-Bus extension installation failed: {e}, falling back to direct extraction")

        if cancel_event.is_set():
            return False, "Cancelled", [], ""

        # 2. Fallback: Direct archive download and manual staging
        # Used when D-Bus is unavailable (e.g., non-GNOME desktop, headless, or mock environment)
        tmp_dir = Path(tempfile.mkdtemp(prefix="adorn_ext_"))
        try:
            dl_path = tmp_dir / f"{ext_uuid or 'extension'}.shell-extension.zip"
            progress_cb(0.05, f"Connecting to extensions server…", 0, 0)

            req = urllib.request.Request(
                file_info.url,
                headers={"User-Agent": "Adorn/1.0 (GNOME Customization Store; com.grinch.gnome.adorn)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0) or 0)
                downloaded = 0
                with open(dl_path, "wb") as f:
                    while True:
                        if cancel_event.is_set():
                            return False, "Cancelled", [], ""
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = int((downloaded / total) * 100)
                            frac = 0.05 + 0.82 * (downloaded / total)
                            msg = f"{pct}% • {format_bytes(downloaded)} / {format_bytes(total)}"
                        else:
                            frac = 0.05 + 0.80 * (1.0 - math.exp(-downloaded / (1024 * 1024)))
                            msg = f"{format_bytes(downloaded)} downloaded"
                        progress_cb(frac, msg, downloaded, total)

            if cancel_event.is_set():
                return False, "Cancelled", [], ""

            progress_cb(0.90, "Extracting and verifying extension…", downloaded, total)

            extract_staging = tmp_dir / "unpacked"
            extract_staging.mkdir(exist_ok=True)
            with zipfile.ZipFile(dl_path, "r") as zf:
                zf.extractall(extract_staging)

            # Find metadata.json anywhere in extracted files (handles outer container folders)
            meta_path = None
            for p in extract_staging.rglob("metadata.json"):
                if p.is_file():
                    meta_path = p
                    break

            if not meta_path:
                return False, "Invalid extension package: metadata.json not found", [], ""

            source_dir = meta_path.parent
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                return False, f"Invalid metadata.json in extension: {e}", [], ""

            real_uuid = meta.get("uuid") or ext_uuid
            if not real_uuid:
                return False, "Extension metadata.json does not specify a UUID", [], ""

            # Ensure current GNOME Shell version compatibility in metadata.json
            try:
                from adorn.core.ego_client import EgoClient
                current_shell = EgoClient().shell_version
                supported = [str(v) for v in meta.get("shell-version", [])]
                if current_shell not in supported and not any(v.startswith(f"{current_shell}.") for v in supported):
                    supported.append(current_shell)
                    meta["shell-version"] = supported
                    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            except Exception:
                pass

            dest = self.extensions_dir / real_uuid
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True, exist_ok=True)

            for child in source_dir.iterdir():
                target_dest = dest / child.name
                if child.is_dir():
                    shutil.copytree(child, target_dest)
                else:
                    shutil.copy2(child, target_dest)

            # Compile schemas if schemas directory exists
            schemas_dir = dest / "schemas"
            if schemas_dir.is_dir():
                try:
                    subprocess.run(
                        ["glib-compile-schemas", str(schemas_dir)],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                except Exception:
                    pass

            progress_cb(0.96, "Enabling extension…", downloaded, total)

            # Enable via gnome-extensions CLI
            try:
                subprocess.run(["gnome-extensions", "enable", real_uuid], capture_output=True, timeout=10)
            except Exception:
                pass

            # Also ensure enabled in GSettings and cache invalidated
            try:
                from adorn.core.gsettings import GSettingsManager
                gsm = GSettingsManager.get_default()
                gsm.toggle_extension(real_uuid, True)
                gsm.invalidate_asset_cache()
            except Exception:
                pass

            progress_cb(1.0, f"Installed {item.name}", downloaded, total)
            return True, f"Installed {item.name}", [str(dest)], real_uuid
        except Exception as e:
            return False, str(e), [], ""
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _install_archive_or_media(
        self,
        file_info: DownloadableFile,
        category: ItemCategory,
        progress_cb: Callable[[float, str, int, int], None],
        cancel_event: threading.Event,
        fallback_name: str = "",
    ) -> Tuple[bool, str, List[str], str]:
        tmp_dir = Path(tempfile.mkdtemp(prefix="adorn_dl_"))
        try:
            safe_filename = os.path.basename(file_info.name) or "package_download"
            download_path = tmp_dir / safe_filename

            progress_cb(0.05, f"Downloading {safe_filename}…", 0, 0)

            req = urllib.request.Request(
                file_info.url,
                headers={"User-Agent": "Adorn/1.0 (GNOME Customization Store; com.grinch.gnome.adorn)"},
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                total_size = int(resp.headers.get("Content-Length", 0) or 0)
                downloaded = 0
                chunk_size = 65536
                with open(download_path, "wb") as out:
                    while True:
                        if cancel_event.is_set():
                            return False, "Cancelled", [], ""
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            pct = int((downloaded / total_size) * 100)
                            frac = 0.05 + 0.83 * (downloaded / total_size)
                            status_msg = f"{pct}% • {format_bytes(downloaded)} / {format_bytes(total_size)}"
                        else:
                            frac = 0.05 + 0.80 * (1.0 - math.exp(-downloaded / (4 * 1024 * 1024)))
                            status_msg = f"{format_bytes(downloaded)} downloaded"
                        progress_cb(frac, status_msg, downloaded, total_size)

            if cancel_event.is_set():
                return False, "Cancelled", [], ""

            if not download_path.exists() or download_path.stat().st_size == 0:
                return False, "Downloaded package file is empty", [], ""

            # Check if server returned an HTML error/webpage instead of a binary archive
            try:
                with open(download_path, "rb") as test_f:
                    header = test_f.read(512)
                if header.strip().startswith((b"<!DOCTYPE", b"<!doctype", b"<html", b"<HTML")):
                    return False, f"File '{safe_filename}' is an external webpage or link rather than an installable archive.", [], ""
            except Exception:
                pass

            if category == ItemCategory.WALLPAPERS and self._is_image_file(download_path):
                progress_cb(0.92, "Saving wallpaper…", downloaded, downloaded)
                dest = self.wallpapers_dir / safe_filename
                if dest.exists():
                    stem = dest.stem
                    suffix = dest.suffix
                    dest = self.wallpapers_dir / f"{stem}_{int(time.time())}{suffix}"
                shutil.copy2(download_path, dest)
                progress_cb(1.0, f"Saved wallpaper {dest.name}", downloaded, downloaded)
                return True, str(dest), [str(dest)], dest.name

            progress_cb(0.90, "Extracting archive…", downloaded, total_size or downloaded)
            extract_dir = tmp_dir / "extracted"
            extract_dir.mkdir(exist_ok=True)
            self._extract_archive(download_path, extract_dir)

            if cancel_event.is_set():
                return False, "Cancelled", [], ""

            progress_cb(0.95, "Installing files to system…", downloaded, total_size or downloaded)
            installed_names, installed_paths = self._deploy_extracted_files(
                extract_dir, category, fallback_name
            )

            if not installed_names:
                return False, "No valid theme, icon, cursor, or wallpaper files found in archive", [], ""

            summary = ", ".join(installed_names[:3])
            progress_cb(1.0, f"Installed: {summary}", downloaded, total_size or downloaded)
            return True, summary, installed_paths, installed_names[0]

        except Exception as e:
            return False, f"Installation error: {e}", [], ""
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def uninstall_asset(
        self,
        asset: LocalAsset,
        active_check_cb: Optional[Callable[[LocalAsset], None]] = None,
    ) -> Tuple[bool, str, int]:
        """Uninstalls a user LocalAsset, calculates freed bytes, and records history."""
        if asset.is_system:
            return False, "System-wide assets in /usr/share cannot be removed without root privileges.", 0

        freed = 0
        label = asset.display_name or asset.name
        try:
            if asset.category == ItemCategory.EXTENSIONS:
                ext_dir = Path(asset.path) if asset.path else (self.extensions_dir / asset.name)
                freed = compute_path_size(ext_dir)
                uninstalled = False
                try:
                    from adorn.core.gsettings import GSettingsManager
                    uninstalled = GSettingsManager.get_default().uninstall_extension(asset.name)
                except Exception:
                    pass
                if not uninstalled:
                    res = subprocess.run(
                        ["gnome-extensions", "uninstall", asset.name],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if res.returncode != 0 and ext_dir.exists():
                        shutil.rmtree(ext_dir, ignore_errors=True)
            else:
                target = Path(asset.path)
                if target.exists():
                    freed = compute_path_size(target)
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink(missing_ok=True)

            if active_check_cb:
                active_check_cb(asset)

            self.task_manager.unregister_installation(theme_name=asset.name, path=asset.path)
            self.task_manager.record_uninstalled(
                name=label,
                category=asset.category,
                freed_bytes=freed,
                status_text=f"Removed {label}",
            )
            return True, f"Removed {label} ({format_bytes(freed)} freed)", freed
        except Exception as e:
            return False, f"Failed to remove {label}: {e}", 0

    def uninstall_customization_item(
        self,
        item: CustomizationItem,
        gsettings: "GSettingsManager",
    ) -> Tuple[bool, str, int]:
        matches = gsettings.find_matching_local_assets(item)
        if matches:
            total_freed = 0
            for asset in matches:
                if not asset.is_system:
                    ok, _, freed = self.uninstall_asset(asset, active_check_cb=gsettings.reset_if_active)
                    if ok:
                        total_freed += freed
            item.is_installed = False
            item.is_active = False
            self.task_manager.unregister_installation(item_id=item.item_id)
            gsettings.invalidate_asset_cache()
            return True, f"Removed {item.name}", total_freed

        item.is_installed = False
        self.task_manager.unregister_installation(item_id=item.item_id)
        gsettings.invalidate_asset_cache()
        return False, f"Could not find user-installed files for {item.name}", 0

    @staticmethod
    def _is_image_file(path: Path) -> bool:
        ext = path.suffix.lower()
        return ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".svg")

    def _extract_archive(self, archive_path: Path, dest_dir: Path) -> None:
        name_lower = archive_path.name.lower()
        has_tar = bool(shutil.which("tar"))
        has_unzip = bool(shutil.which("unzip"))
        has_7z = bool(shutil.which("7z"))

        # Check magic bytes to identify archive type reliably
        magic = b""
        try:
            with open(archive_path, "rb") as mf:
                magic = mf.read(16)
        except Exception:
            pass

        is_zip = magic.startswith(b"PK\x03\x04") or zipfile.is_zipfile(archive_path) or name_lower.endswith(".zip")
        is_tar = (
            magic.startswith(b"\x1f\x8b")  # gzip
            or magic.startswith(b"\xfd7zXZ\x00")  # xz
            or magic.startswith(b"BZh")  # bzip2
            or any(name_lower.endswith(ext) for ext in (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar"))
            or tarfile.is_tarfile(archive_path)
        )
        is_7z = magic.startswith(b"7z\xbc\xaf\x27\x1c") or name_lower.endswith(".7z")

        if is_zip:
            if has_unzip:
                try:
                    subprocess.run(
                        ["unzip", "-q", "-o", str(archive_path), "-d", str(dest_dir)],
                        check=True,
                        timeout=90,
                    )
                    return
                except Exception:
                    pass
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(dest_dir)
            return

        if is_tar:
            if has_tar:
                try:
                    subprocess.run(
                        ["tar", "-xf", str(archive_path), "-C", str(dest_dir)],
                        check=True,
                        timeout=90,
                    )
                    return
                except Exception:
                    pass
            with tarfile.open(archive_path, "r:*") as tf:
                tf.extractall(dest_dir)
            return

        if is_7z and has_7z:
            subprocess.run(
                ["7z", "x", str(archive_path), f"-o{dest_dir}", "-y"],
                check=True,
                timeout=90,
            )
            return

        # Fallback tries
        if has_tar:
            try:
                subprocess.run(["tar", "-xf", str(archive_path), "-C", str(dest_dir)], check=True, timeout=90)
                return
            except Exception:
                pass
        try:
            with tarfile.open(archive_path, "r:*") as tf:
                tf.extractall(dest_dir)
                return
        except Exception:
            pass
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)

    def _deploy_extracted_files(
        self,
        extract_dir: Path,
        category: ItemCategory,
        fallback_name: str = "",
    ) -> Tuple[List[str], List[str]]:
        installed_names: List[str] = []
        installed_paths: List[str] = []

        if category == ItemCategory.WALLPAPERS:
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    fpath = Path(root) / file
                    if self._is_image_file(fpath):
                        dest = self.wallpapers_dir / fpath.name
                        shutil.copy2(fpath, dest)
                        installed_names.append(dest.name)
                        installed_paths.append(str(dest))
            return installed_names, installed_paths

        candidate_dirs: List[Tuple[Path, Path]] = []
        for root, dirs, files in os.walk(extract_dir):
            root_path = Path(root)
            has_index = "index.theme" in files
            has_cursors = "cursors" in dirs
            has_gtk = any(d.startswith("gtk-") for d in dirs)
            has_shell = "gnome-shell" in dirs

            if category == ItemCategory.CURSORS:
                has_cursor_content = "cursors" in dirs or "cursors_scalable" in dirs or "hyprcursors" in dirs or "cursor.theme" in files
                if has_cursor_content or has_index:
                    candidate_dirs.append((root_path, self.icons_dir))
                    dirs.clear()
            elif category == ItemCategory.ICONS:
                has_icon_dirs = any(d in dirs for d in ("apps", "scalable", "places", "status", "16x16", "48x48", "symbolic"))
                if has_index or has_icon_dirs:
                    candidate_dirs.append((root_path, self.icons_dir))
                    dirs.clear()
            elif category in (ItemCategory.GTK_THEMES, ItemCategory.SHELL_THEMES):
                if has_gtk or has_shell or has_index:
                    candidate_dirs.append((root_path, self.themes_dir))
                    dirs.clear()

        target_root = (
            self.icons_dir
            if category in (ItemCategory.ICONS, ItemCategory.CURSORS)
            else self.themes_dir
        )

        if not candidate_dirs:
            top_items = [p for p in extract_dir.iterdir() if p.is_dir()]
            if top_items:
                for p in top_items:
                    candidate_dirs.append((p, target_root))
            elif fallback_name:
                safe_dir = "".join(c if c.isalnum() or c in "-_." else "-" for c in fallback_name).strip("-")
                dest = target_root / (safe_dir or "Custom-Theme")
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                try:
                    shutil.move(str(extract_dir), str(dest))
                except Exception:
                    shutil.copytree(extract_dir, dest)
                return [dest.name], [str(dest)]

        for src_dir, target_parent in candidate_dirs:
            folder_name = src_dir.name
            if folder_name == "extracted" and fallback_name:
                folder_name = "".join(c if c.isalnum() or c in "-_." else "-" for c in fallback_name).strip("-")
            dest = target_parent / folder_name
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            try:
                shutil.move(str(src_dir), str(dest))
            except Exception:
                shutil.copytree(src_dir, dest)

            # Post-processing: If cursor theme lacks index.theme, generate one
            if category == ItemCategory.CURSORS or (dest / "cursors").is_dir():
                idx_file = dest / "index.theme"
                if not idx_file.exists():
                    try:
                        idx_file.write_text(
                            f"[Icon Theme]\nName={dest.name}\nComment={dest.name} Cursor Theme\nInherits=Adwaita\n",
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
            elif category == ItemCategory.ICONS:
                try:
                    from adorn.core.gsettings import GSettingsManager
                    GSettingsManager._ensure_icon_theme_index(dest, dest.name)
                except Exception:
                    pass

            # Update icon cache if tool exists
            if category in (ItemCategory.ICONS, ItemCategory.CURSORS):
                try:
                    subprocess.run(
                        ["gtk-update-icon-cache", "-q", "-t", "-f", str(dest)],
                        check=False,
                        timeout=10,
                    )
                except Exception:
                    pass

            installed_names.append(dest.name)
            installed_paths.append(str(dest))

        return installed_names, installed_paths
