"""Primary ApplicationWindow for Adorn with Libadwaita NavigationView, ViewStack, and live Downloads badge."""

import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ..core.ego_client import EgoClient
from ..core.gsettings import GSettingsManager
from ..core.installer import InstallationEngine
from ..core.models import CustomizationItem, DownloadableFile, ItemCategory
from ..core.ocs_client import OcsClient
from ..core.task_manager import TaskManager
from .components.about_dialog import AboutDialog
from .components.detail_page import DetailPage
from .components.report_issue_dialog import ReportIssueDialog
from .views.downloads_view import DownloadsView
from .views.explore_view import ExploreView
from .views.installed_view import InstalledView
from .views.tweaks_view import TweaksView


class MainWindow(Adw.ApplicationWindow):
    """
    Modern GNOME HIG MainWindow:
    - Root Adw.NavigationView so DetailPages push cleanly with native HeaderBar back buttons
    - 4 top-level views: Explore, Installed, Downloads (with live badge), Tweaks
    - Adaptive Adw.ViewSwitcherTitle + bottom Adw.ViewSwitcherBar at < 720sp
    - Tab-aware Ctrl+F search and Ctrl+R refresh
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.set_title("Adorn")
        self.set_default_size(1160, 800)
        self.set_size_request(360, 460)
        self.set_icon_name("com.grinch.gnome.adorn")

        self.gsettings = GSettingsManager()
        self.ocs = OcsClient()
        self.ego = EgoClient()
        self.installer = InstallationEngine()
        self.task_manager = TaskManager.get_default()

        self._search_binding: Optional[GObject.Binding] = None

        self._load_css()
        self._setup_actions()

        # Register local data/icons path so com.grinch.gnome.adorn icon is loaded
        display = Gdk.Display.get_default()
        if display:
            icon_theme = Gtk.IconTheme.get_for_display(display)
            local_icons = Path(__file__).resolve().parent.parent.parent / "data" / "icons"
            if local_icons.exists():
                icon_theme.add_search_path(str(local_icons))

        # ToastOverlay -> Root NavigationView
        self.toast_overlay = Adw.ToastOverlay()
        self.root_nav = Adw.NavigationView()
        self.root_nav.connect("notify::visible-page", self._on_nav_visible_page_changed)
        self.toast_overlay.set_child(self.root_nav)
        self.set_content(self.toast_overlay)

        # Root NavigationPage
        self.root_page = Adw.NavigationPage(title="Adorn", tag="main-root")
        self.toolbar_view = Adw.ToolbarView()
        self.root_page.set_child(self.toolbar_view)

        # Top HeaderBar with ViewSwitcherTitle
        self.header = Adw.HeaderBar()
        self.view_stack = Adw.ViewStack()

        self.switcher_title = Adw.ViewSwitcherTitle()
        self.switcher_title.set_stack(self.view_stack)
        self.switcher_title.set_title("Adorn")
        self.header.set_title_widget(self.switcher_title)

        # Left Header Controls: Search + Refresh
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic")
        self.search_btn.set_tooltip_text("Search (Ctrl+F)")
        self.search_btn.connect("toggled", self._on_search_btn_toggled)
        self.header.pack_start(self.search_btn)

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_btn.set_tooltip_text("Refresh (Ctrl+R)")
        self.refresh_btn.connect("clicked", self._on_refresh_clicked)
        self.header.pack_start(self.refresh_btn)

        # Right Primary MenuButton
        menu = Gio.Menu()
        folder_sec = Gio.Menu()
        folder_sec.append("Open Themes Folder", "win.open_themes_dir")
        folder_sec.append("Open Icons Folder", "win.open_icons_dir")
        folder_sec.append("Clear Finished Downloads", "win.clear_tasks")
        menu.append_section(None, folder_sec)

        app_sec = Gio.Menu()
        app_sec.append("Donate on Patreon", "win.donate")
        app_sec.append("Report an Issue…", "win.report_issue")
        app_sec.append("Keyboard Shortcuts", "win.shortcuts")
        app_sec.append("About Adorn", "win.about")
        menu.append_section(None, app_sec)

        menu_btn = Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=menu, primary=True
        )
        menu_btn.set_tooltip_text("Main Menu")
        self.header.pack_end(menu_btn)

        self.toolbar_view.add_top_bar(self.header)

        # 1. Explore View (Storefront)
        self.explore_view = ExploreView(
            ocs_client=self.ocs,
            ego_client=self.ego,
            installer=self.installer,
            gsettings=self.gsettings,
            on_open_detail=self.open_detail_page,
            toast_cb=self.show_toast,
            download_toast_cb=self.show_download_toast,
        )
        self.explore_page = self.view_stack.add_titled_with_icon(
            self.explore_view,
            "explore",
            "Explore",
            "compass-symbolic",
        )

        # 2. Installed View (Unified Themes, Icons, Cursors, Shell, Wallpapers & Extensions)
        self.installed_view = InstalledView(
            gsettings=self.gsettings,
            installer=self.installer,
            ego_client=self.ego,
            toast_cb=self.show_toast,
        )
        self.installed_page = self.view_stack.add_titled_with_icon(
            self.installed_view,
            "installed",
            "Installed",
            "package-x-generic-symbolic",
        )

        # 3. Downloads View (Active Transfers, Completed, Failed & Recently Removed)
        self.downloads_view = DownloadsView(
            installer=self.installer,
            gsettings=self.gsettings,
            ocs_client=self.ocs,
            on_browse_store=lambda: self.switch_to_tab("explore"),
            on_open_detail=self.open_detail_page,
            toast_cb=self.show_toast,
        )
        self.downloads_page = self.view_stack.add_titled_with_icon(
            self.downloads_view,
            "downloads",
            "Downloads",
            "folder-download-symbolic",
        )

        # 4. Tweaks View (Focused non-duplicated GNOME 51 power-user tweaks)
        self.tweaks_view = TweaksView(
            gsettings=self.gsettings,
            show_toast_cb=self.show_toast,
        )
        self.tweaks_page = self.view_stack.add_titled_with_icon(
            self.tweaks_view,
            "tweaks",
            "Tweaks",
            "org.gnome.tweaks-symbolic",
        )

        self.toolbar_view.set_content(self.view_stack)

        # Bottom ViewSwitcherBar for narrow widths (< 720sp)
        self.bottom_bar = Adw.ViewSwitcherBar()
        self.bottom_bar.set_stack(self.view_stack)
        self.toolbar_view.add_bottom_bar(self.bottom_bar)

        self.switcher_title.bind_property(
            "title-visible",
            self.bottom_bar,
            "reveal",
            GObject.BindingFlags.SYNC_CREATE,
        )

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.bottom_bar, "reveal", True)
        self.add_breakpoint(bp)

        self.root_nav.add(self.root_page)

        self.view_stack.connect("notify::visible-child-name", self._on_tab_changed)
        self._sync_tab_search_binding()

        self.task_manager.subscribe(self._on_tasks_changed)
        self._on_tasks_changed()

        self.connect("realize", self._on_window_realized)
        self._on_window_width_changed(1160)

    # =========================================================================
    # DETAIL PAGE NAVIGATION & ACTIONS
    # =========================================================================

    def open_detail_page(self, item: CustomizationItem) -> None:
        self.gsettings.sync_item_state(item)
        page = DetailPage(
            item=item,
            ocs_client=self.ocs,
            on_install_requested=self._on_detail_install,
            on_apply_requested=self._on_detail_apply,
            on_remove_requested=self._on_detail_remove,
            shell_version=self.ego.shell_version,
        )
        self.root_nav.push(page)

    def _on_detail_install(
        self,
        item: CustomizationItem,
        selected_file: Optional[DownloadableFile],
        progress_cb: Callable[[float, str], None],
        done_cb: Callable[[bool, str], None],
    ) -> None:
        self.show_download_toast(f"Downloading {item.name}…")

        def _wrapped_done(ok: bool, msg: str) -> None:
            done_cb(ok, msg)
            if ok:
                self.show_toast(f"Installed {item.name}")
            elif msg and msg != "Cancelled":
                self.show_download_toast(f"Failed: {msg}")

        self.installer.start_install_task(
            item=item,
            file_info=selected_file,
            progress_cb=progress_cb,
            done_cb=_wrapped_done,
        )

    def _on_detail_apply(self, item: CustomizationItem) -> None:
        ok, applied_name = self.gsettings.apply_customization_item(item)
        if ok:
            verb = "Enabled" if item.category == ItemCategory.EXTENSIONS else "Applied"
            self.show_toast(f"{verb} {applied_name}")
            self.installed_view.refresh_assets(run_async=True)
        else:
            if (
                item.category == ItemCategory.SHELL_THEMES
                and not self.gsettings.is_user_theme_extension_installed()
            ):
                self.show_toast(
                    "Install the User Themes extension in Installed → Shell Themes first."
                )
            else:
                self.show_toast(f"Could not apply {item.name}")

    def _on_detail_remove(
        self, item: CustomizationItem, done_cb: Callable[[bool, str], None]
    ) -> None:
        ok, msg, _freed = self.installer.uninstall_customization_item(
            item, self.gsettings
        )
        self.show_toast(msg)
        done_cb(ok, msg)

    # =========================================================================
    # RESPONSIVE LAYOUT & SEARCH / TAB BINDINGS
    # =========================================================================

    def _on_window_realized(self, _widget: Gtk.Widget) -> None:
        surf = self.get_surface()
        if surf:
            surf.connect(
                "notify::width",
                lambda s, _p: self._on_window_width_changed(s.get_width()),
            )
            w = surf.get_width()
            if w > 1:
                self._on_window_width_changed(w)

    def _on_window_width_changed(self, width: int) -> None:
        if width <= 1:
            return
        self.explore_view.adapt_columns(width)
        self.installed_view.adapt_columns(width)
        self.tweaks_view.adapt_columns(width)

    def _on_nav_visible_page_changed(self, _nav: Adw.NavigationView, _p) -> None:
        self._sync_tab_search_binding()

    def _on_tab_changed(self, _stack: Adw.ViewStack, _p) -> None:
        self._sync_tab_search_binding()
        cur = self.view_stack.get_visible_child_name()
        if cur == "tweaks":
            self.tweaks_view.refresh()

    def _sync_tab_search_binding(self) -> None:
        if self._search_binding:
            self._search_binding.unbind()
            self._search_binding = None

        # Detach key capture by default so inactive search bars never steal keystrokes
        self.explore_view.search_bar.set_key_capture_widget(None)
        self.installed_view.search_bar.set_key_capture_widget(None)

        vis_page = self.root_nav.get_visible_page()
        if vis_page is not None and vis_page.get_tag() != "main-root":
            return

        cur = self.view_stack.get_visible_child_name()
        if cur == "explore":
            sb = self.explore_view.search_bar
            sb.set_key_capture_widget(self)
            self.search_btn.set_sensitive(True)
            self.search_btn.set_active(sb.get_search_mode())
            self._search_binding = sb.bind_property(
                "search-mode-enabled",
                self.search_btn,
                "active",
                GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
            )
        elif cur == "installed":
            sb = self.installed_view.search_bar
            sb.set_key_capture_widget(self)
            self.search_btn.set_sensitive(True)
            self.search_btn.set_active(sb.get_search_mode())
            self._search_binding = sb.bind_property(
                "search-mode-enabled",
                self.search_btn,
                "active",
                GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
            )
        else:
            self.search_btn.set_active(False)
            self.search_btn.set_sensitive(False)

    def _on_search_btn_toggled(self, btn: Gtk.ToggleButton) -> None:
        if not btn.get_active():
            return
        cur = self.view_stack.get_visible_child_name()
        if cur == "explore":
            self.explore_view.focus_search()
        elif cur == "installed":
            self.installed_view.focus_search()

    def switch_to_tab(self, tab_name: str) -> None:
        self.root_nav.pop_to_tag("main-root")
        self.view_stack.set_visible_child_name(tab_name)

    def _on_refresh_clicked(self, _btn: Optional[Gtk.Button] = None) -> None:
        self.root_nav.pop_to_tag("main-root")
        cur = self.view_stack.get_visible_child_name()
        if cur == "explore":
            self.explore_view.refresh_catalog()
            self.show_toast("Refreshing storefront catalog…")
        elif cur == "installed":
            self.installed_view.refresh_assets(run_async=True)
            self.show_toast("Rescanned installed customizations")
        elif cur == "downloads":
            self.downloads_view.rebuild()
            self.show_toast("Updated downloads list")
        elif cur == "tweaks":
            self.tweaks_view.refresh()
            self.show_toast("Refreshed desktop settings")

    # =========================================================================
    # WINDOW ACTIONS & DIALOGS
    # =========================================================================

    def _setup_actions(self) -> None:
        search_act = Gio.SimpleAction.new("search", None)
        search_act.connect("activate", self._on_action_search)
        self.add_action(search_act)

        refresh_act = Gio.SimpleAction.new("refresh", None)
        refresh_act.connect("activate", lambda _a, _p: self._on_refresh_clicked())
        self.add_action(refresh_act)

        for action_name, tab_id in (
            ("tab_explore", "explore"),
            ("tab_installed", "installed"),
            ("tab_downloads", "downloads"),
            ("tab_tweaks", "tweaks"),
        ):
            act = Gio.SimpleAction.new(action_name, None)
            act.connect("activate", lambda _a, _p, t=tab_id: self.switch_to_tab(t))
            self.add_action(act)

        open_themes_act = Gio.SimpleAction.new("open_themes_dir", None)
        open_themes_act.connect(
            "activate",
            lambda _a, _p: self._open_local_dir(Path.home() / ".local" / "share" / "themes"),
        )
        self.add_action(open_themes_act)

        open_icons_act = Gio.SimpleAction.new("open_icons_dir", None)
        open_icons_act.connect(
            "activate",
            lambda _a, _p: self._open_local_dir(Path.home() / ".local" / "share" / "icons"),
        )
        self.add_action(open_icons_act)

        clear_act = Gio.SimpleAction.new("clear_tasks", None)
        clear_act.connect(
            "activate", lambda _a, _p: self.task_manager.clear_finished_tasks()
        )
        self.add_action(clear_act)

        shortcuts_act = Gio.SimpleAction.new("shortcuts", None)
        shortcuts_act.connect("activate", self._on_show_shortcuts)
        self.add_action(shortcuts_act)

        about_act = Gio.SimpleAction.new("about", None)
        about_act.connect("activate", self._on_about)
        self.add_action(about_act)

        donate_act = Gio.SimpleAction.new("donate", None)
        donate_act.connect("activate", self._on_donate)
        self.add_action(donate_act)

        report_act = Gio.SimpleAction.new("report_issue", None)
        report_act.connect("activate", self._on_report_issue)
        self.add_action(report_act)

    def _on_donate(self, _action: Gio.SimpleAction, _param) -> None:
        Gtk.show_uri(self, "https://www.patreon.com/c/grinch_", Gdk.CURRENT_TIME)

    def show_report_issue_dialog(self) -> None:
        dlg = ReportIssueDialog(
            parent_window=self,
            shell_version=self.ego.shell_version,
        )
        dlg.present(self)

    def _on_report_issue(self, _action: Gio.SimpleAction, _param) -> None:
        self.show_report_issue_dialog()

    def _on_action_search(self, _action: Gio.SimpleAction, _param) -> None:
        self.root_nav.pop_to_tag("main-root")
        cur = self.view_stack.get_visible_child_name()
        if cur not in ("explore", "installed"):
            self.view_stack.set_visible_child_name("explore")
            cur = "explore"
        if cur == "explore":
            self.explore_view.focus_search()
        else:
            self.installed_view.focus_search()

    def _open_local_dir(self, path_obj: Path) -> None:
        path_obj.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(path_obj)])
        except Exception:
            self.show_toast(str(path_obj))

    def _on_show_shortcuts(self, _action: Gio.SimpleAction, _param) -> None:
        dlg = Adw.ShortcutsDialog()

        nav_sec = Adw.ShortcutsSection(title="Navigation")
        nav_sec.add(Adw.ShortcutsItem(title="Explore Storefront", accelerator="<Alt>1"))
        nav_sec.add(Adw.ShortcutsItem(title="Installed Library", accelerator="<Alt>2"))
        nav_sec.add(Adw.ShortcutsItem(title="Downloads & Activity", accelerator="<Alt>3"))
        nav_sec.add(Adw.ShortcutsItem(title="Desktop Tweaks", accelerator="<Alt>4"))
        nav_sec.add(Adw.ShortcutsItem(title="Go Back", accelerator="<Alt>Left"))
        dlg.add(nav_sec)

        gen_sec = Adw.ShortcutsSection(title="General")
        gen_sec.add(Adw.ShortcutsItem(title="Search Store or Library", accelerator="<Control>f"))
        gen_sec.add(Adw.ShortcutsItem(title="Refresh Current View", accelerator="<Control>r"))
        gen_sec.add(Adw.ShortcutsItem(title="Keyboard Shortcuts", accelerator="<Control>question"))
        gen_sec.add(Adw.ShortcutsItem(title="Close Window", accelerator="<Control>w"))
        gen_sec.add(Adw.ShortcutsItem(title="Quit Application", accelerator="<Control>q"))
        dlg.add(gen_sec)

        dlg.present(self)

    def _on_about(self, _action: Gio.SimpleAction, _param) -> None:
        dlg = AboutDialog(on_report_issue=self.show_report_issue_dialog)
        dlg.present(self)

    # =========================================================================
    # BADGES, TOASTS & STYLESHEET
    # =========================================================================

    def _on_tasks_changed(self) -> None:
        active_count = len(self.task_manager.get_active_downloads())
        self.downloads_page.set_badge_number(active_count)
        self.downloads_page.set_needs_attention(active_count > 0)
        if hasattr(self, "installed_view"):
            try:
                self.installed_view.refresh_assets()
            except Exception:
                pass

    def _load_css(self) -> None:
        provider = Gtk.CssProvider()
        css_path = os.path.join(os.path.dirname(__file__), "styles.css")
        if os.path.exists(css_path):
            provider.load_from_path(css_path)
            disp = Gdk.Display.get_default()
            if disp:
                Gtk.StyleContext.add_provider_for_display(
                    disp,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )

    def show_toast(self, message: str) -> None:
        toast = Adw.Toast.new(message)
        toast.set_use_markup(False)
        toast.set_timeout(3)
        self.toast_overlay.add_toast(toast)

    def show_download_toast(self, message: str) -> None:
        toast = Adw.Toast.new(message)
        toast.set_use_markup(False)
        toast.set_timeout(4)
        if self.view_stack.get_visible_child_name() != "downloads":
            toast.set_button_label("Downloads")
            toast.connect("button-clicked", lambda _t: self.switch_to_tab("downloads"))
        self.toast_overlay.add_toast(toast)
