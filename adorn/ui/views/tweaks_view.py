"""Focused GNOME 51 Desktop Tweaks View — zero duplication with Explore/Installed or GNOME Settings."""

import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from adorn.core.gsettings import GSettingsManager


class TweaksView(Gtk.Box):
    """
    Curated GNOME 51 power-user tweaks organized into 4 clear sections:
    1. Windows & Titlebars
    2. Fonts & Rendering
    3. Input, Sound & Startup
    4. Styling & Wallpaper Tools

    Settings already built into GNOME 51 Settings (Dark Style, Accent Colors,
    Multitasking/Workspaces, Date & Time, Mouse/Touchpad, Power, Accessibility)
    or Adorn's Installed tab (Themes, Icons, Cursors, Extensions, Wallpapers)
    are intentionally omitted to eliminate clutter and confusion.
    """

    SECTIONS: List[Tuple[str, str, str]] = [
        ("windows", "Windows & Titlebars", "window-maximize-symbolic"),
        ("fonts", "Fonts & Rendering", "preferences-desktop-font-symbolic"),
        ("input_startup", "Input, Sound & Startup", "applications-utilities-symbolic"),
        ("advanced", "Styling & Wallpaper Tools", "preferences-color-symbolic"),
    ]

    TITLEBAR_CLICK_OPTIONS: List[Tuple[str, str]] = [
        ("Toggle Maximize", "toggle-maximize"),
        ("Toggle Maximize Horizontally", "toggle-maximize-horizontally"),
        ("Toggle Maximize Vertically", "toggle-maximize-vertically"),
        ("Minimize", "minimize"),
        ("Lower Behind Other Windows", "lower"),
        ("Window Menu", "menu"),
        ("Do Nothing", "none"),
    ]

    PLACEMENT_OPTIONS: List[Tuple[str, str]] = [
        ("Right (GNOME Default)", "right"),
        ("Left", "left"),
    ]

    ACTION_KEY_OPTIONS: List[Tuple[str, str]] = [
        ("Super (Windows Key)", "<Super>"),
        ("Alt", "<Alt>"),
        ("Disabled", "disabled"),
    ]

    FOCUS_OPTIONS: List[Tuple[str, str]] = [
        ("Click to Focus", "click"),
        ("Focus on Hover (Sloppy)", "sloppy"),
        ("Focus Follows Mouse (Strict)", "mouse"),
    ]

    ANTIALIAS_OPTIONS: List[Tuple[str, str]] = [
        ("Subpixel (LCD Displays)", "rgba"),
        ("Standard Grayscale (OLED & HiDPI)", "grayscale"),
        ("None", "none"),
    ]

    HINTING_OPTIONS: List[Tuple[str, str]] = [
        ("Slight (Recommended)", "slight"),
        ("Medium", "medium"),
        ("Full", "full"),
        ("None", "none"),
    ]

    CAPS_OPTIONS: List[Tuple[str, str]] = [
        ("Standard Caps Lock", "none"),
        ("Additional Escape Key", "caps:escape"),
        ("Swap Caps Lock and Escape", "caps:swapescape"),
        ("Additional Ctrl Key", "caps:ctrl_modifier"),
        ("Disabled", "caps:none"),
    ]

    WALLPAPER_FIT_OPTIONS: List[Tuple[str, str]] = [
        ("Zoom (Fill Screen)", "zoom"),
        ("Scaled (Fit Inside)", "scaled"),
        ("Centered", "centered"),
        ("Spanned (Across Monitors)", "spanned"),
        ("Stretched", "stretched"),
        ("Tiled", "wallpaper"),
    ]

    SLIDESHOW_INTERVALS: List[Tuple[str, int]] = [
        ("Every 15 min", 900),
        ("Every 30 min", 1800),
        ("Every 1 hour", 3600),
        ("Every 6 hours", 21600),
    ]

    def __init__(
        self,
        gsettings: GSettingsManager,
        show_toast_cb: Callable[[str], None],
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.gsettings = gsettings
        self.show_toast = show_toast_cb
        self._syncing: bool = False
        self._autostart_rows: List[Gtk.Widget] = []
        self._sound_themes: List[str] = []

        # Adaptive NavigationSplitView
        self.split_view = Adw.NavigationSplitView()
        self.split_view.set_vexpand(True)
        self.split_view.set_hexpand(True)
        self.split_view.set_min_sidebar_width(220)
        self.split_view.set_max_sidebar_width(265)
        self.append(self.split_view)

        # Sidebar Page
        sidebar_page = Adw.NavigationPage(title="Tweaks", tag="tweaks-sidebar")
        sidebar_scroller = Gtk.ScrolledWindow()
        sidebar_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.connect("row-selected", self._on_sidebar_selected)

        self._sidebar_rows: Dict[str, Gtk.ListBoxRow] = {}
        for sec_id, sec_title, sec_icon in self.SECTIONS:
            row = Gtk.ListBoxRow()
            row._sec_id = sec_id  # type: ignore[attr-defined]
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            hbox.set_margin_top(9)
            hbox.set_margin_bottom(9)
            hbox.set_margin_start(10)
            hbox.set_margin_end(10)

            ic = Gtk.Image.new_from_icon_name(sec_icon)
            ic.set_pixel_size(18)
            hbox.append(ic)

            lbl = Gtk.Label(label=sec_title)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_hexpand(True)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            hbox.append(lbl)

            row.set_child(hbox)
            self.sidebar_list.append(row)
            self._sidebar_rows[sec_id] = row

        sidebar_scroller.set_child(self.sidebar_list)
        sidebar_page.set_child(sidebar_scroller)
        self.split_view.set_sidebar(sidebar_page)

        # Content Page
        self.content_page = Adw.NavigationPage(
            title="Windows & Titlebars", tag="tweaks-content"
        )
        content_toolbar = Adw.ToolbarView()
        self.content_page.set_child(content_toolbar)

        # Sub-header bar shown when narrow/collapsed so user can return to sidebar
        self.collapsed_bar = Adw.HeaderBar()
        self.collapsed_bar.set_show_end_title_buttons(False)
        self.collapsed_bar.set_show_start_title_buttons(False)
        back_side_btn = Gtk.Button()
        back_side_btn.add_css_class("flat")
        back_side_btn.set_child(
            Adw.ButtonContent(icon_name="sidebar-show-symbolic", label="Categories")
        )
        back_side_btn.connect(
            "clicked", lambda _b: self.split_view.set_show_content(False)
        )
        self.collapsed_bar.pack_start(back_side_btn)
        self.collapsed_bar.set_visible(False)
        content_toolbar.add_top_bar(self.collapsed_bar)

        self.page_stack = Gtk.Stack()
        self.page_stack.set_vexpand(True)
        self.page_stack.set_hexpand(True)
        self.page_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.page_stack.set_transition_duration(140)
        content_toolbar.set_content(self.page_stack)

        self.page_stack.add_named(self._build_windows_page(), "windows")
        self.page_stack.add_named(self._build_fonts_page(), "fonts")
        self.page_stack.add_named(self._build_input_startup_page(), "input_startup")
        self.page_stack.add_named(self._build_advanced_page(), "advanced")

        self.split_view.set_content(self.content_page)

        first_row = self._sidebar_rows.get("windows")
        if first_row:
            self.sidebar_list.select_row(first_row)

        self.refresh()

    # =========================================================================
    # RESPONSIVE & NAVIGATION HELPERS
    # =========================================================================

    def adapt_columns(self, width: int) -> None:
        is_narrow = width < 720
        self.split_view.set_collapsed(is_narrow)
        self.collapsed_bar.set_visible(is_narrow)

    def _on_sidebar_selected(
        self, _listbox: Gtk.ListBox, row: Optional[Gtk.ListBoxRow]
    ) -> None:
        if not row:
            return
        sec_id = getattr(row, "_sec_id", "windows")
        self.page_stack.set_visible_child_name(sec_id)
        self.split_view.set_show_content(True)
        for sid, stitle, _ in self.SECTIONS:
            if sid == sec_id:
                self.content_page.set_title(stitle)
                break

    @staticmethod
    def _wrap_page(
        title: str,
        subtitle: str,
        header_suffix: Optional[Gtk.Widget] = None,
    ) -> Tuple[Gtk.ScrolledWindow, Gtk.Box]:
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)

        clamp = Adw.Clamp(maximum_size=820, tightening_threshold=560)
        scroller.set_child(clamp)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        col.set_margin_top(20)
        col.set_margin_bottom(36)
        col.set_margin_start(20)
        col.set_margin_end(20)
        clamp.set_child(col)

        hdr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hdr_row.set_valign(Gtk.Align.CENTER)

        hdr_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        hdr_text.set_hexpand(True)
        t_lbl = Gtk.Label(label=title)
        t_lbl.add_css_class("title-2")
        t_lbl.set_halign(Gtk.Align.START)
        hdr_text.append(t_lbl)

        s_lbl = Gtk.Label(label=subtitle)
        s_lbl.add_css_class("caption")
        s_lbl.add_css_class("dim-label")
        s_lbl.set_halign(Gtk.Align.START)
        s_lbl.set_wrap(True)
        hdr_text.append(s_lbl)

        hdr_row.append(hdr_text)
        if header_suffix is not None:
            hdr_row.append(header_suffix)

        col.append(hdr_row)
        return scroller, col

    @staticmethod
    def _make_combo_row(
        title: str,
        subtitle: str,
        options: Sequence[Tuple[str, str]],
        on_change: Callable[[str], None],
    ) -> Adw.ComboRow:
        row = Adw.ComboRow()
        row.set_use_markup(False)
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)
        model = Gtk.StringList.new([label for label, _ in options])
        row.set_model(model)

        def _changed(r: Adw.ComboRow, _p) -> None:
            idx = r.get_selected()
            if 0 <= idx < len(options):
                on_change(options[idx][1])

        row.connect("notify::selected", _changed)
        return row

    @staticmethod
    def _select_combo_value(
        row: Adw.ComboRow, options: Sequence[Tuple[str, str]], value: str
    ) -> None:
        for idx, (_lbl, val) in enumerate(options):
            if val == value:
                row.set_selected(idx)
                return
        row.set_selected(0)

    # =========================================================================
    # PAGE 1: WINDOWS & TITLEBARS
    # =========================================================================

    def _build_windows_page(self) -> Gtk.Widget:
        reset_btn = Gtk.Button(label="Reset Defaults")
        reset_btn.add_css_class("flat")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.connect("clicked", self._on_reset_window_tweaks)

        scroller, col = self._wrap_page(
            "Windows & Titlebars",
            "Customize window control buttons, titlebar click behavior, and window focus.",
            header_suffix=reset_btn,
        )

        # Group 1: Titlebar Buttons
        btn_grp = Adw.PreferencesGroup()
        btn_grp.set_title("Titlebar Buttons")
        btn_grp.set_description("Control which buttons appear alongside Close on window header bars.")

        self.win_max_sw = Adw.SwitchRow()
        self.win_max_sw.set_title("Maximize Button")
        self.win_max_sw.set_subtitle("Show a maximize/restore button in window titlebars")
        self.win_max_sw.connect("notify::active", lambda *_: self._apply_titlebar_buttons())
        btn_grp.add(self.win_max_sw)

        self.win_min_sw = Adw.SwitchRow()
        self.win_min_sw.set_title("Minimize Button")
        self.win_min_sw.set_subtitle("Show a minimize button in window titlebars")
        self.win_min_sw.connect("notify::active", lambda *_: self._apply_titlebar_buttons())
        btn_grp.add(self.win_min_sw)

        self.win_place_combo = self._make_combo_row(
            "Button Placement",
            "Side of the header bar where Close, Minimize, and Maximize sit",
            self.PLACEMENT_OPTIONS,
            lambda _v: self._apply_titlebar_buttons(),
        )
        btn_grp.add(self.win_place_combo)
        col.append(btn_grp)

        # Group 2: Titlebar Click Actions
        click_grp = Adw.PreferencesGroup()
        click_grp.set_title("Titlebar Click Actions")

        self.dbl_click_combo = self._make_combo_row(
            "Double-Click",
            "Action when double-clicking an empty area of a titlebar",
            self.TITLEBAR_CLICK_OPTIONS,
            self._on_dbl_click_changed,
        )
        click_grp.add(self.dbl_click_combo)

        self.mid_click_combo = self._make_combo_row(
            "Middle-Click",
            "Action when middle-clicking a titlebar",
            self.TITLEBAR_CLICK_OPTIONS,
            self._on_mid_click_changed,
        )
        click_grp.add(self.mid_click_combo)

        self.right_click_combo = self._make_combo_row(
            "Secondary-Click",
            "Action when right-clicking a titlebar",
            self.TITLEBAR_CLICK_OPTIONS,
            self._on_right_click_changed,
        )
        click_grp.add(self.right_click_combo)
        col.append(click_grp)

        # Group 3: Window Behavior & Focus
        beh_grp = Adw.PreferencesGroup()
        beh_grp.set_title("Window Behavior &amp; Focus")
        beh_grp.set_description(
            "Hot Corner, Edge Tiling, and Workspace options are managed in GNOME Settings → Multitasking."
        )

        self.center_win_sw = Adw.SwitchRow()
        self.center_win_sw.set_title("Center New Windows")
        self.center_win_sw.set_subtitle("Open newly launched windows in the center of the display")
        self.center_win_sw.connect("notify::active", self._on_center_windows_toggled)
        beh_grp.add(self.center_win_sw)

        self.attach_modal_sw = Adw.SwitchRow()
        self.attach_modal_sw.set_title("Attach Modal Dialogs")
        self.attach_modal_sw.set_subtitle(
            "Lock child dialogs to their parent window and move them together"
        )
        self.attach_modal_sw.connect("notify::active", self._on_attach_modal_toggled)
        beh_grp.add(self.attach_modal_sw)

        self.action_key_combo = self._make_combo_row(
            "Window Action Key",
            "Hold this modifier key to drag windows from anywhere inside them",
            self.ACTION_KEY_OPTIONS,
            self._on_action_key_changed,
        )
        beh_grp.add(self.action_key_combo)

        self.right_resize_sw = Adw.SwitchRow()
        self.right_resize_sw.set_title("Resize with Secondary-Click")
        self.right_resize_sw.set_subtitle(
            "Hold the Window Action Key and right-click drag to resize windows"
        )
        self.right_resize_sw.connect("notify::active", self._on_right_resize_toggled)
        beh_grp.add(self.right_resize_sw)

        self.focus_mode_combo = self._make_combo_row(
            "Window Focus Mode",
            "Choose whether windows focus on click or when the pointer enters them",
            self.FOCUS_OPTIONS,
            self._on_focus_mode_changed,
        )
        beh_grp.add(self.focus_mode_combo)

        self.auto_raise_sw = Adw.SwitchRow()
        self.auto_raise_sw.set_title("Raise Windows on Hover")
        self.auto_raise_sw.set_subtitle(
            "Automatically bring hovered windows to the front (applies to Hover/Mouse focus)"
        )
        self.auto_raise_sw.connect("notify::active", self._on_auto_raise_toggled)
        beh_grp.add(self.auto_raise_sw)

        col.append(beh_grp)
        return scroller

    def _apply_titlebar_buttons(self) -> None:
        if self._syncing:
            return
        idx = self.win_place_combo.get_selected()
        placement = (
            self.PLACEMENT_OPTIONS[idx][1]
            if 0 <= idx < len(self.PLACEMENT_OPTIONS)
            else "right"
        )
        self.gsettings.set_titlebar_buttons(
            maximize=self.win_max_sw.get_active(),
            minimize=self.win_min_sw.get_active(),
            placement=placement,
        )

    def _on_dbl_click_changed(self, val: str) -> None:
        if not self._syncing:
            self.gsettings.set_action_double_click_titlebar(val)

    def _on_mid_click_changed(self, val: str) -> None:
        if not self._syncing:
            self.gsettings.set_action_middle_click_titlebar(val)

    def _on_right_click_changed(self, val: str) -> None:
        if not self._syncing:
            self.gsettings.set_action_right_click_titlebar(val)

    def _on_center_windows_toggled(self, row: Adw.SwitchRow, _p) -> None:
        if not self._syncing:
            self.gsettings.set_center_new_windows(row.get_active())

    def _on_attach_modal_toggled(self, row: Adw.SwitchRow, _p) -> None:
        if not self._syncing:
            self.gsettings.set_attach_modal_dialogs(row.get_active())

    def _on_action_key_changed(self, val: str) -> None:
        if not self._syncing:
            self.gsettings.set_window_action_key("" if val == "disabled" else val)

    def _on_right_resize_toggled(self, row: Adw.SwitchRow, _p) -> None:
        if not self._syncing:
            self.gsettings.set_resize_with_right_button(row.get_active())

    def _on_focus_mode_changed(self, val: str) -> None:
        self.auto_raise_sw.set_sensitive(val != "click")
        if not self._syncing:
            self.gsettings.set_focus_mode(val)

    def _on_auto_raise_toggled(self, row: Adw.SwitchRow, _p) -> None:
        if not self._syncing:
            self.gsettings.set_auto_raise(row.get_active())

    def _on_reset_window_tweaks(self, _btn: Gtk.Button) -> None:
        if self.gsettings.wm:
            for k in (
                "button-layout",
                "action-double-click-titlebar",
                "action-middle-click-titlebar",
                "action-right-click-titlebar",
                "focus-mode",
                "auto-raise",
                "mouse-button-modifier",
                "resize-with-right-button",
            ):
                try:
                    self.gsettings.wm.reset(k)
                except Exception:
                    pass
        if self.gsettings.mutter:
            for k in ("center-new-windows", "attach-modal-dialogs"):
                try:
                    self.gsettings.mutter.reset(k)
                except Exception:
                    pass
        self.refresh()
        self.show_toast("Reset window and titlebar settings to defaults")

    # =========================================================================
    # PAGE 2: FONTS & RENDERING
    # =========================================================================

    def _build_fonts_page(self) -> Gtk.Widget:
        reset_btn = Gtk.Button(label="Reset Defaults")
        reset_btn.add_css_class("flat")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.connect("clicked", self._on_reset_fonts)

        scroller, col = self._wrap_page(
            "Fonts & Rendering",
            "Choose system typefaces, fine-tune text scaling, and adjust font smoothing.",
            header_suffix=reset_btn,
        )

        # Group 1: System Typefaces
        font_grp = Adw.PreferencesGroup()
        font_grp.set_title("System Fonts")

        self.ui_font_btn = self._add_font_row(
            font_grp,
            "Interface Text",
            "Menus, buttons, header bars, and desktop shell labels",
            self.gsettings.set_interface_font,
        )
        self.doc_font_btn = self._add_font_row(
            font_grp,
            "Document Text",
            "Default typeface used for reading documents and articles",
            self.gsettings.set_document_font,
        )
        self.mono_font_btn = self._add_font_row(
            font_grp,
            "Monospace Text",
            "Fixed-width font for terminals, code editors, and logs",
            self.gsettings.set_monospace_font,
        )
        self.title_font_btn = self._add_font_row(
            font_grp,
            "Legacy Window Titles",
            "Used by older X11 / server-decorated window frames",
            self.gsettings.set_titlebar_font,
        )
        col.append(font_grp)

        # Group 2: Text Scaling & Smoothing
        render_grp = Adw.PreferencesGroup()
        render_grp.set_title("Scaling &amp; Subpixel Smoothing")

        self.scale_spin = Adw.SpinRow.new_with_range(0.75, 2.00, 0.05)
        self.scale_spin.set_digits(2)
        self.scale_spin.set_title("Text Scaling Factor")
        self.scale_spin.set_subtitle(
            "Proportional multiplier across all text (1.00 = 100% standard size)"
        )
        self.scale_spin.connect("notify::value", self._on_font_scale_changed)
        render_grp.add(self.scale_spin)

        self.antialias_combo = self._make_combo_row(
            "Antialiasing",
            "Subpixel smoothing for standard LCDs, or Grayscale for OLED & HiDPI panels",
            self.ANTIALIAS_OPTIONS,
            self._on_antialias_changed,
        )
        render_grp.add(self.antialias_combo)

        self.hinting_combo = self._make_combo_row(
            "Hinting",
            "Controls how tightly glyph outlines snap to the pixel grid",
            self.HINTING_OPTIONS,
            self._on_hinting_changed,
        )
        render_grp.add(self.hinting_combo)

        col.append(render_grp)
        return scroller

    def _add_font_row(
        self,
        group: Adw.PreferencesGroup,
        title: str,
        subtitle: str,
        setter: Callable[[str], bool],
    ) -> Gtk.FontDialogButton:
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)

        dialog = Gtk.FontDialog(title=f"Select {title} Font")
        btn = Gtk.FontDialogButton(dialog=dialog)
        btn.set_valign(Gtk.Align.CENTER)

        def _on_font_desc_changed(b: Gtk.FontDialogButton, _p) -> None:
            if self._syncing:
                return
            desc = b.get_font_desc()
            if desc:
                setter(desc.to_string())

        btn.connect("notify::font-desc", _on_font_desc_changed)
        row.add_suffix(btn)
        group.add(row)
        return btn

    def _on_font_scale_changed(self, row: Adw.SpinRow, _p) -> None:
        if not self._syncing:
            self.gsettings.set_font_scaling(round(row.get_value(), 2))

    def _on_antialias_changed(self, val: str) -> None:
        if not self._syncing:
            self.gsettings.set_font_antialiasing(val)

    def _on_hinting_changed(self, val: str) -> None:
        if not self._syncing:
            self.gsettings.set_font_hinting(val)

    def _on_reset_fonts(self, _btn: Gtk.Button) -> None:
        self.gsettings.reset_fonts_to_default()
        self.refresh()
        self.show_toast("Reset fonts and text scaling to Adwaita defaults")

    # =========================================================================
    # PAGE 3: INPUT, SOUND & STARTUP
    # =========================================================================

    def _build_input_startup_page(self) -> Gtk.Widget:
        scroller, col = self._wrap_page(
            "Input, Sound & Startup",
            "Extra keyboard/mouse behaviors, system sound themes, and login startup apps.",
        )

        # Group 1: Keyboard & Mouse Extras
        in_grp = Adw.PreferencesGroup()
        in_grp.set_title("Keyboard &amp; Mouse Extras")
        in_grp.set_description(
            "Pointer speed, touchpad gestures, and Compose key live in GNOME Settings → Mouse &amp; Keyboard."
        )

        self.mid_paste_sw = Adw.SwitchRow()
        self.mid_paste_sw.set_title("Middle-Click Paste")
        self.mid_paste_sw.set_subtitle(
            "Paste the currently highlighted text selection when middle-clicking"
        )
        self.mid_paste_sw.connect("notify::active", self._on_mid_paste_toggled)
        in_grp.add(self.mid_paste_sw)

        self.caps_combo = self._make_combo_row(
            "Caps Lock Behavior",
            "Remap Caps Lock using XKB input options without external daemons",
            self.CAPS_OPTIONS,
            self._on_caps_changed,
        )
        in_grp.add(self.caps_combo)
        col.append(in_grp)

        # Group 2: Sound Theme
        snd_grp = Adw.PreferencesGroup()
        snd_grp.set_title("Sound Theme")

        self.sound_theme_row = Adw.ComboRow()
        self.sound_theme_row.set_title("System Sound Theme")
        self.sound_theme_row.set_subtitle(
            "Audio pack from /usr/share/sounds or ~/.local/share/sounds"
        )
        self.sound_theme_row.connect("notify::selected", self._on_sound_theme_selected)
        snd_grp.add(self.sound_theme_row)

        self.input_snd_sw = Adw.SwitchRow()
        self.input_snd_sw.set_title("Input Feedback Sounds")
        self.input_snd_sw.set_subtitle("Play subtle sound effects on UI interactions")
        self.input_snd_sw.connect("notify::active", self._on_input_snd_toggled)
        snd_grp.add(self.input_snd_sw)
        col.append(snd_grp)

        # Group 3: Startup Applications
        self.startup_grp = Adw.PreferencesGroup()
        self.startup_grp.set_title("Startup Applications")
        self.startup_grp.set_description(
            "Applications launched automatically when you sign in (~/.config/autostart)."
        )

        add_app_btn = Gtk.Button()
        add_app_btn.add_css_class("flat")
        add_app_btn.set_valign(Gtk.Align.CENTER)
        add_app_btn.set_child(
            Adw.ButtonContent(icon_name="list-add-symbolic", label="Add App…")
        )
        add_app_btn.connect("clicked", self._open_add_startup_dialog)
        self.startup_grp.set_header_suffix(add_app_btn)

        col.append(self.startup_grp)
        return scroller

    def _on_mid_paste_toggled(self, row: Adw.SwitchRow, _p) -> None:
        if not self._syncing:
            self.gsettings.set_middle_click_paste(row.get_active())

    def _on_caps_changed(self, val: str) -> None:
        if not self._syncing:
            self.gsettings.set_caps_lock_behavior(val)

    def _on_sound_theme_selected(self, row: Adw.ComboRow, _p) -> None:
        if self._syncing:
            return
        idx = row.get_selected()
        if 0 <= idx < len(self._sound_themes):
            self.gsettings.set_sound_theme(self._sound_themes[idx])

    def _on_input_snd_toggled(self, row: Adw.SwitchRow, _p) -> None:
        if not self._syncing:
            self.gsettings.set_input_feedback_sounds(row.get_active())

    def _rebuild_startup_list(self) -> None:
        for r in self._autostart_rows:
            self.startup_grp.remove(r)
        self._autostart_rows.clear()

        apps = self.gsettings.list_autostart_apps()
        if not apps:
            empty_row = Adw.ActionRow()
            empty_row.set_title("No Startup Applications Configured")
            empty_row.set_subtitle("Click “Add App…” above to launch an application at login.")
            empty_row.set_icon_name("system-run-symbolic")
            self.startup_grp.add(empty_row)
            self._autostart_rows.append(empty_row)
            return

        for app in apps:
            row = Adw.ActionRow()
            row.set_title(GLib.markup_escape_text(app["name"]))
            sub = app["comment"] or app["exec"] or app["file_name"]
            row.set_subtitle(GLib.markup_escape_text(sub))
            row.set_icon_name(app.get("icon") or "application-x-executable-symbolic")

            rm_btn = Gtk.Button(icon_name="user-trash-symbolic")
            rm_btn.add_css_class("circular")
            rm_btn.add_css_class("flat")
            rm_btn.set_valign(Gtk.Align.CENTER)
            rm_btn.set_tooltip_text(f"Remove {app['name']} from startup")
            rm_btn.connect(
                "clicked",
                lambda _b, fn=app["file_name"], nm=app["name"]: self._on_remove_startup(fn, nm),
            )
            row.add_suffix(rm_btn)

            sw = Gtk.Switch()
            sw.set_valign(Gtk.Align.CENTER)
            sw.set_active(bool(app.get("enabled", True)))
            sw.set_tooltip_text("Enable or pause at login")
            sw.connect(
                "state-set",
                lambda _s, state, fn=app["file_name"]: self._on_toggle_startup(fn, state),
            )
            row.add_suffix(sw)

            self.startup_grp.add(row)
            self._autostart_rows.append(row)

    def _on_toggle_startup(self, file_name: str, state: bool) -> bool:
        self.gsettings.set_autostart_app_enabled(file_name, state)
        return False

    def _on_remove_startup(self, file_name: str, app_name: str) -> None:
        if self.gsettings.remove_autostart_app(file_name):
            self.show_toast(f"Removed {app_name} from startup")
            self._rebuild_startup_list()

    def _open_add_startup_dialog(self, _btn: Gtk.Button) -> None:
        dlg = Adw.Dialog()
        dlg.set_title("Add Startup Application")
        dlg.set_content_width(480)
        dlg.set_content_height(540)

        tview = Adw.ToolbarView()
        hbar = Adw.HeaderBar()
        tview.add_top_bar(hbar)

        search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        search_box.set_margin_start(14)
        search_box.set_margin_end(14)
        search_box.set_margin_top(6)
        search_box.set_margin_bottom(8)
        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text("Search installed applications…")
        search_box.append(search_entry)
        tview.add_top_bar(search_box)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        listbox.set_margin_start(14)
        listbox.set_margin_end(14)
        listbox.set_margin_top(6)
        listbox.set_margin_bottom(16)
        scroller.set_child(listbox)
        tview.set_content(scroller)

        existing_files = {a["file_name"] for a in self.gsettings.list_autostart_apps()}
        available = [
            a
            for a in self.gsettings.list_available_desktop_apps()
            if a["file_name"] not in existing_files
        ]

        row_items: List[Tuple[Adw.ActionRow, str]] = []
        for app in available:
            arow = Adw.ActionRow()
            arow.set_title(GLib.markup_escape_text(app["name"]))
            if app["comment"]:
                arow.set_subtitle(GLib.markup_escape_text(app["comment"]))
            arow.set_icon_name(app.get("icon") or "application-x-executable-symbolic")

            add_b = Gtk.Button(label="Add")
            add_b.add_css_class("pill")
            add_b.set_valign(Gtk.Align.CENTER)

            def _pick(_b: Gtk.Button, p=app["path"], nm=app["name"]) -> None:
                if self.gsettings.add_autostart_app(p):
                    self.show_toast(f"Added {nm} to startup applications")
                    self._rebuild_startup_list()
                dlg.close()

            add_b.connect("clicked", _pick)
            arow.add_suffix(add_b)
            listbox.append(arow)
            haystack = f"{app['name']} {app['comment']} {app['exec']}".lower()
            row_items.append((arow, haystack))

        def _filter_changed(entry: Gtk.SearchEntry) -> None:
            q = entry.get_text().strip().lower()
            for r_widget, hay in row_items:
                r_widget.set_visible(not q or q in hay)

        search_entry.connect("search-changed", _filter_changed)
        dlg.set_child(tview)
        dlg.present(self.get_root())

    # =========================================================================
    # PAGE 4: STYLING & WALLPAPER TOOLS
    # =========================================================================

    def _build_advanced_page(self) -> Gtk.Widget:
        scroller, col = self._wrap_page(
            "Styling & Wallpaper Tools",
            "Optional GTK window CSS rules, lock screen background, color palette extraction, and slideshows.",
        )

        # Group 1: GTK Window CSS Overrides
        css_grp = Adw.PreferencesGroup()
        css_grp.set_title("GTK Window CSS Overrides")
        css_grp.set_description(
            "Writes marked CSS blocks to ~/.config/gtk-4.0/gtk.css and gtk-3.0/gtk.css. Newly opened apps pick up changes."
        )

        self.css_enable_sw = Adw.SwitchRow()
        self.css_enable_sw.set_title("Enable Custom Window CSS")
        self.css_enable_sw.set_subtitle(
            "Apply custom window corner rounding and unfocused backdrop opacity"
        )
        self.css_enable_sw.connect("notify::active", self._on_css_toggled)
        css_grp.add(self.css_enable_sw)

        self.css_radius_spin = Adw.SpinRow.new_with_range(0, 28, 1)
        self.css_radius_spin.set_digits(0)
        self.css_radius_spin.set_title("Window Corner Radius (px)")
        self.css_radius_spin.set_subtitle("Adwaita default is 12 px (0 px = sharp corners)")
        self.css_radius_spin.connect("notify::value", lambda *_: self._write_custom_css_if_active())
        css_grp.add(self.css_radius_spin)

        self.css_opacity_spin = Adw.SpinRow.new_with_range(0.65, 1.00, 0.05)
        self.css_opacity_spin.set_digits(2)
        self.css_opacity_spin.set_title("Unfocused Window Opacity")
        self.css_opacity_spin.set_subtitle("1.00 keeps inactive windows fully opaque")
        self.css_opacity_spin.connect("notify::value", lambda *_: self._write_custom_css_if_active())
        css_grp.add(self.css_opacity_spin)
        col.append(css_grp)

        # Group 2: Wallpaper & Lock Screen Tools
        wall_grp = Adw.PreferencesGroup()
        wall_grp.set_title("Wallpaper &amp; Lock Screen Power Tools")

        self.wall_fit_combo = self._make_combo_row(
            "Wallpaper Fit Mode",
            "How the desktop background scales across your display",
            self.WALLPAPER_FIT_OPTIONS,
            self._on_wall_fit_changed,
        )
        wall_grp.add(self.wall_fit_combo)

        # Lock screen background row
        self.lockscreen_row = Adw.ActionRow()
        self.lockscreen_row.set_title("Lock Screen Image")
        self.lockscreen_row.set_subtitle("Follows desktop wallpaper")

        self.lock_reset_btn = Gtk.Button(label="Reset")
        self.lock_reset_btn.add_css_class("flat")
        self.lock_reset_btn.set_valign(Gtk.Align.CENTER)
        self.lock_reset_btn.connect("clicked", self._on_reset_lockscreen)
        self.lockscreen_row.add_suffix(self.lock_reset_btn)

        lock_pick_btn = Gtk.Button(label="Choose Image…")
        lock_pick_btn.add_css_class("pill")
        lock_pick_btn.set_valign(Gtk.Align.CENTER)
        lock_pick_btn.connect("clicked", self._on_pick_lockscreen)
        self.lockscreen_row.add_suffix(lock_pick_btn)
        wall_grp.add(self.lockscreen_row)

        # Palette Extractor Row
        palette_row = Adw.ActionRow()
        palette_row.set_title("Wallpaper Color Palette")
        palette_row.set_subtitle("Extract dominant hex colors from your active wallpaper")

        self.palette_swatches = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.palette_swatches.set_valign(Gtk.Align.CENTER)
        palette_row.add_suffix(self.palette_swatches)

        sample_btn = Gtk.Button(label="Sample Colors")
        sample_btn.add_css_class("pill")
        sample_btn.set_valign(Gtk.Align.CENTER)
        sample_btn.connect("clicked", self._on_sample_palette)
        palette_row.add_suffix(sample_btn)
        wall_grp.add(palette_row)

        # Folder Slideshow Builder Row
        slide_row = Adw.ActionRow()
        slide_row.set_title("Folder Wallpaper Slideshow")
        slide_row.set_subtitle(
            "Generate and apply a dynamic GNOME XML slideshow from any folder of images"
        )

        self.slide_interval_dd = Gtk.DropDown.new_from_strings(
            [lbl for lbl, _sec in self.SLIDESHOW_INTERVALS]
        )
        self.slide_interval_dd.set_selected(1)  # 30 min default
        self.slide_interval_dd.set_valign(Gtk.Align.CENTER)
        slide_row.add_suffix(self.slide_interval_dd)

        slide_btn = Gtk.Button(label="Select Folder…")
        slide_btn.add_css_class("pill")
        slide_btn.set_valign(Gtk.Align.CENTER)
        slide_btn.connect("clicked", self._on_pick_slideshow_folder)
        slide_row.add_suffix(slide_btn)
        wall_grp.add(slide_row)

        col.append(wall_grp)

        # Group 3: GNOME Settings Bridge (clarifies where native GNOME 51 settings live)
        sys_grp = Adw.PreferencesGroup()
        sys_grp.set_title("System Settings")

        sys_row = Adw.ActionRow()
        sys_row.set_title("Looking for Dark Style, Accent Colors, or Multitasking?")
        sys_row.set_subtitle(
            "Appearance, workspaces, clock format, power, and touchpad settings are built directly into GNOME Settings."
        )
        sys_row.set_icon_name("preferences-system-symbolic")
        sys_row.set_activatable(True)

        open_cc_btn = Gtk.Button()
        open_cc_btn.add_css_class("pill")
        open_cc_btn.set_valign(Gtk.Align.CENTER)
        open_cc_btn.set_child(
            Adw.ButtonContent(
                icon_name="external-link-symbolic", label="Open GNOME Settings"
            )
        )
        open_cc_btn.connect("clicked", lambda _b: self._launch_gnome_settings())
        sys_row.connect("activated", lambda _r: self._launch_gnome_settings())
        sys_row.add_suffix(open_cc_btn)
        sys_grp.add(sys_row)

        col.append(sys_grp)

        # Group 4: Support & About Adorn
        support_grp = Adw.PreferencesGroup()
        support_grp.set_title("Project &amp; Support")

        patreon_row = Adw.ActionRow()
        patreon_row.set_title("Donate on Patreon")
        patreon_row.set_subtitle("Support Grinch_ and ongoing development: patreon.com/c/grinch_")
        patreon_row.set_icon_name("starred-symbolic")
        patreon_row.set_activatable(True)

        patreon_btn = Gtk.Button()
        patreon_btn.add_css_class("pill")
        patreon_btn.add_css_class("suggested-action")
        patreon_btn.set_valign(Gtk.Align.CENTER)
        patreon_btn.set_child(
            Adw.ButtonContent(
                icon_name="external-link-symbolic", label="Patreon"
            )
        )

        def _open_patreon():
            root = self.get_root()
            Gtk.show_uri(root if isinstance(root, Gtk.Window) else None, "https://www.patreon.com/c/grinch_", Gdk.CURRENT_TIME)

        patreon_btn.connect("clicked", lambda _b: _open_patreon())
        patreon_row.connect("activated", lambda _r: _open_patreon())
        patreon_row.add_suffix(patreon_btn)
        support_grp.add(patreon_row)

        report_row = Adw.ActionRow()
        report_row.set_title("Report an Issue")
        report_row.set_subtitle("Send bug reports or feature requests directly to developer")
        report_row.set_icon_name("mail-send-symbolic")
        report_row.set_activatable(True)

        report_btn = Gtk.Button()
        report_btn.add_css_class("pill")
        report_btn.set_valign(Gtk.Align.CENTER)
        report_btn.set_child(
            Adw.ButtonContent(
                icon_name="mail-send-symbolic", label="Report"
            )
        )
        report_btn.connect("clicked", lambda _b: self.activate_action("win.report_issue", None))
        report_row.connect("activated", lambda _r: self.activate_action("win.report_issue", None))
        report_row.add_suffix(report_btn)
        support_grp.add(report_row)

        about_row = Adw.ActionRow()
        about_row.set_title("About Adorn")
        about_row.set_subtitle("Version 1.0 • Created by Grinch_")
        about_row.set_icon_name("help-about-symbolic")
        about_row.set_activatable(True)

        about_btn = Gtk.Button()
        about_btn.add_css_class("pill")
        about_btn.add_css_class("compact-btn")
        about_btn.set_valign(Gtk.Align.CENTER)
        about_btn.set_child(
            Adw.ButtonContent(
                icon_name="go-next-symbolic", label="About"
            )
        )
        about_btn.connect("clicked", lambda _b: self.activate_action("win.about", None))
        about_row.connect("activated", lambda _r: self.activate_action("win.about", None))
        about_row.add_suffix(about_btn)
        support_grp.add(about_row)

        col.append(support_grp)
        return scroller

    def _on_css_toggled(self, row: Adw.SwitchRow, _p) -> None:
        enabled = row.get_active()
        self.css_radius_spin.set_sensitive(enabled)
        self.css_opacity_spin.set_sensitive(enabled)
        if self._syncing:
            return
        if enabled:
            self._write_custom_css_if_active()
            self.show_toast("Enabled custom window CSS overrides")
        else:
            self.gsettings.clear_gtk_custom_css()
            self.show_toast("Cleared custom window CSS overrides")

    def _write_custom_css_if_active(self) -> None:
        if self._syncing or not self.css_enable_sw.get_active():
            return
        radius = int(round(self.css_radius_spin.get_value()))
        opacity = round(self.css_opacity_spin.get_value(), 2)
        self.gsettings.set_gtk_custom_css(radius, opacity)

    def _on_wall_fit_changed(self, val: str) -> None:
        if not self._syncing:
            self.gsettings.set_wallpaper_options(val)

    def _update_lockscreen_subtitle(self) -> None:
        uri = self.gsettings.get_lockscreen_uri()
        wall_uri = self.gsettings.get_wallpaper_uri()
        if uri and uri != wall_uri:
            name = Path(uri.replace("file://", "")).name
            self.lockscreen_row.set_subtitle(GLib.markup_escape_text(f"Custom: {name}"))
            self.lock_reset_btn.set_visible(True)
        else:
            self.lockscreen_row.set_subtitle("Follows desktop wallpaper")
            self.lock_reset_btn.set_visible(False)

    def _on_pick_lockscreen(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Choose Lock Screen Image")
        img_filter = Gtk.FileFilter()
        img_filter.set_name("Images (PNG, JPEG, WebP)")
        img_filter.add_mime_type("image/png")
        img_filter.add_mime_type("image/jpeg")
        img_filter.add_mime_type("image/webp")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(img_filter)
        dialog.set_filters(filters)

        def _done(dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                gfile = dlg.open_finish(res)
                if gfile and gfile.get_path():
                    if self.gsettings.set_lockscreen_uri(gfile.get_path()):
                        self._update_lockscreen_subtitle()
                        self.show_toast("Updated lock screen wallpaper")
            except GLib.Error:
                pass

        dialog.open(self.get_root(), None, _done)

    def _on_reset_lockscreen(self, _btn: Gtk.Button) -> None:
        self.gsettings.reset_lockscreen_uri()
        self._update_lockscreen_subtitle()
        self.show_toast("Reset lock screen image to follow desktop wallpaper")

    def _on_sample_palette(self, _btn: Gtk.Button) -> None:
        child = self.palette_swatches.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.palette_swatches.remove(child)
            child = nxt

        colors = self.gsettings.extract_wallpaper_palette()
        if not colors:
            self.show_toast("Could not sample colors from current wallpaper")
            return

        for _label, hex_code in colors[:5]:
            chip = Gtk.Button(label=hex_code.upper())
            chip.add_css_class("flat")
            chip.add_css_class("numeric")
            chip.set_tooltip_text(f"Click to copy {hex_code.upper()}")

            prov = Gtk.CssProvider()
            prov.load_from_string(
                f"button {{ background-color: {hex_code}; color: #ffffff; "
                f"text-shadow: 0 1px 2px rgba(0,0,0,0.65); border-radius: 999px; "
                f"padding: 3px 10px; font-size: 0.76rem; font-weight: 700; }}"
            )
            chip.get_style_context().add_provider(
                prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            chip.connect("clicked", lambda _b, hc=hex_code.upper(): self._copy_hex(hc))
            self.palette_swatches.append(chip)

        self.show_toast("Click any color chip to copy its hex code")

    def _copy_hex(self, hex_code: str) -> None:
        disp = Gdk.Display.get_default()
        if disp:
            disp.get_clipboard().set(hex_code)
            self.show_toast(f"Copied {hex_code} to clipboard")

    def _on_pick_slideshow_folder(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Select Folder with Wallpaper Images")

        def _done(dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
            try:
                gfile = dlg.select_folder_finish(res)
                if gfile and gfile.get_path():
                    idx = self.slide_interval_dd.get_selected()
                    duration = (
                        self.SLIDESHOW_INTERVALS[idx][1]
                        if 0 <= idx < len(self.SLIDESHOW_INTERVALS)
                        else 1800
                    )
                    xml_path = self.gsettings.generate_wallpaper_slideshow(
                        gfile.get_path(), duration=duration
                    )
                    if xml_path:
                        self.show_toast(
                            f"Created & applied slideshow from {Path(gfile.get_path()).name}"
                        )
                    else:
                        self.show_toast(
                            "Folder needs at least 2 images (JPG, PNG, or WebP)"
                        )
            except GLib.Error:
                pass

        dialog.select_folder(self.get_root(), None, _done)

    def _launch_gnome_settings(self) -> None:
        try:
            subprocess.Popen(["gnome-control-center"])
        except Exception:
            self.show_toast("Could not launch gnome-control-center")

    # =========================================================================
    # STATE SYNCHRONIZATION
    # =========================================================================

    def refresh(self) -> None:
        """Reads current system GSettings, startup apps, and CSS state into UI controls."""
        self._syncing = True
        try:
            # 1. Windows & Titlebars
            self.win_max_sw.set_active(self.gsettings.get_has_maximize_button())
            self.win_min_sw.set_active(self.gsettings.get_has_minimize_button())
            self._select_combo_value(
                self.win_place_combo,
                self.PLACEMENT_OPTIONS,
                self.gsettings.get_button_placement(),
            )
            self._select_combo_value(
                self.dbl_click_combo,
                self.TITLEBAR_CLICK_OPTIONS,
                self.gsettings.get_action_double_click_titlebar(),
            )
            self._select_combo_value(
                self.mid_click_combo,
                self.TITLEBAR_CLICK_OPTIONS,
                self.gsettings.get_action_middle_click_titlebar(),
            )
            self._select_combo_value(
                self.right_click_combo,
                self.TITLEBAR_CLICK_OPTIONS,
                self.gsettings.get_action_right_click_titlebar(),
            )
            self.center_win_sw.set_active(self.gsettings.get_center_new_windows())
            self.attach_modal_sw.set_active(self.gsettings.get_attach_modal_dialogs())

            raw_mod = self.gsettings.get_window_action_key() or "disabled"
            self._select_combo_value(
                self.action_key_combo, self.ACTION_KEY_OPTIONS, raw_mod
            )
            self.right_resize_sw.set_active(self.gsettings.get_resize_with_right_button())

            fmode = self.gsettings.get_focus_mode()
            self._select_combo_value(self.focus_mode_combo, self.FOCUS_OPTIONS, fmode)
            self.auto_raise_sw.set_active(self.gsettings.get_auto_raise())
            self.auto_raise_sw.set_sensitive(fmode != "click")

            # 2. Fonts & Rendering
            self.ui_font_btn.set_font_desc(
                Pango.FontDescription.from_string(self.gsettings.get_interface_font())
            )
            self.doc_font_btn.set_font_desc(
                Pango.FontDescription.from_string(self.gsettings.get_document_font())
            )
            self.mono_font_btn.set_font_desc(
                Pango.FontDescription.from_string(self.gsettings.get_monospace_font())
            )
            self.title_font_btn.set_font_desc(
                Pango.FontDescription.from_string(self.gsettings.get_titlebar_font())
            )
            self.scale_spin.set_value(self.gsettings.get_font_scaling())
            self._select_combo_value(
                self.antialias_combo,
                self.ANTIALIAS_OPTIONS,
                self.gsettings.get_font_antialiasing(),
            )
            self._select_combo_value(
                self.hinting_combo,
                self.HINTING_OPTIONS,
                self.gsettings.get_font_hinting(),
            )

            # 3. Input, Sound & Startup
            self.mid_paste_sw.set_active(self.gsettings.get_middle_click_paste())
            self._select_combo_value(
                self.caps_combo,
                self.CAPS_OPTIONS,
                self.gsettings.get_caps_lock_behavior(),
            )

            self._sound_themes = self.gsettings.list_installed_sound_themes()
            cur_snd = self.gsettings.get_sound_theme()
            if cur_snd and cur_snd not in self._sound_themes:
                self._sound_themes.insert(0, cur_snd)
            self.sound_theme_row.set_model(Gtk.StringList.new(self._sound_themes))
            if cur_snd in self._sound_themes:
                self.sound_theme_row.set_selected(self._sound_themes.index(cur_snd))

            self.input_snd_sw.set_active(self.gsettings.get_input_feedback_sounds())
            self._rebuild_startup_list()

            # 4. Styling & Wallpaper Tools
            linked_gtk4 = self.gsettings.get_linked_gtk4_theme()
            css_enabled, radius, opacity = self.gsettings.get_gtk_custom_css_state()
            if linked_gtk4:
                self.css_enable_sw.set_active(False)
                self.css_enable_sw.set_sensitive(False)
                self.css_enable_sw.set_subtitle(
                    f"Unavailable while GTK4 theme “{linked_gtk4}” is linked in Installed → GTK Themes"
                )
                self.css_radius_spin.set_sensitive(False)
                self.css_opacity_spin.set_sensitive(False)
            else:
                self.css_enable_sw.set_sensitive(True)
                self.css_enable_sw.set_subtitle(
                    "Apply custom window corner rounding and unfocused backdrop opacity"
                )
                self.css_enable_sw.set_active(css_enabled)
                self.css_radius_spin.set_value(radius)
                self.css_opacity_spin.set_value(opacity)
                self.css_radius_spin.set_sensitive(css_enabled)
                self.css_opacity_spin.set_sensitive(css_enabled)

            self._select_combo_value(
                self.wall_fit_combo,
                self.WALLPAPER_FIT_OPTIONS,
                self.gsettings.get_wallpaper_options(),
            )
            self._update_lockscreen_subtitle()
        finally:
            self._syncing = False
