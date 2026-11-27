"""Playing a running YouTube live stream from any point since it began.

YouTube keeps every piece of a live stream (when the streamer allows rewinding) and hands them out by number: piece N of the
picture and piece N of the sound are each a tiny complete MP4 file, about two seconds long, and a reply also says how many
pieces exist so far. This is the numbering the website itself uses. The playlist Marquee plays normally only lists the newest
couple of hours, so this is the way to reach the true beginning.

Nothing is recorded: pieces are fetched as they are played, so any point opens at once."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request


def split_init(data: bytes) -> tuple[bytes, bytes]:
    """A piece = (the header that describes the tracks, the moving picture or sound itself). Pieces carry their own header;
    only one copy is needed at the start of what is played."""
    i = 0
    while i + 8 <= len(data):
        size = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8]
        if kind in (b"moof", b"styp", b"sidx"):
            return data[:i], data[i:]
        if size < 8:
            break
        i += size
    return b"", data


def pick_formats(info: dict) -> tuple[dict | None, dict | None]:
    """The picture and sound formats to play from: H.264 in MP4 up to 1080p, and AAC in M4A (what every player copes with)."""
    video, audio = [], []
    for f in info.get("formats", []):
        if not f.get("is_from_start") or not f.get("url"):
            continue
        if f.get("vcodec", "none") not in ("none", None) and str(f.get("vcodec")).startswith("avc1") and f.get("ext") == "mp4":
            if (f.get("height") or 0) <= 1080:
                video.append(f)
        elif f.get("vcodec") in ("none", None) and str(f.get("acodec", "")).startswith("mp4a.40.2") and f.get("ext") == "m4a":
            audio.append(f)
    video.sort(key=lambda f: (f.get("height") or 0, f.get("fps") or 0, f.get("tbr") or 0))
    return (video[-1] if video else None), (audio[-1] if audio else None)


def resolve(exe: str, video_id: str, extra: list[str] | None = None) -> tuple[str, str, dict]:
    """(picture address, sound address, headers) for the numbered pieces, from yt-dlp; raises when there are none."""
    cmd = [exe, "-J", "--no-warnings", "--live-from-start", "--force-ipv4", *(extra or []), f"https://www.youtube.com/watch?v={video_id}"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError((p.stderr.strip().splitlines() or ["yt-dlp failed"])[-1])
    v, a = pick_formats(json.loads(p.stdout))
    if not v or not a:
        raise RuntimeError("no H.264/AAC numbered pieces for this stream")
    headers = dict(v.get("http_headers") or {})
    return v["url"], a["url"], headers


class DashSource:
    def __init__(self, video_url: str, audio_url: str, headers: dict | None = None, refresh=None, patience: float = 40.0):
        self.patience = patience                      # seconds to wait for a piece that does not exist yet before giving up
        self.url = {"v": video_url, "a": audio_url}
        self.headers = dict(headers or {})
        self.refresh = refresh                        # () -> (video url, audio url, headers), for when the addresses expire
        self._lock = __import__("threading").Lock()
        self.head = 0                                 # the newest piece number YouTube has
        self.head_ms = 0                              # how far into the broadcast that is
        self.seen = 0.0

    def _get(self, kind: str, sq: int, timeout: float = 20.0):
        sep = "&" if "?" in self.url[kind] else "?"
        req = urllib.request.Request(f"{self.url[kind]}{sep}sq={sq}", headers=self.headers)
        return urllib.request.urlopen(req, timeout=timeout)

    def _renew(self) -> None:
        if not self.refresh:
            return
        with self._lock:
            v, a, h = self.refresh()
            self.url = {"v": v, "a": a}
            self.headers = dict(h or {})

    def fetch(self, kind: str, sq: int) -> bytes:
        for attempt in (0, 1):
            try:
                with self._get(kind, sq) as r:
                    self._note(r.headers)
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 0 and self.refresh:
                    print(f"[history] the piece address was refused (HTTP {e.code}); asking for a new one", file=sys.stderr, flush=True)
                    self._renew()
                    continue
                raise
        raise RuntimeError("unreachable")

    def _note(self, headers) -> None:
        try:
            seq = int(headers.get("X-Head-Seqnum") or 0)
            ms = int(headers.get("X-Head-Time-Millis") or 0)
        except ValueError:
            return
        if seq > 0 and ms > 0:
            self.head, self.head_ms, self.seen = seq, ms, time.monotonic()

    def look(self) -> tuple[int, float, float]:
        """(newest piece number, seconds per piece, seconds that can be played) now."""
        for attempt in (0, 1):
            try:
                with self._get("a", 0) as r:
                    self._note(r.headers)
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 0 and self.refresh:
                    self._renew()
                    continue
                raise
        if self.head < 3:
            raise RuntimeError("YouTube did not say how long this stream is")
        seg = self.head_ms / 1000.0 / self.head
        return self.head, seg, max(0.0, (self.head - 2) * seg)

    def pieces(self, kind: str, start: int, alive=lambda: True):
        """Bytes of the pieces from number `start` on: the header once, then each piece's moving part, following the stream as it grows."""
        sq = start
        first = True
        waited = 0.0
        while alive():
            if sq > self.head - 2 or time.monotonic() - self.seen > 6:
                try:                                  # not there yet: ask what exists now
                    with self._get("a", max(0, self.head)) as r:
                        self._note(r.headers)
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403) and self.refresh:
                        self._renew()
                except OSError:
                    pass
                if sq > self.head - 2:
                    time.sleep(0.5)
                    waited += 0.5
                    if waited > self.patience:
                        return
                    continue
            waited = 0.0
            try:
                data = self.fetch(kind, sq)
            except urllib.error.HTTPError as e:
                if e.code in (404, 503):              # a piece that is not ready yet
                    time.sleep(0.5)
                    waited += 0.5
                    if waited > self.patience:
                        return
                    continue
                raise
            init, body = split_init(data)
            yield (init + body) if first else body
            first = False
            sq += 1
