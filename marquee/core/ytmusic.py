"""YouTube Music: search, albums, artists and playlists, with no account.

The catalogue comes from YouTube Music's own web API through the bundled `ytmusicapi` library (MIT licence, in
marquee/_vendor, needs the `requests` package). Playing a track is a YouTube video, so the audio itself is fetched
with yt-dlp, the same way the YouTube page does it (see YtDlp.resolve_audio in youtube.py).

Everything the screens use goes through the plain dicts built here, so ytmusicapi's shapes stay in one file:

    track    {id: "ytm:<videoId>", vid, title, artist, artistId, album, albumId, dur, cover, n}
    album    {id: browseId, title, artist, artistId, year, kind, cover, playlistId}
    artist   {id: browseId, name, cover}
    playlist {id: playlistId, title, author, count, cover}
"""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from .library import data_dir

PREFIX = "ytm:"
RECENT_MAX = 60
_VENDOR = Path(__file__).resolve().parent.parent / "_vendor"


def _log(msg: str) -> None:
    print(f"[ytmusic] {msg}", file=sys.stderr, flush=True)


# ---- turning ytmusicapi's answers into the dicts above ---------------------------------------------------------
def art(thumbs: Any, size: int = 544) -> str:
    """The biggest picture in a thumbnail list, asked for at `size` pixels where YouTube's image servers allow it."""
    best, area = "", -1
    for t in thumbs or []:
        if not isinstance(t, dict) or not t.get("url"):
            continue
        a = int(t.get("width") or 0) * int(t.get("height") or 0)
        if a > area:
            best, area = t["url"], a
    if not best:
        return ""
    if "googleusercontent.com" in best or "ggpht.com" in best:
        best = re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", best)
        best = re.sub(r"=s\d+", f"=s{size}", best)
    return best


def seconds(d: dict) -> float:
    """A track's length: ytmusicapi gives duration_seconds, and a "3:45" style text as a second choice."""
    v = d.get("duration_seconds")
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    text = str(d.get("duration") or "")
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", text):
        total = 0
        for part in text.split(":"):
            total = total * 60 + int(part)
        return float(total)
    return 0.0


def _names(v: Any) -> tuple[str, str]:
    """(display text, id of the first artist) from either a list of {name, id} or a plain string."""
    if isinstance(v, str):
        return v, ""
    if isinstance(v, dict):
        v = [v]
    names = [str(a.get("name") or "") for a in (v or []) if isinstance(a, dict) and a.get("name")]
    first = next((str(a.get("id") or "") for a in (v or []) if isinstance(a, dict) and a.get("id")), "")
    return ", ".join(names), first


def norm_track(d: dict, cover: str = "", album: dict | str | None = None, number: int = 0) -> dict | None:
    vid = d.get("videoId")
    if not vid:
        return None
    if d.get("isAvailable") is False:
        return None
    artist, artist_id = _names(d.get("artists") if d.get("artists") else d.get("artist"))
    alb = d.get("album") if d.get("album") else album
    album_name, album_id = "", ""
    if isinstance(alb, dict):
        album_name, album_id = str(alb.get("name") or alb.get("title") or ""), str(alb.get("id") or "")
    elif isinstance(alb, str):
        album_name = alb
    return {"id": PREFIX + vid, "vid": vid, "title": str(d.get("title") or ""), "artist": artist, "artistId": artist_id,
            "album": album_name, "albumId": album_id, "dur": seconds(d),
            "cover": art(d.get("thumbnails")) or cover, "n": int(d.get("trackNumber") or number or 0)}


def norm_album(d: dict) -> dict | None:
    bid = d.get("browseId")
    if not bid:
        return None
    artist, artist_id = _names(d.get("artists") if d.get("artists") else d.get("artist"))
    return {"id": bid, "title": str(d.get("title") or ""), "artist": artist, "artistId": artist_id,
            "year": str(d.get("year") or ""), "kind": str(d.get("type") or "Album"), "cover": art(d.get("thumbnails")),
            "playlistId": str(d.get("playlistId") or d.get("audioPlaylistId") or "")}


def norm_artist(d: dict) -> dict | None:
    bid = d.get("browseId") or d.get("channelId")
    if not bid:
        return None
    return {"id": bid, "name": str(d.get("artist") or d.get("name") or d.get("title") or ""), "cover": art(d.get("thumbnails"))}


