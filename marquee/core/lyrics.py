"""Lyrics for a song, with timings when they exist, so the screen can follow the singing like a karaoke display.

Where they come from, in order:
  1. a `.lrc` file beside the song (same name),
  2. lyrics stored in the audio file's tags (read with ffprobe), including timed ones,
  3. LRCLIB (https://lrclib.net), a free community library of timed lyrics. It needs no account. Only the artist,
     title, album and length of the song are sent.

Found lyrics are kept in the cache folder, so a song is looked up once. A song with no lyrics is not asked about
again for a week.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .library import Item, Library, cache_dir

API = "https://lrclib.net/api"
UA = "Marquee v0.1 (https://github.com/marquee)"
Request = Callable[[str], bytes]        # url -> response body; raises on failure
NEGATIVE_DAYS = 7


@dataclass
class Line:
    t: float                  # seconds from the start of the song; -1 when the lyrics have no timing
    text: str


@dataclass
class Lyrics:
    lines: list[Line] = field(default_factory=list)
    synced: bool = False
    source: str = ""          # "file", "tags", "lrclib"
    duration: float = 0.0     # length of the recording the timings were made for, when known

    def as_rows(self) -> list[dict]:
        return [{"t": ln.t, "text": ln.text} for ln in self.lines]


# ---------------------------------------------------------------- reading LRC
_STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_WORD = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")
_TAG = re.compile(r"^\[(ar|ti|al|au|by|length|re|ve|tool|la|#):[^\]]*\]\s*$", re.I)
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]", re.I | re.M)


def _secs(m: re.Match) -> float:
    frac = m.group(3) or "0"
    return int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / (10 ** len(frac))


def parse(text: str) -> Lyrics | None:
    """Parse LRC (timed) or plain lyrics. None when there is nothing to show."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    off = _OFFSET.search(text)
    shift = int(off.group(1)) / 1000 if off else 0.0
    timed: list[Line] = []
    plain: list[str] = []
    for raw in text.split("\n"):
        raw = raw.strip()
        if not raw or _TAG.match(raw) or _OFFSET.match(raw):
            continue
        stamps = list(_STAMP.finditer(raw))
        if stamps and stamps[0].start() == 0:
            end = 0
            for m in stamps:
                if m.start() != end:
                    break
                end = m.end()
            body = _WORD.sub("", raw[end:]).strip()
            for m in stamps:
                if m.end() <= end:
                    timed.append(Line(max(0.0, _secs(m) + shift), body))
        else:
            plain.append(_WORD.sub("", raw))
    if len([ln for ln in timed if ln.text]) >= 2:
        timed.sort(key=lambda ln: ln.t)
        return Lyrics(timed, True)
    lines = [Line(-1.0, ln) for ln in plain if ln]
    return Lyrics(lines, False) if len(lines) >= 2 else None


