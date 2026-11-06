"""Image and display utilities for Adorn UI components."""

from concurrent.futures import ThreadPoolExecutor
import os
import threading
from typing import Callable, Dict, Optional, Tuple
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib

_TEXTURE_CACHE: Dict[Tuple[str, int, int], Gdk.Texture] = {}
_CACHE_LOCK = threading.Lock()
_DECODE_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="adorn-img")


def load_cropped_texture(file_path: str, width: int, height: int) -> Optional[Gdk.Texture]:
    """
    Loads an image from file_path, center-crops it to match (width:height) aspect ratio,
    and scales it cleanly to an exact Gdk.Texture.
    """
    if not file_path or not os.path.exists(file_path):
        return None

    cache_key = (file_path, width, height)
    with _CACHE_LOCK:
        if cache_key in _TEXTURE_CACHE:
            return _TEXTURE_CACHE[cache_key]

    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(file_path)
        w = pixbuf.get_width()
        h = pixbuf.get_height()
        if w <= 0 or h <= 0:
            return None

        target_ratio = width / float(height)
        src_ratio = w / float(h)

        if src_ratio > target_ratio:
            crop_h = h
            crop_w = max(1, int(round(h * target_ratio)))
            src_x = (w - crop_w) // 2
            src_y = 0
        else:
            crop_w = w
            crop_h = max(1, int(round(w / target_ratio)))
            src_x = 0
            src_y = (h - crop_h) // 2

        cropped = GdkPixbuf.Pixbuf.new_subpixbuf(pixbuf, src_x, src_y, crop_w, crop_h)
        scaled = cropped.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        if not scaled:
            return None
        tex = Gdk.Texture.new_for_pixbuf(scaled)
        with _CACHE_LOCK:
            if len(_TEXTURE_CACHE) > 400:
                _TEXTURE_CACHE.clear()
            _TEXTURE_CACHE[cache_key] = tex
        return tex
    except Exception:
        return None


def load_fixed_square_texture(file_path: str, size: int = 56) -> Optional[Gdk.Texture]:
    return load_cropped_texture(file_path, size, size)


def load_cropped_texture_async(
    file_path: str,
    width: int,
    height: int,
    callback: Optional[Callable[[Optional[Gdk.Texture]], None]] = None,
) -> None:
    if not file_path:
        if callback:
            GLib.idle_add(callback, None)
        return

    cache_key = (file_path, width, height)
    with _CACHE_LOCK:
        if cache_key in _TEXTURE_CACHE:
            if callback:
                GLib.idle_add(callback, _TEXTURE_CACHE[cache_key])
            return

    def worker():
        tex = load_cropped_texture(file_path, width, height)
        if callback:
            GLib.idle_add(callback, tex)

    _DECODE_POOL.submit(worker)


def load_fixed_square_texture_async(
    file_path: str,
    size: int = 56,
    callback: Optional[Callable[[Optional[Gdk.Texture]], None]] = None,
) -> None:
    load_cropped_texture_async(file_path, size, size, callback)


def format_count(n: int) -> str:
    """Formats download and rating counts into clean human-readable strings."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        val = n / 1_000
        return f"{val:.1f}k" if val < 100 else f"{int(val)}k"
    return str(n)


def format_relative_time(timestamp: float) -> str:
    """Formats a UNIX timestamp into a concise relative string."""
    import time
    diff = max(0, int(time.time() - timestamp))
    if diff < 45:
        return "Just now"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    days = diff // 86400
    if days < 30:
        return f"{days}d ago"
    return time.strftime("%b %d", time.localtime(timestamp))
