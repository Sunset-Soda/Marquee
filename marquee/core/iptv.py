"""IPTV: M3U playlists, XMLTV guide data, and the store that ties them together.

Everything here is toolkit-free and blocking; call `Store.refresh` off the UI thread.

Playlists are M3U (`#EXTINF` with tvg-id / tvg-name / tvg-logo / group-title). The guide is
XMLTV, optionally gzipped, from the playlist header (`url-tvg`) or a URL the user supplies.
Guide data is filtered while it is parsed (only the channels we have, only roughly now-1h to
now+36h) so a 200 MB national guide ends up as a few hundred KB.
"""
from __future__ import annotations

import bisect
import gzip
import hashlib
import io
import json
import re
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, IO
from xml.etree import ElementTree as ET

from .library import cache_dir, data_dir
from .net import UA

FAV = "__fav__"
ALL = "__all__"

EPG_MAX_AGE = 12 * 3600          # refresh the guide when older than this
PLAYLIST_MAX_AGE = 24 * 3600
WINDOW_BEFORE = 3600.0           # keep programmes ending after now-1h ...
WINDOW_AFTER = 36 * 3600.0       # ... and starting before now+36h


@dataclass
class Channel:
    id: str
    name: str
    url: str
    logo: str = ""
    group: str = ""
    tvg_id: str = ""
    tvg_name: str = ""
    number: int = 0

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "url": self.url, "logo": self.logo,
                "group": self.group, "tvg_id": self.tvg_id, "tvg_name": self.tvg_name,
                "number": self.number}

    @staticmethod
    def from_json(d: dict) -> "Channel":
        return Channel(d["id"], d["name"], d["url"], d.get("logo", ""), d.get("group", ""),
                       d.get("tvg_id", ""), d.get("tvg_name", ""), int(d.get("number", 0)))


@dataclass(frozen=True)
class Programme:
    start: float
    end: float
    title: str
    desc: str = ""
    cat: str = ""


# ----------------------------------------------------------------------------------- M3U

_ATTR = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')


def _split_extinf(line: str) -> tuple[str, str]:
    """'#EXTINF:-1 a="b,c" d="e",Name' -> (attribute text, display name); the comma that ends
    the attributes is the last one outside quotes."""
    body = line.split(":", 1)[1] if ":" in line else ""
    inq = False
    cut = -1
    for i, ch in enumerate(body):
        if ch == '"':
            inq = not inq
        elif ch == "," and not inq:
            cut = i
            break
    if cut < 0:
        return body, ""
    return body[:cut], body[cut + 1:].strip()


def channel_id(url: str) -> str:
    return hashlib.sha1(url.strip().encode("utf-8", "replace")).hexdigest()[:12]


def parse_m3u(text: str) -> tuple[list[Channel], list[str]]:
    """Return (channels, guide URLs from the header). Entries without a URL are dropped, and so
    are repeated URLs."""
    channels: list[Channel] = []
    epg: list[str] = []
    seen: set[str] = set()
    pending: dict | None = None
    group_override = ""
    for raw in text.replace("﻿", "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTM3U"):
            attrs = dict((k.lower(), v) for k, v in _ATTR.findall(line))
            for key in ("url-tvg", "x-tvg-url", "tvg-url"):
                for u in attrs.get(key, "").split(","):
                    u = u.strip()
                    if u and u not in epg:
                        epg.append(u)
            continue
        if line.upper().startswith("#EXTINF"):
            head, name = _split_extinf(line)
            attrs = dict((k.lower(), v) for k, v in _ATTR.findall(head))
            pending = {"attrs": attrs, "name": name}
            continue
        if line.upper().startswith("#EXTGRP:"):
            group_override = line.split(":", 1)[1].strip()
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            continue
        url = line
        attrs = pending["attrs"]
        name = pending["name"] or attrs.get("tvg-name", "") or url
        cid = channel_id(url)
        if cid not in seen:
            seen.add(cid)
            num = attrs.get("tvg-chno") or attrs.get("channel-number") or ""
            channels.append(Channel(
                id=cid, name=name, url=url,
                logo=attrs.get("tvg-logo", ""),
                group=(attrs.get("group-title") or group_override or "").strip(),
                tvg_id=attrs.get("tvg-id", "").strip(),
                tvg_name=attrs.get("tvg-name", "").strip(),
                number=int(num) if num.isdigit() else 0,
            ))
        pending = None
        group_override = ""
    return channels, epg


# -------------------------------------------------------------------------------- XMLTV

_TIME = re.compile(r"^\s*(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?\s*([+-]\d{2}:?\d{2}|Z)?\s*$")
_NOISE = re.compile(r"\b(hd|fhd|uhd|sd|4k|hevc|h265|h264|raw|backup)\b")


