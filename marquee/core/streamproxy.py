"""A tiny local web server that fetches a YouTube stream for the player.

YouTube's stream addresses answer 403 unless the request carries the same headers yt-dlp used to find them, and Qt's
player can't add headers. So the player opens http://127.0.0.1:PORT/TOKEN and this server fetches the real address
with the right headers, passing Range requests through so seeking works.
"""
from __future__ import annotations

import re
import threading
import urllib.error
import urllib.request
from .gvfetch import open_stream
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PASS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified")


CHUNK = 10 * 1024 * 1024       # YouTube's own players (and yt-dlp) fetch in pieces this size; whole-file requests get a 403


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

    def _serve(self, head: bool):
        entry = self.server.entries.get(self.path.strip("/"))     # type: ignore[attr-defined]
        if not entry:
            self.send_error(404)
            return
        if entry[0] == "file":
            self._serve_file(head, entry[1], entry[2], entry[3])
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
            s = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
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

    def add_file(self, path: str, size: int = 0, proc=None) -> str:
        """The local address that plays a file that may still be growing (size = its final size, if known)."""
        s = self._start()
        self._n += 1
        token = f"s{self._n}"
        s.entries[token] = ("file", path, size, proc)            # type: ignore[attr-defined]
        return f"http://127.0.0.1:{s.server_address[1]}/{token}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
