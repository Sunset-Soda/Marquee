"""YouTube without an account, the FreeTube way.

Searching, channel pages and stream addresses come from `yt-dlp` (a separate program that has to be installed). Your
subscriptions are kept on this computer, and their latest videos come from each channel's public RSS feed, so the feed
needs neither yt-dlp nor a login. An optional "Recommended" list uses the login your own browser already has, passed to
yt-dlp with --cookies-from-browser; that is off unless you choose a browser.

Nothing here knows about Qt. Anything that runs a program or opens the network takes an injectable function so it can be
tested.
"""
from __future__ import annotations

import json
import re
import sys
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from .gvfetch import open_stream
from .library import cache_dir, data_dir

# YouTube's feeds and pages refuse unfamiliar clients, so ask like a browser would.
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# One stream that has the picture and the sound together; Marquee's player can't join two streams. Usually up to 720p.
FORMAT = ("best[height<=720][protocol=https][vcodec!=none][acodec!=none]/best[height<=720][vcodec!=none][acodec!=none]"
          "/best[protocol=https][vcodec!=none][acodec!=none]/best[vcodec!=none][acodec!=none]/best")
# Live streams: the best picture and sound together (HLS offers 1080p60 as itag 301 next to 720p60 as 300), no 720 cap.
LIVE_FORMAT = "best[protocol=m3u8_native][vcodec!=none][acodec!=none]/best[vcodec!=none][acodec!=none]/best"
# Ways of asking YouTube, in the order tried: yt-dlp's own choice first, then clients that need no special token.
CLIENTS = ["", "android_vr", "tv", "mweb", "ios", "web_safari"]
# Separate picture and sound streams (what the website plays), joined by yt-dlp when the old single-file format is a stub.
# Sorted by "res" (the shorter side of the picture), so a vertical video's 480 means 480 wide, not 480 tall.
DASH_FORMAT = "bv*[vcodec^=avc1]+ba[ext=m4a]/bv*+ba/b"
DASH_SORT = "res:480,vcodec:h264,acodec:aac"
# Sound only, for YouTube Music. m4a (AAC) first because every Qt audio backend plays it; Opus otherwise.
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio/best"


QUALITIES = ["auto", "1080", "720", "480", "360"]
DEFAULT_SPEED = 3_000_000                    # bytes/second assumed until we have measured yours
AUTO_BUDGET = 20.0                           # seconds Auto is willing to wait for a download


def levels_from(d: dict) -> list[dict]:
    """The picture sizes a video comes in, as [{res, size}] best first. res is the shorter side of the picture (so a
    vertical 480x854 video is 480), size the bytes of picture plus sound."""
    fmts = d.get("formats") or []
    dur = float(d.get("duration") or 0)

    def size(f: dict) -> int:
        n = f.get("filesize") or f.get("filesize_approx")
        if n:
            return int(n)
        br = f.get("tbr") or f.get("vbr") or f.get("abr") or 0
        return int(br * 125 * dur) if br and dur else 0
    sound = [f for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")
             and str(f.get("protocol") or "").startswith("http")]
    sound.sort(key=lambda f: (f.get("ext") == "m4a", f.get("abr") or 0), reverse=True)
    audio = size(sound[0]) if sound else int(dur * 16000)
    best: dict[int, tuple[bool, int]] = {}
    for f in fmts:
        if f.get("vcodec") in (None, "none") or f.get("acodec") not in (None, "none"):
            continue
        if not str(f.get("protocol") or "").startswith("http"):
            continue
        w, h = f.get("width") or 0, f.get("height") or 0
        res = min(w, h) if w and h else h
        if not res or res < 144:
            continue
        avc = str(f.get("vcodec") or "").startswith("avc1")
        cand = (avc, size(f))
        if res not in best or (cand[0], -cand[1]) > (best[res][0], -best[res][1]):
            best[res] = cand
    return [{"res": r, "size": v[1] + audio} for r, v in sorted(best.items(), reverse=True)]


def choose_level(quality: str, levels: list[dict], speed: float = 0.0, budget: float = AUTO_BUDGET) -> int:
    """The picture size to play. 360 (or less) means stream it now; more means fetch the streams first."""
    res = [l for l in levels if l["res"] <= 1080]
    if not res:
        return 360
    if str(quality).isdigit():
        want = int(quality)
        ok = [l for l in res if l["res"] <= want]
        return (max(ok, key=lambda l: l["res"]) if ok else min(res, key=lambda l: l["res"]))["res"]
    allowed = (speed or DEFAULT_SPEED) * budget
    ok = [l["res"] for l in res if l["size"] and l["size"] <= allowed and l["res"] >= 360]
    return max(ok, default=360)


class StubStream(RuntimeError):
    """YouTube answered with a few hundred KB instead of the video."""


MIN_BYTES_PER_SECOND = 8000                  # a real video with sound is never thinner than this
BROWSERS = ["firefox", "chromium", "chrome", "brave", "edge", "opera", "vivaldi", "librewolf"]
LIVE_FILTER = "EgJAAQ%253D%253D"            # ... and for streams that are live right now
PLAYLIST_FILTER = "EgIQAw%253D%253D"       # ... and for playlists
CHANNEL_FILTER = "EgIQAg%253D%253D"        # YouTube's search filter for channels


def js_runtime_args() -> list[str]:
    """yt-dlp only looks for deno by default; if node or bun is what's installed, point it there."""
    if shutil.which("deno"):
        return []
    for rt in ("node", "bun"):
        if shutil.which(rt):
            return ["--js-runtimes", rt]
    return []


def has_js_runtime() -> bool:
    return bool(shutil.which("deno") or shutil.which("node") or shutil.which("bun"))


# Where each browser keeps its profile, besides the usual place: Flatpak and Snap installs live elsewhere.
_PROFILES = {
    "firefox": ["~/.mozilla/firefox", "~/.var/app/org.mozilla.firefox/.mozilla/firefox", "~/snap/firefox/common/.mozilla/firefox"],
    "librewolf": ["~/.librewolf", "~/.var/app/io.gitlab.librewolf-community/.librewolf"],
    "chromium": ["~/.config/chromium", "~/.var/app/org.chromium.Chromium/config/chromium", "~/snap/chromium/common/chromium"],
    "chrome": ["~/.config/google-chrome", "~/.var/app/com.google.Chrome/config/google-chrome"],
    "brave": ["~/.config/BraveSoftware/Brave-Browser", "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"],
    "edge": ["~/.config/microsoft-edge", "~/.var/app/com.microsoft.Edge/config/microsoft-edge"],
    "opera": ["~/.config/opera", "~/.var/app/com.opera.Opera/config/opera"],
    "vivaldi": ["~/.config/vivaldi", "~/.config/vivaldi-snapshot", "~/.var/app/com.vivaldi.Vivaldi/config/vivaldi"],
}


def cookie_spec(browser: str) -> str:
    """What to give yt-dlp's --cookies-from-browser: the browser, plus the profile folder when it isn't in the usual place."""
    for d in _PROFILES.get(browser, []):
        root = Path(d).expanduser()
        if root.is_dir() and (next(root.rglob("Cookies"), None) or next(root.rglob("cookies.sqlite"), None)):
            return f"{browser}:{root}"
    return browser


def find_ytdlp() -> str:
    return shutil.which("yt-dlp") or ""


# ---------------------------------------------------------------------------------------------- shaping data

