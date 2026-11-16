"""Tiny HTTP helper shared by the artwork and TV lookups. Blocking: call it off the UI thread."""
from __future__ import annotations

import time
import urllib.error
import urllib.request

UA = "Marquee/0.1 (media library; +https://github.com/)"


def get(url: str, timeout: float = 10.0) -> bytes:
    """GET with one polite retry when the server asks us to slow down (HTTP 429)."""
    for attempt in (0, 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(3)
                continue
            raise
    raise OSError("unreachable")