# ---------------------------------------------------------------- local sources
def from_sidecar(path: str) -> Lyrics | None:
    p = Path(path).with_suffix(".lrc")
    for cand in (p, p.with_name(p.stem + ".en.lrc")):
        try:
            got = parse(cand.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if got:
            got.source = "file"
            return got
    return None


def from_tags(path: str, timeout: float = 15.0) -> Lyrics | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json", path],
                             capture_output=True, text=True, timeout=timeout, errors="replace").stdout
        data = json.loads(out or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    tags = dict(data.get("format", {}).get("tags") or {})
    for st in data.get("streams", []):
        tags.update(st.get("tags") or {})
    best = None
    for key, value in tags.items():
        if "lyric" in key.lower() and isinstance(value, str) and len(value) > 20:
            got = parse(value)
            if got and (best is None or (got.synced and not best.synced)):
                best = got
    if best:
        best.source = "tags"
    return best


# ---------------------------------------------------------------- LRCLIB
def http(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def _clean(title: str) -> str:
    """Drop the parts of a title that other people leave out: (feat. X), - Remastered 2011, [Live]."""
    t = re.sub(r"\s*[\(\[][^)\]]*(feat|ft\.|remaster|live|version|mix|edit)[^)\]]*[\)\]]", "", title, flags=re.I)
    t = re.sub(r"\s+-\s+.*(remaster|live|version|mix|edit).*$", "", t, flags=re.I)
    return t.strip() or title


def _pick(rows: list[dict], duration: float) -> dict | None:
    usable = [r for r in rows if isinstance(r, dict) and (r.get("syncedLyrics") or r.get("plainLyrics"))]
    if not usable:
        return None

    def rank(r: dict):
        d = r.get("duration")
        gap = abs(float(d) - duration) if d and duration else 0.0
        return (0 if gap <= 3 else 1, 0 if r.get("syncedLyrics") else 1, gap)
    best = min(usable, key=rank)
    if duration and best.get("duration") and abs(float(best["duration"]) - duration) > 4:
        return None                                  # a different recording or a different song
    return best


def from_lrclib(artist: str, title: str, album: str, duration: float, request: Request = http) -> Lyrics | None:
    def get(path: str, **q) -> object:
        url = f"{API}/{path}?" + urllib.parse.urlencode({k: v for k, v in q.items() if v not in ("", None, 0)})
        try:
            return json.loads(request(url) or b"null")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    dur = int(round(duration)) if duration else 0
    row = None
    if artist and title:
        row = get("get", artist_name=artist, track_name=title, album_name=album, duration=dur)
        if not isinstance(row, dict) or not (row.get("syncedLyrics") or row.get("plainLyrics")):
            row = None
    if row is None:
        for name in dict.fromkeys([title, _clean(title)]):
            found = get("search", track_name=name, artist_name=artist)
            row = _pick(found if isinstance(found, list) else [], duration)
            if row:
                break
    if not row:
        return None
    got = parse(row.get("syncedLyrics") or "") or parse(row.get("plainLyrics") or "")
    if got:
        got.source = "lrclib"
        got.duration = float(row.get("duration") or 0)
        print(f"[lyrics] lrclib chose {row.get('artistName')!r} - {row.get('trackName')!r} ({row.get('albumName')!r}), "
              f"{row.get('duration')}s against {duration:g}s here; first line at {got.lines[0].t:g}s", flush=True)
    return got


# ---------------------------------------------------------------- cache and entry point
def _cache_file(artist: str, title: str, album: str) -> Path:
    key = hashlib.sha1(f"{artist}|{title}|{album}".lower().encode()).hexdigest()
    return cache_dir() / "lyrics" / f"{key}.json"


def _load_cached(p: Path) -> tuple[bool, Lyrics | None]:
    """(known, lyrics). known is False when the song has not been looked up (or the 'none' answer is old)."""
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, None
    if not raw.get("lines"):
        return (time.time() - raw.get("at", 0) < NEGATIVE_DAYS * 86400), None
    lines = [Line(float(r["t"]), str(r["text"])) for r in raw["lines"]]
    return True, Lyrics(lines, bool(raw.get("synced")), raw.get("source", "lrclib"), float(raw.get("duration") or 0))


def _store(p: Path, got: Lyrics | None) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"at": time.time(), "synced": bool(got and got.synced), "source": got.source if got else "", "duration": got.duration if got else 0,
                                 "lines": got.as_rows() if got else []}), encoding="utf-8")
    except OSError:
        pass


def lookup(lib: Library, item: Item, online: bool = True, request: Request = http) -> Lyrics | None:
    """Lyrics for a song, from the quickest source that has them. Never raises."""
    try:
        got = from_sidecar(item.path) if item.exists else None
        got = got or (from_tags(item.path) if item.exists else None)
        if got and got.synced:
            return got
        artist, title, album = lib.artist_of(item), item.title, lib.album_of(item)
        cache = _cache_file(artist, title, album)
        known, cached = _load_cached(cache)
        if known:
            return cached if cached and (not got or cached.synced) else got
        if not online:
            return got
        try:
            net = from_lrclib(artist, title, album, item.dur, request)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return got                                   # no connection: try again next time, remember nothing
        _store(cache, net or got)
        return net if net and (not got or net.synced) else got
    except Exception:
        return None


def lookup_track(artist: str, title: str, album: str = "", dur: float = 0.0, online: bool = True, request: Request = http,
                 first: Callable[[], Lyrics | None] | None = None) -> Lyrics | None:
    """Lyrics for a song that is not a file in the library (YouTube Music). `first` (the service's own lyrics) is tried
    before LRCLIB. Results are cached; a failed lookup is not. Never raises."""
    try:
        cache = _cache_file(("ytm4|" if first else "") + artist, title, album)
        known, cached = _load_cached(cache)
        if known:
            return cached
        if not online:
            return None
        net = None
        if first:
            try:
                net = first()
            except Exception as e:
                print(f"[lyrics] the service's own lyrics failed: {e}")
        try:
            if net is None or not net.synced:                # the service had no timing: LRCLIB's timed version is better
                other = from_lrclib(artist, title, album, dur, request)
                if other and (net is None or other.synced):
                    net = other
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None                                  # no connection: try again next time, remember nothing
        _store(cache, net)
        return net
    except Exception:
        return None


def refine(artist: str, title: str, album: str, audio_dur: float, request: Request = http) -> Lyrics | None:
    """Timed lyrics made for a recording about as long as the audio really being played (within a few seconds).
    The listed length of a YouTube Music track can differ from its audio, and lyric timings only fit the recording they
    were made for. Cached by the audio length; never raises."""
    if not (artist and title and audio_dur > 0):
        return None
    try:
        cache = _cache_file("ytm-len|" + artist, title, f"{album}|{round(audio_dur)}")
        known, cached = _load_cached(cache)
        if known:
            return cached
        try:
            got = from_lrclib(artist, title, album, audio_dur, request)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None
        if got and not (got.synced and got.duration and abs(got.duration - audio_dur) <= 4):
            got = None                                       # only a close recording is worth switching to
        _store(cache, got)
        return got
    except Exception:
        return None
