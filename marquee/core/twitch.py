"""Twitch: signing in, the channels you follow, and finding a live channel's stream. Shared by every frontend.

Signing in uses Twitch's device-code login: Marquee shows a short code, you type it at twitch.com/activate and press
Authorize. Marquee never sees your password. Twitch asks every app that uses its login to have its own (free) developer
application, so you register one at dev.twitch.tv/console (client type "Public") and paste its Client ID into Marquee.

Playing a channel goes through yt-dlp, which can read Twitch's live streams. Twitch puts its ads into the stream itself,
so Marquee cannot remove them. The optional "ad-free" switch sends the stream request through a proxy address you give it
(one in a place where Twitch does not serve ads); when that does not work Marquee plays the normal stream instead.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .library import data_dir
from .net import UA

ID_URL = "https://id.twitch.tv/oauth2"
API = "https://api.twitch.tv/helix"
SCOPE = "user:read:follows"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
PROXY_TIMEOUT = 25.0                    # seconds the ad-free route gets before the normal stream is used instead


class TwitchError(RuntimeError):
    """Something Twitch refused or could not do; the text says what to try."""


# ---- the network, in one place so tests can stand in for it ----------------------------------
def http(method: str, url: str, headers: dict | None = None, form: dict | None = None, timeout: float = 15.0) -> tuple[int, dict]:
    """(status, json) for one request. Never raises for an HTTP error status: the body usually says why."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, status = e.read(), e.code
    except (urllib.error.URLError, OSError) as e:
        raise TwitchError(f"Couldn’t reach Twitch ({getattr(e, 'reason', e)}).") from e
    try:
        return status, json.loads(body.decode("utf-8") or "{}")
    except ValueError:
        return status, {}


# ---- what is kept on this computer -----------------------------------------------------------
class Store:
    """twitch.json: your Client ID, the login, and the switches. Readable only by you, since it holds a login token."""
    def __init__(self, path=None):
        self.path = path or (data_dir() / "twitch.json")
        self.client_id = ""
        self.access = ""
        self.refresh = ""
        self.expires = 0.0
        self.user: dict = {}                      # {id, login, name, image}
        self.adfree = False                       # the ad-free switch
        self.proxy = ""                           # address of the proxy it uses
        self.browser_login = False                # let yt-dlp use the browser's Twitch login (Turbo / subscriptions)
        self.quality = "best"                     # "best" or a height such as "720"
        self._load()

    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for k in ("client_id", "access", "refresh", "proxy", "quality"):
            setattr(self, k, str(d.get(k) or getattr(self, k)))
        self.expires = float(d.get("expires") or 0)
        self.user = dict(d.get("user") or {})
        self.adfree = bool(d.get("adfree"))
        self.browser_login = bool(d.get("browser_login"))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"client_id": self.client_id, "access": self.access, "refresh": self.refresh, "expires": self.expires,
                   "user": self.user, "adfree": self.adfree, "proxy": self.proxy, "browser_login": self.browser_login,
                   "quality": self.quality}
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # private from the first byte
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
        tmp.replace(self.path)

    @property
    def signed_in(self) -> bool:
        return bool(self.access and self.user.get("id"))

    def sign_out(self):
        self.access = self.refresh = ""
        self.expires = 0.0
        self.user = {}
        self.save()