def parse_time(s: str) -> float | None:
    """XMLTV '20261115183000 +1300' -> epoch seconds. A missing zone means UTC."""
    m = _TIME.match(s or "")
    if not m:
        return None
    y, mo, d, h, mi, se, tz = m.groups()
    off = timedelta(0)
    if tz and tz != "Z":
        sign = -1 if tz[0] == "-" else 1
        digits = tz[1:].replace(":", "")
        off = sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
    try:
        dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(se or 0),
                      tzinfo=timezone(off))
    except ValueError:
        return None
    return dt.timestamp()


def norm_name(name: str) -> str:
    """Loose channel-name key: 'BBC One HD [UK]' and 'bbc one' compare equal."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[\[\(\{].*?[\]\)\}]", " ", s)
    s = re.sub(r"^\s*[a-z]{2,3}\s*[:|]\s*", "", s)        # 'UK: ' / 'US| ' prefixes
    s = _NOISE.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def open_stream(raw: IO[bytes]) -> IO[bytes]:
    """Hand back a readable stream, transparently un-gzipping."""
    head = raw.read(2)
    rest = _Chain(head, raw)
    if head == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=rest)
    return rest


class _Chain(io.RawIOBase):
    def __init__(self, head: bytes, tail: IO[bytes]):
        self._head = head
        self._tail = tail

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        if self._head:
            n = min(len(b), len(self._head))
            b[:n] = self._head[:n]
            self._head = self._head[n:]
            return n
        data = self._tail.read(len(b))
        b[:len(data)] = data
        return len(data)


def parse_xmltv(stream: IO[bytes], want_ids: set[str], want_names: set[str],
                t_from: float, t_to: float) -> tuple[dict[str, list[Programme]], dict[str, list[str]]]:
    """Stream an XMLTV file. Returns (programmes by guide id, display names by guide id).

    A guide channel is kept when its id is in `want_ids` or one of its display names normalises
    into `want_names`. Programmes outside [t_from, t_to] are dropped as they are read."""
    progs: dict[str, list[Programme]] = {}
    names: dict[str, list[str]] = {}
    verdict: dict[str, bool] = {}

    def keep(cid: str) -> bool:
        v = verdict.get(cid)
        if v is None:
            v = cid in want_ids or any(norm_name(n) in want_names for n in names.get(cid, ()))
            verdict[cid] = v
        return v

    for _ev, el in ET.iterparse(open_stream(stream), events=("end",)):
        tag = el.tag
        if tag == "channel":
            cid = el.get("id", "")
            for dn in el.findall("display-name"):
                if dn.text and dn.text.strip():
                    names.setdefault(cid, []).append(dn.text.strip())
            el.clear()
        elif tag == "programme":
            cid = el.get("channel", "")
            if keep(cid):
                s = parse_time(el.get("start", ""))
                e = parse_time(el.get("stop", ""))
                if s is not None and e is None:
                    e = s + 1800
                if s is not None and e is not None and e > t_from and s < t_to and e > s:
                    t = el.findtext("title") or ""
                    d = el.findtext("desc") or ""
                    c = el.findtext("category") or ""
                    progs.setdefault(cid, []).append(Programme(s, e, t.strip(), d.strip(), c.strip()))
            el.clear()
    for lst in progs.values():
        lst.sort(key=lambda p: p.start)
    return progs, names


# ------------------------------------------------------------------------------- opening

def open_source(src: str, timeout: float = 30.0) -> IO[bytes]:
    """Open a URL, file:// URL or local path as a binary stream."""
    if re.match(r"^https?://", src, re.I):
        req = urllib.request.Request(src, headers={"User-Agent": UA})
        return urllib.request.urlopen(req, timeout=timeout)
    if src.startswith("file://"):
        src = src[7:]
    return open(Path(src).expanduser(), "rb")


def read_text(src: str, opener: Callable[[str], IO[bytes]] = open_source) -> str:
    with opener(src) as f:
        data = open_stream(f).read()
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


# --------------------------------------------------------------------------------- store

@dataclass
class Playlist:
    name: str
    url: str
    epg: str = ""


