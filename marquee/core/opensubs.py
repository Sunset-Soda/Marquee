"""Download subtitles from OpenSubtitles.com (https://opensubtitles.com), using the user's own free API key.

You need three things from your OpenSubtitles account: an API key (free: Profile > API consumers), and your
username and password (a free account works). The key and login are kept in ~/.local/share/marquee/opensubtitles.json,
readable only by you. Nothing is sent anywhere else.

Matching: a hash of the video file is tried first, which finds subtitles made for this exact release. Otherwise the
show name with season and episode, or the movie title with its year.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import subdl, subtitles
from .library import Item, Library, cache_dir, data_dir

API = "https://api.opensubtitles.com/api/v1"
UA = "Marquee v0.1"
Request = Callable[..., bytes]      # (method, url, body=None, headers=None) -> response bytes


class SubError(Exception):
    """An error with a message that is fit to show the user."""


# ---------------------------------------------------------------- credentials
@dataclass
class Credentials:
    api_key: str = ""
    username: str = ""
    password: str = ""
    language: str = ""
    auto: bool = True
    subdl_key: str = ""         # optional second source, used when OpenSubtitles finds nothing or cannot be used

    @property
    def ready(self) -> bool:
        return bool(self.api_key.strip() or self.subdl_key.strip())


def _cred_path() -> Path:
    return data_dir() / "opensubtitles.json"


def load_credentials() -> Credentials:
    try:
        raw = json.loads(_cred_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    c = Credentials(api_key=str(raw.get("api_key", "")), username=str(raw.get("username", "")),
                    password=str(raw.get("password", "")), language=str(raw.get("language", "")),
                    auto=bool(raw.get("auto", True)), subdl_key=str(raw.get("subdl_key", "")))
    if not c.language:
        c.language = (os.environ.get("LANG", "en") or "en").split("_")[0].split(".")[0].lower() or "en"
        if c.language == "c" or len(c.language) not in (2, 3):
            c.language = "en"
    return c


def save_credentials(c: Credentials) -> None:
    path = _cred_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"api_key": c.api_key.strip(), "username": c.username.strip(), "password": c.password,
                       "language": c.language.strip().lower() or "en", "auto": c.auto,
                       "subdl_key": c.subdl_key.strip()}, indent=2)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # private to you
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.chmod(path, 0o600)


# ---------------------------------------------------------------- http
def http(method: str, url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 20.0) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def moviehash(path: str) -> str:
    """The OpenSubtitles file hash: size plus the 64-bit words of the first and last 64 KiB. '' for tiny files."""
    try:
        size = os.path.getsize(path)
        if size < 65536 * 2:
            return ""
        h = size
        with open(path, "rb") as f:
            for off in (0, max(0, size - 65536)):
                f.seek(off)
                buf = f.read(65536)
                for i in range(0, len(buf) - 7, 8):
                    h = (h + int.from_bytes(buf[i:i + 8], "little")) & 0xFFFFFFFFFFFFFFFF
        return f"{h:016x}"
    except OSError:
        return ""


# ---------------------------------------------------------------- client
class Client:
    def __init__(self, creds: Credentials, request: Request = http):
        self.c = creds
        self.request = request
        self.token = ""
        self.base = API

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Api-Key": self.c.api_key.strip(), "Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return {**h, **(extra or {})}

    def _call(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(sorted((k, v) for k, v in query.items() if v not in ("", None, 0)))
        try:
            return json.loads(self.request(method, url, body, self._headers()) or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SubError("OpenSubtitles did not accept your API key or login. Check them in Subtitle Settings.")
            if e.code == 406:
                raise SubError("You have used today's OpenSubtitles download allowance. It resets within 24 hours.")
            if e.code == 429:
                raise SubError("OpenSubtitles asked us to slow down. Try again in a minute.")
            raise SubError(f"OpenSubtitles returned an error ({e.code}).")
        except (urllib.error.URLError, OSError, TimeoutError):
            raise SubError("Couldn't reach OpenSubtitles. Check your connection.")
        except ValueError:
            raise SubError("OpenSubtitles sent a reply that could not be read.")

    def login(self) -> None:
        if not (self.c.username and self.c.password) or self.token:
            return
        reply = self._call("POST", "/login", {"username": self.c.username, "password": self.c.password})
        self.token = reply.get("token", "")
        host = reply.get("base_url", "")
        if host:
            self.base = "https://" + host.replace("https://", "").rstrip("/") + "/api/v1"
        if not self.token:
            raise SubError("OpenSubtitles did not accept your username and password.")

    def search(self, query: dict) -> list[dict]:
        return self._call("GET", "/subtitles", query=query).get("data", [])

    def download(self, file_id: int) -> bytes:
        self.login()
        reply = self._call("POST", "/download", {"file_id": file_id, "sub_format": "srt"})
        link = reply.get("link")
        if not link:
            raise SubError(reply.get("message") or "OpenSubtitles did not give a download link.")
        try:
            return self.request("GET", link, None, {"Accept": "*/*"})
        except (urllib.error.URLError, OSError, TimeoutError):
            raise SubError("Couldn't download the subtitle file.")


_NOISE = {"the", "and", "mkv", "mp4", "avi", "srt", "en", "eng"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 1 and t not in _NOISE}


def release_match(release: str, filename: str) -> float:
    """0..1: how much of the video file's name (resolution, source, group...) the subtitle's release name shares.
    A subtitle cut for the same release as your file is far more likely to be in time with it."""
    want = _tokens(filename)
    return len(want & _tokens(release)) / len(want) if want else 0.0


def _score(r: dict, want_sdh: bool, filename: str = "") -> tuple:
    a = r.get("attributes", {})
    return (bool(a.get("moviehash_match")), round(release_match(a.get("release", ""), filename), 1),
            bool(a.get("hearing_impaired")) == want_sdh,
            not a.get("foreign_parts_only"), not a.get("machine_translated"), a.get("download_count") or 0,
            a.get("ratings") or 0)


_SXE = re.compile(r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?!\d)|(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?!\d)", re.I)


def fits_episode(r: dict, season: int, episode: int) -> bool:
    """False when the result says it is for a different episode, either in its details or in the S01E02 of its
    release or file name. Results that do not say are given the benefit of the doubt."""
    a = r.get("attributes", {})
    fd = a.get("feature_details") or {}
    s, e = fd.get("season_number"), fd.get("episode_number")
    try:
        if s not in (None, "") and e not in (None, "") and (s or e):
            if int(s) != season or int(e) != episode:
                return False
    except (TypeError, ValueError):
        pass
    for name in (a.get("release", ""), ((a.get("files") or [{}])[0]).get("file_name", "")):
        m = _SXE.search(name or "")
        if m:
            ss, ee = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if int(ss) != season or int(ee) != episode:
                return False
    return True


def ranked(results: list[dict], want_sdh: bool = False, filename: str = "", episode: tuple[int, int] | None = None) -> list[dict]:
    usable = [r for r in results if (r.get("attributes", {}).get("files") or [{}])[0].get("file_id")
              and (episode is None or fits_episode(r, *episode))]
    return sorted(usable, key=lambda r: _score(r, want_sdh, filename), reverse=True)


def pick(results: list[dict], want_sdh: bool = False, filename: str = "", episode: tuple[int, int] | None = None) -> dict | None:
    best = ranked(results, want_sdh, filename, episode)
    return best[0] if best else None


def imdb_id(lib: Library, item: Item, request: Request) -> str:
    """The show's IMDb number (digits), from TVmaze, so OpenSubtitles can look up the exact episode instead of
    guessing from the name. '' when unknown."""
    info = lib.shows.get(item.show_key) if item.kind == "show" else None
    if not info or not info.tvmaze_id:
        return ""
    if getattr(info, "imdb_of", 0) != info.tvmaze_id:        # read for another match (or before matches were tracked)
        info.imdb = ""
    if not getattr(info, "imdb", ""):
        try:
            raw = json.loads(request("GET", f"https://api.tvmaze.com/shows/{info.tvmaze_id}", None, {"Accept": "application/json"}) or b"{}")
            info.imdb = str((raw.get("externals") or {}).get("imdb") or "").lstrip("t") or "-"
            info.imdb_of = info.tvmaze_id
            lib.save()
        except Exception:
            return ""
    return "" if info.imdb == "-" else info.imdb.lstrip("t")


def queries_for(lib: Library, item: Item, language: str, imdb: str = "") -> list[dict]:
    """Searches to try, most exact first."""
    out: list[dict] = []
    h = moviehash(item.path)
    if h:
        out.append({"moviehash": h, "languages": language})
    if item.kind == "show" and item.show_key:
        info = lib.shows.get(item.show_key)
        if item.episode > 0:                                  # an unnumbered special can only match by file hash
            name = info.name if info and info.name else item.title
            if imdb:                                          # the exact show, so season and episode really filter
                out.append({"parent_imdb_id": imdb, "season_number": item.season, "episode_number": item.episode,
                            "type": "episode", "languages": language})
            ep = (info.episodes.get(f"{item.season}:{item.episode}") or {}).get("name") if info else ""
            if ep:                                            # the episode's own title finds it even if numbers are ignored
                out.append({"query": f"{name} {ep}", "season_number": item.season, "episode_number": item.episode,
                            "type": "episode", "languages": language})
            out.append({"query": f"{name} S{item.season:02d}E{item.episode:02d}", "season_number": item.season,
                        "episode_number": item.episode, "type": "episode", "languages": language})
            out.append({"query": name, "season_number": item.season,
                        "episode_number": item.episode, "type": "episode", "languages": language,
                        "year": info.year if info and info.year else ""})
    elif item.kind == "movie":
        out.append({"query": item.title, "year": item.sub if item.sub.isdigit() else "", "type": "movie", "languages": language})
    return out


def _log(msg: str) -> None:
    print("marquee: " + msg, file=sys.stderr, flush=True)


# ---- telling episodes apart by what is said in them
def dialogue(cues) -> frozenset:
    """The distinct lines of a subtitle, lower-cased with punctuation dropped, so two releases of the same episode
    compare as the same and two different episodes do not."""
    return frozenset(t for t in (re.sub(r"[^a-z0-9]+", " ", c.text.lower()).strip() for c in cues) if len(t) > 8)


def same_dialogue(a: frozenset, b: frozenset, threshold: float = 0.35) -> bool:
    """True when most of the shorter subtitle's lines also appear in the other: the same episode."""
    if len(a) < 20 or len(b) < 20:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


