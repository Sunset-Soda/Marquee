"""Podcasts: search, RSS feeds, subscriptions and where you stopped. Shared by every frontend.

Search uses the iTunes Search API (no key). A podcast itself is just an RSS feed, so you can also paste a feed address,
or an Apple Podcasts link (its feed address is looked up). Episodes play straight from the address in the feed.
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import net
from .library import cache_dir, data_dir

SEARCH_URL = "https://itunes.apple.com/search?media=podcast&entity=podcast&limit={n}&term={q}"
LOOKUP_URL = "https://itunes.apple.com/lookup?id={id}"
MAX_FEED_BYTES = 12_000_000
MAX_EPISODES = 500
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac")


class FeedError(RuntimeError):
    """The address did not give a podcast feed."""


def _web(url: str) -> str:
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise FeedError("That isn’t a web address Marquee can open.")
    return url


def fetch(url: str, timeout: float = 20.0) -> bytes:
    """The bytes at an address, never more than MAX_FEED_BYTES (a feed is a few hundred KB at most)."""
    req = urllib.request.Request(_web(url), headers={"User-Agent": net.UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(MAX_FEED_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise FeedError(f"The server answered {e.code}.") from e
    except (urllib.error.URLError, OSError) as e:
        raise FeedError(f"Couldn’t reach it ({getattr(e, 'reason', e)}).") from e
    if len(data) > MAX_FEED_BYTES:
        raise FeedError("That feed is too large to be a podcast.")
    return data


# ---- what the person typed ------------------------------------------------------------------
def classify(text: str) -> tuple[str, str]:
    """("feed", address) for something that looks like an address, ("apple", link) for an Apple Podcasts page,
    otherwise ("search", words)."""
    t = text.strip()
    if not t:
        return "search", ""
    m = re.match(r"^(feed|itpc|pcast|podcast)://(.*)$", t, re.I)
    if m:
        t = "https://" + m.group(2)
    if re.match(r"^https?://", t, re.I):
        host = urllib.parse.urlparse(t).netloc.lower()
        return ("apple", t) if host.endswith("podcasts.apple.com") or host.endswith("itunes.apple.com") else ("feed", t)
    if " " not in t and "." in t and ("/" in t or t.lower().endswith((".xml", ".rss"))):
        return "feed", "https://" + t
    return "search", t


# ---- search ---------------------------------------------------------------------------------
def search(query: str, n: int = 25) -> list[dict]:
    """Podcasts matching some words: [{title, author, feed, image, genre}], the ones with a feed address."""
    data = json.loads(net.get(SEARCH_URL.format(n=n, q=urllib.parse.quote(query.strip()))))
    out = []
    for r in data.get("results", []):
        feed = r.get("feedUrl")
        if feed:
            out.append({"title": r.get("collectionName") or r.get("trackName") or "", "author": r.get("artistName") or "",
                        "feed": feed, "image": r.get("artworkUrl600") or r.get("artworkUrl100") or "",
                        "genre": r.get("primaryGenreName") or ""})
    return out


def feed_from_apple(link: str) -> str:
    """The feed address behind an Apple Podcasts link (…/id1234567890)."""
    m = re.search(r"/id(\d+)", link)
    if not m:
        raise FeedError("That Apple Podcasts link has no podcast number in it.")
    data = json.loads(net.get(LOOKUP_URL.format(id=m.group(1))))
    for r in data.get("results", []):
        if r.get("feedUrl"):
            return r["feedUrl"]
    raise FeedError("Apple has no feed address for that podcast.")


# ---- reading a feed -------------------------------------------------------------------------
def _text(el, tag: str) -> str:
    e = el.find(tag)
    return (e.text or "").strip() if e is not None and e.text else ""


def plain(markup: str) -> str:
    """Show notes without their HTML."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", markup or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", s)).strip()


def parse_duration(text: str) -> float:
    """itunes:duration is seconds, MM:SS or HH:MM:SS."""
    t = (text or "").strip()
    if not t:
        return 0.0
    try:
        parts = [float(p) for p in t.split(":")]
    except ValueError:
        return 0.0
    secs = 0.0
    for p in parts[-3:]:
        secs = secs * 60 + p
    return secs


def _when(text: str) -> float:
    try:
        return email.utils.parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError):
        return 0.0


def episode_key(feed_url: str, guid: str, audio: str) -> str:
    return hashlib.sha1(f"{feed_url}\n{guid or audio}".encode("utf-8")).hexdigest()[:16]


