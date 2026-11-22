"""A tiny local web server that fetches a YouTube stream for the player.

YouTube's stream addresses answer 403 unless the request carries the same headers yt-dlp used to find them, and Qt's
player can't add headers. So the player opens http://127.0.0.1:PORT/TOKEN and this server fetches the real address
with the right headers, passing Range requests through so seeking works.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
from .gvfetch import open_stream
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PASS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified")


CHUNK = 10 * 1024 * 1024       # YouTube's own players (and yt-dlp) fetch in pieces this size; whole-file requests get a 403


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):          # a player that hangs up mid-reply (a seek, closing) is not an error
        import sys
        if not isinstance(sys.exc_info()[1], (ConnectionError, BrokenPipeError, TimeoutError)):
            super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _open(self, url, headers, a, b):
        h = dict(headers)
        h["Range"] = f"bytes={a}-{b}"
        return open_stream(url, h, 600)          # a throttled chunk can take a while; the player gives up long before this

    def _serve_file(self, head: bool, path: str, size: int, proc):
        """A file yt-dlp is still writing: serve what is there, and wait for the rest when the player asks ahead."""
        import os
        import time
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        asked = bool(m)
        start = int(m.group(1)) if m and m.group(1) else 0
        total = size
        if not total:
            while proc is not None and proc.poll() is None:
                time.sleep(0.1)
            total = os.path.getsize(path)
        end = min(int(m.group(2)) if m and m.group(2) else total - 1, total - 1)
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{total}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206 if asked else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        if asked:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        if head:
            return
        pos = start
        try:
            with open(path, "rb") as f:
                while pos <= end:
                    have = os.path.getsize(path)
                    if have <= pos:
                        if proc is not None and proc.poll() is not None and os.path.getsize(path) <= pos:
                            break                                # yt-dlp is done (or failed) and there is no more
                        time.sleep(0.05)
                        continue
                    f.seek(pos)
                    data = f.read(min(64 * 1024, have - pos, end - pos + 1))
                    self.wfile.write(data)
                    pos += len(data)
        except (OSError, ValueError):                            # the player closed the connection
            pass

    def _serve_hls(self, head: bool, url: str, part: str, state):
        """The channel ("main") or the commercial break ("ad") of a live playlist, read fresh each time the player asks (see core.twitch.AdState)."""
        import sys
        import time
        from . import twitch
        deadline = time.time() + 20
        while True:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": twitch.UA})
                with urllib.request.urlopen(req, timeout=10) as r:
                    text = r.read(600_000).decode("utf-8", "replace")
                body, n, beyond = state.ad_part(text, url) if part == "ad" else state.main(text, url)
            except Exception:
                self.send_error(502)
                return
            w = state.ad
            if part == "main" and w and getattr(state, "logged", None) != w:
                state.logged = w                                    # type: ignore[attr-defined]
                print(f"marquee: twitch: commercial break listed: {time.strftime('%H:%M:%S', time.gmtime(w[0]))}Z for {w[1] - w[0]:.0f}s", file=sys.stderr, flush=True)
            # the channel's pieces appear a little after a break's own time is up: wait for them rather than hand the player an empty list
            if n or beyond or time.time() > deadline or part == "ad":
                break
            print(f"marquee: twitch: waiting for the {part}: no pieces yet", file=sys.stderr, flush=True)
            time.sleep(1)
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not head:
            try:
                self.wfile.write(data)
            except OSError:
                pass

    @staticmethod
    def _live_edge(lines: list, keep: int = 12, hold: int = 2) -> list:
        """A YouTube live playlist holds the whole DVR window (1600+ pieces, about 2 MB). Keep only the newest few so the player
        starts at the live point, and move the media sequence number along with what was dropped."""
        if any(l.startswith("#EXT-X-ENDLIST") for l in lines):
            return lines
        first = next((i for i, l in enumerate(lines) if l.startswith("#EXTINF")), None)
        if first is None:
            return lines
        head, body = lines[:first], lines[first:]
        groups, cur = [], []
        for l in body:
            cur.append(l)
            if l.strip() and not l.startswith("#"):
                groups.append(cur)
                cur = []
        if len(groups) <= keep + hold:
            return lines
        # the newest pieces are listed before YouTube will serve them (403), so stay a couple of pieces behind
        groups = groups[:len(groups) - hold] if hold else groups
        dropped = len(groups) - keep
        head = [re.sub(r"(#EXT-X-MEDIA-SEQUENCE:)(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + dropped), l) for l in head]
        kept = [l for g in groups[dropped:] for l in g]
        return head + kept

    def _maybe_refresh_live(self, token: str):
        st = getattr(self.server, "live_refresh", {}).get(token)
        if not st or st["busy"] or time.monotonic() - st["last"] < st["every"]:
            return
        st["busy"] = True
        st["last"] = time.monotonic()                          # counted from when a refresh starts, so a slow one doesn't push the next later
        server = self.server

        def work():
            import sys
            try:
                url, headers = st["fn"]()
                if url:
                    server.entries[token] = ("live", url, dict(headers or {}))      # type: ignore[attr-defined]
                    print("[youtube] live stream: swapped in a fresh address", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[youtube] live stream: could not refresh the address: {e}", file=sys.stderr, flush=True)
            finally:
                st["busy"] = False
        threading.Thread(target=work, daemon=True).start()

    def _serve_live(self, head: bool, token: str, url: str, headers: dict):
        """A live HLS stream: YouTube turns the player's own requests away (wrong fingerprint and headers), so the playlist and
        every piece it names come through here. Addresses inside a playlist are rewritten to point back at this server."""
        from urllib.parse import parse_qs, quote, urljoin, urlsplit
        q = parse_qs(urlsplit(self.path).query)
        target = q["u"][0] if q.get("u") else url
        h = dict(headers)
        if self.headers.get("Range"):
            h["Range"] = self.headers["Range"]
        import time as _time
        m = re.search(r"/sq/(\d+)", target)
        r, err = None, None
        for attempt in range(8 if m else 1):                  # a brand-new piece can be refused for a few seconds
            try:
                r = open_stream(target, h, 20)
                if attempt:
                    import sys
                    print(f"[youtube] live stream: piece {m.group(1)} became available after {attempt} retries", file=sys.stderr, flush=True)
                break
            except urllib.error.HTTPError as e:
                err = e
                if e.code != 403:
                    break
                _time.sleep(1)
            except OSError:
                err = None
                break
        if r is None and err is None:
            self.send_error(502)
            return
        if err is not None and r is None:
            e = err
            import sys
            try:
                why = e.read(160)
            except Exception:
                why = b""
            print(f"[youtube] live stream: upstream refused {'piece ' + m.group(1) if m else 'the playlist'}: HTTP {e.code} {why!r}", file=sys.stderr, flush=True)
            self.send_response(e.code)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        port = self.server.server_address[1]                                   # type: ignore[attr-defined]

        def local(addr: str, default: str = "seg.ts") -> str:
            # ffmpeg only accepts pieces whose address ends in a known extension, so keep the real name (seg.ts, index.m3u8)
            full = urljoin(target, addr)
            name = urlsplit(full).path.rsplit("/", 1)[-1]
            if "." not in name:
                name = default
            return f"http://127.0.0.1:{port}/{token}/{quote(name, safe='.')}?u=" + quote(full, safe="")
        with r:
            first = r.read(64 * 1024)
            if first.startswith(b"#EXTM3U"):
                text = (first + r.read(40_000_000)).decode("utf-8", "replace")
                out = []
                was_variant = False
                for line in text.splitlines():
                    if line.startswith("#"):
                        line = re.sub(r'URI="([^"]+)"', lambda m: 'URI="' + local(m.group(1), "init.mp4") + '"', line)
                        was_variant = line.startswith("#EXT-X-STREAM-INF")
                    elif line.strip():
                        line = local(line.strip(), "index.m3u8" if was_variant else "seg.ts")
                        was_variant = False
                    out.append(line)
                out = self._live_edge(out)
                nums = [int(x) for x in re.findall(r"%2Fsq%2F(\d+)%2F", "\n".join(out))]
                if nums and getattr(self.server, "_live_seen", None) != (nums[0], nums[-1]):
                    self.server._live_seen = (nums[0], nums[-1])              # type: ignore[attr-defined]
                    import sys
                    print(f"[youtube] live stream: playlist lists pieces {nums[0]} to {nums[-1]} ({len(nums)})", file=sys.stderr, flush=True)
                data = ("\n".join(out) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                if not head:
                    try:
                        self.wfile.write(data)
                    except OSError:
                        pass
                return
            # a piece is small (a couple of seconds of video): take all of it, so the player is told exactly how long it is.
            # Without a length ffmpeg treats the end of every piece as a broken connection and gives up on it.
            body = first
            try:
                while len(body) < 40_000_000:
                    c = r.read(256 * 1024)
                    if not c:
                        break
                    body += c
            except (OSError, ValueError):
                pass
            self.send_response(200)
            self.send_header("Content-Type", r.headers.get("Content-Type") or "video/mp2t")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

    def _serve(self, head: bool):
        entry = self.server.entries.get(self.path.split("?")[0].strip("/").split("/")[0])     # type: ignore[attr-defined]
        if not entry:
            self.send_error(404)
            return
        if entry[0] == "file":
            self._serve_file(head, entry[1], entry[2], entry[3])
            return
        if entry[0] == "hls":
            self._serve_hls(head, entry[1], entry[2], entry[3])
            return
        if entry[0] == "live":
            self._maybe_refresh_live(self.path.split("?")[0].strip("/").split("/")[0])
            entry = self.server.entries.get(self.path.split("?")[0].strip("/").split("/")[0], entry)   # type: ignore[attr-defined]
            self._serve_live(head, self.path.split("?")[0].strip("/").split("/")[0], entry[1], entry[2])
            return
        _, url, headers = entry
        chunk = getattr(self.server, "chunk", CHUNK)
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        asked = bool(m)
        start = int(m.group(1)) if m and m.group(1) else 0
        end = int(m.group(2)) if m and m.group(2) else None
        try:
            r = self._open(url, headers, start, start + chunk - 1 if end is None else min(end, start + chunk - 1))
        except urllib.error.HTTPError as e:
            import sys
            print(f"[youtube] stream server: upstream answered HTTP {e.code} for bytes={start}-{start + chunk - 1} "
                  f"(player asked: {self.headers.get('Range')!r}); {e.read(160)!r}", file=sys.stderr, flush=True)
            self.send_response(e.code)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        except OSError:
            self.send_error(502)
            return
        cr = r.headers.get("Content-Range", "")
        total = int(cr.rsplit("/", 1)[1]) if "/" in cr and cr.rsplit("/", 1)[1].isdigit() else None
        if total is None:                                        # the server ignored the range: pass it through whole
            with r:
                self.send_response(200)
                for k in ("Content-Type", "Content-Length"):
                    if r.headers.get(k):
                        self.send_header(k, r.headers[k])
                self.end_headers()
                if not head:
                    try:
                        while True:
                            c = r.read(64 * 1024)
                            if not c:
                                break
                            self.wfile.write(c)
                    except (OSError, ValueError):
                        pass
            return
        if end is None or end >= total:
            end = total - 1
        import sys
        print(f"[youtube] stream server: player asked {self.headers.get('Range')!r}; upstream {r.status} {cr!r}; "
              f"replying {start}-{end}/{total}{' (HEAD)' if head else ''}", file=sys.stderr, flush=True)
        self.send_response(206 if asked else 200)
        self.send_header("Content-Type", r.headers.get("Content-Type", "video/mp4"))
        self.send_header("Accept-Ranges", "bytes")
        if asked:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        if head:
            r.close()
            return
        pos = start
        try:
            while True:
                with r:
                    while True:
                        c = r.read(64 * 1024)
                        if not c:
                            break
                        self.wfile.write(c)
                        pos += len(c)
                if pos > end:
                    break
                r = self._open(url, headers, pos, min(end, pos + chunk - 1))
        except (OSError, ValueError):                            # the player closed the connection (a seek, or stop)
            pass
        except Exception as e:                                   # the connection to YouTube broke: say so
            print(f"[youtube] stream server: upstream failed at byte {pos} of {end}: {e!r}", file=sys.stderr, flush=True)
        if pos <= end:
            print(f"[youtube] stream server: reply stopped at byte {pos} of {end}", file=sys.stderr, flush=True)

    def do_GET(self):
        self._serve(False)

    def do_HEAD(self):
        self._serve(True)


class StreamProxy:
    def __init__(self):
        self._server: ThreadingHTTPServer | None = None
        self._n = 0

    def _start(self) -> ThreadingHTTPServer:
        if not self._server:
            s = _Server(("127.0.0.1", 0), _Handler)
            s.daemon_threads = True
            s.entries = {}                                       # type: ignore[attr-defined]
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self._server = s
        return self._server

    def add(self, url: str, headers: dict | None = None) -> str:
        """The local address that plays `url`."""
        s = self._start()
        self._n += 1
        token = f"s{self._n}"
        s.entries[token] = ("url", url, dict(headers or {}))     # type: ignore[attr-defined]
        return f"http://127.0.0.1:{s.server_address[1]}/{token}"

    def add_live(self, url: str, headers: dict | None = None, refresh=None, every: float = 10.0) -> str:
        """The local address of a live HLS playlist that is fetched with `headers` (its pieces too).

        YouTube only honours a live address for about a dozen pieces past the moment it was made, so `refresh()` (returning
        (url, headers)) is called in the background every `every` seconds and the playlist is simply re-read from the new address."""
        s = self._start()
        self._n += 1
        token = f"s{self._n}"
        s.entries[token] = ("live", url, dict(headers or {}))    # type: ignore[attr-defined]
        if refresh:
            if not hasattr(s, "live_refresh"):
                s.live_refresh = {}                              # type: ignore[attr-defined]
            s.live_refresh[token] = {"fn": refresh, "every": every, "last": time.monotonic(), "busy": False}   # type: ignore[attr-defined]
        return f"http://127.0.0.1:{s.server_address[1]}/{token}"

    def add_file(self, path: str, size: int = 0, proc=None) -> str:
        """The local address that plays a file that may still be growing (size = its final size, if known)."""
        s = self._start()
        self._n += 1
        token = f"s{self._n}"
        s.entries[token] = ("file", path, size, proc)            # type: ignore[attr-defined]
        return f"http://127.0.0.1:{s.server_address[1]}/{token}"

    def add_hls(self, url: str, part: str, state) -> str:
        """The local address of the channel ("main") or the current commercial break ("ad") of a live playlist; `state` is a core.twitch.AdState."""
        s = self._start()
        self._n += 1
        token = f"s{self._n}"
        s.entries[token] = ("hls", url, part, state)               # type: ignore[attr-defined]
        return f"http://127.0.0.1:{s.server_address[1]}/{token}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
