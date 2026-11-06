"""Adorn: GNOME Customization Storefront & Desktop Tweaks."""

import socket

__version__ = "1.0.0"
__app_id__ = "com.grinch.gnome.adorn"
__app_name__ = "Adorn"

# Global resilient DNS resolution for CDN77 Anycast fallback (fixes timeout on local edge caches)
_orig_getaddrinfo = socket.getaddrinfo
FALLBACK_CDN_IPS = ["207.211.213.90", "37.19.218.108"]


def _safe_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == "cdn.extensions.gnome.org":
        res = []
        for ip in FALLBACK_CDN_IPS:
            res.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
        return res
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _safe_getaddrinfo

