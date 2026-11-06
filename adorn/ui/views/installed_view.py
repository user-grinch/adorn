"""Unified Installed Library View for Adorn with NavigationSplitView, filters, and pagination."""

import math
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from adorn.core.ego_client import EgoClient
from adorn.core.gsettings import GSettingsManager
from adorn.core.installer import InstallationEngine
from adorn.core.models import ItemCategory, LocalAsset
from adorn.core.task_manager import TaskManager
from adorn.ui.components.pagination_bar import PaginationBar
from adorn.ui.image_utils import (
    load_cropped_texture_async,
    load_fixed_square_texture_async,
)


class InstalledView(Gtk.Box):
    """
    Modern GNOME Installed Customizations Manager:
    - Adaptive Adw.NavigationSplitView sidebar:
      Active Setup, Extensions, Shell Themes, GTK Themes, Icons, Cursors, Wallpapers
    - Contextual Adw.Banner for User Themes extension in Shell Themes
    - Optional GTK4 / Libadwaita linking for themes shipping gtk-4.0
    - Search, Source/Status filters, and PaginationBar for large local collections
    """

    SECTIONS = [
        ("overview", "Active Setup", "desktop-symbolic"),
        (ItemCategory.EXTENSIONS.value, "Extensions", ItemCategory.EXTENSIONS.icon_name),
        (ItemCategory.SHELL_THEMES.value, "Shell Themes", ItemCategory.SHELL_THEMES.icon_name),
        (ItemCategory.GTK_THEMES.value, "GTK Themes", ItemCategory.GTK_THEMES.icon_name),
        (ItemCategory.ICONS.value, "Icon Themes", ItemCategory.ICONS.icon_name),
        (ItemCategory.CURSORS.value, "Cursor Themes", ItemCategory.CURSORS.icon_name),
        (ItemCategory.WALLPAPERS.value, "Wallpapers", ItemCategory.WALLPAPERS.icon_name),
    ]

    def __init__(
        self,
        gsettings: GSettingsManager,
        installer: InstallationEngine,
        ego_client: EgoClient,
        toast_cb: Callable[[str], None],
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.gsettings = gsettings
        self.installer = installer
        self.ego = ego_client
        self.toast_cb = toast_cb
        self.task_manager = TaskManager.get_default()

        self.all_assets: List[LocalAsset] = []
        self.active_section: str = "overview"
        self.search_query: str = ""
        self.source_filter: str = "all"  # "all", "user", "system"
        self.ext_status_filter: str = "all"  # "all", "active", "disabled"
        self.current_page: int = 1
        self.page_size: int = 20
        self._is_scanning: bool = False
        self._updating_master_sw: bool = False
        self._wallpaper_cols: int = 2

        # Collapsible Search Bar
        self.search_bar = Gtk.SearchBar()
        self.search_bar.set_show_close_button(True)
        search_clamp = Adw.Clamp(maximum_size=640, tightening_threshold=480)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filter installed extensions, themes, icons & wallpapers…")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        search_clamp.set_child(self.search_entry)
        self.search_bar.set_child(search_clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.connect("notify::search-mode-enabled", self._on_search_mode_toggled)
        self.append(self.search_bar)

        # Adaptive NavigationSplitView
        self.split_view = Adw.NavigationSplitView()
        self.split_view.set_vexpand(True)
        self.split_view.set_hexpand(True)
        self.split_view.set_min_sidebar_width(220)
        self.split_view.set_max_sidebar_width(265)
        self.append(self.split_view)

        # Sidebar Page
        sidebar_page = Adw.NavigationPage(title="Installed", tag="installed-sidebar")
        sidebar_scroller = Gtk.ScrolledWindow()
        sidebar_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.connect("row-selected", self._on_sidebar_row_selected)

        self._sidebar_badges: Dict[str, Gtk.Label] = {}
        self._sidebar_rows: Dict[str, Gtk.ListBoxRow] = {}

        for sec_id, sec_title, sec_icon in self.SECTIONS:
            lrow = Gtk.ListBoxRow()
            lrow._sec_id = sec_id  # type: ignore[attr-defined]
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            hbox.set_margin_top(8)
            hbox.set_margin_bottom(8)
            hbox.set_margin_start(10)
            hbox.set_margin_end(10)

            ic = Gtk.Image.new_from_icon_name(sec_icon)
            ic.set_pixel_size(18)
            hbox.append(ic)

            lbl = Gtk.Label(label=sec_title)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_hexpand(True)
            lbl.set_ellipsize(3)
            hbox.append(lbl)

            if sec_id != "overview":
                badge = Gtk.Label(label="0")
                badge.add_css_class("sidebar-count")
                badge.set_valign(Gtk.Align.CENTER)
                hbox.append(badge)
                self._sidebar_badges[sec_id] = badge

            lrow.set_child(hbox)
            self.sidebar_list.append(lrow)
            self._sidebar_rows[sec_id] = lrow

        sidebar_scroller.set_child(self.sidebar_list)
        sidebar_page.set_child(sidebar_scroller)
        self.split_view.set_sidebar(sidebar_page)

        # Content Page
        self.content_page = Adw.NavigationPage(title="Active Setup", tag="installed-content")
        content_toolbar = Adw.ToolbarView()
        self.content_page.set_child(content_toolbar)

        # Sub-header bar shown only when split_view is collapsed so user can return to sidebar
        self.collapsed_bar = Adw.HeaderBar()
        self.collapsed_bar.set_show_end_title_buttons(False)
        self.collapsed_bar.set_show_start_title_buttons(False)
        back_side_btn = Gtk.Button()
        back_side_btn.add_css_class("flat")
        back_side_btn.set_child(
            Adw.ButtonContent(icon_name="sidebar-show-symbolic", label="Categories")
        )
        back_side_btn.connect("clicked", lambda _b: self.split_view.set_show_content(False))
        self.collapsed_bar.pack_start(back_side_btn)
        self.collapsed_bar.set_visible(False)
        content_toolbar.add_top_bar(self.collapsed_bar)

        # Contextual User Themes Banner (revealed only in Shell Themes when needed)
        self.user_theme_banner = Adw.Banner.new(
            "The User Themes extension is required to apply custom GNOME Shell themes."
        )
        self.user_theme_banner.set_button_label("Install Extension")
        self.user_theme_banner.connect("button-clicked", self._on_user_theme_banner_action)
        self.user_theme_banner.set_revealed(False)
        content_toolbar.add_top_bar(self.user_theme_banner)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_vexpand(True)
        self.scroller.set_hexpand(True)
        content_toolbar.set_content(self.scroller)

        self.clamp = Adw.Clamp(maximum_size=880, tightening_threshold=580)
        self.scroller.set_child(self.clamp)

        self.body_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.body_col.set_margin_top(20)
        self.body_col.set_margin_bottom(36)
        self.body_col.set_margin_start(20)
        self.body_col.set_margin_end(20)
        self.clamp.set_child(self.body_col)

        self.split_view.set_content(self.content_page)

        # Select Overview initially
        first_row = self._sidebar_rows.get("overview")
        if first_row:
            self.sidebar_list.select_row(first_row)

        self.task_manager.subscribe(lambda: self.refresh_assets(run_async=True))
        self.refresh_assets(run_async=True)

    def adapt_columns(self, width: int) -> None:
        is_narrow = width < 720
        self.split_view.set_collapsed(is_narrow)
        self.collapsed_bar.set_visible(is_narrow)

        avail_w = width - (0 if is_narrow else 250)
        new_wcols = 2 if avail_w >= 520 else 1
        if new_wcols != self._wallpaper_cols:
            self._wallpaper_cols = new_wcols
            if self.active_section == ItemCategory.WALLPAPERS.value:
                self._render_active_section()

    def focus_search(self) -> None:
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()

    def jump_to_section(self, sec_id: str) -> None:
        row = self._sidebar_rows.get(sec_id)
        if row:
            self.sidebar_list.select_row(row)
            self.split_view.set_show_content(True)

    def _on_search_mode_toggled(self, search_bar: Gtk.SearchBar, _p) -> None:
        if not search_bar.get_search_mode() and self.search_entry.get_text():
            self.search_entry.set_text("")
            self.search_query = ""
            self.current_page = 1
            self._render_active_section()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        q = entry.get_text().strip().lower()
        if q != self.search_query:
            self.search_query = q
            self.current_page = 1
            if q and self.active_section == "overview":
                # Automatically jump to Extensions or matching category when searching from Overview
                self.jump_to_section(ItemCategory.EXTENSIONS.value)
            else:
                self._render_active_section()

    def _on_sidebar_row_selected(
        self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]
    ) -> None:
        if not row:
            return
        sec_id = getattr(row, "_sec_id", "overview")
        self.active_section = sec_id
        self.current_page = 1
        self.split_view.set_show_content(True)

        for sid, stitle, _ in self.SECTIONS:
            if sid == sec_id:
                self.content_page.set_title(stitle)
                break

        vadj = self.scroller.get_vadjustment()
        if vadj:
            vadj.set_value(0)
        self._render_active_section()

    def refresh_assets(self, run_async: bool = True) -> None:
        if self._is_scanning:
            return
        self._is_scanning = True

        def _do_scan():
            raw = self.gsettings.scan_all_local_assets(force=True)

            def _apply():
                self._is_scanning = False
                self.all_assets = raw
                self._update_sidebar_counts()
                self._render_active_section()
                return False

            GLib.idle_add(_apply)

        if run_async:
            threading.Thread(target=_do_scan, daemon=True).start()
        else:
            self.all_assets = self.gsettings.scan_all_local_assets(force=True)
            self._is_scanning = False
            self._update_sidebar_counts()
            self._render_active_section()

    def _update_sidebar_counts(self) -> None:
        counts: Dict[str, int] = {k: 0 for k in self._sidebar_badges}
        for a in self.all_assets:
            if a.category.value in counts:
                counts[a.category.value] += 1
        for k, badge in self._sidebar_badges.items():
            badge.set_label(str(counts.get(k, 0)))

    @staticmethod
    def _clear_box(box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _update_user_theme_banner(self) -> None:
        if self.active_section != ItemCategory.SHELL_THEMES.value:
            self.user_theme_banner.set_revealed(False)
            return

        if not self.gsettings.is_user_theme_extension_installed():
            self.user_theme_banner.set_title(
                "The User Themes extension is required to apply custom GNOME Shell themes."
            )
            self.user_theme_banner.set_button_label("Install Extension")
            self.user_theme_banner.set_revealed(True)
        elif not self.gsettings.is_user_theme_extension_enabled():
            self.user_theme_banner.set_title(
                "The User Themes extension is installed but currently disabled."
            )
            self.user_theme_banner.set_button_label("Enable Extension")
            self.user_theme_banner.set_revealed(True)
        else:
            self.user_theme_banner.set_revealed(False)

    def _on_user_theme_banner_action(self, _banner: Adw.Banner) -> None:
        if self.gsettings.is_user_theme_extension_installed():
            if self.gsettings.enable_user_theme_extension():
                self.toast_cb("Enabled User Themes extension")
                self.refresh_assets(run_async=True)
            return

        self.user_theme_banner.set_button_label("Installing…")

        def worker():
            item = self.ego.fetch_user_theme_extension()
            if item and item.download_files:
                def _done(ok: bool, msg: str):
                    if ok:
                        self.gsettings.refresh_user_theme_schema()
                        self.gsettings.enable_user_theme_extension()
                        self.toast_cb("Installed & enabled User Themes extension")
                        self.refresh_assets(run_async=True)
                    else:
                        self.toast_cb(f"Failed to install User Themes: {msg}")
                        self._update_user_theme_banner()

                self.installer.start_install_task(item=item, file_info=item.download_files[0], done_cb=_done)
            else:
                GLib.idle_add(self.toast_cb, "Could not fetch User Themes extension from extensions.gnome.org")
                GLib.idle_add(self._update_user_theme_banner)

        threading.Thread(target=worker, daemon=True).start()

    # =========================================================================
    # SECTION RENDERING
    # =========================================================================

    def _render_active_section(self) -> None:
        self._clear_box(self.body_col)
        self._update_user_theme_banner()

        if self.active_section == "overview":
            self._render_overview_page()
            return

        try:
            cat = ItemCategory(self.active_section)
        except ValueError:
            self._render_overview_page()
            return

        self._render_category_page(cat)

    def _render_overview_page(self) -> None:
        hdr_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t_lbl = Gtk.Label(label="Active Desktop Customizations")
        t_lbl.add_css_class("title-2")
        t_lbl.set_halign(Gtk.Align.START)
        hdr_col.append(t_lbl)

        s_lbl = Gtk.Label(
            label="Summary of currently active themes, icons, extensions, and wallpaper."
        )
        s_lbl.add_css_class("dim-label")
        s_lbl.set_halign(Gtk.Align.START)
        s_lbl.set_wrap(True)
        hdr_col.append(s_lbl)
        self.body_col.append(hdr_col)

        grp = Adw.PreferencesGroup()
        grp.set_title("Current Desktop State")

        active_ext_assets = [a for a in self.all_assets if a.category == ItemCategory.EXTENSIONS and a.is_active]
        master_on = self.gsettings.get_extensions_master_enabled()
        ext_sub = (
            f"{len(active_ext_assets)} enabled"
            if master_on
            else "All user extensions paused (master toggle off)"
        )
        grp.add(
            self._make_overview_row(
                "GNOME Extensions",
                ext_sub,
                ItemCategory.EXTENSIONS.icon_name,
                lambda: self.jump_to_section(ItemCategory.EXTENSIONS.value),
            )
        )

        shell_val = self.gsettings.get_shell_theme()
        grp.add(
            self._make_overview_row(
                "Shell Theme",
                shell_val,
                ItemCategory.SHELL_THEMES.icon_name,
                lambda: self.jump_to_section(ItemCategory.SHELL_THEMES.value),
            )
        )

        gtk_val = self.gsettings.get_gtk_theme()
        linked_gtk4 = self.gsettings.get_linked_gtk4_theme()
        gtk_sub = f"{gtk_val} (GTK4 linked: {linked_gtk4})" if linked_gtk4 else f"{gtk_val} (Legacy GTK3)"
        grp.add(
            self._make_overview_row(
                "GTK Theme",
                gtk_sub,
                ItemCategory.GTK_THEMES.icon_name,
                lambda: self.jump_to_section(ItemCategory.GTK_THEMES.value),
                reset_cb=(
                    self._on_reset_gtk_all
                    if (gtk_val not in ("Adwaita", "Default") or linked_gtk4)
                    else None
                ),
            )
        )

        icon_val = self.gsettings.get_icon_theme()
        grp.add(
            self._make_overview_row(
                "Icon Theme",
                icon_val,
                ItemCategory.ICONS.icon_name,
                lambda: self.jump_to_section(ItemCategory.ICONS.value),
                reset_cb=(
                    self._on_reset_icons if icon_val != "Adwaita" else None
                ),
            )
        )

        cursor_val = self.gsettings.get_cursor_theme()
        grp.add(
            self._make_overview_row(
                "Cursor Theme",
                cursor_val,
                ItemCategory.CURSORS.icon_name,
                lambda: self.jump_to_section(ItemCategory.CURSORS.value),
                reset_cb=(
                    self._on_reset_cursors if cursor_val != "Adwaita" else None
                ),
            )
        )

        wall_uri = self.gsettings.get_wallpaper_uri()
        wall_name = Path(wall_uri.replace("file://", "")).name if wall_uri else "System Default"
        grp.add(
            self._make_overview_row(
                "Desktop Wallpaper",
                wall_name,
                ItemCategory.WALLPAPERS.icon_name,
                lambda: self.jump_to_section(ItemCategory.WALLPAPERS.value),
            )
        )

        self.body_col.append(grp)

        # Quick folder access group
        folders_grp = Adw.PreferencesGroup()
        folders_grp.set_title("Local Customization Directories")
        for label, path_obj in [
            ("User Themes (~/.local/share/themes)", Path.home() / ".local" / "share" / "themes"),
            ("User Icons & Cursors (~/.local/share/icons)", Path.home() / ".local" / "share" / "icons"),
            ("Saved Wallpapers (~/Pictures/Wallpapers)", Path.home() / "Pictures" / "Wallpapers"),
        ]:
            frow = Adw.ActionRow()
            frow.set_use_markup(False)
            frow.set_title(label)
            frow.set_activatable(True)
            frow.add_suffix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
            frow.connect("activated", lambda _r, p=str(path_obj): self._open_dir(p))
            folders_grp.add(frow)
        self.body_col.append(folders_grp)

    def _make_overview_row(
        self,
        title: str,
        value: str,
        icon_name: str,
        manage_cb: Callable[[], None],
        reset_cb: Optional[Callable[[], None]] = None,
    ) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(GLib.markup_escape_text(value))
        row.set_icon_name(icon_name)
        row.set_activatable(True)
        row.connect("activated", lambda _r: manage_cb())

        if reset_cb:
            rst = Gtk.Button(label="Reset")
            rst.add_css_class("flat")
            rst.set_valign(Gtk.Align.CENTER)
            rst.set_tooltip_text(f"Reset {title} to Adwaita default")
            rst.connect("clicked", lambda _b: reset_cb())
            row.add_suffix(rst)

        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        return row

    def _on_reset_gtk_all(self) -> None:
        self.gsettings.reset_gtk_theme()
        self.toast_cb("Reset GTK3 & GTK4 themes to Adwaita")
        self.refresh_assets(run_async=True)

    def _on_reset_icons(self) -> None:
        self.gsettings.reset_icon_theme()
        self.toast_cb("Reset Icon Theme to Adwaita")
        self.refresh_assets(run_async=True)

    def _on_reset_cursors(self) -> None:
        self.gsettings.reset_cursor_theme()
        self.toast_cb("Reset Cursor Theme to Adwaita")
        self.refresh_assets(run_async=True)

    def _open_dir(self, path_str: str) -> None:
        Path(path_str).mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", path_str])
        except Exception:
            self.toast_cb(path_str)

    # =========================================================================
    # CATEGORY PAGES (WITH FILTERS & PAGINATION)
    # =========================================================================

    def _render_category_page(self, cat: ItemCategory) -> None:
        # 1. Category Header + Filter Controls
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top_row.set_valign(Gtk.Align.CENTER)

        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_col.set_hexpand(True)
        h_lbl = Gtk.Label(label=cat.display_name)
        h_lbl.add_css_class("title-2")
        h_lbl.set_halign(Gtk.Align.START)
        title_col.append(h_lbl)

        cat_descriptions = {
            ItemCategory.EXTENSIONS: "Enable, configure, or remove installed GNOME Shell extensions.",
            ItemCategory.SHELL_THEMES: "Customize the GNOME top bar, quick settings, and overview style.",
            ItemCategory.GTK_THEMES: "Applies to GTK3 apps immediately; themes with GTK4 assets can also be linked to ~/.config/gtk-4.0.",
            ItemCategory.ICONS: "Switch application and folder icon packs across your desktop.",
            ItemCategory.CURSORS: "Change your mouse pointer theme.",
            ItemCategory.WALLPAPERS: "Apply saved wallpapers or import a local image.",
        }
        sub_lbl = Gtk.Label(label=cat_descriptions.get(cat, ""))
        sub_lbl.add_css_class("caption")
        sub_lbl.add_css_class("dim-label")
        sub_lbl.set_halign(Gtk.Align.START)
        sub_lbl.set_wrap(True)
        title_col.append(sub_lbl)
        top_row.append(title_col)

        if cat == ItemCategory.WALLPAPERS:
            import_btn = Gtk.Button()
            import_btn.add_css_class("pill")
            import_btn.add_css_class("compact-btn")
            import_btn.set_valign(Gtk.Align.CENTER)
            import_btn.set_child(
                Adw.ButtonContent(icon_name="list-add-symbolic", label="Import Image…")
            )
            import_btn.connect("clicked", self._on_import_wallpaper)
            top_row.append(import_btn)
        elif cat == ItemCategory.GTK_THEMES and self.gsettings.get_linked_gtk4_theme():
            unlink_btn = Gtk.Button(label="Unlink GTK4 Override")
            unlink_btn.add_css_class("pill")
            unlink_btn.add_css_class("compact-btn")
            unlink_btn.set_valign(Gtk.Align.CENTER)
            unlink_btn.connect("clicked", lambda _b: self._on_unlink_gtk4())
            top_row.append(unlink_btn)

        self.body_col.append(top_row)

        # Extensions Master Switch
        if cat == ItemCategory.EXTENSIONS:
            master_grp = Adw.PreferencesGroup()
            master_row = Adw.SwitchRow()
            master_row.set_title("Enable User Extensions")
            master_row.set_subtitle("Global switch for all GNOME Shell extensions")
            master_row.set_icon_name("application-x-addon-symbolic")
            master_row.set_active(self.gsettings.get_extensions_master_enabled())
            master_row.connect("notify::active", self._on_master_extensions_toggled)
            master_grp.add(master_row)
            self.body_col.append(master_grp)

        # Filter Bar Row (Status ToggleGroup for Extensions + Source ToggleGroup for all)
        filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        filter_bar.set_valign(Gtk.Align.CENTER)

        if cat == ItemCategory.EXTENSIONS:
            st_group = Adw.ToggleGroup()
            st_group.add(Adw.Toggle(name="all", label="All"))
            st_group.add(Adw.Toggle(name="active", label="Enabled"))
            st_group.add(Adw.Toggle(name="disabled", label="Disabled"))
            st_group.set_active_name(self.ext_status_filter)
            st_group.connect("notify::active-name", self._on_ext_status_changed)
            filter_bar.append(st_group)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        filter_bar.append(spacer)

        src_group = Adw.ToggleGroup()
        src_group.add(Adw.Toggle(name="all", label="All Sources"))
        src_group.add(Adw.Toggle(name="user", label="User"))
        src_group.add(Adw.Toggle(name="system", label="System"))
        src_group.set_active_name(self.source_filter)
        src_group.connect("notify::active-name", self._on_source_changed)
        filter_bar.append(src_group)

        self.body_col.append(filter_bar)

        # Filter assets
        filtered = self._get_filtered_assets(cat)
        if not filtered:
            empty = Adw.StatusPage(
                icon_name=cat.icon_name,
                title=f"No {cat.display_name} Found",
                description="Adjust your filters above or browse Explore to install new packages.",
            )
            self.body_col.append(empty)
            return

        # Paginate assets
        total_items = len(filtered)
        total_pages = max(1, math.ceil(total_items / self.page_size))
        if self.current_page > total_pages:
            self.current_page = total_pages
        start_idx = (self.current_page - 1) * self.page_size
        page_slice = filtered[start_idx : start_idx + self.page_size]

        if cat == ItemCategory.WALLPAPERS:
            grid = Gtk.FlowBox()
            grid.set_selection_mode(Gtk.SelectionMode.NONE)
            grid.set_homogeneous(True)
            grid.set_column_spacing(16)
            grid.set_row_spacing(16)
            grid.set_min_children_per_line(self._wallpaper_cols)
            grid.set_max_children_per_line(self._wallpaper_cols)
            for asset in page_slice:
                grid.append(self._build_wallpaper_card(asset))
            self.body_col.append(grid)
        else:
            list_grp = Adw.PreferencesGroup()
            list_grp.set_title(f"{total_items} Installed")
            for asset in page_slice:
                if cat == ItemCategory.EXTENSIONS:
                    list_grp.add(self._build_extension_row(asset))
                else:
                    list_grp.add(self._build_theme_row(asset))
            self.body_col.append(list_grp)

        pbar = PaginationBar(on_page_changed=self._on_page_changed)
        pbar.update_state(
            page=self.current_page,
            total_pages=total_pages,
            total_items=total_items,
        )
        self.body_col.append(pbar)

    def _get_filtered_assets(self, cat: ItemCategory) -> List[LocalAsset]:
        out: List[LocalAsset] = []
        for a in self.all_assets:
            if a.category != cat:
                continue
            if self.source_filter == "user" and a.is_system:
                continue
            if self.source_filter == "system" and not a.is_system:
                continue
            if cat == ItemCategory.EXTENSIONS:
                if self.ext_status_filter == "active" and not a.is_active:
                    continue
                if self.ext_status_filter == "disabled" and a.is_active:
                    continue
            if self.search_query:
                haystack = f"{a.display_name} {a.name} {a.details} {a.description}".lower()
                if self.search_query not in haystack:
                    continue
            out.append(a)

        out.sort(key=lambda x: (not x.is_active, x.is_system, x.display_name.lower()))
        return out

    def _on_ext_status_changed(self, group: Adw.ToggleGroup, _p) -> None:
        val = group.get_active_name() or "all"
        if val != self.ext_status_filter:
            self.ext_status_filter = val
            self.current_page = 1
            self._render_active_section()

    def _on_source_changed(self, group: Adw.ToggleGroup, _p) -> None:
        val = group.get_active_name() or "all"
        if val != self.source_filter:
            self.source_filter = val
            self.current_page = 1
            self._render_active_section()

    def _on_page_changed(self, new_page: int) -> None:
        self.current_page = new_page
        vadj = self.scroller.get_vadjustment()
        if vadj:
            vadj.set_value(0)
        self._render_active_section()

    def _on_master_extensions_toggled(self, row: Adw.SwitchRow, _p) -> None:
        if self._updating_master_sw:
            return
        state = row.get_active()
        self.gsettings.set_extensions_master_enabled(state)
        self.toast_cb("User extensions enabled" if state else "User extensions paused")
        self._update_user_theme_banner()
        self.refresh_assets(run_async=True)

    # =========================================================================
    # ROW & CARD BUILDERS
    # =========================================================================

    def _build_extension_row(self, asset: LocalAsset) -> Gtk.Widget:
        row = Adw.ActionRow()
        row.set_title(GLib.markup_escape_text(asset.display_name))

        sub_parts = []
        if asset.version:
            sub_parts.append(f"v{asset.version}")
        sub_parts.append("System" if asset.is_system else "User")
        if asset.description:
            sub_parts.append(asset.description[:95])
        else:
            sub_parts.append(asset.name)

        row.set_subtitle(GLib.markup_escape_text(" • ".join(sub_parts)))
        row.set_icon_name("application-x-addon-symbolic")

        if asset.error:
            err_btn = Gtk.Button(icon_name="dialog-warning-symbolic")
            err_btn.add_css_class("circular")
            err_btn.add_css_class("flat")
            err_btn.add_css_class("compact-btn")
            err_btn.add_css_class("warning")
            err_btn.set_valign(Gtk.Align.CENTER)
            err_btn.set_tooltip_text(f"Extension Error: {asset.error}")
            row.add_suffix(err_btn)

        if asset.has_prefs:
            prefs_btn = Gtk.Button(icon_name="emblem-system-symbolic")
            prefs_btn.add_css_class("circular")
            prefs_btn.add_css_class("flat")
            prefs_btn.set_valign(Gtk.Align.CENTER)
            prefs_btn.set_tooltip_text(f"Preferences for {asset.display_name}")
            prefs_btn.connect(
                "clicked",
                lambda _b, uuid=asset.name: subprocess.Popen(["gnome-extensions", "prefs", uuid]),
            )
            row.add_suffix(prefs_btn)

        if not asset.is_system:
            rm_btn = Gtk.Button(icon_name="user-trash-symbolic")
            rm_btn.add_css_class("circular")
            rm_btn.add_css_class("flat")
            rm_btn.add_css_class("compact-btn")
            rm_btn.set_valign(Gtk.Align.CENTER)
            rm_btn.set_tooltip_text(f"Uninstall {asset.display_name}")
            rm_btn.connect("clicked", lambda _b, a=asset: self._confirm_remove_asset(a))
            row.add_suffix(rm_btn)

        master_on = self.gsettings.get_extensions_master_enabled()
        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(asset.is_active)
        if not asset.is_system and not master_on:
            sw.set_sensitive(False)
            sw.set_tooltip_text("User extensions are globally paused (master switch off)")
        sw.connect("state-set", lambda _s, state, a=asset: self._on_toggle_extension(a, state))
        row.add_suffix(sw)

        return row

    @staticmethod
    def _find_sample_icon(path_str: str) -> str:
        p = Path(path_str)
        if not p.is_dir():
            return ""
        candidates = [
            "scalable/places/folder.svg",
            "places/scalable/folder.svg",
            "48x48/places/folder.svg",
            "48x48/places/folder.png",
            "scalable/apps/org.gnome.Nautilus.svg",
            "apps/scalable/org.gnome.Nautilus.svg",
            "48x48/apps/org.gnome.Nautilus.png",
        ]
        for c in candidates:
            fp = p / c
            if fp.is_file():
                return str(fp)
        for ext in (".svg", ".png"):
            for root, _, files in os.walk(p):
                for f in files:
                    if f.endswith(ext) and "folder" in f.lower():
                        return str(Path(root) / f)
        return ""

    def _build_theme_row(self, asset: LocalAsset) -> Gtk.Widget:
        row = Adw.ActionRow()
        row.set_title(GLib.markup_escape_text(asset.display_name))

        src_lbl = "System" if asset.is_system else "User"
        sub = f"{asset.details} • {src_lbl}" if asset.details else src_lbl
        row.set_subtitle(GLib.markup_escape_text(sub))

        if asset.category == ItemCategory.ICONS:
            sample_icon = self._find_sample_icon(asset.path)
            if sample_icon:
                img = Gtk.Image.new_from_file(sample_icon)
                img.set_pixel_size(26)
                img.set_valign(Gtk.Align.CENTER)
                row.add_prefix(img)
            else:
                row.set_icon_name("applications-graphics-symbolic")
        elif asset.category == ItemCategory.CURSORS:
            img = Gtk.Image.new_from_icon_name("input-mouse-symbolic")
            img.set_pixel_size(22)
            img.set_valign(Gtk.Align.CENTER)
            row.add_prefix(img)
        else:
            row.set_icon_name(asset.category.icon_name)

        # Optional GTK4 link toggle for themes that ship a gtk-4.0 folder
        if asset.category == ItemCategory.GTK_THEMES and asset.has_gtk4:
            linked_gtk4 = self.gsettings.get_linked_gtk4_theme()
            is_linked = linked_gtk4 == asset.name
            gtk4_btn = Gtk.Button(label="Unlink" if is_linked else "Link GTK4")
            gtk4_btn.add_css_class("flat" if not is_linked else "pill")
            gtk4_btn.add_css_class("compact-btn")
            gtk4_btn.set_valign(Gtk.Align.CENTER)
            gtk4_btn.set_tooltip_text(
                "Unlink from ~/.config/gtk-4.0"
                if is_linked
                else "Also link this theme's gtk-4.0 stylesheet for Libadwaita/GTK4 apps"
            )
            gtk4_btn.connect("clicked", lambda _b, a=asset, lk=is_linked: self._on_toggle_gtk4_link(a, lk))
            row.add_suffix(gtk4_btn)

        # Dual capability indicators
        if asset.category == ItemCategory.ICONS and asset.has_cursors:
            dual_pill = Gtk.Label(label="+Cursors")
            dual_pill.add_css_class("badge-pill")
            dual_pill.set_valign(Gtk.Align.CENTER)
            dual_pill.set_tooltip_text("This icon package also bundles mouse cursor pointers")
            row.add_suffix(dual_pill)
        elif asset.category == ItemCategory.CURSORS and asset.has_icons:
            dual_pill = Gtk.Label(label="+Icons")
            dual_pill.add_css_class("badge-pill")
            dual_pill.set_valign(Gtk.Align.CENTER)
            dual_pill.set_tooltip_text("This cursor theme is bundled inside a full icon package")
            row.add_suffix(dual_pill)

        if asset.is_active:
            badge = Gtk.Label(label="Active")
            badge.add_css_class("badge-pill")
            badge.add_css_class("badge-accent")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)
        else:
            apply_btn = Gtk.Button()
            apply_btn.add_css_class("pill")
            apply_btn.add_css_class("compact-btn")
            apply_btn.add_css_class("suggested-action")
            apply_btn.set_valign(Gtk.Align.CENTER)
            apply_btn.set_tooltip_text(f"Apply {asset.display_name}")
            apply_btn.set_child(
                Adw.ButtonContent(icon_name="object-select-symbolic", label="Apply")
            )
            apply_btn.connect("clicked", lambda _b, a=asset: self._on_apply_asset(a))
            row.add_suffix(apply_btn)

        if not asset.is_system:
            rm_btn = Gtk.Button(icon_name="user-trash-symbolic")
            rm_btn.add_css_class("circular")
            rm_btn.add_css_class("flat")
            rm_btn.add_css_class("compact-btn")
            rm_btn.set_valign(Gtk.Align.CENTER)
            rm_btn.set_tooltip_text(f"Uninstall {asset.display_name}")
            rm_btn.connect("clicked", lambda _b, a=asset: self._confirm_remove_asset(a))
            row.add_suffix(rm_btn)

        return row

    def _build_wallpaper_card(self, asset: LocalAsset) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("wallpaper-card")
        if asset.is_active:
            card.add_css_class("wallpaper-active")
        card.set_overflow(Gtk.Overflow.HIDDEN)

        art_overlay = Gtk.Overlay()
        art_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        art_box.set_size_request(-1, 230)
        art_box.set_overflow(Gtk.Overflow.HIDDEN)

        fallback_art = Gtk.Image.new_from_icon_name("preferences-desktop-wallpaper-symbolic")
        fallback_art.set_pixel_size(48)
        fallback_art.set_valign(Gtk.Align.CENTER)
        fallback_art.set_halign(Gtk.Align.CENTER)
        fallback_art.set_vexpand(True)
        fallback_art.set_hexpand(True)
        art_box.append(fallback_art)

        pic = Gtk.Picture()
        pic.set_content_fit(Gtk.ContentFit.COVER)
        pic.set_can_shrink(True)
        pic.set_size_request(-1, 230)
        pic.set_hexpand(True)
        pic.set_visible(False)
        art_box.append(pic)
        art_overlay.set_child(art_box)

        if asset.path and os.path.exists(asset.path):
            def _apply_wtex(tex: Optional[Gdk.Texture], p=pic, fb=fallback_art):
                if tex:
                    p.set_paintable(tex)
                    fb.set_visible(False)
                    p.set_visible(True)

            load_cropped_texture_async(asset.path, 720, 420, _apply_wtex)

        if asset.is_active:
            act_pill = Gtk.Label(label="Active")
            act_pill.add_css_class("badge-pill")
            act_pill.add_css_class("badge-accent")
            act_pill.set_halign(Gtk.Align.END)
            act_pill.set_valign(Gtk.Align.START)
            act_pill.set_margin_top(10)
            act_pill.set_margin_end(10)
            art_overlay.add_overlay(act_pill)

        card.append(art_overlay)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.set_margin_top(10)
        footer.set_margin_bottom(10)
        footer.set_margin_start(14)
        footer.set_margin_end(14)
        footer.set_valign(Gtk.Align.CENTER)

        t_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t_col.set_hexpand(True)
        t_col.set_valign(Gtk.Align.CENTER)

        n_lbl = Gtk.Label(label=asset.display_name)
        n_lbl.add_css_class("heading")
        n_lbl.set_halign(Gtk.Align.START)
        n_lbl.set_ellipsize(3)
        n_lbl.set_lines(1)
        t_col.append(n_lbl)

        s_lbl = Gtk.Label(label="System" if asset.is_system else "User")
        s_lbl.add_css_class("caption")
        s_lbl.add_css_class("dim-label")
        s_lbl.set_halign(Gtk.Align.START)
        t_col.append(s_lbl)
        footer.append(t_col)

        if not asset.is_active:
            set_btn = Gtk.Button()
            set_btn.add_css_class("pill")
            set_btn.add_css_class("compact-btn")
            set_btn.add_css_class("suggested-action")
            set_btn.set_valign(Gtk.Align.CENTER)
            set_btn.set_tooltip_text(f"Apply {asset.display_name}")
            set_btn.set_child(
                Adw.ButtonContent(icon_name="object-select-symbolic", label="Apply")
            )
            set_btn.connect("clicked", lambda _b, a=asset: self._on_apply_asset(a))
            footer.append(set_btn)

        if not asset.is_system:
            rm_btn = Gtk.Button(icon_name="user-trash-symbolic")
            rm_btn.add_css_class("circular")
            rm_btn.add_css_class("flat")
            rm_btn.add_css_class("compact-btn")
            rm_btn.set_valign(Gtk.Align.CENTER)
            rm_btn.set_tooltip_text(f"Delete {asset.display_name}")
            rm_btn.connect("clicked", lambda _b, a=asset: self._confirm_remove_asset(a))
            footer.append(rm_btn)

        card.append(footer)
        return card

    # =========================================================================
    # ACTIONS
    # =========================================================================

    def _on_apply_asset(self, asset: LocalAsset) -> None:
        if asset.category == ItemCategory.SHELL_THEMES and not self.gsettings.is_user_theme_extension_installed():
            self._update_user_theme_banner()
            self.toast_cb("Install the User Themes extension from the banner above first.")
            return

        ok = self.gsettings.apply_local_asset(asset)
        if ok:
            self.toast_cb(f"Applied {asset.display_name}")
            self.refresh_assets(run_async=True)
        else:
            self.toast_cb(f"Could not apply {asset.display_name}")

    def _on_toggle_gtk4_link(self, asset: LocalAsset, currently_linked: bool) -> None:
        if currently_linked:
            self.gsettings.reset_gtk4_theme()
            self.toast_cb("Unlinked GTK4 override (reopen GTK4 apps to take effect)")
        else:
            ok = self.gsettings.apply_gtk4_theme(asset.path, asset.name)
            if ok:
                self.gsettings.set_gtk_theme(asset.name)
                self.toast_cb(f"Linked {asset.display_name} to GTK4 & GTK3 (reopen apps to apply)")
            else:
                self.toast_cb(f"Failed to link GTK4 files for {asset.display_name}")
        self.refresh_assets(run_async=True)

    def _on_unlink_gtk4(self) -> None:
        self.gsettings.reset_gtk4_theme()
        self.toast_cb("Removed GTK4 stylesheet override")
        self.refresh_assets(run_async=True)

    def _on_toggle_extension(self, asset: LocalAsset, state: bool) -> bool:
        ok = self.gsettings.toggle_extension(asset.name, state)
        if ok:
            asset.is_active = state
            self.toast_cb(f"{'Enabled' if state else 'Disabled'} {asset.display_name}")
            self._update_sidebar_counts()
            if self.ext_status_filter != "all":
                GLib.idle_add(self._render_active_section)
        return not ok

    def _confirm_remove_asset(self, asset: LocalAsset) -> None:
        dlg = Adw.AlertDialog.new(
            f"Remove {asset.display_name}?",
            f"This will permanently delete “{asset.display_name}” from your local customization folder.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def _on_choice(d, res):
            if d.choose_finish(res) == "remove":
                self._do_remove_asset(asset)

        dlg.choose(self.get_root(), None, _on_choice)

    def _do_remove_asset(self, asset: LocalAsset) -> None:
        def _worker():
            ok, msg, _freed = self.installer.uninstall_asset(
                asset, active_check_cb=self.gsettings.reset_if_active
            )

            def _done():
                self.toast_cb(msg)
                if ok:
                    self.refresh_assets(run_async=True)
                return False

            GLib.idle_add(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_import_wallpaper(self, _btn: Gtk.Button) -> None:
        import shutil
        file_dialog = Gtk.FileDialog(title="Import Wallpaper Image")
        img_filter = Gtk.FileFilter()
        img_filter.set_name("Images")
        for mime in ("image/jpeg", "image/png", "image/webp"):
            img_filter.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(img_filter)
        file_dialog.set_filters(filters)

        def _on_finish(d, res):
            try:
                gfile = d.open_finish(res)
                if gfile and gfile.get_path():
                    src = Path(gfile.get_path())
                    dest_dir = Path.home() / "Pictures" / "Wallpapers"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / src.name
                    if src.resolve() != dest.resolve():
                        shutil.copy2(src, dest)
                    self.gsettings.set_wallpaper_uri(str(dest))
                    self.toast_cb(f"Imported & applied {dest.name}")
                    self.refresh_assets(run_async=True)
            except Exception:
                pass

        file_dialog.open(self.get_root(), None, _on_finish)