# ---- the client ------------------------------------------------------------------------------
class Twitch:
    def __init__(self, store: Store, request=None):
        self.store = store
        self.http = request or http

    # -- signing in
    def start_login(self) -> dict:
        """{device_code, user_code, url, interval, expires}: show the code and send the person to the url."""
        if not self.store.client_id:
            raise TwitchError("Paste your Twitch Client ID first (dev.twitch.tv/console → Register Your Application).")
        status, d = self.http("POST", f"{ID_URL}/device", form={"client_id": self.store.client_id, "scopes": SCOPE})
        if status != 200 or "device_code" not in d:
            raise TwitchError(self._why(d, "Twitch did not start the login. Check that the Client ID is right and that the "
                                           "application’s client type is set to Public."))
        return {"device_code": d["device_code"], "user_code": d.get("user_code", ""), "url": d.get("verification_uri", "https://www.twitch.tv/activate"),
                "interval": max(2, int(d.get("interval") or 5)), "expires": time.time() + int(d.get("expires_in") or 600)}

    def poll_login(self, login: dict) -> bool:
        """One check. True once you have authorised it (and the login is saved), False while Twitch is still waiting."""
        status, d = self.http("POST", f"{ID_URL}/token", form={
            "client_id": self.store.client_id, "scopes": SCOPE, "device_code": login["device_code"], "grant_type": DEVICE_GRANT})
        if status == 200 and d.get("access_token"):
            self._keep_tokens(d)
            self.store.user = self._me()
            self.store.save()
            return True
        msg = str(d.get("message") or "").lower()
        if "pending" in msg:
            return False
        if "slow" in msg:
            login["interval"] = login.get("interval", 5) + 5
            return False
        raise TwitchError("The login was not completed (the code may have run out). Press Sign in to start again.")

    def _keep_tokens(self, d: dict):
        self.store.access = d["access_token"]
        self.store.refresh = d.get("refresh_token") or self.store.refresh
        self.store.expires = time.time() + int(d.get("expires_in") or 3600) - 60
        self.store.save()

    def _refresh(self):
        if not self.store.refresh:
            raise TwitchError("Your Twitch login has expired. Press Sign in again.")
        status, d = self.http("POST", f"{ID_URL}/token", form={
            "client_id": self.store.client_id, "grant_type": "refresh_token", "refresh_token": self.store.refresh})
        if status != 200 or not d.get("access_token"):
            self.store.sign_out()
            raise TwitchError("Your Twitch login has expired. Press Sign in again.")
        self._keep_tokens(d)

    # -- asking Twitch
    def _api(self, path: str, **params):
        if self.store.expires and time.time() > self.store.expires:
            self._refresh()
        url = f"{API}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        for attempt in (0, 1):
            status, d = self.http("GET", url, headers={"Authorization": f"Bearer {self.store.access}", "Client-Id": self.store.client_id})
            if status == 401 and attempt == 0:
                self._refresh()
                continue
            if status != 200:
                raise TwitchError(self._why(d, f"Twitch answered {status}."))
            return d
        raise TwitchError("Twitch refused the login.")

    def _me(self) -> dict:
        rows = self._api("users").get("data") or []
        if not rows:
            raise TwitchError("Twitch did not say who is signed in.")
        u = rows[0]
        return {"id": u["id"], "login": u.get("login", ""), "name": u.get("display_name") or u.get("login", ""),
                "image": u.get("profile_image_url", "")}

    @staticmethod
    def _why(d: dict, fallback: str) -> str:
        m = d.get("message") if isinstance(d, dict) else ""
        return f"Twitch says: {m}." if m else fallback

    def following(self) -> list[dict]:
        """Every channel you follow: [{login, name, image}], sorted by name."""
        uid, out, cursor = self.store.user["id"], [], ""
        while True:
            d = self._api("channels/followed", user_id=uid, first=100, **({"after": cursor} if cursor else {}))
            out += [{"id": r["broadcaster_id"], "login": r["broadcaster_login"], "name": r.get("broadcaster_name") or r["broadcaster_login"]}
                    for r in d.get("data", [])]
            cursor = (d.get("pagination") or {}).get("cursor") or ""
            if not cursor or len(out) >= 2000:
                break
        for i in range(0, len(out), 100):                         # their pictures, a hundred at a time
            chunk = out[i:i + 100]
            for u in self._api("users", id=[c["id"] for c in chunk]).get("data", []):
                for c in chunk:
                    if c["id"] == u["id"]:
                        c["image"] = u.get("profile_image_url", "")
        for c in out:
            c.setdefault("image", "")
        return sorted(out, key=lambda c: c["name"].lower())

    def live(self) -> list[dict]:
        """The followed channels that are live now, most viewers first."""
        out, cursor = [], ""
        while True:
            d = self._api("streams/followed", user_id=self.store.user["id"], first=100, **({"after": cursor} if cursor else {}))
            for s in d.get("data", []):
                if s.get("type", "live") != "live":
                    continue
                out.append({"login": s["user_login"], "name": s.get("user_name") or s["user_login"], "title": s.get("title", ""),
                            "game": s.get("game_name", ""), "viewers": int(s.get("viewer_count") or 0), "started": s.get("started_at", ""),
                            "thumb": (s.get("thumbnail_url") or "").replace("{width}", "440").replace("{height}", "248")})
            cursor = (d.get("pagination") or {}).get("cursor") or ""
            if not cursor:
                break
        return sorted(out, key=lambda s: s["viewers"], reverse=True)


