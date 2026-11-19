"""YouTube for the Qt screens: runs yt-dlp and the RSS feeds off the UI thread and answers the QML page. See core/youtube.py."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..core import youtube as yt
from ..core.streamproxy import StreamProxy


def _log(msg: str) -> None:
    print(f"marquee: youtube: {msg}", flush=True)


class YoutubeController(QObject):
    changed = Signal()
    _progress = Signal(str)
    imported = Signal(int, int)         # channels added, channels already subscribed
    ready = Signal(str)                 # a video's stream address is known: play "yt:<id>"
    shortReady = Signal(str)            # the same for a Short, which plays inside the Shorts page instead
    shortFailed = Signal(str, str)      # a Short's id and why it could not be opened
    _done = Signal(str, object)
    _failed = Signal(str, str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._c = controller
        controller.yt_info = {}
        controller.yt_save = self._save_position
        controller.yt_neighbor = self._neighbor
        self._beginUpgrade.connect(self._start_upgrade)
        self._current_file = ""
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self._on_quit)
        self._store = yt.Store()
        self._dlp = yt.YtDlp(browser=self._store.browser)
        self._feed: list[dict] = self._store.load_feed()
        self._proxy = StreamProxy()
        self._recs: list[dict] = []
        self._results: list[dict] = []
        self._chan_results: list[dict] = []
        self._channel: dict = {}
        self._playlists: list[dict] = []
        self._pl_results: list[dict] = []
        self._playlist: dict = {}
        self._queue: list[str] = []                 # video ids that play one after another (a playlist)
        self._known: dict[str, dict] = {}
        self._shorts: set[str] = set()              # ids being opened for the Shorts page (waiting or running)
        self._short_run: set[str] = set()           # ... the ones being fetched right now
        self._short_queue: list[tuple[str, dict]] = []   # ... and those waiting for a turn
        self._short_files: dict[str, str] = {}      # the file each fetched Short plays from
        self._busy: set[str] = set()
        self._latest: dict[str, int] = {}
        self._error = ""
        self._done.connect(self._on_done)
        self._note = ""
        self._resume: dict[str, float] = {}
        self._upgrade_res: dict[str, int] = {}
        self._upgraded: dict[str, dict] = {}
        self._progress.connect(self._on_progress)
        self._dlp.progress = self._progress.emit
        self._failed.connect(self._on_failed)

    # ---- background work -----------------------------------------------------------
    def _job(self, key: str, fn, replace: bool = False):
        """Run fn off the UI thread. With replace, a newer request wins and the older one's answer is dropped."""
        if key in self._busy and not replace:
            return
        self._busy.add(key)
        self._token = getattr(self, "_token", 0) + 1
        token = self._latest[key] = self._token
        self._error = ""
        self.changed.emit()

        def work():
            try:
                data = fn()
            except Exception as e:  # yt-dlp or the network: say so on the page
                if self._latest.get(key) == token:
                    self._failed.emit(key, str(e) or e.__class__.__name__)
                return
            if self._latest.get(key) == token:
                self._done.emit(key, data)
        threading.Thread(target=work, daemon=True).start()

    def _remember(self, vids):
        for v in vids:
            self._known[v["id"]] = v

    def _on_done(self, key: str, data):
        self._busy.discard(key)
        self._note = ""
        if key == "feed":
            self._feed = data
            self._store.save_feed(data)
        elif key == "avatars":
            for cid, url in data.items():
                self._store.set_thumb(cid, url)
            data = []
        elif key == "recs":
            self._recs = data
        elif key == "search":
            self._results = data
        elif key == "channels":
            self._chan_results = data
        elif key == "channel":
            self._channel = data
            data = data["videos"]
        elif key == "chanurl":
            self.openChannel(data["id"], data["name"])
            data = []
        elif key == "plsearch":
            self._pl_results = data
            data = []
        elif key == "playlists":
            self._playlists = data
            data = []
        elif key == "playlist":
            self._playlist = data
            data = data["videos"]
        elif key.startswith("up:"):
            if data:
                self._upgraded[key[3:]] = data
                self.upgradeReady.emit(key[3:])
            data = []
        elif key.startswith("play:"):
            self._finish_play(data)
            data = []
        if isinstance(data, list):
            self._remember([v for v in data if isinstance(v, dict) and "title" in v and "dur" in v])
        self.changed.emit()

    def _on_progress(self, text: str):
        self._note = text
        self.changed.emit()

    def _on_failed(self, key: str, msg: str):
        _log(f"{key}: {msg}")
        if key.startswith("play:") and key[5:] in self._shorts:
            self._shorts.discard(key[5:])
            self._short_run.discard(key[5:])
            self._busy.discard(key)
            self.shortFailed.emit(key[5:], msg)
            self._pump_shorts()
            return
        self._note = ""
        self._busy.discard(key)
        self._error = msg
        self.changed.emit()

    # ---- what the page shows ---------------------------------------------------------
    def _dress(self, vids):
        now = None
        out = []
        for v in vids:
            r = dict(v)
            r["durText"] = "LIVE" if v.get("live") else (yt.clock(v["dur"]) if v.get("dur") else "")
            r["viewsText"] = (yt.short_count(v["views"]) + " views") if v.get("views") else ""
            r["agoText"] = yt.ago(v.get("published", 0), now)
            out.append(r)
        return out

    positionsChanged = Signal()
    _beginUpgrade = Signal(str, object)   # from the lookup thread: start the bigger download at once
    upgradeReady = Signal(str)          # the bigger picture for this video has finished downloading

    def _on_quit(self):
        if self._store.cache == "exit":
            yt.clear_cache()

    @Property(bool, notify=changed)
    def listMode(self):
        return self._store.list_mode

    @Slot(bool)
    def setListMode(self, on: bool):
        self._store.set_list_mode(on)
        self.changed.emit()

    @Property(str, notify=changed)
    def cacheMode(self):
        return self._store.cache

    @Slot(str)
    def setCacheMode(self, mode: str):
        self._store.set_cache(mode)
        self.changed.emit()

    @Property(str, notify=changed)
    def cacheText(self):
        b = yt.cache_size()
        return "empty" if b < 1_000_000 else (f"{b / 1e9:.1f} GB" if b >= 1e9 else f"{b / 1e6:.0f} MB")

    @Slot()
    def clearCache(self):
        """Delete saved videos, but not the one that is playing."""
        yt.clear_cache(keep=self._current_file)
        self.changed.emit()

    @Property(bool, notify=changed)
    def fastStart(self):
        return self._store.fast

    @Slot(bool)
    def setFastStart(self, on: bool):
        self._store.set_fast(on)
        self.changed.emit()

    @Property(str, notify=changed)
    def upgrading(self):
        """"<video id>:<size>" while a bigger picture downloads behind the one that is playing."""
        return next((k[3:] for k in self._busy if k.startswith("up:")), "")

    def _start_upgrade(self, vid: str, up: dict):
        res, meta = up["res"], up["meta"]
        self._upgrade_res[vid] = res

        def work():
            try:
                return self._dlp.fetch_dash(vid, meta, res)
            except Exception as e:
                _log(f"upgrade to {res}p failed: {e}")
                return None
        self._job(f"up:{vid}", work)

    @Slot(str, float)
    def applyUpgrade(self, vid: str, seconds: float):
        """Switch the playing video to the bigger picture that has arrived, from where you are."""
        info = self._upgraded.pop(vid, None)
        if info:
            info.pop("upgrade", None)
            self._resume[vid] = max(0.0, seconds)
            self._finish_play(info)

    @Property("QVariantMap", notify=changed)
    def upgradeInfo(self):
        """{id, note} for the bigger picture being fetched, or {}."""
        k = self.upgrading
        return {"id": k, "note": self._note} if k else {}

    def _save_position(self, vid: str, pos: float, dur: float):
        if (self._c.yt_info.get(f"yt:{vid}") or {}).get("isLive"):
            return
        if dur < 0:
            self._store.forget(vid)
        else:
            self._store.set_position(vid, pos, dur)
        self.positionsChanged.emit()

    @Property("QVariantMap", notify=positionsChanged)
    def positions(self):
        """video id -> fraction watched (0..1), for the progress bar on a video's card."""
        return {k: min(1.0, v["pos"] / v["dur"]) for k, v in self._store.positions.items() if v.get("dur")}

    @Slot(str)
    def copy(self, text: str):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(text)

    @Slot()
    def recheck(self):
        self.changed.emit()

    @Property(bool, notify=changed)
    def available(self):
        return bool(yt.find_ytdlp())

    @Property("QVariantList", notify=changed)
    def subs(self):
        return self._store.subs

    @Property("QVariantList", notify=changed)
    def feed(self):
        return self._dress(self._feed)

    @Property("QVariantList", notify=changed)
    def recommended(self):
        return self._dress(self._recs)

    @Property("QVariantList", notify=changed)
    def results(self):
        return self._dress(self._results)

    @Property("QVariantList", notify=changed)
    def channelResults(self):
        return [dict(c, subbed=self._store.subscribed(c["id"])) for c in self._chan_results]

    @Property("QVariantList", notify=changed)
    def playlistResults(self):
        return self._pl_results

    @Property("QVariantList", notify=changed)
    def playlists(self):
        return self._playlists

    @Property("QVariantMap", notify=changed)
    def playlist(self):
        if not self._playlist:
            return {}
        return {"id": self._playlist["id"], "title": self._playlist.get("title", ""), "videos": self._dress(self._playlist.get("videos", []))}

    @Property(bool, notify=changed)
    def busyPlaylists(self):
        return "playlists" in self._busy

    @Property(bool, notify=changed)
    def busyPlaylist(self):
        return "playlist" in self._busy

    @Property("QVariantMap", notify=changed)
    def channel(self):
        if not self._channel:
            return {}
        return {"id": self._channel["id"], "name": self._channel.get("name", ""), "videos": self._dress(self._channel.get("videos", [])),
                "subbed": self._store.subscribed(self._channel["id"])}

    @Property("QVariantList", notify=changed)
    def history(self):
        return self._dress(self._store.history)

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(bool, notify=changed)
    def busyFeed(self):
        return "feed" in self._busy

    @Property(bool, notify=changed)
    def busyRecs(self):
        return "recs" in self._busy

    @Property(bool, notify=changed)
    def busySearch(self):
        return "search" in self._busy or "channels" in self._busy or "plsearch" in self._busy

    @Property(bool, notify=changed)
    def busyChannel(self):
        return "channel" in self._busy

    @Property(str, notify=changed)
    def resolving(self):
        return next((k[5:] for k in self._busy if k.startswith("play:")), "")

    @Property(bool, notify=changed)
    def needsJs(self):
        return not yt.has_js_runtime()

    @Property(str, notify=changed)
    def note(self):
        return self._note

    @Property(str, notify=changed)
    def browser(self):
        return self._store.browser

    @Property("QVariantList", constant=True)
    def browsers(self):
        names = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "librewolf": "LibreWolf"}
        return [["", "Off (no account)"]] + [[b, names.get(b, b.capitalize())] for b in yt.BROWSERS]

    # ---- actions -----------------------------------------------------------------------
    @Slot()
    def refreshFeed(self):
        subs = list(self._store.subs)
        old = list(self._feed)

        def work():
            errors: list = []
            failed: list = []
            feed = yt.subscription_feed(subs, errors=errors, failed=failed)
            if errors:
                _log(f"feed: {len(errors)} of {len(subs)} channels failed, e.g. {errors[0]}")
            if failed:
                # YouTube's feed addresses sometimes answer 404 for everyone; ask yt-dlp for those channels' latest videos instead
                from concurrent.futures import ThreadPoolExecutor

                def one(sub):
                    try:
                        return self._dlp.channel(sub["id"], n=6)["videos"]
                    except Exception:
                        return []
                with ThreadPoolExecutor(max_workers=6) as pool:
                    extra = [v for v in pool.map(one, failed) if v]
                _log(f"feed: {len(extra)} of {len(failed)} failed channels loaded through yt-dlp")
                if extra:
                    have = {v["id"] for v in feed}
                    feed = feed + [v for v in yt.interleave(extra) if v["id"] not in have]
            if not feed and old:
                return old                              # offline or blocked: keep what we had
            if not feed and errors:
                raise RuntimeError(f"Couldn’t load channel feeds ({errors[0]})")
            return feed
        self._job("feed", work)
        self.fetchAvatars()

    @Slot()
    def fetchAvatars(self):
        """Channel pictures aren't in Takeout or the feeds, so read them off each channel's page once."""
        need = [s["id"] for s in self._store.subs if not s.get("thumb")]
        if not need:
            return

        def work():
            from concurrent.futures import ThreadPoolExecutor

            def one(cid):
                try:
                    return cid, yt.fetch_avatar(cid)
                except Exception:
                    return cid, ""
            with ThreadPoolExecutor(max_workers=6) as pool:
                return {cid: url for cid, url in pool.map(one, need) if url}
        self._job("avatars", work)

    @Slot()
    def refreshRecommended(self):
        if self._store.browser:
            self._job("recs", self._dlp.recommended)

    @Slot(str, bool)
    def search(self, query: str, channels: bool):
        query = query.strip()
        if not query:
            return
        self._results, self._chan_results = [], []
        if channels:
            self._job("channels", lambda: self._dlp.search_channels(query), replace=True)
        else:
            self._job("search", lambda: self._dlp.search(query), replace=True)

    @Slot(str, result=bool)
    def openUrl(self, text: str):
        """A pasted YouTube address: play the video, open the playlist or the channel. False if it isn't one."""
        kind, ident = yt.parse_url(text)
        if kind == "video":
            self.play(ident)
        elif kind == "playlist":
            self._queue = []
            self.openPlaylist(ident, "Playlist")
        elif kind == "channel":
            if ident.startswith("UC"):
                self.openChannel(ident, "")
            else:
                self._job("chanurl", lambda: self._dlp.resolve_channel(ident), replace=True)
        else:
            return False
        return True

    @Slot(str)
    def searchPlaylists(self, query: str):
        self._pl_results = []
        self._job("plsearch", lambda: self._dlp.search_playlists(query.strip()), replace=True)

    @Slot(str)
    def searchLive(self, query: str):
        self._job("search", lambda: self._dlp.search_live(query.strip() or "live"), replace=True)

    @Slot(str, str)
    def openChannel(self, cid: str, name: str):
        self._channel = {"id": cid, "name": name, "videos": []}
        self._playlists = []
        self._playlist = {}
        self.changed.emit()

        def work():
            d = self._dlp.channel(cid)
            d["id"] = cid
            d["name"] = d["name"] or name
            return d
        self._job("channel", work, replace=True)

    @Slot()
    def closeChannel(self):
        self._channel = {}
        self._playlists = []
        self._playlist = {}
        self.changed.emit()

    @Slot()
    def loadPlaylists(self):
        """The channel that is open: list its playlists (once)."""
        cid = self._channel.get("id")
        if not cid or self._playlists:
            return
        self._job("playlists", lambda: self._dlp.channel_playlists(cid), replace=True)

    @Slot(str, str)
    def openPlaylist(self, pid: str, title: str):
        self._playlist = {"id": pid, "title": title, "videos": []}
        self.changed.emit()

        def work():
            d = self._dlp.playlist(pid)
            d["id"] = pid
            d["title"] = d["title"] or title
            return d
        self._job("playlist", work, replace=True)

    @Slot()
    def closePlaylist(self):
        self._playlist = {}
        self.changed.emit()

    @Slot(str)
    def playInPlaylist(self, vid: str):
        """Play a video from the open playlist; the rest of the list follows it."""
        self._queue = [v["id"] for v in self._playlist.get("videos", [])]
        self.play(vid)

    def _neighbor(self, vid: str, delta: int) -> str:
        try:
            i = self._queue.index(vid) + delta
        except ValueError:
            return ""
        return f"yt:{self._queue[i]}" if 0 <= i < len(self._queue) else ""

    @Slot(str, str, str)
    def subscribe(self, cid: str, name: str, thumb: str):
        self._store.subscribe(cid, name, thumb)
        self.changed.emit()
        self.refreshFeed()

    @Slot("QUrl")
    def importTakeout(self, url):
        """Subscribe to every channel in a Google Takeout subscriptions.csv."""
        try:
            path = url.toLocalFile() if hasattr(url, "toLocalFile") else str(url)
            with open(path, encoding="utf-8", errors="replace") as f:
                rows = yt.parse_takeout(f.read())
        except OSError as e:
            self._error = f"Couldn’t read that file: {e}"
            self.changed.emit()
            return
        new = [r for r in rows if not self._store.subscribed(r[0])]
        for cid, name in new:
            self._store.subscribe(cid, name)
        self._error = "" if rows else "No channels found. Pick the subscriptions.csv from your Takeout export."
        self.changed.emit()
        self.imported.emit(len(new), len(rows) - len(new))
        if new:
            self.refreshFeed()

    @Slot(str)
    def unsubscribe(self, cid: str):
        self._store.unsubscribe(cid)
        self._feed = [v for v in self._feed if v.get("channelId") != cid]
        self.changed.emit()

    @Slot()
    def clearHistory(self):
        self._store.clear_history()
        self.changed.emit()

    @Slot(str)
    def setBrowser(self, name: str):
        self._store.set_browser(name)
        self._dlp = yt.YtDlp(browser=self._store.browser)
        self._dlp.progress = self._progress.emit
        self._recs = []
        self.changed.emit()

    @Slot(str)
    def play(self, video_id: str, fast: bool = True):
        """Look up the stream for a video, then say it is ready."""
        quality, speed = self._store.quality, self._store.speed
        self._dlp.keep = self._store.keep_count
        fast = fast and self._store.fast
        self._job(f"play:{video_id}", lambda: self._dlp.resolve(
            video_id, quality=quality, speed=speed, fast=fast,
            on_upgrade=lambda up: self._beginUpgrade.emit(video_id, up)))

    SHORTS_AT_ONCE = 2                                  # Shorts fetched in the background at the same time
    SHORTS_KEEP = 8                                     # fetched Shorts kept on disk: the one playing, the last, and those coming

    @Slot(str, "QVariantMap")
    def playShort(self, video_id: str, known=None):
        """Get a Short's stream for the Shorts page, at the quality chosen for YouTube, right now; it says shortReady when done."""
        self._want_short(video_id, known or {}, now=True)

    @Slot(str, "QVariantMap")
    def prefetchShort(self, video_id: str, known=None):
        """Start getting a Short that is coming up, a couple at a time, so it is ready when you move on to it."""
        self._want_short(video_id, known or {}, now=False)

    @Slot()
    def dropShortQueue(self):
        """Forget the Shorts waiting their turn (you jumped somewhere else); those already being fetched carry on."""
        for vid, _ in self._short_queue:
            self._shorts.discard(vid)
        self._short_queue = []

    def _short_ready(self, video_id: str) -> bool:
        info = self._c.yt_info.get(f"yt:{video_id}")
        path = self._short_files.get(video_id, "")
        return bool(info) and (not path or Path(path).exists())

    def _want_short(self, video_id: str, known: dict, now: bool):
        if self._short_ready(video_id):
            if now:
                self.shortReady.emit(video_id)
            return
        self._c.yt_info.pop(f"yt:{video_id}", None)             # its file is gone: fetch it again
        if video_id in self._short_run:
            return                                              # already on its way; shortReady comes when it lands
        self._short_queue = [q for q in self._short_queue if q[0] != video_id]
        self._shorts.add(video_id)
        if now:
            self._start_short(video_id, known)
        else:
            self._short_queue.append((video_id, known))
            self._pump_shorts()

    def _pump_shorts(self):
        while self._short_queue and len(self._short_run) < self.SHORTS_AT_ONCE:
            vid, known = self._short_queue.pop(0)
            self._start_short(vid, known)

    def _start_short(self, video_id: str, known: dict):
        self._short_run.add(video_id)
        quality, speed = self._store.quality, self._store.speed
        self._dlp.keep = max(self._store.keep_count, self.SHORTS_KEEP)
        self._job(f"play:{video_id}", lambda: self._dlp.resolve_short(video_id, dict(known), quality=quality, speed=speed))

    @Slot(str, float, str)
    def reopen(self, video_id: str, seconds: float, quality: str):
        """Play the same video again at another quality, from where you were."""
        self._store.set_quality(quality)
        self._resume[video_id] = max(0.0, seconds)
        self.changed.emit()
        self.play(video_id, fast=False)

    @Slot(str)
    def setQuality(self, quality: str):
        self._store.set_quality(quality)
        self.changed.emit()

    @Property(str, notify=changed)
    def quality(self):
        return self._store.quality

    def _finish_play(self, info: dict):
        vid = info["id"]
        if info.get("speed"):
            old = self._store.speed
            self._store.set_speed(0.5 * old + 0.5 * info["speed"] if old else info["speed"])
        known = self._known.get(vid, {})
        if vid not in self._shorts:
            self._current_file = info.get("file", "")
        else:
            self._short_files[vid] = info.get("file", "")
        up = info.get("upgrade")
        if up:
            self._start_upgrade(vid, up)
        self._c.yt_info[f"yt:{vid}"] = {
            "id": f"yt:{vid}", "title": info["title"] or known.get("title", ""), "sub": info["channel"] or known.get("channel", ""),
            "levels": [l["res"] for l in info.get("levels", []) if 360 <= l["res"] <= 1080], "res": info.get("res", 360),
            "quality": self._store.quality, "upgradeTo": (up or {}).get("res", 0), "media": "video", "url": (self._proxy.add_file(info["file"], info.get("size", 0), info.get("proc")) if info.get("file")
                    else info["url"] if info["live"] else self._proxy.add(info["url"], info.get("headers"))), "exists": True, "pos": self._resume.pop(vid, None) if vid in self._resume else (0.0 if info["live"] else self._store.resume_at(vid)), "dur": info["dur"], "subDelay": 0.0,
            "cover": info["thumb"], "icon": "video-x-generic", "yt": True, "isLive": info["live"]}
        row = dict(known) if known else {"id": vid}
        row.update({"id": vid, "title": info["title"] or row.get("title", ""), "channel": info["channel"] or row.get("channel", ""),
                    "thumb": row.get("thumb") or info["thumb"], "dur": info["dur"] or row.get("dur", 0)})
        if vid in self._shorts:                         # opened by the Shorts page: it plays it itself, and history isn't touched
            self._shorts.discard(vid)
            self._short_run.discard(vid)
            self.shortReady.emit(vid)
            self._pump_shorts()
            return
        self._store.watched(row)
        self.ready.emit(f"yt:{vid}")
