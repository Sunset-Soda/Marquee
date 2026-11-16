"""Qt glue: exposes the shared Library to QML. No styling lives here."""
from __future__ import annotations

import threading
import time as _time

from PySide6.QtCore import (Property, QAbstractListModel, QByteArray, QModelIndex, QObject, QUrl, Qt, Signal, Slot)
from PySide6.QtGui import QImage

from ..core import artwork, iptv, lyrics, opensubs, palette, playqueue, spectrum, subsync, subtitles, tv
from ..core.library import ALL, KIND, KINDS, Item, Library, path_kind
from ..core.nav import Navigator, View


def _url(path: str) -> str:
    return QUrl.fromLocalFile(path).toString() if path else ""


class ItemModel(QAbstractListModel):
    (IdRole, TitleRole, SubRole, KindRole, CoverRole, RatingRole, FavRole, ProgressRole, DurationRole, MediaRole,
     RatioRole, IconRole, NodeRole, ShowKeyRole) = (Qt.UserRole + i for i in range(1, 15))
    _names = {IdRole: b"itemId", TitleRole: b"title", SubRole: b"sub", KindRole: b"kind", CoverRole: b"cover",
              RatingRole: b"rating", FavRole: b"fav", ProgressRole: b"progress", DurationRole: b"duration",
              MediaRole: b"media", RatioRole: b"ratio", IconRole: b"iconName", NodeRole: b"node", ShowKeyRole: b"showKey"}

    def __init__(self, lib: Library, parent=None):
        super().__init__(parent)
        self._lib = lib
        self._items: list[Item] = []

    def roleNames(self):
        return {k: QByteArray(v) for k, v in self._names.items()}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        it = self._items[index.row()]
        k = KIND.get(it.kind, KINDS[0])
        return {
            self.IdRole: it.id, self.TitleRole: it.title, self.SubRole: it.sub, self.KindRole: it.kind,
            self.CoverRole: _url(self._lib.cover_for(it)),
            self.RatingRole: it.rating, self.FavRole: it.fav, self.ProgressRole: it.progress, self.DurationRole: it.dur,
            self.MediaRole: it.media, self.RatioRole: k.ratio, self.IconRole: k.qt_icon,
            self.NodeRole: it.node, self.ShowKeyRole: it.show_key,
        }.get(role)

    def reset(self, items: list[Item]):
        new = list(items)
        if [i.id for i in new] == [i.id for i in self._items]:
            # Same rows in the same order (a cover arrived, a rating changed): refresh them in place. A full reset
            # would throw the view back to the top.
            self._items = new
            if new:
                self.dataChanged.emit(self.index(0), self.index(len(new) - 1))
            return
        self.beginResetModel()
        self._items = new
        self.endResetModel()

    def touch(self, item_id: str):
        for row, it in enumerate(self._items):
            if it.id == item_id:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx)
                return

    @property
    def ids(self) -> list[str]:
        return [i.id for i in self._items if not i.node]


def _log(msg: str) -> None:
    import sys
    print("marquee: " + msg, file=sys.stderr, flush=True)


