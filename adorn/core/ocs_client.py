"""OpenCollaborationServices (OCS / Pling / GNOME-Look) client.

Clean-room implementation of the OCS REST API for discovering and downloading
community themes, icons, cursors, and wallpapers with full pagination and sorting.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import math
import os
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from gi.repository import GLib

from .models import (
    CustomizationItem,
    DownloadableFile,
    ItemCategory,
    PaginatedResult,
    StoreSort,
)


def _rank_download_file(name: str, category: ItemCategory) -> int:
    """Ranks downloadable package files so suitable Linux archives appear first."""
    name_low = name.lower().strip()
    if "windows" in name_low or name_low.endswith((".exe", ".msi", ".bat", ".cmd")):
        return 100
    has_ext = "." in name_low and not name_low.endswith(".")
    if not has_ext:
        return 90

    if category == ItemCategory.WALLPAPERS:
        if name_low.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return 1
        if name_low.endswith((".tar.xz", ".tar.gz", ".zip")):
            return 2
        return 10

    if name_low.endswith((".tar.xz", ".txz")):
        return 1
    if name_low.endswith((".tar.gz", ".tgz")):
        return 2
    if name_low.endswith((".tar.bz2", ".tbz2")):
        return 3
    if name_low.endswith(".tar"):
        return 4
    if name_low.endswith(".zip"):
        return 5
    if name_low.endswith(".7z"):
        return 6
    return 40


class OcsClient:
    """Queries gnome-look / opendesktop OCS API."""

    BASE_URL = "https://api.opendesktop.org/ocs/v1/content"

    CATEGORY_MAP = {
        ItemCategory.ICONS: "386",
        ItemCategory.GTK_THEMES: "135",
        ItemCategory.SHELL_THEMES: "134",
        ItemCategory.CURSORS: "107",
        ItemCategory.WALLPAPERS: "300",
    }

    SORT_MAP = {
        "popular": "high",
        "high_rating": "high",
        "high": "high",
        "downloads": "down",
        "down": "down",
        "newest": "new",
        "new": "new",
        "name": "alpha",
        "alpha": "alpha",
    }

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or (Path.home() / ".cache" / "adorn" / "thumbnails")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = "Adorn/1.0 (GNOME Customization Store; Linux; com.grinch.gnome.adorn)"
        self._thumb_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="adorn-thumb")
        self._inflight_lock = threading.Lock()
        self._inflight_callbacks: Dict[str, List[Callable[[Optional[str]], None]]] = {}

    def _make_request(
        self, endpoint: str, params: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        params["format"] = "json"
        query_str = urllib.parse.urlencode(params)
        full_url = f"{self.BASE_URL}/{endpoint}?{query_str}"

        req = urllib.request.Request(
            full_url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=14) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data, None
        except Exception as e:
            print(f"[OcsClient] Request error ({full_url}): {e}")
            return None, str(e)

    def fetch_paginated(
        self,
        category: Optional[ItemCategory] = ItemCategory.ICONS,
        search: str = "",
        page: int = 1,
        page_size: int = 24,
        sort: StoreSort = StoreSort.POPULAR,
    ) -> PaginatedResult:
        """Fetches a page of items with total count and page metadata."""
        safe_page = max(1, page)
        safe_size = max(10, min(60, page_size))
        sortmode = sort.ocs_sortmode if isinstance(sort, StoreSort) else self.SORT_MAP.get(str(sort), "high")

        params: Dict[str, Any] = {
            "page": safe_page - 1,
            "pagesize": safe_size,
            "sortmode": sortmode,
        }
        if category and category in self.CATEGORY_MAP:
            params["categories"] = self.CATEGORY_MAP[category]
        else:
            params["categories"] = "x".join(self.CATEGORY_MAP.values())

        if search.strip():
            params["search"] = search.strip()

        resp, err = self._make_request("data", params)
        if not resp:
            return PaginatedResult(
                items=[],
                page=safe_page,
                page_size=safe_size,
                total_items=0,
                total_pages=1,
                error=err or "Could not reach gnome-look.org",
            )

        raw_items = resp.get("data", [])
        if not isinstance(raw_items, list):
            raw_items = []

        try:
            total_items = int(resp.get("totalitems", len(raw_items)) or 0)
        except (ValueError, TypeError):
            total_items = len(raw_items)

        results: List[CustomizationItem] = []
        for raw in raw_items:
            cat = category or self._infer_category(str(raw.get("typeid", "")))
            item = self._parse_item(raw, cat)
            if item:
                results.append(item)

        if total_items < len(results):
            total_items = len(results)
        total_pages = max(1, math.ceil(total_items / safe_size)) if total_items > 0 else 1

        return PaginatedResult(
            items=results,
            page=safe_page,
            page_size=safe_size,
            total_items=total_items,
            total_pages=total_pages,
        )

    def fetch_items(
        self,
        category: ItemCategory = ItemCategory.ICONS,
        search: str = "",
        page: int = 1,
        page_size: int = 24,
        sort: str = "popular",
    ) -> List[CustomizationItem]:
        """Convenience wrapper returning the items list."""
        sort_enum = StoreSort.POPULAR
        for s in StoreSort:
            if s.value == sort or s.ocs_sortmode == self.SORT_MAP.get(sort):
                sort_enum = s
                break
        return self.fetch_paginated(
            category=category,
            search=search,
            page=page,
            page_size=page_size,
            sort=sort_enum,
        ).items

    def search_items(
        self,
        query: str,
        category: Optional[ItemCategory] = None,
        page: int = 1,
        page_size: int = 24,
    ) -> List[CustomizationItem]:
        return self.fetch_paginated(
            category=category,
            search=query,
            page=page,
            page_size=page_size,
        ).items

    def fetch_item_details(self, item_id: str, category: ItemCategory) -> Optional[CustomizationItem]:
        resp, _ = self._make_request(f"data/{item_id}", {})
        if not resp:
            return None

        raw = resp.get("data", {})
        if isinstance(raw, list) and raw:
            raw = raw[0]
        if not isinstance(raw, dict):
            return None

        return self._parse_item(raw, category, include_files=True)

    def _infer_category(self, type_id: str) -> ItemCategory:
        reverse_map = {v: k for k, v in self.CATEGORY_MAP.items()}
        return reverse_map.get(str(type_id), ItemCategory.GTK_THEMES)

    def _format_size(self, size_val: Any) -> str:
        if size_val is None or size_val == "":
            return ""
        try:
            # OCS downloadsize is reported in KB
            val = float(str(size_val).strip())
            if val <= 0:
                return ""
            if val < 1024:
                return f"{int(round(val))} KB"
            elif val < 1024 * 1024:
                return f"{val / 1024:.1f} MB"
            else:
                return f"{val / (1024 * 1024):.1f} GB"
        except (ValueError, TypeError):
            return str(size_val)

    def _parse_item(
        self,
        raw: Dict[str, Any],
        category: ItemCategory,
        include_files: bool = True,
    ) -> Optional[CustomizationItem]:
        item_id = str(raw.get("id", ""))
        name = html.unescape(str(raw.get("name", "")).strip())
        if not item_id or not name:
            return None

        thumb = (
            raw.get("smallpreviewpic1")
            or raw.get("previewpic1")
            or raw.get("icon")
            or ""
        )
        preview = raw.get("previewpic1") or thumb

        screenshots: List[str] = []
        for i in range(1, 7):
            pic = raw.get(f"previewpic{i}")
            if pic and pic not in screenshots:
                screenshots.append(pic)

        try:
            score = float(raw.get("score", 0) or 0)
        except (ValueError, TypeError):
            score = 0.0

        try:
            downloads = int(raw.get("downloads", 0) or 0)
        except (ValueError, TypeError):
            downloads = 0

        files: List[DownloadableFile] = []
        if include_files:
            i = 1
            while i <= 25:
                link = raw.get(f"downloadlink{i}")
                file_name = raw.get(f"downloadname{i}")
                raw_size = raw.get(f"downloadsize{i}", "")
                if not link and not file_name:
                    break
                if link and file_name:
                    formatted_sz = self._format_size(raw_size)
                    files.append(
                        DownloadableFile(
                            file_id=str(i),
                            name=html.unescape(str(file_name)),
                            download_url=str(link),
                            size=str(raw_size),
                            formatted_size=formatted_sz,
                        )
                    )
                i += 1

            files.sort(key=lambda f: _rank_download_file(f.name, category))

        raw_summary = str(raw.get("summary", "") or "")
        summary = html.unescape(re.sub(r"<[^>]+>", " ", raw_summary))
        summary = " ".join(summary.split()).strip()

        detail_url = str(raw.get("detailpage", "") or f"https://www.gnome-look.org/p/{item_id}")
        changed_raw = str(raw.get("changed", "") or "")
        updated_at = changed_raw[:10] if len(changed_raw) >= 10 else ""

        return CustomizationItem(
            item_id=item_id,
            name=name,
            summary=summary,
            description=str(raw.get("description", "") or summary),
            author=str(raw.get("personid", "") or "Community"),
            category=category,
            preview_url=preview,
            thumbnail_url=thumb,
            rating=round(score, 1),
            downloads=downloads,
            screenshots=screenshots,
            download_files=files,
            version=str(raw.get("version", "") or "").strip(),
            homepage_url=detail_url,
            updated_at=updated_at,
        )

    def download_thumbnail(self, url: str) -> Optional[str]:
        if not url:
            return None

        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"):
            ext = ".png"
        safe_name = hashlib.sha256(url.encode("utf-8")).hexdigest() + ext
        cached_path = self.cache_dir / safe_name

        if cached_path.exists() and cached_path.stat().st_size > 0:
            return str(cached_path)

        tmp_path = cached_path.with_suffix(ext + ".part")
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
                if not data:
                    return None
                with open(tmp_path, "wb") as out:
                    out.write(data)
                os.replace(tmp_path, cached_path)
            return str(cached_path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def load_thumbnail_async(self, url: str, callback: Callable[[Optional[str]], None]) -> None:
        if not url:
            GLib.idle_add(callback, None)
            return

        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"):
            ext = ".png"
        safe_name = hashlib.sha256(url.encode("utf-8")).hexdigest() + ext
        cached_path = self.cache_dir / safe_name
        if cached_path.exists() and cached_path.stat().st_size > 0:
            GLib.idle_add(callback, str(cached_path))
            return

        with self._inflight_lock:
            if url in self._inflight_callbacks:
                self._inflight_callbacks[url].append(callback)
                return
            self._inflight_callbacks[url] = [callback]

        def worker():
            path = self.download_thumbnail(url)
            with self._inflight_lock:
                cbs = self._inflight_callbacks.pop(url, [])
            for cb in cbs:
                GLib.idle_add(cb, path)

        self._thumb_pool.submit(worker)