def parse_feed(data: bytes, url: str = "") -> dict:
    """{url, title, author, image, desc, episodes: [...newest first]} from RSS. Raises FeedError for anything else."""
    head = data[:4096].lstrip().lower()
    if b"<!entity" in data[:200_000].lower():
        raise FeedError("That feed uses XML features Marquee won’t read.")      # entity tricks can balloon in memory
    if head.startswith((b"<!doctype html", b"<html")):
        raise FeedError("That address is a web page, not a podcast feed.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FeedError("That isn’t a podcast feed (it isn’t readable RSS).") from e
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FeedError("That isn’t a podcast feed (Marquee reads RSS feeds).")
    img = channel.find(f"{ITUNES}image")
    image = (img.get("href") if img is not None else "") or _text(channel.find("image") if channel.find("image") is not None else channel, "url")
    episodes = []
    for it in channel.findall("item"):
        enc = it.find("enclosure")
        audio = (enc.get("url") or "").strip() if enc is not None else ""
        kind = (enc.get("type") or "").lower() if enc is not None else ""
        if not audio or not audio.lower().startswith(("http://", "https://")):
            continue
        if not (kind.startswith("audio/") or urllib.parse.urlparse(audio).path.lower().endswith(AUDIO_EXT)):
            continue                                                        # a video episode: Marquee plays audio here
        guid = _text(it, "guid")
        eimg = it.find(f"{ITUNES}image")
        episodes.append({
            "key": episode_key(url, guid, audio), "title": _text(it, "title") or "Untitled episode", "audio": audio,
            "published": _when(_text(it, "pubDate")), "dur": parse_duration(_text(it, f"{ITUNES}duration")),
            "desc": plain(_text(it, f"{CONTENT}encoded") or _text(it, "description") or _text(it, f"{ITUNES}summary"))[:4000],
            "image": (eimg.get("href") if eimg is not None else "") or "",
            "episode": int(_text(it, f"{ITUNES}episode") or 0) if _text(it, f"{ITUNES}episode").isdigit() else 0,
            "season": int(_text(it, f"{ITUNES}season") or 0) if _text(it, f"{ITUNES}season").isdigit() else 0,
        })
    episodes.sort(key=lambda e: e["published"], reverse=True)
    return {"url": url, "title": _text(channel, "title") or url, "author": _text(channel, f"{ITUNES}author") or _text(channel, "managingEditor"),
            "image": image or "", "desc": plain(_text(channel, "description") or _text(channel, f"{ITUNES}summary"))[:2000],
            "episodes": episodes[:MAX_EPISODES], "fetched": time.time()}


def load_feed(url: str) -> dict:
    """Fetch and read one feed. Follows an Apple link to its feed first."""
    kind, addr = classify(url)
    if kind == "apple":
        addr = feed_from_apple(addr)
    return parse_feed(fetch(addr), addr)


# ---- subscriptions and where you stopped ----------------------------------------------------
class Store:
    """podcasts.json: your subscriptions and each episode's place; the episode lists are cached to work offline."""
    def __init__(self, path=None, cache=None):
        self.path = path or (data_dir() / "podcasts.json")
        self.cache = cache or (cache_dir() / "podcasts")
        self.subs: list[dict] = []                    # {url, title, author, image, added}
        self.state: dict[str, dict] = {}              # episode key -> {"t": seconds, "d": length, "played": bool}
        self.speed = 1.0
        self._load()

    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.subs = [s for s in d.get("subs", []) if s.get("url")]
        self.state = {k: v for k, v in (d.get("state") or {}).items() if isinstance(v, dict)}
        self.speed = float(d.get("speed") or 1.0)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"subs": self.subs, "state": self.state, "speed": self.speed}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def subscribed(self, url: str) -> bool:
        return any(s["url"] == url for s in self.subs)

    def subscribe(self, feed: dict) -> None:
        if not self.subscribed(feed["url"]):
            self.subs.append({"url": feed["url"], "title": feed["title"], "author": feed["author"], "image": feed["image"], "added": time.time()})
        self.save_feed(feed)
        self.save()

    def unsubscribe(self, url: str) -> None:
        self.subs = [s for s in self.subs if s["url"] != url]
        try:
            self._cache_file(url).unlink()
        except OSError:
            pass
        self.save()

    def _cache_file(self, url: str):
        return self.cache / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".json")

    def save_feed(self, feed: dict) -> None:
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            self._cache_file(feed["url"]).write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        for s in self.subs:                           # keep the name and picture current
            if s["url"] == feed["url"]:
                s.update(title=feed["title"] or s["title"], author=feed["author"] or s["author"], image=feed["image"] or s["image"])

    def cached_feed(self, url: str) -> dict | None:
        try:
            return json.loads(self._cache_file(url).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def position(self, key: str) -> float:
        st = self.state.get(key) or {}
        return 0.0 if st.get("played") else float(st.get("t") or 0)

    def is_played(self, key: str) -> bool:
        return bool((self.state.get(key) or {}).get("played"))

    def set_position(self, key: str, seconds: float, dur: float) -> None:
        if seconds < 5:
            return                                    # just started (or still jumping to the old place): keep what we have
        st = self.state.setdefault(key, {})
        st["t"], st["d"] = round(seconds, 1), round(dur, 1)
        if dur and seconds >= dur - 30:               # near the end counts as heard
            st["played"], st["t"] = True, 0
        self.save()

    def mark_played(self, key: str, played: bool) -> None:
        st = self.state.setdefault(key, {})
        st["played"] = bool(played)
        if played:
            st["t"] = 0
        self.save()

    def unplayed(self, feed: dict | None) -> int:
        return sum(1 for e in (feed or {}).get("episodes", []) if not self.is_played(e["key"]))

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.5, min(3.0, float(speed)))
        self.save()