def norm_playlist(d: dict) -> dict | None:
    pid = d.get("browseId") or d.get("playlistId")
    if not pid:
        return None
    pid = str(pid)
    if pid.startswith("VL"):
        pid = pid[2:]
    count = d.get("itemCount") or d.get("count") or 0
    try:
        count = int(str(count).replace(",", "").split()[0])
    except (ValueError, IndexError):
        count = 0
    author = d.get("author")
    if isinstance(author, list):
        author = _names(author)[0]
    return {"id": pid, "title": str(d.get("title") or ""), "author": str(author or ""), "count": count, "cover": art(d.get("thumbnails"))}


def _list(rows, fn: Callable, *args) -> list[dict]:
    out = []
    for r in rows or []:
        if isinstance(r, dict):
            try:
                x = fn(r, *args)
            except Exception as e:              # one odd row must not lose the whole page
                _log(f"skipped a result: {e!r}")
                continue
            if x:
                out.append(x)
    return out


def shape_album(raw: dict, browse_id: str = "") -> dict:
    cover = art(raw.get("thumbnails"))
    artist, artist_id = _names(raw.get("artists"))
    album = {"name": str(raw.get("title") or ""), "id": browse_id}
    tracks = []
    for i, t in enumerate(raw.get("tracks") or [], 1):
        x = norm_track(t, cover=cover, album=album, number=i) if isinstance(t, dict) else None
        if x:
            x["cover"] = cover or x["cover"]          # every song on an album shows the album's cover
            x["album"] = x["album"] or album["name"]
            x["albumId"] = x["albumId"] or browse_id
            x["artist"] = x["artist"] or artist
            x["artistId"] = x["artistId"] or artist_id
            tracks.append(x)
    for i, t in enumerate(tracks, 1):
        t["n"] = i
    return {"id": browse_id, "title": album["name"], "artist": artist, "artistId": artist_id, "year": str(raw.get("year") or ""),
            "kind": str(raw.get("type") or "Album"), "cover": cover, "tracks": tracks,
            "dur": float(raw.get("duration_seconds") or sum(t["dur"] for t in tracks)),
            "playlistId": str(raw.get("audioPlaylistId") or "")}


def shape_artist(raw: dict, channel_id: str = "") -> dict:
    def section(key: str) -> dict:
        s = raw.get(key)
        return s if isinstance(s, dict) else {}
    cover = art(raw.get("thumbnails"))
    name = str(raw.get("name") or "")
    songs = []
    for t in section("songs").get("results") or []:
        x = norm_track(t, cover=cover) if isinstance(t, dict) else None
        if x:
            x["artist"] = x["artist"] or name
            songs.append(x)
    for i, t in enumerate(songs, 1):
        t["n"] = i
    albums = _list(section("albums").get("results"), norm_album)
    singles = _list(section("singles").get("results"), norm_album)
    for a in albums + singles:
        a["artist"] = a["artist"] or name
    return {"id": channel_id, "name": name, "cover": cover, "description": str(raw.get("description") or ""),
            "listeners": str(raw.get("monthlyListeners") or raw.get("subscribers") or ""), "songs": songs,
            "songsBrowseId": str(section("songs").get("browseId") or ""), "albums": albums, "singles": singles,
            "radioId": str(raw.get("radioId") or ""), "shuffleId": str(raw.get("shuffleId") or "")}


def shape_playlist(raw: dict, playlist_id: str = "") -> dict:
    cover = art(raw.get("thumbnails"))
    tracks = []
    for t in raw.get("tracks") or []:
        x = norm_track(t, cover=cover) if isinstance(t, dict) else None
        if x:
            tracks.append(x)
    for i, t in enumerate(tracks, 1):
        t["n"] = i
    author = raw.get("author")
    author = author.get("name") if isinstance(author, dict) else (author if isinstance(author, str) else "")
    return {"id": playlist_id, "title": str(raw.get("title") or ""), "author": str(author or ""), "cover": cover,
            "tracks": tracks, "dur": float(raw.get("duration_seconds") or sum(t["dur"] for t in tracks)),
            "count": int(raw.get("trackCount") or len(tracks))}


# ---- the client --------------------------------------------------------------------------------------------------
class Unavailable(RuntimeError):
    """The catalogue library can't be used (the `requests` package is missing, for instance)."""