# ---- playing a channel ----------------------------------------------------------------------
def channel_login(text: str) -> str:
    """A channel name from a name or a twitch.tv link; "" when it is neither."""
    t = text.strip().lower()
    m = re.match(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/([a-z0-9_]{1,25})(?:[/?#].*)?$", t)
    if m:
        t = m.group(1)
    return t if re.fullmatch(r"[a-z0-9_]{3,25}", t) else ""


def pick_format(formats: list[dict], quality: str = "best") -> dict | None:
    """The stream to play from yt-dlp's list: the biggest picture at or under the chosen height (everything when "best").
    Twitch's own streams carry sound with the picture, so no sound track needs joining."""
    cands = [f for f in formats if f.get("url") and f.get("vcodec") != "none" and f.get("acodec") != "none"]
    if not cands:
        return None
    if str(quality).isdigit():
        under = [f for f in cands if (f.get("height") or 0) <= int(quality)]
        if not under:
            return min(cands, key=lambda f: f.get("height") or 0)       # nothing that small: the smallest there is
        cands = under

    def rank(f):
        return (f.get("height") or 0, f.get("fps") or 0, f.get("tbr") or 0)
    return max(cands, key=rank)


def _ytdlp(exe: str, args: list[str], timeout: float) -> dict:
    try:
        p = subprocess.run([exe, "--no-warnings", "--no-playlist", "--dump-single-json", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise TwitchError("Twitch took too long to answer.") from e
    except OSError as e:
        raise TwitchError(f"Couldn’t run yt-dlp: {e}") from e
    if p.returncode != 0 or not p.stdout.strip():
        err = (p.stderr or "").strip().splitlines()
        last = err[-1] if err else ""
        if "offline" in last.lower() or "not currently live" in last.lower() or "is offline" in last.lower():
            raise TwitchError("That channel is offline right now.")
        raise TwitchError(last[:200] or "yt-dlp could not open that channel.")
    return json.loads(p.stdout)


def resolve(exe: str, login: str, store: Store, cookie_spec: str = "", run=None) -> dict:
    """{url, headers, title, height, adfree, note}: how to play a live channel.

    With the ad-free switch on and a proxy address set, the stream is asked for through that proxy first. If that fails or
    is slow, the normal stream is used (with its ads) and `note` says so. `run` stands in for yt-dlp in tests."""
    run = run or (lambda args, timeout: _ytdlp(exe, args, timeout))
    base = [f"https://www.twitch.tv/{login}"]
    if store.browser_login and cookie_spec:
        base = ["--cookies-from-browser", cookie_spec, *base]
    note, tried_proxy = "", False
    d = None
    if store.adfree:
        if store.proxy.strip():
            tried_proxy = True
            try:
                d = run(["--proxy", store.proxy.strip(), *base], PROXY_TIMEOUT)
            except TwitchError as e:
                if "offline" in str(e).lower():
                    raise
                note = "Ad-free mode didn’t work (" + str(e)[:80].rstrip(".") + "), so this is the normal stream, with ads."
        else:
            note = "Ad-free mode is on but no proxy address is set, so this is the normal stream, with ads."
    if d is None:
        d = run(base, 40.0)
    fmt = pick_format(d.get("formats") or [], store.quality)
    if not fmt:
        raise TwitchError("Twitch gave no playable stream for that channel.")
    return {"url": fmt["url"], "headers": fmt.get("http_headers") or d.get("http_headers") or {}, "title": d.get("title") or login,
            "height": int(fmt.get("height") or 0), "adfree": tried_proxy and not note, "note": note,
            "heights": sorted({int(f.get("height") or 0) for f in d.get("formats") or [] if f.get("height")}, reverse=True)}


def fetch_playlist(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(600_000).decode("utf-8", "replace")


def playlist_summary(url: str, fetch=None, timeout: float = 8.0) -> list[str]:
    """What a channel's stream playlist holds, as short lines for the log: its tags (never the segment or stream addresses)
    and counts. Meant for finding out why a stream does not play."""
    if fetch is None:
        def fetch(u):
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(600_000).decode("utf-8", "replace")
    try:
        text = fetch(url)
    except Exception as e:
        return [f"playlist unreadable: {str(e)[:100]}"]
    segs = discont = 0
    tags, dates = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("#"):
            segs += 1
        elif line.startswith("#EXT-X-DISCONTINUITY"):
            discont += 1
        elif line.startswith("#EXTINF"):
            continue
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME"):
            dates.append(line.split(":", 1)[1])
        else:
            tags.append(re.sub(r"https?://\S+", "<url>", line)[:220])
    out = [f"playlist: {segs} segments, {discont} discontinuities, first time {dates[0] if dates else '-'}, last time {dates[-1] if dates else '-'}"]
    out += [f"playlist tag: {t}" for t in tags[:14]]
    return out


def _epoch(stamp: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def ad_windows(text: str) -> list[tuple[float, float]]:
    """Every commercial break listed in a playlist, as (start, end) in epoch seconds, in order."""
    out = []
    for line in text.splitlines():
        if line.startswith("#EXT-X-DATERANGE") and 'CLASS="twitch-stitched-ad"' in line:
            start = re.search(r'START-DATE="([^"]+)"', line)
            dur = re.search(r'DURATION=([\d.]+)', line)
            if start and dur:
                try:
                    b = _epoch(start.group(1))
                    out.append((b, b + float(dur.group(1))))
                except ValueError:
                    pass
    return sorted(out)


def ad_end(text: str) -> float | None:
    """When the first commercial break listed in a playlist ends (epoch seconds), or None if there is none."""
    w = ad_windows(text)
    return w[0][1] if w else None


def _parse(text: str, base: str):
    """A media playlist as (head tags, pieces, first sequence number); a piece is (time, its lines, the start-up data in force)."""
    from urllib.parse import urljoin
    head: list[str] = []
    segs: list[tuple[float | None, list[str], str]] = []
    pending: list[str] = []
    pdt: float | None = None
    cur_map = ""
    seq0 = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                seq0 = int(line.split(":", 1)[1])
            except ValueError:
                seq0 = 0
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            try:
                pdt = _epoch(line.split(":", 1)[1])
            except ValueError:
                pdt = None
            pending.append(line)
        elif line.startswith("#EXT-X-MAP"):
            cur_map = line
            pending.append(line)
        elif line.startswith("#EXT-X-DATERANGE"):
            continue
        elif line.startswith("#"):
            if segs or pending or line.startswith(("#EXTINF", "#EXT-X-DISCONTINUITY", "#EXT-X-BYTERANGE")):
                pending.append(line)
            else:
                head.append(line)
        else:
            segs.append((pdt, pending + [urljoin(base, line)], cur_map))
            pending = []
    return head, segs, seq0


def slice_playlist(text: str, base: str, lo: float | None = None, hi: float | None = None) -> tuple[str, int, int]:
    """The pieces of a live playlist from time `lo` up to (not including) `hi`, as a playlist of their own.

    Returns (playlist, pieces in it, pieces at or after `hi`). The list is marked finished (ENDLIST) only once pieces at or
    after `hi` exist, so a player keeps asking for more until the part is really complete."""
    head, segs, seq0 = _parse(text, base)

    def before_hi(t):
        return hi is None or t is None or t < hi - 0.05
    keep = [(k, b) for k, b in enumerate(segs) if (lo is None or b[0] is None or b[0] >= lo - 0.05) and before_hi(b[0])]
    beyond = sum(1 for b in segs if hi is not None and b[0] is not None and b[0] >= hi - 0.05)
    first = keep[0][0] if keep else len(segs)
    out = [h for h in head if not h.startswith("#EXT-X-MEDIA-SEQUENCE")] + ["#EXT-X-MEDIA-SEQUENCE:%d" % (seq0 + first)]
    for n, (_, (_, lines, mp)) in enumerate(keep):
        if n == 0 and mp and not any(ln.startswith("#EXT-X-MAP") for ln in lines):
            out.append(mp)                                         # the start-up data applies until a new one is named
        out += lines
    if beyond:
        out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n", len(keep), beyond


def split_at(text: str, base: str, cut: float) -> tuple[str, str]:
    """Cut a live playlist in two at time `cut`: (the part before; the part from there on)."""
    return slice_playlist(text, base, hi=cut)[0], slice_playlist(text, base, lo=cut)[0]


class AdState:
    """Where a channel's playback is relative to the commercial breaks Twitch stitches into its playlist.

    A break and the channel are separate videos that Qt's player cannot cross in one go, so each is played as its own
    playlist: the channel up to the break, the break (in full), then the channel again from where the break ends."""

    def __init__(self):
        self.floor = 0.0                                    # the channel plays from here
        self.ad: tuple[float, float] | None = None          # the next break, once it is listed

    def open(self, text: str, base: str) -> bool:
        """Start-of-stream check; True when the stream opens inside a break."""
        _, segs, _ = _parse(text, base)
        first = next((b[0] for b in segs if b[0] is not None), None)
        w = ad_windows(text)
        if first is not None:
            self.floor = first
            if w and w[0][0] - 0.5 <= first < w[0][1]:
                self.ad, self.floor = w[0], w[0][0]
                return True
        return False

    def _next(self, text: str):
        if self.ad is None:
            self.ad = next((w for w in ad_windows(text) if w[0] > self.floor + 0.05), None)
        return self.ad

    def pending(self) -> bool:
        return self.ad is not None

    def main(self, text: str, base: str) -> tuple[str, int, int]:
        w = self._next(text)
        return slice_playlist(text, base, lo=self.floor, hi=w[0] if w else None)

    def ad_part(self, text: str, base: str) -> tuple[str, int, int]:
        w = self.ad
        if w is None:
            return slice_playlist(text, base, lo=1e18)
        return slice_playlist(text, base, lo=w[0], hi=w[1])

    def advance(self) -> None:
        if self.ad:
            self.floor = self.ad[1]
        self.ad = None


def fetch_playlist(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(600_000).decode("utf-8", "replace")


def playlist_summary(url: str, fetch=None, timeout: float = 8.0) -> list[str]:
    """What a channel's stream playlist holds, as short lines for the log: its tags (never the segment or stream addresses)
    and counts. Meant for finding out why a stream does not play."""
    if fetch is None:
        def fetch(u):
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(600_000).decode("utf-8", "replace")
    try:
        text = fetch(url)
    except Exception as e:
        return [f"playlist unreadable: {str(e)[:100]}"]
    segs = discont = 0
    tags, dates = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("#"):
            segs += 1
        elif line.startswith("#EXT-X-DISCONTINUITY"):
            discont += 1
        elif line.startswith("#EXTINF"):
            continue
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME"):
            dates.append(line.split(":", 1)[1])
        else:
            tags.append(re.sub(r"https?://\S+", "<url>", line)[:220])
    out = [f"playlist: {segs} segments, {discont} discontinuities, first time {dates[0] if dates else '-'}, last time {dates[-1] if dates else '-'}"]
    out += [f"playlist tag: {t}" for t in tags[:14]]
    return out


def _epoch(stamp: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def ad_end(text: str) -> float | None:
    """When the commercial break stitched in at the start of a channel's playlist ends (epoch seconds), or None if there is none."""
    for line in text.splitlines():
        if line.startswith("#EXT-X-DATERANGE") and 'CLASS="twitch-stitched-ad"' in line:
            start = re.search(r'START-DATE="([^"]+)"', line)
            dur = re.search(r'DURATION=([\d.]+)', line)
            if start and dur:
                try:
                    return _epoch(start.group(1)) + float(dur.group(1))
                except ValueError:
                    return None
    return None


def split_at(text: str, base: str, cut: float) -> tuple[str, str]:
    """Cut a live playlist in two at the time `cut`: (the part before; the part from there on).

    The commercial break and the channel are separate videos (different size, different start-up data), which Qt's player
    cannot cross in one go. So the break is played as its own playlist and, when it ends, the channel as another.
    Nothing is hidden: the break is played in full. The first part is only marked finished once the channel's video has
    started appearing (Twitch publishes the break piece by piece), so the player keeps asking for the rest of the break."""
    from urllib.parse import urljoin
    head: list[str] = []
    segs: list[tuple[float | None, list[str]]] = []
    pending: list[str] = []
    pdt: float | None = None
    cur_map = ""
    seq0 = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                seq0 = int(line.split(":", 1)[1])
            except ValueError:
                seq0 = 0
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            try:
                pdt = _epoch(line.split(":", 1)[1])
            except ValueError:
                pdt = None
            pending.append(line)
        elif line.startswith("#EXT-X-MAP"):
            cur_map = line
            pending.append(line)
        elif line.startswith("#EXT-X-DATERANGE"):
            continue
        elif line.startswith("#"):
            if segs or pending or line.startswith(("#EXTINF", "#EXT-X-DISCONTINUITY", "#EXT-X-BYTERANGE")):
                pending.append(line)
            else:
                head.append(line)
        else:
            segs.append((pdt, pending + [urljoin(base, line)], cur_map))     # type: ignore[arg-type]
            pending = []
    n_before = len([b for b in segs if b[0] is not None and b[0] < cut - 0.05])
    before, after = segs[:n_before], segs[n_before:]

    def build(items, seq, finished):
        out = [h for h in head if not h.startswith("#EXT-X-MEDIA-SEQUENCE")] + ["#EXT-X-MEDIA-SEQUENCE:%d" % seq]
        for k, (_, lines, mp) in enumerate(items):
            if k == 0 and mp and not any(ln.startswith("#EXT-X-MAP") for ln in lines):
                out.append(mp)                                     # the start-up data applies until a new one is named
            out += lines
        if finished:
            out.append("#EXT-X-ENDLIST")
        return "\n".join(out) + "\n"
    return build(before, seq0, bool(after)), build(after, seq0 + len(before), False)


