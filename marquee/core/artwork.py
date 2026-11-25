"""Cover art and metadata lookup. Blocking calls: run them off the UI thread.

Movies, music, podcasts and audiobooks use the iTunes Search API. TV shows use TVmaze (see tv.py), which also
supplies season artwork and episode names.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from . import net, tv
from .library import Item, Library

# kind -> (media, entity) for the iTunes Search API. TV is handled by tv.py.
LOOKUP = {
    "movie": ("movie", "movie"),
    "music": ("music", "song"),
    "podcast": ("podcast", "podcast"),
}


def searchable(item: Item) -> bool:
    return item.kind in LOOKUP and not item.sample and not item.node


def _terms(item: Item) -> list[str]:
    """What to search for, most specific first: "Title Year", then just the title (a wrong year finds nothing)."""
    if item.kind == "music":
        return [" ".join(x for x in (item.sub, item.title) if x)]
    title = re.sub(r"[._]+", " ", item.title).strip()
    terms = []
    if item.kind == "movie" and re.fullmatch(r"\d{4}", item.sub or ""):
        terms.append(f"{title} {item.sub}")
    terms.append(title)
    return terms


def lookup_url(item: Item) -> str | None:
    media, entity = LOOKUP[item.kind]
    for term in _terms(item):
        query = urllib.parse.urlencode({"term": term, "media": media, "entity": entity, "limit": "5"})
        data = json.loads(net.get("https://itunes.apple.com/search?" + query))
        for hit in data.get("results", []):
            art = hit.get("artworkUrl100") or hit.get("artworkUrl60")
            if art:
                return re.sub(r"/\d+x\d+bb", "/600x600bb", art)
    return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def wiki_poster(item: Item) -> str | None:
    """A second place to look for a movie poster, for when iTunes has none: the poster named in the infobox of the film's
    Wikipedia article. (The API's own "page image" leaves film posters out, so the infobox is read instead.)

    An article is used only if it is a film article and either mentions the movie's year or has every word of its title,
    so a different film is not mistaken for it."""
    title = re.sub(r"[._]+", " ", item.title).strip()
    year = item.sub if re.fullmatch(r"\d{4}", item.sub or "") else ""
    api = "https://en.wikipedia.org/w/api.php?"
    found = json.loads(net.get(api + urllib.parse.urlencode({
        "action": "query", "format": "json", "list": "search", "srlimit": "4", "srsearch": f"{title} {year} film".strip()})))
    want = _words(title)
    for hit in (found.get("query") or {}).get("search") or []:
        name = hit.get("title", "")
        page = json.loads(net.get(api + urllib.parse.urlencode({
            "action": "query", "format": "json", "formatversion": "2", "prop": "revisions", "rvprop": "content",
            "rvslots": "main", "rvsection": "0", "titles": name})))
        pages = (page.get("query") or {}).get("pages") or [{}]
        revs = pages[0].get("revisions") or [{}]
        text = ((revs[0].get("slots") or {}).get("main") or {}).get("content", "")
        if "infobox film" not in text.lower():
            continue
        if not ((year and year in text) or (want and want <= _words(name))):
            continue
        m = re.search(r"\|\s*image\s*=\s*(?:\[\[)?(?:(?:File|Image):)?([^\n|\]]+\.(?:jpe?g|png|webp))", text, re.I)
        if m:
            return "https://en.wikipedia.org/wiki/Special:FilePath/" + urllib.parse.quote(m.group(1).strip().replace(" ", "_")) + "?width=600"
    return None


def fetch_cover(item: Item, library: Library) -> str:
    """Find and store a cover for one non-TV item. Returns "ok", "none", "error" or "skip"."""
    if not searchable(item):
        return "skip"
    try:
        url = lookup_url(item)
        src = "iTunes"
        if not url and item.kind == "movie":
            url, src = wiki_poster(item), "Wikipedia"
        item.cover_tried = True
        print(f"[artwork] {item.kind} {item.title!r} ({item.sub}): {'found on ' + src if url else 'nothing found'}",
              file=sys.stderr, flush=True)
        if not url:
            library.save()
            return "none"
        library.set_cover_bytes(item, net.get(url))
        library.save()
        return "ok"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        print(f"[artwork] {item.kind} {item.title!r}: lookup failed: {e}", file=sys.stderr, flush=True)
        return "error"


@dataclass
class Report:
    covers: int = 0
    no_cover: int = 0
    shows: list[str] = field(default_factory=list)       # "Ted (2024)"
    episodes: int = 0                                      # episodes given a real name
    unmatched: list[str] = field(default_factory=list)    # show names nothing matched
    error: bool = False

    @property
    def did_anything(self) -> bool:
        return bool(self.covers or self.shows or self.unmatched or self.no_cover)

    def message(self) -> str:
        parts = []
        if self.shows:
            parts.append("Matched " + ", ".join(f"“{s}”" for s in self.shows)
                         + (f" and named {self.episodes} episode{'s' if self.episodes != 1 else ''}" if self.episodes else ""))
        for name in self.unmatched:
            parts.append(f"No TV match for “{name}”. Use Change Match to search for it by name")
        if self.covers:
            parts.append(f"Found cover art for {self.covers} title{'s' if self.covers != 1 else ''}")
        elif self.no_cover and not parts:
            parts.append("No matching cover art found")
        if self.error:
            parts.append("Couldn't reach the lookup service. Check your connection and try again")
        return ". ".join(parts) if parts else "Everything already has cover art and episode names"


def enrich(lib: Library, items: list[Item], notify: Callable[[], None] | None = None, force_shows: bool = False) -> Report:
    """Look up what is missing for `items`: TV matches, then cover art for everything else."""
    rep = Report()
    keys = tv.pending_shows(lib, items)
    if force_shows:
        keys = list(dict.fromkeys(i.show_key for i in items if i.kind == "show" and i.show_key))
    for key in keys:
        status, text, renamed = tv.scrub(lib, key, force=force_shows)
        if status == "error":
            rep.error = True
            break
        if status == "ok":
            rep.shows.append(text)
            rep.episodes += renamed
        else:
            rep.unmatched.append(lib.shows[key].name if key in lib.shows else text)
        if notify:
            notify()
    if not rep.error:
        for it in items:
            if it.cover or not searchable(it):
                continue
            r = fetch_cover(it, lib)
            if r == "error":
                rep.error = True
                break
            rep.covers += r == "ok"
            rep.no_cover += r == "none"
            if r == "ok" and notify:
                notify()
    return rep
