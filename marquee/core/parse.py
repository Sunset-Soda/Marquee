"""File name parsing and ID3 tag reading (pure Python, no dependencies)."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".ogv", ".mpg", ".mpeg", ".ts", ".3gp"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".oga", ".opus", ".wma", ".aif", ".aiff"}


def media_of(path: str | Path) -> str | None:
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return None


_TAGS = re.compile(
    r"[\s(\[]*\b(480p|720p|1080p|2160p|4k|bluray|brrip|webrip|web-dl|hdrip|dvdrip|x264|x265|h264|hevc|aac|ac3|dts)\b.*$",
    re.I,
)


def slug(name: str, year: int = 0) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "show"
    return f"{key}-{year}" if year else key


def episode_sub(show: str, season: int, episode: int) -> str:
    if not episode:
        tail = "Special" if not season else f"S{season}"
    else:
        tail = f"S{season} · E{episode}" if season else f"E{episode}"
    return f"{show} · {tail}" if show else tail


def split_year(name: str) -> tuple[str, int]:
    """'Ted (2024)' -> ('Ted', 2024). A bare name is returned unchanged with year 0."""
    m = re.search(r"[\s(]*\b((?:19|20)\d{2})\b\)?\s*$", name)
    if m and m.start() > 0:
        return name[: m.start()].strip(), int(m.group(1))
    return name.strip(), 0


def _episode_marker(base: str):
    """Find S01E02 or 1x02. Returns (season, episode, start, end) or None."""
    m = re.search(r"\bS(\d{1,2})\s?E(\d{1,3})(?:\s?-?\s?E\d{1,3})?\b", base, re.I) or re.search(r"\b(\d{1,2})x(\d{2,3})\b", base)
    return (int(m.group(1)), int(m.group(2)), m.start(), m.end()) if m else None


def _episode_info(show: str, year: int, season: int, episode: int, title: str = "") -> dict:
    return {"kind": "show", "show": show, "year": year, "season": season, "episode": episode,
            "title": title or f"Episode {episode}", "sub": episode_sub(show, season, episode)}


_SEASON_WORD = re.compile(r"(?:season|series|saison|staffel|temporada)[ ._-]*(\d{1,2})\b", re.I)
_SEASON_S = re.compile(r"(?<![a-z0-9])s(\d{1,2})(?![a-z0-9])(?!\s?e\d)", re.I)


def season_of_folder(name: str) -> int | None:
    """'Season 1', 'Season 01 (1080p)', 'S02', 'Ted.S04.1080p.WEB', 'Specials' -> season number (0 for specials)."""
    name = name.strip()
    if re.fullmatch(r"specials?", name, re.I):
        return 0
    m = _SEASON_WORD.search(name) or _SEASON_S.search(name)
    return int(m.group(1)) if m else None


@lru_cache(maxsize=512)
def _has_season_dirs(folder: str) -> bool:
    """True when this folder has Season N sub-folders on disk, which makes it a TV show folder."""
    try:
        with os.scandir(folder) as it:
            return any(e.is_dir() and (season_of_folder(e.name) or 0) > 0 for e in it)
    except OSError:
        return False


def _clean_folder(name: str) -> tuple[str, int]:
    name = re.sub(r"\[[^\]]*\]", " ", re.sub(r"[._]+", " ", name))
    name = re.sub(r"\s+", " ", _TAGS.sub("", name)).strip(" -")
    return split_year(name)


def show_context(p: Path) -> tuple[str, int | None] | None:
    """(show folder name, season from the folder or None) when this file sits inside a TV show folder layout:
    Show/Season 1/ep.mkv, Show/Season 1/Disc 1/ep.mkv, or ep.mkv straight inside a Show folder that has season folders."""
    parent = p.parent
    for level, folder in enumerate((parent, parent.parent)):
        if folder.parent == folder or not folder.parent.name:
            break
        season = season_of_folder(folder.name)
        if season is not None and (season > 0 or _has_season_dirs(str(folder.parent))):
            return folder.parent.name, season
    if parent.name and _has_season_dirs(str(parent)):
        return parent.name, None
    return None


GENERIC_DIRS = {"movies", "movie", "films", "film", "video", "videos", "downloads", "download", "torrents", "media",
                "tv", "tv shows", "tvshows", "series", "shows", "music", "audio", "songs", "itunes", "itunes media",
                "new folder", "documents", "desktop", "home", "mnt", "media", "data", "storage", "library", "usb"}
EXTRA_DIRS = {"extras", "extra", "featurettes", "featurette", "behind the scenes", "deleted scenes", "interviews",
              "trailers", "trailer", "samples", "sample", "scenes", "shorts", "other", "bonus", "bonus features"}
_EXTRA_FILE = re.compile(r"(?:^|[\s._-])(sample|trailer|teaser)$", re.I)
ART_NAMES = ("poster", "folder", "cover", "front", "movie", "album", "albumart", "thumb")
ART_EXT = (".jpg", ".jpeg", ".png", ".webp")


def is_extra_name(path: str | Path) -> bool:
    """A sample, trailer or teaser file by name."""
    return bool(_EXTRA_FILE.search(Path(path).stem))


def local_art(folder: str | Path, extra_stems: tuple[str, ...] = ()) -> str:
    """poster.jpg, folder.png, cover.jpg ... sitting in a folder. '' when there is none."""
    try:
        names = {n.lower(): n for n in os.listdir(folder)}
    except OSError:
        return ""
    for stem in tuple(x.lower() for x in extra_stems) + ART_NAMES:
        for ext in ART_EXT:
            if stem + ext in names:
                return str(Path(folder) / names[stem + ext])
    return ""


@lru_cache(maxsize=512)
def _main_videos(folder: str) -> int:
    """How many real videos (not samples or trailers) a folder holds directly."""
    try:
        with os.scandir(folder) as it:
            return sum(1 for e in it if e.is_file() and media_of(e.name) == "video" and not is_extra_name(e.name))
    except OSError:
        return 0


def _movie_from_folder(p: Path, info: dict) -> dict:
    """Movies/Heat (1995)/whatever.mkv is 'Heat', 1995: a movie's own folder names it when it holds one film."""
    folder = p.parent
    if not folder.name or folder.name.lower() in GENERIC_DIRS or season_of_folder(folder.name) is not None:
        return info
    if folder.name.lower() in EXTRA_DIRS or _main_videos(str(folder)) != 1:
        return info
    named = parse_name(folder.name + ".mkv", "video")
    if named["kind"] != "movie" or not named["title"]:
        return info
    year = named["sub"] or info.get("sub", "")
    return {"kind": "movie", "title": named["title"], "sub": year}


