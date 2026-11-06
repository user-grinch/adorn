"""Centralized download, installation, and uninstallation task queue & registry."""

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional
from gi.repository import GLib

from adorn.core.models import CustomizationItem, DownloadableFile, ItemCategory


@dataclass
class TaskEntry:
    task_id: str
    item_id: str
    name: str
    category: ItemCategory
    thumbnail_url: Optional[str] = None
    item: Optional[CustomizationItem] = None
    file_info: Optional[DownloadableFile] = None
    status: str = "downloading"  # downloading, extracting, installing, done, uninstalled, failed, cancelled
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    status_text: str = "Starting…"
    installed_paths: List[str] = field(default_factory=list)
    installed_theme_name: str = ""
    freed_bytes: int = 0
    error_message: str = ""
    created_at: float = field(default_factory=time.time)
    is_historical: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)


class TaskManager:
    """
    Observable download, installation, and uninstallation queue + registry.
    Coordinates real-time state across ExploreView, DetailPage, DownloadsView,
    InstalledView, and MainWindow.
    """

    _instance: Optional["TaskManager"] = None

    @classmethod
    def get_default(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = TaskManager()
        return cls._instance

    def __init__(self):
        self.tasks: Dict[str, TaskEntry] = {}
        self._listeners: List[Callable[[], None]] = []
        self._task_listeners: List[Callable[[TaskEntry], None]] = []
        self.registry_path = Path.home() / ".local" / "share" / "adorn" / "installed_registry.json"
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry: Dict[str, dict] = self._load_registry()
        self._seed_history_from_registry()

    def _load_registry(self) -> Dict[str, dict]:
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        # Fallback to legacy path if migrating
        legacy_path = Path.home() / ".local" / "share" / "ggcu" / "installed_registry.json"
        if legacy_path.exists():
            try:
                data = json.loads(legacy_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def _save_registry(self) -> None:
        try:
            self.registry_path.write_text(json.dumps(self.registry, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _seed_history_from_registry(self) -> None:
        """Populates recent store installations from persistent registry so Downloads isn't blank on relaunch."""
        entries = list(self.registry.values())
        entries.sort(key=lambda e: float(e.get("installed_at", 0.0) or 0.0), reverse=True)
        for entry in entries[:25]:
            paths = entry.get("paths", [])
            if not any(os.path.exists(p) for p in paths):
                continue
            item_id = str(entry.get("item_id", ""))
            if not item_id:
                continue
            try:
                cat = ItemCategory(entry.get("category", "gtk_themes"))
            except ValueError:
                cat = ItemCategory.GTK_THEMES

            tid = f"hist_{item_id}"
            self.tasks[tid] = TaskEntry(
                task_id=tid,
                item_id=item_id,
                name=entry.get("name", item_id),
                category=cat,
                thumbnail_url=entry.get("thumbnail_url"),
                status="done",
                progress=1.0,
                status_text="Installed",
                installed_paths=paths,
                installed_theme_name=entry.get("theme_name", ""),
                created_at=float(entry.get("installed_at", time.time())),
                is_historical=True,
            )

    def subscribe(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def subscribe_task(self, callback: Callable[[TaskEntry], None]) -> None:
        if callback not in self._task_listeners:
            self._task_listeners.append(callback)

    def notify_progress(self, task: TaskEntry) -> None:
        """Lightweight notification for progress bar updates."""
        def _emit():
            for tcb in list(self._task_listeners):
                try:
                    tcb(task)
                except Exception as e:
                    print(f"[TaskManager] progress listener error: {e}")
            return False

        GLib.idle_add(_emit)

    def notify_state_change(self, task: Optional[TaskEntry] = None) -> None:
        """Full notification for state transitions."""
        def _emit():
            for cb in list(self._listeners):
                try:
                    cb()
                except Exception as e:
                    print(f"[TaskManager] listener error: {e}")
            if task is not None:
                for tcb in list(self._task_listeners):
                    try:
                        tcb(task)
                    except Exception as e:
                        print(f"[TaskManager] task listener error: {e}")
            return False

        GLib.idle_add(_emit)

    def notify(self, task: Optional[TaskEntry] = None) -> None:
        self.notify_state_change(task)

    def get_active_downloads(self) -> List[TaskEntry]:
        entries = [
            t for t in self.tasks.values()
            if t.status in ("downloading", "extracting", "installing")
        ]
        entries.sort(key=lambda x: x.created_at, reverse=True)
        return entries

    def get_active_count(self) -> int:
        return len(self.get_active_downloads())

    def get_completed_downloads(self) -> List[TaskEntry]:
        entries = [t for t in self.tasks.values() if t.status == "done"]
        entries.sort(key=lambda x: x.created_at, reverse=True)
        return entries

    def get_failed_downloads(self) -> List[TaskEntry]:
        entries = [t for t in self.tasks.values() if t.status in ("failed", "cancelled")]
        entries.sort(key=lambda x: x.created_at, reverse=True)
        return entries

    def get_active_and_recent_downloads(self) -> List[TaskEntry]:
        entries = [
            t for t in self.tasks.values()
            if t.status in ("downloading", "extracting", "installing", "done", "failed", "cancelled")
        ]
        entries.sort(key=lambda x: x.created_at, reverse=True)
        return entries

    def get_recently_uninstalled(self) -> List[TaskEntry]:
        entries = [t for t in self.tasks.values() if t.status == "uninstalled"]
        entries.sort(key=lambda x: x.created_at, reverse=True)
        return entries

    def dismiss_task(self, task_id: str) -> None:
        if task_id in self.tasks:
            self.tasks.pop(task_id, None)
            self.notify()

    def clear_finished_tasks(self) -> None:
        to_remove = [
            tid for tid, t in self.tasks.items()
            if t.status in ("done", "uninstalled", "failed", "cancelled")
        ]
        for tid in to_remove:
            self.tasks.pop(tid, None)
        self.notify()

    def get_task_for_item(self, item_id: str) -> Optional[TaskEntry]:
        for t in self.tasks.values():
            if t.item_id == str(item_id) and t.status in ("downloading", "extracting", "installing"):
                return t
        return None

    def register_installation(
        self,
        item: CustomizationItem,
        installed_paths: List[str],
        theme_name: str,
    ) -> None:
        # Remove any stale historical entry for the same item_id so it doesn't duplicate
        self.tasks.pop(f"hist_{item.item_id}", None)
        self.registry[str(item.item_id)] = {
            "item_id": str(item.item_id),
            "name": item.name,
            "author": item.author,
            "category": item.category.value,
            "theme_name": theme_name,
            "paths": installed_paths,
            "installed_at": time.time(),
            "thumbnail_url": item.thumbnail_url or (item.screenshots[0] if item.screenshots else None),
        }
        self._save_registry()

    def unregister_installation(self, item_id: str = "", theme_name: str = "", path: str = "") -> None:
        keys_to_delete = []
        for k, entry in self.registry.items():
            if item_id and k == str(item_id):
                keys_to_delete.append(k)
            elif theme_name and entry.get("theme_name", "").lower() == theme_name.lower():
                keys_to_delete.append(k)
            elif path and path in entry.get("paths", []):
                keys_to_delete.append(k)
        for k in keys_to_delete:
            self.registry.pop(k, None)
            self.tasks.pop(f"hist_{k}", None)
        # Also remove matching done task entries
        done_to_pop = [
            tid for tid, t in self.tasks.items()
            if t.status == "done" and (
                (item_id and t.item_id == str(item_id))
                or (theme_name and t.installed_theme_name.lower() == theme_name.lower())
                or (path and path in t.installed_paths)
            )
        ]
        for tid in done_to_pop:
            self.tasks.pop(tid, None)
        if keys_to_delete:
            self._save_registry()

    def get_registered_entry(self, item: CustomizationItem) -> Optional[dict]:
        entry = self.registry.get(str(item.item_id))
        if entry:
            paths = entry.get("paths", [])
            if any(os.path.exists(p) for p in paths):
                return entry
            else:
                self.registry.pop(str(item.item_id), None)
                self._save_registry()
        return None

    def record_uninstalled(
        self,
        name: str,
        category: ItemCategory,
        freed_bytes: int,
        item_id: str = "",
        thumbnail_url: Optional[str] = None,
        status_text: str = "Removed",
    ) -> TaskEntry:
        tid = f"uninst_{item_id or name}_{int(time.time() * 1000)}"
        entry = TaskEntry(
            task_id=tid,
            item_id=str(item_id or name),
            name=name,
            category=category,
            thumbnail_url=thumbnail_url,
            status="uninstalled",
            progress=1.0,
            freed_bytes=freed_bytes,
            status_text=status_text,
        )
        self.tasks[tid] = entry
        self.notify(entry)
        return entry
