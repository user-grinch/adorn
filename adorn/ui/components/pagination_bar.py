"""Reusable modern GNOME pagination bar component."""

from typing import Callable, List, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk


class PaginationBar(Gtk.Box):
    """
    Modern GNOME pagination footer with:
    - Left: Page X of Y (& total items summary)
    - Center: Previous, numbered page pills with smart ellipsis, Next
    - Right: Quick 'Jump to page' popover & optional page-size selector
    """

    PAGE_SIZES = [12, 24, 48]

    def __init__(
        self,
        on_page_changed: Callable[[int], None],
        on_page_size_changed: Optional[Callable[[int], None]] = None,
        default_page_size: int = 24,
    ):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("pagination-bar")
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)

        self.on_page_changed = on_page_changed
        self.on_page_size_changed = on_page_size_changed

        self.current_page: int = 1
        self.total_pages: int = 1
        self.total_items: int = 0
        self.page_size: int = default_page_size
        self._compact_mode: bool = False
        self._updating_controls: bool = False

        # Left summary label
        self.summary_lbl = Gtk.Label(label="Page 1 of 1")
        self.summary_lbl.add_css_class("caption")
        self.summary_lbl.add_css_class("dim-label")
        self.summary_lbl.set_halign(Gtk.Align.START)
        self.summary_lbl.set_valign(Gtk.Align.CENTER)
        self.summary_lbl.set_ellipsize(3)
        self.append(self.summary_lbl)

        # Center page buttons container
        center_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        center_wrap.set_hexpand(True)
        center_wrap.set_halign(Gtk.Align.CENTER)
        center_wrap.set_valign(Gtk.Align.CENTER)

        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.add_css_class("flat")
        self.prev_btn.add_css_class("page-btn")
        self.prev_btn.set_tooltip_text("Previous Page")
        self.prev_btn.connect("clicked", lambda _b: self._go_to(self.current_page - 1))
        center_wrap.append(self.prev_btn)

        self.pages_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.pages_box.set_valign(Gtk.Align.CENTER)
        center_wrap.append(self.pages_box)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.add_css_class("flat")
        self.next_btn.add_css_class("page-btn")
        self.next_btn.set_tooltip_text("Next Page")
        self.next_btn.connect("clicked", lambda _b: self._go_to(self.current_page + 1))
        center_wrap.append(self.next_btn)

        self.append(center_wrap)

        # Right controls: Jump to page popover + optional page size dropdown
        right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        right_box.set_halign(Gtk.Align.END)
        right_box.set_valign(Gtk.Align.CENTER)

        self.jump_btn = Gtk.MenuButton(icon_name="view-paged-symbolic")
        self.jump_btn.add_css_class("flat")
        self.jump_btn.set_tooltip_text("Jump to Page…")
        self._build_jump_popover()
        right_box.append(self.jump_btn)

        if self.on_page_size_changed is not None:
            size_labels = [f"{s} / page" for s in self.PAGE_SIZES]
            self.size_dropdown = Gtk.DropDown.new_from_strings(size_labels)
            self.size_dropdown.set_valign(Gtk.Align.CENTER)
            self.size_dropdown.set_tooltip_text("Items per page")
            if default_page_size in self.PAGE_SIZES:
                self.size_dropdown.set_selected(self.PAGE_SIZES.index(default_page_size))
            self.size_dropdown.connect("notify::selected", self._on_size_selected)
            right_box.append(self.size_dropdown)
        else:
            self.size_dropdown = None

        self.append(right_box)
        self.set_visible(False)

    def _build_jump_popover(self) -> None:
        self.jump_popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)

        lbl = Gtk.Label(label="Page:")
        lbl.add_css_class("caption")
        box.append(lbl)

        self.jump_spin = Gtk.SpinButton.new_with_range(1, 1, 1)
        self.jump_spin.set_numeric(True)
        self.jump_spin.connect("activate", lambda _s: self._apply_jump())
        box.append(self.jump_spin)

        go_btn = Gtk.Button(label="Go")
        go_btn.add_css_class("suggested-action")
        go_btn.connect("clicked", lambda _b: self._apply_jump())
        box.append(go_btn)

        self.jump_popover.set_child(box)
        self.jump_btn.set_popover(self.jump_popover)

    def _apply_jump(self) -> None:
        target = int(self.jump_spin.get_value())
        self.jump_popover.popdown()
        self._go_to(target)

    def _on_size_selected(self, dropdown: Gtk.DropDown, _p) -> None:
        if self._updating_controls or not self.on_page_size_changed:
            return
        idx = dropdown.get_selected()
        if 0 <= idx < len(self.PAGE_SIZES):
            new_size = self.PAGE_SIZES[idx]
            if new_size != self.page_size:
                self.page_size = new_size
                self.on_page_size_changed(new_size)

    def adapt_width(self, width: int) -> None:
        compact = width < 640
        if compact != self._compact_mode:
            self._compact_mode = compact
            self.summary_lbl.set_visible(not compact)
            if self.size_dropdown:
                self.size_dropdown.set_visible(width >= 520)
            self._rebuild_buttons()

    def update_state(
        self,
        page: int,
        total_pages: int,
        total_items: int = 0,
        always_show_if_items: bool = False,
    ) -> None:
        self.current_page = max(1, page)
        self.total_pages = max(1, total_pages)
        self.total_items = max(0, total_items)

        should_show = self.total_pages > 1 or (always_show_if_items and self.total_items > self.PAGE_SIZES[0])
        self.set_visible(should_show)
        if not should_show:
            return

        if self.total_items > 0:
            self.summary_lbl.set_label(
                f"Page {self.current_page:,} of {self.total_pages:,} ({self.total_items:,} items)"
            )
        else:
            self.summary_lbl.set_label(f"Page {self.current_page:,} of {self.total_pages:,}")

        self.prev_btn.set_sensitive(self.current_page > 1)
        self.next_btn.set_sensitive(self.current_page < self.total_pages)

        self.jump_spin.set_range(1, max(1, self.total_pages))
        self.jump_spin.set_value(self.current_page)
        self.jump_btn.set_visible(self.total_pages > 5)

        self._rebuild_buttons()

    def _compute_page_tokens(self) -> List[Optional[int]]:
        """Returns page numbers with None representing an ellipsis gap."""
        total = self.total_pages
        cur = self.current_page

        if self._compact_mode:
            if total <= 3:
                return list(range(1, total + 1))
            tokens: List[Optional[int]] = [1]
            if cur > 2:
                tokens.append(None)
            if cur not in (1, total):
                tokens.append(cur)
            if cur < total - 1:
                tokens.append(None)
            tokens.append(total)
            return tokens

        if total <= 7:
            return list(range(1, total + 1))

        tokens = [1]
        window_start = max(2, cur - 1)
        window_end = min(total - 1, cur + 1)

        if cur <= 3:
            window_start, window_end = 2, 4
        elif cur >= total - 2:
            window_start, window_end = total - 3, total - 1

        if window_start > 2:
            tokens.append(None)
        for p in range(window_start, window_end + 1):
            tokens.append(p)
        if window_end < total - 1:
            tokens.append(None)
        tokens.append(total)
        return tokens

    def _rebuild_buttons(self) -> None:
        child = self.pages_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.pages_box.remove(child)
            child = nxt

        for tok in self._compute_page_tokens():
            if tok is None:
                dots = Gtk.Label(label="…")
                dots.add_css_class("dim-label")
                dots.set_margin_start(3)
                dots.set_margin_end(3)
                self.pages_box.append(dots)
            else:
                btn = Gtk.Button(label=str(tok))
                btn.add_css_class("page-btn")
                if tok == self.current_page:
                    btn.add_css_class("current-page")
                else:
                    btn.add_css_class("flat")
                    btn.connect("clicked", lambda _b, p=tok: self._go_to(p))
                self.pages_box.append(btn)

    def _go_to(self, target_page: int) -> None:
        clamped = max(1, min(self.total_pages, target_page))
        if clamped != self.current_page:
            self.current_page = clamped
            self.on_page_changed(clamped)