_DISC = re.compile(r"^(?:disc|disk|cd|dvd)[\s._-]*\d{1,2}$", re.I)


def _clean_album(name: str) -> tuple[str, str]:
    """'Abbey Road (1969)', '[1969] Abbey Road', '1969 - Abbey Road [FLAC]' -> ('Abbey Road', '1969')"""
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\s*[\[(](?:flac|mp3|320|v0|web|cd|lossless|remaster(?:ed)?|deluxe edition)[^\])]*[\])]", "", name, flags=re.I)
    year = ""
    m = re.match(r"^[\[(]?((?:19|20)\d{2})[\])]?\s*[-–]?\s+(.+)$", name)
    if m:
        year, name = m.group(1), m.group(2)
    else:
        m = re.search(r"\s*[\[(]((?:19|20)\d{2})[\])]\s*$", name)
        if m:
            year, name = m.group(1), name[: m.start()]
    return re.sub(r"\s+", " ", name).strip(" -"), year


def parse_music_path(path: str | Path, tags: dict | None = None) -> dict:
    """Artist/Album/01 - Title.flac, Artist - Album/01 Title.mp3, Album/Disc 1/01.flac. Tags win for the title and
    track number; the folders win for artist and album so one album always stays together."""
    p = Path(path)
    tags = tags or {}
    stem = re.sub(r"\s+", " ", re.sub(r"[._]+", " ", p.stem)).strip()
    disc, track = 0, 0
    m = re.match(r"^(\d{1,2})[-.](\d{1,3})\s+(.*)$", stem)         # 1-03 Title
    if m:
        disc, track, stem = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        m = re.match(r"^(\d{1,3})\s*[-.)]?\s+(.*)$", stem)
        if m:
            track, stem = int(m.group(1)), m.group(2)
    album_dir = p.parent
    if _DISC.match(album_dir.name) and album_dir.parent != album_dir:
        dm = re.search(r"\d+", album_dir.name)
        disc = disc or (int(dm.group()) if dm else 1)
        album_dir = album_dir.parent
    album, artist = "", ""
    if album_dir.name and album_dir.name.lower() not in GENERIC_DIRS:
        album, _y = _clean_album(album_dir.name)
        parent = album_dir.parent
        if parent != album_dir and parent.name and parent.name.lower() not in GENERIC_DIRS and not _DISC.match(parent.name):
            artist = re.sub(r"[._]+", " ", parent.name).strip()
        elif " - " in album:                                       # Artist - Album
            artist, album = (x.strip() for x in album.split(" - ", 1))
    parts = stem.split(" - ")
    title_artist = ""
    if len(parts) >= 2:
        title_artist, title = parts[0].strip(), " - ".join(parts[1:]).strip()
    else:
        title = stem
    if artist and title_artist.lower() == artist.lower():           # "Artist - 03 - Title" repeats the folder
        pass
    album = tags.get("album") or album
    artist = tags.get("album_artist") or artist or tags.get("artist") or title_artist
    title = tags.get("title") or title
    try:
        track = int(str(tags.get("track", "")).split("/")[0]) if tags.get("track") else track
    except ValueError:
        pass
    try:
        disc = int(str(tags.get("disc", "")).split("/")[0]) if tags.get("disc") else disc
    except ValueError:
        pass
    return {"kind": "music", "title": title or p.stem, "sub": artist, "album": album, "track": track, "disc": disc}


