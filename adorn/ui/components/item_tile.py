"""Modern GNOME Storefront item card component."""

import os
from typing import Callable, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from adorn.core.models import CustomizationItem, ItemCategory
from adorn.core.ocs_client import OcsClient
from adorn.core.task_manager import TaskEntry, TaskManager
from adorn.ui.image_utils import (
    format_count,
    load_cropped_texture_async,
    load_fixed_square_texture_async,
)


class ItemTile(Gtk.Box):
    """
    Modern Libadwaita storefront card:
    - Uniform 56x56 square icon squircle for all items
    - Crisp Title + Installed/Active status badge
    - Clean 1-line description
    - Real metadata row (Author, Rating or GNOME compatibility, Download count)
    - Quick Install / Tick Apply / Cancel action button on the right
    """

    def __init__(
        self,
        item: CustomizationItem,
        ocs_client: OcsClient,
        on_clicked: Callable[[CustomizationItem], None],
        on_quick_install: Optional[Callable[[CustomizationItem], None]] = None,
        on_quick_apply: Optional[Callable[[CustomizationItem], None]] = None,
        show_category: bool = False,
    ):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.item = item
        self.ocs = ocs_client
        self.on_clicked = on_clicked
        self.on_quick_install = on_quick_install
        self.on_quick_apply = on_quick_apply
        self._pulse_source_id: Optional[int] = None

        self.add_css_class("store-item-card")
        self.set_hexpand(True)

        # Left + Center clickable area (opens DetailPage)
        click_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        click_area.set_hexpand(True)
        click_area.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
        gesture = Gtk.GestureClick.new()
        gesture.connect("released", lambda _g, _n, _x, _y: self.on_clicked(self.item))
        click_area.add_controller(gesture)
        self.append(click_area)

        # Thumbnail / Icon Frame - Strictly 56x56 square for uniform storefront alignment
        self._thumb_w = 56
        self._thumb_h = 56

        self.icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.icon_box.add_css_class("store-icon-frame")
        self.icon_box.set_size_request(56, 56)
        self.icon_box.set_valign(Gtk.Align.CENTER)
        self.icon_box.set_halign(Gtk.Align.START)
        self.icon_box.set_hexpand(False)
        self.icon_box.set_vexpand(False)
        self.icon_box.set_overflow(Gtk.Overflow.HIDDEN)

        self.fallback_img = Gtk.Image.new_from_icon_name(item.category.icon_name)
        self.fallback_img.set_pixel_size(26)
        self.fallback_img.set_valign(Gtk.Align.CENTER)
        self.fallback_img.set_halign(Gtk.Align.CENTER)
        self.fallback_img.set_vexpand(True)
        self.fallback_img.set_hexpand(True)
        self.icon_box.append(self.fallback_img)

        self.thumb_img = Gtk.Image()
        self.thumb_img.set_pixel_size(56)
        self.thumb_img.set_size_request(56, 56)
        self.thumb_img.set_valign(Gtk.Align.CENTER)
        self.thumb_img.set_halign(Gtk.Align.CENTER)
        self.thumb_img.set_vexpand(False)
        self.thumb_img.set_hexpand(False)
        self.thumb_img.set_visible(False)
        self.icon_box.append(self.thumb_img)
        click_area.append(self.icon_box)

        # Text Column
        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        text_col.set_valign(Gtk.Align.CENTER)
        text_col.set_hexpand(True)

        # Row 1: Title + Status Badge
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_row.set_valign(Gtk.Align.CENTER)

        self.title_lbl = Gtk.Label(label=item.name)
        self.title_lbl.add_css_class("heading")
        self.title_lbl.set_halign(Gtk.Align.START)
        self.title_lbl.set_xalign(0.0)
        self.title_lbl.set_ellipsize(3)
        self.title_lbl.set_lines(1)
        self.title_lbl.set_hexpand(True)
        title_row.append(self.title_lbl)

        self.status_badge = Gtk.Label()
        self.status_badge.add_css_class("badge-pill")
        self.status_badge.set_visible(False)
        title_row.append(self.status_badge)

        text_col.append(title_row)

        # Row 2: Clean 1-line summary
        self._default_summary = self._compute_summary()
        self.sub_lbl = Gtk.Label(label=self._default_summary)
        self.sub_lbl.add_css_class("caption")
        self.sub_lbl.add_css_class("dim-label")
        self.sub_lbl.set_halign(Gtk.Align.START)
        self.sub_lbl.set_xalign(0.0)
        self.sub_lbl.set_ellipsize(3)
        self.sub_lbl.set_lines(1)
        text_col.append(self.sub_lbl)

        # Row 3: Metadata chips
        meta_parts = []
        if show_category:
            meta_parts.append(item.category.short_name)
        if item.author:
            meta_parts.append(item.author)
        if item.category == ItemCategory.EXTENSIONS:
            meta_parts.append("Compatible" if item.is_compatible else "Unverified version")
        elif item.rating > 0:
            meta_parts.append(f"★ {item.rating:.0f}%")
        if item.downloads > 0:
            meta_parts.append(f"↓ {format_count(item.downloads)}")

        meta_lbl = Gtk.Label(label="  •  ".join(meta_parts))
        meta_lbl.add_css_class("caption")
        meta_lbl.add_css_class("dim-label")
        meta_lbl.set_opacity(0.82)
        meta_lbl.set_halign(Gtk.Align.START)
        meta_lbl.set_xalign(0.0)
        meta_lbl.set_ellipsize(3)
        meta_lbl.set_lines(1)
        text_col.append(meta_lbl)

        # Inline progress bar for active downloads
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_margin_top(4)
        self.progress_bar.set_visible(False)
        self.progress_bar.set_show_text(True)
        text_col.append(self.progress_bar)

        click_area.append(text_col)

        # Right Quick Action Area (separate from click_area so clicks don't open DetailPage)
        self.action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.action_box.set_valign(Gtk.Align.CENTER)
        self.append(self.action_box)

        self.refresh_state()

        TaskManager.get_default().subscribe_task(self._on_task_updated)

        thumb = item.thumbnail_url or (item.screenshots[0] if item.screenshots else None)
        if thumb:
            self.ocs.load_thumbnail_async(thumb, self._on_thumb_loaded)

    def _compute_summary(self) -> str:
        clean = " ".join((self.item.summary or self.item.description or "").split())
        if not clean:
            return f"{self.item.category.display_name} package"
        return clean[:100]

    def _on_thumb_loaded(self, path: Optional[str]) -> None:
        if path and os.path.exists(path):
            load_fixed_square_texture_async(path, 56, self._apply_thumb_texture)

    def _apply_thumb_texture(self, tex: Optional[Gdk.Texture]) -> None:
        if tex:
            self.thumb_img.set_from_paintable(tex)
            self.fallback_img.set_visible(False)
            self.thumb_img.set_visible(True)

    def _stop_pulse(self) -> None:
        if self._pulse_source_id is not None:
            GLib.source_remove(self._pulse_source_id)
            self._pulse_source_id = None

    def _start_pulse(self) -> None:
        if self._pulse_source_id is not None:
            return

        def _tick():
            if not self.progress_bar.get_visible():
                self._pulse_source_id = None
                return False
            self.progress_bar.pulse()
            return True

        self._pulse_source_id = GLib.timeout_add(100, _tick)

    def _clear_action_box(self) -> None:
        child = self.action_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.action_box.remove(child)
            child = nxt

    def _rebuild_actions_and_badge(self, active_task: Optional[TaskEntry]) -> None:
        self._clear_action_box()
        self.status_badge.remove_css_class("badge-accent")
        self.status_badge.remove_css_class("badge-success")

        if active_task:
            self.status_badge.set_visible(False)
            spin = Adw.Spinner()
            spin.set_size_request(18, 18)
            spin.set_valign(Gtk.Align.CENTER)
            self.action_box.append(spin)

            stop_btn = Gtk.Button(icon_name="process-stop-symbolic")
            stop_btn.add_css_class("circular")
            stop_btn.add_css_class("flat")
            stop_btn.add_css_class("compact-btn")
            stop_btn.set_valign(Gtk.Align.CENTER)
            stop_btn.set_tooltip_text("Cancel Download")
            stop_btn.connect("clicked", lambda _b, t=active_task: t.cancel_event.set())
            self.action_box.append(stop_btn)
            return

        if self.item.is_installed:
            if self.item.is_active:
                self.status_badge.set_label("Active")
                self.status_badge.add_css_class("badge-accent")
            else:
                self.status_badge.set_label("Installed")
                self.status_badge.add_css_class("badge-success")
            self.status_badge.set_visible(True)

            if not self.item.is_active and self.on_quick_apply:
                lbl = "Enable" if self.item.category == ItemCategory.EXTENSIONS else "Apply"
                apply_btn = Gtk.Button()
                apply_btn.add_css_class("pill")
                apply_btn.add_css_class("compact-btn")
                apply_btn.add_css_class("suggested-action")
                apply_btn.set_valign(Gtk.Align.CENTER)
                apply_btn.set_tooltip_text(f"{lbl} {self.item.name}")
                apply_btn.set_child(
                    Adw.ButtonContent(icon_name="object-select-symbolic", label=lbl)
                )
                apply_btn.connect("clicked", lambda _b: self._trigger_quick_apply())
                self.action_box.append(apply_btn)
        else:
            self.status_badge.set_visible(False)
            if self.on_quick_install:
                has_variants = len(self.item.download_files) > 1
                dl_btn = Gtk.Button(
                    icon_name="go-next-symbolic" if has_variants else "folder-download-symbolic"
                )
                dl_btn.add_css_class("circular")
                dl_btn.add_css_class("flat")
                dl_btn.add_css_class("compact-btn")
                dl_btn.set_valign(Gtk.Align.CENTER)
                dl_btn.set_tooltip_text(
                    "Choose variant to install…" if has_variants else f"Install {self.item.name}"
                )
                dl_btn.connect(
                    "clicked",
                    lambda _b: (
                        self.on_clicked(self.item)
                        if len(self.item.download_files) > 1
                        else self.on_quick_install(self.item)
                    ),
                )
                self.action_box.append(dl_btn)

    def _trigger_quick_apply(self) -> None:
        if self.on_quick_apply:
            self.on_quick_apply(self.item)
            self.refresh_state()

    def _on_task_updated(self, task: TaskEntry) -> None:
        if str(task.item_id) != str(self.item.item_id):
            return
        if task.status in ("downloading", "extracting", "installing"):
            was_visible = self.progress_bar.get_visible()
            self.progress_bar.set_visible(True)
            if task.status == "extracting":
                self._start_pulse()
                self.progress_bar.set_text("Extracting…")
            elif task.status == "installing":
                self._start_pulse()
                self.progress_bar.set_text("Installing…")
            else:
                self._stop_pulse()
                pct = int(task.progress * 100)
                self.progress_bar.set_fraction(task.progress)
                self.progress_bar.set_text(f"{pct}%")
            self.sub_lbl.set_label(task.status_text)
            if not was_visible:
                self._rebuild_actions_and_badge(task)
        elif task.status == "done":
            self._stop_pulse()
            self.progress_bar.set_visible(False)
            self.item.is_installed = True
            self.sub_lbl.set_label(self._default_summary)
            self._rebuild_actions_and_badge(None)
        elif task.status in ("uninstalled", "failed", "cancelled"):
            self._stop_pulse()
            self.progress_bar.set_visible(False)
            if task.status == "uninstalled":
                self.item.is_installed = False
                self.item.is_active = False
            self.sub_lbl.set_label(self._default_summary)
            self._rebuild_actions_and_badge(None)

    def refresh_state(self) -> None:
        active_task = TaskManager.get_default().get_task_for_item(self.item.item_id)
        if active_task:
            self.progress_bar.set_visible(True)
            self.progress_bar.set_fraction(active_task.progress)
            self.sub_lbl.set_label(active_task.status_text)
            self._rebuild_actions_and_badge(active_task)
        else:
            self._stop_pulse()
            self.progress_bar.set_visible(False)
            self.sub_lbl.set_label(self._default_summary)
            self._rebuild_actions_and_badge(None)


