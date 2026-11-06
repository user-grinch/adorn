"""GSettings and GNOME Desktop Customization manager for Adorn."""

import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from gi.repository import Gio

from .models import CustomizationItem, DesktopState, ItemCategory, LocalAsset
from .task_manager import TaskManager


class GSettingsManager:
    """Manages GNOME desktop themes, extensions, local asset scanning, and focused desktop tweaks."""

    _default_instance: Optional["GSettingsManager"] = None

    @classmethod
    def get_default(cls) -> "GSettingsManager":
        if cls._default_instance is None:
            cls._default_instance = cls()
        return cls._default_instance

    SCHEMA_INTERFACE = "org.gnome.desktop.interface"
    SCHEMA_WM_PREFS = "org.gnome.desktop.wm.preferences"
    SCHEMA_MUTTER = "org.gnome.mutter"
    SCHEMA_BACKGROUND = "org.gnome.desktop.background"
    SCHEMA_SCREENSAVER = "org.gnome.desktop.screensaver"
    SCHEMA_SOUND = "org.gnome.desktop.sound"
    SCHEMA_USER_THEME = "org.gnome.shell.extensions.user-theme"
    SCHEMA_INPUT_SOURCES = "org.gnome.desktop.input-sources"
    SCHEMA_SHELL = "org.gnome.shell"

    USER_THEME_UUID = "user-theme@gnome-shell-extensions.gcampax.github.com"

    def __init__(self):
        GSettingsManager._default_instance = self
        self._schema_source = Gio.SettingsSchemaSource.get_default()

        self.interface = self._get_settings(self.SCHEMA_INTERFACE)
        self.wm = self._get_settings(self.SCHEMA_WM_PREFS)
        self.mutter = self._get_settings(self.SCHEMA_MUTTER)
        self.background = self._get_settings(self.SCHEMA_BACKGROUND)
        self.screensaver = self._get_settings(self.SCHEMA_SCREENSAVER)
        self.sound = self._get_settings(self.SCHEMA_SOUND)
        self.input_sources = self._get_settings(self.SCHEMA_INPUT_SOURCES)
        self.shell = self._get_settings(self.SCHEMA_SHELL)
        self.user_theme = self._resolve_user_theme_settings()

        self.gtk4_css_dir = Path.home() / ".config" / "gtk-4.0"
        self.gtk3_css_dir = Path.home() / ".config" / "gtk-3.0"
        self.autostart_dir = Path.home() / ".config" / "autostart"
        self.gtk4_css_dir.mkdir(parents=True, exist_ok=True)
        self.gtk3_css_dir.mkdir(parents=True, exist_ok=True)
        self.autostart_dir.mkdir(parents=True, exist_ok=True)

        self._gtk4_marker = self.gtk4_css_dir / ".adorn-linked-theme"

        self._asset_cache: Optional[List[LocalAsset]] = None
        self._asset_cache_time: float = 0.0

    def _get_settings(self, schema_id: str) -> Optional[Gio.Settings]:
        if self._schema_source and self._schema_source.lookup(schema_id, True):
            return Gio.Settings.new(schema_id)
        return None

    def _resolve_user_theme_settings(self) -> Optional[Gio.Settings]:
        """Checks both global schema source and local user-theme extension schema directory."""
        if self._schema_source and self._schema_source.lookup(self.SCHEMA_USER_THEME, True):
            return Gio.Settings.new(self.SCHEMA_USER_THEME)

        ext_dirs = [
            Path.home() / ".local" / "share" / "gnome-shell" / "extensions" / self.USER_THEME_UUID / "schemas",
            Path("/usr/share/gnome-shell/extensions") / self.USER_THEME_UUID / "schemas",
        ]
        for sdir in ext_dirs:
            if sdir.is_dir():
                compiled = sdir / "gschemas.compiled"
                if not compiled.exists() and shutil.which("glib-compile-schemas"):
                    try:
                        subprocess.run(["glib-compile-schemas", str(sdir)], capture_output=True, timeout=5)
                    except Exception:
                        pass
                if compiled.exists():
                    try:
                        src = Gio.SettingsSchemaSource.new_from_directory(
                            str(sdir), self._schema_source, False
                        )
                        schema = src.lookup(self.SCHEMA_USER_THEME, True)
                        if schema:
                            return Gio.Settings.new_full(schema, None, None)
                    except Exception:
                        pass
        return None

    def refresh_user_theme_schema(self) -> None:
        self._schema_source = Gio.SettingsSchemaSource.get_default()
        self.user_theme = self._resolve_user_theme_settings()

    # =========================================================================
    # 1. ACTIVE THEMES & SHELL EXTENSION CHECKS
    # =========================================================================

    def get_icon_theme(self) -> str:
        return self.interface.get_string("icon-theme") if self.interface else "Adwaita"

    def set_icon_theme(self, name: str) -> bool:
        self.invalidate_asset_cache()
        return self.interface.set_string("icon-theme", name) if self.interface else False

    def reset_icon_theme(self) -> None:
        if self.interface:
            self.interface.reset("icon-theme")
        self.invalidate_asset_cache()

    def get_gtk_theme(self) -> str:
        return self.interface.get_string("gtk-theme") if self.interface else "Adwaita"

    def set_gtk_theme(self, name: str) -> bool:
        self.invalidate_asset_cache()
        return self.interface.set_string("gtk-theme", name) if self.interface else False

    def reset_gtk_theme(self) -> None:
        if self.interface:
            self.interface.reset("gtk-theme")
        self.reset_gtk4_theme()
        self.invalidate_asset_cache()

    def get_cursor_theme(self) -> str:
        return self.interface.get_string("cursor-theme") if self.interface else "Adwaita"

    def set_cursor_theme(self, name: str) -> bool:
        self.invalidate_asset_cache()
        return self.interface.set_string("cursor-theme", name) if self.interface else False

    def reset_cursor_theme(self) -> None:
        if self.interface:
            self.interface.reset("cursor-theme")
        self.invalidate_asset_cache()

    def get_shell_theme(self) -> str:
        self.refresh_user_theme_schema()
        if self.user_theme:
            val = self.user_theme.get_string("name")
            return val if val else "Default"
        return "Default"

    def set_shell_theme(self, name: str) -> bool:
        self.invalidate_asset_cache()
        self.refresh_user_theme_schema()
        if not self.user_theme:
            return False
        if not self.is_user_theme_extension_enabled():
            self.enable_user_theme_extension()
        return self.user_theme.set_string("name", "" if name in ("Default", "System", "") else name)

    def is_user_theme_extension_installed(self) -> bool:
        self.refresh_user_theme_schema()
        if self.user_theme is not None:
            return True
        for base in [
            Path.home() / ".local" / "share" / "gnome-shell" / "extensions",
            Path("/usr/share/gnome-shell/extensions"),
        ]:
            if (base / self.USER_THEME_UUID).is_dir():
                return True
        return False

    def is_user_theme_extension_enabled(self) -> bool:
        if not self.is_user_theme_extension_installed():
            return False
        if not self.get_extensions_master_enabled():
            return False
        return self.USER_THEME_UUID in self.list_enabled_extensions()

    def enable_user_theme_extension(self) -> bool:
        if not self.get_extensions_master_enabled():
            self.set_extensions_master_enabled(True)
        ok = self.toggle_extension(self.USER_THEME_UUID, True)
        self.refresh_user_theme_schema()
        return ok

    # GTK4 / Libadwaita theme linking for themes shipping a gtk-4.0 folder
    def get_linked_gtk4_theme(self) -> str:
        if self._gtk4_marker.exists():
            try:
                return self._gtk4_marker.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        return ""

    def apply_gtk4_theme(self, theme_dir: str, theme_name: str) -> bool:
        src_gtk4 = Path(theme_dir) / "gtk-4.0"
        if not src_gtk4.is_dir():
            return False

        # Clean previous Adorn links first
        self.reset_gtk4_theme()

        try:
            for item_name in ("gtk.css", "gtk-dark.css", "assets"):
                src_item = src_gtk4 / item_name
                dest_item = self.gtk4_css_dir / item_name
                backup_item = self.gtk4_css_dir / f"{item_name}.adorn-backup"

                if dest_item.exists() or dest_item.is_symlink():
                    if not dest_item.is_symlink() and not backup_item.exists():
                        shutil.move(str(dest_item), str(backup_item))
                    else:
                        if dest_item.is_dir() and not dest_item.is_symlink():
                            shutil.rmtree(dest_item, ignore_errors=True)
                        else:
                            dest_item.unlink(missing_ok=True)

                if src_item.exists():
                    dest_item.symlink_to(src_item.resolve())

            self._gtk4_marker.write_text(theme_name, encoding="utf-8")
            self.invalidate_asset_cache()
            return True
        except Exception as e:
            print(f"[GSettings] Failed linking GTK4 theme: {e}")
            return False

    def reset_gtk4_theme(self) -> bool:
        try:
            had_marker = self._gtk4_marker.exists()
            for item_name in ("gtk.css", "gtk-dark.css", "assets"):
                dest_item = self.gtk4_css_dir / item_name
                backup_item = self.gtk4_css_dir / f"{item_name}.adorn-backup"
                if dest_item.is_symlink():
                    dest_item.unlink(missing_ok=True)
                if backup_item.exists() and not dest_item.exists():
                    shutil.move(str(backup_item), str(dest_item))
            self._gtk4_marker.unlink(missing_ok=True)
            if had_marker:
                self.invalidate_asset_cache()
            return True
        except Exception:
            return False

    def is_dark_mode(self) -> bool:
        return (self.interface.get_string("color-scheme") if self.interface else "default") == "prefer-dark"

    def get_color_scheme(self) -> str:
        return self.interface.get_string("color-scheme") if self.interface else "default"

    def get_accent_color(self) -> str:
        try:
            return self.interface.get_string("accent-color") if self.interface else "blue"
        except Exception:
            return "blue"

    # =========================================================================
    # 2. WALLPAPER & LOCK SCREEN POWER TOOLS
    # =========================================================================

    def get_wallpaper_uri(self) -> str:
        if self.background:
            key = "picture-uri-dark" if self.is_dark_mode() else "picture-uri"
            try:
                val = self.background.get_string(key)
                if val:
                    return val
            except Exception:
                pass
            try:
                return self.background.get_string("picture-uri")
            except Exception:
                pass
        return ""

    def set_wallpaper_uri(self, path_or_uri: str) -> bool:
        if not self.background:
            return False
        uri = path_or_uri if path_or_uri.startswith("file://") else f"file://{os.path.abspath(path_or_uri)}"
        self.background.set_string("picture-uri", uri)
        try:
            self.background.set_string("picture-uri-dark", uri)
        except Exception:
            pass
        self.invalidate_asset_cache()
        return True

    def get_wallpaper_options(self) -> str:
        try:
            return self.background.get_string("picture-options") if self.background else "zoom"
        except Exception:
            return "zoom"

    def set_wallpaper_options(self, opt: str) -> bool:
        try:
            return self.background.set_string("picture-options", opt) if self.background else False
        except Exception:
            return False

    def get_lockscreen_uri(self) -> str:
        try:
            return self.screensaver.get_string("picture-uri") if self.screensaver else ""
        except Exception:
            return ""

    def set_lockscreen_uri(self, path_or_uri: str) -> bool:
        if not self.screensaver:
            return False
        uri = path_or_uri if path_or_uri.startswith("file://") else f"file://{os.path.abspath(path_or_uri)}"
        try:
            return self.screensaver.set_string("picture-uri", uri)
        except Exception:
            return False

    def reset_lockscreen_uri(self) -> None:
        if self.screensaver:
            try:
                self.screensaver.reset("picture-uri")
            except Exception:
                pass

    # =========================================================================
    # 3. FONTS & TYPOGRAPHY (Non-duplicated with GNOME Settings)
    # =========================================================================

    def get_interface_font(self) -> str:
        try:
            return self.interface.get_string("font-name") if self.interface else "Adwaita Sans 11"
        except Exception:
            return "Adwaita Sans 11"

    def set_interface_font(self, font: str) -> bool:
        try:
            return self.interface.set_string("font-name", font) if self.interface else False
        except Exception:
            return False

    def get_document_font(self) -> str:
        try:
            return self.interface.get_string("document-font-name") if self.interface else "Adwaita Sans 11"
        except Exception:
            return "Adwaita Sans 11"

    def set_document_font(self, font: str) -> bool:
        try:
            return self.interface.set_string("document-font-name", font) if self.interface else False
        except Exception:
            return False

    def get_monospace_font(self) -> str:
        try:
            return self.interface.get_string("monospace-font-name") if self.interface else "Adwaita Mono 11"
        except Exception:
            return "Adwaita Mono 11"

    def set_monospace_font(self, font: str) -> bool:
        try:
            return self.interface.set_string("monospace-font-name", font) if self.interface else False
        except Exception:
            return False

    def get_titlebar_font(self) -> str:
        try:
            return self.wm.get_string("titlebar-font") if self.wm else "Adwaita Sans Bold 11"
        except Exception:
            return "Adwaita Sans Bold 11"

    def set_titlebar_font(self, font: str) -> bool:
        try:
            return self.wm.set_string("titlebar-font", font) if self.wm else False
        except Exception:
            return False

    def get_font_scaling(self) -> float:
        try:
            return self.interface.get_double("text-scaling-factor") if self.interface else 1.0
        except Exception:
            return 1.0

    def set_font_scaling(self, factor: float) -> bool:
        try:
            return self.interface.set_double("text-scaling-factor", factor) if self.interface else False
        except Exception:
            return False

    def get_font_antialiasing(self) -> str:
        try:
            return self.interface.get_string("font-antialiasing") if self.interface else "rgba"
        except Exception:
            return "rgba"

    def set_font_antialiasing(self, val: str) -> bool:
        try:
            return self.interface.set_string("font-antialiasing", val) if self.interface else False
        except Exception:
            return False

    def get_font_hinting(self) -> str:
        try:
            return self.interface.get_string("font-hinting") if self.interface else "slight"
        except Exception:
            return "slight"

    def set_font_hinting(self, val: str) -> bool:
        try:
            return self.interface.set_string("font-hinting", val) if self.interface else False
        except Exception:
            return False

    def reset_fonts_to_default(self) -> None:
        if self.interface:
            for k in (
                "font-name",
                "document-font-name",
                "monospace-font-name",
                "text-scaling-factor",
                "font-antialiasing",
                "font-hinting",
            ):
                try:
                    self.interface.reset(k)
                except Exception:
                    pass
        if self.wm:
            try:
                self.wm.reset("titlebar-font")
            except Exception:
                pass

    # =========================================================================
    # 4. INPUT & SOUND EXTRAS (Only items NOT in GNOME 51 Settings)
    # =========================================================================

    def get_middle_click_paste(self) -> bool:
        try:
            return self.interface.get_boolean("gtk-enable-primary-paste") if self.interface else True
        except Exception:
            return True

    def set_middle_click_paste(self, enabled: bool) -> bool:
        try:
            return self.interface.set_boolean("gtk-enable-primary-paste", enabled) if self.interface else False
        except Exception:
            return False

    def get_xkb_options(self) -> List[str]:
        try:
            return list(self.input_sources.get_strv("xkb-options")) if self.input_sources else []
        except Exception:
            return []

    def set_xkb_options(self, opts: List[str]) -> bool:
        try:
            return self.input_sources.set_strv("xkb-options", opts) if self.input_sources else False
        except Exception:
            return False

    def get_caps_lock_behavior(self) -> str:
        for opt in self.get_xkb_options():
            if opt.startswith("caps:"):
                return opt
        return "none"

    def set_caps_lock_behavior(self, opt_value: str) -> bool:
        curr = [o for o in self.get_xkb_options() if not o.startswith("caps:")]
        if opt_value and opt_value != "none":
            curr.append(opt_value)
        return self.set_xkb_options(curr)

    def get_sound_theme(self) -> str:
        try:
            return self.sound.get_string("theme-name") if self.sound else "freedesktop"
        except Exception:
            return "freedesktop"

    def set_sound_theme(self, name: str) -> bool:
        try:
            return self.sound.set_string("theme-name", name) if self.sound else False
        except Exception:
            return False

    def get_input_feedback_sounds(self) -> bool:
        try:
            return self.sound.get_boolean("input-feedback-sounds") if self.sound else False
        except Exception:
            return False

    def set_input_feedback_sounds(self, enabled: bool) -> bool:
        try:
            return self.sound.set_boolean("input-feedback-sounds", enabled) if self.sound else False
        except Exception:
            return False

    def list_installed_sound_themes(self) -> List[str]:
        themes = {"freedesktop", "gnome"}
        for base in [Path("/usr/share/sounds"), Path.home() / ".local" / "share" / "sounds"]:
            if base.is_dir():
                for p in base.iterdir():
                    if p.is_dir() and (p / "index.theme").exists():
                        themes.add(p.name)
        return sorted(themes)

    # =========================================================================
    # 5. STARTUP APPLICATIONS (~/.config/autostart)
    # =========================================================================

    def list_autostart_apps(self) -> List[Dict[str, Any]]:
        apps: List[Dict[str, Any]] = []
        if not self.autostart_dir.is_dir():
            return []
        for p in sorted(self.autostart_dir.glob("*.desktop")):
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                name = p.stem
                exec_cmd = ""
                comment = ""
                icon = "application-x-executable-symbolic"
                disabled = False
                hidden = False
                for line in content.splitlines():
                    if line.startswith("Name="):
                        name = line.split("=", 1)[1].strip()
                    elif line.startswith("Exec="):
                        exec_cmd = line.split("=", 1)[1].strip()
                    elif line.startswith("Comment="):
                        comment = line.split("=", 1)[1].strip()
                    elif line.startswith("Icon="):
                        icon = line.split("=", 1)[1].strip()
                    elif line.strip() == "Hidden=true":
                        hidden = True
                    elif line.strip() == "X-GNOME-Autostart-enabled=false":
                        disabled = True
                if hidden:
                    continue
                apps.append({
                    "file_name": p.name,
                    "path": str(p),
                    "name": name or p.stem,
                    "exec": exec_cmd,
                    "comment": comment,
                    "icon": icon,
                    "enabled": not disabled,
                })
            except Exception:
                continue
        return apps

    def set_autostart_app_enabled(self, file_name: str, enabled: bool) -> bool:
        target = self.autostart_dir / file_name
        if not target.exists():
            return False
        try:
            lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
            val_str = "true" if enabled else "false"
            replaced = False
            new_lines = []
            for line in lines:
                if line.startswith("X-GNOME-Autostart-enabled="):
                    new_lines.append(f"X-GNOME-Autostart-enabled={val_str}")
                    replaced = True
                else:
                    new_lines.append(line)
            if not replaced:
                new_lines.append(f"X-GNOME-Autostart-enabled={val_str}")
            target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

    def list_available_desktop_apps(self) -> List[Dict[str, str]]:
        seen_names = set()
        apps = []
        search_dirs = [
            Path("/usr/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"),
            Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
            Path.home() / ".local" / "share" / "applications",
        ]
        for d in search_dirs:
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.desktop")):
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    if "NoDisplay=true" in content or "Type=Link" in content:
                        continue
                    name = ""
                    exec_cmd = ""
                    comment = ""
                    icon = "application-x-executable-symbolic"
                    for line in content.splitlines():
                        if line.startswith("Name=") and not name:
                            name = line.split("=", 1)[1].strip()
                        elif line.startswith("Exec=") and not exec_cmd:
                            exec_cmd = line.split("=", 1)[1].strip().split("%")[0].strip()
                        elif line.startswith("Comment=") and not comment:
                            comment = line.split("=", 1)[1].strip()
                        elif line.startswith("Icon=") and icon == "application-x-executable-symbolic":
                            icon = line.split("=", 1)[1].strip()
                    if name and name not in seen_names:
                        seen_names.add(name)
                        apps.append({
                            "file_name": p.name,
                            "path": str(p),
                            "name": name,
                            "exec": exec_cmd,
                            "comment": comment,
                            "icon": icon,
                        })
                except Exception:
                    continue
        apps.sort(key=lambda a: a["name"].lower())
        return apps

    def add_autostart_app(self, desktop_file_path: str) -> bool:
        src = Path(desktop_file_path)
        if not src.exists():
            return False
        dest = self.autostart_dir / src.name
        try:
            shutil.copy2(src, dest)
            self.set_autostart_app_enabled(dest.name, True)
            return True
        except Exception:
            return False

    def remove_autostart_app(self, file_name: str) -> bool:
        target = self.autostart_dir / file_name
        if target.exists():
            try:
                target.unlink()
                return True
            except Exception:
                return False
        return False

    # =========================================================================
    # 6. WINDOW TITLEBARS & WINDOW MANAGEMENT
    # =========================================================================

    def get_button_layout(self) -> str:
        return self.wm.get_string("button-layout") if self.wm else "appmenu:close"

    def set_button_layout(self, layout: str) -> bool:
        return self.wm.set_string("button-layout", layout) if self.wm else False

    def get_has_maximize_button(self) -> bool:
        return "maximize" in self.get_button_layout()

    def get_has_minimize_button(self) -> bool:
        return "minimize" in self.get_button_layout()

    def get_button_placement(self) -> str:
        layout = self.get_button_layout()
        left, _, _ = layout.partition(":")
        return "left" if "close" in left else "right"

    def set_titlebar_buttons(self, maximize: bool, minimize: bool, placement: str) -> bool:
        btns = []
        if minimize:
            btns.append("minimize")
        if maximize:
            btns.append("maximize")
        btns.append("close")
        if placement == "left":
            layout = ",".join(reversed(btns)) + ":appmenu"
        else:
            layout = "appmenu:" + ",".join(btns)
        return self.set_button_layout(layout)

    def get_action_double_click_titlebar(self) -> str:
        try:
            return self.wm.get_string("action-double-click-titlebar") if self.wm else "toggle-maximize"
        except Exception:
            return "toggle-maximize"

    def set_action_double_click_titlebar(self, act: str) -> bool:
        try:
            return self.wm.set_string("action-double-click-titlebar", act) if self.wm else False
        except Exception:
            return False

    def get_action_middle_click_titlebar(self) -> str:
        try:
            return self.wm.get_string("action-middle-click-titlebar") if self.wm else "none"
        except Exception:
            return "none"

    def set_action_middle_click_titlebar(self, act: str) -> bool:
        try:
            return self.wm.set_string("action-middle-click-titlebar", act) if self.wm else False
        except Exception:
            return False

    def get_action_right_click_titlebar(self) -> str:
        try:
            return self.wm.get_string("action-right-click-titlebar") if self.wm else "menu"
        except Exception:
            return "menu"

    def set_action_right_click_titlebar(self, act: str) -> bool:
        try:
            return self.wm.set_string("action-right-click-titlebar", act) if self.wm else False
        except Exception:
            return False

    def get_focus_mode(self) -> str:
        return self.wm.get_string("focus-mode") if self.wm else "click"

    def set_focus_mode(self, mode: str) -> bool:
        return self.wm.set_string("focus-mode", mode) if self.wm else False

    def get_auto_raise(self) -> bool:
        try:
            return self.wm.get_boolean("auto-raise") if self.wm else False
        except Exception:
            return False

    def set_auto_raise(self, enabled: bool) -> bool:
        try:
            return self.wm.set_boolean("auto-raise", enabled) if self.wm else False
        except Exception:
            return False

    def get_window_action_key(self) -> str:
        try:
            return self.wm.get_string("mouse-button-modifier") if self.wm else "<Super>"
        except Exception:
            return "<Super>"

    def set_window_action_key(self, key: str) -> bool:
        try:
            return self.wm.set_string("mouse-button-modifier", key) if self.wm else False
        except Exception:
            return False

    def get_resize_with_right_button(self) -> bool:
        try:
            return self.wm.get_boolean("resize-with-right-button") if self.wm else False
        except Exception:
            return False

    def set_resize_with_right_button(self, enabled: bool) -> bool:
        try:
            return self.wm.set_boolean("resize-with-right-button", enabled) if self.wm else False
        except Exception:
            return False

    def get_center_new_windows(self) -> bool:
        try:
            return self.mutter.get_boolean("center-new-windows") if self.mutter else False
        except Exception:
            return False

    def set_center_new_windows(self, enabled: bool) -> bool:
        try:
            return self.mutter.set_boolean("center-new-windows", enabled) if self.mutter else False
        except Exception:
            return False

    def get_attach_modal_dialogs(self) -> bool:
        try:
            return self.mutter.get_boolean("attach-modal-dialogs") if self.mutter else True
        except Exception:
            return True

    def set_attach_modal_dialogs(self, enabled: bool) -> bool:
        try:
            return self.mutter.set_boolean("attach-modal-dialogs", enabled) if self.mutter else False
        except Exception:
            return False

    # =========================================================================
    # 7. GTK WINDOW CSS OVERRIDES & WALLPAPER TOOLS
    # =========================================================================

    def _update_css_block(self, css_file: Path, marker_name: str, block_body: str) -> None:
        if css_file.is_symlink():
            # Do not overwrite a symlinked theme's source gtk.css!
            return
        start_tag = f"/* ADORN-{marker_name}-START */"
        end_tag = f"/* ADORN-{marker_name}-END */"
        existing = ""
        if css_file.exists():
            try:
                existing = css_file.read_text(encoding="utf-8")
            except Exception:
                existing = ""
        pattern = re.compile(re.escape(start_tag) + r".*?" + re.escape(end_tag) + r"\n?", re.DOTALL)
        existing = pattern.sub("", existing).rstrip()
        if block_body.strip():
            new_block = f"{start_tag}\n{block_body.strip()}\n{end_tag}\n"
            final = (existing + "\n\n" + new_block) if existing else new_block
        else:
            final = existing + ("\n" if existing else "")
        try:
            if final.strip():
                css_file.write_text(final, encoding="utf-8")
            elif css_file.exists():
                css_file.unlink(missing_ok=True)
        except Exception as e:
            print(f"[GSettings] Error writing CSS to {css_file}: {e}")

    def get_gtk_custom_css_state(self) -> Tuple[bool, int, float]:
        """Reads back (enabled, border_radius, backdrop_opacity) from gtk-4.0/gtk.css."""
        css_file = self.gtk4_css_dir / "gtk.css"
        if not css_file.exists() or css_file.is_symlink():
            return False, 12, 1.0
        try:
            txt = css_file.read_text(encoding="utf-8")
            m_block = re.search(
                r"/\* ADORN-WINDOW-START \*/(.*?)/\* ADORN-WINDOW-END \*/", txt, re.DOTALL
            )
            if not m_block:
                return False, 12, 1.0
            body = m_block.group(1)
            r_match = re.search(r"border-radius:\s*(\d+)px", body)
            o_match = re.search(r"opacity:\s*([0-9.]+)", body)
            radius = int(r_match.group(1)) if r_match else 12
            opacity = float(o_match.group(1)) if o_match else 1.0
            return True, radius, opacity
        except Exception:
            return False, 12, 1.0

    def set_gtk_custom_css(self, border_radius: int, opacity: float) -> bool:
        if (self.gtk4_css_dir / "gtk.css").is_symlink():
            return False
        css_content = f"""window.csd,
window.solid-csd,
.window-frame {{
    border-radius: {border_radius}px;
}}

window.background:backdrop {{
    opacity: {opacity:.2f};
}}"""
        self._update_css_block(self.gtk4_css_dir / "gtk.css", "WINDOW", css_content)
        self._update_css_block(self.gtk3_css_dir / "gtk.css", "WINDOW", css_content)
        return True

    def clear_gtk_custom_css(self) -> None:
        self._update_css_block(self.gtk4_css_dir / "gtk.css", "WINDOW", "")
        self._update_css_block(self.gtk3_css_dir / "gtk.css", "WINDOW", "")

    def extract_wallpaper_palette(self) -> List[Tuple[str, str]]:
        """Extracts dominant colors from current wallpaper using Pillow or GdkPixbuf."""
        uri = self.get_wallpaper_uri()
        if not uri:
            return []

        path = uri.replace("file://", "")
        if not os.path.exists(path) or path.endswith(".xml"):
            return []

        try:
            from gi.repository import GdkPixbuf
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 48, 48, False)
            if not pb:
                return []
            pixels = pb.get_pixels()
            n_channels = pb.get_n_channels()
            rowstride = pb.get_rowstride()
            w, h = pb.get_width(), pb.get_height()
            buckets: Dict[Tuple[int, int, int], int] = {}
            for y in range(h):
                for x in range(w):
                    idx = y * rowstride + x * n_channels
                    r, g, b = pixels[idx], pixels[idx + 1], pixels[idx + 2]
                    if (r < 25 and g < 25 and b < 25) or (r > 235 and g > 235 and b > 235):
                        continue
                    # Quantize slightly so similar pixels cluster cleanly
                    qr, qg, qb = (r // 24) * 24 + 12, (g // 24) * 24 + 12, (b // 24) * 24 + 12
                    buckets[(qr, qg, qb)] = buckets.get((qr, qg, qb), 0) + 1

            ranked = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
            palette: List[Tuple[str, str]] = []
            chosen_rgb: List[Tuple[int, int, int]] = []
            for (r, g, b), _cnt in ranked:
                if any(abs(r - cr) + abs(g - cg) + abs(b - cb) < 75 for cr, cg, cb in chosen_rgb):
                    continue
                chosen_rgb.append((r, g, b))
                hex_c = f"#{min(255, r):02x}{min(255, g):02x}{min(255, b):02x}"
                palette.append((f"Swatch {len(palette) + 1}", hex_c))
                if len(palette) >= 6:
                    break
            return palette
        except Exception:
            return []

    def generate_wallpaper_slideshow(
        self, folder_path: str, duration: int = 1800, transition: int = 5
    ) -> Optional[str]:
        folder = Path(folder_path)
        if not folder.is_dir():
            return None

        images = sorted([
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        ])
        if len(images) < 2:
            return None

        props_dir = Path.home() / ".local" / "share" / "gnome-background-properties"
        props_dir.mkdir(parents=True, exist_ok=True)
        xml_path = props_dir / f"adorn-slideshow-{folder.name}.xml"

        root = ET.Element("background")
        starttime = ET.SubElement(root, "starttime")
        ET.SubElement(starttime, "year").text = "2026"
        ET.SubElement(starttime, "month").text = "01"
        ET.SubElement(starttime, "day").text = "01"
        ET.SubElement(starttime, "hour").text = "00"
        ET.SubElement(starttime, "minute").text = "00"
        ET.SubElement(starttime, "second").text = "00"

        for i, img in enumerate(images):
            nxt = images[(i + 1) % len(images)]
            static = ET.SubElement(root, "static")
            ET.SubElement(static, "duration").text = str(float(duration))
            ET.SubElement(static, "file").text = str(img.resolve())

            trans = ET.SubElement(root, "transition")
            ET.SubElement(trans, "duration").text = str(float(transition))
            ET.SubElement(trans, "from").text = str(img.resolve())
            ET.SubElement(trans, "to").text = str(nxt.resolve())

        tree = ET.ElementTree(root)
        tree.write(str(xml_path), encoding="utf-8", xml_declaration=True)

        self.set_wallpaper_uri(str(xml_path))
        return str(xml_path)

    # =========================================================================
    # 8. EXTENSIONS MANAGEMENT
    # =========================================================================

    def get_extensions_master_enabled(self) -> bool:
        if self.shell:
            try:
                return not self.shell.get_boolean("disable-user-extensions")
            except Exception:
                pass
        return True

    def set_extensions_master_enabled(self, enabled: bool) -> bool:
        self.invalidate_asset_cache()
        if self.shell:
            try:
                return self.shell.set_boolean("disable-user-extensions", not enabled)
            except Exception:
                pass
        return False

    def list_enabled_extensions(self) -> List[str]:
        if self.shell:
            try:
                return list(self.shell.get_strv("enabled-extensions"))
            except Exception:
                pass
        return []

    def toggle_extension(self, uuid: str, enable: bool) -> bool:
        self.invalidate_asset_cache()
        cmd_ok = False
        try:
            cmd = ["gnome-extensions", "enable" if enable else "disable", uuid]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                cmd_ok = True
        except Exception:
            pass

        if self.shell:
            try:
                curr_enabled = list(self.shell.get_strv("enabled-extensions"))
                curr_disabled = list(self.shell.get_strv("disabled-extensions")) if "disabled-extensions" in self.shell.list_keys() else []
                if enable:
                    if uuid not in curr_enabled:
                        curr_enabled.append(uuid)
                        self.shell.set_strv("enabled-extensions", curr_enabled)
                    if uuid in curr_disabled:
                        curr_disabled.remove(uuid)
                        self.shell.set_strv("disabled-extensions", curr_disabled)
                    return True
                else:
                    if uuid in curr_enabled:
                        curr_enabled.remove(uuid)
                        self.shell.set_strv("enabled-extensions", curr_enabled)
                    if uuid not in curr_disabled:
                        curr_disabled.append(uuid)
                        self.shell.set_strv("disabled-extensions", curr_disabled)
                    return True
            except Exception:
                pass
        return cmd_ok

    # =========================================================================
    # 9. ASSET SCANNING & CACHE
    # =========================================================================

    def invalidate_asset_cache(self) -> None:
        self._asset_cache = None
        self._asset_cache_time = 0.0

    @staticmethod
    def _get_search_dirs(subfolder: str) -> List[Tuple[Path, bool]]:
        home = Path.home()
        candidates = [
            (home / ".local" / "share" / subfolder, False),
            (home / f".{subfolder}", False),
            (Path("/usr/local/share") / subfolder, True),
            (Path("/usr/share") / subfolder, True),
        ]
        return [(d, is_sys) for d, is_sys in candidates if d.is_dir()]

    @staticmethod
    def _parse_index_theme_meta(index_file: Path) -> Tuple[str, str]:
        """Extracts Name and Comment from an XDG index.theme file."""
        if not index_file.exists():
            return "", ""
        name_val = ""
        comment_val = ""
        try:
            for line in index_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Name=") and not name_val:
                    name_val = line.split("=", 1)[1].strip()
                elif line.startswith("Comment=") and not comment_val:
                    comment_val = line.split("=", 1)[1].strip()
                if name_val and comment_val:
                    break
        except Exception:
            pass
        return name_val, comment_val

    CURSOR_SUBDIR_NAMES = {"cursors", "cursors_scalable", "hyprcursors", "x11"}
    ICON_DIR_PATTERNS = {
        "apps", "actions", "places", "devices", "status", "mimetypes",
        "categories", "emblems", "panel", "scalable", "symbolic", "legacy",
        "ui", "animations", "preferences", "applets",
        "16x16", "22x22", "24x24", "32x32", "48x48", "64x64", "96x96",
        "128x128", "256x256", "512x512", "16", "22", "24", "32", "48", "64", "128"
    }

    @classmethod
    def _detect_icon_and_cursor_types(cls, entry: Path) -> Tuple[bool, bool]:
        if not entry.is_dir():
            return False, False
        name_low = entry.name.lower()
        if name_low in ("default", "hicolor", "locolor"):
            return False, False

        # 1. Cursor Theme Detection
        has_cursors = False
        cursors_dir = entry / "cursors"
        if cursors_dir.is_dir():
            try:
                has_cursors = any(not f.name.startswith(".") for f in cursors_dir.iterdir())
            except Exception:
                has_cursors = True
        elif (
            (entry / "cursors_scalable").is_dir()
            or (entry / "hyprcursors").is_dir()
            or (entry / "cursor.theme").is_file()
        ):
            has_cursors = True

        # 2. Icon Theme Detection via index.theme Directories= declaration
        has_icons = False
        itf = entry / "index.theme"
        if itf.is_file():
            try:
                txt = itf.read_text(encoding="utf-8", errors="ignore")
                for line in txt.splitlines():
                    line_s = line.strip()
                    if line_s.lower().startswith("directories="):
                        dirs_val = line_s.split("=", 1)[1].strip()
                        if dirs_val:
                            dirs_list = [d.strip() for d in dirs_val.split(",") if d.strip()]
                            valid_dirs = [
                                d for d in dirs_list
                                if not any(c in d.lower() for c in cls.CURSOR_SUBDIR_NAMES)
                            ]
                            if valid_dirs:
                                for d in valid_dirs:
                                    if (entry / d).exists():
                                        has_icons = True
                                        break
                        break
            except Exception:
                pass

        # 3. Content Inspection fallback (when index.theme has no Directories= or is absent)
        if not has_icons:
            try:
                for sub in entry.iterdir():
                    if not sub.is_dir():
                        continue
                    sub_clean = sub.name.lower().split("@")[0]
                    if sub_clean in cls.ICON_DIR_PATTERNS and sub_clean not in cls.CURSOR_SUBDIR_NAMES:
                        for _, _, files in os.walk(sub):
                            if any(f.lower().endswith((".png", ".svg", ".svgz")) for f in files):
                                has_icons = True
                                break
                        if has_icons:
                            break
            except Exception:
                pass

        # 4. Flat icon set inspection (direct svg/png files in root)
        if not has_icons and not has_cursors:
            try:
                icon_files = [
                    f for f in entry.iterdir()
                    if f.is_file() and f.suffix.lower() in (".svg", ".png", ".svgz")
                    and f.stem.lower() not in ("preview", "screenshot", "icon", "thumb")
                ]
                if len(icon_files) >= 3:
                    has_icons = True
            except Exception:
                pass

        return has_icons, has_cursors

    @staticmethod
    def _ensure_icon_theme_index(entry: Path, name: str) -> None:
        """Generates a minimal valid index.theme for an icon theme that lacks one."""
        itf = entry / "index.theme"
        if itf.exists():
            return
        icon_subdirs = []
        for root_dir, _, subfiles in os.walk(entry):
            rel = Path(root_dir).relative_to(entry)
            if str(rel) != "." and any(f.lower().endswith((".png", ".svg", ".svgz")) for f in subfiles):
                icon_subdirs.append(str(rel))
        if not icon_subdirs:
            return
        icon_subdirs.sort()
        lines = [
            "[Icon Theme]",
            f"Name={name}",
            f"Comment={name} Icon Theme",
            "Inherits=hicolor,Adwaita",
            f"Directories={','.join(icon_subdirs)}",
            "",
        ]
        for d in icon_subdirs:
            context = "Applications"
            d_low = d.lower()
            if "place" in d_low: context = "Places"
            elif "action" in d_low: context = "Actions"
            elif "mime" in d_low: context = "MimeTypes"
            elif "status" in d_low: context = "Status"
            elif "device" in d_low: context = "Devices"
            elif "category" in d_low or "categories" in d_low: context = "Categories"
            elif "emblem" in d_low: context = "Emblems"
            lines.extend([
                f"[{d}]",
                f"Context={context}",
                "Size=48",
                "Type=Scalable",
                "MinSize=16",
                "MaxSize=512",
                "",
            ])
        try:
            itf.write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass

    def _infer_author(
        self,
        name: str,
        path: str,
        is_system: bool,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        if meta:
            for k in ("creator", "original-author", "author", "developer_name"):
                v = meta.get(k)
                if v and str(v).strip():
                    return str(v).strip()
        try:
            reg = TaskManager.get_default().registry
            for entry in reg.values():
                if entry.get("theme_name", "").lower() == name.lower() or path in entry.get("paths", []):
                    if entry.get("author"):
                        return entry["author"]
        except Exception:
            pass
        if "@" in name:
            domain = name.split("@")[-1]
            user_part = domain.split(".")[0]
            if user_part and user_part.lower() not in ("gnome", "org", "com", "net"):
                return user_part.capitalize()
        return "System" if is_system else "User"

    def list_installed_assets(self, force_rescan: bool = False) -> List[LocalAsset]:
        """Scans icons, cursors, GTK themes, Shell themes, wallpapers, and extensions."""
        now = time.time()
        if not force_rescan and self._asset_cache is not None and (now - self._asset_cache_time < 3.0):
            return self._asset_cache

        assets_by_name: Dict[str, LocalAsset] = {}
        active_icon = self.get_icon_theme()
        active_cursor = self.get_cursor_theme()
        active_gtk = self.get_gtk_theme()
        linked_gtk4 = self.get_linked_gtk4_theme()
        active_shell = self.get_shell_theme()
        active_wall = self.get_wallpaper_uri()
        enabled_exts = set(self.list_enabled_extensions())

        # 1. Icons & Cursors
        for root, is_sys in self._get_search_dirs("icons"):
            try:
                for entry in root.iterdir():
                    if not entry.is_dir():
                        continue
                    name = entry.name
                    has_icons, has_cursors = self._detect_icon_and_cursor_types(entry)
                    if not has_icons and not has_cursors:
                        continue

                    itf = entry / "index.theme"
                    disp_name, comment = self._parse_index_theme_meta(itf)

                    # Auto-generate index.theme for user icon pack if missing so GNOME can load it
                    if has_icons and not itf.exists() and not is_sys:
                        self._ensure_icon_theme_index(entry, disp_name or name)

                    # Icon Theme Entry
                    if has_icons and f"icon:{name}" not in assets_by_name:
                        details_str = "Icon pack (includes cursors)" if has_cursors else "Icon pack"
                        if comment:
                            details_str = f"{details_str} • {comment}"
                        assets_by_name[f"icon:{name}"] = LocalAsset(
                            name=name,
                            display_name=disp_name or name,
                            description=comment,
                            category=ItemCategory.ICONS,
                            path=str(entry),
                            is_system=is_sys,
                            is_active=(name == active_icon),
                            has_icons=True,
                            has_cursors=has_cursors,
                            details=details_str,
                            author=self._infer_author(name, str(entry), is_sys),
                        )

                    # Cursor Theme Entry
                    if has_cursors and f"cursor:{name}" not in assets_by_name:
                        details_str = "Cursor theme (part of icon pack)" if has_icons else "Cursor theme"
                        if comment:
                            details_str = f"{details_str} • {comment}"
                        assets_by_name[f"cursor:{name}"] = LocalAsset(
                            name=name,
                            display_name=disp_name or name,
                            description=comment,
                            category=ItemCategory.CURSORS,
                            path=str(entry),
                            is_system=is_sys,
                            is_active=(name == active_cursor),
                            has_icons=has_icons,
                            has_cursors=True,
                            details=details_str,
                            author=self._infer_author(name, str(entry), is_sys),
                        )
            except Exception:
                continue

        # 2. GTK & Shell themes
        for root, is_sys in self._get_search_dirs("themes"):
            try:
                for entry in root.iterdir():
                    if not entry.is_dir():
                        continue
                    name = entry.name
                    has_gtk3 = (entry / "gtk-3.0").is_dir()
                    has_gtk4 = (entry / "gtk-4.0").is_dir()
                    has_shell = (entry / "gnome-shell").is_dir()
                    disp_name, comment = self._parse_index_theme_meta(entry / "index.theme")

                    if (has_gtk3 or has_gtk4) and f"gtk:{name}" not in assets_by_name:
                        ver_str = "GTK3 & GTK4" if (has_gtk3 and has_gtk4) else ("GTK4" if has_gtk4 else "GTK3")
                        gtk4_tag = " • Linked to GTK4" if (linked_gtk4 and linked_gtk4 == name) else ""
                        assets_by_name[f"gtk:{name}"] = LocalAsset(
                            name=name,
                            display_name=name,
                            description=comment,
                            category=ItemCategory.GTK_THEMES,
                            path=str(entry),
                            is_system=is_sys,
                            is_active=(name == active_gtk),
                            has_gtk3=has_gtk3,
                            has_gtk4=has_gtk4,
                            details=f"{ver_str}{gtk4_tag}",
                            author=self._infer_author(name, str(entry), is_sys),
                        )

                    if has_shell and f"shell:{name}" not in assets_by_name:
                        assets_by_name[f"shell:{name}"] = LocalAsset(
                            name=name,
                            display_name=name,
                            description=comment,
                            category=ItemCategory.SHELL_THEMES,
                            path=str(entry),
                            is_system=is_sys,
                            is_active=(name == active_shell),
                            has_shell=True,
                            details=comment or "GNOME Shell Theme",
                            author=self._infer_author(name, str(entry), is_sys),
                        )
            except Exception:
                continue

        # Ensure Default Shell & Adwaita GTK entries exist so user can always switch back cleanly
        if "shell:Default" not in assets_by_name:
            assets_by_name["shell:Default"] = LocalAsset(
                name="Default",
                display_name="Adwaita (Default Shell)",
                description="Built-in GNOME 51 Shell style",
                category=ItemCategory.SHELL_THEMES,
                path="/usr/share/gnome-shell/theme",
                is_system=True,
                is_active=(active_shell in ("Default", "", "System")),
                has_shell=True,
                details="Built-in GNOME Shell style",
                author="GNOME",
            )

        if "gtk:Adwaita" not in assets_by_name:
            assets_by_name["gtk:Adwaita"] = LocalAsset(
                name="Adwaita",
                display_name="Adwaita (Default)",
                description="Built-in GNOME application style",
                category=ItemCategory.GTK_THEMES,
                path="/usr/share/themes/Adwaita",
                is_system=True,
                is_active=(active_gtk in ("Adwaita", "Default", "")),
                has_gtk3=True,
                details="Built-in GTK3 style",
                author="GNOME",
            )

        # 3. Wallpapers (User + System backgrounds)
        wall_dirs = [
            (Path.home() / "Pictures" / "Wallpapers", False),
            (Path.home() / ".local" / "share" / "backgrounds", False),
            (Path("/usr/share/backgrounds"), True),
        ]
        for wdir, is_sys in wall_dirs:
            if not wdir.is_dir():
                continue
            try:
                for root_w, _, files_w in os.walk(wdir):
                    for fname in files_w:
                        f = Path(root_w) / fname
                        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                            key = f"wall:{f.resolve()}"
                            if key in assets_by_name:
                                continue
                            clean_title = f.stem.replace("-", " ").replace("_", " ").strip()
                            assets_by_name[key] = LocalAsset(
                                name=f.name,
                                display_name=clean_title.title() if clean_title else f.stem,
                                category=ItemCategory.WALLPAPERS,
                                path=str(f),
                                is_system=is_sys,
                                is_active=active_wall.endswith(f.name),
                                details="System background" if is_sys else "Saved in Wallpapers",
                                author=self._infer_author(f.stem, str(f), is_sys),
                            )
            except Exception:
                continue

        # 4. GNOME Shell Extensions
        ext_roots = [
            (Path.home() / ".local" / "share" / "gnome-shell" / "extensions", False),
            (Path("/usr/share/gnome-shell/extensions"), True),
        ]
        for ext_root, is_sys in ext_roots:
            if not ext_root.is_dir():
                continue
            try:
                for entry in ext_root.iterdir():
                    if not entry.is_dir():
                        continue
                    uuid = entry.name
                    if f"ext:{uuid}" in assets_by_name:
                        continue
                    ext_title = uuid
                    ext_desc = ""
                    ext_meta = None
                    ext_ver = ""
                    meta_file = entry / "metadata.json"
                    if meta_file.exists():
                        try:
                            meta = json.loads(meta_file.read_text(encoding="utf-8", errors="ignore"))
                            if isinstance(meta, dict):
                                ext_meta = meta
                                ext_title = str(meta.get("name", uuid)).strip() or uuid
                                raw_desc = str(meta.get("description", "")).strip()
                                ext_desc = raw_desc.split("\n")[0].strip() if raw_desc else ""
                                if meta.get("version") is not None:
                                    ext_ver = str(meta["version"])
                                elif meta.get("version-name") is not None:
                                    ext_ver = str(meta["version-name"])
                        except Exception:
                            pass

                    assets_by_name[f"ext:{uuid}"] = LocalAsset(
                        name=uuid,
                        display_name=ext_title,
                        description=ext_desc,
                        category=ItemCategory.EXTENSIONS,
                        path=str(entry),
                        is_system=is_sys,
                        is_active=(uuid in enabled_exts),
                        has_prefs=(entry / "prefs.js").exists(),
                        details=ext_desc or uuid,
                        author=self._infer_author(uuid, str(entry), is_sys, ext_meta),
                        version=ext_ver,
                    )
            except Exception:
                continue

        res = list(assets_by_name.values())
        self._asset_cache = res
        self._asset_cache_time = time.time()
        return res

    def scan_all_local_assets(self, force: bool = False) -> List[LocalAsset]:
        return self.list_installed_assets(force_rescan=force)

    def apply_local_asset(self, asset: LocalAsset) -> bool:
        if asset.category == ItemCategory.ICONS:
            return self.set_icon_theme(asset.name)
        elif asset.category == ItemCategory.GTK_THEMES:
            return self.set_gtk_theme(asset.name)
        elif asset.category == ItemCategory.SHELL_THEMES:
            return self.set_shell_theme(asset.name)
        elif asset.category == ItemCategory.CURSORS:
            return self.set_cursor_theme(asset.name)
        elif asset.category == ItemCategory.WALLPAPERS:
            return self.set_wallpaper_uri(asset.path)
        elif asset.category == ItemCategory.EXTENSIONS:
            return self.toggle_extension(asset.name, not asset.is_active)
        return False

    def reset_if_active(self, asset: LocalAsset) -> None:
        if asset.category == ItemCategory.ICONS and self.get_icon_theme() == asset.name:
            self.set_icon_theme("Adwaita")
        elif asset.category == ItemCategory.GTK_THEMES:
            if self.get_gtk_theme() == asset.name:
                self.set_gtk_theme("Adwaita")
            if self.get_linked_gtk4_theme() == asset.name:
                self.reset_gtk4_theme()
        elif asset.category == ItemCategory.SHELL_THEMES and self.get_shell_theme() == asset.name:
            self.set_shell_theme("Default")
        elif asset.category == ItemCategory.CURSORS and self.get_cursor_theme() == asset.name:
            self.set_cursor_theme("Adwaita")

    def find_matching_local_assets(
        self,
        item: CustomizationItem,
        cached_assets: Optional[List[LocalAsset]] = None,
    ) -> List[LocalAsset]:
        assets = cached_assets if cached_assets is not None else self.list_installed_assets()
        tm = TaskManager.get_default()
        reg = tm.get_registered_entry(item)
        matched: List[LocalAsset] = []

        if reg:
            reg_paths = set(reg.get("paths", []))
            reg_theme = reg.get("theme_name", "").lower()
            for a in assets:
                if a.category != item.category:
                    continue
                if a.path in reg_paths or (reg_theme and a.name.lower() == reg_theme):
                    matched.append(a)
            if matched:
                return matched

        item_name_low = item.name.lower().strip()
        item_slug = re.sub(r"[^a-z0-9]+", "", item_name_low)
        ext_uuid = (item.uuid or item.item_id or "").lower().strip()

        for a in assets:
            if a.category != item.category:
                continue
            a_low = a.name.lower().strip()
            a_slug = re.sub(r"[^a-z0-9]+", "", a_low)
            if item.category == ItemCategory.EXTENSIONS and ext_uuid and a_low == ext_uuid:
                matched.append(a)
            elif a_low == item_name_low or (
                len(a_slug) >= 4 and len(item_slug) >= 4 and (a_slug == item_slug or a_slug in item_slug or item_slug in a_slug)
            ):
                matched.append(a)

        return matched

    def sync_item_state(
        self,
        item: CustomizationItem,
        cached_assets: Optional[List[LocalAsset]] = None,
    ) -> None:
        tm = TaskManager.get_default()
        reg = tm.get_registered_entry(item)
        matches = self.find_matching_local_assets(item, cached_assets)

        item.is_installed = bool(reg or matches)
        item.is_active = any(m.is_active for m in matches)

    def apply_customization_item(self, item: CustomizationItem) -> Tuple[bool, str]:
        matches = self.find_matching_local_assets(item)
        if matches:
            target = matches[0]
            if target.category == ItemCategory.EXTENSIONS:
                ok = self.toggle_extension(target.name, True)
            else:
                ok = self.apply_local_asset(target)
            item.is_active = ok
            return ok, target.display_name or target.name

        tm = TaskManager.get_default()
        reg = tm.get_registered_entry(item)
        theme_name = reg.get("theme_name", item.name) if reg else item.name
        paths = reg.get("paths", []) if reg else []

        ok = False
        if item.category == ItemCategory.ICONS:
            ok = self.set_icon_theme(theme_name)
        elif item.category == ItemCategory.GTK_THEMES:
            ok = self.set_gtk_theme(theme_name)
        elif item.category == ItemCategory.SHELL_THEMES:
            ok = self.set_shell_theme(theme_name)
        elif item.category == ItemCategory.CURSORS:
            ok = self.set_cursor_theme(theme_name)
        elif item.category == ItemCategory.WALLPAPERS and paths:
            ok = self.set_wallpaper_uri(paths[0])
        elif item.category == ItemCategory.EXTENSIONS:
            ok = self.toggle_extension(item.uuid or item.item_id or theme_name, True)

        if ok:
            item.is_active = True
        return ok, theme_name

    def get_desktop_state(self) -> DesktopState:
        return DesktopState(
            icon_theme=self.get_icon_theme(),
            gtk_theme=self.get_gtk_theme(),
            shell_theme=self.get_shell_theme(),
            cursor_theme=self.get_cursor_theme(),
            color_scheme=self.get_color_scheme(),
            accent_color=self.get_accent_color(),
            button_layout=self.get_button_layout(),
            center_new_windows=self.get_center_new_windows(),
            font_scaling_factor=self.get_font_scaling(),
            wallpaper_uri=self.get_wallpaper_uri(),
            enabled_extensions=self.list_enabled_extensions(),
            focus_mode=self.get_focus_mode(),
            font_antialiasing=self.get_font_antialiasing(),
            font_hinting=self.get_font_hinting(),
        )
