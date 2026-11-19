"""The Podcasts page: search, feeds, subscriptions and where you stopped, with the network work off the UI thread.
See core/podcasts.py."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..core import podcasts as pc
from ..core.youtube import clock

REFRESH_AFTER = 15 * 60               # seconds before the subscriptions are read again when you come back to the page


def _log(msg: str) -> None:
    print(f"marquee: podcasts: {msg}", flush=True)


def _date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%-d %b %Y") if ts > 0 else ""


class PodcastsController(QObject):
    changed = Signal()
    _done = Signal(int, str, object)
    _failed = Signal(int, str)
    _refreshed_feeds = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = pc.Store()
        self._view = "subs"                    # subs | results | show
        self._results: list[dict] = []
        self._feed: dict | None = None         # the show being looked at
        self._busy = False
        self._error = ""
        self._label = ""
        self._token = 0
        self._refreshed = 0.0
        self._by_key: dict[str, dict] = {}     # episode key -> episode plus its show, for the player
        self._done.connect(self._on_done)
        self._failed.connect(self._on_failed)
        self._refreshed_feeds.connect(self._on_refreshed)

    # ---- what the page shows ---------------------------------------------------------------
    @Property(str, notify=changed)
    def view(self):
        return self._view

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(str, notify=changed)
    def label(self):
        return self._label

    @Property("QVariantList", notify=changed)
    def subs(self):
        out = []
        for s in self._store.subs:
            out.append({"url": s["url"], "title": s["title"], "author": s.get("author", ""), "image": s.get("image", ""),
                        "unplayed": self._store.unplayed(self._store.cached_feed(s["url"]))})
        return sorted(out, key=lambda s: s["title"].lower())

    @Property("QVariantList", notify=changed)
    def results(self):
        return [dict(r, subscribed=self._store.subscribed(r["feed"])) for r in self._results]

    @Property("QVariantMap", notify=changed)
    def show(self):
        f = self._feed
        if not f:
            return {}
        return {"url": f["url"], "title": f["title"], "author": f["author"], "image": f["image"], "desc": f["desc"],
                "subscribed": self._store.subscribed(f["url"]), "count": len(f["episodes"])}

    @Property("QVariantList", notify=changed)
    def episodes(self):
        f = self._feed
        if not f:
            return []
        out = []
        for e in f["episodes"]:
            pos = self._store.position(e["key"])
            out.append({"key": e["key"], "title": e["title"], "desc": e["desc"], "date": _date(e["published"]),
                        "durText": clock(e["dur"]) if e["dur"] else "", "dur": e["dur"], "played": self._store.is_played(e["key"]),
                        "pos": pos, "progress": min(1.0, pos / e["dur"]) if e["dur"] and pos else 0.0,
                        "number": (f"S{e['season']} E{e['episode']}" if e["season"] and e["episode"] else f"Episode {e['episode']}" if e["episode"] else "")})
        return out

    @Property(float, notify=changed)
    def speed(self):
        return self._store.speed

    @Slot(float)
    def setSpeed(self, speed: float):
        self._store.set_speed(speed)
        self.changed.emit()

    # ---- background work -------------------------------------------------------------------
    def _run(self, label: str, fn, kind: str):
        self._token += 1
        token = self._token
        self._busy, self._error, self._label = True, "", label
        self.changed.emit()

        def work():
            try:
                data = fn()
            except pc.FeedError as e:
                self._failed.emit(token, str(e))
                return
            except Exception as e:
                _log(f"{label}: {e!r}")
                self._failed.emit(token, f"Couldn’t finish that ({str(e)[:120] or e.__class__.__name__}).")
                return
            self._done.emit(token, kind, data)
        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, token: int, kind: str, data):
        if token != self._token:
            return                                          # you asked for something else meanwhile
        self._busy = False
        if kind == "results":
            self._results = data
            self._view = "results"
            if not data:
                self._error = "No podcasts found."
        elif kind == "feed":
            self._open(data)
        self.changed.emit()

    def _on_failed(self, token: int, message: str):
        if token != self._token:
            return
        self._busy, self._error = False, message
        self.changed.emit()

    def _open(self, feed: dict):
        self._feed = feed
        for e in feed["episodes"]:
            self._by_key[e["key"]] = dict(e, show=feed["title"], showImage=feed["image"], feed=feed["url"])
        if self._store.subscribed(feed["url"]):
            self._store.save_feed(feed)                     # a subscribed show keeps its newest episode list
            self._store.save()
        self._view = "show"

    # ---- actions ---------------------------------------------------------------------------
    @Slot(str)
    def search(self, text: str):
        """Search podcasts by name, or open a pasted feed address or Apple Podcasts link."""
        kind, value = pc.classify(text)
        if not value:
            return
        if kind == "search":
            self._run(f"Podcasts for “{value}”", lambda: pc.search(value), "results")
        else:
            self._run("Opening the feed…", lambda: pc.load_feed(value), "feed")

    @Slot(str)
    def openFeed(self, url: str):
        cached = self._store.cached_feed(url)
        if cached:                                          # show what we have at once, then read the latest
            self._open(cached)
            self.changed.emit()
        self._run("Reading the feed…", lambda: pc.load_feed(url), "feed")

    @Slot()
    def subscribe(self):
        if self._feed:
            self._store.subscribe(self._feed)
            self.changed.emit()

    @Slot()
    def unsubscribe(self):
        if self._feed:
            self._store.unsubscribe(self._feed["url"])
            self.changed.emit()

    @Slot()
    def back(self):
        self._error = ""
        self._view = "results" if self._view == "show" and self._results and not self._store.subscribed((self._feed or {}).get("url", "")) else "subs"
        self.changed.emit()

    @Slot()
    def showSubs(self):
        self._error = ""
        self._view = "subs"
        self.changed.emit()

    @Slot()
    def refreshAll(self):
        """Read every subscribed feed again (at most every fifteen minutes unless asked twice)."""
        urls = [s["url"] for s in self._store.subs]
        if not urls or time.time() - self._refreshed < REFRESH_AFTER:
            return
        self._refreshed = time.time()

        def one(u):
            try:
                return pc.load_feed(u)
            except Exception as e:
                _log(f"{u}: {str(e)[:100]}")
                return None

        def work():
            with ThreadPoolExecutor(max_workers=4) as pool:
                feeds = [f for f in pool.map(one, urls) if f]
            self._refreshed_feeds.emit(feeds)
        threading.Thread(target=work, daemon=True).start()

    def _on_refreshed(self, feeds):
        for f in feeds:
            if self._store.subscribed(f["url"]):
                self._store.save_feed(f)
                for e in f["episodes"]:
                    self._by_key[e["key"]] = dict(e, show=f["title"], showImage=f["image"], feed=f["url"])
        self._store.save()
        self.changed.emit()

    # ---- playing ---------------------------------------------------------------------------
    @Slot(str, result="QVariantMap")
    def episodeInfo(self, key: str):
        e = self._by_key.get(key)
        if not e:
            return {}
        return {"key": key, "title": e["title"], "sub": e["show"], "url": e["audio"], "cover": e.get("image") or e.get("showImage", ""),
                "dur": e["dur"], "pos": self._store.position(key), "feed": e["feed"]}

    @Slot(str, float, float)
    def savePosition(self, key: str, seconds: float, dur: float):
        self._store.set_position(key, seconds, dur)
        self.changed.emit()

    @Slot(str, bool)
    def markPlayed(self, key: str, played: bool):
        self._store.mark_played(key, played)
        self.changed.emit()
