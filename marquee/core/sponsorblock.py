"""SponsorBlock: community-made timestamps for the parts of a YouTube video you probably want to skip.

The service is asked with only the first four characters of a hash of the video id, so it never learns which video
you are watching. The answer lists every video sharing that prefix, and we keep the one that is ours.
https://wiki.sponsor.ajay.app/w/API_Docs
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

API = "https://sponsor.ajay.app/api/skipSegments/"
UA = "Marquee (https://github.com/Sunset-Soda/Marquee)"

CATEGORIES = {
    "sponsor": "Sponsor",
    "selfpromo": "Self-promotion",
    "interaction": "Like and subscribe reminder",
    "intro": "Intro",
    "outro": "Outro / end cards",
    "preview": "Preview or recap",
    "filler": "Filler and tangents",
    "music_offtopic": "Non-music part of music videos",
}
DEFAULT = ("sponsor", "selfpromo", "interaction")


def http(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read()


def segments(video_id: str, categories=DEFAULT, request: Callable[[str], bytes] = http) -> list[tuple[float, float, str]]:
    """Time ranges to skip, as (start, end, category) sorted by start. [] when there are none or the service is out of reach."""
    cats = [c for c in categories if c in CATEGORIES]
    if not video_id or not cats:
        return []
    prefix = hashlib.sha256(video_id.encode()).hexdigest()[:4]
    url = API + prefix + "?" + urllib.parse.urlencode({"categories": json.dumps(cats), "actionTypes": json.dumps(["skip"])})
    try:
        rows = json.loads(request(url))
    except urllib.error.HTTPError as e:
        if e.code != 404:                                  # 404 just means nobody has marked anything yet
            print(f"[sponsorblock] {video_id}: HTTP {e.code}")
        return []
    except Exception as e:
        print(f"[sponsorblock] {video_id}: {e}")
        return []
    out: list[tuple[float, float, str]] = []
    for row in rows if isinstance(rows, list) else []:
        if row.get("videoID") != video_id:
            continue
        for s in row.get("segments", []):
            try:
                a, b = (float(x) for x in s["segment"])
            except (KeyError, TypeError, ValueError):
                continue
            if b > a and s.get("category") in cats and s.get("actionType", "skip") == "skip":
                out.append((a, b, s["category"]))
    return sorted(out)


def merge(segs: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Join segments that overlap or touch, so playback jumps once instead of stepping through a chain."""
    out: list[list] = []
    for a, b, c in sorted(segs):
        if out and a <= out[-1][1] + 0.5:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b, c])
    return [(a, b, c) for a, b, c in out]
