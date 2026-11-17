"""TV lookups through TVmaze (https://www.tvmaze.com/api): show poster, season posters and episode names.

Data and images are from TVmaze under CC BY-SA 4.0, and the app credits them in its About dialog and README.
Everything here is blocking network I/O. Run it off the UI thread.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
from typing import Callable

from . import net, parse
from .library import Item, Library

API = "https://api.tvmaze.com"
Fetch = Callable[[str], bytes]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def search(query: str, fetch: Fetch = net.get) -> list[dict]:
    return json.loads(fetch(f"{API}/search/shows?" + urllib.parse.urlencode({"q": query})))


def best_match(results: list[dict], name: str, year: int = 0, rank: Callable[[dict], float] | None = None) -> dict | None:
    """Choose the right series from search results.

    An exact name wins over a looser one, a matching premiere year wins among exact names, and TVmaze's own
    relevance score breaks the rest. A result whose name does not even contain what we searched is rejected,
    so a wrong show is never matched silently.

    When several shows tie on name and year (an original and its reboot, both called "Scrubs"), `rank` is asked
    about each tied show and the highest wins before TVmaze's score is consulted. The library passes how many of the
    user's own episodes each show actually has.
    """
    want = _norm(name)
    ranked = []
    for r in results:
        show = r.get("show") or {}
        got = _norm(show.get("name", ""))
        if not got or want not in got:
            continue
        premiered = (show.get("premiered") or "")[:4]
        ranked.append((got == want, bool(year) and premiered == str(year), r.get("score") or 0, show))
    if not ranked:
        return None
    top = max(t[:2] for t in ranked)
    tied = [t for t in ranked if t[:2] == top]
    if rank and len(tied) > 1:
        return max(tied, key=lambda t: (rank(t[3]), t[2]))[3]
    return max(tied, key=lambda t: t[2])[3]


def have_episodes(lib: Library, key: str) -> set[tuple[int, int]]:
    """(season, episode) of every numbered episode the user has of this show."""
    return {(it.season, it.episode) for it in lib.items if it.show_key == key and it.episode and it.episode > 0}


def _coverage(show: dict, have: set[tuple[int, int]], fetch: Fetch) -> int:
    """How many of the user's episodes this TVmaze show really has."""
    try:
        eps = json.loads(fetch(f"{API}/shows/{show['id']}/episodes"))
    except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError):
        return 0
    return len(have & {(e.get("season"), e.get("number")) for e in eps})


def _image(img: dict | None) -> str:
    img = img or {}
    return img.get("original") or img.get("medium") or ""


def _download(lib: Library, stem: str, url: str, fetch: Fetch) -> str:
    if not url:
        return ""
    try:
        return lib.write_art(stem, fetch(url))
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def apply(lib: Library, key: str, show: dict, seasons: list[dict], episodes: list[dict], fetch: Fetch = net.get) -> int:
    """Store a match: names, the show poster, season posters, then rename the episodes the user has.

    The show poster comes only from the show's own image. A season image is saved against its season and is
    never used as the show poster, even when the show has no poster of its own.
    Returns how many of the user's episodes were given a real name.
    """
    info = lib.ensure_show(key, show.get("name", ""), 0)
    new_id = int(show.get("id") or 0)
    if new_id != info.tvmaze_id:
        info.imdb, info.imdb_of = "", 0      # the IMDb number belongs to the old match: look it up again for this show
    info.tvmaze_id = new_id
    info.name = show.get("name") or info.name
    info.year = int((show.get("premiered") or "0")[:4] or 0) or info.year
    poster = _download(lib, f"show-{key}", _image(show.get("image")), fetch)
    if poster:
        info.poster = poster
    for s in seasons:
        n = s.get("number")
        if n is None:
            continue
        entry = info.seasons.setdefault(str(n), {"poster": "", "name": ""})
        entry["name"] = s.get("name") or ""
        art = _download(lib, f"season-{key}-{n}", _image(s.get("image")), fetch)
        if art:
            entry["poster"] = art
    info.episodes = {f"{e['season']}:{e['number']}": {"name": e.get("name") or "", "airdate": e.get("airdate") or ""}
                     for e in episodes if e.get("season") is not None and e.get("number") is not None}
    info.matched = True
    renamed = 0
    for it in lib.items:
        if it.show_key != key or not it.episode:
            continue
        ep = info.episodes.get(f"{it.season}:{it.episode}")
        it.sub = parse.episode_sub(info.name, it.season, it.episode)
        if ep and ep["name"] and it.title_auto:
            it.title = ep["name"]
            renamed += 1
    lib.save()
    return renamed


def scrub(lib: Library, key: str, query: str = "", force: bool = False, fetch: Fetch = net.get) -> tuple[str, str, int]:
    """Look up one show. Returns (status, text, episodes renamed).

    status is "ok" (text = "Name (year)"), "none" (nothing matched, text = what was searched) or "error".
    `query` overrides the name to search for, which is how Change Match works. `force` re-reads a show that is
    already matched, using its stored TVmaze id.
    """
    info = lib.shows.get(key)
    if not info:
        return "none", key, 0
    try:
        show = None
        have = have_episodes(lib, key)
        rank = (lambda sh: _coverage(sh, have, fetch)) if have else None
        if info.tvmaze_id and force and not query:
            show = json.loads(fetch(f"{API}/shows/{info.tvmaze_id}"))
            if have and _coverage(show, have, fetch) < 0.8 * len(have):
                show = None                                   # the stored match lacks most of the user's episodes: search again
        if show is None:
            text = query.strip() or info.name
            name, year = parse.split_year(text)
            show = best_match(search(text, fetch), name, year or (0 if info.tvmaze_id else info.year), rank)
            if not show and year:
                show = best_match(search(name, fetch), name, year, rank)
        if not show:
            return "none", query.strip() or info.name, 0
        sid = show["id"]
        seasons = json.loads(fetch(f"{API}/shows/{sid}/seasons"))
        episodes = json.loads(fetch(f"{API}/shows/{sid}/episodes"))
        renamed = apply(lib, key, show, seasons, episodes, fetch)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError):
        return "error", "", 0
    done = lib.shows[key]
    return "ok", (f"{done.name} ({done.year})" if done.year else done.name), renamed


def pending_shows(lib: Library, items: list[Item] | None = None) -> list[str]:
    """Show keys with episodes in `items` (default: whole library) that have not been matched yet."""
    seen: list[str] = []
    for it in (lib.items if items is None else items):
        if it.kind == "show" and it.show_key and it.show_key not in seen and not (lib.shows.get(it.show_key) and lib.shows[it.show_key].matched):
            seen.append(it.show_key)
    return seen
