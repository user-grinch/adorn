"""Explore Storefront view for Adorn with category browsing, search, sorting, and pagination."""

from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Callable, List, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from adorn.core.ego_client import EgoClient
from adorn.core.gsettings import GSettingsManager
from adorn.core.installer import InstallationEngine
from adorn.core.models import (
    CustomizationItem,
    DownloadableFile,
    ItemCategory,
    PaginatedResult,
    StoreSort,
)
from adorn.core.ocs_client import OcsClient
from adorn.core.task_manager import TaskManager
from adorn.ui.components.banner import FeaturedBanner
from adorn.ui.components.category_bar import CategoryBar, CategoryFilterStrip
from adorn.ui.components.item_tile import ItemTile, WallpaperTile
from adorn.ui.components.pagination_bar import PaginationBar


class ExploreView(Gtk.Box):
    """
    Modern GNOME Customization Storefront:
    - Home Overview: Featured Hero Carousel, 6 Category Cards, Popular Extensions shelf, Top Themes shelf
    - Catalog & Search Mode: Filter pill strip, Sort dropdown, responsive grid, full PaginationBar
    - Live debounced search across both extensions.gnome.org and gnome-look.org
    """

    SORT_OPTIONS: List[StoreSort] = [
        StoreSort.POPULAR,
        StoreSort.DOWNLOADS,
        StoreSort.NEWEST,
        StoreSort.NAME,
    ]

    def __init__(
        self,
        ocs_client: OcsClient,
        ego_client: EgoClient,
        installer: InstallationEngine,
        gsettings: GSettingsManager,
        on_open_detail: Callable[[CustomizationItem], None],
        toast_cb: Callable[[str], None],
        download_toast_cb: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.ocs = ocs_client
        self.ego = ego_client
        self.installer = installer
        self.gsettings = gsettings
        self.on_open_detail = on_open_detail
        self.toast_cb = toast_cb
        self.download_toast_cb = download_toast_cb or toast_cb

        self.current_category: Optional[ItemCategory] = None
        self.current_query: str = ""
        self.current_sort: StoreSort = StoreSort.POPULAR
        self.current_page: int = 1
        self.page_size: int = 24

        self._fetch_token: int = 0
        self._search_debounce_id: Optional[int] = None
        self._displayed_tiles: List[ItemTile] = []
        self._current_cols: int = -1
        self._updating_sort_ui: bool = False
        self._pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="adorn-store")

        # Collapsible Search Bar
        self.search_bar = Gtk.SearchBar()
        self.search_bar.set_show_close_button(True)

        search_clamp = Adw.Clamp(maximum_size=680, tightening_threshold=500)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(
            "Search extensions, GTK themes, shell themes, icons, cursors & wallpapers…"
        )
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self._on_search_activated)
        self.search_entry.connect("search-changed", self._on_search_changed)
        search_clamp.set_child(self.search_entry)

        self.search_bar.set_child(search_clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.connect("notify::search-mode-enabled", self._on_search_mode_toggled)
        self.append(self.search_bar)

        # Main Stack: loading vs content
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        self.append(self.stack)

        # 1. Loading State
        loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        loading_box.set_valign(Gtk.Align.CENTER)
        loading_box.set_halign(Gtk.Align.CENTER)
        loading_box.set_vexpand(True)
        spinner = Adw.Spinner()
        spinner.set_size_request(40, 40)
        loading_box.append(spinner)
        self.loading_lbl = Gtk.Label(label="Loading Storefront…")
        self.loading_lbl.add_css_class("dim-label")
        loading_box.append(self.loading_lbl)
        self.stack.add_named(loading_box, "loading")

        # 2. Scrollable Storefront Content
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_vexpand(True)
        self.scroller.set_hexpand(True)

        self.clamp = Adw.Clamp(maximum_size=1040, tightening_threshold=640)
        self.scroller.set_child(self.clamp)

        root_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        root_col.set_hexpand(True)
        root_col.set_margin_top(18)
        root_col.set_margin_bottom(32)
        root_col.set_margin_start(20)
        root_col.set_margin_end(20)
        self.clamp.set_child(root_col)

        # --- A. Home Overview Container ---
        self.overview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=26)
        root_col.append(self.overview_box)

        self.banner = FeaturedBanner(self.ocs, self.on_open_detail)
        self.overview_box.append(self.banner)

        self.category_bar = CategoryBar(self._select_category_from_home)
        self.overview_box.append(self.category_bar)

        # Shelf 1: Popular GNOME Extensions
        ext_shelf = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        ext_hdr = self._build_shelf_header(
            "Popular GNOME Extensions",
            "Browse All",
            lambda: self._select_category_from_home(ItemCategory.EXTENSIONS),
        )
        ext_shelf.append(ext_hdr)
        self.home_ext_grid = self._create_item_flowbox()
        ext_shelf.append(self.home_ext_grid)
        self.overview_box.append(ext_shelf)

        # Shelf 2: Top Rated Themes & Icon Packs
        theme_shelf = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        theme_hdr = self._build_shelf_header(
            "Top Themes & Icon Packs",
            "Browse GTK Themes",
            lambda: self._select_category_from_home(ItemCategory.GTK_THEMES),
        )
        theme_shelf.append(theme_hdr)
        self.home_theme_grid = self._create_item_flowbox()
        theme_shelf.append(self.home_theme_grid)
        self.overview_box.append(theme_shelf)

        # --- B. Paginated Catalog / Search Results Container ---
        self.catalog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.catalog_box.set_visible(False)
        root_col.append(self.catalog_box)

        # Filter chip strip
        self.filter_strip = CategoryFilterStrip(self._on_filter_chip_selected)
        self.catalog_box.append(self.filter_strip)

        # Toolbar row: Back to Overview + Title/Count + Sort Dropdown
        cat_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cat_toolbar.set_valign(Gtk.Align.CENTER)

        self.back_overview_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_overview_btn.add_css_class("circular")
        self.back_overview_btn.add_css_class("flat")
        self.back_overview_btn.set_tooltip_text("Back to Storefront Overview")
        self.back_overview_btn.connect("clicked", lambda _b: self.return_to_overview())
        cat_toolbar.append(self.back_overview_btn)

        title_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_wrap.set_hexpand(True)
        title_wrap.set_valign(Gtk.Align.CENTER)

        self.catalog_title = Gtk.Label(label="Catalog")
        self.catalog_title.add_css_class("title-3")
        self.catalog_title.set_halign(Gtk.Align.START)
        self.catalog_title.set_ellipsize(3)
        title_wrap.append(self.catalog_title)

        self.catalog_subtitle = Gtk.Label(label="")
        self.catalog_subtitle.add_css_class("caption")
        self.catalog_subtitle.add_css_class("dim-label")
        self.catalog_subtitle.set_halign(Gtk.Align.START)
        self.catalog_subtitle.set_ellipsize(3)
        title_wrap.append(self.catalog_subtitle)
        cat_toolbar.append(title_wrap)

        sort_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sort_box.set_valign(Gtk.Align.CENTER)
        sort_ic = Gtk.Image.new_from_icon_name("view-sort-descending-symbolic")
        sort_ic.add_css_class("dim-label")
        sort_box.append(sort_ic)

        self.sort_dropdown = Gtk.DropDown.new_from_strings(
            [s.display_name for s in self.SORT_OPTIONS]
        )
        self.sort_dropdown.set_tooltip_text("Sort results")
        self.sort_dropdown.connect("notify::selected", self._on_sort_changed)
        sort_box.append(self.sort_dropdown)
        cat_toolbar.append(sort_box)

        self.catalog_box.append(cat_toolbar)

        # Inline loading bar for smooth page transitions inside catalog
        self.catalog_loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.catalog_loading_box.set_valign(Gtk.Align.CENTER)
        self.catalog_loading_box.set_halign(Gtk.Align.CENTER)
        self.catalog_loading_box.set_margin_top(48)
        self.catalog_loading_box.set_margin_bottom(48)
        cat_spin = Adw.Spinner()
        cat_spin.set_size_request(34, 34)
        self.catalog_loading_box.append(cat_spin)
        self.catalog_loading_box.set_visible(False)
        self.catalog_box.append(self.catalog_loading_box)

        # Results Grid
        self.results_grid = self._create_item_flowbox()
        self.catalog_box.append(self.results_grid)

        # Empty / Error Status Page
        self.empty_status = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title="No Matching Customizations",
            description="Try adjusting your search keywords or selecting another category.",
        )
        self.empty_status.set_visible(False)
        self.catalog_box.append(self.empty_status)

        # Pagination Footer Bar
        self.pagination_bar = PaginationBar(
            on_page_changed=self._on_page_changed,
            on_page_size_changed=self._on_page_size_changed,
            default_page_size=self.page_size,
        )
        self.catalog_box.append(self.pagination_bar)

        self.stack.add_named(self.scroller, "content")

        TaskManager.get_default().subscribe(self._on_tasks_changed)
        self.adapt_columns(1180)
        self._load_overview()

    def _build_shelf_header(
        self, title: str, action_label: str, callback: Callable[[], None]
    ) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_valign(Gtk.Align.CENTER)

        lbl = Gtk.Label(label=title)
        lbl.add_css_class("title-3")
        lbl.set_halign(Gtk.Align.START)
        lbl.set_hexpand(True)
        lbl.set_ellipsize(3)
        row.append(lbl)

        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.set_child(Adw.ButtonContent(icon_name="go-next-symbolic", label=action_label))
        btn.connect("clicked", lambda _b: callback())
        row.append(btn)
        return row

    def _create_item_flowbox(self) -> Gtk.FlowBox:
        fb = Gtk.FlowBox()
        fb.set_valign(Gtk.Align.START)
        fb.set_hexpand(True)
        fb.set_column_spacing(12)
        fb.set_row_spacing(12)
        fb.set_homogeneous(True)
        fb.set_selection_mode(Gtk.SelectionMode.NONE)
        cols = 2 if self._current_cols != 1 else 1
        fb.set_min_children_per_line(cols)
        fb.set_max_children_per_line(cols)
        return fb

    def adapt_columns(self, width: int) -> None:
        content_w = min(width, self.clamp.get_maximum_size())
        self.category_bar.adapt_columns(content_w)
        self.banner.adapt_width(content_w)
        self.pagination_bar.adapt_width(content_w)

        cols = 2 if content_w >= 560 else 1
        if cols != self._current_cols:
            self._current_cols = cols
            for grid in (self.home_ext_grid, self.home_theme_grid, self.results_grid):
                grid.set_min_children_per_line(cols)
                grid.set_max_children_per_line(cols)

    def focus_search(self) -> None:
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()

    def refresh_catalog(self) -> None:
        if self._is_in_overview_mode():
            self._load_overview()
        else:
            self._load_catalog_page()

    def return_to_overview(self) -> None:
        self.current_category = None
        if self.search_entry.get_text():
            self.search_entry.set_text("")
        self.current_query = ""
        self.current_page = 1
        self.filter_strip.set_active_category(None)
        self._load_overview()

    def _is_in_overview_mode(self) -> bool:
        return self.current_category is None and not self.current_query

    def _select_category_from_home(self, category: Optional[ItemCategory]) -> None:
        self.current_category = category
        self.current_page = 1
        self.filter_strip.set_active_category(category)
        if self._is_in_overview_mode():
            self._load_overview()
        else:
            self._load_catalog_page()

    def _on_filter_chip_selected(self, category: Optional[ItemCategory]) -> None:
        self.current_category = category
        self.current_page = 1
        if self._is_in_overview_mode():
            self._load_overview()
        else:
            self._load_catalog_page()

    def _on_sort_changed(self, dropdown: Gtk.DropDown, _p) -> None:
        if self._updating_sort_ui:
            return
        idx = dropdown.get_selected()
        if 0 <= idx < len(self.SORT_OPTIONS):
            chosen = self.SORT_OPTIONS[idx]
            if chosen != self.current_sort:
                self.current_sort = chosen
                self.current_page = 1
                self._load_catalog_page()

    def _on_page_changed(self, new_page: int) -> None:
        self.current_page = new_page
        vadj = self.scroller.get_vadjustment()
        if vadj:
            vadj.set_value(0)
        self._load_catalog_page()

    def _on_page_size_changed(self, new_size: int) -> None:
        self.page_size = new_size
        self.current_page = 1
        vadj = self.scroller.get_vadjustment()
        if vadj:
            vadj.set_value(0)
        self._load_catalog_page()

    def _cancel_search_debounce(self) -> None:
        if self._search_debounce_id is not None:
            GLib.source_remove(self._search_debounce_id)
            self._search_debounce_id = None

    def _on_search_mode_toggled(self, search_bar: Gtk.SearchBar, _p) -> None:
        if not search_bar.get_search_mode():
            self._cancel_search_debounce()
            if self.search_entry.get_text() or self.current_query:
                self.search_entry.set_text("")
                self.current_query = ""
                self.current_page = 1
                if self._is_in_overview_mode():
                    self._load_overview()
                else:
                    self._load_catalog_page()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._cancel_search_debounce()
        text = entry.get_text().strip()
        if text == self.current_query:
            return

        if text == "":
            self.current_query = ""
            self.current_page = 1
            if self._is_in_overview_mode():
                self._load_overview()
            else:
                self._load_catalog_page()
            return

        def _fire():
            self._search_debounce_id = None
            new_q = self.search_entry.get_text().strip()
            if new_q != self.current_query:
                self.current_query = new_q
                self.current_page = 1
                self._load_catalog_page()
            return False

        self._search_debounce_id = GLib.timeout_add(380, _fire)

    def _on_search_activated(self, entry: Gtk.SearchEntry) -> None:
        self._cancel_search_debounce()
        new_q = entry.get_text().strip()
        self.current_query = new_q
        self.current_page = 1
        if self._is_in_overview_mode():
            self._load_overview()
        else:
            self._load_catalog_page()

    def _on_tasks_changed(self) -> None:
        def worker():
            cached_assets = self.gsettings.list_installed_assets()

            def apply():
                for tile in list(self._displayed_tiles):
                    self.gsettings.sync_item_state(tile.item, cached_assets)
                    tile.refresh_state()
                return False

            GLib.idle_add(apply)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _clear_flowbox(fb: Gtk.FlowBox) -> None:
        child = fb.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            fb.remove(child)
            child = nxt

    def _load_overview(self) -> None:
        self._fetch_token += 1
        token = self._fetch_token

        self.overview_box.set_visible(True)
        self.catalog_box.set_visible(False)
        self.stack.set_visible_child_name("loading")

        def worker():
            f_ext = self._pool.submit(
                self.ego.fetch_paginated, "", 1, 8, StoreSort.POPULAR
            )
            f_gtk = self._pool.submit(
                self.ocs.fetch_paginated, ItemCategory.GTK_THEMES, "", 1, 10, StoreSort.POPULAR
            )
            f_ico = self._pool.submit(
                self.ocs.fetch_paginated, ItemCategory.ICONS, "", 1, 10, StoreSort.POPULAR
            )
            f_sh = self._pool.submit(
                self.ocs.fetch_paginated, ItemCategory.SHELL_THEMES, "", 1, 10, StoreSort.POPULAR
            )

            exts = f_ext.result().items
            gtks = f_gtk.result().items
            icos = f_ico.result().items
            shs = f_sh.result().items

            # Interleave top GTK, Icon, and Shell themes for the Themes shelf
            mixed_themes: List[CustomizationItem] = []
            for idx in range(max(len(gtks), len(icos), len(shs))):
                if idx < len(gtks):
                    mixed_themes.append(gtks[idx])
                if idx < len(icos):
                    mixed_themes.append(icos[idx])
                if idx < len(shs):
                    mixed_themes.append(shs[idx])

            banner_pool = exts[:4] + gtks[:4] + icos[:4] + shs[:3]
            GLib.idle_add(
                self._on_overview_loaded,
                token,
                banner_pool,
                exts[:6],
                mixed_themes[:6],
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_overview_loaded(
        self,
        token: int,
        banner_pool: List[CustomizationItem],
        ext_items: List[CustomizationItem],
        theme_items: List[CustomizationItem],
    ) -> bool:
        if token != self._fetch_token:
            return False

        self._displayed_tiles.clear()
        self._clear_flowbox(self.home_ext_grid)
        self._clear_flowbox(self.home_theme_grid)

        if banner_pool:
            self.banner.set_items(banner_pool)

        cached_assets = self.gsettings.list_installed_assets()

        for it in ext_items:
            self.gsettings.sync_item_state(it, cached_assets)
            tile = self._make_tile(it, show_category=False)
            self._displayed_tiles.append(tile)
            self.home_ext_grid.append(tile)

        for it in theme_items:
            self.gsettings.sync_item_state(it, cached_assets)
            tile = self._make_tile(it, show_category=True)
            self._displayed_tiles.append(tile)
            self.home_theme_grid.append(tile)

        self.stack.set_visible_child_name("content")
        return False

    def _load_catalog_page(self) -> None:
        self._fetch_token += 1
        token = self._fetch_token

        self.overview_box.set_visible(False)
        self.catalog_box.set_visible(True)
        self.stack.set_visible_child_name("content")

        self.filter_strip.set_active_category(self.current_category)
        self.back_overview_btn.set_visible(not bool(self.current_query))

        if self.current_query:
            cat_suffix = f" in {self.current_category.display_name}" if self.current_category else ""
            self.catalog_title.set_label(f"Results for “{self.current_query}”{cat_suffix}")
        elif self.current_category:
            self.catalog_title.set_label(self.current_category.display_name)
        else:
            self.catalog_title.set_label("All Customizations")

        self.catalog_subtitle.set_label("Loading…")
        self.catalog_loading_box.set_visible(True)
        self.results_grid.set_visible(False)
        self.empty_status.set_visible(False)
        self.pagination_bar.set_visible(False)

        cat = self.current_category
        query = self.current_query
        page = self.current_page
        page_size = self.page_size
        sort = self.current_sort

        def worker():
            try:
                if cat == ItemCategory.EXTENSIONS:
                    res = self.ego.fetch_paginated(
                        query=query, page=page, page_size=page_size, sort=sort
                    )
                elif cat is None:
                    # Search/browse across BOTH Extensions (EGO) and Themes/Icons/Wallpapers (OCS)
                    half = max(10, page_size // 2)
                    f_ego = self._pool.submit(
                        self.ego.fetch_paginated, query, page, half, sort
                    )
                    f_ocs = self._pool.submit(
                        self.ocs.fetch_paginated, None, query, page, half, sort
                    )
                    r_ego = f_ego.result()
                    r_ocs = f_ocs.result()

                    combined: List[CustomizationItem] = []
                    for idx in range(max(len(r_ego.items), len(r_ocs.items))):
                        if idx < len(r_ego.items):
                            combined.append(r_ego.items[idx])
                        if idx < len(r_ocs.items):
                            combined.append(r_ocs.items[idx])

                    res = PaginatedResult(
                        items=combined[:page_size],
                        page=page,
                        page_size=page_size,
                        total_items=r_ego.total_items + r_ocs.total_items,
                        total_pages=max(r_ego.total_pages, r_ocs.total_pages),
                        error=r_ego.error if (r_ego.error and r_ocs.error) else None,
                    )
                else:
                    res = self.ocs.fetch_paginated(
                        category=cat,
                        search=query,
                        page=page,
                        page_size=page_size,
                        sort=sort,
                    )
            except Exception as e:
                res = PaginatedResult(items=[], page=page, page_size=page_size, error=str(e))

            GLib.idle_add(self._on_catalog_page_loaded, token, res, cat is None)

        threading.Thread(target=worker, daemon=True).start()

    def _on_catalog_page_loaded(
        self, token: int, res: PaginatedResult, show_cat_badge: bool
    ) -> bool:
        if token != self._fetch_token:
            return False

        self.catalog_loading_box.set_visible(False)
        self._displayed_tiles.clear()
        self._clear_flowbox(self.results_grid)

        if not res.items:
            if res.error:
                self.empty_status.set_icon_name("network-offline-symbolic")
                self.empty_status.set_title("Unable to Load Store Catalog")
                self.empty_status.set_description(
                    "Check your internet connection and click Refresh to try again."
                )
            else:
                self.empty_status.set_icon_name("system-search-symbolic")
                self.empty_status.set_title("No Matching Customizations")
                self.empty_status.set_description(
                    "Try clearing your search or choosing a different category."
                )
            self.catalog_subtitle.set_label("0 items")
            self.empty_status.set_visible(True)
            self.results_grid.set_visible(False)
            self.pagination_bar.set_visible(False)
            return False

        cached_assets = self.gsettings.list_installed_assets()
        for item in res.items:
            self.gsettings.sync_item_state(item, cached_assets)
            tile = self._make_tile(item, show_category=show_cat_badge)
            self._displayed_tiles.append(tile)
            self.results_grid.append(tile)

        if res.total_items > 0 and self.current_category != ItemCategory.EXTENSIONS:
            self.catalog_subtitle.set_label(
                f"{res.total_items:,} packages • Page {res.page:,} of {res.total_pages:,}"
            )
        else:
            self.catalog_subtitle.set_label(
                f"Page {res.page:,} of {res.total_pages:,} • Showing {len(res.items)} items"
            )

        self.results_grid.set_visible(True)
        self.empty_status.set_visible(False)
        self.pagination_bar.update_state(
            page=res.page,
            total_pages=res.total_pages,
            total_items=res.total_items if self.current_category != ItemCategory.EXTENSIONS else 0,
            always_show_if_items=True,
        )
        return False

    def _make_tile(self, item: CustomizationItem, show_category: bool) -> Gtk.Widget:
        if item.category == ItemCategory.WALLPAPERS and self.current_category == ItemCategory.WALLPAPERS:
            return WallpaperTile(
                item=item,
                ocs_client=self.ocs,
                on_clicked=self.on_open_detail,
                on_quick_install=self._on_quick_install,
                on_quick_apply=self._on_quick_apply,
            )
        return ItemTile(
            item=item,
            ocs_client=self.ocs,
            on_clicked=self.on_open_detail,
            on_quick_install=self._on_quick_install,
            on_quick_apply=self._on_quick_apply,
            show_category=show_category,
        )

    def _on_quick_install(self, item: CustomizationItem) -> None:
        if not item.download_files:
            self.on_open_detail(item)
            return
        self.download_toast_cb(f"Downloading {item.name}…")
        self.installer.start_install_task(
            item=item,
            file_info=item.download_files[0],
            done_cb=lambda ok, msg: (
                self.toast_cb(f"Installed {item.name}")
                if ok
                else (self.download_toast_cb(f"Install failed: {msg}") if msg != "Cancelled" else None)
            ),
        )

    def _on_quick_apply(self, item: CustomizationItem) -> None:
        ok, applied_name = self.gsettings.apply_customization_item(item)
        if ok:
            verb = "Enabled" if item.category == ItemCategory.EXTENSIONS else "Applied"
            self.toast_cb(f"{verb} {applied_name}")
            self._on_tasks_changed()
        else:
            if item.category == ItemCategory.SHELL_THEMES and not self.gsettings.is_user_theme_extension_installed():
                self.toast_cb("Install the User Themes extension in Installed → Shell Themes first.")
            else:
                self.toast_cb(f"Could not apply {item.name}")