class Store:
    """Playlists, favourites, the channel list, and the guide."""

    def __init__(self, data: Path | None = None, cache: Path | None = None):
        self._data = (data or data_dir()) / "iptv.json"
        self._cache = cache or (cache_dir() / "iptv")
        self.playlists: list[Playlist] = []
        self.favourites: list[str] = []
        self.channels: list[Channel] = []
        self.by_id: dict[str, Channel] = {}
        self.programmes: dict[str, list[Programme]] = {}   # keyed by guide id
        self.epg_of: dict[str, str] = {}                   # channel id -> guide id
        self.channels_at = 0.0
        self.epg_at = 0.0
        self.error = ""
        self._load_settings()
        self._load_cache()

    # ---- settings

    def _load_settings(self) -> None:
        try:
            d = json.loads(self._data.read_text())
        except (OSError, ValueError):
            return
        self.playlists = [Playlist(p.get("name", ""), p.get("url", ""), p.get("epg", ""))
                          for p in d.get("playlists", []) if p.get("url")]
        self.favourites = [str(x) for x in d.get("favourites", [])]

    def save(self) -> None:
        try:
            self._data.parent.mkdir(parents=True, exist_ok=True)
            self._data.write_text(json.dumps({
                "playlists": [vars(p) for p in self.playlists],
                "favourites": self.favourites,
            }, indent=1))
        except OSError:
            pass

    def add_playlist(self, name: str, url: str, epg: str = "") -> None:
        url = url.strip()
        if not url:
            return
        if any(p.url == url for p in self.playlists):
            for p in self.playlists:
                if p.url == url:
                    p.name = name.strip() or p.name
                    p.epg = epg.strip()
            self.save()
            return
        name = name.strip() or re.sub(r"^https?://([^/]+).*$", r"\1", url)[:40]
        self.playlists.append(Playlist(name, url, epg.strip()))
        self.save()

    def remove_playlist(self, index: int) -> None:
        if 0 <= index < len(self.playlists):
            del self.playlists[index]
            self.save()
            if not self.playlists:
                self.channels, self.by_id, self.programmes, self.epg_of = [], {}, {}, {}
                self._save_cache()

    def toggle_favourite(self, cid: str) -> bool:
        if cid in self.favourites:
            self.favourites.remove(cid)
            on = False
        else:
            self.favourites.append(cid)
            on = True
        self.save()
        return on

    def is_favourite(self, cid: str) -> bool:
        return cid in self.favourites

    # ---- cache

    def _save_cache(self) -> None:
        try:
            self._cache.mkdir(parents=True, exist_ok=True)
            (self._cache / "channels.json").write_text(json.dumps({
                "at": self.channels_at, "channels": [c.to_json() for c in self.channels]}))
            (self._cache / "guide.json").write_text(json.dumps({
                "at": self.epg_at,
                "programmes": {k: [[p.start, p.end, p.title, p.desc, p.cat] for p in v]
                               for k, v in self.programmes.items()},
                "epg_of": self.epg_of}))
        except OSError:
            pass

    def _load_cache(self) -> None:
        try:
            d = json.loads((self._cache / "channels.json").read_text())
            self.channels = [Channel.from_json(c) for c in d.get("channels", [])]
            self.channels_at = float(d.get("at", 0))
            self._index()
        except (OSError, ValueError, KeyError):
            pass
        try:
            d = json.loads((self._cache / "guide.json").read_text())
            self.programmes = {k: [Programme(*row) for row in v]
                               for k, v in d.get("programmes", {}).items()}
            self.epg_of = {k: v for k, v in d.get("epg_of", {}).items() if k in self.by_id}
            self.epg_at = float(d.get("at", 0))
        except (OSError, ValueError, TypeError):
            pass

    def _index(self) -> None:
        self.by_id = {c.id: c for c in self.channels}

    # ---- refresh

    def stale(self, now: float | None = None) -> bool:
        now = now or time.time()
        if not self.playlists:
            return False
        return (now - self.channels_at > PLAYLIST_MAX_AGE or not self.channels
                or now - self.epg_at > EPG_MAX_AGE)

    def refresh(self, force: bool = False, progress: Callable[[str], None] | None = None,
                opener: Callable[[str], IO[bytes]] = open_source, now: float | None = None) -> str:
        """Fetch playlists, then the guide. Returns '' or a short error summary. Blocking."""
        now = now or time.time()
        say = progress or (lambda _m: None)
        errors: list[str] = []
        epg_urls: list[str] = []
        if force or not self.channels or now - self.channels_at > PLAYLIST_MAX_AGE:
            got: list[Channel] = []
            seen: set[str] = set()
            ok = False
            for pl in self.playlists:
                say(f"Loading {pl.name}…")
                try:
                    chans, hdr = parse_m3u(read_text(pl.url, opener))
                except Exception as e:  # network, file, decoding: report and carry on
                    errors.append(f"{pl.name}: {_short(e)}")
                    continue
                ok = True
                for c in chans:
                    if c.id not in seen:
                        seen.add(c.id)
                        got.append(c)
                for u in ([pl.epg] if pl.epg else hdr):
                    if u and u not in epg_urls:
                        epg_urls.append(u)
            if ok:
                self.channels = got
                self._index()
                self.channels_at = now
                force_epg = True
            else:
                force_epg = False
        else:
            force_epg = False
            for pl in self.playlists:
                if pl.epg and pl.epg not in epg_urls:
                    epg_urls.append(pl.epg)
        if not epg_urls:
            # playlist wasn't re-read: fall back to what the settings say, then nothing
            epg_urls = [p.epg for p in self.playlists if p.epg]
        if self.channels and epg_urls and (force or force_epg or now - self.epg_at > EPG_MAX_AGE):
            say("Loading the guide…")
            err = self._load_guide(epg_urls, opener, now)
            if err:
                errors.append(err)
        self._save_cache()
        self.error = "; ".join(errors)
        return self.error

    def _load_guide(self, urls: list[str], opener, now: float) -> str:
        want_ids = {c.tvg_id for c in self.channels if c.tvg_id}
        want_names = {n for c in self.channels for n in (norm_name(c.name), norm_name(c.tvg_name)) if n}
        t0, t1 = now - WINDOW_BEFORE, now + WINDOW_AFTER
        progs: dict[str, list[Programme]] = {}
        names: dict[str, list[str]] = {}
        err = ""
        got = False
        for u in urls:
            try:
                with opener(u) as f:
                    p, n = parse_xmltv(f, want_ids, want_names, t0, t1)
            except Exception as e:
                err = f"Guide: {_short(e)}"
                continue
            got = True
            for k, v in p.items():
                progs.setdefault(k, []).extend(v)
            for k, v in n.items():
                names.setdefault(k, []).extend(v)
        if not got:
            return err
        for v in progs.values():
            v.sort(key=lambda p: p.start)
        self.programmes = progs
        self.epg_of = self._match(progs, names)
        self.epg_at = now
        return ""

    def _match(self, progs: dict[str, list[Programme]], names: dict[str, list[str]]) -> dict[str, str]:
        """channel id -> guide id: exact tvg-id first, then by normalised name."""
        by_name: dict[str, str] = {}
        for gid, ns in names.items():
            if gid not in progs:
                continue
            for n in ns:
                by_name.setdefault(norm_name(n), gid)
        out: dict[str, str] = {}
        for c in self.channels:
            if c.tvg_id and c.tvg_id in progs:
                out[c.id] = c.tvg_id
                continue
            for cand in (c.tvg_name, c.name):
                k = norm_name(cand)
                if k and k in by_name:
                    out[c.id] = by_name[k]
                    break
        return out

    # ---- queries

    def groups(self) -> list[tuple[str, str, int]]:
        """[(key, label, count)] — favourites and All first, then provider groups A→Z."""
        counts: dict[str, int] = {}
        for c in self.channels:
            counts[c.group or "Other"] = counts.get(c.group or "Other", 0) + 1
        favs = sum(1 for c in self.channels if c.id in self.favourites)
        out = [(ALL, "All channels", len(self.channels))]
        if favs:
            out.insert(0, (FAV, "Favourites", favs))
        out += [(g, g, n) for g, n in sorted(counts.items(), key=lambda kv: kv[0].lower())]
        return out

    def select(self, group: str = ALL, query: str = "") -> list[Channel]:
        if group == FAV:
            fav = set(self.favourites)
            rows = [c for c in self.channels if c.id in fav]
        elif group in ("", ALL):
            rows = list(self.channels)
        else:
            rows = [c for c in self.channels if (c.group or "Other") == group]
        q = query.strip().lower()
        if q:
            rows = [c for c in rows if q in c.name.lower()]
        return rows

    def programmes_for(self, cid: str) -> list[Programme]:
        return self.programmes.get(self.epg_of.get(cid, ""), [])

    def between(self, cid: str, t0: float, t1: float) -> list[Programme]:
        return [p for p in self.programmes_for(cid) if p.end > t0 and p.start < t1]

    def now_next(self, cid: str, t: float | None = None) -> tuple[Programme | None, Programme | None]:
        t = time.time() if t is None else t
        rows = self.programmes_for(cid)
        if not rows:
            return None, None
        i = bisect.bisect_right([p.start for p in rows], t) - 1
        cur = rows[i] if i >= 0 and rows[i].end > t else None
        j = i + 1 if cur else bisect.bisect_left([p.start for p in rows], t)
        nxt = rows[j] if j < len(rows) else None
        return cur, nxt

    def neighbour(self, cid: str, step: int, group: str = ALL, query: str = "") -> Channel | None:
        rows = self.select(group, query) or self.channels
        if not rows:
            return None
        for i, c in enumerate(rows):
            if c.id == cid:
                return rows[(i + step) % len(rows)]
        return rows[0]


def _short(e: Exception) -> str:
    s = str(e) or e.__class__.__name__
    return s if len(s) < 90 else s[:87] + "…"
