"""Subdl (https://subdl.com): a second place to look for subtitles when OpenSubtitles has none, or can't be used.

It needs its own free API key (subdl.com > account), kept beside the OpenSubtitles login. A show is looked up by its
IMDb number when Marquee knows it, so a remake with the same name is never confused with the original; otherwise by
name and year. Subtitles arrive as .zip files.
https://subdl.com/api-doc
"""
from __future__ import annotations

import io
import json
import re
import sys
import urllib.error
import urllib.parse
import zipfile
from typing import Callable

API = "https://api.subdl.com/api/v1/subtitles"
DOWNLOAD = "https://dl.subdl.com"
Request = Callable[..., bytes]

_SXE = re.compile(r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?!\d)|(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?!\d)", re.I)


def _log(msg: str) -> None:
    print("marquee: " + msg, file=sys.stderr, flush=True)


class SubdlError(Exception):
    """A message fit to show the user."""


def query_for(key: str, kind: str, name: str, year: int | str, season: int, episode: int, imdb: str, language: str) -> dict:
    q: dict = {"api_key": key.strip(), "languages": language.upper(), "subs_per_page": 30}
    if kind == "show":
        q.update({"type": "tv", "season_number": season, "episode_number": episode})
    else:
        q["type"] = "movie"
    if imdb:
        q["imdb_id"] = "tt" + imdb.lstrip("t")
    else:
        q["film_name"] = name
        if year:
            q["year"] = year
    return q


def search(query: dict, request: Request) -> list[dict]:
    url = API + "?" + urllib.parse.urlencode(query)
    try:
        reply = json.loads(request("GET", url, None, {"Accept": "application/json"}) or b"{}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SubdlError("Subdl did not accept your API key. Check it in Subtitle Settings.")
        if e.code == 429:
            raise SubdlError("Subdl asked us to slow down. Try again in a minute.")
        if e.code == 404:
            return []
        raise SubdlError(f"Subdl returned an error ({e.code}).")
    except (urllib.error.URLError, OSError, TimeoutError):
        raise SubdlError("Couldn't reach Subdl. Check your connection.")
    except ValueError:
        raise SubdlError("Subdl sent a reply that could not be read.")
    shown = {k: v for k, v in query.items() if k != "api_key"}
    if not isinstance(reply, dict):
        _log(f"subdl: unexpected reply for {shown}: {str(reply)[:200]}")
        return []
    if reply.get("status") is False:
        why = str(reply.get("error") or reply.get("message") or "no reason given")
        _log(f"subdl: refused {shown}: {why}")
        if re.search(r"key|auth|token|limit|quota", why, re.I):
            raise SubdlError(f"Subdl said: {why[:150]}")
        return []
    subs = [s for s in reply.get("subtitles") or [] if isinstance(s, dict) and s.get("url")]
    names = [(r.get("name"), r.get("year")) for r in (reply.get("results") or [])[:3] if isinstance(r, dict)]
    _log(f"subdl: {len(subs)} subtitle(s) for {shown}; matched title(s) {names}")
    return subs


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 1}


def pick(subs: list[dict], filename: str = "", episode: tuple[int, int] | None = None) -> list[dict]:
    """Usable results, best first. A result that says it is for another episode, or a whole season in one file, is dropped."""
    out = []
    for s in subs:
        if episode:
            if s.get("full_season"):
                continue
            try:
                ss, ee = s.get("season"), s.get("episode")
                if ss not in (None, "", 0) and ee not in (None, "", 0) and (int(ss), int(ee)) != episode:
                    continue
            except (TypeError, ValueError):
                pass
            m = _SXE.search(s.get("release_name") or s.get("name") or "")
            if m:
                a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                if (int(a), int(b)) != episode:
                    continue
        out.append(s)
    if len(out) < len(subs):
        _log(f"subdl: dropped {len(subs) - len(out)} of {len(subs)} as other episodes or season packs")
    want = _tokens(filename)

    def score(s: dict):
        have = _tokens(s.get("release_name") or s.get("name") or "")
        return (len(want & have) / len(want) if want else 0.0, not s.get("hi"))
    return sorted(out, key=score, reverse=True)


def download(sub: dict, request: Request, episode: tuple[int, int] | None = None) -> bytes:
    """The subtitle file (as bytes) from a result's zip."""
    path = str(sub["url"])
    url = path if path.startswith("http") else DOWNLOAD + (path if path.startswith("/") else "/" + path)
    try:
        raw = request("GET", url, None, {"Accept": "*/*"})
    except (urllib.error.URLError, OSError, TimeoutError):
        raise SubdlError("Couldn't download the subtitle file from Subdl.")
    if not raw[:2] == b"PK":                                  # not a zip: the file itself
        return raw
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise SubdlError("Subdl sent a file that could not be opened.")
    names = [n for n in zf.namelist() if n.lower().endswith((".srt", ".vtt", ".ass", ".ssa", ".sub"))]
    if episode:                                               # a pack: take the file for this episode
        for n in names:
            m = _SXE.search(n)
            if m and (int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4))) == episode:
                return zf.read(n)
        if len(names) > 1:
            raise SubdlError("That download held several episodes and none was this one.")
    srt = [n for n in names if n.lower().endswith(".srt")] or names
    if not srt:
        raise SubdlError("The Subdl download had no subtitle file in it.")
    return zf.read(srt[0])
