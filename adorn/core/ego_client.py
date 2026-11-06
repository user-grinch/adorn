"""GNOME Shell Extensions (extensions.gnome.org) client."""

import json
import subprocess
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .models import (
    CustomizationItem,
    DownloadableFile,
    ItemCategory,
    PaginatedResult,
    StoreSort,
)


class EgoClient:
    """Queries extensions.gnome.org API with pagination, sorting, and shell version checks."""

    BASE_URL = "https://extensions.gnome.org"

    def __init__(self):
        self.user_agent = "Adorn/1.0 (GNOME Customization Store; Linux; com.grinch.gnome.adorn)"
        self._cached_shell_ver: Optional[str] = None

    @property
    def shell_version(self) -> str:
        if self._cached_shell_ver is None:
            self._cached_shell_ver = self._detect_shell_version()
        return self._cached_shell_ver

    def _detect_shell_version(self) -> str:
        try:
            out = subprocess.check_output(["gnome-shell", "--version"], text=True, timeout=3)
            parts = out.strip().split()
            if len(parts) >= 3:
                return parts[2].split(".")[0]
        except Exception:
            pass
        return "51"

    def fetch_paginated(
        self,
        query: str = "",
        page: int = 1,
        page_size: int = 24,
        sort: StoreSort = StoreSort.POPULAR,
    ) -> PaginatedResult:
        safe_page = max(1, page)
        safe_size = max(5, min(50, page_size))
        ego_sort = sort.ego_sort if isinstance(sort, StoreSort) else "popularity"

        params: Dict[str, Any] = {
            "page": safe_page,
            "n_per_page": safe_size,
            "sort": ego_sort,
        }
        if query.strip():
            params["search"] = query.strip()

        query_str = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}/extension-query/?{query_str}"
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})

        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[EgoClient] Request failed ({url}): {e}")
            return PaginatedResult(
                items=[],
                page=safe_page,
                page_size=safe_size,
                total_items=0,
                total_pages=1,
                error=str(e),
            )

        shell_ver = self.shell_version
        results: List[CustomizationItem] = []
        for ext in data.get("extensions", []):
            item = self._parse_extension(ext, shell_ver)
            if item:
                results.append(item)

        try:
            total_pages = max(1, int(data.get("numpages", 1) or 1))
        except (ValueError, TypeError):
            total_pages = 1

        approx_total = total_pages * safe_size if total_pages > 1 else len(results)
        return PaginatedResult(
            items=results,
            page=safe_page,
            page_size=safe_size,
            total_items=approx_total,
            total_pages=total_pages,
        )

    def fetch_popular(self, page: int = 1, page_size: int = 24) -> List[CustomizationItem]:
        return self.fetch_paginated(page=page, page_size=page_size, sort=StoreSort.POPULAR).items

    def search_extensions(
        self,
        query: str = "",
        page: int = 1,
        page_size: int = 24,
        sort: StoreSort = StoreSort.POPULAR,
    ) -> List[CustomizationItem]:
        return self.fetch_paginated(query=query, page=page, page_size=page_size, sort=sort).items

    def fetch_user_theme_extension(self) -> Optional[CustomizationItem]:
        items = self.search_extensions("user-theme", page=1, page_size=10)
        for it in items:
            if it.uuid == "user-theme@gnome-shell-extensions.gcampax.github.com" or "user-theme@" in it.uuid:
                return it
        return items[0] if items else None

    def _parse_extension(self, ext: Dict[str, Any], shell_ver: str) -> Optional[CustomizationItem]:
        uuid = ext.get("uuid")
        name = ext.get("name")
        if not uuid or not name:
            return None

        icon_path = ext.get("icon", "") or ""
        if icon_path.startswith("/"):
            icon_url = f"{self.BASE_URL}{icon_path}"
        else:
            icon_url = icon_path
        if "plugin.png" in icon_url:
            icon_url = ""

        vmap = ext.get("shell_version_map", {}) or {}
        supported_versions = sorted(
            [str(k) for k in vmap.keys()],
            key=lambda s: [int(p) if p.isdigit() else 0 for p in s.split(".")],
        )
        is_compatible = shell_ver in vmap or any(k.startswith(f"{shell_ver}.") for k in vmap)

        target_info = vmap.get(shell_ver)
        if not target_info:
            for k in reversed(supported_versions):
                if k.startswith(f"{shell_ver}."):
                    target_info = vmap.get(k)
                    break
        if not target_info and supported_versions:
            target_info = vmap.get(supported_versions[-1])

        download_files: List[DownloadableFile] = []
        ext_ver_str = ""
        if target_info:
            pk = target_info.get("pk")
            ver = target_info.get("version", "")
            if ver != "":
                ext_ver_str = str(ver)
            if pk:
                dl_url = f"{self.BASE_URL}/download-extension/{uuid}.shell-extension.zip?version_tag={pk}"
                label = f"{uuid} (v{ver})" if ver else f"{uuid}.zip"
                download_files.append(
                    DownloadableFile(
                        file_id=str(pk),
                        name=label,
                        download_url=dl_url,
                    )
                )

        description = (ext.get("description", "") or "").strip()
        first_line = description.split("\n")[0].strip() if description else ""

        downloads = ext.get("downloads", 0)
        try:
            downloads = int(downloads)
        except (ValueError, TypeError):
            downloads = 0

        screenshots: List[str] = []
        sc = ext.get("screenshot") or ""
        if sc:
            if sc.startswith("/"):
                sc = f"{self.BASE_URL}{sc}"
            screenshots.append(sc)

        link = ext.get("link") or ""
        homepage_url = f"{self.BASE_URL}{link}" if link.startswith("/") else (link or f"{self.BASE_URL}")

        return CustomizationItem(
            item_id=uuid,
            uuid=uuid,
            name=name.strip(),
            summary=first_line,
            description=description,
            author=ext.get("creator", "") or "GNOME Community",
            category=ItemCategory.EXTENSIONS,
            preview_url=icon_url,
            thumbnail_url=icon_url,
            rating=0.0,
            downloads=downloads,
            screenshots=screenshots,
            download_files=download_files,
            version=ext_ver_str,
            homepage_url=homepage_url,
            is_compatible=is_compatible,
            supported_shell_versions=supported_versions,
        )
