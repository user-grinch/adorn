"""Storefront category tiles and filter chip bar components."""

from typing import Callable, Dict, List, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

from adorn.core.models import ItemCategory


ALL_STORE_CATEGORIES: List[ItemCategory] = [
    ItemCategory.EXTENSIONS,
    ItemCategory.GTK_THEMES,
    ItemCategory.SHELL_THEMES,
    ItemCategory.ICONS,
    ItemCategory.CURSORS,
    ItemCategory.WALLPAPERS,
]


class CategoryBar(Gtk.Box):
    """
    Modern Libadwaita Category Cards Grid (3 columns on desktop, 2 on tablet, 1 on phone)
    covering all 6 GNOME customization categories including Extensions.
    """

    def __init__(self, on_category_selected: Callable[[Optional[ItemCategory]], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.on_category_selected = on_category_selected
        self._current_cols: int = -1

        hdr = Gtk.Label(label="Browse Categories")
        hdr.add_css_class("title-3")
        hdr.set_halign(Gtk.Align.START)
        self.append(hdr)

        self.grid = Gtk.FlowBox()
        self.grid.set_valign(Gtk.Align.START)
        self.grid.set_hexpand(True)
        self.grid.set_column_spacing(12)
        self.grid.set_row_spacing(12)
        self.grid.set_homogeneous(True)
        self.grid.set_selection_mode(Gtk.SelectionMode.NONE)

        for cat in ALL_STORE_CATEGORIES:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("category-tile")
            btn.set_hexpand(True)

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.set_valign(Gtk.Align.CENTER)
            row.set_hexpand(True)

            icon_badge = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            icon_badge.add_css_class("category-icon-badge")
            icon_badge.set_size_request(40, 40)
            icon_badge.set_valign(Gtk.Align.CENTER)
            icon_badge.set_halign(Gtk.Align.START)
            icon_badge.set_hexpand(False)
            icon_badge.set_vexpand(False)

            icon = Gtk.Image.new_from_icon_name(cat.icon_name)
            icon.set_pixel_size(20)
            icon.set_valign(Gtk.Align.CENTER)
            icon.set_halign(Gtk.Align.CENTER)
            icon.set_vexpand(True)
            icon.set_hexpand(True)
            icon_badge.append(icon)
            row.append(icon_badge)

            text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            text_col.set_valign(Gtk.Align.CENTER)
            text_col.set_hexpand(True)

            title_lbl = Gtk.Label(label=cat.display_name)
            title_lbl.add_css_class("heading")
            title_lbl.set_halign(Gtk.Align.START)
            title_lbl.set_ellipsize(3)
            text_col.append(title_lbl)

            sub_lbl = Gtk.Label(label=cat.subtitle)
            sub_lbl.add_css_class("caption")
            sub_lbl.add_css_class("dim-label")
            sub_lbl.set_halign(Gtk.Align.START)
            sub_lbl.set_ellipsize(3)
            text_col.append(sub_lbl)
            row.append(text_col)

            chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
            chevron.add_css_class("dim-label")
            chevron.set_valign(Gtk.Align.CENTER)
            row.append(chevron)

            btn.set_child(row)
            btn.connect("clicked", lambda _b, c=cat: self.on_category_selected(c))
            self.grid.append(btn)

        self.append(self.grid)
        self.adapt_columns(960)

    def adapt_columns(self, width: int) -> None:
        if width >= 720:
            cols = 3
        elif width >= 440:
            cols = 2
        else:
            cols = 1

        if cols != self._current_cols:
            self._current_cols = cols
            self.grid.set_min_children_per_line(cols)
            self.grid.set_max_children_per_line(cols)


class CategoryFilterStrip(Gtk.ScrolledWindow):
    """
    Horizontally scrollable category pill strip shown during Category Browsing & Search
    so switching categories or returning to Overview takes a single click.
    """

    def __init__(self, on_category_selected: Callable[[Optional[ItemCategory]], None]):
        super().__init__()
        self.on_category_selected = on_category_selected
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.set_propagate_natural_height(True)
        self.set_hexpand(True)

        self.buttons: Dict[Optional[ItemCategory], Gtk.Button] = {}
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_margin_bottom(2)

        all_items: List[tuple[Optional[ItemCategory], str, str]] = [
            (None, "All Categories", "view-grid-symbolic"),
        ] + [(c, c.short_name, c.icon_name) for c in ALL_STORE_CATEGORIES]

        for cat_key, label, icon_name in all_items:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("filter-chip")
            if cat_key is None:
                btn.add_css_class("active-chip")

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_valign(Gtk.Align.CENTER)
            ic = Gtk.Image.new_from_icon_name(icon_name)
            ic.set_pixel_size(14)
            box.append(ic)
            lbl = Gtk.Label(label=label)
            box.append(lbl)
            btn.set_child(box)

            btn.connect("clicked", lambda _b, ck=cat_key: self._select(ck, True))
            row.append(btn)
            self.buttons[cat_key] = btn

        self.set_child(row)

    def _select(self, cat_key: Optional[ItemCategory], notify: bool) -> None:
        self.set_active_category(cat_key)
        if notify:
            self.on_category_selected(cat_key)

    def set_active_category(self, cat_key: Optional[ItemCategory]) -> None:
        for k, btn in self.buttons.items():
            if k == cat_key:
                btn.add_css_class("active-chip")
            else:
                btn.remove_css_class("active-chip")
