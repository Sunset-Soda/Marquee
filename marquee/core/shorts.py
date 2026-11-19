"""YouTube Shorts: where the short videos come from.

A Short is an ordinary YouTube video of up to about a minute, so playing one uses the same yt-dlp lookup as any video.
This module only finds them: a channel's Shorts tab, a search, or the channels you subscribe to. There is no account
feed to copy, so "For you" is built from your subscriptions or a random topic.
"""
from __future__ import annotations

import random
from itertools import zip_longest
from concurrent.futures import ThreadPoolExecutor

from . import youtube as yt

SHORT_MAX = 61            # seconds; a Short can be a little over a minute
TOPICS = ["funny", "satisfying", "cooking", "science", "animals", "sports", "music", "gaming", "travel", "art"]


def _as_short(v: dict) -> dict:
    v = dict(v)
    v["short"] = True
    return v


def channel_shorts(dlp, channel_id: str, n: int = 12) -> list[dict]:
    """The newest Shorts of one channel."""
    d = dlp._json("--flat-playlist", "--dump-single-json", "--playlist-end", str(n),
                  f"https://www.youtube.com/channel/{channel_id}/shorts")
    name = d.get("channel") or d.get("uploader") or ""
    out = []
    for e in yt.entries(d):
        v = yt.norm_video(e)
        if v:
            v["channel"] = v["channel"] or name
            v["channelId"] = v["channelId"] or channel_id
            out.append(_as_short(v))
    return out


def search_shorts(dlp, query: str, n: int = 20) -> list[dict]:
    """Search results that are short enough to be Shorts. Results with no length shown are kept, since a flat result
    does not always carry one."""
    found = dlp.search(f"{query.strip()} #shorts", n * 2)
    return [_as_short(v) for v in found if not v.get("live") and (v["dur"] <= 0 or v["dur"] <= SHORT_MAX)][:n]


def subscription_shorts(dlp, subs: list[dict], per: int = 6, channels: int = 14, rng: random.Random | None = None) -> list[dict]:
    """Recent Shorts from a random handful of subscribed channels, woven together so no one channel dominates."""
    rng = rng or random.Random()
    chosen = rng.sample(subs, min(channels, len(subs)))

    def one(sub):
        try:
            return channel_shorts(dlp, sub["id"], per)
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        lists = [x for x in pool.map(one, chosen) if x]
    rows = yt.interleave(lists, limit=120)
    rng.shuffle(rows)
    return rows


_STOP = set("the a an and or of to in on at for with from by is are was were be this that it its my your our you i we how what why who when "
            "best new video videos short shorts official full part episode vs".split())


def topics_from(history: list[dict], rng: random.Random | None = None, n: int = 2) -> list[str]:
    """Search words taken from what you watched lately: the first few meaningful words of a few recent titles."""
    rng = rng or random.Random()
    titles = [h.get("title", "") for h in history[:30] if h.get("title")]
    out = []
    for t in rng.sample(titles, min(n, len(titles))):
        words = [w for w in "".join(c if c.isalnum() or c.isspace() else " " for c in t).split() if w.lower() not in _STOP and len(w) > 2]
        if words:
            out.append(" ".join(words[:4]))
    return out


def discover(dlp, topics: list[str], per: int = 12) -> list[dict]:
    """Shorts from searches, none of them tied to a channel you follow."""
    def one(t):
        try:
            return search_shorts(dlp, t, per)
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=4) as pool:
        lists = [x for x in pool.map(one, topics) if x]
    return yt.interleave(lists, limit=len(topics) * per)


def from_recommendations(dlp, recs: list[dict], rng: random.Random, channels: int = 6) -> list[dict]:
    """Shorts that follow what YouTube itself recommends to your account: any Short in the home feed as it is, plus the
    newest Shorts of the channels the feed shows (apart from those you already follow, which are covered elsewhere)."""
    direct = [_as_short(v) for v in recs if v.get("id") and 0 < v.get("dur", 0) <= SHORT_MAX and not v.get("live")]
    seen, chans = set(), []
    for v in recs:
        cid = v.get("channelId")
        if cid and cid not in seen:
            seen.add(cid)
            chans.append(cid)

    def one(cid):
        try:
            return channel_shorts(dlp, cid, 5)
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        lists = [x for x in pool.map(one, rng.sample(chans, min(channels, len(chans)))) if x]
    return direct + yt.interleave(lists, limit=60)


def for_you(dlp, subs: list[dict], history: list[dict], rng: random.Random | None = None, recs: list[dict] | None = None) -> list[dict]:
    """A mix of three sources, taken in turn so none dominates:
    - what YouTube recommends to your account (only when `recs`, the home feed, could be read),
    - your subscriptions,
    - searches on things you watch and on random topics, so it is not only what you already follow."""
    rng = rng or random.Random()
    recs = recs or []
    topics = topics_from(recs, rng, 2) + topics_from(history, rng, 1 if recs else 2)
    topics += rng.sample([t for t in TOPICS if t not in topics], 2 if (subs or recs) else 4)
    found = discover(dlp, topics, 10)
    mine = subscription_shorts(dlp, subs, rng=rng)[:30] if subs else []
    theirs = from_recommendations(dlp, recs, rng) if recs else []
    rows, seen = [], set()
    for group in zip_longest(theirs, mine, found):
        for v in group:
            if v and v["id"] not in seen:
                seen.add(v["id"])
                rows.append(v)
    return rows


def more_like(dlp, title: str, n: int = 24) -> list[dict]:
    words = topics_from([{"title": title}], None, 1)
    return search_shorts(dlp, words[0] if words else title, n)


def random_topic(rng: random.Random | None = None) -> str:
    return (rng or random.Random()).choice(TOPICS)