_URL = re.compile(r"https?://[^\s<>\"']+")
_STAMP = re.compile(r"(?<![\w:/.#=&?-])((?:\d{1,2}:)?\d{1,2}:\d{2})(?![\w:])")


def stamp_seconds(text: str) -> int:
    """\"1:02:03\" or \"12:34\" -> seconds."""
    n = 0
    for part in text.split(":"):
        n = n * 60 + int(part)
    return n


def describe_html(text: str) -> str:
    """A description or comment as the small HTML the player shows: web addresses become links, and times like 12:34 become
    seek:<seconds> links that jump the video there."""
    import html
    out = []
    pos = 0
    for m in _URL.finditer(text or ""):
        out.append(_stamps(html.escape(text[pos:m.start()])))
        url = m.group(0).rstrip(".,;:!?)")
        out.append(f'<a href="{html.escape(url)}">{html.escape(url)}</a>' + html.escape(m.group(0)[len(url):]))
        pos = m.end()
    out.append(_stamps(html.escape((text or "")[pos:])))
    return "".join(out).replace("\r\n", "\n").replace("\n", "<br>")


def _stamps(escaped: str) -> str:
    return _STAMP.sub(lambda m: f'<a href="seek:{stamp_seconds(m.group(1))}">{m.group(1)}</a>', escaped)


def chapters_from(d: dict) -> list[dict]:
    """[{title, start}] from yt-dlp's chapter list (empty for videos without chapters and for live streams)."""
    out = []
    for c in d.get("chapters") or []:
        try:
            out.append({"title": str(c.get("title") or "").strip() or "Chapter", "start": float(c.get("start_time") or 0)})
        except (TypeError, ValueError):
            continue
    return out


def comments_from(d: dict, limit: int = 60) -> list[dict]:
    """Top-level comments from yt-dlp's JSON, pinned first, then in the order YouTube gave them."""
    rows = []
    for c in d.get("comments") or []:
        if c.get("parent") not in (None, "root"):
            continue
        rows.append({"id": c.get("id") or "", "author": (c.get("author") or "").lstrip("@") or "Someone",
                     "html": describe_html(c.get("text") or ""), "likes": int(c.get("like_count") or 0),
                     "ago": c.get("_time_text") or "", "pinned": bool(c.get("is_pinned")),
                     "uploader": bool(c.get("author_is_uploader")), "thumb": c.get("author_thumbnail") or ""})
    rows.sort(key=lambda r: not r["pinned"])
    return rows[:limit]


def upload_text(d: dict) -> str:
    """\"20261112\" -> \"12 Nov 2026\"."""
    s = str(d.get("upload_date") or "")
    if len(s) != 8 or not s.isdigit():
        return ""
    import datetime
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:])).strftime("%-d %b %Y")


def captions_from(d: dict) -> list[dict]:
    """The caption tracks a video offers: [{id, lang, auto, label}]. Captions the uploader made come first; of the
    auto-generated ones only the video's own spoken language is listed (YouTube also offers dozens of machine translations)."""
    out = []
    for lang, files in (d.get("subtitles") or {}).items():
        if lang == "live_chat" or not files:
            continue
        name = next((f.get("name") for f in files if f.get("name")), "") or lang
        out.append({"id": f"s:{lang}", "lang": lang, "auto": False, "label": name})
    auto = d.get("automatic_captions") or {}
    own = [k for k in auto if k.endswith("-orig")]
    if not own and d.get("language") and d["language"] in auto:
        own = [d["language"]]
    for lang in own:
        files = auto[lang]
        name = next((f.get("name") for f in files if f.get("name")), "") or lang
        name = re.sub(r"\s*\(?(?:auto-?generated|original)\)?\s*$", "", name, flags=re.I).strip() or lang
        out.append({"id": f"a:{lang}", "lang": lang, "auto": True, "label": f"{name} (auto-generated)"})
    return out


def unescape_caption(text: str) -> str:
    """YouTube's caption files write some characters as codes (&gt;&gt; for the >> speaker mark, &amp;, &#39;, &nbsp;): turn them
    back into the characters, and drop invisible ones that show up as boxes."""
    import html
    text = html.unescape(html.unescape(text)).replace("\u00a0", " ")
    return re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", text)


def rolling_to_lines(cues: list) -> list:
    """YouTube's auto-generated captions scroll: every cue repeats the line before it, with the new words tagged on. Keep each
    cue's newest line once, shown until the next one arrives."""
    from .subtitles import Cue
    out: list = []
    for c in cues:
        lines = [ln.strip() for ln in (c.text or "").split("\n") if ln.strip()]
        if not lines or lines[-1] == (out[-1].text if out else None):
            continue
        out.append(Cue(c.start, c.end, lines[-1]))
    for a, b in zip(out, out[1:]):
        a.end = min(max(a.end, a.start + 1.0), b.start) if b.start > a.start else a.end
    return out


def thumb_for(vid: str) -> str:
    return f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"


def norm_video(d: dict) -> dict | None:
    """A yt-dlp (flat) entry -> {id, title, channel, channelId, thumb, dur, views, published}. None for non-videos."""
    vid = d.get("id") or ""
    if not re.fullmatch(r"[\w-]{11}", vid):
        url = d.get("url") or ""
        m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url)
        vid = m.group(1) if m else ""
    if not vid:
        return None
    thumbs = d.get("thumbnails") or []
    thumb = d.get("thumbnail") or next((t.get("url") for t in reversed(thumbs) if t.get("url", "").startswith("http")), "")
    ts = d.get("timestamp") or 0
    if not ts and d.get("upload_date"):
        try:
            ts = time.mktime(time.strptime(d["upload_date"], "%Y%m%d"))
        except ValueError:
            ts = 0
    return {"id": vid, "title": d.get("title") or "", "channel": d.get("channel") or d.get("uploader") or "",
            "channelId": d.get("channel_id") or d.get("uploader_id") or "", "thumb": thumb or thumb_for(vid),
            "dur": float(d.get("duration") or 0), "views": int(d.get("view_count") or 0), "published": float(ts),
            "live": bool(d.get("is_live") or d.get("live_status") == "is_live")}


def norm_channel(d: dict) -> dict | None:
    cid = d.get("channel_id") or d.get("id") or ""
    if not cid.startswith("UC"):
        m = re.search(r"/channel/(UC[\w-]+)", d.get("url") or "")
        cid = m.group(1) if m else ""
    if not cid:
        return None
    thumbs = d.get("thumbnails") or []
    thumb = next((t.get("url") for t in reversed(thumbs) if t.get("url", "").startswith(("http", "//"))), "")
    if thumb.startswith("//"):
        thumb = "https:" + thumb
    return {"id": cid, "name": d.get("title") or d.get("channel") or d.get("uploader") or cid, "thumb": thumb,
            "subs": int(d.get("channel_follower_count") or 0)}


def norm_playlist(d: dict) -> dict | None:
    """A yt-dlp (flat) entry from a channel's Playlists tab -> {id, title, thumb, count}."""
    pid = d.get("id") or ""
    m = re.search(r"[?&]list=([\w-]+)", d.get("url") or "")
    if m:
        pid = m.group(1)
    if not re.fullmatch(r"(PL|OL|UU|FL|LL|RD)[\w-]+", pid):
        return None
    thumbs = d.get("thumbnails") or []
    thumb = d.get("thumbnail") or next((t.get("url") for t in reversed(thumbs) if t.get("url", "").startswith("http")), "")
    return {"id": pid, "title": d.get("title") or "Playlist", "thumb": thumb,
            "count": int(d.get("playlist_count") or d.get("n_entries") or 0),
            "channel": d.get("channel") or d.get("uploader") or ""}