def _files_of(it: Item) -> list[str]:
    beside = [t.path for t in subtitles.sidecars(it.path)] if it.exists else []
    return [p for p in dict.fromkeys(list(it.sub_files) + beside) if os.path.exists(p)]


def _dialogue_of(path: str) -> frozenset:
    try:
        return dialogue(subtitles.parse_file(path))
    except Exception:
        return frozenset()


def other_episodes_dialogue(lib: Library, item: Item) -> list[tuple[Item, frozenset]]:
    """The dialogue of subtitles already held for the other episodes of this show."""
    out = []
    for it in lib.items:
        if it.kind == "show" and it.show_key == item.show_key and it.id != item.id and (it.season, it.episode) != (item.season, item.episode):
            for p in _files_of(it):
                d = _dialogue_of(p)
                if d:
                    out.append((it, d))
    return out


def reset_stale_attempts(lib: Library) -> None:
    """An earlier version marked a video as tried even when the lookup failed for a passing reason (the daily limit,
    no connection), so it was never looked up again. Clear those marks once; only an answer of "nothing exists for
    this" is remembered from now on."""
    if lib.prefs.get("sub_retry_v2"):
        return
    for it in lib.items:
        it.sub_tried = False
    lib.prefs["sub_retry_v2"] = True
    lib.save()