class YtMusic:
    """Thin wrapper: every method returns the plain dicts above and raises RuntimeError with something readable."""

    def __init__(self, factory: Callable[[], Any] | None = None):
        self._factory = factory
        self._api: Any = None
        self._lock = threading.Lock()

    def _client(self):
        with self._lock:
            if self._api is None:
                if self._factory:
                    self._api = self._factory()
                else:
                    if str(_VENDOR) not in sys.path:
                        sys.path.insert(0, str(_VENDOR))
                    try:
                        from ytmusicapi import YTMusic
                    except ImportError as e:
                        raise Unavailable(f"YouTube Music needs the Python package 'requests' ({e}). Arch: sudo pacman -S python-requests") from e
                    self._api = YTMusic(language="en")
            return self._api

    def _call(self, what: str, fn: Callable[[Any], Any]):
        api = self._client()
        try:
            return fn(api)
        except Unavailable:
            raise
        except Exception as e:
            _log(f"{what} failed: {e!r}")
            text = str(e) or e.__class__.__name__
            if "Connection" in e.__class__.__name__ or "Timeout" in e.__class__.__name__ or "resolve" in text.lower():
                raise RuntimeError("Couldn’t reach YouTube Music. Check your connection and try again.") from e
            raise RuntimeError(f"YouTube Music: {text[:200]}") from e

    def search(self, query: str, kind: str = "songs", limit: int = 30) -> list[dict]:
        """kind: songs | albums | artists | playlists."""
        q = query.strip()
        if not q:
            return []
        flt = {"songs": "songs", "albums": "albums", "artists": "artists", "playlists": "community_playlists"}[kind]
        rows = self._call(f"search {kind}", lambda a: a.search(q, filter=flt, limit=limit))
        fn = {"songs": norm_track, "albums": norm_album, "artists": norm_artist, "playlists": norm_playlist}[kind]
        out = _list(rows, fn)
        _log(f"search {q!r} {kind}: {len(out)} of {len(rows or [])} results")
        return out

    def lyrics(self, video_id: str):
        """YouTube Music's own lyrics for a song, timed when it has them: a lyrics.Lyrics, or None when there are none."""
        from . import lyrics as ly
        watch = self._call("lyrics lookup", lambda a: a.get_watch_playlist(videoId=video_id, limit=1))
        browse = (watch or {}).get("lyrics") if isinstance(watch, dict) else None
        if not browse:
            return None
        raw = self._call("lyrics", lambda a: a.get_lyrics(browse, timestamps=True))
        if not isinstance(raw, dict) or not raw.get("lyrics"):
            return None
        body = raw["lyrics"]
        if isinstance(body, str):
            lines = [ly.Line(-1.0, t.strip()) for t in body.splitlines()]
            while lines and not lines[-1].text:
                lines.pop()
            return ly.Lyrics(lines, False, "youtube music") if lines else None
        lines = []
        for row in body:
            get = row.get if isinstance(row, dict) else lambda k, d=None: getattr(row, k, d)
            start, text = get("start_time"), (get("text") or "").strip()
            if start is None:
                continue
            lines.append(ly.Line(float(start) / 1000.0, text))
        return ly.Lyrics(lines, True, "youtube music") if lines else None

    def album(self, browse_id: str) -> dict:
        raw = self._call("album", lambda a: a.get_album(browse_id))
        out = shape_album(raw, browse_id)
        _log(f"album {out['title']!r}: {len(out['tracks'])} tracks")
        return out

    def artist(self, channel_id: str) -> dict:
        raw = self._call("artist", lambda a: a.get_artist(channel_id))
        out = shape_artist(raw, channel_id)
        _log(f"artist {out['name']!r}: {len(out['songs'])} songs, {len(out['albums'])} albums, {len(out['singles'])} singles")
        return out

    def playlist(self, playlist_id: str, limit: int = 200) -> dict:
        raw = self._call("playlist", lambda a: a.get_playlist(playlist_id, limit=limit))
        out = shape_playlist(raw, playlist_id)
        _log(f"playlist {out['title']!r}: {len(out['tracks'])} tracks")
        return out

    def artist_albums(self, channel_id: str, browse_id: str, params: str = "") -> list[dict]:
        rows = self._call("artist albums", lambda a: a.get_artist_albums(browse_id, params))
        return _list(rows, norm_album)


# ---- a short list of what you played last, kept on this computer ------------------------------------------------
class Store:
    def __init__(self, folder: Path | None = None):
        self.path = (folder or data_dir()) / "ytmusic.json"
        self.recent: list[dict] = []
        try:
            d = json.loads(self.path.read_text("utf-8"))
            self.recent = [t for t in d.get("recent", []) if isinstance(t, dict) and t.get("id", "").startswith(PREFIX)][:RECENT_MAX]
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"recent": self.recent}), "utf-8")
            tmp.replace(self.path)
        except OSError as e:
            _log(f"couldn’t save: {e}")

    def played(self, track: dict) -> None:
        self.recent = [t for t in self.recent if t["id"] != track["id"]]
        self.recent.insert(0, {k: track.get(k, "") for k in ("id", "vid", "title", "artist", "artistId", "album", "albumId", "dur", "cover")})
        del self.recent[RECENT_MAX:]
        self.save()

    def clear_recent(self) -> None:
        self.recent = []
        self.save()