_DASH_GUARD = threading.Lock()
_DASH_BUSY: dict[str, threading.Lock] = {}      # "<id>-<size>p" -> held while that file is being downloaded


def dash_in_use(f: Path) -> bool:
    """True for a file that is being downloaded right now (the cache clean-up must leave it alone)."""
    with _DASH_GUARD:
        return any(lock.locked() and f.name.startswith(key) for key, lock in _DASH_BUSY.items())


def clear_cache(keep: str = "") -> tuple[int, int]:
    """Delete the saved videos (except the file named `keep`). Returns (files removed, bytes freed)."""
    n = freed = 0
    folder = cache_dir() / "youtube"
    for f in folder.glob("*") if folder.exists() else []:
        if str(f) == keep or not f.is_file() or dash_in_use(f):
            continue
        try:
            size = f.stat().st_size
            f.unlink()
            n, freed = n + 1, freed + size
        except OSError:
            pass
    return n, freed


def cache_size() -> int:
    folder = cache_dir() / "youtube"
    return sum(f.stat().st_size for f in folder.glob("*") if f.is_file()) if folder.exists() else 0


def parse_url(text: str) -> tuple[str, str]:
    """A pasted YouTube address -> ("video", id) | ("playlist", id) | ("channel", id or handle address) | ("", "")."""
    from urllib.parse import parse_qs, urlparse
    t = (text or "").strip()
    if not re.match(r"^(https?://)?([\w-]+\.)*(youtube\.com|youtu\.be|youtube-nocookie\.com)/", t + "/" if "/" not in t else t):
        return "", ""
    u = urlparse(t if "://" in t else "https://" + t)
    host, path, q = u.netloc.lower(), u.path, parse_qs(u.query)
    if host.endswith("youtu.be"):
        m = re.fullmatch(r"/([\w-]{11})", path)
        return ("video", m.group(1)) if m else ("", "")
    m = re.fullmatch(r"/(?:shorts|live|embed|v)/([\w-]{11})", path)
    if m:
        return "video", m.group(1)
    if path == "/watch" and re.fullmatch(r"[\w-]{11}", (q.get("v") or [""])[0]):
        return "video", q["v"][0]
    if path == "/playlist" and q.get("list"):
        return "playlist", q["list"][0]
    m = re.fullmatch(r"/channel/(UC[\w-]+)(?:/.*)?", path)
    if m:
        return "channel", m.group(1)
    m = re.fullmatch(r"(/(?:@[^/]+|c/[^/]+|user/[^/]+))(?:/.*)?", path)
    if m:
        return "channel", "https://www.youtube.com" + m.group(1)
    return "", ""


def entries(payload) -> list[dict]:
    return [e for e in (payload or {}).get("entries", []) if isinstance(e, dict)]


