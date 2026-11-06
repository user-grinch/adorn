"""Report an Issue dialog for Adorn that composes an email directly to developer."""

import urllib.parse
from typing import Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk


class ReportIssueDialog(Adw.Dialog):
    """
    Modern Libadwaita dialog to report bugs or feature requests:
    - Select Category: Bug Report, Feature Request, Theme / Extension Issue, Store / Download Issue, Other
    - Summary input field
    - Detailed description text area
    - Opens default email client addressed directly to user.grinch@gmail.com with prefilled info
    """

    CATEGORIES = [
        "Bug Report",
        "Feature Request",
        "Theme / Extension Compatibility",
        "Store / Download Issue",
        "UI / Usability Feedback",
        "Other",
    ]

    def __init__(self, parent_window: Gtk.Window, shell_version: str = "51"):
        super().__init__()
        self.parent_window = parent_window
        self.shell_version = shell_version

        self.set_title("Report an Issue")
        self.set_content_width(520)
        self.set_content_height(580)

        toolbar = Adw.ToolbarView()
        self.set_child(toolbar)

        # HeaderBar
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title="Report an Issue", subtitle="Adorn 1.0")
        )

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("flat")
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        send_btn = Gtk.Button()
        send_btn.add_css_class("suggested-action")
        send_btn.set_child(
            Adw.ButtonContent(icon_name="mail-send-symbolic", label="Send Email")
        )
        send_btn.connect("clicked", self._on_send)
        header.pack_end(send_btn)

        toolbar.add_top_bar(header)

        # Preferences Page
        page = Adw.PreferencesPage()
        toolbar.set_content(page)

        # Group 1: Application & Category
        grp_cat = Adw.PreferencesGroup()
        grp_cat.set_title("App &amp; Category")
        page.add(grp_cat)

        app_row = Adw.ActionRow()
        app_row.set_title("Application")
        app_row.set_subtitle("Adorn v1.0")
        app_icon = Gtk.Image.new_from_icon_name("com.grinch.gnome.adorn")
        app_icon.set_pixel_size(24)
        app_row.add_prefix(app_icon)
        grp_cat.add(app_row)

        self.cat_row = Adw.ComboRow()
        self.cat_row.set_title("Issue Category")
        self.cat_row.set_model(Gtk.StringList.new(self.CATEGORIES))
        cat_icon = Gtk.Image.new_from_icon_name("starred-symbolic")
        cat_icon.set_pixel_size(20)
        self.cat_row.add_prefix(cat_icon)
        grp_cat.add(self.cat_row)

        self.summary_entry = Adw.EntryRow()
        self.summary_entry.set_title("Summary")
        self.summary_entry.connect("entry-activated", lambda _e: self._on_send(send_btn))
        grp_cat.add(self.summary_entry)

        # Group 2: Description
        grp_desc = Adw.PreferencesGroup()
        grp_desc.set_title("Details")
        page.add(grp_desc)

        desc_card = Adw.PreferencesRow()
        desc_card.set_activatable(False)

        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(140)
        scroller.set_has_frame(True)

        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_top_margin(10)
        self.text_view.set_bottom_margin(10)
        self.text_view.set_left_margin(12)
        self.text_view.set_right_margin(12)
        self.text_buf = self.text_view.get_buffer()
        self.text_buf.set_text(
            "Please describe the bug or feature request in detail:\n"
            "- What happened?\n"
            "- What was expected?\n"
            "- Steps to reproduce (if applicable):\n"
        )
        scroller.set_child(self.text_view)
        desc_card.set_child(scroller)
        grp_desc.add(desc_card)

        # Group 3: Destination & GitHub
        grp_dest = Adw.PreferencesGroup()
        grp_dest.set_title("Destination")
        page.add(grp_dest)

        gh_row = Adw.ActionRow()
        gh_row.set_title("Open GitHub Issues")
        gh_row.set_subtitle("https://github.com/user-grinch/adorn/issues")
        gh_row.set_activatable(True)
        gh_icon = Gtk.Image.new_from_icon_name("network-server-symbolic")
        gh_icon.set_pixel_size(20)
        gh_row.add_prefix(gh_icon)
        gh_row.add_suffix(Gtk.Image.new_from_icon_name("external-link-symbolic"))
        gh_row.connect("activated", lambda _r: self._open_github_issues())
        grp_dest.add(gh_row)

        dest_row = Adw.ActionRow()
        dest_row.set_title("Direct Email Inbox")
        dest_row.set_subtitle("user.grinch@gmail.com")
        mail_icon = Gtk.Image.new_from_icon_name("mail-send-symbolic")
        mail_icon.set_pixel_size(20)
        dest_row.add_prefix(mail_icon)
        grp_dest.add(dest_row)

    def _open_github_issues(self) -> None:
        launcher = Gtk.UriLauncher.new("https://github.com/user-grinch/adorn/issues")
        launcher.launch(self.get_root(), None, None)

    def _on_send(self, _btn: Gtk.Button) -> None:
        idx = self.cat_row.get_selected()
        cat_name = self.CATEGORIES[idx] if 0 <= idx < len(self.CATEGORIES) else "Feedback"
        user_summary = self.summary_entry.get_text().strip() or "Issue Report"

        start_iter, end_iter = self.text_buf.get_bounds()
        user_body = self.text_buf.get_text(start_iter, end_iter, False).strip()

        subject = f"[Adorn 1.0] [{cat_name}] {user_summary}"
        body = (
            f"Application: Adorn v1.0\n"
            f"Category: {cat_name}\n"
            f"GNOME Shell Version: {self.shell_version}\n"
            f"Summary: {user_summary}\n\n"
            f"--- Description ---\n"
            f"{user_body}\n"
        )

        mailto_uri = f"mailto:user.grinch@gmail.com?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body)}"
        Gtk.show_uri(self.parent_window, mailto_uri, Gdk.CURRENT_TIME)
        self.close()

        if hasattr(self.parent_window, "show_toast"):
            self.parent_window.show_toast("Opening mail client for user.grinch@gmail.com…")