def repair_duplicates(lib: Library) -> int:
    """An earlier version could save one episode's subtitles for other episodes of a show, from a different
    release so not byte for byte the same. Where a subtitle says the same things as the subtitle of an earlier
    episode, delete it so it is looked up again properly. The earliest episode keeps its file. Returns how many
    files were removed."""
    shows: dict[str, list[Item]] = {}
    for it in lib.items:
        if it.kind == "show" and it.show_key and it.episode > 0:
            shows.setdefault(it.show_key, []).append(it)
    removed = 0
    for eps in shows.values():
        eps.sort(key=lambda i: (i.season, i.episode))
        kept: list[tuple[Item, frozenset]] = []
        for it in eps:
            mine = []
            for p in _files_of(it):
                d = _dialogue_of(p)
                if d and any(o.id != it.id and same_dialogue(d, od) for o, od in kept):
                    try:
                        Path(p).unlink()
                    except OSError:
                        continue
                    if p in it.sub_files:
                        it.sub_files.remove(p)
                    if it.sub_sel == "f:" + p:
                        it.sub_sel = ""
                    it.sub_tried = False
                    removed += 1
                    _log(f"removed {Path(p).name}: it says the same as an earlier episode's subtitles")
                elif d:
                    mine.append((it, d))
            kept.extend(mine)
    if removed:
        lib.save()
    return removed


