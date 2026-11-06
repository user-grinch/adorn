"""Native single-page Libadwaita About Dialog with all credits and links on the main view."""

from typing import Callable, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk


class AboutDialog(Adw.Dialog):
    """
    Modern single-page About dialog for Adorn:
    - App icon, name, version, and clean summary
    - Top-level Patreon donation and issue reporting actions
    - Direct visible credits and ecosystem recognition (all on main page, no hidden subpages)
    - GNU General Public License v3.0 (GPL-3.0-or-later)
    """

    def __init__(self, on_report_issue: Optional[Callable[[], None]] = None):
        super().__init__()
        self.on_report_issue = on_report_issue

        self.set_title("About Adorn")
        self.set_content_width(480)
        self.set_content_height(580)

        tb = Adw.ToolbarView()
        header = Adw.HeaderBar()
        tb.add_top_bar(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        clamp = Adw.Clamp(maximum_size=460)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        col.set_margin_top(20)
        col.set_margin_bottom(28)
        col.set_margin_start(16)
        col.set_margin_end(16)

        # Header: App Icon + Name + Version
        app_icon = Gtk.Image.new_from_icon_name("com.grinch.gnome.adorn")
        app_icon.set_pixel_size(72)
        app_icon.set_halign(Gtk.Align.CENTER)
        col.append(app_icon)

        name_lbl = Gtk.Label(label="Adorn")
        name_lbl.add_css_class("title-1")
        name_lbl.set_halign(Gtk.Align.CENTER)
        col.append(name_lbl)

        ver_lbl = Gtk.Label(label="Version 1.0")
        ver_lbl.add_css_class("caption")
        ver_lbl.add_css_class("dim-label")
        ver_lbl.set_halign(Gtk.Align.CENTER)
        col.append(ver_lbl)

        desc_lbl = Gtk.Label(
            label=(
                "GNOME Customization Storefront & Focused Desktop Tweaks.\n"
                "Browse, download, and manage GNOME Shell extensions, GTK & Shell themes, "
                "icon packs, cursors, and wallpapers."
            )
        )
        desc_lbl.set_wrap(True)
        desc_lbl.set_justify(Gtk.Justification.CENTER)
        desc_lbl.add_css_class("body")
        desc_lbl.set_margin_top(4)
        desc_lbl.set_margin_bottom(8)
        col.append(desc_lbl)

        # Group 1: Support & Community Links (Top-level)
        links_grp = Adw.PreferencesGroup()
        links_grp.set_title("Support &amp; Project Links")

        repo_row = Adw.ActionRow()
        repo_row.set_use_markup(False)
        repo_row.set_title("GitHub Repository")
        repo_row.set_subtitle("https://github.com/user-grinch/adorn")
        repo_row.set_activatable(True)
        repo_row.add_prefix(Gtk.Image.new_from_icon_name("network-server-symbolic"))
        repo_row.add_suffix(Gtk.Image.new_from_icon_name("external-link-symbolic"))
        repo_row.connect("activated", lambda _r: self._open_url("https://github.com/user-grinch/adorn"))
        links_grp.add(repo_row)

        patreon_row = Adw.ActionRow()
        patreon_row.set_use_markup(False)
        patreon_row.set_title("Donate on Patreon")
        patreon_row.set_subtitle("https://www.patreon.com/c/grinch_")
        patreon_row.set_activatable(True)
        patreon_row.add_prefix(Gtk.Image.new_from_icon_name("starred-symbolic"))
        patreon_row.add_suffix(Gtk.Image.new_from_icon_name("external-link-symbolic"))
        patreon_row.connect("activated", lambda _r: self._open_url("https://www.patreon.com/c/grinch_"))
        links_grp.add(patreon_row)

        report_row = Adw.ActionRow()
        report_row.set_use_markup(False)
        report_row.set_title("Report an Issue…")
        report_row.set_subtitle("GitHub Issues or email to user.grinch@gmail.com")
        report_row.set_activatable(True)
        report_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        report_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        report_row.connect("activated", self._on_report_clicked)
        links_grp.add(report_row)
        col.append(links_grp)

        # Group 2: Credits & Ecosystem (Directly visible on main page)
        credits_grp = Adw.PreferencesGroup()
        credits_grp.set_title("Credits &amp; Ecosystem")

        dev_row = Adw.ActionRow()
        dev_row.set_use_markup(False)
        dev_row.set_title("Lead Developer")
        dev_row.set_subtitle("Grinch_ <user.grinch@gmail.com>")
        dev_row.add_prefix(Gtk.Image.new_from_icon_name("avatar-default-symbolic"))
        credits_grp.add(dev_row)

        gnome_row = Adw.ActionRow()
        gnome_row.set_use_markup(False)
        gnome_row.set_title("GNOME & Libadwaita")
        gnome_row.set_subtitle("The GNOME Foundation & Community contributors")
        gnome_row.add_prefix(Gtk.Image.new_from_icon_name("preferences-system-symbolic"))
        credits_grp.add(gnome_row)

        api_row = Adw.ActionRow()
        api_row.set_use_markup(False)
        api_row.set_title("Storefront & Extensions APIs")
        api_row.set_subtitle("OpenDesktop.org / Pling & extensions.gnome.org")
        api_row.add_prefix(Gtk.Image.new_from_icon_name("network-server-symbolic"))
        credits_grp.add(api_row)
        col.append(credits_grp)

        # Legal & License
        legal_lbl = Gtk.Label(label="GNU General Public License v3.0 (GPL-3.0-or-later)")
        legal_lbl.add_css_class("caption")
        legal_lbl.add_css_class("dim-label")
        legal_lbl.set_halign(Gtk.Align.CENTER)
        legal_lbl.set_margin_top(8)
        col.append(legal_lbl)

        clamp.set_child(col)
        scroller.set_child(clamp)
        tb.set_content(scroller)
        self.set_child(tb)

    def _open_url(self, url: str) -> None:
        launcher = Gtk.UriLauncher.new(url)
        launcher.launch(self.get_root(), None, None)

    def _on_report_clicked(self, _row: Adw.ActionRow) -> None:
        self.close()
        if self.on_report_issue:
            self.on_report_issue()
