"""Application entrypoint for Adorn (GNOME Desktop Customizer)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from .ui.window import MainWindow


def _ensure_desktop_integration() -> None:
    """Ensure .desktop file and icon are installed in user XDG directories.

    Wayland compositors (like GNOME Shell/Mutter) and desktop taskbars (like Dash to Panel,
    Ubuntu Dock, etc.) require the application's .desktop file in $XDG_DATA_DIRS/applications
    and icon in $XDG_DATA_DIRS/icons to match the running window (app_id: com.grinch.gnome.adorn)
    and display its icon in the taskbar.
    """
    if os.path.exists("/.flatpak-info") or os.environ.get("FLATPAK_ID"):
        return

    try:
        repo_root = Path(__file__).resolve().parent.parent
        data_dir = repo_root / "data"
        icon_src = data_dir / "icons" / "com.grinch.gnome.adorn.svg"
        desktop_src = data_dir / "com.grinch.gnome.adorn.desktop"

        if not icon_src.is_file() or not desktop_src.is_file():
            return

        xdg_data = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))

        # 1. Install user icon
        icon_dst_dir = xdg_data / "icons" / "hicolor" / "scalable" / "apps"
        icon_dst_dir.mkdir(parents=True, exist_ok=True)
        icon_dst = icon_dst_dir / "com.grinch.gnome.adorn.svg"

        if not icon_dst.exists() or icon_dst.stat().st_size != icon_src.stat().st_size:
            shutil.copy2(icon_src, icon_dst)
            subprocess.run(
                ["gtk-update-icon-cache", "-q", "-t", "-f", str(xdg_data / "icons" / "hicolor")],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # 2. Install user desktop entry
        desktop_dst_dir = xdg_data / "applications"
        desktop_dst_dir.mkdir(parents=True, exist_ok=True)
        desktop_dst = desktop_dst_dir / "com.grinch.gnome.adorn.desktop"

        # Determine Exec path: prefer installed adorn executable, else point to local run.py
        if shutil.which("adorn"):
            exec_line = "Exec=adorn"
        else:
            launcher = repo_root / "run.py"
            exec_line = f"Exec={launcher.resolve()}"

        desktop_content = desktop_src.read_text(encoding="utf-8")
        if "Exec=adorn" in desktop_content:
            desktop_content = desktop_content.replace("Exec=adorn", exec_line)

        should_write = True
        if desktop_dst.exists():
            try:
                if desktop_dst.read_text(encoding="utf-8") == desktop_content:
                    should_write = False
            except Exception:
                pass

        if should_write:
            desktop_dst.write_text(desktop_content, encoding="utf-8")
            subprocess.run(
                ["update-desktop-database", str(desktop_dst_dir)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass


class AdornApplication(Adw.Application):
    """Main Adwaita application class with standard GNOME keyboard accelerators."""

    def __init__(self):
        super().__init__(
            application_id="com.grinch.gnome.adorn",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        GLib.set_application_name("Adorn")
        GLib.set_prgname("com.grinch.gnome.adorn")

    def do_startup(self):
        Adw.Application.do_startup(self)

        # Set default window icon name
        Gtk.Window.set_default_icon_name("com.grinch.gnome.adorn")

        # Register local data/icons path in icon theme
        display = Gdk.Display.get_default()
        if display:
            icon_theme = Gtk.IconTheme.get_for_display(display)
            local_icons = Path(__file__).resolve().parent.parent / "data" / "icons"
            if local_icons.exists():
                icon_theme.add_search_path(str(local_icons))

        # Ensure desktop integration for Wayland compositors / taskbars
        _ensure_desktop_integration()

        quit_act = Gio.SimpleAction.new("quit", None)
        quit_act.connect("activate", lambda _a, _p: self.quit())
        self.add_action(quit_act)

        self.set_accels_for_action("app.quit", ["<Control>q", "<Control>w"])
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("win.refresh", ["<Control>r", "F5"])
        self.set_accels_for_action("win.shortcuts", ["<Control>question"])
        self.set_accels_for_action("win.tab_explore", ["<Alt>1"])
        self.set_accels_for_action("win.tab_installed", ["<Alt>2"])
        self.set_accels_for_action("win.tab_downloads", ["<Alt>3"])
        self.set_accels_for_action("win.tab_tweaks", ["<Alt>4"])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(application=self)
        win.present()


def main():
    app = AdornApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