def destination(item: Item, language: str) -> Path:
    """Next to the video when that folder is writable (other players find it there too), otherwise the cache."""
    beside = Path(item.path).with_suffix(f".{language}.srt")
    try:
        if os.access(beside.parent, os.W_OK):
            return beside
    except OSError:
        pass
    return cache_dir() / "subs" / "downloaded" / f"{item.id}.{language}.srt"


def _from_opensubtitles(lib: Library, item: Item, creds: Credentials, request: Request, lang: str, want, others) -> tuple[str, str]:
    """(subtitle text, why there is none). Raises SubError for a failure that is worth telling the user about."""
    client = Client(creds, request)
    candidates: list[dict] = []
    for q in queries_for(lib, item, lang, imdb_id(lib, item, request)):
        for page in ((1, 2, 3) if want else (1,)):            # the right episode may not be on the first page
            found = client.search({**q, "page": page})
            candidates += [r for r in ranked(found, False, Path(item.path).stem, want) if r not in candidates]
            if candidates or not found:
                break
        if candidates:
            break
    if not candidates:
        return "", "No subtitles found for this one."
    for best in candidates[:2]:                               # downloads are limited per day, so only two tries
        a = best["attributes"]
        _log(f"subtitles for {Path(item.path).name}: trying {a.get('release')!r} "
             f"{(a.get('files') or [{}])[0].get('file_name')!r} {a.get('feature_details')}")
        cand = subtitles.decode(client.download(a["files"][0]["file_id"]))
        cues = subtitles.parse(cand, "x.srt")
        if not cues:
            raise SubError("The downloaded subtitle file was empty or not readable.")
        d = dialogue(cues)
        clash = next((o for o, od in others if same_dialogue(d, od)), None)
        if clash:
            _log(f"rejected: it says the same as S{clash.season:02d}E{clash.episode:02d}, not this episode")
            continue
        return cand, ""
    return "", "The only subtitles found were for a different episode."