class Controller(QObject):
    modelChanged = Signal()
    collectionsChanged = Signal()
    stateChanged = Signal()
    toast = Signal(str)
    # worker threads emit these; Qt queues them onto the main thread
    _refresh = Signal()
    _say = Signal(str)
    # subtitles: (item id, [{id, label, source}], selected track id or "", note shown when the list is empty)
    subtitleTracksReady = Signal(str, "QVariantList", str, str)
    _subs_found = Signal(str, object, str)
    _cues_ready = Signal(str, str, object)
    _sync_done = Signal(str, str, object, bool)
    subtitleTimingChanged = Signal(str, float)
    _reopen = Signal(str)
    playbackModeChanged = Signal()
    vizStyleChanged = Signal()
    lyricsReady = Signal(str, "QVariantList", bool, str)
    _lyrics_done = Signal(str, object)
    liveChanged = Signal()
    _live_done = Signal(str)

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self.lib = library
        self.model = ItemModel(library, self)
        self.nav = Navigator(View(*self.lib.start_view()))       # reopen where you left off
        self._analyzer: spectrum.Analyzer | None = None
        self._palettes: dict[str, dict] = {}
        self._busy = 0
        self._queue: list[str] = []
        self._albums: list[dict] = []                            # the artist page: every album with its songs
        self._artist_order: list[str] = []
        self._scoped: list[str] = []                             # a shuffled album: the next queue is just its songs
        self._pq = playqueue.PlayQueue()                         # music: shuffle and repeat
        self._pq.shuffle = bool(self.lib.prefs.get("shuffle", False))
        self._pq.set_repeat(str(self.lib.prefs.get("repeat", "off")))
        self._lyrics_done.connect(self._on_lyrics_done)
        self._refresh.connect(self.refresh)
        self._say.connect(self.toast)
        self._sub_item = ""
        self._sub_tracks: list[subtitles.Track] = []
        self._sub_sel = ""
        self._timeline: subtitles.Timeline | None = None
        self._sub_delay = 0.0
        self._sub_scale = 1.0
        self._syncing: set[str] = set()
        self._subs_found.connect(self._on_subs_found)
        self._cues_ready.connect(self._on_cues_ready)
        self._sync_done.connect(self._on_sync_done)
        self._reopen.connect(self._reopen_current)
        self.yt_neighbor = None
        self.yt_save = None                                      # set by YoutubeController: remembers where a video stopped
        self.yt_info: dict = {}                                  # YouTube videos being played (see youtube_ctl.py)
        self._live = iptv.Store()
        self._live_busy = False
        self._live_status = ""
        self._live_view = (iptv.ALL, "")                         # what channel up/down steps through
        self._live_done.connect(self._on_live_done)
        self.refresh()
        if self._live.stale():
            self.refreshLive(False)
        threading.Thread(target=self._fill_art, daemon=True).start()

    def _fill_art(self):
        """Older libraries: read the picture out of music files that have none, then share it across each album."""
        try:
            if self.lib.fill_embedded_art():
                self._refresh.emit()
            opensubs.reset_stale_attempts(self.lib)
            opensubs.repair_duplicates(self.lib)
        except Exception:
            pass

    def _reopen_current(self, item_id: str):
        """A subtitle finished downloading. Show it only if that video is still the one on screen: by now playback
        may have moved to the next episode, and reopening the old one would put its subtitles on the new one."""
        if item_id == self._sub_item:
            self.openSubtitles(item_id)

    # ---- properties --------------------------------------------------
    @Property(QObject, constant=True)
    def items(self):
        return self.model

    @Property("QVariantList", notify=collectionsChanged)
    def collections(self):
        rows = [{"id": k.id, "name": k.name, "icon": k.qt_icon, "count": self.lib.count(k.id)} for k in KINDS]
        return rows

    @Property("QVariantList", constant=True)
    def kinds(self):
        return [{"id": k.id, "name": k.name} for k in KINDS]

    @Property(str, notify=stateChanged)
    def collection(self):
        return self.nav.collection

    @Property(str, notify=stateChanged)
    def collectionName(self):
        return "All Media" if self.nav.collection == ALL else KIND[self.nav.collection].name

    @Property(str, notify=stateChanged)
    def heading(self):
        """Page title: the collection, or Show › Season, or Artist › Album while you are inside one."""
        return self.lib.heading(self.nav.path if self.nav.drilled else (), self.nav.collection)

    @Property(bool, notify=stateChanged)
    def inArtist(self):
        """On an artist's page (not searching): shown as every album with its songs, not as album tiles."""
        p = self.nav.path
        return self.nav.drilled and len(p) == 1 and p[0].startswith("artist:") and not self.nav.query

    @Property("QVariantList", notify=modelChanged)
    def artistAlbums(self):
        return self._albums

    def _build_artist(self):
        self._albums, self._artist_order, self._scoped = [], [], []
        if not self.inArtist:
            return
        key = self.nav.path[0].partition(":")[2]
        for node, songs in self.lib.artist_albums(key):
            rows = [{"itemId": s.id, "id": s.id, "title": s.title, "sub": self.lib.artist_of(s), "kind": "music",
                     "node": "", "fav": s.fav, "dur": s.dur, "track": s.track or n, "showKey": "", "cover": ""}
                    for n, s in enumerate(songs, 1)]
            self._albums.append({"albumId": node.id, "title": node.title, "cover": _url(node.cover), "count": len(rows),
                                 "dur": sum(s.dur for s in songs), "songs": rows})
            self._artist_order += [s.id for s in songs]

    @Slot(str, result=str)
    def shuffleAlbum(self, album_id: str):
        """Shuffle on, and pick a song from this one album; the queue is then just that album."""
        album = next((a for a in self._albums if a["albumId"] == album_id), None)
        if not album or not album["songs"]:
            return ""
        if not self._pq.shuffle:
            self.toggleShuffle()
        import random
        self._scoped = [s["id"] for s in album["songs"]]
        return random.choice(self._scoped)

    @Slot(result=str)
    def shuffleArtist(self):
        """Turn shuffle on and pick a song from this artist to start with ('' when there are none)."""
        if not self._artist_order:
            return ""
        if not self._pq.shuffle:
            self.toggleShuffle()
        import random
        return random.choice(self._artist_order)

    @Property(bool, notify=stateChanged)
    def canGoBack(self):
        return self.nav.can_back

    @Property(bool, notify=stateChanged)
    def canGoForward(self):
        return self.nav.can_forward

    @Property(bool, notify=stateChanged)
    def canGoHome(self):
        return not self.nav.at_home

    @Property(bool, notify=stateChanged)
    def canGoUp(self):
        return self.nav.drilled

    @Property(str, notify=stateChanged)
    def tab(self):
        return self.nav.tab

    @Property(str, notify=stateChanged)
    def query(self):
        return self.nav.query

    @Property(str, notify=stateChanged)
    def searchHint(self):
        """Say what search will look at on this screen."""
        if self.nav.tab != "library":
            return "Search " + ("what you can continue" if self.nav.tab == "continue" else "favorites")
        shows, artists = path_kind(self.nav.path)
        if shows:
            return "Search episodes in this " + ("show" if shows[1] is None else "season")
        if artists:
            return "Search songs by this artist" if artists[1] is None else "Search songs on this album"
        return {"all": "Search shows, movies, artists…", "show": "Search TV shows…", "movie": "Search movies…",
                "music": "Search artists…"}.get(self.nav.collection, "Search…")

    @Property(bool, notify=stateChanged)
    def busy(self):
        return self._busy > 0

    @Property(bool, notify=stateChanged)
    def onContinue(self):
        return self.nav.tab == "continue"

    @Property(str, notify=modelChanged)
    def statusText(self):
        n = len(self.model._items)
        return f"{n} item" + ("" if n == 1 else "s")

    @Property(str, notify=stateChanged)
    def emptyTitle(self):
        if self.nav.query:
            return "No matches"
        if self.nav.tab == "continue":
            return "Nothing to resume"
        if self.nav.tab == "favorites":
            return "No favorites yet"
        return "Your library is empty" if not self.lib.items else f"No {self.collectionName} yet"

    @Property(str, notify=stateChanged)
    def emptyText(self):
        if self.nav.query:
            return "Nothing on this screen matches your search."
        if self.nav.tab == "continue":
            return "Titles you stop partway through appear here."
        if self.nav.tab == "favorites":
            return "Use the context menu on any title to add it to Favorites."
        return "Add a whole folder of shows, movies or music with Add Folder, or drop files or folders into this window."

    @Property(bool, notify=stateChanged)
    def canAdd(self):
        return self.nav.tab == "library" and not self.nav.query

    @Property(bool, notify=stateChanged)
    def translucent(self):
        return bool(self.lib.prefs.get("translucent", True))

    @Slot(bool)
    def setTranslucent(self, on: bool):
        self.lib.prefs["translucent"] = bool(on)
        self.lib.save()
        self.stateChanged.emit()

    # ---- view state --------------------------------------------------
    def _dead(self, path: tuple) -> bool:
        shows, artists = path_kind(path)
        if shows and not any(i.show_key == shows[0] for i in self.lib.items if i.kind == "show"):
            return True
        return bool(artists and not any(self.lib.akey(self.lib.artist_of(i)) == artists[0] for i in self.lib.items if i.kind == "music"))

    @Slot()
    def refresh(self):
        self.nav.prune(self._dead)                                   # the last episode or song was removed
        path = self.nav.path if self.nav.drilled else ()
        items = self.lib.browse(self.nav.collection, self.nav.tab, self.nav.query, path)
        if path and not items and not self.nav.query:                # a season or album emptied: step back out
            self.nav.up()
            items = self.lib.browse(self.nav.collection, self.nav.tab, self.nav.query, self.nav.path)
        self.lib.remember_view(self.nav.collection, self.nav.tab)
        self.model.reset(items)
        self._build_artist()
        self.modelChanged.emit()
        self.collectionsChanged.emit()
        self.stateChanged.emit()

    @Slot(str)
    def setCollection(self, cid: str):
        self.nav.set_collection(cid)
        self.refresh()

    @Slot(str)
    def setTab(self, tab: str):
        self.nav.set_tab(tab)
        self.refresh()

    @Slot(str)
    def setQuery(self, q: str):
        if self.nav.set_query(q):
            self.refresh()

    @Slot()
    def back(self):
        """Back in history, like a browser."""
        if self.nav.back():
            self.refresh()

    @Slot()
    def forward(self):
        if self.nav.forward():
            self.refresh()

    @Slot()
    def home(self):
        if self.nav.home():
            self.refresh()

    @Slot()
    def up(self):
        """One level up: from a season to its show, from a show to the library."""
        if self.nav.up():
            self.refresh()

    @Slot(str, result=str)
    def activate(self, item_id: str) -> str:
        """Open a show, season, artist or album tile in place. Returns the id to play when it was a real item, else ''."""
        if item_id.partition(":")[0] in ("show", "season", "artist", "album"):
            self.nav.open_node(item_id)
            self.refresh()
            return ""
        return item_id

    # ---- Continue ----------------------------------------------------
    @Slot()
    def clearContinue(self):
        n = self.lib.clear_resume()
        self.refresh()
        self.toast.emit(f"Cleared {n} title{'s' if n != 1 else ''} from Continue" if n else "Nothing to clear")

    @Slot(str)
    def removeFromContinue(self, item_id: str):
        if self.lib.clear_resume([item_id]):
            self.refresh()

    # ---- subtitle settings and downloads ------------------------------
    @Slot(result="QVariantMap")
    def subtitleSettings(self):
        c = opensubs.load_credentials()
        return {"apiKey": c.api_key, "username": c.username, "password": c.password, "language": c.language, "auto": c.auto}

    @Slot(str, str, str, str, bool)
    def saveSubtitleSettings(self, key: str, user: str, password: str, language: str, auto: bool):
        opensubs.save_credentials(opensubs.Credentials(key, user, password, language, auto))
        self.toast.emit("Subtitle settings saved")

    @Property(bool, notify=stateChanged)
    def subtitleKeySet(self):
        return opensubs.load_credentials().ready

    @Slot(str)
    def downloadSubtitles(self, item_id: str):
        it = self.lib.by_id(item_id)
        if not it:
            return
        creds = opensubs.load_credentials()
        if not creds.ready:
            self.toast.emit("Add your OpenSubtitles API key in Subtitle Settings first.")
            return
        self.toast.emit("Looking for subtitles…")

        def work():
            status, msg, path = opensubs.fetch_for_item(self.lib, it, creds)
            self._say.emit(msg)
            if status == "ok":
                self._reopen.emit(item_id)
        threading.Thread(target=work, daemon=True).start()

    # ---- adding media ------------------------------------------------
    def _work(self, fn):
        self._busy += 1
        self.stateChanged.emit()

        def run():
            try:
                fn()
            finally:
                self._busy -= 1
                self._refresh.emit()
        threading.Thread(target=run, daemon=True).start()

    @Slot("QVariantList")
    def addUrls(self, urls):
        paths = [QUrl(u).toLocalFile() if not isinstance(u, QUrl) else u.toLocalFile() for u in urls]
        paths = [p for p in paths if p]
        if not paths:
            return

        def job():
            new, skipped = self.lib.add_paths(paths)
            if not new:
                self._say.emit("Nothing new to add. Those files are unsupported or already in the library.")
                return
            self._refresh.emit()
            shows = len({i.show_key for i in new if i.show_key})
            self._say.emit(f"Added {len(new)} title{'s' if len(new) != 1 else ''}" + (f" ({skipped} skipped)" if skipped else "")
                           + (". Looking up episode names and artwork…" if shows else ". Looking for cover art…"))
            self._enrich(new)
        self._work(job)

    def _enrich(self, items: list[Item], force_shows: bool = False):
        rep = artwork.enrich(self.lib, items, notify=self._refresh.emit, force_shows=force_shows)
        self._say.emit(rep.message())

    def _item_or_show_items(self, item_id: str) -> tuple[list[Item], bool]:
        if item_id.startswith("show:") or item_id.startswith("season:"):
            return self.lib.episodes_of(item_id.split(":")[1]), True
        it = self.lib.by_id(item_id)
        if not it:
            return [], False
        return ([*self.lib.episodes_of(it.show_key)], True) if it.kind == "show" and it.show_key else ([it], False)

    @Slot(str)
    def findCover(self, item_id: str):
        items, is_show = self._item_or_show_items(item_id)
        if not items:
            return
        self.toast.emit("Looking up episode names and artwork…" if is_show else "Searching for cover art…")
        self._work(lambda: self._enrich(items, force_shows=is_show))

    @Slot()
    def findAllCovers(self):
        self.toast.emit("Looking for missing artwork and episode names…")
        self._work(lambda: self._enrich(list(self.lib.items)))

    @Slot(str, str)
    def matchShow(self, show_key: str, query: str):
        """Change Match: search TVmaze for the name the user typed and re-apply it to the whole show."""
        if not query.strip() or show_key not in self.lib.shows:
            return
        self.toast.emit(f"Searching for “{query.strip()}”…")

        def job():
            status, text, renamed = tv.scrub(self.lib, show_key, query=query)
            if status == "ok":
                self._say.emit(f"Matched “{text}”" + (f" and named {renamed} episode{'s' if renamed != 1 else ''}" if renamed else ""))
            elif status == "none":
                self._say.emit(f"TVmaze has no show called “{text}”. Try the exact title, with the year if there are several")
            else:
                self._say.emit("Couldn't reach TVmaze. Check your connection and try again")
        self._work(job)

    @Slot(str, result=str)
    def showName(self, show_key: str) -> str:
        info = self.lib.shows.get(show_key)
        return info.name if info and info.name else ""

    @Slot(str, QUrl)
    def setCover(self, item_id: str, url: QUrl):
        it = self.lib.by_id(item_id)
        if it and url.isLocalFile():
            self.lib.set_cover_file(it, url.toLocalFile())
            self.refresh()
            self.toast.emit("Cover updated")

    # ---- item actions ------------------------------------------------
    @Slot(str, int)
    def rate(self, item_id: str, n: int):
        it = self.lib.by_id(item_id)
        if it:
            it.rating = 0 if it.rating == n else n
            self.lib.save()
            self.model.touch(item_id)

    @Slot(str)
    def toggleFav(self, item_id: str):
        it = self.lib.by_id(item_id)
        if it:
            it.fav = not it.fav
            self.lib.save()
            self.refresh()

    @Slot(str, str, str, str)
    def edit(self, item_id: str, title: str, sub: str, kind: str):
        it = self.lib.by_id(item_id)
        if it:
            self.lib.edit(it, title, sub, kind)
            self.refresh()

    @Slot(str)
    def remove(self, item_id: str):
        it = self.lib.by_id(item_id)
        if it:
            self.lib.remove(it)
            self.refresh()

    # ---- playback ----------------------------------------------------
    @Slot(str, result="QVariantMap")
    def itemInfo(self, item_id: str):
        if item_id.startswith("live:"):
            return self._live_info(item_id[5:])
        if item_id.startswith("yt:"):
            return self.yt_info.get(item_id, {})
        it = self.lib.by_id(item_id)
        if not it:
            return {}
        return {"id": it.id, "title": it.title, "sub": it.sub or KIND[it.kind].name, "media": it.media,
                "url": _url(it.path), "exists": it.exists, "pos": it.pos if it.resumable else 0.0, "dur": it.dur,
                "subDelay": it.sub_delay,
                "cover": _url(self.lib.cover_for(it)), "icon": KIND[it.kind].qt_icon}

    # ---- Now Playing: colours from the cover and the audio visualiser -------
    @Slot(str, result="QVariantMap")
    def paletteFor(self, item_id: str):
        """Backdrop and bar colours for a track, from its album art. Cached per cover."""
        try:
            return self._palette_for(item_id)
        except Exception:
            return palette.fallback("").as_dict()

    def _palette_for(self, item_id: str):
        it = self.lib.by_id(item_id)
        if not it:
            return palette.fallback("").as_dict()
        art = self.lib.cover_for(it)
        key = art or "title:" + it.title
        if key not in self._palettes:
            pal = None
            if art:
                img = QImage(art)
                if not img.isNull():
                    side = min(img.width(), img.height())
                    img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
                    img = img.scaled(40, 40, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGBA8888)
                    pal = palette.from_pixels((c.red(), c.green(), c.blue(), c.alpha())
                                              for c in (img.pixelColor(x, y) for y in range(40) for x in range(40)))
            self._palettes[key] = (pal or palette.fallback(it.title)).as_dict()
        return self._palettes[key]

    @Slot(str, result=bool)
    def startVisualizer(self, item_id: str):
        """Start reading the levels of an audio file. False when ffmpeg is missing, so the screen shows resting bars."""
        self.stopVisualizer()
        it = self.lib.by_id(item_id)
        if not it or it.media != "audio" or not it.exists or not spectrum.available():
            return False
        try:
            self._analyzer = spectrum.Analyzer(it.path).start()
        except Exception:
            self._analyzer = None
            return False
        return True

    @Slot()
    def stopVisualizer(self):
        if self._analyzer:
            self._analyzer.stop()
            self._analyzer = None

    @Slot(float, bool, result="QVariantList")
    def bandsAt(self, seconds: float, playing: bool):
        try:
            if self._analyzer:
                return self._analyzer.bands(seconds) if playing else self._analyzer.rest()
        except Exception:
            pass
        return [0.0] * spectrum.BANDS

    @Slot(float, result="QVariantList")
    def waveAt(self, seconds: float):
        try:
            if self._analyzer:
                return self._analyzer.wave(seconds)
        except Exception:
            pass
        return [0.0] * 96

    @Slot(result=str)
    def vizStyle(self):
        return str(self.lib.prefs.get("viz", "bars"))

    @Slot(str)
    def setVizStyle(self, style: str):
        self.lib.prefs["viz"] = style
        self.lib.save()
        self.vizStyleChanged.emit()

    @Slot(result=bool)
    def lyricsDefault(self):
        return bool(self.lib.prefs.get("lyrics_default", False))

    @Slot(bool)
    def setLyricsDefault(self, on: bool):
        self.lib.prefs["lyrics_default"] = bool(on)
        self.lib.save()

    # ---- subtitles ---------------------------------------------------
    @Slot(str)
    def openSubtitles(self, item_id: str):
        """Look for subtitle tracks for this video (files beside it, ones the user added, ones inside it)."""
        it = self.lib.by_id(item_id)
        self._sub_item, self._sub_tracks, self._sub_sel, self._timeline = item_id, [], "", None
        if not it or it.media != "video" or not it.exists:
            return
        self._sub_delay, self._sub_scale = it.sub_delay, it.sub_scale
        path, extra, saved = it.path, list(it.sub_files), it.sub_sel

        def work():
            found = subtitles.tracks_for(path, extra)
            self._subs_found.emit(item_id, found, subtitles.choose(found.tracks, saved))
        threading.Thread(target=work, daemon=True).start()

    def _on_subs_found(self, item_id: str, found, sel: str):
        if item_id != self._sub_item:
            return
        self._sub_tracks = found.tracks
        _log(f"subtitles: opened {item_id}: tracks={[t.id for t in found.tracks]} chosen={sel!r}")
        note = ""
        if not found.tracks:
            if found.picture_only:
                note = "This file only has picture-based subtitles, which can’t be shown. Add an .srt file."
            elif found.no_ffmpeg:
                note = "No subtitle files found. Install ffmpeg to read subtitles stored inside video files."
        it = self.lib.by_id(item_id)
        if not found.tracks and it and not it.sub_tried:
            creds = opensubs.load_credentials()
            if creds.ready and creds.auto:
                it.sub_tried = True                                   # holds off a second try while this one runs
                self.lib.save()
                self.toast.emit("No subtitles found. Looking online…")
                _log(f"subtitles: none for {it.title!r}, looking online")

                def fetch():
                    status, msg, _path = opensubs.fetch_for_item(self.lib, it, creds)
                    _log(f"subtitles: online lookup for {it.title!r}: {status}: {msg}")
                    it.sub_tried = status == "none"                   # a passing failure is tried again next time
                    self.lib.save()
                    if status in ("ok", "error"):
                        self._say.emit(msg)
                    if status == "ok":
                        self._reopen.emit(item_id)
                threading.Thread(target=fetch, daemon=True).start()
        rows = [{"id": t.id, "label": t.label, "source": t.source} for t in found.tracks]
        self._sub_sel = sel
        self.subtitleTracksReady.emit(item_id, rows, sel, note)
        if sel:
            self._load_cues(item_id, sel)

    def _load_cues(self, item_id: str, track_id: str):
        it = self.lib.by_id(item_id)
        track = next((t for t in self._sub_tracks if t.id == track_id), None)
        if not it or not track:
            return
        path = it.path

        def work():
            try:
                cues = subtitles.load(path, track)
            except Exception:
                cues = []
            self._cues_ready.emit(item_id, track_id, cues)
        threading.Thread(target=work, daemon=True).start()

    def _on_cues_ready(self, item_id: str, track_id: str, cues):
        if item_id != self._sub_item or track_id != self._sub_sel:
            return
        if not cues:
            self._say.emit("Couldn’t read subtitles from that track.")
            return
        self._timeline = subtitles.Timeline(cues)
        it = self.lib.by_id(item_id)
        track = next((t for t in self._sub_tracks if t.id == track_id), None)
        if it and subsync.wanted(it, track_id, self.lib.prefs, embedded=bool(track and track.stream >= 0)):
            self._start_sync(item_id, track_id, False)
        _log(f"subtitles: showing {len(cues)} cues for {item_id} from {track_id!r}; first line: {cues[0].text[:50]!r}")

    @Slot(str, str)
    def selectSubtitle(self, item_id: str, track_id: str):
        it = self.lib.by_id(item_id)
        if not it or item_id != self._sub_item:
            return
        it.sub_sel = track_id or "off"
        self.lib.save()
        self._sub_sel = track_id
        self._timeline = None
        if track_id:
            self._load_cues(item_id, track_id)

    @Slot(float, result=str)
    def subtitleAt(self, seconds: float):
        """Markup (Qt StyledText) for what should be on screen at this position, or an empty string."""
        if not self._timeline or not self._sub_sel:
            return ""
        return subtitles.to_markup(self._timeline.at(seconds, self._sub_delay, self._sub_scale), "<br>")

    @Slot(str, QUrl)
    def addSubtitleFile(self, item_id: str, url: QUrl):
        it = self.lib.by_id(item_id)
        path = url.toLocalFile() if url.isLocalFile() else url.toString()
        if not it or not path:
            return
        try:
            ok = bool(subtitles.parse_file(path))
        except OSError:
            ok = False
        if not ok:
            self.toast.emit("Couldn’t read subtitles from that file. Use .srt, .vtt or .ass.")
            return
        if path not in it.sub_files:
            it.sub_files.append(path)
        it.sub_sel = "f:" + path
        self.lib.save()
        self.openSubtitles(item_id)

    def _start_sync(self, item_id: str, track_id: str, manual: bool):
        it = self.lib.by_id(item_id)
        if not it or not self._timeline or not subsync.available():
            if manual:
                self.toast.emit("Automatic sync needs ffmpeg, which isn’t installed.")
            return
        if item_id in self._syncing:
            return
        self._syncing.add(item_id)
        path, cues = it.path, list(self._timeline.cues)
        if manual:
            self.toast.emit("Syncing subtitles…")

        def work():
            try:
                res = subsync.sync(path, cues)
            except Exception as e:
                _log(f"subtitle sync failed: {e}")
                res = None
            self._sync_done.emit(item_id, track_id, res, manual)
        threading.Thread(target=work, daemon=True).start()

    @Slot(str)
    def syncSubtitles(self, item_id: str):
        """Line the subtitles up with the speech in the video (also done on its own for downloaded subtitles)."""
        if item_id == self._sub_item and self._sub_sel:
            self._start_sync(item_id, self._sub_sel, True)

    def _on_sync_done(self, item_id: str, track_id: str, res, manual: bool):
        self._syncing.discard(item_id)
        it = self.lib.by_id(item_id)
        if not it or item_id != self._sub_item:
            return
        _log(f"subtitle sync for {it.title!r}: {res}")
        subsync.apply(self.lib, it, track_id, res)
        if res is not None and res.changed:
            self._sub_delay, self._sub_scale = it.sub_delay, it.sub_scale
            self.subtitleTimingChanged.emit(item_id, it.sub_delay)
        if manual or (res is not None and res.changed):
            self.toast.emit(subsync.describe(res))

    @Slot(result=bool)
    def autoSyncSubtitles(self):
        return bool(self.lib.prefs.get("sub_autosync", True))

    @Slot(bool)
    def setAutoSyncSubtitles(self, on: bool):
        self.lib.prefs["sub_autosync"] = bool(on)
        self.lib.save()

    @Slot(str, float)
    def setSubtitleDelay(self, item_id: str, delay: float):
        it = self.lib.by_id(item_id)
        if it:
            it.sub_delay = delay
            self.lib.save()
        self._sub_delay = delay

    @Slot(result=int)
    def subtitleSize(self):
        return int(self.lib.prefs.get("sub_size", 1))

    @Slot(int)
    def setSubtitleSize(self, size: int):
        self.lib.prefs["sub_size"] = max(0, min(2, size))
        self.lib.save()

    @Slot(str)
    def startQueue(self, item_id: str):
        it = self.lib.by_id(item_id)
        if it and item_id in self._scoped:                       # "Shuffle Album": only that album
            self._queue, self._scoped = list(self._scoped), []
        elif it and item_id in self._artist_order:               # started from an artist page: play it as shown
            self._queue = list(self._artist_order)
        else:
            self._queue = self.lib.queue_for(it, self.model.ids) if it else [item_id]
        if it and it.media == "audio":
            self._pq.start(self._queue, item_id)

    # ---- music: shuffle, repeat and the track after this one ------------
    @Property(bool, notify=playbackModeChanged)
    def shuffle(self):
        return self._pq.shuffle

    @Property(str, notify=playbackModeChanged)
    def repeatMode(self):
        return self._pq.repeat

    @Slot()
    def toggleShuffle(self):
        self._pq.set_shuffle(not self._pq.shuffle)
        self.lib.prefs["shuffle"] = self._pq.shuffle
        self.lib.save()
        self.playbackModeChanged.emit()

    @Slot()
    def cycleRepeat(self):
        self.lib.prefs["repeat"] = self._pq.cycle_repeat()
        self.lib.save()
        self.playbackModeChanged.emit()

    @Slot(str)
    def noteTrack(self, item_id: str):
        """A track started playing; keeps the queue's place if it was picked from somewhere else."""
        self._pq.play(item_id)

    @Slot(bool, result=str)
    def nextTrack(self, auto: bool):
        """Id of the track to play after the current one, '' when the queue has run out."""
        return self._pq.next(auto)

    @Slot(result=str)
    def previousTrack(self):
        return self._pq.previous()

    # ---- live TV (IPTV) -----------------------------------------------
    def _live_info(self, cid: str):
        ch = self._live.by_id.get(cid)
        if not ch:
            return {}
        now, _nxt = self._live.now_next(cid)
        return {"id": "live:" + cid, "title": ch.name, "sub": now.title if now else (ch.group or "Live"),
                "media": "video", "url": ch.url, "exists": True, "pos": 0.0, "dur": 0.0, "subDelay": 0.0,
                "cover": ch.logo, "icon": "video-television", "live": True}

    @Property(bool, notify=liveChanged)
    def liveBusy(self):
        return self._live_busy

    @Property(str, notify=liveChanged)
    def liveStatus(self):
        """One line for the Live TV page: progress while loading, otherwise counts or the last error."""
        if self._live_busy:
            return self._live_status or "Loading…"
        st = self._live
        if not st.playlists:
            return ""
        parts = [f"{len(st.channels)} channels"]
        if st.epg_of:
            parts.append(f"guide for {len(st.epg_of)}")
        elif st.channels:
            parts.append("no guide data")
        if st.error:
            parts.append(st.error)
        return " · ".join(parts)

    @Property(int, notify=liveChanged)
    def livePlaylistCount(self):
        return len(self._live.playlists)

    @Slot(result="QVariantList")
    def livePlaylists(self):
        return [{"name": p.name, "url": p.url, "epg": p.epg} for p in self._live.playlists]

    @Slot(str, str, str)
    def addLivePlaylist(self, name: str, url: str, epg: str):
        if not url.strip():
            return
        self._live.add_playlist(name, url, epg)
        self.refreshLive(True)

    @Slot(int)
    def removeLivePlaylist(self, index: int):
        self._live.remove_playlist(index)
        self.liveChanged.emit()
        if self._live.playlists:
            self.refreshLive(True)

    @Slot(bool)
    def refreshLive(self, force: bool):
        if self._live_busy or not self._live.playlists:
            return
        self._live_busy, self._live_status = True, "Loading…"
        self.liveChanged.emit()

        def work():
            try:
                err = self._live.refresh(force=force, progress=self._on_live_progress)
            except Exception as e:  # never let the worker die silently
                err = str(e) or e.__class__.__name__
            self._live_done.emit(err)
        threading.Thread(target=work, daemon=True).start()

    def _on_live_progress(self, msg: str):
        self._live_status = msg
        self.liveChanged.emit()          # queued by Qt when emitted from the worker thread

    def _on_live_done(self, err: str):
        self._live_busy = False
        _log(f"live: {len(self._live.channels)} channels, guide for {len(self._live.epg_of)}" + (f", {err}" if err else ""))
        self.liveChanged.emit()

    @Slot(result="QVariantList")
    def liveGroups(self):
        return [{"key": k, "label": lbl, "count": n} for k, lbl, n in self._live.groups()]

    def _live_row(self, ch, now_t: float, num: int = 0):
        cur, nxt = self._live.now_next(ch.id, now_t)
        prog = 0.0
        if cur and cur.end > cur.start:
            prog = max(0.0, min(1.0, (now_t - cur.start) / (cur.end - cur.start)))
        return {"id": "live:" + ch.id, "cid": ch.id, "name": ch.name, "logo": ch.logo, "group": ch.group,
                "num": ch.number or num, "fav": self._live.is_favourite(ch.id),
                "now": cur.title if cur else "", "next": nxt.title if nxt else "", "progress": prog,
                "guide": ch.id in self._live.epg_of}

    @Slot(str, str, result="QVariantList")
    def liveChannels(self, group: str, query: str):
        """Channels for a group/search with what's on now. Remembers the view for channel up/down."""
        self._live_view = (group or iptv.ALL, query)
        now_t = _time.time()
        pos = {c.id: i + 1 for i, c in enumerate(self._live.channels)}
        return [self._live_row(ch, now_t, pos.get(ch.id, 0)) for ch in self._live.select(group, query)[:3000]]

    @Slot(str, result="QVariantMap")
    def liveChannel(self, item_id: str):
        cid = item_id[5:] if item_id.startswith("live:") else item_id
        ch = self._live.by_id.get(cid)
        if not ch:
            return {}
        pos = {c.id: i + 1 for i, c in enumerate(self._live.channels)}
        return self._live_row(ch, _time.time(), pos.get(cid, 0))

    @Slot(str, float, float, result="QVariantList")
    def liveProgrammes(self, cid: str, t0: float, t1: float):
        """Programmes of one channel overlapping [t0, t1] (epoch seconds), clipped for drawing."""
        return [{"start": p.start, "end": p.end, "title": p.title, "desc": p.desc, "cat": p.cat}
                for p in self._live.between(cid, t0, t1)]

    @Slot(str, result="QVariantMap")
    def liveNowNext(self, item_id: str):
        cid = item_id[5:] if item_id.startswith("live:") else item_id
        cur, nxt = self._live.now_next(cid)

        def row(p):
            return {"title": p.title, "desc": p.desc, "start": p.start, "end": p.end} if p else {}
        return {"now": row(cur), "next": row(nxt)}

    @Slot(str)
    def toggleLiveFavourite(self, item_id: str):
        self._live.toggle_favourite(item_id[5:] if item_id.startswith("live:") else item_id)
        self.liveChanged.emit()

    @Slot(result=bool)
    def liveAutoplay(self):
        return bool(self.lib.prefs.get("live_autoplay", True))

    @Slot(bool)
    def setLiveAutoplay(self, on: bool):
        self.lib.prefs["live_autoplay"] = bool(on)
        self.lib.save()

    @Slot(result=str)
    def liveLast(self):
        """The channel you last had on, if it is still in the list."""
        last = str(self.lib.prefs.get("live_last", ""))
        return last if last[5:] in self._live.by_id else ""

    @Slot(str)
    def setLiveLast(self, item_id: str):
        if item_id and self.lib.prefs.get("live_last") != item_id:
            self.lib.prefs["live_last"] = item_id
            self.lib.save()

    @Slot(float, result=str)
    def clockText(self, t: float):
        return _time.strftime("%H:%M", _time.localtime(t))

    @Slot(result=float)
    def nowSeconds(self):
        return _time.time()

    # ---- lyrics ------------------------------------------------------
    @Slot(str)
    def loadLyrics(self, item_id: str):
        it = self.lib.by_id(item_id)
        if not it or it.media != "audio":
            return

        def work():
            self._lyrics_done.emit(item_id, lyrics.lookup(self.lib, it))
        threading.Thread(target=work, daemon=True).start()

    def _on_lyrics_done(self, item_id: str, got):
        _log(f"lyrics for {item_id}: " + (f"{len(got.lines)} lines, {'timed' if got.synced else 'plain'}, from {got.source}" if got else "none"))
        self.lyricsReady.emit(item_id, got.as_rows() if got else [], bool(got and got.synced), got.source if got else "")

    @Slot(str, int, result=str)
    def neighbor(self, item_id: str, delta: int):
        if item_id.startswith("yt:"):
            return self.yt_neighbor(item_id[3:], delta) if self.yt_neighbor else ""
        if item_id.startswith("live:"):
            ch = self._live.neighbour(item_id[5:], delta, *self._live_view)
            return "live:" + ch.id if ch else ""
        try:
            i = self._queue.index(item_id) + delta
        except ValueError:
            return ""
        return self._queue[i] if 0 <= i < len(self._queue) else ""

    @Slot(str, float, float)
    def savePosition(self, item_id: str, pos: float, dur: float):
        if item_id.startswith("yt:"):
            if self.yt_save:
                self.yt_save(item_id[3:], pos, dur)
            return
        it = self.lib.by_id(item_id)
        if it:
            self.lib.record_position(it, pos, dur)

    @Slot(str)
    def finished(self, item_id: str):
        if item_id.startswith("yt:"):
            if self.yt_save:
                self.yt_save(item_id[3:], 0.0, -1.0)        # played to the end: forget the resume point
            return
        it = self.lib.by_id(item_id)
        if it:
            self.lib.record_position(it, 0.0)