class WallpaperTile(Gtk.Box):
    """
    Modern storefront card dedicated to Wallpapers:
    - Large 16:9 / 16:10 preview (height 230px) that properly displays wallpaper artwork
    - Fallback centered symbolic icon while image loads or if missing
    - Active badge indicator
    - Click to open DetailPage
    - Bottom bar with Title, author, downloads, and compact actions (tick apply / download / cancel)
    """

    def __init__(
        self,
        item: CustomizationItem,
        ocs_client: OcsClient,
        on_clicked: Callable[[CustomizationItem], None],
        on_quick_install: Optional[Callable[[CustomizationItem], None]] = None,
        on_quick_apply: Optional[Callable[[CustomizationItem], None]] = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.item = item
        self.ocs = ocs_client
        self.on_clicked = on_clicked
        self.on_quick_install = on_quick_install
        self.on_quick_apply = on_quick_apply
        self._pulse_source_id: Optional[int] = None

        self.add_css_class("wallpaper-card")
        if item.is_active:
            self.add_css_class("wallpaper-active")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_hexpand(True)

        # Top area: Large preview image overlay
        art_overlay = Gtk.Overlay()
        art_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        art_box.set_size_request(-1, 230)
        art_box.set_overflow(Gtk.Overflow.HIDDEN)
        art_box.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

        self.fallback_art = Gtk.Image.new_from_icon_name("preferences-desktop-wallpaper-symbolic")
        self.fallback_art.set_pixel_size(48)
        self.fallback_art.set_valign(Gtk.Align.CENTER)
        self.fallback_art.set_halign(Gtk.Align.CENTER)
        self.fallback_art.set_vexpand(True)
        self.fallback_art.set_hexpand(True)
        art_box.append(self.fallback_art)

        self.pic = Gtk.Picture()
        self.pic.set_content_fit(Gtk.ContentFit.COVER)
        self.pic.set_can_shrink(True)
        self.pic.set_size_request(-1, 230)
        self.pic.set_hexpand(True)
        self.pic.set_visible(False)
        art_box.append(self.pic)
        art_overlay.set_child(art_box)

        art_click = Gtk.GestureClick.new()
        art_click.connect("released", lambda _g, _n, _x, _y: self.on_clicked(self.item))
        art_box.add_controller(art_click)

        # Active badge overlay
        self.active_pill = Gtk.Label(label="Active")
        self.active_pill.add_css_class("badge-pill")
        self.active_pill.add_css_class("badge-accent")
        self.active_pill.set_halign(Gtk.Align.END)
        self.active_pill.set_valign(Gtk.Align.START)
        self.active_pill.set_margin_top(10)
        self.active_pill.set_margin_end(10)
        self.active_pill.set_visible(item.is_active)
        art_overlay.add_overlay(self.active_pill)

        self.append(art_overlay)

        # Bottom row: Title + Meta & Actions
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.set_margin_top(10)
        footer.set_margin_bottom(10)
        footer.set_margin_start(14)
        footer.set_margin_end(14)
        footer.set_valign(Gtk.Align.CENTER)

        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_col.set_hexpand(True)
        text_col.set_valign(Gtk.Align.CENTER)
        text_col.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
        text_click = Gtk.GestureClick.new()
        text_click.connect("released", lambda _g, _n, _x, _y: self.on_clicked(self.item))
        text_col.add_controller(text_click)

        self.title_lbl = Gtk.Label(label=item.name)
        self.title_lbl.add_css_class("heading")
        self.title_lbl.set_halign(Gtk.Align.START)
        self.title_lbl.set_xalign(0.0)
        self.title_lbl.set_ellipsize(3)
        self.title_lbl.set_lines(1)
        text_col.append(self.title_lbl)

        meta_parts = []
        if item.author:
            meta_parts.append(item.author)
        if item.rating > 0:
            meta_parts.append(f"★ {item.rating:.0f}%")
        if item.downloads > 0:
            meta_parts.append(f"↓ {format_count(item.downloads)}")
        self._default_sub = "  •  ".join(meta_parts) if meta_parts else "Wallpaper"

        self.sub_lbl = Gtk.Label(label=self._default_sub)
        self.sub_lbl.add_css_class("caption")
        self.sub_lbl.add_css_class("dim-label")
        self.sub_lbl.set_halign(Gtk.Align.START)
        self.sub_lbl.set_xalign(0.0)
        self.sub_lbl.set_ellipsize(3)
        self.sub_lbl.set_lines(1)
        text_col.append(self.sub_lbl)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_margin_top(4)
        self.progress_bar.set_visible(False)
        text_col.append(self.progress_bar)

        footer.append(text_col)

        self.action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.action_box.set_valign(Gtk.Align.CENTER)
        footer.append(self.action_box)

        self.append(footer)

        self.refresh_state()
        TaskManager.get_default().subscribe_task(self._on_task_updated)

        thumb = item.thumbnail_url or (item.screenshots[0] if item.screenshots else None)
        if thumb:
            self.ocs.load_thumbnail_async(thumb, self._on_thumb_loaded)

    def _on_thumb_loaded(self, path: Optional[str]) -> None:
        if path and os.path.exists(path):
            load_cropped_texture_async(path, 720, 420, self._apply_thumb_texture)

    def _apply_thumb_texture(self, tex: Optional[Gdk.Texture]) -> None:
        if tex:
            self.pic.set_paintable(tex)
            self.fallback_art.set_visible(False)
            self.pic.set_visible(True)

    def _stop_pulse(self) -> None:
        if self._pulse_source_id is not None:
            GLib.source_remove(self._pulse_source_id)
            self._pulse_source_id = None

    def _start_pulse(self) -> None:
        if self._pulse_source_id is not None:
            return

        def _tick():
            if not self.progress_bar.get_visible():
                self._pulse_source_id = None
                return False
            self.progress_bar.pulse()
            return True

        self._pulse_source_id = GLib.timeout_add(100, _tick)

    def _clear_action_box(self) -> None:
        child = self.action_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.action_box.remove(child)
            child = nxt

    def _rebuild_actions_and_badge(self, active_task: Optional[TaskEntry]) -> None:
        self._clear_action_box()
        self.remove_css_class("wallpaper-active")

        if active_task:
            self.active_pill.set_visible(False)
            spin = Adw.Spinner()
            spin.set_size_request(18, 18)
            spin.set_valign(Gtk.Align.CENTER)
            self.action_box.append(spin)

            stop_btn = Gtk.Button(icon_name="process-stop-symbolic")
            stop_btn.add_css_class("circular")
            stop_btn.add_css_class("flat")
            stop_btn.add_css_class("compact-btn")
            stop_btn.set_valign(Gtk.Align.CENTER)
            stop_btn.set_tooltip_text("Cancel Download")
            stop_btn.connect("clicked", lambda _b, t=active_task: t.cancel_event.set())
            self.action_box.append(stop_btn)
            return

        if self.item.is_installed:
            if self.item.is_active:
                self.add_css_class("wallpaper-active")
                self.active_pill.set_visible(True)
            else:
                self.active_pill.set_visible(False)
                if self.on_quick_apply:
                    apply_btn = Gtk.Button()
                    apply_btn.add_css_class("pill")
                    apply_btn.add_css_class("compact-btn")
                    apply_btn.add_css_class("suggested-action")
                    apply_btn.set_valign(Gtk.Align.CENTER)
                    apply_btn.set_tooltip_text(f"Apply {self.item.name}")
                    apply_btn.set_child(
                        Adw.ButtonContent(icon_name="object-select-symbolic", label="Apply")
                    )
                    apply_btn.connect("clicked", lambda _b: self._trigger_quick_apply())
                    self.action_box.append(apply_btn)
        else:
            self.active_pill.set_visible(False)
            if self.on_quick_install:
                has_variants = len(self.item.download_files) > 1
                dl_btn = Gtk.Button(
                    icon_name="go-next-symbolic" if has_variants else "folder-download-symbolic"
                )
                dl_btn.add_css_class("circular")
                dl_btn.add_css_class("flat")
                dl_btn.add_css_class("compact-btn")
                dl_btn.set_valign(Gtk.Align.CENTER)
                dl_btn.set_tooltip_text(
                    "Choose variant to install…" if has_variants else f"Install {self.item.name}"
                )
                dl_btn.connect(
                    "clicked",
                    lambda _b: (
                        self.on_clicked(self.item)
                        if len(self.item.download_files) > 1
                        else self.on_quick_install(self.item)
                    ),
                )
                self.action_box.append(dl_btn)

    def _trigger_quick_apply(self) -> None:
        if self.on_quick_apply:
            self.on_quick_apply(self.item)
            self.refresh_state()

    def _on_task_updated(self, task: TaskEntry) -> None:
        if str(task.item_id) != str(self.item.item_id):
            return
        if task.status in ("downloading", "extracting", "installing"):
            was_visible = self.progress_bar.get_visible()
            self.progress_bar.set_visible(True)
            if task.status == "extracting":
                self._start_pulse()
                self.progress_bar.set_text("Extracting…")
            elif task.status == "installing":
                self._start_pulse()
                self.progress_bar.set_text("Installing…")
            else:
                self._stop_pulse()
                pct = int(task.progress * 100)
                self.progress_bar.set_fraction(task.progress)
                self.progress_bar.set_text(f"{pct}%")
            self.sub_lbl.set_label(task.status_text)
            if not was_visible:
                self._rebuild_actions_and_badge(task)
        elif task.status == "done":
            self._stop_pulse()
            self.progress_bar.set_visible(False)
            self.item.is_installed = True
            self.sub_lbl.set_label(self._default_sub)
            self._rebuild_actions_and_badge(None)
        elif task.status in ("uninstalled", "failed", "cancelled"):
            self._stop_pulse()
            self.progress_bar.set_visible(False)
            if task.status == "uninstalled":
                self.item.is_installed = False
                self.item.is_active = False
            self.sub_lbl.set_label(self._default_sub)
            self._rebuild_actions_and_badge(None)

    def refresh_state(self) -> None:
        active_task = TaskManager.get_default().get_task_for_item(self.item.item_id)
        if active_task:
            self.progress_bar.set_visible(True)
            self.progress_bar.set_fraction(active_task.progress)
            self.sub_lbl.set_label(active_task.status_text)
            self._rebuild_actions_and_badge(active_task)
        else:
            self._stop_pulse()
            self.progress_bar.set_visible(False)
            self.sub_lbl.set_label(self._default_sub)
            self._rebuild_actions_and_badge(None)

