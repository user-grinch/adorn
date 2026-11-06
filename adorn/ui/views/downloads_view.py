"""Dedicated Downloads & Activity Center view for Adorn."""

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from adorn.core.gsettings import GSettingsManager
from adorn.core.installer import InstallationEngine, format_bytes
from adorn.core.models import CustomizationItem, ItemCategory
from adorn.core.ocs_client import OcsClient
from adorn.core.task_manager import TaskEntry, TaskManager
from adorn.ui.image_utils import format_relative_time, load_fixed_square_texture_async


class DownloadsView(Gtk.Box):
    """
    First-class Downloads & Installation Activity View:
    - Active Transfers with smooth in-place progress bars & Cancel
    - Completed Installations with 1-click Apply/Enable, Open Folder, and View Details
    - Failed / Cancelled Transfers with 1-click Retry & Dismiss
    - Recently Removed items with freed disk space readout
    """

    def __init__(
        self,
        installer: InstallationEngine,
        gsettings: GSettingsManager,
        ocs_client: OcsClient,
        on_browse_store: Callable[[], None],
        on_open_detail: Optional[Callable[[CustomizationItem], None]],
        toast_cb: Callable[[str], None],
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.installer = installer
        self.gsettings = gsettings
        self.ocs = ocs_client
        self.on_browse_store = on_browse_store
        self.on_open_detail = on_open_detail
        self.toast_cb = toast_cb
        self.task_manager = TaskManager.get_default()

        # Map task_id -> (ProgressBar, status_label) for flicker-free progress updates
        self._active_row_widgets: Dict[str, Tuple[Gtk.ProgressBar, Gtk.Label]] = {}

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.append(self.stack)

        # 1. Empty State
        empty_page = Adw.StatusPage(
            icon_name="folder-download-symbolic",
            title="No Recent Downloads",
            description="Packages you install from Explore will appear here with live progress and quick actions.",
        )
        browse_btn = Gtk.Button(label="Browse Storefront")
        browse_btn.add_css_class("pill")
        browse_btn.add_css_class("suggested-action")
        browse_btn.set_halign(Gtk.Align.CENTER)
        browse_btn.connect("clicked", lambda _b: self.on_browse_store())
        empty_page.set_child(browse_btn)
        self.stack.add_named(empty_page, "empty")

        # 2. Content Scroller
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)

        clamp = Adw.Clamp(maximum_size=880, tightening_threshold=600)
        scroller.set_child(clamp)

        main_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        main_col.set_margin_top(20)
        main_col.set_margin_bottom(36)
        main_col.set_margin_start(20)
        main_col.set_margin_end(20)
        clamp.set_child(main_col)

        # Top Header Row
        hdr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hdr_row.set_valign(Gtk.Align.CENTER)

        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_col.set_hexpand(True)
        title_lbl = Gtk.Label(label="Downloads & Activity")
        title_lbl.add_css_class("title-2")
        title_lbl.set_halign(Gtk.Align.START)
        title_col.append(title_lbl)

        self.sub_summary_lbl = Gtk.Label(label="")
        self.sub_summary_lbl.add_css_class("caption")
        self.sub_summary_lbl.add_css_class("dim-label")
        self.sub_summary_lbl.set_halign(Gtk.Align.START)
        title_col.append(self.sub_summary_lbl)
        hdr_row.append(title_col)

        self.clear_btn = Gtk.Button()
        self.clear_btn.add_css_class("flat")
        self.clear_btn.set_valign(Gtk.Align.CENTER)
        self.clear_btn.set_child(
            Adw.ButtonContent(icon_name="edit-clear-symbolic", label="Clear Finished")
        )
        self.clear_btn.connect("clicked", lambda _b: self.task_manager.clear_finished_tasks())
        hdr_row.append(self.clear_btn)
        main_col.append(hdr_row)

        # Dynamic Groups Container
        self.groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        main_col.append(self.groups_box)

        self.stack.add_named(scroller, "content")

        self.task_manager.subscribe(self.rebuild)
        self.task_manager.subscribe_task(self._on_task_progress)
        self.rebuild()

    @staticmethod
    def _clear_box(box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _on_task_progress(self, task: TaskEntry) -> None:
        widgets = self._active_row_widgets.get(task.task_id)
        if widgets and task.status in ("downloading", "extracting", "installing"):
            pbar, stat_lbl = widgets
            phase = {
                "downloading": "Downloading",
                "extracting": "Extracting",
                "installing": "Installing",
            }.get(task.status, "Working")

            if task.status == "downloading":
                pct = int(task.progress * 100)
                pbar.set_fraction(max(0.03, min(1.0, task.progress)))
                pbar.set_text(f"{pct}%")
            elif task.status == "extracting":
                pbar.pulse()
                pbar.set_text("Extracting…")
            else:
                pbar.set_fraction(0.96)
                pbar.set_text("Installing…")

            stat_lbl.set_label(f"{phase} • {task.status_text}")

    def rebuild(self) -> None:
        self._active_row_widgets.clear()
        self._clear_box(self.groups_box)

        active = self.task_manager.get_active_downloads()
        completed = self.task_manager.get_completed_downloads()
        failed = self.task_manager.get_failed_downloads()
        removed = self.task_manager.get_recently_uninstalled()

        total_count = len(active) + len(completed) + len(failed) + len(removed)
        if total_count == 0:
            self.stack.set_visible_child_name("empty")
            return

        self.stack.set_visible_child_name("content")
        self.clear_btn.set_visible(bool(completed or failed or removed))

        parts = []
        if active:
            parts.append(f"{len(active)} active")
        if completed:
            parts.append(f"{len(completed)} installed")
        if failed:
            parts.append(f"{len(failed)} failed")
        if removed:
            parts.append(f"{len(removed)} removed")
        self.sub_summary_lbl.set_label(" • ".join(parts))

        if active:
            grp = Adw.PreferencesGroup()
            grp.set_title(f"Active Transfers ({len(active)})")
            for entry in active:
                grp.add(self._build_active_row(entry))
            self.groups_box.append(grp)

        if failed:
            grp = Adw.PreferencesGroup()
            grp.set_title("Failed or Cancelled")
            for entry in failed:
                grp.add(self._build_failed_row(entry))
            self.groups_box.append(grp)

        if completed:
            cached_assets = self.gsettings.list_installed_assets()
            grp = Adw.PreferencesGroup()
            grp.set_title(f"Installed from Store ({len(completed)})")
            for entry in completed:
                grp.add(self._build_completed_row(entry, cached_assets))
            self.groups_box.append(grp)

        if removed:
            grp = Adw.PreferencesGroup()
            grp.set_title("Recently Removed")
            for entry in removed:
                grp.add(self._build_removed_row(entry))
            self.groups_box.append(grp)

    def _make_prefix_thumbnail(self, entry: TaskEntry) -> Gtk.Widget:
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("download-icon-frame")
        frame.set_size_request(44, 44)
        frame.set_valign(Gtk.Align.CENTER)
        frame.set_halign(Gtk.Align.START)
        frame.set_hexpand(False)
        frame.set_vexpand(False)
        frame.set_margin_top(6)
        frame.set_margin_bottom(6)
        frame.set_overflow(Gtk.Overflow.HIDDEN)

        fb = Gtk.Image.new_from_icon_name(entry.category.icon_name)
        fb.set_pixel_size(22)
        fb.set_valign(Gtk.Align.CENTER)
        fb.set_halign(Gtk.Align.CENTER)
        fb.set_vexpand(True)
        fb.set_hexpand(True)
        frame.append(fb)

        img = Gtk.Image()
        img.set_pixel_size(44)
        img.set_size_request(44, 44)
        img.set_valign(Gtk.Align.CENTER)
        img.set_halign(Gtk.Align.CENTER)
        img.set_vexpand(False)
        img.set_hexpand(False)
        img.set_visible(False)
        frame.append(img)

        if entry.thumbnail_url:
            def _on_dl(path: Optional[str]):
                if path and os.path.exists(path):
                    def _on_tex(tex: Optional[Gdk.Texture]):
                        if tex:
                            img.set_from_paintable(tex)
                            fb.set_visible(False)
                            img.set_visible(True)
                    load_fixed_square_texture_async(path, 44, _on_tex)
            self.ocs.load_thumbnail_async(entry.thumbnail_url, _on_dl)

        return frame

    def _build_active_row(self, entry: TaskEntry) -> Gtk.Widget:
        row = Adw.PreferencesRow()
        row.set_activatable(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)
        outer.set_margin_start(14)
        outer.set_margin_end(14)
        row.set_child(outer)

        outer.append(self._make_prefix_thumbnail(entry))

        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        mid.set_valign(Gtk.Align.CENTER)
        mid.set_hexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_lbl = Gtk.Label(label=entry.name)
        name_lbl.add_css_class("heading")
        name_lbl.set_halign(Gtk.Align.START)
        name_lbl.set_hexpand(True)
        name_lbl.set_ellipsize(3)
        top.append(name_lbl)

        phase = {
            "downloading": "Downloading",
            "extracting": "Extracting",
            "installing": "Installing",
        }.get(entry.status, "Working")
        stat_lbl = Gtk.Label(label=f"{phase} • {entry.status_text}")
        stat_lbl.add_css_class("caption")
        stat_lbl.add_css_class("dim-label")
        stat_lbl.set_halign(Gtk.Align.END)
        top.append(stat_lbl)
        mid.append(top)

        pbar = Gtk.ProgressBar()
        pbar.set_show_text(True)
        pbar.set_fraction(max(0.03, min(1.0, entry.progress)))
        mid.append(pbar)
        outer.append(mid)

        self._active_row_widgets[entry.task_id] = (pbar, stat_lbl)

        stop_btn = Gtk.Button(icon_name="process-stop-symbolic")
        stop_btn.add_css_class("circular")
        stop_btn.add_css_class("flat")
        stop_btn.add_css_class("compact-btn")
        stop_btn.set_valign(Gtk.Align.CENTER)
        stop_btn.set_tooltip_text("Cancel transfer")
        stop_btn.connect("clicked", lambda _b, t=entry: t.cancel_event.set())
        outer.append(stop_btn)

        return row

    def _build_completed_row(self, entry: TaskEntry, cached_assets) -> Gtk.Widget:
        row = Adw.ActionRow()
        row.set_title(GLib.markup_escape_text(entry.name))

        when_str = format_relative_time(entry.created_at)
        pkg_part = (
            f"Installed as {entry.installed_theme_name}"
            if entry.installed_theme_name and entry.installed_theme_name != entry.name
            else entry.category.display_name
        )
        row.set_subtitle(GLib.markup_escape_text(f"{pkg_part} • {when_str}"))
        row.add_prefix(self._make_prefix_thumbnail(entry))

        if entry.item and self.on_open_detail:
            row.set_activatable(True)
            row.connect("activated", lambda _r, it=entry.item: self.on_open_detail(it))

        # Check if currently active
        is_active = False
        matched_asset = None
        for a in cached_assets:
            if (
                (entry.installed_paths and a.path in entry.installed_paths)
                or (entry.installed_theme_name and a.name.lower() == entry.installed_theme_name.lower() and a.category == entry.category)
            ):
                matched_asset = a
                if a.is_active:
                    is_active = True
                    break

        if is_active:
            badge = Gtk.Label(label="Active")
            badge.add_css_class("badge-pill")
            badge.add_css_class("badge-accent")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)
        else:
            verb = "Enable" if entry.category == ItemCategory.EXTENSIONS else "Apply"
            apply_btn = Gtk.Button()
            apply_btn.add_css_class("pill")
            apply_btn.add_css_class("compact-btn")
            apply_btn.add_css_class("suggested-action")
            apply_btn.set_valign(Gtk.Align.CENTER)
            apply_btn.set_tooltip_text(f"{verb} {entry.name}")
            apply_btn.set_child(
                Adw.ButtonContent(icon_name="object-select-symbolic", label=verb)
            )
            apply_btn.connect("clicked", lambda _b, e=entry, ma=matched_asset: self._apply_completed(e, ma))
            row.add_suffix(apply_btn)

        if entry.installed_paths:
            first_path = entry.installed_paths[0]
            if os.path.exists(first_path):
                open_btn = Gtk.Button(icon_name="folder-open-symbolic")
                open_btn.add_css_class("circular")
                open_btn.add_css_class("flat")
                open_btn.add_css_class("compact-btn")
                open_btn.set_valign(Gtk.Align.CENTER)
                open_btn.set_tooltip_text("Show in Files")
                open_btn.connect("clicked", lambda _b, p=first_path: self._open_in_files(p))
                row.add_suffix(open_btn)

        return row

    def _build_failed_row(self, entry: TaskEntry) -> Gtk.Widget:
        row = Adw.ActionRow()
        row.set_title(GLib.markup_escape_text(entry.name))
        err_txt = entry.error_message or entry.status_text or "Transfer interrupted"
        row.set_subtitle(GLib.markup_escape_text(err_txt))
        row.add_prefix(self._make_prefix_thumbnail(entry))

        if entry.item:
            retry_btn = Gtk.Button()
            retry_btn.add_css_class("pill")
            retry_btn.add_css_class("compact-btn")
            retry_btn.add_css_class("suggested-action")
            retry_btn.set_valign(Gtk.Align.CENTER)
            retry_btn.set_child(Adw.ButtonContent(icon_name="view-refresh-symbolic", label="Retry"))
            retry_btn.connect("clicked", lambda _b, e=entry: self.installer.retry_task(e))
            row.add_suffix(retry_btn)

        dismiss_btn = Gtk.Button(icon_name="window-close-symbolic")
        dismiss_btn.add_css_class("circular")
        dismiss_btn.add_css_class("flat")
        dismiss_btn.add_css_class("compact-btn")
        dismiss_btn.set_valign(Gtk.Align.CENTER)
        dismiss_btn.set_tooltip_text("Dismiss")
        dismiss_btn.connect("clicked", lambda _b, tid=entry.task_id: self.task_manager.dismiss_task(tid))
        row.add_suffix(dismiss_btn)

        return row

    def _build_removed_row(self, entry: TaskEntry) -> Gtk.Widget:
        row = Adw.ActionRow()
        row.set_title(GLib.markup_escape_text(entry.name))
        row.set_subtitle(
            GLib.markup_escape_text(
                f"{entry.category.display_name} • {format_relative_time(entry.created_at)}"
            )
        )
        row.set_icon_name(entry.category.icon_name)

        if entry.freed_bytes > 0:
            freed = Gtk.Label(label=f"{format_bytes(entry.freed_bytes)} freed")
            freed.add_css_class("badge-pill")
            freed.add_css_class("badge-success")
            freed.set_valign(Gtk.Align.CENTER)
            row.add_suffix(freed)

        return row

    def _apply_completed(self, entry: TaskEntry, matched_asset) -> None:
        ok = False
        label = entry.installed_theme_name or entry.name
        if matched_asset is not None:
            if matched_asset.category == ItemCategory.EXTENSIONS:
                ok = self.gsettings.toggle_extension(matched_asset.name, True)
            else:
                ok = self.gsettings.apply_local_asset(matched_asset)
            label = matched_asset.display_name or matched_asset.name
        elif entry.item is not None:
            ok, label = self.gsettings.apply_customization_item(entry.item)
        else:
            tname = entry.installed_theme_name or entry.name
            if entry.category == ItemCategory.ICONS:
                ok = self.gsettings.set_icon_theme(tname)
            elif entry.category == ItemCategory.GTK_THEMES:
                ok = self.gsettings.set_gtk_theme(tname)
            elif entry.category == ItemCategory.SHELL_THEMES:
                ok = self.gsettings.set_shell_theme(tname)
            elif entry.category == ItemCategory.CURSORS:
                ok = self.gsettings.set_cursor_theme(tname)
            elif entry.category == ItemCategory.WALLPAPERS and entry.installed_paths:
                ok = self.gsettings.set_wallpaper_uri(entry.installed_paths[0])
            elif entry.category == ItemCategory.EXTENSIONS:
                ok = self.gsettings.toggle_extension(entry.item_id or tname, True)

        if ok:
            verb = "Enabled" if entry.category == ItemCategory.EXTENSIONS else "Applied"
            self.toast_cb(f"{verb} {label}")
            self.rebuild()
        else:
            if entry.category == ItemCategory.SHELL_THEMES and not self.gsettings.is_user_theme_extension_installed():
                self.toast_cb("Install the User Themes extension in Installed → Shell Themes first.")
            else:
                self.toast_cb(f"Could not apply {label}")

    def _open_in_files(self, path_str: str) -> None:
        p = Path(path_str)
        target = str(p if p.is_dir() else p.parent)
        try:
            subprocess.Popen(["xdg-open", target])
        except Exception:
            self.toast_cb(f"Folder: {target}")
