"""Native Libadwaita NavigationPage for Storefront Item Details."""

import html
import os
import re
import threading
from typing import Callable, List, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from adorn.core.models import CustomizationItem, DownloadableFile, ItemCategory
from adorn.core.ocs_client import OcsClient
from adorn.core.task_manager import TaskEntry, TaskManager
from adorn.ui.image_utils import format_count, load_fixed_square_texture_async


def sanitize_description_to_pango(raw_text: str) -> str:
    """Converts Markdown, HTML, and BBCode descriptions into clean Pango markup."""
    if not raw_text:
        return "No additional description provided by the author."

    text = html.unescape(raw_text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li\s*>", "  • ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li\s*>", "\n", text, flags=re.IGNORECASE)

    bold_tokens: List[str] = []

    def _stash_bold(m):
        idx = len(bold_tokens)
        bold_tokens.append(m.group(1))
        return f"@@BOLD_{idx}@@"

    text = re.sub(r"<(?:b|strong)>(.*?)</(?:b|strong)>", _stash_bold, text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\*\*(.+?)\*\*", _stash_bold, text)
    text = re.sub(r"\[b\](.*?)\[/b\]", _stash_bold, text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"^#{1,4}\s+(.+)$", _stash_bold, text, flags=re.MULTILINE)

    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[/?[a-zA-Z0-9_=:#\s]+\]", "", text)
    text = html.escape(text)

    for idx, content in enumerate(bold_tokens):
        clean_inner = html.escape(re.sub(r"<[^>]+>", "", content).strip())
        text = text.replace(f"@@BOLD_{idx}@@", f"<b>{clean_inner}</b>")

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or "No additional description provided."


class DetailPage(Adw.NavigationPage):
    """
    Modern GNOME DetailPage pushed onto the root Adw.NavigationView:
    - Own Adw.ToolbarView + Adw.HeaderBar (native Back button & swipe-back)
    - Upstream webpage button in HeaderBar
    - Compatibility Adw.Banner when extension shell support is unverified
    - Full variant/package file selector for multi-variant theme archives
    - Click-to-zoom screenshot carousel
    """

    def __init__(
        self,
        item: CustomizationItem,
        ocs_client: OcsClient,
        on_install_requested: Callable[
            [CustomizationItem, Optional[DownloadableFile], Callable[[float, str], None], Callable[[bool, str], None]],
            None,
        ],
        on_apply_requested: Callable[[CustomizationItem], None],
        on_remove_requested: Optional[Callable[[CustomizationItem, Callable[[bool, str], None]], None]] = None,
        shell_version: str = "51",
    ):
        super().__init__(title=item.name)
        self.item = item
        self.ocs = ocs_client
        self.on_install_requested = on_install_requested
        self.on_apply_requested = on_apply_requested
        self.on_remove_requested = on_remove_requested
        self.shell_version = shell_version
        self._pulse_source_id: Optional[int] = None
        self._last_task_status: Optional[str] = None

        toolbar = Adw.ToolbarView()
        self.set_child(toolbar)

        # Native HeaderBar (Adw.NavigationView automatically supplies the '<' Back button!)
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title=item.name, subtitle=item.category.display_name)
        )

        if item.homepage_url:
            web_btn = Gtk.Button(icon_name="external-link-symbolic")
            web_btn.set_tooltip_text(f"Open on {item.source_label}")
            web_btn.connect("clicked", self._on_open_webpage)
            header.pack_end(web_btn)

        toolbar.add_top_bar(header)

        # Optional Compatibility Warning Banner for GNOME Shell Extensions
        if item.category == ItemCategory.EXTENSIONS and not item.is_compatible:
            compat_banner = Adw.Banner.new(
                f"This extension does not explicitly list GNOME {shell_version} support and may not load."
            )
            compat_banner.set_revealed(True)
            toolbar.add_top_bar(compat_banner)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        toolbar.set_content(scroller)

        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=600)
        scroller.set_child(clamp)

        self.main_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        self.main_col.set_margin_top(20)
        self.main_col.set_margin_bottom(36)
        self.main_col.set_margin_start(20)
        self.main_col.set_margin_end(20)
        clamp.set_child(self.main_col)

        # 1. Identity Hero Row
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        header_box.set_valign(Gtk.Align.CENTER)

        icon_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        icon_frame.add_css_class("detail-icon-frame")
        icon_frame.set_size_request(80, 80)
        icon_frame.set_valign(Gtk.Align.START)
        icon_frame.set_halign(Gtk.Align.START)
        icon_frame.set_hexpand(False)
        icon_frame.set_vexpand(False)
        icon_frame.set_overflow(Gtk.Overflow.HIDDEN)

        self.icon_fallback = Gtk.Image.new_from_icon_name(item.category.icon_name)
        self.icon_fallback.set_pixel_size(38)
        self.icon_fallback.set_valign(Gtk.Align.CENTER)
        self.icon_fallback.set_halign(Gtk.Align.CENTER)
        self.icon_fallback.set_vexpand(True)
        self.icon_fallback.set_hexpand(True)
        icon_frame.append(self.icon_fallback)

        self.icon_img = Gtk.Image()
        self.icon_img.set_pixel_size(80)
        self.icon_img.set_size_request(80, 80)
        self.icon_img.set_valign(Gtk.Align.CENTER)
        self.icon_img.set_halign(Gtk.Align.CENTER)
        self.icon_img.set_vexpand(False)
        self.icon_img.set_hexpand(False)
        self.icon_img.set_visible(False)
        icon_frame.append(self.icon_img)
        header_box.append(icon_frame)

        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title_col.set_valign(Gtk.Align.CENTER)
        title_col.set_hexpand(True)

        name_lbl = Gtk.Label(label=item.name)
        name_lbl.add_css_class("title-1")
        name_lbl.set_halign(Gtk.Align.START)
        name_lbl.set_xalign(0.0)
        name_lbl.set_wrap(True)
        title_col.append(name_lbl)

        author_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        author_row.set_halign(Gtk.Align.START)
        dev_lbl = Gtk.Label(label=item.author or "Community")
        dev_lbl.add_css_class("heading")
        dev_lbl.add_css_class("dim-label")
        author_row.append(dev_lbl)

        if item.category == ItemCategory.EXTENSIONS and item.is_compatible:
            compat_pill = Gtk.Label(label=f"GNOME {shell_version}")
            compat_pill.add_css_class("badge-pill")
            compat_pill.add_css_class("badge-success")
            author_row.append(compat_pill)

        title_col.append(author_row)

        if item.summary:
            tagline_lbl = Gtk.Label(label=" ".join(item.summary.split())[:130])
            tagline_lbl.add_css_class("dim-label")
            tagline_lbl.set_halign(Gtk.Align.START)
            tagline_lbl.set_xalign(0.0)
            tagline_lbl.set_wrap(True)
            tagline_lbl.set_margin_top(2)
            title_col.append(tagline_lbl)

        header_box.append(title_col)

        # Right Action Cluster
        action_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        action_col.set_valign(Gtk.Align.CENTER)
        action_col.set_halign(Gtk.Align.END)

        self.btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_row.set_halign(Gtk.Align.END)
        action_col.append(self.btn_row)

        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.progress_box.set_visible(False)
        self.progress_box.set_size_request(210, -1)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_lbl = Gtk.Label(label="Preparing…")
        self.progress_lbl.add_css_class("caption")
        self.progress_lbl.add_css_class("dim-label")
        self.progress_lbl.set_halign(Gtk.Align.END)
        self.progress_box.append(self.progress_bar)
        self.progress_box.append(self.progress_lbl)
        action_col.append(self.progress_box)

        header_box.append(action_col)
        self.main_col.append(header_box)

        self._rebuild_action_buttons()

        # 2. Screenshot Carousel (shown only when screenshots exist)
        self.screenshots_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_col.append(self.screenshots_container)
        self._populate_screenshots()

        # 3. GNOME Software-style 4-Cell Context Bar
        self.context_flow = Gtk.FlowBox()
        self.context_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.context_flow.set_homogeneous(True)
        self.context_flow.set_min_children_per_line(2)
        self.context_flow.set_max_children_per_line(4)
        self.context_flow.set_column_spacing(10)
        self.context_flow.set_row_spacing(10)
        self.main_col.append(self.context_flow)
        self._populate_context_bar()

        # 4. Package Files / Variants Section (when >1 file or to inspect download size)
        self.variants_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_col.append(self.variants_container)
        self._populate_variants_group()

        # 5. Description Card
        desc_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        desc_card.add_css_class("description-card")

        about_hdr = Gtk.Label(label="Description")
        about_hdr.add_css_class("title-4")
        about_hdr.set_halign(Gtk.Align.START)
        desc_card.append(about_hdr)

        self.desc_lbl = Gtk.Label()
        self.desc_lbl.set_markup(sanitize_description_to_pango(item.description or item.summary))
        self.desc_lbl.set_wrap(True)
        self.desc_lbl.set_halign(Gtk.Align.START)
        self.desc_lbl.set_selectable(True)
        self.desc_lbl.set_xalign(0.0)
        desc_card.append(self.desc_lbl)
        self.main_col.append(desc_card)

        thumb_url = item.thumbnail_url or (item.screenshots[0] if item.screenshots else None)
        if thumb_url:
            self.ocs.load_thumbnail_async(thumb_url, self._on_icon_loaded)

        TaskManager.get_default().subscribe_task(self._on_task_updated)

        # Enrich OCS details asynchronously if download_files are missing
        if item.category != ItemCategory.EXTENSIONS and not item.download_files:
            self._enrich_ocs_details_async()

    def _enrich_ocs_details_async(self) -> None:
        def worker():
            detailed = self.ocs.fetch_item_details(self.item.item_id, self.item.category)
            if detailed:
                GLib.idle_add(self._apply_enriched_details, detailed)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_enriched_details(self, detailed: CustomizationItem) -> bool:
        if detailed.download_files:
            self.item.download_files = detailed.download_files
        if detailed.screenshots and len(detailed.screenshots) > len(self.item.screenshots):
            self.item.screenshots = detailed.screenshots
            self._populate_screenshots()
        if detailed.description and len(detailed.description) > len(self.item.description or ""):
            self.item.description = detailed.description
            self.desc_lbl.set_markup(sanitize_description_to_pango(detailed.description))
        self._populate_context_bar()
        self._populate_variants_group()
        self._rebuild_action_buttons()
        return False

    @staticmethod
    def _clear_box(box: Gtk.Widget) -> None:
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _populate_screenshots(self) -> None:
        self._clear_box(self.screenshots_container)

        shots: List[str] = [s for s in self.item.screenshots if s]
        if not shots and self.item.preview_url and not self.item.preview_url.endswith((".svg", ".ico")):
            if self.item.category != ItemCategory.EXTENSIONS:
                shots = [self.item.preview_url]

        if not shots:
            self.screenshots_container.set_visible(False)
            return

        self.screenshots_container.set_visible(True)
        canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        canvas.add_css_class("detail-screenshot-box")

        shot_overlay = Gtk.Overlay()
        canvas.append(shot_overlay)

        self.shot_carousel = Adw.Carousel()
        self.shot_carousel.set_hexpand(True)
        self.shot_carousel.set_spacing(16)
        shot_overlay.set_child(self.shot_carousel)

        for url in shots[:6]:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.set_hexpand(True)
            btn.set_tooltip_text("Click to view full-size image")

            pic = Gtk.Picture()
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_size_request(-1, 290)
            pic.set_hexpand(True)
            btn.set_child(pic)

            state = {"path": None}

            def _on_loaded(path: Optional[str], p=pic, st=state):
                if path and os.path.exists(path):
                    st["path"] = path
                    p.set_filename(path)

            self.ocs.load_thumbnail_async(url, _on_loaded)
            btn.connect("clicked", lambda _b, st=state: self._open_lightbox(st["path"]))
            self.shot_carousel.append(btn)

        if len(shots) > 1:
            prev_s = Gtk.Button(icon_name="go-previous-symbolic")
            prev_s.add_css_class("circular")
            prev_s.add_css_class("osd")
            prev_s.set_valign(Gtk.Align.CENTER)
            prev_s.set_halign(Gtk.Align.START)
            prev_s.set_margin_start(8)
            prev_s.connect("clicked", self._on_prev_shot)
            shot_overlay.add_overlay(prev_s)

            next_s = Gtk.Button(icon_name="go-next-symbolic")
            next_s.add_css_class("circular")
            next_s.add_css_class("osd")
            next_s.set_valign(Gtk.Align.CENTER)
            next_s.set_halign(Gtk.Align.END)
            next_s.set_margin_end(8)
            next_s.connect("clicked", self._on_next_shot)
            shot_overlay.add_overlay(next_s)

            shot_dots = Adw.CarouselIndicatorDots()
            shot_dots.set_carousel(self.shot_carousel)
            canvas.append(shot_dots)

        self.screenshots_container.append(canvas)

    def _open_lightbox(self, file_path: Optional[str]) -> None:
        if not file_path or not os.path.exists(file_path):
            return
        dlg = Adw.Dialog()
        dlg.set_title(self.item.name)
        dlg.set_content_width(920)
        dlg.set_content_height(620)

        tb = Adw.ToolbarView()
        tb.add_top_bar(Adw.HeaderBar())
        pic = Gtk.Picture.new_for_filename(file_path)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic.set_margin_top(12)
        pic.set_margin_bottom(16)
        pic.set_margin_start(16)
        pic.set_margin_end(16)
        tb.set_content(pic)
        dlg.set_child(tb)
        dlg.present(self)

    def _populate_context_bar(self) -> None:
        self._clear_box(self.context_flow)

        dl_str = format_count(self.item.downloads) if self.item.downloads > 0 else "—"
        self.context_flow.append(self._make_context_cell(dl_str, "Downloads"))

        if self.item.category == ItemCategory.EXTENSIONS:
            if self.item.supported_shell_versions:
                hi = self.item.supported_shell_versions[-1]
                lo = self.item.supported_shell_versions[0]
                ver_range = f"GNOME {lo}–{hi}" if lo != hi else f"GNOME {hi}"
            else:
                ver_range = f"GNOME {self.shell_version}"
            self.context_flow.append(self._make_context_cell(ver_range, "Shell Support"))
        else:
            score_str = f"{self.item.rating:.0f}%" if self.item.rating > 0 else "Unrated"
            self.context_flow.append(self._make_context_cell(score_str, "Community Score"))

        if len(self.item.download_files) > 1:
            pkg_val = f"{len(self.item.download_files)} Files"
            pkg_lbl = "Available Variants"
        elif self.item.download_size:
            pkg_val = self.item.download_size
            pkg_lbl = "Download Size"
        elif self.item.version:
            pkg_val = f"v{self.item.version}" if not self.item.version.startswith("v") else self.item.version
            pkg_lbl = "Version"
        else:
            pkg_val = "Archive"
            pkg_lbl = "Format"
        self.context_flow.append(self._make_context_cell(pkg_val, pkg_lbl))

        self.context_flow.append(self._make_context_cell(self.item.source_label, "Source"))

    def _make_context_cell(self, value: str, label: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.add_css_class("context-bar")
        box.set_valign(Gtk.Align.CENTER)

        v = Gtk.Label(label=value)
        v.add_css_class("context-cell-value")
        v.set_halign(Gtk.Align.CENTER)
        v.set_ellipsize(3)
        box.append(v)

        l = Gtk.Label(label=label)
        l.add_css_class("context-cell-label")
        l.set_halign(Gtk.Align.CENTER)
        l.set_ellipsize(3)
        box.append(l)
        return box

    def _populate_variants_group(self) -> None:
        self._clear_box(self.variants_container)
        files = self.item.download_files
        if len(files) <= 1:
            self.variants_container.set_visible(False)
            return

        self.variants_container.set_visible(True)
        group = Adw.PreferencesGroup()
        group.set_title("Available Variants &amp; Packages")
        group.set_description(
            "This package provides multiple color schemes or variants. Install any variant below."
        )

        for df in files:
            row = Adw.ActionRow()
            row.set_title(GLib.markup_escape_text(df.name))
            if df.formatted_size:
                row.set_subtitle(df.formatted_size)
            row.set_icon_name("package-x-generic-symbolic")

            inst_btn = Gtk.Button(label="Install")
            inst_btn.add_css_class("pill")
            inst_btn.add_css_class("compact-btn")
            inst_btn.set_valign(Gtk.Align.CENTER)
            inst_btn.connect("clicked", lambda _b, f=df: self._install_specific_file(f))
            row.add_suffix(inst_btn)
            group.add(row)

        self.variants_container.append(group)

    def _on_open_webpage(self, _btn: Gtk.Button) -> None:
        if self.item.homepage_url:
            launcher = Gtk.UriLauncher.new(self.item.homepage_url)
            launcher.launch(self.get_root(), None, None)

    def _stop_pulse(self) -> None:
        if self._pulse_source_id is not None:
            GLib.source_remove(self._pulse_source_id)
            self._pulse_source_id = None

    def _start_pulse(self) -> None:
        if self._pulse_source_id is not None:
            return

        def _tick():
            if not self.progress_box.get_visible():
                self._pulse_source_id = None
                return False
            self.progress_bar.pulse()
            return True

        self._pulse_source_id = GLib.timeout_add(100, _tick)

    def _rebuild_action_buttons(self) -> None:
        self._clear_box(self.btn_row)

        active_task = TaskManager.get_default().get_task_for_item(self.item.item_id)
        if active_task:
            self.progress_box.set_visible(True)
            self.progress_bar.set_fraction(active_task.progress)
            self.progress_lbl.set_label(active_task.status_text)

            cancel_btn = Gtk.Button()
            cancel_btn.add_css_class("pill")
            cancel_btn.add_css_class("compact-btn")
            cancel_btn.set_child(Adw.ButtonContent(icon_name="process-stop-symbolic", label="Cancel"))
            cancel_btn.connect("clicked", lambda _b, t=active_task: t.cancel_event.set())
            self.btn_row.append(cancel_btn)
            return

        self._stop_pulse()
        self.progress_box.set_visible(False)

        if self.item.is_installed:
            apply_btn = Gtk.Button()
            apply_btn.add_css_class("pill")
            apply_btn.add_css_class("compact-btn")
            if self.item.is_active:
                apply_btn.set_child(Adw.ButtonContent(icon_name="emblem-ok-symbolic", label="Active"))
                apply_btn.set_sensitive(False)
            else:
                act_lbl = "Enable" if self.item.category == ItemCategory.EXTENSIONS else "Apply"
                apply_btn.set_child(Adw.ButtonContent(icon_name="object-select-symbolic", label=act_lbl))
                apply_btn.add_css_class("suggested-action")
                apply_btn.connect("clicked", self._on_apply_clicked)
            self.btn_row.append(apply_btn)

            remove_btn = Gtk.Button()
            remove_btn.add_css_class("pill")
            remove_btn.add_css_class("compact-btn")
            remove_btn.add_css_class("destructive-action")
            remove_btn.set_child(Adw.ButtonContent(icon_name="user-trash-symbolic", label="Remove"))
            remove_btn.connect("clicked", self._on_remove_clicked)
            self.btn_row.append(remove_btn)
        else:
            install_btn = Gtk.Button()
            install_btn.add_css_class("pill")
            install_btn.add_css_class("compact-btn")
            install_btn.add_css_class("suggested-action")
            lbl = "Install Default" if len(self.item.download_files) > 1 else "Install"
            install_btn.set_child(Adw.ButtonContent(icon_name="folder-download-symbolic", label=lbl))
            install_btn.set_sensitive(bool(self.item.download_files))
            install_btn.connect("clicked", lambda _b: self._install_specific_file(
                self.item.download_files[0] if self.item.download_files else None
            ))
            self.btn_row.append(install_btn)

    def _install_specific_file(self, selected_file: Optional[DownloadableFile]) -> None:
        def progress_cb(frac: float, msg: str):
            GLib.idle_add(self._update_progress, frac, msg)

        def done_cb(ok: bool, msg: str):
            GLib.idle_add(self._finish_install, ok, msg)

        self.on_install_requested(self.item, selected_file, progress_cb, done_cb)

    def _on_task_updated(self, task: TaskEntry) -> None:
        if str(task.item_id) != str(self.item.item_id):
            return

        status_changed = task.status != self._last_task_status
        self._last_task_status = task.status

        if task.status in ("downloading", "extracting", "installing"):
            self.progress_box.set_visible(True)
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
            self.progress_lbl.set_label(task.status_text)
            if status_changed:
                self._rebuild_action_buttons()
        elif task.status == "done":
            self._stop_pulse()
            self.item.is_installed = True
            if status_changed:
                self._rebuild_action_buttons()
        elif task.status in ("uninstalled", "failed", "cancelled"):
            self._stop_pulse()
            if task.status == "uninstalled":
                self.item.is_installed = False
                self.item.is_active = False
            if status_changed:
                self._rebuild_action_buttons()

    def _on_icon_loaded(self, path: Optional[str]) -> None:
        if path and os.path.exists(path):
            load_fixed_square_texture_async(path, 80, self._apply_icon_texture)

    def _apply_icon_texture(self, tex: Optional[Gdk.Texture]) -> None:
        if tex:
            self.icon_img.set_from_paintable(tex)
            self.icon_fallback.set_visible(False)
            self.icon_img.set_visible(True)

    def _on_prev_shot(self, _btn: Gtk.Button) -> None:
        n = self.shot_carousel.get_n_pages()
        if n > 1:
            pos = int(round(self.shot_carousel.get_position()))
            self.shot_carousel.scroll_to(self.shot_carousel.get_nth_page((pos - 1) % n), True)

    def _on_next_shot(self, _btn: Gtk.Button) -> None:
        n = self.shot_carousel.get_n_pages()
        if n > 1:
            pos = int(round(self.shot_carousel.get_position()))
            self.shot_carousel.scroll_to(self.shot_carousel.get_nth_page((pos + 1) % n), True)

    def _on_apply_clicked(self, _btn: Gtk.Button) -> None:
        self.on_apply_requested(self.item)
        self._rebuild_action_buttons()

    def _on_remove_clicked(self, _btn: Gtk.Button) -> None:
        if not self.on_remove_requested:
            return
        dlg = Adw.AlertDialog.new(
            f"Remove {self.item.name}?",
            "This will delete the user-installed files for this package from your computer.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def _on_resp(d, res):
            choice = d.choose_finish(res)
            if choice == "remove" and self.on_remove_requested:
                def done_cb(ok: bool, _msg: str):
                    if ok:
                        self.item.is_installed = False
                        self.item.is_active = False
                        self._rebuild_action_buttons()
                self.on_remove_requested(self.item, done_cb)

        dlg.choose(self, None, _on_resp)

    def _update_progress(self, frac: float, msg: str) -> bool:
        self.progress_box.set_visible(True)
        self.progress_bar.set_fraction(max(0.0, min(1.0, frac)))
        self.progress_lbl.set_label(msg)
        return False

    def _finish_install(self, ok: bool, _msg: str) -> bool:
        self._stop_pulse()
        if ok:
            self.item.is_installed = True
        self._rebuild_action_buttons()
        return False
