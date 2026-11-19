"""The Shorts page: finds short videos (a search, your subscriptions, a pasted link) off the UI thread."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..core import shorts, youtube as yt


def _log(msg: str) -> None:
    print(f"marquee: shorts: {msg}", flush=True)


class ShortsController(QObject):
    changed = Signal()
    loaded = Signal()                 # a new list of Shorts has arrived: start at the first
    _done = Signal(int, object, str)
    _more = Signal(int, object)

    def __init__(self, youtube, parent=None):
        super().__init__(parent)
        self._yt = youtube
        self._items: list[dict] = []
        self._busy = False
        self._error = ""
        self._label = ""
        self._token = 0
        self._done.connect(self._on_done)
        self._more.connect(self._on_more)
        self._more_busy = False
        self._recs_cache = None

    # ---- what the page shows ---------------------------------------------------------
    @Property("QVariantList", notify=changed)
    def items(self):
        return self._items

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(str, notify=changed)
    def label(self):
        return self._label

    @Property(bool, notify=changed)
    def hasSubscriptions(self):
        return bool(self._yt._store.subs)

    # ---- finding Shorts --------------------------------------------------------------
    def _run(self, label: str, fn):
        self._more_busy = False
        self._token += 1
        token = self._token
        self._busy, self._error, self._label = True, "", label
        self.changed.emit()

        def work():
            try:
                rows = fn()
                err = "" if rows else "No Shorts found."
            except Exception as e:
                rows, err = [], str(e) or e.__class__.__name__
                _log(f"{label}: {err}")
            self._done.emit(token, rows, err)
        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, token: int, rows, err: str):
        if token != self._token:
            return                                          # a newer search replaced this one
        self._items = [dict(r, durText=yt.clock(r["dur"]) if r.get("dur") else "") for r in rows]
        self._busy, self._error = False, err
        self.changed.emit()
        self.loaded.emit()

    def _recs(self) -> list[dict]:
        """YouTube's recommendations for your account, when a browser login is set (kept ten minutes; [] when unavailable)."""
        dlp = self._yt._dlp
        if not getattr(dlp, "browser", ""):
            return []
        now = time.time()
        if self._recs_cache and now - self._recs_cache[0] < 600:
            return self._recs_cache[1]
        try:
            recs = dlp.recommended(40)
        except Exception as e:
            _log(f"recommendations unavailable: {str(e)[:160]}")
            recs = []
        self._recs_cache = (now, recs)
        return recs

    @Slot()
    def forYou(self):
        """A mix of your subscriptions and new finds (things like what you watch, and random topics)."""
        dlp, store = self._yt._dlp, self._yt._store
        subs, hist = list(store.subs), list(store.history)
        self._run("For you", lambda: shorts.for_you(dlp, subs, hist, recs=self._recs()))

    @Slot()
    def more(self):
        """Add more new Shorts to the end of the list (called as you near the end), without moving you."""
        if self._more_busy or self._busy or not self._items:
            return
        self._more_busy = True
        token = self._token
        dlp, store = self._yt._dlp, self._yt._store
        subs, hist = list(store.subs), list(store.history)

        def work():
            try:
                rows = shorts.for_you(dlp, subs, hist, recs=self._recs())
            except Exception as e:
                _log(f"more: {e}")
                rows = []
            self._more.emit(token, rows)
        threading.Thread(target=work, daemon=True).start()

    def _on_more(self, token: int, rows):
        self._more_busy = False
        if token != self._token:
            return                                          # you searched for something else meanwhile
        have = {r["id"] for r in self._items}
        fresh = [dict(r, durText=yt.clock(r["dur"]) if r.get("dur") else "") for r in rows if r["id"] not in have]
        if fresh:
            self._items = self._items + fresh
            self.changed.emit()

    @Slot(str)
    def similar(self, title: str):
        """Shorts like the one playing."""
        dlp = self._yt._dlp
        self._run("More like this", lambda: shorts.more_like(dlp, title))

    @Slot(str)
    def search(self, text: str):
        text = text.strip()
        if not text:
            return
        kind, ident = yt.parse_url(text)
        dlp = self._yt._dlp
        if kind == "video":                                 # a pasted Shorts (or video) link
            self._run("Your link", lambda: [{"id": ident, "title": "", "channel": "", "channelId": "", "thumb": yt.thumb_for(ident),
                                             "dur": 0.0, "views": 0, "published": 0.0, "live": False, "short": True}])
        elif kind == "channel":
            def channel():
                cid = ident if ident.startswith("UC") else dlp.resolve_channel(ident)["id"]
                return shorts.channel_shorts(dlp, cid, 30)
            self._run("That channel’s Shorts", channel)
        else:
            self._run(f"Shorts for “{text}”", lambda: shorts.search_shorts(dlp, text, 24))

    @Slot(str, str)
    def channel(self, channel_id: str, name: str):
        """More Shorts from the channel of the one playing."""
        if not channel_id:
            return
        dlp = self._yt._dlp
        self._run(f"{name or 'This channel'}’s Shorts", lambda: shorts.channel_shorts(dlp, channel_id, 30))