def clock(sec: float) -> str:
    s = int(sec)
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def ago(ts: float, now: float | None = None) -> str:
    if not ts:
        return ""
    d = max(0.0, (now if now is not None else time.time()) - ts)
    for unit, secs in (("year", 31536000), ("month", 2592000), ("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if d >= secs:
            n = int(d // secs)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def short_count(n: int) -> str:
    for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= div:
            v = n / div
            return (f"{v:.1f}".rstrip("0").rstrip(".") if v < 10 else f"{v:.0f}") + suf
    return str(n)


# ---------------------------------------------------------------------------------------------- RSS feeds

_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
       "m": "http://search.yahoo.com/mrss/"}


def _iso(s: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def parse_rss(data: bytes) -> list[dict]:
    """A channel's public feed (the 15 latest videos) -> video dicts."""
    out = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return out
    cid = root.findtext("yt:channelId", default="", namespaces=_NS)
    for e in root.findall("a:entry", _NS):
        vid = e.findtext("yt:videoId", default="", namespaces=_NS)
        if not vid:
            continue
        stats = e.find("m:group/m:community/m:statistics", _NS)
        thumb = e.find("m:group/m:thumbnail", _NS)
        out.append({"id": vid, "title": e.findtext("a:title", default="", namespaces=_NS),
                    "channel": e.findtext("a:author/a:name", default="", namespaces=_NS), "channelId": cid,
                    "thumb": (thumb.get("url") if thumb is not None else "") or thumb_for(vid), "dur": 0.0,
                    "views": int(stats.get("views", 0)) if stats is not None else 0,
                    "published": _iso(e.findtext("a:published", default="", namespaces=_NS)), "live": False})
    return out


def fetch_feed(channel_id: str, opener: Callable[[str], bytes] | None = None) -> list[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    if opener:
        return parse_rss(opener(url))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_rss(r.read())


def parse_avatar(html: str) -> str:
    """A channel page's picture: its og:image."""
    m = (re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
         or re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', html))
    return m.group(1).replace("&amp;", "&") if m else ""


def fetch_avatar(channel_id: str, opener: Callable[[str], str] | None = None) -> str:
    url = f"https://www.youtube.com/channel/{channel_id}"
    if opener:
        return parse_avatar(opener(url))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en", "Cookie": "CONSENT=YES+1; SOCS=CAI"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_avatar(r.read(400_000).decode("utf-8", "replace"))


def merge_feed(feeds: list[list[dict]], limit: int = 150) -> list[dict]:
    """Latest first, across channels, each video once."""
    seen: set[str] = set()
    rows = []
    for f in feeds:
        for v in f:
            if v["id"] not in seen:
                seen.add(v["id"])
                rows.append(v)
    rows.sort(key=lambda v: v["published"], reverse=True)
    return rows[:limit]


def subscription_feed(subs: list[dict], opener: Callable[[str], bytes] | None = None,
                      errors: list | None = None, failed: list | None = None) -> list[dict]:
    """The latest videos of every subscribed channel. A channel that fails to load is skipped (and noted in `errors`)."""
    def one(s):
        try:
            return fetch_feed(s["id"], opener)
        except Exception as e:
            if errors is not None:
                errors.append(f"{s.get('name', s['id'])}: {e}")
            if failed is not None:
                failed.append(s)
            return []
    if not subs:
        return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        return merge_feed(list(pool.map(one, subs)))


def interleave(lists: list[list[dict]], limit: int = 150) -> list[dict]:
    """Newest-first lists from several channels woven together: every channel's latest, then every second-latest..."""
    seen: set[str] = set()
    rows: list[dict] = []
    for i in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if i < len(x) and x[i]["id"] not in seen:
                seen.add(x[i]["id"])
                rows.append(x[i])
    return rows[:limit]


# ---------------------------------------------------------------------------------------------- yt-dlp

Runner = Callable[[list], str]


def _run(args: list, timeout: float = 90.0) -> str:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 and not p.stdout.strip():
        msg = (p.stderr.strip().splitlines() or ["yt-dlp failed"])[-1]
        raise RuntimeError(msg[:200])
    return p.stdout


class YtDlp:
    def __init__(self, exe: str = "", runner: Runner | None = None, browser: str = ""):
        self.exe = exe or find_ytdlp() or "yt-dlp"
        self._run = runner or _run
        self.browser = browser

    def _json(self, *args: str, cookies: bool = False):
        cmd = [self.exe, "--no-warnings", "--ignore-no-formats-error", *args]
        if cookies and self.browser:
            cmd[1:1] = ["--cookies-from-browser", cookie_spec(self.browser)]
        out = self._run(cmd)
        return json.loads(out) if out.strip() else {}

    def caption_cues(self, video_id: str, lang: str, auto: bool) -> list:
        """Download one caption track with yt-dlp (into the cache folder) and return its cues."""
        from . import subtitles
        folder = cache_dir() / "captions"
        folder.mkdir(parents=True, exist_ok=True)
        for old in sorted(folder.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)[40:]:
            try:
                old.unlink()
            except OSError:
                pass
        want = folder / f"{video_id}.{lang}.vtt"
        if not want.exists() or want.stat().st_size < 20:
            cmd = [self.exe, "--skip-download", "--no-playlist", "--force-ipv4", "-q", "--no-warnings",
                   "--write-auto-subs" if auto else "--write-subs", "--sub-langs", lang, "--sub-format", "vtt",
                   "-o", str(folder / "%(id)s.%(ext)s"), f"https://www.youtube.com/watch?v={video_id}"]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except (OSError, subprocess.SubprocessError) as e:
                raise RuntimeError(f"Couldn’t run yt-dlp: {e}")
            if not want.exists():
                msg = (p.stderr.strip().splitlines() or ["YouTube didn’t give out that caption file."])[-1]
                raise RuntimeError(msg[:160])
        cues = subtitles.parse(want.read_text(encoding="utf-8", errors="replace"), "x.vtt")
        for c in cues:
            c.text = unescape_caption(c.text)
        if auto:
            cues = rolling_to_lines(cues)
        if not cues:
            raise RuntimeError("That caption track is empty.")
        return cues

    def comments(self, video_id: str, n: int = 40) -> list[dict]:
        """The top comments of a video (no replies). A separate, slower request, so it is made only when they are wanted."""
        d = self._json("--dump-single-json", "--no-playlist", "--skip-download", "--write-comments", "--force-ipv4",
                       "--extractor-args", f"youtube:max_comments={n},{n},0,0;comment_sort=top",
                       f"https://www.youtube.com/watch?v={video_id}")
        return comments_from(d, n)

    def search(self, query: str, n: int = 24) -> list[dict]:
        d = self._json("--flat-playlist", "--dump-single-json", f"ytsearch{n}:{query}")
        return [v for v in (norm_video(e) for e in entries(d)) if v]

    def search_live(self, query: str, n: int = 24) -> list[dict]:
        from urllib.parse import quote_plus
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={LIVE_FILTER}"
        d = self._json("--flat-playlist", "--dump-single-json", "--playlist-end", str(n), url)
        out = [v for v in (norm_video(e) for e in entries(d)) if v]
        for v in out:
            v["live"] = True
        return out

    def search_channels(self, query: str, n: int = 12) -> list[dict]:
        from urllib.parse import quote_plus
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={CHANNEL_FILTER}"
        d = self._json("--flat-playlist", "--dump-single-json", "--playlist-end", str(n), url)
        return [c for c in (norm_channel(e) for e in entries(d)) if c]

    def search_playlists(self, query: str, n: int = 24) -> list[dict]:
        from urllib.parse import quote_plus
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={PLAYLIST_FILTER}"
        d = self._json("--flat-playlist", "--dump-single-json", "--playlist-end", str(n), url)
        seen, out = set(), []
        for e in entries(d):
            p = norm_playlist(e)
            if p and p["id"] not in seen:
                seen.add(p["id"])
                out.append(p)
        return out

    def channel(self, channel_id: str, n: int = 36) -> dict:
        """{'name', 'videos'} for a channel's uploads."""
        d = self._json("--flat-playlist", "--dump-single-json", "--playlist-end", str(n),
                       f"https://www.youtube.com/channel/{channel_id}/videos")
        vids = [v for v in (norm_video(e) for e in entries(d)) if v]
        name = d.get("channel") or d.get("uploader") or d.get("title") or ""
        name = re.sub(r"\s*-\s*Videos$", "", name)
        for v in vids:
            v["channel"] = v["channel"] or name
            v["channelId"] = v["channelId"] or channel_id
        return {"name": name, "videos": vids}

    def resolve_channel(self, address: str) -> dict:
        """{'id', 'name'} for a channel address like https://www.youtube.com/@handle."""
        d = self._json("--flat-playlist", "--dump-single-json", "--playlist-end", "1", address + "/videos")
        cid = d.get("channel_id") or d.get("uploader_id") or ""
        if not cid.startswith("UC"):
            raise RuntimeError("Couldn’t find that channel.")
        name = re.sub(r"\s*-\s*Videos$", "", d.get("channel") or d.get("uploader") or d.get("title") or "")
        return {"id": cid, "name": name}

    def channel_playlists(self, channel_id: str, n: int = 60) -> list[dict]:
        d = self._json("--flat-playlist", "--dump-single-json", "--playlist-end", str(n),
                       f"https://www.youtube.com/channel/{channel_id}/playlists")
        seen, out = set(), []
        for e in entries(d):
            p = norm_playlist(e)
            if p and p["id"] not in seen:
                seen.add(p["id"])
                out.append(p)
        return out

    def playlist(self, playlist_id: str, n: int = 200) -> dict:
        """{'title', 'videos'} for one playlist."""
        d = self._json("--flat-playlist", "--dump-single-json", "--playlist-end", str(n),
                       f"https://www.youtube.com/playlist?list={playlist_id}")
        vids = [v for v in (norm_video(e) for e in entries(d)) if v]
        return {"title": d.get("title") or "Playlist", "videos": vids}

    def recommended(self, n: int = 36) -> list[dict]:
        """The signed-in home feed. Needs a browser chosen; raises when there isn't one."""
        if not self.browser:
            raise RuntimeError("Choose the browser you are signed into YouTube with, in Settings.")
        try:
            d = self._json("--flat-playlist", "--dump-single-json", "--playlist-end", str(n), ":ytrec", cookies=True)
        except RuntimeError as e:
            if "cookie" in str(e).lower():
                raise RuntimeError(f"Marquee couldn’t read {self.browser}’s login ({str(e)[:120]}). "
                                   "Is YouTube signed in there, and is it a normal (not Flatpak/Snap) install? "
                                   "Try another browser in Settings.")
            raise
        return [v for v in (norm_video(e) for e in entries(d)) if v]

    def _shape(self, d: dict, video_id: str) -> dict:
        url = d.get("url") or ""
        if not url:
            fmts = [f for f in (d.get("formats") or []) if f.get("url") and f.get("vcodec") != "none" and f.get("acodec") != "none"]
            url = fmts[-1]["url"] if fmts else d.get("manifest_url") or ""
        import sys
        print(f"[youtube] {video_id}: client={self._client or 'default'} format={d.get('format_id')} {d.get('ext')} {d.get('protocol')} "
              f"{d.get('height')}p host={url.split('/')[2] if '//' in url else ''}", file=sys.stderr, flush=True)
        if not url:
            raise RuntimeError(d.get("reason") or "YouTube gave no playable stream for this video.")
        return {"id": video_id, "title": d.get("title") or "", "channel": d.get("channel") or d.get("uploader") or "",
                "url": url, "headers": d.get("http_headers") or {}, "dur": float(d.get("duration") or 0),
                "thumb": d.get("thumbnail") or thumb_for(video_id), "live": bool(d.get("is_live")),
                "size": int(d.get("filesize") or 0), "size_hint": int(d.get("filesize") or d.get("filesize_approx") or 0),
                "levels": levels_from(d), "res": (min(d["width"], d["height"]) if d.get("width") and d.get("height") else 360),
                "desc": describe_html(d.get("description") or ""), "chapters": chapters_from(d),
                "views": int(d.get("view_count") or 0), "uploaded": upload_text(d), "captions": captions_from(d)}

    _client = ""
    keep = 3                                    # how many fetched videos stay in the cache
    retry_wait = 3.5
    progress: Callable[[str], None] | None = None

    def _attempt(self, video_id: str, client: str, with_login: bool, fmt: str = FORMAT) -> dict:
        self._client = client + ("+login" if with_login else "")
        extra = ["--force-ipv4", *js_runtime_args()] + (["--extractor-args", f"youtube:player_client={client}"] if client else [])
        d = self._json("--dump-single-json", "--no-playlist", "-f", fmt, *extra,
                       f"https://www.youtube.com/watch?v={video_id}", cookies=with_login)
        return self._shape(d, video_id)

    def dash_file(self, video_id: str, res: int) -> Path:
        return cache_dir() / "youtube" / f"{video_id}-{res}p.mp4"

    def dash_cached(self, video_id: str, res: int) -> bool:
        f = self.dash_file(video_id, res)
        return f.exists() and f.stat().st_size >= 1_000_000

    def resolve(self, video_id: str, probe: Callable[[dict], bool] | None = None, quality: str = "auto",
                speed: float = 0.0, fast: bool = False, on_upgrade: Callable[[dict], None] | None = None,
                uncapped: bool = False, clients: list[str] | None = None) -> dict:
        """What the player needs for one video: {id, title, channel, url, headers, dur, thumb, live, levels, res}.

        fast: when a bigger size is wanted and isn't already saved, start streaming 360p now and hand back
        info["upgrade"] = {"res", "meta"} so the caller can fetch the bigger one in the background (fetch_dash).

        quality: "auto" or a size ("1080", "720", "480", "360"). 360 streams straight away; bigger sizes are fetched as
        separate picture and sound streams by yt-dlp first. YouTube hands some clients addresses that then answer 403
        (they need a token only a real player has), so each way of asking is tried in turn and every address is
        test-fetched before it is used.
        """
        probe = probe or probe_stream
        meta: dict | None = None
        from .gvfetch import curl_available
        print(f"[youtube] fetching streams with {'curl_cffi (browser fingerprint)' if curl_available() else 'plain urllib (curl_cffi not found)'}",
              file=sys.stderr, flush=True)
        last: Exception | None = None
        waited = False
        first: dict | None = None
        first_err: Exception | None = None
        try:
            if clients:
                raise RuntimeError("skipped")                 # only the asked-for clients are tried (a quick refresh)
            first = self._attempt(video_id, "", False, LIVE_FORMAT if uncapped else FORMAT)
        except Exception as e:
            first_err = e
        want = 360
        quick: dict | None = None
        if first and not first["live"]:
            want = choose_level(quality, first.get("levels") or [], speed, budget=AUTO_BUDGET * (2 if fast else 1))
            print(f"[youtube] {video_id}: quality {quality}: levels {[l['res'] for l in first.get('levels') or []]} -> {want}p",
                  file=sys.stderr, flush=True)
            if want > 360 and fast and not self.dash_cached(video_id, want):
                quick = {"res": want, "meta": dict(first)}
                print(f"[youtube] {video_id}: starting at 360p; {want}p will follow", file=sys.stderr, flush=True)
                if on_upgrade:
                    on_upgrade(quick)               # begin the bigger download now, while the 360p address is still being checked
            elif want > 360:
                try:
                    return self.fetch_dash(video_id, first, want)
                except RuntimeError as e:
                    print(f"[youtube] {video_id}: {want}p not available ({str(e)[:100]}); streaming instead", file=sys.stderr, flush=True)

        def check(info: dict) -> bool:
            # yt-dlp itself pauses ~3 s before the first download ("as required by the site"). An address asked for too
            # soon can answer 403, so give the first refusal one pause and a second try before moving on.
            nonlocal waited
            if probe(info):
                return True
            if waited or self.retry_wait <= 0:
                return False
            waited = True
            print(f"[youtube] {video_id}: refused; checking again for up to {self.retry_wait:g}s", file=sys.stderr, flush=True)
            return self._probe_again(probe, info)
        # Without your login first: some clients (android_vr) work best anonymously and stop working when cookies are sent.
        # Then, if you chose a browser, the same clients again with its login.
        for with_login in ([False, True] if self.browser else [False]):
            for client in (clients or CLIENTS):
                try:
                    if client == "" and not with_login:
                        if first_err:
                            raise first_err
                        info = first
                        self._client = ""
                    else:
                        info = self._attempt(video_id, client, with_login, LIVE_FORMAT if uncapped else FORMAT)
                        if info["live"] and not uncapped and quality in ("auto", "1080") and info["res"] < 1080:
                            try:                                  # a live stream: look again without the 720p cap for a bigger picture
                                bigger = self._attempt(video_id, client, with_login, LIVE_FORMAT)
                                if bigger["live"] and bigger["res"] > info["res"]:
                                    info = bigger
                            except Exception:
                                pass
                except Exception as e:
                    last = e
                    if with_login and "cookie" in str(e).lower():
                        print(f"[youtube] {e}", file=sys.stderr, flush=True)
                        break                                        # no point asking again with a login we can't read
                    continue
                meta = meta or info
                if info["live"] or check(info):
                    if quick and not info["live"]:
                        info = dict(info, upgrade=quick)       # a copy: `quick["meta"]` must not carry the upgrade itself
                    return info
                print(f"[youtube] {video_id}: client={self._client or 'default'} address refused", file=sys.stderr, flush=True)
        if meta is not None:
            # Marquee itself is turned away, but yt-dlp usually is not: let it fetch the file while the player reads it.
            print(f"[youtube] {video_id}: asking yt-dlp to fetch it instead", file=sys.stderr, flush=True)
            try:
                got = self.fetch_to_cache(video_id, meta)
                # the bigger picture is already downloading (it began before the 360p address was checked): say so, so the
                # player shows it and swaps to it when it arrives instead of leaving you on 360p
                return dict(got, upgrade=quick) if quick and not got.get("live") and got.get("res", 360) < quick["res"] else got
            except RuntimeError as e:
                print(f"[youtube] {video_id}: single-file version failed ({str(e)[:100]}); trying separate picture and sound streams",
                      file=sys.stderr, flush=True)
                # The first lookup got no list of sizes (so `want` is still the 360 it started at). Don't quietly fall to 480:
                # use the size chosen in Settings, or for Auto what the video's length and your speed allow.
                size = max(want, 480) if want > 360 else self.short_level(quality, float(meta.get("dur") or 0), speed)
                print(f"[youtube] {video_id}: no size list from YouTube; fetching {size}p (quality {quality})", file=sys.stderr, flush=True)
                return self.fetch_dash(video_id, meta, size)
        raise RuntimeError("YouTube won’t let Marquee stream this particular video (every address was refused, HTTP 403). "
                           "Other videos should still play. " + (self.diagnose(video_id) or "").strip())

    def short_level(self, quality: str, dur: float, speed: float = 0.0) -> int:
        """The picture size to fetch for a Short without asking YouTube first. A size you chose is used as it is; Auto takes
        the biggest of 1080, 720 and 480 whose download (about what a Short that long weighs) fits the time allowed."""
        if str(quality).isdigit():
            return min(1080, int(quality))
        dur = dur if dur > 0 else 60.0
        allowed = (speed or DEFAULT_SPEED) * AUTO_BUDGET * 1.5          # the next ones are fetched while one plays
        for res, per_second in ((1080, 600_000), (720, 300_000)):
            if dur * per_second <= allowed:
                return res
        return 480

    def resolve_short(self, video_id: str, known: dict, quality: str = "auto", speed: float = 0.0) -> dict:
        """Like resolve(), for a Short the page already knows the title and length of. A normal lookup asks YouTube twice
        (what sizes exist, then the download); this goes straight to the download, which halves the wait. If that fails it
        falls back to the full lookup."""
        want = self.short_level(quality, float(known.get("dur") or 0), speed)
        if want <= 360:
            return self.resolve(video_id, quality=quality, speed=speed, fast=False)
        meta = {"id": video_id, "title": known.get("title") or "", "channel": known.get("channel") or "",
                "dur": float(known.get("dur") or 0), "thumb": known.get("thumb") or "", "live": False, "levels": []}
        try:
            return self.fetch_dash(video_id, meta, want)
        except RuntimeError as e:
            print(f"[youtube] {video_id}: direct {want}p fetch failed ({str(e)[:100]}); using the full lookup", file=sys.stderr, flush=True)
            return self.resolve(video_id, quality=quality, speed=speed, fast=False)

    _audio_good = ""        # the player client that last gave a working audio address (the default client gives audio-only m4a; android_vr only gives a video file)

    # ---- audio only, for YouTube Music --------------------------------------------------------------------
    def _attempt_audio(self, video_id: str, client: str, with_login: bool) -> dict:
        self._client = client + ("+login" if with_login else "")
        extra = ["--force-ipv4", *js_runtime_args()] + (["--extractor-args", f"youtube:player_client={client}"] if client else [])
        d = self._json("--dump-single-json", "--no-playlist", "-f", AUDIO_FORMAT, *extra,
                       f"https://music.youtube.com/watch?v={video_id}", cookies=with_login)
        url = d.get("url") or ""
        if not url:
            fmts = [f for f in (d.get("formats") or []) if f.get("url") and f.get("acodec") not in (None, "none")]
            fmts.sort(key=lambda f: (f.get("vcodec") in (None, "none"), f.get("abr") or 0))
            url = fmts[-1]["url"] if fmts else ""
        print(f"[youtube] {video_id}: audio client={self._client or 'default'} format={d.get('format_id')} {d.get('ext')} "
              f"{d.get('abr')}kbps host={url.split('/')[2] if '//' in url else ''}", file=sys.stderr, flush=True)
        if not url:
            raise RuntimeError(d.get("reason") or "YouTube gave no audio stream for this track.")
        return {"id": video_id, "title": d.get("track") or d.get("title") or "", "channel": d.get("artist") or d.get("channel") or "",
                "url": url, "headers": d.get("http_headers") or {}, "dur": float(d.get("duration") or 0),
                "thumb": d.get("thumbnail") or "", "live": False, "size_hint": int(d.get("filesize") or d.get("filesize_approx") or 0),
                "audio": True}

    def _probe_again(self, probe: Callable[[dict], bool], info: dict) -> bool:
        """A brand-new address is sometimes refused for a moment. Ask again every 0.7 s for up to `retry_wait` seconds and
        stop at the first yes, instead of always sleeping the whole time."""
        deadline = time.monotonic() + self.retry_wait
        while True:
            time.sleep(max(0.0, min(0.7, deadline - time.monotonic())))
            if probe(info):
                return True
            if time.monotonic() >= deadline:
                return False

    def resolve_audio(self, video_id: str, probe: Callable[[dict], bool] | None = None) -> dict:
        """The audio stream of one track: {id, url, headers, dur, ...}, or {file: ...} when yt-dlp had to download it.

        Each way of asking YouTube is tried in turn and every address is test-fetched first, exactly as resolve() does
        for video, because some clients are handed addresses that answer 403.
        """
        probe = probe or probe_stream
        last: Exception | None = None
        waited = False
        order = sorted(CLIENTS, key=lambda c: c != self._audio_good)        # what worked last time goes first
        for with_login in ([False, True] if self.browser else [False]):
            for client in order:
                try:
                    info = self._attempt_audio(video_id, client, with_login)
                except Exception as e:
                    last = e
                    if with_login and "cookie" in str(e).lower():
                        break
                    continue
                if probe(info):
                    self._audio_good = client
                    return info
                if not waited and self.retry_wait > 0:
                    waited = True
                    print(f"[youtube] {video_id}: audio address refused; checking again for up to {self.retry_wait:g}s",
                          file=sys.stderr, flush=True)
                    if self._probe_again(probe, info):
                        self._audio_good = client
                        return info
                print(f"[youtube] {video_id}: audio client={self._client or 'default'} address refused", file=sys.stderr, flush=True)
        print(f"[youtube] {video_id}: asking yt-dlp to download the audio instead", file=sys.stderr, flush=True)
        try:
            return self.fetch_audio(video_id)
        except RuntimeError as e:
            raise RuntimeError(_hint(str(last or "")) or str(e)) from e

    def fetch_audio(self, video_id: str) -> dict:
        """Let yt-dlp save the audio (a few MB) into the cache, and hand back the file."""
        folder = cache_dir() / "ytmusic"
        folder.mkdir(parents=True, exist_ok=True)
        for old in sorted(folder.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)[20:]:
            try:
                old.unlink()
            except OSError:
                pass
        done = [f for f in folder.glob(f"{video_id}.*") if f.suffix not in (".part", ".ytdl") and f.stat().st_size > 50_000]
        if done:
            return {"id": video_id, "title": "", "channel": "", "file": str(done[0]), "size": done[0].stat().st_size, "headers": {},
                    "dur": 0.0, "thumb": "", "live": False, "audio": True}
        cmd = [self.exe, "--no-playlist", "--force-ipv4", "-q", "--no-warnings", *js_runtime_args(),
               "-f", AUDIO_FORMAT, "-o", str(folder / "%(id)s.%(ext)s"), f"https://music.youtube.com/watch?v={video_id}"]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"Couldn’t run yt-dlp: {e}")
        files = [f for f in folder.glob(f"{video_id}.*") if f.suffix not in (".part", ".ytdl")]
        if p.returncode != 0 or not files:
            print(p.stderr, file=sys.stderr, flush=True)
            raise RuntimeError(_hint(p.stderr) or "yt-dlp could not fetch this track's audio either.")
        return {"id": video_id, "title": "", "channel": "", "file": str(files[0]), "size": files[0].stat().st_size, "headers": {},
                "dur": 0.0, "thumb": "", "live": False, "audio": True}

    def fetch_to_cache(self, video_id: str, meta: dict) -> dict:
        """Start yt-dlp saving the video into the cache; return once enough has arrived to start playing it."""
        import atexit
        folder = cache_dir() / "youtube"
        folder.mkdir(parents=True, exist_ok=True)
        for old in sorted(folder.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)[5:]:
            try:
                old.unlink()
            except OSError:
                pass
        for old in folder.glob(f"{video_id}.*"):
            try:
                old.unlink()
            except OSError:
                pass
        cmd = [self.exe, "--no-playlist", "--no-part", "--force-ipv4", "-q", "--no-warnings", *js_runtime_args(),
               "-f", FORMAT, "-o", str(folder / "%(id)s.%(ext)s"), f"https://www.youtube.com/watch?v={video_id}"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        except OSError as e:
            raise RuntimeError(f"Couldn’t run yt-dlp: {e}")
        atexit.register(lambda: proc.poll() is None and proc.terminate())
        deadline = time.time() + 45
        while time.time() < deadline:
            files = [f for f in folder.glob(f"{video_id}.*") if f.suffix not in (".part", ".ytdl")]
            if files and files[0].stat().st_size >= 1_000_000:
                return dict(meta, file=str(files[0]), proc=proc, headers={})
            if proc.poll() is not None:
                files = [f for f in folder.glob(f"{video_id}.*") if f.suffix not in (".part", ".ytdl")]
                if proc.returncode == 0 and files:
                    got = files[0].stat().st_size
                    expect = meta.get("size_hint") or int(float(meta.get("dur") or 0) * MIN_BYTES_PER_SECOND)
                    if expect and got < expect * 0.5:
                        print(f"[youtube] {video_id}: yt-dlp saved only {got} bytes (expected about {expect})", file=sys.stderr, flush=True)
                        raise StubStream("YouTube only supplied a small fragment of the single-file version of this video.")
                    return dict(meta, file=str(files[0]), proc=proc, headers={})
                err = proc.stderr.read() if proc.stderr else ""
                print(err, file=sys.stderr, flush=True)
                raise RuntimeError(_hint(err) or "yt-dlp could not fetch this video either.")
            time.sleep(0.1)
        proc.terminate()
        raise RuntimeError("yt-dlp took too long to start fetching this video.")

    def fetch_dash(self, video_id: str, meta: dict, res: int = 480) -> dict:
        """Download the separate picture and sound streams, joined into one file (this takes a while: it is a real download).

        Only one download of a given video and size runs at a time: opening the same video again while its big file is still
        coming waits for that download and then plays the finished file, instead of starting a second one that deletes the first's
        half-written file."""
        key = f"{video_id}-{res}p"
        with _DASH_GUARD:
            lock = _DASH_BUSY.setdefault(key, threading.Lock())
        if lock.locked():
            print(f"[youtube] {video_id}: the {res}p file is already being fetched; waiting for it", file=sys.stderr, flush=True)
        with lock:
            return self._fetch_dash(video_id, meta, res)

    def _fetch_dash(self, video_id: str, meta: dict, res: int) -> dict:
        folder = cache_dir() / "youtube"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{video_id}-{res}p.mp4"
        expect = int(float(meta.get("dur") or 0) * MIN_BYTES_PER_SECOND)
        if target.exists() and target.stat().st_size >= max(expect, 1_000_000):
            target.touch()                      # already fetched at this quality: play it again at once
            size = target.stat().st_size
            print(f"[youtube] {video_id}: reusing the {res}p file from earlier", file=sys.stderr, flush=True)
            return dict(meta, file=str(target), size=size, proc=None, headers={}, res=res, speed=0.0)
        # keep the newest few (the setting); the file being fetched is one of them
        for old in sorted((f for f in folder.glob("*") if f != target and not dash_in_use(f)), key=lambda f: f.stat().st_mtime, reverse=True)[max(0, self.keep - 1):]:
            try:
                old.unlink()
            except OSError:
                pass
        if target.exists():
            try:
                target.unlink()                 # a leftover from an interrupted download
            except OSError:
                pass
        cmd = [self.exe, "--no-playlist", "--no-part", "--newline", "--force-ipv4", *js_runtime_args(),
               "-f", DASH_FORMAT, "-S", f"res:{res},vcodec:h264,acodec:aac", "-N", "4", "--merge-output-format", "mp4", "-o", str(folder / ("%(id)s-" + str(res) + "p.%(ext)s")),
               "--progress-template", "download:PROGRESS %(progress._percent_str)s of %(progress._total_bytes_str)s at %(progress._speed_str)s",
               "--print", "after_move:filepath", f"https://www.youtube.com/watch?v={video_id}"]
        started = time.time()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as e:
            raise RuntimeError(f"Couldn’t run yt-dlp: {e}")
        import atexit
        import threading
        atexit.register(lambda: proc.poll() is None and proc.terminate())
        errs: list[str] = []
        threading.Thread(target=lambda: errs.extend(proc.stderr), daemon=True).start()
        path, last = "", 0.0
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESS"):
                if time.time() - last > 0.5:
                    last = time.time()
                    text = line[9:].strip()
                    print(f"[youtube] {video_id}: downloading {text}", file=sys.stderr, flush=True)
                    if self.progress:
                        self.progress(text)
            elif line:
                path = line
        proc.wait()
        if proc.returncode != 0 or not path or not Path(path).exists():
            text = "".join(errs)
            print(text, file=sys.stderr, flush=True)
            raise RuntimeError(_hint(text) or "YouTube won’t let yt-dlp fetch this video either.")
        got = Path(path).stat().st_size
        expect = int(float(meta.get("dur") or 0) * MIN_BYTES_PER_SECOND)
        if expect and got < expect * 0.5:
            raise RuntimeError("YouTube only supplies a small fragment of this video, so it can’t be played here.")
        speed = got / max(1.0, time.time() - started)
        print(f"[youtube] {video_id}: fetched {got / 1e6:.0f} MB at {res}p in {time.time() - started:.0f}s ({speed / 1e6:.1f} MB/s)",
              file=sys.stderr, flush=True)
        return dict(meta, file=path, size=got, proc=None, headers={}, res=res, speed=speed)

    def diagnose(self, video_id: str) -> str:
        """Ask yt-dlp what it is unhappy about, and turn the usual causes into something you can act on."""
        try:
            p = subprocess.run([self.exe, "--simulate", "-f", FORMAT, f"https://www.youtube.com/watch?v={video_id}"],
                               capture_output=True, text=True, timeout=60)
            text = p.stderr
        except Exception:
            return ""
        print(text, file=sys.stderr, flush=True)
        return _hint(text)


def _hint(text: str) -> str:
    """yt-dlp's complaint -> something you can act on."""
    low = text.lower()
    if "javascript runtime" in low or "js runtime" in low or "n challenge" in low or "ejs" in low:
        return ("yt-dlp needs a JavaScript runtime to unlock YouTube streams. Install deno and the yt-dlp-ejs package "
                "(Arch: sudo pacman -S deno yt-dlp-ejs), then try again.")
    if "sign in to confirm" in low or "not a bot" in low:
        return "YouTube wants a sign-in. Choose your browser in Settings › YouTube, then try again."
    if "po token" in low:
        return "YouTube wants a proof-of-origin token. Update yt-dlp, or choose your browser in Settings › YouTube."
    errs = [ln for ln in text.splitlines() if ln.startswith(("ERROR", "WARNING"))]
    return errs[-1][:220] if errs else ""


def probe_stream(info: dict) -> bool:
    """Does the stream address answer? Asks for its first two bytes with the headers yt-dlp said to use."""
    h = dict(info.get("headers") or {})
    h["Range"] = "bytes=0-10485759"                   # what the stream server will ask for, so the test means something
    m = re.search(r"[?&/]ip[=/]([^&/]+)", info["url"])
    try:
        with open_stream(info["url"], h, 15) as r:
            ok = r.status in (200, 206) and len(r.read(1)) == 1
            cr = r.headers.get("Content-Range", "") or ""
            total = int(cr.rsplit("/", 1)[1]) if "/" in cr and cr.rsplit("/", 1)[1].isdigit() else int(r.headers.get("Content-Length", 0) or 0)
            # YouTube sometimes answers with a stub of a few hundred KB instead of the video: far too small for its length.
            expect = info.get("size_hint") or int(float(info.get("dur") or 0) * MIN_BYTES_PER_SECOND)
            if ok and total and expect and total < expect * 0.5:
                print(f"[youtube] {info['id']}: address answers with only {total} bytes (expected about {expect}); ignoring it",
                      file=sys.stderr, flush=True)
                ok = False
    except urllib.error.HTTPError as e:
        ok = e.code < 400
        print(f"[youtube] {info['id']}: HTTP {e.code} for address bound to ip={m.group(1) if m else '?'}", file=sys.stderr, flush=True)
    except OSError as e:
        ok = False
        print(f"[youtube] {info['id']}: {e}", file=sys.stderr, flush=True)
    return ok


def _old_ipv4_opener() -> urllib.request.OpenerDirector:
    """An opener that only connects over IPv4. YouTube ties a stream address to the IP address that asked for it, so
    asking yt-dlp over IPv4 and then fetching over IPv6 (or the reverse) gets a 403."""
    import functools
    import http.client

    class H(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(functools.partial(http.client.HTTPConnection, source_address=("0.0.0.0", 0)), req)

    class S(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(functools.partial(http.client.HTTPSConnection, source_address=("0.0.0.0", 0)), req)
    return urllib.request.build_opener(H, S)


# ---------------------------------------------------------------------------------------------- your data

def parse_takeout(text: str) -> list[tuple[str, str]]:
    """Channels from Google Takeout's subscriptions.csv (Channel ID, Channel URL, Channel title) as (id, name)."""
    import csv
    import io
    out = []
    for row in csv.reader(io.StringIO(text.lstrip("\ufeff"))):
        if len(row) < 3:
            continue
        cid, name = row[0].strip(), row[2].strip()
        if re.fullmatch(r"UC[\w-]{22}", cid):
            out.append((cid, name))
    return out


class Store:
    """Subscriptions, watch history and the browser choice, kept in youtube.json."""
    HISTORY_MAX = 200

    def __init__(self, folder: Path | None = None):
        self._file = (folder or data_dir()) / "youtube.json"
        self.subs: list[dict] = []
        self.history: list[dict] = []
        self.browser = ""
        self.quality = "auto"
        self.speed = 0.0
        self.cache = "3"                             # "1" | "3" | "10" | "exit" (keep 3, wipe when Marquee closes)
        self.list_mode = False                       # Subscriptions / Recommended shown as a list
        self.fast = True                             # start at 360p at once and upgrade in the background
        self.caption_lang = ""                       # the caption language last chosen ("en"), picked again on the next video that has it
        self.positions: dict[str, dict] = {}        # video id -> {pos, dur, t}: where you stopped watching
        try:
            d = json.loads(self._file.read_text())
            self.positions = {k: v for k, v in (d.get("positions") or {}).items() if isinstance(v, dict) and v.get("pos")}
            self.quality = d.get("quality", "auto") if d.get("quality", "auto") in QUALITIES else "auto"
            self.speed = float(d.get("speed", 0) or 0)
            self.fast = bool(d.get("fast", True))
            self.caption_lang = str(d.get("caption_lang", "") or "")
            self.list_mode = bool(d.get("list_mode", False))
            self.cache = d.get("cache", "3") if d.get("cache", "3") in ("1", "3", "10", "exit") else "3"
            self.subs = [s for s in d.get("subs", []) if s.get("id")]
            self.history = [h for h in d.get("history", []) if h.get("id")]
            self.browser = d.get("browser", "") if d.get("browser", "") in BROWSERS else ""
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(json.dumps({"subs": self.subs, "history": self.history, "browser": self.browser,
                                                      "quality": self.quality, "speed": self.speed, "fast": self.fast, "cache": self.cache, "list_mode": self.list_mode,
                                                      "positions": self.positions, "caption_lang": self.caption_lang}, indent=1))
        except OSError:
            pass

    def subscribed(self, cid: str) -> bool:
        return any(s["id"] == cid for s in self.subs)

    def subscribe(self, cid: str, name: str, thumb: str = "") -> None:
        if cid and not self.subscribed(cid):
            self.subs.append({"id": cid, "name": name or cid, "thumb": thumb})
            self.subs.sort(key=lambda s: s["name"].lower())
            self.save()

    def unsubscribe(self, cid: str) -> None:
        self.subs = [s for s in self.subs if s["id"] != cid]
        self.save()

    def watched(self, video: dict, now: float | None = None) -> None:
        row = {k: video.get(k, "") for k in ("id", "title", "channel", "channelId", "thumb", "dur")}
        row["watched"] = now if now is not None else time.time()
        self.history = ([row] + [h for h in self.history if h["id"] != row["id"]])[: self.HISTORY_MAX]
        self.save()

    def clear_history(self) -> None:
        self.history = []
        self.save()

    def set_thumb(self, cid: str, thumb: str) -> None:
        for s in self.subs:
            if s["id"] == cid:
                s["thumb"] = thumb
        self.save()

    def load_feed(self) -> list[dict]:
        try:
            return json.loads((self._file.parent / "youtube_feed.json").read_text())
        except (OSError, ValueError):
            return []

    def save_feed(self, feed: list[dict]) -> None:
        try:
            (self._file.parent / "youtube_feed.json").write_text(json.dumps(feed))
        except OSError:
            pass

    POSITIONS_MAX = 500

    def set_position(self, vid: str, pos: float, dur: float, now: float | None = None) -> None:
        """Remember where you stopped. Near the start or the very end counts as not started / finished."""
        if not vid or dur <= 0:
            return
        if pos < 10:
            return                                  # just started (or still jumping to the resume point): keep what we have
        if pos > dur - max(15.0, dur * 0.03):
            self.forget(vid)                        # watched to the end
            return
        self.positions[vid] = {"pos": round(pos, 1), "dur": round(dur, 1), "t": now if now is not None else time.time()}
        if len(self.positions) > self.POSITIONS_MAX:
            keep = sorted(self.positions.items(), key=lambda kv: kv[1].get("t", 0), reverse=True)[: self.POSITIONS_MAX]
            self.positions = dict(keep)
        self.save()

    def forget(self, vid: str) -> None:
        if vid in self.positions:
            del self.positions[vid]
            self.save()

    def resume_at(self, vid: str) -> float:
        return float(self.positions.get(vid, {}).get("pos", 0.0))

    def set_caption_lang(self, lang: str) -> None:
        self.caption_lang = lang
        self.save()

    def set_quality(self, q: str) -> None:
        self.quality = q if q in QUALITIES else "auto"
        self.save()

    @property
    def keep_count(self) -> int:
        return {"1": 1, "3": 3, "10": 10}.get(self.cache, 3)

    def set_cache(self, mode: str) -> None:
        self.cache = mode if mode in ("1", "3", "10", "exit") else "3"
        self.save()

    def set_list_mode(self, on: bool) -> None:
        self.list_mode = bool(on)
        self.save()

    def set_fast(self, on: bool) -> None:
        self.fast = bool(on)
        self.save()

    def set_speed(self, v: float) -> None:
        self.speed = max(0.0, v)
        self.save()

    def set_browser(self, name: str) -> None:
        self.browser = name if name in BROWSERS else ""
        self.save()
