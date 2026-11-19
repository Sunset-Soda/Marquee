"""HLS master playlists: which picture sizes a live channel offers.

A live channel's address is often a "master" playlist listing several versions of the same stream (say 480p, 720p
and 1080p). The player picks one of them by itself; reading the list lets Marquee see what is on offer and ask for
the largest.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request

from . import net

MAX_BYTES = 1_000_000
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def parse_master(text: str, base: str) -> list[dict]:
    """[{url, width, height, bandwidth}], biggest picture first. Empty when this is not a master playlist."""
    out, pending = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = {k: v.strip('"') for k, v in _ATTR.findall(line.split(":", 1)[1])}
            m = re.match(r"(\d+)x(\d+)", attrs.get("RESOLUTION", ""))
            try:
                bw = int(attrs.get("BANDWIDTH") or attrs.get("AVERAGE-BANDWIDTH") or 0)
            except ValueError:
                bw = 0
            pending = {"width": int(m.group(1)) if m else 0, "height": int(m.group(2)) if m else 0, "bandwidth": bw}
        elif line and not line.startswith("#") and pending is not None:
            url = urllib.parse.urljoin(base, line)
            if url.lower().startswith(("http://", "https://")):
                out.append(dict(pending, url=url))
            pending = None
    return sorted(out, key=lambda v: (v["height"], v["bandwidth"]), reverse=True)


def variants(url: str, timeout: float = 6.0) -> list[dict]:
    """The versions a channel offers, or [] when it is not an HLS master playlist (or cannot be read)."""
    if not url.lower().startswith(("http://", "https://")):
        return []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": net.UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.geturl()
            data = r.read(MAX_BYTES)
    except Exception:
        return []
    text = data.decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" not in text:
        return []
    return parse_master(text, final)


def describe(vs: list[dict]) -> str:
    return ", ".join(f"{v['width']}x{v['height']} @ {v['bandwidth'] / 1e6:.1f} Mbps" if v["height"]
                     else f"unknown size @ {v['bandwidth'] / 1e6:.1f} Mbps" for v in vs)
