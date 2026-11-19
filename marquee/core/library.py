"""The media library: items, shows, collections, persistence. Shared by every frontend."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Iterable, Iterator

from . import parse


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "marquee"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "marquee"


@dataclass(frozen=True)
class Kind:
    id: str
    name: str
    ratio: float          # cover width / height
    qt_icon: str          # freedesktop theme icon (Breeze has all of these)
    gtk_icon: str         # symbolic icon shipped with Adwaita


KINDS: tuple[Kind, ...] = (
    Kind("movie", "Movies", 2 / 3, "video-x-generic", "video-x-generic-symbolic"),
    Kind("show", "TV Shows", 2 / 3, "video-television", "video-display-symbolic"),
    Kind("music", "Music", 1.0, "audio-x-generic", "audio-x-generic-symbolic"),
    Kind("podcast", "Podcasts", 1.0, "audio-input-microphone", "audio-input-microphone-symbolic"),
)
RETIRED = {"home": "movie", "book": "podcast"}   # Home Videos and Audiobooks were removed: such items move to the nearest kind
KIND = {k.id: k for k in KINDS}
ALL = "all"
TABS = ("library", "continue", "favorites")


@dataclass
class Item:
    id: str
    title: str
    kind: str = "movie"
    media: str = "video"          # "video" | "audio"
    sub: str = ""
    path: str = ""
    rating: int = 0
    fav: bool = False
    added: float = 0.0
    played: float = 0.0
    pos: float = 0.0              # resume position, seconds
    dur: float = 0.0              # seconds, 0 when unknown
    cover: str = ""               # absolute path of a cover image, "" when none
    cover_tried: bool = False
    sample: bool = False
    # TV episodes
    show_key: str = ""            # which show this episode belongs to
    season: int = 0               # 0 = not part of a numbered season
    episode: int = 0
    title_auto: bool = False      # True while the title is a guess that a lookup may replace
    # Music
    album: str = ""
    track: int = 0
    disc: int = 0
    # Subtitles
    sub_files: list = field(default_factory=list)   # subtitle files the user added by hand (absolute paths)
    sub_sel: str = ""             # "" = automatic, "off", or a subtitles.Track id
    sub_delay: float = 0.0        # seconds; positive shows subtitles later
    sub_scale: float = 1.0        # 1.0, or a stretch found by the automatic sync for a different frame rate
    sub_synced: str = ""          # the subtitle track the automatic sync has already looked at
    sub_tried: bool = False       # an automatic subtitle download was already attempted for this video
    # Set only on the show / season tiles that Library.browse() builds. Never stored.
    node: str = ""                # "" | "show" | "season" | "artist" | "album"

    @property
    def progress(self) -> float:
        return min(1.0, self.pos / self.dur) if self.media == "video" and self.dur and self.pos > 0 else 0.0

    @property
    def resumable(self) -> bool:
        return self.media == "video" and self.pos > 5 and (not self.dur or self.pos < self.dur - 10)

    @property
    def exists(self) -> bool:
        return bool(self.path) and os.path.exists(self.path)


@dataclass
class Show:
    """One TV series, with the artwork and episode names found for it."""
    key: str
    name: str
    year: int = 0
    tvmaze_id: int = 0
    poster: str = ""                                  # the SHOW poster only. Season art never goes here.
    seasons: dict = field(default_factory=dict)       # "1" -> {"poster": path, "name": str}
    episodes: dict = field(default_factory=dict)      # "1:3" -> {"name": str, "airdate": str}
    matched: bool = False
    imdb: str = ""                                    # IMDb number from TVmaze, '-' when it has none
    imdb_of: int = 0                                  # the TVmaze id that `imdb` was read for (a match changed later makes it stale)


def probe_tags(path: str, timeout: float = 6.0) -> tuple[float, dict]:
    """(duration in seconds, tags) from one ffprobe call. Tags use lower-case names: title, artist, album,
    album_artist, track, disc. Both are empty when ffprobe is missing."""
    exe = shutil.which("ffprobe")
    if not exe:
        return 0.0, {}
    try:
        r = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration:format_tags", "-of", "json", path],
                           capture_output=True, text=True, timeout=timeout)
        fmt = json.loads(r.stdout or "{}").get("format", {})
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0, {}
    tags = {k.lower().replace("albumartist", "album_artist"): str(v).strip() for k, v in (fmt.get("tags") or {}).items()}
    if "album_artist" not in tags and tags.get("albumartist"):
        tags["album_artist"] = tags["albumartist"]
    try:
        dur = max(0.0, float(fmt.get("duration", 0)))
    except ValueError:
        dur = 0.0
    return dur, {k: v for k, v in tags.items() if v and k in ("title", "artist", "album", "album_artist", "track", "disc")}


def embedded_art(path: str) -> bytes:
    """The picture stored inside an audio file: ID3 for mp3, otherwise whatever ffmpeg finds (flac, m4a, ogg, opus)."""
    if path.lower().endswith(".mp3"):
        id3 = parse.read_id3(path)
        if id3 and id3.get("pic"):
            return id3["pic"]
    exe = shutil.which("ffmpeg")
    if not exe:
        return b""
    try:
        r = subprocess.run([exe, "-v", "error", "-nostdin", "-i", path, "-map", "0:v:0", "-frames:v", "1", "-c", "copy",
                            "-f", "image2pipe", "-"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return b""
    data = r.stdout if r.returncode == 0 else b""
    return data if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"RIFF" else b""


def probe_duration(path: str, timeout: float = 4.0) -> float:
    """Duration in seconds via ffprobe when it is installed, otherwise 0 (learned at playback)."""
    exe = shutil.which("ffprobe")
    if not exe:
        return 0.0
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=timeout,
        )
        return max(0.0, float(r.stdout.strip()))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def expand(paths: Iterable[str | Path]) -> Iterator[Path]:
    """Yield media files, walking into any directories. Inside a folder you pick, Extras / Featurettes / Samples
    folders and sample or trailer files are left out. A file or an extras folder you pick yourself is kept."""
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d.lower() not in parse.EXTRA_DIRS)
                for name in sorted(files):
                    if parse.media_of(name) and not parse.is_extra_name(name):
                        yield Path(root) / name
        elif p.is_file() and parse.media_of(p):
            yield p


def path_kind(path: tuple):
    """Decode a browse path. Returns ((show key, season or None) or None, (artist key, album key or None) or None)."""
    shows = artists = None
    for node in path:
        kind, _, rest = node.partition(":")
        if kind == "show":
            shows = (rest, None)
        elif kind == "season":
            key, _, n = rest.rpartition(":")
            shows = (key, int(n))
        elif kind == "artist":
            artists = (rest, None)
        elif kind == "album":
            a, _, b = rest.partition(":")
            artists = (a, b)
    return shows, artists


def natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


class Library:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "library.json")
        self.items: list[Item] = []
        self.shows: dict[str, Show] = {}
        self.seeded = False
        self.prefs: dict = {}                 # small app-wide settings, such as subtitle size
        self._lock = threading.RLock()

    # ---- persistence -------------------------------------------------
    def load(self) -> "Library":
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self
        known = {f.name for f in fields(Item)} - {"node"}
        self.seeded = bool(raw.get("seeded"))
        self.prefs = dict(raw.get("prefs") or {})
        self.items = [Item(**{k: v for k, v in d.items() if k in known}) for d in raw.get("items", []) if "id" in d]
        for it in self.items:
            it.kind = RETIRED.get(it.kind, it.kind)
        show_fields = {f.name for f in fields(Show)}
        for d in raw.get("shows", []):
            if "key" in d:
                s = Show(**{k: v for k, v in d.items() if k in show_fields})
                self.shows[s.key] = s
        self._backfill_shows()
        return self

    def repair_episode_numbers(self) -> int:
        """Once: an earlier version read "103 - Title" in a Season 1 folder as episode 103. Read those again with the
        current rules. Returns how many episodes changed."""
        if self.prefs.get("ep_compact_v1"):
            return 0
        changed = 0
        for it in self.items:
            if it.kind != "show" or it.episode < 100 or not it.path:
                continue
            info = parse.parse_path(it.path)
            if info.get("kind") == "show" and info["episode"] != it.episode and info["episode"] < 100:
                it.season, it.episode = info["season"], info["episode"]
                it.sub = info["sub"]
                it.sub_tried = False                         # its subtitles were searched for under the wrong number
                if it.title_auto:
                    it.title = info["title"]
                changed += 1
        self.prefs["ep_compact_v1"] = True
        self.save()
        return changed

    def _backfill_shows(self) -> None:
        """Libraries saved before shows existed: give old TV items a show, season and episode."""
        for it in self.items:
            if it.kind != "show" or it.show_key:
                continue
            info = parse.parse_path(it.path) if it.path else {}
            if info.get("kind") == "show":
                it.show_key = parse.slug(info["show"], info["year"])
                it.season, it.episode = info["season"], info["episode"]
                it.title, it.sub, it.title_auto = info["title"], info["sub"], True
                self.ensure_show(it.show_key, info["show"], info["year"])
            else:
                it.show_key = parse.slug(it.title)
                self.ensure_show(it.show_key, it.title, 0)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 2, "seeded": self.seeded, "prefs": self.prefs,
                       "items": [{k: v for k, v in asdict(i).items() if k != "node"} for i in self.items],
                       "shows": [asdict(s) for s in self.shows.values()]}
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".library-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # ---- queries -----------------------------------------------------
    def by_id(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def count(self, collection: str = ALL) -> int:
        """Number of top-level entries: a whole TV series counts once, and so does an artist."""
        return len(self.browse(collection, "library", "", ()))

    def _filter(self, collection: str, tab: str) -> list[Item]:
        items = self.items
        if collection != ALL:
            items = [i for i in items if i.kind == collection]
        if tab == "favorites":
            items = [i for i in items if i.fav]
        elif tab == "continue":
            items = [i for i in items if i.resumable]
        return items

    def _haystack(self, i: Item) -> str:
        info = self.shows.get(i.show_key) if i.show_key else None
        return f"{i.title} {i.sub} {i.album} {info.name if info else ''}".lower()

    @staticmethod
    def _matches(text: str, query: str) -> bool:
        return all(w in text for w in query.lower().split())

    def visible(self, collection: str = ALL, tab: str = "library", query: str = "") -> list[Item]:
        """Flat, filtered list of real items (episodes and songs included)."""
        items = self._filter(collection, tab)
        if query.strip():
            items = [i for i in items if self._matches(self._haystack(i), query)]
        if tab == "continue":
            return sorted(items, key=lambda i: -i.played)
        return sorted(items, key=lambda i: natural_key(i.title))

    # ---- browsing ---------------------------------------------------
    @staticmethod
    def artist_of(i: Item) -> str:
        return i.sub.strip() or "Unknown Artist"

    @staticmethod
    def album_of(i: Item) -> str:
        return i.album.strip() or "Singles"

    @staticmethod
    def akey(name: str) -> str:
        return parse.slug(name) or "unknown"

    def browse(self, collection: str = ALL, tab: str = "library", query: str = "", path: tuple = ()) -> list[Item]:
        """What to put on screen at one level of the library.

        Library tab: TV episodes fold into one tile per show (then seasons, then episodes) and songs into one
        tile per artist (then albums, then songs). `path` is the nodes opened so far, such as
        ("show:ted", "season:ted:2") or ("artist:abba", "album:abba:arrival").

        Search only looks at the level you are on. On the main screen it finds shows, movies and artists, not
        their episodes or songs. Inside a show or an artist it finds that show's episodes or that artist's songs.
        Continue and Favorites are flat lists of what you can play, so search there matches those directly.
        """
        query = query.strip()
        if tab != "library":
            return self.visible(collection, tab, query)
        items = self._filter(collection, "library")
        shows, artists = path_kind(path)
        if shows is None and artists is None:
            loose: list[Item] = []
            by_show: dict[str, list[Item]] = {}
            by_artist: dict[str, list[Item]] = {}
            for it in items:
                if it.kind == "show" and it.show_key:
                    by_show.setdefault(it.show_key, []).append(it)
                elif it.kind == "music":
                    by_artist.setdefault(self.akey(self.artist_of(it)), []).append(it)
                else:
                    loose.append(it)
            nodes = loose + [self._show_node(k, v) for k, v in by_show.items()] \
                + [self._artist_node(k, v) for k, v in by_artist.items()]
            if query:
                nodes = [n for n in nodes if self._matches(f"{n.title} {n.sub if not n.node else ''}".lower(), query)]
            return sorted(nodes, key=lambda i: natural_key(i.title))
        if shows is not None:
            key, season = shows
            eps = [i for i in items if i.kind == "show" and i.show_key == key]
            if query:                                                    # search inside a show: its episodes, any season
                eps = [e for e in eps if season is None or e.season == season]
                return sorted((e for e in eps if self._matches(self._haystack(e), query)),
                              key=lambda e: (e.season, e.episode, natural_key(e.title)))
            if season is None:
                by: dict[int, list[Item]] = {}
                for e in eps:
                    by.setdefault(e.season, []).append(e)
                return [self._season_node(key, n, v) for n, v in sorted(by.items())]
            return sorted((e for e in eps if e.season == season), key=lambda e: (e.episode, natural_key(e.title)))
        key, album = artists
        songs = [i for i in items if i.kind == "music" and self.akey(self.artist_of(i)) == key]
        if query:
            songs = [s_ for s_ in songs if album is None or self.akey(self.album_of(s_)) == album]
            return sorted((s_ for s_ in songs if self._matches(self._haystack(s_), query)), key=self._track_key)
        if album is None:
            by_album: dict[str, list[Item]] = {}
            for s_ in songs:
                by_album.setdefault(self.akey(self.album_of(s_)), []).append(s_)
            return sorted((self._album_node(key, k, v) for k, v in by_album.items()), key=lambda n: natural_key(n.title))
        return sorted((s_ for s_ in songs if self.akey(self.album_of(s_)) == album), key=self._track_key)

    @staticmethod
    def _track_key(i: Item):
        return (i.disc or 1, i.track or 9999, natural_key(i.title))

    def start_view(self):
        """Where to open: the collection and tab you had open last time, else the first collection that has something."""
        saved = self.prefs.get("last_view") or {}
        coll = saved.get("collection")
        if coll not in {k.id for k in KINDS}:
            coll = next((k.id for k in KINDS if any(i.kind == k.id for i in self.items)), KINDS[0].id)
        tab = saved.get("tab") if saved.get("tab") in ("library", "continue", "favorites") else "library"
        return coll, tab

    def remember_view(self, collection: str, tab: str) -> None:
        if collection != ALL and self.prefs.get("last_view") != {"collection": collection, "tab": tab}:
            self.prefs["last_view"] = {"collection": collection, "tab": tab}
            self.save()

    def heading(self, path: tuple, collection: str = ALL) -> str:
        """Title for the page at this path: the collection name at the top, otherwise Show › Season or Artist › Album."""
        if not path:
            return "All Media" if collection == ALL else KIND[collection].name
        shows, artists = path_kind(path)
        if shows is not None:
            key, season = shows
            info = self.shows.get(key)
            name = info.name if info and info.name else key
            return name if season is None else f"{name} › " + (f"Season {season}" if season else "Specials")
        key, album = artists
        songs = [i for i in self.items if i.kind == "music" and self.akey(self.artist_of(i)) == key]
        name = self._common(self.artist_of(i) for i in songs) if songs else key
        if album is None:
            return name
        inner = [i for i in songs if self.akey(self.album_of(i)) == album]
        return f"{name} › {self._common(self.album_of(i) for i in inner) if inner else album}"

    @staticmethod
    def _common(names) -> str:
        counts: dict[str, int] = {}
        for n in names:
            counts[n] = counts.get(n, 0) + 1
        return max(counts, key=lambda n: counts[n]) if counts else ""

    def _artist_node(self, key: str, songs: list[Item]) -> Item:
        albums = {self.akey(self.album_of(s_)) for s_ in songs}
        art = next((self.cover_for(s_) for s_ in sorted(songs, key=self._track_key) if self.cover_for(s_)), "")
        return Item(id=f"artist:{key}", title=self._common(self.artist_of(s_) for s_ in songs), kind="music", media="audio",
                    sub=f"{plural(len(albums), 'album')} · {plural(len(songs), 'song')}", node="artist", cover=art)

    def _album_node(self, artist_key: str, key: str, songs: list[Item]) -> Item:
        art = next((self.cover_for(s_) for s_ in sorted(songs, key=self._track_key) if self.cover_for(s_)), "")
        return Item(id=f"album:{artist_key}:{key}", title=self._common(self.album_of(s_) for s_ in songs), kind="music",
                    media="audio", sub=plural(len(songs), "song"), node="album", cover=art)

    def _exists(self, path: str) -> str:
        return path if path and os.path.exists(path) else ""

    def _show_node(self, key: str, eps: list[Item]) -> Item:
        info = self.shows.get(key)
        seasons = len({e.season for e in eps})
        return Item(id=f"show:{key}", title=info.name if info and info.name else eps[0].title, kind="show", media="video",
                    sub=f"{plural(seasons, 'season')} · {plural(len(eps), 'episode')}", show_key=key, node="show",
                    # The show tile only ever uses the show's own poster. A season poster is never borrowed.
                    cover=self._exists(info.poster) if info else "")

    def _season_node(self, key: str, n: int, eps: list[Item]) -> Item:
        info = self.shows.get(key)
        sdata = (info.seasons.get(str(n)) if info else None) or {}
        art = self._exists(sdata.get("poster", "")) or (self._exists(info.poster) if info else "")   # no season art: use the show's
        title = f"Season {n}" if n else "Specials"
        return Item(id=f"season:{key}:{n}", title=title, kind="show", media="video", sub=plural(len(eps), "episode"),
                    show_key=key, season=n, node="season", cover=art)

    def cover_for(self, item: Item) -> str:
        """The image to draw for an item. Episodes use their season's art, then the show's."""
        if item.cover and os.path.exists(item.cover):
            return item.cover
        if item.kind == "show" and item.show_key and not item.node:
            info = self.shows.get(item.show_key)
            if info:
                sdata = info.seasons.get(str(item.season)) or {}
                return self._exists(sdata.get("poster", "")) or self._exists(info.poster)
        return ""

    def artist_albums(self, artist_key: str) -> list[tuple[Item, list[Item]]]:
        """An artist's albums, each with its songs in track order: [(album node, songs)]. Albums run A to Z, and the
        songs of all of them, read top to bottom, are the order the artist plays in."""
        groups: dict[str, list[Item]] = {}
        for s_ in self.tracks_of(artist_key):
            groups.setdefault(self.akey(self.album_of(s_)), []).append(s_)
        out = [(self._album_node(artist_key, k, sorted(v, key=self._track_key)), sorted(v, key=self._track_key))
               for k, v in groups.items()]
        return sorted(out, key=lambda a: natural_key(a[0].title))

    def tracks_of(self, artist_key: str) -> list[Item]:
        return sorted((i for i in self.items if i.kind == "music" and self.akey(self.artist_of(i)) == artist_key),
                      key=lambda i: (natural_key(self.album_of(i)), *self._track_key(i)))

    def episodes_of(self, show_key: str) -> list[Item]:
        return sorted((i for i in self.items if i.kind == "show" and i.show_key == show_key),
                      key=lambda e: (e.season, e.episode, natural_key(e.title)))

    def queue_for(self, item: Item, visible_ids: list[str]) -> list[str]:
        """Play order. An episode continues through the rest of its show, across seasons."""
        if item.kind == "show" and item.show_key:
            return [e.id for e in self.episodes_of(item.show_key)]
        if item.kind == "music" and not item.node:
            album = self.akey(self.album_of(item))
            tracks = self.tracks_of(self.akey(self.artist_of(item)))
            ids = [t.id for t in tracks]
            first = next((n for n, t in enumerate(tracks) if self.akey(self.album_of(t)) == album), 0)
            return ids[first:] if item.id in ids else [item.id]       # the rest of the album, then the artist's later albums
        return visible_ids if item.id in visible_ids else [item.id]

    def clear_resume(self, ids: Iterable[str] | None = None) -> int:
        """Take titles off the Continue tab by forgetting where they were stopped. None clears every one."""
        want = None if ids is None else set(ids)
        n = 0
        for it in self.items:
            if it.pos > 0 and (want is None or it.id in want) and (it.resumable or want is not None):
                it.pos = 0.0
                n += 1
        if n:
            self.save()
        return n

    # ---- mutation ----------------------------------------------------
    def ensure_show(self, key: str, name: str, year: int = 0) -> Show:
        s = self.shows.get(key)
        if not s:
            s = self.shows[key] = Show(key=key, name=name, year=year)
        return s

    def add_paths(self, paths: Iterable[str | Path], probe: bool = True) -> tuple[list[Item], int]:
        """Add files or folders. Returns (new items, number skipped as duplicates)."""
        known = {i.path for i in self.items}
        new: list[Item] = []
        skipped = 0
        art_for: dict[str, Item] = {}                    # folder -> first item added from it, for local artwork
        files = list(expand(paths))
        probes: dict[str, tuple[float, dict, bytes]] = {}
        if probe:                                        # ffprobe each new file, several at once: big folders add quickly
            todo = list({str(p.resolve()) for p in files} - known)
            if todo:
                with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
                    probes = dict(zip(todo, pool.map(self._probe, todo)))
        for p in files:
            full = str(p.resolve())
            if full in known:
                skipped += 1
                continue
            media = parse.media_of(p)
            if not media:
                continue
            dur, tags, art = 0.0, {}, b""
            if probe:
                dur, tags, art = probes.get(full) or self._probe(full)
            info = parse.parse_music_path(p, tags) if media == "audio" else parse.parse_path(p)
            item = Item(id=uuid.uuid4().hex[:12], title=info["title"], kind=info["kind"], media=media,
                        sub=info["sub"], path=full, added=time.time(), dur=dur)
            if info["kind"] == "show":
                item.show_key = parse.slug(info["show"], info["year"])
                item.season, item.episode, item.title_auto = info["season"], info["episode"], True
                self.ensure_show(item.show_key, info["show"], info["year"])
            if media == "audio":
                item.album, item.track, item.disc = info.get("album", ""), info.get("track", 0), info.get("disc", 0)
                if art:                                   # the picture inside this very file, whatever the format
                    self.set_shared_cover(item, art)
                    item.cover_tried = True
                if p.suffix.lower() == ".mp3" and not tags:      # no ffprobe: fall back to the ID3 tags
                    id3 = parse.read_id3(p)
                    if id3:
                        item.title = id3.get("title") or item.title
                        item.sub = id3.get("artist") or item.sub
                        item.album = id3.get("album") or item.album
            if not item.cover:
                folder = str(p.parent.parent if item.kind == "music" and parse._DISC.match(p.parent.name) else p.parent)
                if item.kind in ("movie", "music") and folder not in art_for:
                    art_for[folder] = item
            known.add(full)
            new.append(item)
        # Artwork that sits in the folder wins over a download: poster.jpg beside a movie, cover.jpg in an album.
        for folder, item in art_for.items():
            art = parse.local_art(folder, (Path(item.path).stem,) if item.kind == "movie" else ())
            if art:
                try:
                    self.set_cover_bytes(item, Path(art).read_bytes())
                    item.cover_tried = True
                except OSError:
                    pass
        for it in new:
            if it.kind == "show" and it.show_key:
                info = self.shows.get(it.show_key)
                if info and not info.poster:
                    for folder in {Path(i.path).parent for i in new if i.show_key == it.show_key}:
                        root = folder.parent if parse.season_of_folder(folder.name) is not None else folder
                        art = parse.local_art(root)
                        if art:
                            try:
                                info.poster = self.write_art("show-" + it.show_key, Path(art).read_bytes())
                            except OSError:
                                pass
                            break
        with self._lock:
            self.items.extend(new)
        self.share_album_covers()
        if new:
            self.save()
        return new, skipped

    def remove(self, item: Item) -> None:
        with self._lock:
            self.items = [i for i in self.items if i.id != item.id]
            key = item.show_key
            if key and not any(i.show_key == key for i in self.items):
                gone = self.shows.pop(key, None)
                for path in ([gone.poster] if gone else []) + [s.get("poster", "") for s in (gone.seasons.values() if gone else [])]:
                    self._unlink(path)
        if item.cover and not any(i.cover == item.cover for i in self.items):     # album tracks can share one image
            self._unlink(item.cover)
        self.save()

    @staticmethod
    def _unlink(path: str) -> None:
        if path:
            try:
                Path(path).unlink()
            except OSError:
                pass

    def edit(self, item: Item, title: str, sub: str, kind: str) -> None:
        """User edits from the Edit Info dialog. A title the user typed is never overwritten by a lookup."""
        title = title.strip()
        if title and title != item.title:
            item.title, item.title_auto = title, False
        item.sub = sub.strip()
        if kind in KIND and kind != item.kind:
            item.kind = kind
            if kind == "show" and not item.show_key:
                item.show_key = parse.slug(item.title)
                self.ensure_show(item.show_key, item.title, 0)
        self.save()

    def write_art(self, stem: str, data: bytes) -> str:
        """Store image bytes as covers/<stem>.<ext> and return the path."""
        folder = data_dir() / "covers"
        folder.mkdir(parents=True, exist_ok=True)
        ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".webp" if data[:4] == b"RIFF" else ".jpg"
        for old in folder.glob(stem + ".*"):
            if old.suffix != ext:
                self._unlink(str(old))
        dest = folder / f"{stem}{ext}"
        dest.write_bytes(data)
        return str(dest)

    def set_cover_bytes(self, item: Item, data: bytes) -> None:
        item.cover = self.write_art(item.id, data)

    def set_shared_cover(self, item: Item, data: bytes) -> None:
        """Store art by content, so the tracks of one album that carry the same picture share one file."""
        item.cover = self.write_art("art-" + hashlib.sha1(data).hexdigest()[:16], data)

    @staticmethod
    def _probe(path: str) -> tuple[float, dict, bytes]:
        dur, tags = probe_tags(path)
        return dur, tags, (embedded_art(path) if parse.media_of(path) == "audio" else b"")

    def share_album_covers(self) -> int:
        """Every track of an album shows the album's picture. A track with no picture of its own, such as one whose
        file has none, takes it from a sibling that has one. Returns how many tracks changed."""
        best: dict[tuple[str, str], str] = {}
        for i in self.items:
            if i.kind == "music" and not i.node and i.cover and os.path.exists(i.cover):
                best.setdefault((self.akey(self.artist_of(i)), self.akey(self.album_of(i))), i.cover)
        n = 0
        for i in self.items:
            if i.kind == "music" and not i.node and not (i.cover and os.path.exists(i.cover)):
                c = best.get((self.akey(self.artist_of(i)), self.akey(self.album_of(i))))
                if c:
                    i.cover, i.cover_tried, n = c, True, n + 1
        return n

    def fill_embedded_art(self, items: Iterable[Item] | None = None) -> int:
        """Read the picture inside each music file that has none yet (libraries added before this was done for
        every format). Returns how many tracks gained one. Slow for big libraries: call it off the UI thread."""
        todo = [i for i in (items if items is not None else self.items)
                if i.kind == "music" and not i.node and i.exists and not (i.cover and os.path.exists(i.cover))]
        if not todo:
            return 0
        with ThreadPoolExecutor(max_workers=min(6, len(todo))) as pool:
            arts = list(pool.map(lambda i: embedded_art(i.path), todo))
        n = 0
        for i, art in zip(todo, arts):
            if art:
                self.set_shared_cover(i, art)
                i.cover_tried = True
                n += 1
        n += self.share_album_covers()
        if n:
            self.save()
        return n

    def set_cover_file(self, item: Item, source: str | Path) -> None:
        self.set_cover_bytes(item, Path(source).read_bytes())
        item.cover_tried = True
        self.save()

    def record_position(self, item: Item, pos: float, dur: float = 0.0) -> None:
        item.pos = max(0.0, pos) if item.media == "video" else 0.0      # a song always starts from the top
        item.played = time.time()
        if dur and dur > 0:
            item.dur = dur
        self.save()

    def ensure_samples(self) -> bool:
        """Seed the three synthesised sample tracks once. Returns True if items were added."""
        if self.seeded:
            return False
        from . import samples
        for n, (title, artist) in enumerate(samples.TRACKS):
            path = samples.ensure_file(n)
            self.items.append(Item(id=uuid.uuid4().hex[:12], title=title, kind="music", media="audio", sub=artist,
                                   path=str(path), added=time.time(), dur=float(samples.SECONDS), sample=True,
                                   album="Samples", track=n + 1))
        self.seeded = True
        self.save()
        return True
