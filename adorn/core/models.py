"""Data models and type definitions for Adorn."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class ItemCategory(str, Enum):
    EXTENSIONS = "extensions"
    GTK_THEMES = "gtk_themes"
    SHELL_THEMES = "shell_themes"
    ICONS = "icons"
    CURSORS = "cursors"
    WALLPAPERS = "wallpapers"

    @property
    def display_name(self) -> str:
        names = {
            ItemCategory.EXTENSIONS: "GNOME Extensions",
            ItemCategory.GTK_THEMES: "GTK Themes",
            ItemCategory.SHELL_THEMES: "Shell Themes",
            ItemCategory.ICONS: "Icon Themes",
            ItemCategory.CURSORS: "Cursor Themes",
            ItemCategory.WALLPAPERS: "Wallpapers",
        }
        return names.get(self, self.value)

    @property
    def short_name(self) -> str:
        names = {
            ItemCategory.EXTENSIONS: "Extensions",
            ItemCategory.GTK_THEMES: "GTK Themes",
            ItemCategory.SHELL_THEMES: "Shell Themes",
            ItemCategory.ICONS: "Icons",
            ItemCategory.CURSORS: "Cursors",
            ItemCategory.WALLPAPERS: "Wallpapers",
        }
        return names.get(self, self.value)

    @property
    def subtitle(self) -> str:
        subs = {
            ItemCategory.EXTENSIONS: "Add-ons & shell enhancements",
            ItemCategory.GTK_THEMES: "Application styles for GTK3 & GTK4",
            ItemCategory.SHELL_THEMES: "Top bar, quick settings & overview",
            ItemCategory.ICONS: "App & folder icon packs",
            ItemCategory.CURSORS: "Mouse pointer styles",
            ItemCategory.WALLPAPERS: "High-resolution desktop backgrounds",
        }
        return subs.get(self, "")

    @property
    def icon_name(self) -> str:
        icons = {
            ItemCategory.EXTENSIONS: "application-x-addon-symbolic",
            ItemCategory.GTK_THEMES: "org.gnome.Settings-appearance-symbolic",
            ItemCategory.SHELL_THEMES: "preferences-system-symbolic",
            ItemCategory.ICONS: "applications-graphics-symbolic",
            ItemCategory.CURSORS: "input-mouse-symbolic",
            ItemCategory.WALLPAPERS: "preferences-desktop-wallpaper-symbolic",
        }
        return icons.get(self, "package-x-generic-symbolic")


class StoreSort(str, Enum):
    POPULAR = "popular"
    DOWNLOADS = "downloads"
    NEWEST = "newest"
    NAME = "name"

    @property
    def display_name(self) -> str:
        return {
            StoreSort.POPULAR: "Popular",
            StoreSort.DOWNLOADS: "Most Downloaded",
            StoreSort.NEWEST: "Newest",
            StoreSort.NAME: "Name (A–Z)",
        }[self]

    @property
    def ocs_sortmode(self) -> str:
        return {
            StoreSort.POPULAR: "high",
            StoreSort.DOWNLOADS: "down",
            StoreSort.NEWEST: "new",
            StoreSort.NAME: "alpha",
        }[self]

    @property
    def ego_sort(self) -> str:
        return {
            StoreSort.POPULAR: "popularity",
            StoreSort.DOWNLOADS: "downloads",
            StoreSort.NEWEST: "created",
            StoreSort.NAME: "name",
        }[self]


@dataclass
class DownloadableFile:
    name: str
    download_url: str = ""
    file_id: str = "1"
    size: str = ""
    formatted_size: str = ""
    url: str = ""

    def __post_init__(self):
        if self.url and not self.download_url:
            self.download_url = self.url
        elif self.download_url and not self.url:
            self.url = self.download_url


@dataclass
class CustomizationItem:
    item_id: str
    name: str
    author: str
    category: ItemCategory
    summary: str = ""
    description: str = ""
    preview_url: str = ""
    thumbnail_url: str = ""
    rating: float = 0.0
    downloads: int = 0
    screenshots: List[str] = field(default_factory=list)
    download_files: List[DownloadableFile] = field(default_factory=list)
    version: str = ""
    uuid: str = ""
    license: str = ""
    homepage_url: str = ""
    updated_at: str = ""
    is_compatible: bool = True
    supported_shell_versions: List[str] = field(default_factory=list)
    is_installed: bool = False
    is_active: bool = False

    def __post_init__(self):
        if self.preview_url and not self.thumbnail_url:
            self.thumbnail_url = self.preview_url
        elif self.thumbnail_url and not self.preview_url:
            self.preview_url = self.thumbnail_url
        if 0 < self.rating <= 5.0:
            self.rating = round(self.rating * 20.0, 1)

    @property
    def download_size(self) -> str:
        for f in self.download_files:
            if f.formatted_size:
                return f.formatted_size
            if f.size:
                return f.size
        return ""

    @property
    def source_label(self) -> str:
        if self.category == ItemCategory.EXTENSIONS:
            return "extensions.gnome.org"
        return "gnome-look.org"


@dataclass
class PaginatedResult:
    items: List[CustomizationItem] = field(default_factory=list)
    page: int = 1
    page_size: int = 24
    total_items: int = 0
    total_pages: int = 1
    error: Optional[str] = None


@dataclass
class LocalAsset:
    name: str
    category: ItemCategory
    path: str
    display_name: str = ""
    description: str = ""
    is_system: bool = False
    is_active: bool = False
    details: str = ""
    author: str = ""
    has_gtk3: bool = False
    has_gtk4: bool = False
    has_shell: bool = False
    has_icons: bool = False
    has_cursors: bool = False
    has_prefs: bool = False
    version: str = ""
    error: str = ""

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.name


@dataclass
class DesktopState:
    icon_theme: str = "Adwaita"
    gtk_theme: str = "Adwaita"
    shell_theme: str = "Default"
    cursor_theme: str = "Adwaita"
    color_scheme: str = "prefer-dark"
    accent_color: str = "blue"
    button_layout: str = "appmenu:close"
    center_new_windows: bool = False
    font_scaling_factor: float = 1.0
    wallpaper_uri: str = ""
    enabled_extensions: List[str] = field(default_factory=list)
    focus_mode: str = "click"
    font_antialiasing: str = "rgba"
    font_hinting: str = "slight"
