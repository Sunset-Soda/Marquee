"""YouTube Music for the Qt screens: catalogue lookups and stream lookups off the UI thread. See core/ytmusic.py."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ..core import ytmusic as ytm


def _log(msg: str) -> None:
    print(f"marquee: ytmusic: {msg}", flush=True)


KINDS = ("songs", "albums", "artists", "playlists")
STREAM_TTL = 4 * 3600                 # YouTube addresses last about six hours; reuse them for four
ytm_ua = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


class YtMusicController(QObject):
    changed = Signal()
    ready = Signal(str)                 # a track's stream address is known: play "ytm:<videoId>"
    failed = Signal(str, str)           # a track could not be played: id, reason
    _done = Signal(str, object, int)
    _failed = Signal(str, str, int)
    _resolved = Signal(str, object)
    _unplayable = Signal(str, str)
    _cover = Signal(str, str, object)
    paletteReady = Signal(str)          # the Now Playing colours for this track have been worked out from its cover

    def __init__(self, controller, youtube, parent=None):
        super().__init__(parent)
        self._c = controller
        self._yt = youtube                          # shares its yt-dlp (with your browser choice) and its stream proxy
        self._api = ytm.YtMusic()
        controller.ytm_lyrics = self._api.lyrics
        self._store = ytm.Store()
        self._query = ""
        self._kind = "songs"
        self._res: dict[str, list[dict]] = {k: [] for k in KINDS}
        self._loaded: set[str] = set()
        self._stack: list[dict] = []                # pages opened from search: album, artist, playlist; the last one shows
        self._known: dict[str, dict] = {}
        self._busy: set[str] = set()
        self._token = 0
        self._latest: dict[str, int] = {}
        self._error = ""
        self._playing = ""                          # the track whose stream is being looked up
        self._streams: dict[str, tuple[float, dict]] = {}   # video id -> (when, stream): replays and the next song start at once
        self._inflight: set[str] = set()
        self._done.connect(self._on_done)
        self._failed.connect(self._on_failed)
        self._resolved.connect(self._on_resolved)
        self._unplayable.connect(self._on_unplayable)
        self._cover.connect(self._on_cover)

    # ---- background work ---------------------------------------------------------------
    def _job(self, key: str, fn, replace: bool = True):
        if key in self._busy and not replace:
            return
        self._busy.add(key)
        self._token += 1
        token = self._latest[key] = self._token
        self._error = ""
        self.changed.emit()

        def work():
            try:
                data = fn()
            except Exception as e:
                self._failed.emit(key, str(e) or e.__class__.__name__, token)
                return
            self._done.emit(key, data, token)
        threading.Thread(target=work, daemon=True).start()

    def _current(self, key: str, token: int) -> bool:
        return self._latest.get(key) == token

    def _remember(self, tracks):
        for t in tracks or []:
            if isinstance(t, dict) and t.get("id"):
                self._known[t["id"]] = t

    def _on_done(self, key: str, data, token: int):
        if not self._current(key, token):
            return
        self._busy.discard(key)
        if key.startswith("s:"):
            kind = key[2:]
            self._res[kind] = data
            self._loaded.add(kind)
            if kind == "songs":
                self._remember(data)
        elif key.startswith("v:"):
            want = key[2:]
            for page in self._stack:
                if page.get("key") == want:
                    page.update(data)
                    page["loading"] = False
                    self._remember(data.get("tracks"))
                    self._remember(data.get("songs"))
        self.changed.emit()

    def _on_failed(self, key: str, msg: str, token: int):
        if not self._current(key, token):
            return
        _log(f"{key}: {msg}")
        self._busy.discard(key)
        self._error = msg
        if key.startswith("v:"):
            for page in self._stack:
                if page.get("key") == key[2:]:
                    page["loading"] = False
                    page["error"] = msg
        self.changed.emit()

    # ---- searching ------------------------------------------------------------------------
    @Slot(str)
    def search(self, query: str):
        q = query.strip()
        if not q:
            return
        self._query = q
        self._res = {k: [] for k in KINDS}
        self._loaded = set()
        self._stack = []
        self._fetch(self._kind)
        self.changed.emit()

    @Slot(str)
    def showKind(self, kind: str):
        if kind not in KINDS:
            return
        self._kind = kind
        if self._query and kind not in self._loaded:
            self._fetch(kind)
        self.changed.emit()

    def _fetch(self, kind: str):
        q = self._query
        self._job(f"s:{kind}", lambda: self._api.search(q, kind))

    # ---- album, artist and playlist pages -------------------------------------------------
    def _open(self, typ: str, ident: str, title: str, cover: str, fn):
        key = f"{typ}:{ident}"
        page = {"key": key, "type": typ, "id": ident, "title": title, "cover": cover, "loading": True, "error": ""}
        self._stack.append(page)
        self._job(f"v:{key}", fn, replace=True)
        self.changed.emit()

    @Slot(str, str, str)
    def openAlbum(self, browse_id: str, title: str, cover: str):
        self._open("album", browse_id, title, cover, lambda: self._api.album(browse_id))

    @Slot(str, str, str)
    def openArtist(self, channel_id: str, name: str, cover: str):
        if not channel_id:
            return
        self._open("artist", channel_id, name, cover, lambda: self._api.artist(channel_id))

    @Slot(str, str, str)
    def openPlaylist(self, playlist_id: str, title: str, cover: str):
        self._open("playlist", playlist_id, title, cover, lambda: self._api.playlist(playlist_id))

    @Slot()
    def back(self):
        if self._stack:
            self._stack.pop()
            self.changed.emit()

    @Slot()
    def home(self):
        """Back to the start page: forget the search and anything opened from it."""
        self._stack = []
        self._query = ""
        self._res = {k: [] for k in KINDS}
        self._loaded = set()
        self._error = ""
        self.changed.emit()

    @Slot()
    def retry(self):
        if self._stack:
            page = self._stack.pop()
            fn = {"album": self.openAlbum, "artist": self.openArtist, "playlist": self.openPlaylist}.get(page["type"])
            if fn:
                fn(page["id"], page["title"], page["cover"])
        elif self._query:
            self._fetch(self._kind)

    # ---- playing -----------------------------------------------------------------------------
    @Slot("QVariantList", int, result=str)
    def setQueue(self, tracks, index: int):
        """Make `tracks` the queue, starting at `index`, and return that track's id ("" if there is nothing to play).
        The music player then asks for its stream with play()."""
        rows = [t for t in tracks if isinstance(t, dict) and t.get("id")]
        if not rows or not 0 <= index < len(rows):
            return ""
        self._remember(rows)
        ids = [t["id"] for t in rows]
        self._c.start_queue_ids(ids, ids[index])
        return ids[index]

    @Slot(str)
    def play(self, track_id: str):
        t = self._known.get(track_id)
        if not t:
            self.failed.emit(track_id, "That track isn’t known any more. Search for it again.")
            return
        self._c.yt_info[track_id] = self._info(t)             # the title and cover show while the stream is being found
        self.changed.emit()
        dlp = self._yt._dlp
        vid = t["vid"]
        self._playing = track_id
        self._fetch_cover(track_id, t.get("cover", ""))

        cached = self._cached(vid)
        if cached:
            _log(f"{vid}: stream already known")
            QTimer.singleShot(0, lambda: self._on_resolved(track_id, cached))
            return

        def work():
            t0 = time.time()
            try:
                info = dlp.resolve_audio(vid)
            except Exception as e:
                self._unplayable.emit(track_id, str(e) or e.__class__.__name__)
                return
            self._streams[vid] = (time.time(), info)
            _log(f"{vid}: stream found in {time.time() - t0:.1f}s")
            self._resolved.emit(track_id, info)
        threading.Thread(target=work, daemon=True).start()

    def _cached(self, vid: str):
        got = self._streams.get(vid)
        return got[1] if got and time.time() - got[0] < STREAM_TTL else None

    def _prefetch(self):
        """Look up the stream of the song after this one while this one plays, so Next and the end of a song are instant."""
        nxt = self._c._pq.peek()
        t = self._known.get(nxt) if nxt.startswith(ytm.PREFIX) else None
        if not t or self._cached(t["vid"]) or t["vid"] in self._inflight:
            return
        vid = t["vid"]
        self._inflight.add(vid)
        dlp = self._yt._dlp

        def work():
            t0 = time.time()
            try:
                self._streams[vid] = (time.time(), dlp.resolve_audio(vid))
                _log(f"{vid}: next song ready in advance ({time.time() - t0:.1f}s)")
            except Exception as e:
                _log(f"{vid}: couldn’t look ahead: {e}")
            finally:
                self._inflight.discard(vid)
        threading.Thread(target=work, daemon=True).start()

    def _fetch_cover(self, track_id: str, url: str):
        """Download the cover (it is small) to colour the Now Playing backdrop from it."""
        if not url or "c:" + url in self._c._palettes:
            return

        def work():
            import urllib.request
            try:
                req = urllib.request.Request(url, headers={"User-Agent": ytm_ua})
                with urllib.request.urlopen(req, timeout=10) as r:
                    self._cover.emit(track_id, url, r.read(2_000_000))
            except Exception as e:
                _log(f"cover {url[:60]}: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _on_cover(self, track_id: str, url: str, data):
        from PySide6.QtGui import QImage
        pal = self._c.palette_from_image(QImage.fromData(data))
        if pal:
            self._c._palettes["c:" + url] = pal.as_dict()
            self.paletteReady.emit(track_id)

    def _info(self, t: dict, url: str = "", dur: float = 0.0) -> dict:
        sub = t.get("artist", "")
        return {"id": t["id"], "title": t.get("title", ""), "sub": sub, "album": t.get("album", ""), "media": "audio",
                "url": url, "exists": True, "pos": 0.0, "dur": dur or t.get("dur", 0.0), "subDelay": 0.0,
                "cover": t.get("cover", ""), "icon": "audio-x-generic", "ytm": True, "loading": not url}

    def _on_resolved(self, track_id: str, info: dict):
        if getattr(self, "_playing", "") != track_id:           # you picked something else while this was loading
            return
        t = self._known.get(track_id) or {"id": track_id, "vid": track_id[len(ytm.PREFIX):]}
        proxy = self._yt._proxy
        url = proxy.add_file(info["file"], info.get("size", 0), info.get("proc")) if info.get("file") else proxy.add(info["url"], info.get("headers"))
        self._c.yt_info[track_id] = self._info(t, url, float(info.get("dur") or 0))
        self._store.played(t)
        self.ready.emit(track_id)
        self._prefetch()
        self.changed.emit()

    def _on_unplayable(self, track_id: str, msg: str):
        _log(f"{track_id}: {msg}")
        if getattr(self, "_playing", "") == track_id:
            self.failed.emit(track_id, msg)

    @Slot(str, result="QVariantMap")
    def infoFor(self, track_id: str):
        return self._c.yt_info.get(track_id, {})

    # ---- what the page shows ---------------------------------------------------------------
    @Property(str, notify=changed)
    def query(self):
        return self._query

    @Property(str, notify=changed)
    def kind(self):
        return self._kind

    @Property("QVariantList", notify=changed)
    def songs(self):
        return self._res["songs"]

    @Property("QVariantList", notify=changed)
    def albums(self):
        return self._res["albums"]

    @Property("QVariantList", notify=changed)
    def artists(self):
        return self._res["artists"]

    @Property("QVariantList", notify=changed)
    def playlists(self):
        return self._res["playlists"]

    @Property("QVariantList", notify=changed)
    def recent(self):
        return self._store.recent

    @Property("QVariantMap", notify=changed)
    def view(self):
        return dict(self._stack[-1]) if self._stack else {}

    @Property(bool, notify=changed)
    def inView(self):
        return bool(self._stack)

    @Property(bool, notify=changed)
    def busy(self):
        return bool(self._busy)

    @Property(bool, notify=changed)
    def busySearch(self):
        return any(k.startswith("s:") for k in self._busy)

    @Property(bool, notify=changed)
    def searched(self):
        return bool(self._query)

    @Property(bool, notify=changed)
    def kindLoaded(self):
        return self._kind in self._loaded

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Slot()
    def clearRecent(self):
        self._store.clear_recent()
        self.changed.emit()
