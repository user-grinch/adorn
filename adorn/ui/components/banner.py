"""Featured Showcase Carousel Banner for the Adorn Storefront."""

import hashlib
import os
from datetime import date
from typing import Callable, List, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from adorn.core.models import CustomizationItem
from adorn.core.ocs_client import OcsClient
from adorn.ui.image_utils import (
    format_count,
    load_cropped_texture_async,
    load_fixed_square_texture_async,
)


class FeaturedBanner(Gtk.Box):
    """
    GNOME Software-inspired Featured Hero Carousel using Adw.Carousel
    with subtle Libadwaita accent tinting and indicator dots.
    """

    def __init__(
        self,
        ocs_client: OcsClient,
        on_item_activated: Callable[[CustomizationItem], None],
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.ocs = ocs_client
        self.on_item_activated = on_item_activated
        self.items: List[CustomizationItem] = []
        self._artwork_frames: List[Gtk.Widget] = []
        self._show_artwork: bool = True
        self.set_hexpand(True)

        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        self.append(overlay)

        self.carousel = Adw.Carousel()
        self.carousel.set_hexpand(True)
        self.carousel.set_spacing(16)
        self.carousel.set_allow_scroll_wheel(True)
        self.carousel.connect("page-changed", lambda _c, _idx: self._sync_nav_buttons())
        overlay.set_child(self.carousel)

        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_btn.add_css_class("circular")
        self.prev_btn.add_css_class("osd")
        self.prev_btn.set_valign(Gtk.Align.CENTER)
        self.prev_btn.set_halign(Gtk.Align.START)
        self.prev_btn.set_margin_start(10)
        self.prev_btn.set_tooltip_text("Previous Highlight")
        self.prev_btn.connect("clicked", lambda _b: self._step(-1))
        overlay.add_overlay(self.prev_btn)

        self.next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_btn.add_css_class("circular")
        self.next_btn.add_css_class("osd")
        self.next_btn.set_valign(Gtk.Align.CENTER)
        self.next_btn.set_halign(Gtk.Align.END)
        self.next_btn.set_margin_end(10)
        self.next_btn.set_tooltip_text("Next Highlight")
        self.next_btn.connect("clicked", lambda _b: self._step(1))
        overlay.add_overlay(self.next_btn)

        self.dots = Adw.CarouselIndicatorDots()
        self.dots.set_carousel(self.carousel)
        self.append(self.dots)

        self.set_visible(False)

    def adapt_width(self, width: int) -> None:
        show_art = width >= 620
        if show_art != self._show_artwork:
            self._show_artwork = show_art
            for frame in self._artwork_frames:
                frame.set_visible(show_art)

    def _step(self, delta: int) -> None:
        n = self.carousel.get_n_pages()
        if n <= 1:
            return
        cur = int(round(self.carousel.get_position()))
        nxt = (cur + delta) % n
        self.carousel.scroll_to(self.carousel.get_nth_page(nxt), True)

    def _sync_nav_buttons(self) -> None:
        n = self.carousel.get_n_pages()
        visible = n > 1
        self.prev_btn.set_visible(visible)
        self.next_btn.set_visible(visible)
        self.dots.set_visible(visible)

    def _clear_carousel(self) -> None:
        self._artwork_frames.clear()
        while self.carousel.get_n_pages() > 0:
            page = self.carousel.get_nth_page(0)
            self.carousel.remove(page)

    def set_items(self, items: List[CustomizationItem]) -> None:
        self.items = list(items)
        self._clear_carousel()
        if not self.items:
            self.set_visible(False)
            return

        today_seed = date.today().isoformat()
        with_shots = [it for it in self.items if it.screenshots or it.preview_url]
        pool = with_shots if len(with_shots) >= 4 else self.items
        ordered = sorted(
            pool,
            key=lambda x: hashlib.sha256(f"{today_seed}:{x.category.value}:{x.item_id}".encode()).hexdigest(),
        )

        # Pick diverse categories for the showcase
        selected: List[CustomizationItem] = []
        seen_cats = set()
        for it in ordered:
            if it.category not in seen_cats:
                selected.append(it)
                seen_cats.add(it.category)
            if len(selected) >= 6:
                break
        for it in ordered:
            if len(selected) >= 6:
                break
            if it not in selected:
                selected.append(it)

        for item in selected[:6]:
            self.carousel.append(self._build_slide(item))

        self.set_visible(True)
        self._sync_nav_buttons()

    def _build_slide(self, item: CustomizationItem) -> Gtk.Widget:
        slide = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        slide.add_css_class("hero-carousel-card")
        slide.set_hexpand(True)
        slide.set_size_request(-1, 200)
        slide.set_margin_start(2)
        slide.set_margin_end(2)

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        inner.set_hexpand(True)
        inner.set_margin_top(22)
        inner.set_margin_bottom(22)
        inner.set_margin_start(28)
        inner.set_margin_end(28)
        slide.append(inner)

        # Left column: Category pill, Icon + Title, Summary, CTA button
        left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        left_col.set_valign(Gtk.Align.CENTER)
        left_col.set_hexpand(True)

        top_badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cat_pill = Gtk.Label(label=item.category.display_name)
        cat_pill.add_css_class("badge-pill")
        cat_pill.add_css_class("badge-accent")
        top_badges.append(cat_pill)

        if item.downloads > 0:
            dl_pill = Gtk.Label(label=f"↓ {format_count(item.downloads)}")
            dl_pill.add_css_class("badge-pill")
            dl_pill.add_css_class("badge-neutral")
            top_badges.append(dl_pill)
        left_col.append(top_badges)

        identity_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        identity_row.set_valign(Gtk.Align.CENTER)

        icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        icon_box.add_css_class("hero-icon-squircle")
        icon_box.set_size_request(56, 56)
        icon_box.set_valign(Gtk.Align.CENTER)
        icon_box.set_halign(Gtk.Align.START)
        icon_box.set_hexpand(False)
        icon_box.set_vexpand(False)
        icon_box.set_overflow(Gtk.Overflow.HIDDEN)

        fb_icon = Gtk.Image.new_from_icon_name(item.category.icon_name)
        fb_icon.set_pixel_size(28)
        fb_icon.set_valign(Gtk.Align.CENTER)
        fb_icon.set_halign(Gtk.Align.CENTER)
        fb_icon.set_vexpand(True)
        fb_icon.set_hexpand(True)
        icon_box.append(fb_icon)

        icon_img = Gtk.Image()
        icon_img.set_pixel_size(56)
        icon_img.set_size_request(56, 56)
        icon_img.set_valign(Gtk.Align.CENTER)
        icon_img.set_halign(Gtk.Align.CENTER)
        icon_img.set_vexpand(False)
        icon_img.set_hexpand(False)
        icon_img.set_visible(False)
        icon_box.append(icon_img)

        thumb_url = item.thumbnail_url or item.preview_url
        if thumb_url:
            def _on_icon_dl(path: Optional[str], img=icon_img, fb=fb_icon):
                if path and os.path.exists(path):
                    def _apply_tex(tex: Optional[Gdk.Texture]):
                        if tex:
                            img.set_from_paintable(tex)
                            fb.set_visible(False)
                            img.set_visible(True)
                    load_fixed_square_texture_async(path, 56, _apply_tex)
            self.ocs.load_thumbnail_async(thumb_url, _on_icon_dl)

        identity_row.append(icon_box)

        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_col.set_valign(Gtk.Align.CENTER)
        title_col.set_hexpand(True)

        title_lbl = Gtk.Label(label=item.name)
        title_lbl.add_css_class("title-2")
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_xalign(0.0)
        title_lbl.set_ellipsize(3)
        title_lbl.set_lines(1)
        title_col.append(title_lbl)

        author_lbl = Gtk.Label(label=f"by {item.author}")
        author_lbl.add_css_class("caption")
        author_lbl.add_css_class("dim-label")
        author_lbl.set_halign(Gtk.Align.START)
        title_col.append(author_lbl)

        identity_row.append(title_col)
        left_col.append(identity_row)

        clean_desc = " ".join((item.summary or item.description or "").split())
        if not clean_desc:
            clean_desc = f"Featured {item.category.display_name.lower()} package for GNOME."
        desc_lbl = Gtk.Label(label=clean_desc[:140])
        desc_lbl.add_css_class("body")
        desc_lbl.add_css_class("dim-label")
        desc_lbl.set_halign(Gtk.Align.START)
        desc_lbl.set_xalign(0.0)
        desc_lbl.set_wrap(True)
        desc_lbl.set_lines(2)
        desc_lbl.set_ellipsize(3)
        left_col.append(desc_lbl)

        cta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cta_row.set_margin_top(4)
        open_btn = Gtk.Button()
        open_btn.add_css_class("pill")
        open_btn.add_css_class("suggested-action")
        open_btn.set_child(Adw.ButtonContent(icon_name="go-next-symbolic", label="View Details"))
        open_btn.connect("clicked", lambda _b, it=item: self.on_item_activated(it))
        cta_row.append(open_btn)
        left_col.append(cta_row)

        inner.append(left_col)

        # Right column: Framed 16:10 screenshot preview (if available)
        shot_url = item.screenshots[0] if item.screenshots else item.preview_url
        if shot_url and not shot_url.endswith(".svg"):
            art_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            art_frame.add_css_class("hero-artwork-frame")
            art_frame.set_size_request(256, 156)
            art_frame.set_valign(Gtk.Align.CENTER)
            art_frame.set_overflow(Gtk.Overflow.HIDDEN)
            art_frame.set_visible(self._show_artwork)

            art_img = Gtk.Image()
            art_img.set_pixel_size(256)
            art_img.set_size_request(256, 156)
            art_frame.append(art_img)

            def _on_shot_dl(path: Optional[str], img=art_img):
                if path and os.path.exists(path):
                    def _apply_art(tex: Optional[Gdk.Texture]):
                        if tex:
                            img.set_from_paintable(tex)
                    load_cropped_texture_async(path, 256, 156, _apply_art)

            self.ocs.load_thumbnail_async(shot_url, _on_shot_dl)
            self._artwork_frames.append(art_frame)
            inner.append(art_frame)

        return slide