@lru_cache(maxsize=256)
def _compact_numbering(folder: str, season: int) -> bool:
    """True when the videos in a season folder are numbered season-then-episode (101, 102 ... in Season 1, 201 ... in
    Season 2): every file that starts with a number has the season's digits first. A folder with any other numbering
    (a daily show with a real episode 105, say) is left alone."""
    try:
        with os.scandir(folder) as it:
            names = [e.name for e in it if e.is_file() and media_of(e.name) == "video" and not is_extra_name(e.name)]
    except OSError:
        return False
    nums = []
    for name in names:
        m = re.match(r"\s*(\d{1,4})(?!\d)", re.sub(r"[._]+", " ", Path(name).stem))
        if m:
            nums.append(int(m.group(1)))
    return bool(nums) and all(n >= 100 and n // 100 == season and 1 <= n % 100 <= 99 for n in nums)


def parse_path(path: str | Path) -> dict:
    """Like parse_name, but a TV show folder layout decides the show, so files in one show folder
    always land in one show: Ted/Season 1/ep.mkv, Ted/Season 02/03 - Title.mkv, Ted/Specials/Making of.mkv."""
    p = Path(path)
    media = media_of(p) or "video"
    info = parse_name(p.name, media)
    if media != "video":
        return parse_music_path(p)
    ctx = show_context(p)
    if not ctx:
        return _movie_from_folder(p, info) if info["kind"] == "movie" else info
    show, year = _clean_folder(ctx[0])
    show = show or "Unknown Show"
    stem = re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", " ", re.sub(r"[._]+", " ", p.stem))).strip()
    marker = _episode_marker(stem)
    if marker:
        season, episode, _start, end = marker
        rest = _TAGS.sub("", stem[end:]).strip(" -")
        return _episode_info(show, year, season, episode, rest)
    if ctx[1] is None:
        return info                      # a loose file in a show folder with no episode number: leave it alone
    season = ctx[1]
    stem = _TAGS.sub("", stem).strip()
    n = re.search(r"(?<!\d)(\d{1,3})(?!\d)", stem)
    if n:
        rest = re.sub(r"^[\s\-–]*(?:e|ep|episode)?\s?\d{1,3}[\s\-–]*", "", stem[n.start():], flags=re.I).strip()
        number = int(n.group(1))
        if season > 0 and n.start() == 0 and number >= 100 and number // 100 == season and 1 <= number % 100 <= 99 \
                and _compact_numbering(str(p.parent), season):
            number %= 100                          # "103 - Title" in Season 1 is episode 3: season digit(s) then two digits
        return _episode_info(show, year, season, number, rest)
    return _episode_info(show, year, season, 0, stem)       # no number at all, such as a "Making of" special


def parse_name(filename: str, media: str) -> dict:
    """Guess kind, title and subtitle from a file name."""
    base = Path(filename).stem
    base = re.sub(r"[._]+", " ", base)
    base = re.sub(r"\[[^\]]*\]", " ", base)
    base = re.sub(r"\s+", " ", base).strip()

    if media == "audio":
        base = re.sub(r"^\d{1,3}\s*[-.)]?\s+", "", base)
        parts = base.split(" - ")
        if len(parts) >= 2:
            return {"kind": "music", "sub": parts[0].strip(), "title": " - ".join(parts[1:]).strip() or base}
        return {"kind": "music", "sub": "", "title": base or filename}

    ep = _episode_marker(base)
    if ep:
        season, episode, start, end = ep
        show, year = split_year(base[:start].strip(" -"))
        rest = _TAGS.sub("", base[end:]).strip(" -")
        return _episode_info(show or base, year, season, episode, rest)

    year = ""
    y = re.search(r"\b(19[2-9]\d|20[0-4]\d)\b", base)
    if y and y.start() > 0:
        year = y.group(1)
        base = base[: y.start()]
    base = _TAGS.sub("", base)
    base = re.sub(r"[(\-\s]+$", "", base).strip()
    return {"kind": "movie", "title": base or filename, "sub": year}


def _decode_text(body: bytes) -> str:
    if not body:
        return ""
    enc, data = body[0], body[1:]
    codec = "latin-1"
    if enc == 1:
        if data[:2] == b"\xff\xfe":
            codec, data = "utf-16-le", data[2:]
        elif data[:2] == b"\xfe\xff":
            codec, data = "utf-16-be", data[2:]
        else:
            codec = "utf-16-le"
    elif enc == 2:
        codec = "utf-16-be"
    elif enc == 3:
        codec = "utf-8"
    try:
        return data.decode(codec, "replace").strip("\x00").strip()
    except Exception:
        return ""


def read_id3(path: str | Path, max_bytes: int = 8_000_000) -> dict | None:
    """Read title, artist and embedded cover from an ID3v2 tag.

    Returns {"title", "artist", "album", "pic": bytes, "pic_mime": str} with only the keys found.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(10)
            if len(head) < 10 or head[:3] != b"ID3":
                return None
            ver = head[3]
            size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
            buf = f.read(min(size, max_bytes))
    except OSError:
        return None

    out: dict = {}
    p = 0
    while p + 10 <= len(buf):
        fid = buf[p : p + 4]
        if not re.fullmatch(rb"[A-Z0-9]{4}", fid):
            break
        raw = buf[p + 4 : p + 8]
        if ver == 4:
            fs = (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]
        else:
            fs = int.from_bytes(raw, "big")
        body = buf[p + 10 : p + 10 + fs]
        p += 10 + fs
        key = fid.decode("ascii")
        if key == "TIT2":
            out["title"] = _decode_text(body)
        elif key == "TPE1":
            out["artist"] = _decode_text(body)
        elif key == "TALB":
            out["album"] = _decode_text(body)
        elif key == "APIC" and "pic" not in out and body:
            enc, i = body[0], 1
            while i < len(body) and body[i] != 0:
                i += 1
            mime = body[1:i].decode("latin-1", "replace")
            i += 2  # terminator + picture type
            if enc in (0, 3):
                while i < len(body) and body[i] != 0:
                    i += 1
                i += 1
            else:
                while i + 1 < len(body) and not (body[i] == 0 and body[i + 1] == 0):
                    i += 2
                i += 2
            if i < len(body):
                out["pic"] = bytes(body[i:])
                out["pic_mime"] = mime if "/" in mime else "image/" + (mime or "jpeg").lower()
    return out or None