def _from_subdl(lib: Library, item: Item, creds: Credentials, request: Request, lang: str, want, others) -> tuple[str, str]:
    """The same, from Subdl. Raises subdl.SubdlError."""
    info = lib.shows.get(item.show_key) if item.kind == "show" else None
    if item.kind == "show":
        name, year = (info.name if info and info.name else item.title), (info.year if info and info.year else 0)
    else:
        name, year = item.title, (item.sub if item.sub.isdigit() else 0)
    imdb = imdb_id(lib, item, request) if item.kind == "show" else ""
    q = subdl.query_for(creds.subdl_key, item.kind, name, year, item.season, item.episode, imdb, lang)
    _log(f"subtitles for {Path(item.path).name}: trying Subdl ({'imdb ' + imdb if imdb else 'by name'}; "
         f"show {name!r} {year or ''}, TVmaze #{info.tvmaze_id if info else '-'})")
    found = subdl.pick(subdl.search(q, request), Path(item.path).stem, want)
    if not found and imdb and want:                           # not under that number: try the name
        found = subdl.pick(subdl.search(subdl.query_for(creds.subdl_key, item.kind, name, year, item.season, item.episode, "", lang), request),
                           Path(item.path).stem, want)
    if not found:
        _log("subdl: nothing usable for this episode")
        return "", "No subtitles found for this one."
    for sub in found[:2]:
        cand = subtitles.decode(subdl.download(sub, request, want))
        cues = subtitles.parse(cand, "x.srt")
        if not cues:
            continue
        d = dialogue(cues)
        if next((o for o, od in others if same_dialogue(d, od)), None):
            _log("rejected: Subdl's subtitle says the same as another episode's")
            continue
        _log(f"subtitles for {Path(item.path).name}: using Subdl's {sub.get('release_name') or sub.get('name')!r}")
        return cand, ""
    return "", "The only subtitles found were for a different episode."


def fetch_for_item(lib: Library, item: Item, creds: Credentials | None = None, request: Request = http) -> tuple[str, str, str]:
    """Find and save the best subtitle for a video. Returns (status, message, saved path). Blocking: use a worker thread.
    OpenSubtitles is tried first; Subdl (when a key is set) is the backup, including when OpenSubtitles can't be used
    (key rejected, daily limit reached). status is "ok", "none" (nothing found), "nokey" (not set up) or "error"."""
    creds = creds or load_credentials()
    if not creds.ready:
        return "nokey", "Add your OpenSubtitles API key in Subtitle Settings to download subtitles.", ""
    if item.media != "video" or not item.exists:
        return "none", "Subtitles can only be downloaded for videos that are on disk.", ""
    lang = creds.language or "en"
    source = "OpenSubtitles"
    try:
        want = (item.season, item.episode) if item.kind == "show" and item.episode > 0 else None
        others = other_episodes_dialogue(lib, item) if want else []
        text, note, problem = "", "No subtitles found for this one.", ""
        if creds.api_key.strip():
            try:
                text, note = _from_opensubtitles(lib, item, creds, request, lang, want, others)
            except SubError as e:
                problem = str(e)
        if not text and creds.subdl_key.strip():
            source = "Subdl"
            try:
                text, note2 = _from_subdl(lib, item, creds, request, lang, want, others)
                note = note2 or note
            except subdl.SubdlError as e:
                problem = problem or str(e)
        if not text:
            if problem:
                return "error", problem, ""
            return "none", note, ""
        for have in _files_of(item):                          # the same file again: keep the one already there
            try:
                if Path(have).read_text(encoding="utf-8", errors="ignore") == text:
                    return "ok", "Subtitles are already downloaded", have
            except OSError:
                pass
        dest = destination(item, lang)
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 1
        while dest.exists():                                  # never overwrite one the user already has
            n += 1
            dest = dest.with_suffix(f".{n}.srt")
        dest.write_text(text, encoding="utf-8")
    except SubError as e:
        return "error", str(e), ""
    except OSError:
        return "error", "Couldn't save the subtitle file.", ""
    if str(dest) not in item.sub_files:
        item.sub_files.append(str(dest))
    item.sub_sel = "f:" + str(dest)
    lib.save()
    name = subtitles.LANG.get(lang, lang.upper())
    return "ok", f"Downloaded {name} subtitles" + (f" from {source}" if source != "OpenSubtitles" else ""), str(dest)
