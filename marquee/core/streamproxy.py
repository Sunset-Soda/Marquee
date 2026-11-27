"""A tiny local web server that fetches a YouTube stream for the player.

YouTube's stream addresses answer 403 unless the request carries the same headers yt-dlp used to find them, and Qt's
player can't add headers. So the player opens http://127.0.0.1:PORT/TOKEN and this server fetches the real address
with the right headers, passing Range requests through so seeking works.
"""
from __future__ import annotations

import os
import re
import threading
import time
import urllib.error
import urllib.request
from .gvfetch import open_stream
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PASS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified")


CHUNK = 10 * 1024 * 1024       # YouTube's own players (and yt-dlp) fetch in pieces this size; whole-file requests get a 403


class Dvr:
    """A live stream that is kept on disk while it plays, so it can be paused, rewound and brought back to the live edge.

    One ffmpeg copies the stream (no re-encoding) into short pieces of about two seconds in a folder. Each viewer is another
    ffmpeg that joins those pieces from any point and hands the player one plain continuous stream whose clock starts at zero.
    Pausing only stops the viewer; the recording carries on, so resuming carries on from the same picture. Pieces older than
    `window` seconds are deleted, and the whole folder goes when nobody has been watching for a while."""

    PIECE = 2          # seconds per piece
    IDLE = 40          # seconds with no viewer before the recording stops

    def __init__(self, source: str, window: float | None = None, folder=None):
        import os
        import tempfile
        from pathlib import Path
        self.source = source
        mins = float(os.environ.get("MARQUEE_DVR_MINUTES") or 60)
        self.window = window if window is not None else mins * 60
        if folder is None:
            from .library import cache_dir
            base = cache_dir() / "live"
            base.mkdir(parents=True, exist_ok=True)
            self._sweep(base)
            folder = tempfile.mkdtemp(prefix="dvr-", dir=base)
        self.dir = Path(folder)
        self.list = self.dir / "list.csv"
        self.proc = None
        self.lock = threading.Lock()
        self.viewers = 0
        self.idle_since = time.monotonic()
        self.first = 0                       # pieces before this number have been deleted
        self.viewer_start = 0.0              # where in the recording the newest viewer began
        self.stopped = False

    @staticmethod
    def _sweep(base) -> None:
        """Folders left by an earlier run that was closed without cleaning up."""
        import shutil
        for d in base.glob("dvr-*"):
            try:
                if time.time() - d.stat().st_mtime > 3600:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass

    def pieces(self) -> list:
        """[(name, start, end)] of the pieces that are complete and still on disk, oldest first."""
        out = []
        try:
            with open(self.list, encoding="utf-8") as f:
                for line in f:
                    a = line.strip().split(",")
                    if len(a) >= 3:
                        out.append((a[0], float(a[1]), float(a[2])))
        except (OSError, ValueError):
            pass
        return out[self.first:]

    def status(self) -> dict:
        """{"oldest", "live", "viewerStart", "alive"} in seconds from the start of the recording."""
        ps = self.pieces()
        alive = bool(self.proc and self.proc.poll() is None) and not self.stopped
        return {"oldest": ps[0][1] if ps else 0.0, "live": ps[-1][2] if ps else 0.0, "viewerStart": self.viewer_start, "alive": alive}

    def start(self) -> None:
        import subprocess
        import sys
        with self.lock:
            if self.proc or self.stopped:
                return
            exe = ffmpeg_path()
            if not exe:
                return
            cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", self.source, "-map", "0:v:0", "-map", "0:a:0?",
                   "-c", "copy", "-f", "segment", "-segment_time", str(self.PIECE), "-segment_format", "mpegts",
                   "-segment_list", str(self.list), "-segment_list_type", "csv", "-segment_list_flags", "live",
                   str(self.dir / "p%06d.ts")]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            except OSError as e:
                print(f"[dvr] could not start ffmpeg: {e}", file=sys.stderr, flush=True)
                return
            proc = self.proc
        threading.Thread(target=lambda: [print(f"[dvr] ffmpeg: {ln.decode('utf-8', 'replace').rstrip()}", file=sys.stderr, flush=True)
                                         for ln in proc.stderr], daemon=True).start()
        threading.Thread(target=self._keep, daemon=True).start()
        print("[dvr] recording the live stream", file=sys.stderr, flush=True)

    def _keep(self) -> None:
        """Delete pieces older than the window, and stop when nobody has watched for a while."""
        while not self.stopped:
            time.sleep(2)
            if self.viewers == 0 and time.monotonic() - self.idle_since > self.IDLE:
                self.stop()
                return
            ps = self.pieces()
            if ps:
                cut = ps[-1][2] - self.window
                n = 0
                for name, start, end in ps:
                    if end < cut:
                        try:
                            (self.dir / name).unlink()
                        except OSError:
                            pass
                        n += 1
                    else:
                        break
                self.first += n

    def stop(self) -> None:
        import shutil
        import sys
        if self.stopped:
            return
        self.stopped = True
        p = self.proc
        if p:
            try:
                p.kill()
            except OSError:
                pass
        shutil.rmtree(self.dir, ignore_errors=True)
        print("[dvr] stopped and cleared the recording", file=sys.stderr, flush=True)

    def begin(self, back: float) -> int | None:
        """The number of the piece a viewer should start from to be `back` seconds behind live, waiting for the first piece to exist."""
        self.start()
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not self.stopped:
            ps = self.pieces()
            if ps:
                live = ps[-1][2]
                want = max(ps[0][1], live - max(0.0, back))
                pick = len(ps) - 1 if back <= 0 else next((i for i, pc in enumerate(ps) if pc[2] > want), len(ps) - 1)
                self.viewer_start = ps[pick][1]
                return self.first + pick
            time.sleep(0.25)
        return None

    def piece_after(self, number: int) -> tuple | None:
        """The piece with this number (counted from the start of the recording), once it is complete."""
        if number < self.first:
            number = self.first                  # it has been deleted: carry on from the oldest left
        with open(self.list, encoding="utf-8") as f:
            rows = [ln for ln in f if ln.count(",") >= 2]
        if number < len(rows):
            a = rows[number].strip().split(",")
            return number, a[0]
        return None


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

    def _serve_vod(self, head: bool, url: str, headers: dict):
        """A saved Twitch broadcast's playlist, cut to start `from` seconds in (see core.twitch.cut_vod); its pieces are fetched from Twitch directly."""
        from urllib.parse import parse_qs, urlsplit
        from . import twitch
        try:
            start = float(parse_qs(urlsplit(self.path).query).get("from", ["0"])[0])
        except ValueError:
            start = 0.0
        try:
            h = {"User-Agent": twitch.UA, **{k: v for k, v in (headers or {}).items() if k.lower() != "range"}}
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15) as r:
                text = r.read(8_000_000).decode("utf-8", "replace")
            body, total = twitch.cut_vod(text, url, start)
        except Exception:
            self.send_error(502)
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Total-Seconds", f"{total:.1f}")
        self.end_headers()
        if not head:
            try:
                self.wfile.write(data)
            except OSError:
                pass

    def _serve_hls(self, head: bool, url: str, part: str, state):
        """The channel ("main") or the commercial break ("ad") of a live playlist, read fresh each time the player asks (see core.twitch.AdState)."""
        import sys
        import time
        from . import twitch
        deadline = time.time() + 20
        rec = getattr(state, "rec", None)
        while True:
            try:
                if rec is not None:
                    text, url = rec.text(), rec.base                # the recording stands in for the live playlist
                else:
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

    @staticmethod
    def _from_offset(lines: list, start: float, hold: int = 2) -> tuple:
        """The same playlist, but starting `start` seconds after its first piece (every piece YouTube still lists is kept, minus the
        newest `hold`, which it is not serving yet). Returns (lines, seconds the playlist covers). Lets a viewer begin anywhere in the past."""
        first = next((i for i, l in enumerate(lines) if l.startswith("#EXTINF")), None)
        if first is None:
            return lines, 0.0
        head, body = lines[:first], lines[first:]
        groups, cur = [], []
        for l in body:
            cur.append(l)
            if l.strip() and not l.startswith("#"):
                groups.append(cur)
                cur = []
        if hold and len(groups) > hold:
            groups = groups[:len(groups) - hold]

        def length(g):
            for l in g:
                if l.startswith("#EXTINF:"):
                    try:
                        return float(l[8:].split(",")[0])
                    except ValueError:
                        pass
            return 0.0
        total = sum(length(g) for g in groups)
        t, dropped = 0.0, 0
        while dropped < len(groups) - 1 and t + length(groups[dropped]) <= start:
            t += length(groups[dropped])
            dropped += 1
        head = [re.sub(r"(#EXT-X-MEDIA-SEQUENCE:)(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + dropped), l) for l in head]
        return head + [l for g in groups[dropped:] for l in g], total

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
                total_s = 0.0
                if q.get("from") and not q.get("u"):                 # a viewer of the past: this playlist begins that many seconds in
                    try:
                        out, total_s = self._from_offset(out, float(q["from"][0]))
                    except ValueError:
                        out = self._live_edge(out)
                else:
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
                if total_s:
                    self.send_header("X-Total-Seconds", f"{total_s:.1f}")
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

    def _watch_client(self, proc) -> None:
        """Stop `proc` as soon as the player hangs up. Without this a pass-through that is waiting for the stream to say something
        only finds out the player has gone when it next has data to send, which can be never (a stalled channel)."""
        import select
        import socket
        conn = self.connection

        def watch():
            while proc.poll() is None:
                try:
                    ready, _, _ = select.select([conn], [], [], 1.0)
                    if ready and conn.recv(1, socket.MSG_PEEK) == b"":
                        proc.kill()
                        return
                    if ready:
                        time.sleep(0.5)                       # the player sent something more (not a hang-up): keep going
                except (OSError, ValueError):
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    return
        threading.Thread(target=watch, daemon=True).start()

    def _serve_retimed(self, head: bool, source: str):
        """A live stream passed through ffmpeg (copying, not re-encoding) so the player gets one plain continuous stream whose
        clock starts at zero, instead of an HLS playlist whose pieces start hours in. Some Qt/FFmpeg versions never start the
        clock on those (the picture stays black, nothing plays)."""
        import os
        import subprocess
        import sys
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Accept-Ranges", "none")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")         # so a stream that ends (a commercial break, a channel going off air) ends cleanly
        self.end_headers()
        self.close_connection = True
        if head:
            return
        exe = ffmpeg_path()
        if not exe:
            return
        first = []
        m = re.match(r"http://127\.0\.0\.1:\d+/([sv]\d+)(?:/index\.m3u8)?(?:\?.*)?$", source)
        ent = self.server.entries.get(m.group(1)) if m else None   # type: ignore[attr-defined]
        if ent and (ent[0] in ("hls", "vod") or (ent[0] == "live" and "from=" in source)):
            first = ["-live_start_index", "0", "-analyzeduration", "1500000", "-probesize", "1500000"]                    # start at the first piece listed (a rewound spot), not three from the end
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", *first, "-i", source, "-map", "0:v:0", "-map", "0:a:0?",
               "-c", "copy", "-flush_packets", "1", "-f", "mpegts", "pipe:1"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except OSError as e:
            print(f"[live] could not start ffmpeg: {e}", file=sys.stderr, flush=True)
            return
        self._watch_client(proc)
        threading.Thread(target=lambda: [print(f"[live] ffmpeg: {ln.decode('utf-8', 'replace').rstrip()}", file=sys.stderr, flush=True)
                                         for ln in proc.stderr], daemon=True).start()
        total = 0
        try:
            fd = proc.stdout.fileno()
            while True:
                data = os.read(fd, 64 * 1024)
                if not data:
                    self.wfile.write(b"0\r\n\r\n")                # the closing chunk: the stream really did end
                    self.wfile.flush()
                    break
                if not total:
                    print("[live] ffmpeg is passing the stream through", file=sys.stderr, flush=True)
                total += len(data)
                self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
                self.wfile.flush()
        except (OSError, ValueError):                            # the player closed the connection (stop, or a new channel)
            pass
        finally:
            try:
                proc.kill()
            except OSError:
                pass
            print(f"[live] ffmpeg pass-through ended after {total // 1024} KB", file=sys.stderr, flush=True)

    def _serve_dvr(self, head: bool, token: str):
        """One viewer of a recorded live stream: joins the pieces `back` seconds behind live and passes them on as one stream."""
        import os
        import subprocess
        import sys
        from urllib.parse import parse_qs, urlsplit
        dvr = self.server.dvrs.get(token)                                   # type: ignore[attr-defined]
        if dvr is None or dvr.stopped:
            self.send_error(404)
            return
        try:
            back = float(parse_qs(urlsplit(self.path).query).get("back", ["0"])[0])
        except ValueError:
            back = 0.0
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Accept-Ranges", "none")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.close_connection = True
        if head:
            return
        exe = ffmpeg_path()
        if not exe:
            return
        dvr.viewers += 1
        proc = None
        total = 0
        try:
            number = dvr.begin(back)
            if number is None:
                return
            # a short look at the first pieces is enough to find the picture and sound; the default waits for five seconds of them,
            # which at the live edge means five seconds with nothing playing
            cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-analyzeduration", "1500000", "-probesize", "1500000",
                   "-f", "mpegts", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?",
                   "-c", "copy", "-flush_packets", "1", "-f", "mpegts", "pipe:1"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
            self._watch_client(proc)
            threading.Thread(target=lambda: [print(f"[dvr] viewer ffmpeg: {ln.decode('utf-8', 'replace').rstrip()}", file=sys.stderr, flush=True)
                                             for ln in proc.stderr], daemon=True).start()
            pr = proc

            def feed():
                n = number
                waited = 0.0
                try:
                    while not dvr.stopped:
                        got = dvr.piece_after(n)
                        if got is None:
                            if not (dvr.proc and dvr.proc.poll() is None) and waited > 4:
                                break                                      # the recording itself has ended
                            time.sleep(0.2)
                            waited += 0.2
                            if waited > 45:
                                break
                            continue
                        waited = 0.0
                        n = got[0] + 1
                        try:
                            with open(dvr.dir / got[1], "rb") as f:
                                pr.stdin.write(f.read())
                                pr.stdin.flush()
                        except FileNotFoundError:
                            continue
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        pr.stdin.close()
                    except OSError:
                        pass
            threading.Thread(target=feed, daemon=True).start()
            st = dvr.status()
            print(f"[dvr] a viewer joined {min(back, st['live'] - st['oldest']):.0f}s behind live (the recording holds {st['live'] - st['oldest']:.0f}s so far)", file=sys.stderr, flush=True)
            fd = proc.stdout.fileno()
            while True:
                data = os.read(fd, 64 * 1024)
                if not data:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    break
                total += len(data)
                self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
                self.wfile.flush()
        except (OSError, ValueError):                                     # the player closed the connection (a seek, stop, or leaving)
            pass
        finally:
            dvr.viewers -= 1
            dvr.idle_since = time.monotonic()
            if proc:
                try:
                    proc.kill()
                except OSError:
                    pass
            print(f"[dvr] a viewer left after {total // 1024} KB", file=sys.stderr, flush=True)

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
        if entry[0] == "retime":
            self._serve_retimed(head, entry[1])
            return
        if entry[0] == "vod":
            self._serve_vod(head, entry[1], entry[2])
            return
        if entry[0] == "rec":
            parts = self.path.split("?")[0].strip("/").split("/")
            f = entry[1].file(parts[1]) if len(parts) > 1 else None
            if not f:
                self.send_error(404)
                return
            try:
                size = os.path.getsize(f)
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t" if f.endswith(".ts") else "video/mp4")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                if not head:
                    with open(f, "rb") as fh:
                        self.wfile.write(fh.read())
            except OSError:
                pass
            return
        if entry[0] == "dvr":
            self._serve_dvr(head, self.path.split("?")[0].strip("/").split("/")[0])
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


def ffmpeg_path() -> str | None:
    import shutil
    return shutil.which("ffmpeg")


def retime_enabled() -> bool:
    """Live streams go through ffmpeg first, when ffmpeg is installed. Set MARQUEE_LIVE_VIA_FFMPEG=0 to hand them to the player directly."""
    import os
    return os.environ.get("MARQUEE_LIVE_VIA_FFMPEG") != "0" and bool(ffmpeg_path())


class StreamProxy:
    def __init__(self):
        self._server: ThreadingHTTPServer | None = None
        self._n = 0

    def _start(self) -> ThreadingHTTPServer:
        if not self._server:
            s = _Server(("127.0.0.1", 0), _Handler)
            s.daemon_threads = True
            s.entries = {}                                       # type: ignore[attr-defined]
            s.dvrs = {}                                          # type: ignore[attr-defined]
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self._server = s
            import atexit
            atexit.register(self._stop_recordings)               # closing Marquee must not leave a recorder running
        return self._server

    def _stop_recordings(self) -> None:
        if self._server:
            for d in list(getattr(self._server, "dvrs", {}).values()):
                d.stop()
            for e in list(self._server.entries.values()):         # type: ignore[attr-defined]
                if e[0] == "rec":
                    e[1].stop()

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

    def retimed(self, local_url: str) -> str:
        """`local_url` (one of this proxy's own addresses) passed through ffmpeg so it starts at time zero; unchanged when that is switched off."""
        if not retime_enabled():
            return local_url
        s = self._start()
        self._n += 1
        token = f"r{self._n}"
        s.entries[token] = ("retime", local_url)                  # type: ignore[attr-defined]
        return f"http://127.0.0.1:{s.server_address[1]}/{token}/live.ts"

    def dvr(self, local_url: str) -> str:
        """`local_url` (one of this proxy's own addresses) recorded as it plays, so it can be paused, rewound and sent back to live.
        Add ?back=SECONDS to start that far behind live. Falls back to plain pass-through when ffmpeg is missing or switched off."""
        if not retime_enabled() or os.environ.get("MARQUEE_DVR") == "0":
            return self.retimed(local_url)
        s = self._start()
        self._n += 1
        token = f"d{self._n}"
        s.entries[token] = ("dvr",)                                # type: ignore[attr-defined]
        s.dvrs[token] = Dvr(local_url)                             # type: ignore[attr-defined]
        return f"http://127.0.0.1:{s.server_address[1]}/{token}/live.ts"

    def dvr_status(self, url: str) -> dict | None:
        """The recording's state for an address made by dvr(), or None for anything else."""
        if not self._server:
            return None
        m = re.search(r"/(d\d+)/live\.ts", url or "")
        d = self._server.dvrs.get(m.group(1)) if m else None      # type: ignore[attr-defined]
        return d.status() if d else None

    def add_vod(self, url: str, headers: dict | None = None) -> str:
        """The local address of a saved broadcast's playlist; add ?from=SECONDS to start that far in."""
        s = self._start()
        self._n += 1
        token = f"v{self._n}"
        s.entries[token] = ("vod", url, dict(headers or {}))        # type: ignore[attr-defined]
        return f"http://127.0.0.1:{s.server_address[1]}/{token}/index.m3u8"

    def add_rec(self, rec) -> str:
        """Serve a core.twitch.Recorder's pieces; sets rec.base to their address and returns it."""
        s = self._start()
        self._n += 1
        token = f"t{self._n}"
        s.entries[token] = ("rec", rec)                            # type: ignore[attr-defined]
        rec.base = f"http://127.0.0.1:{s.server_address[1]}/{token}/"
        return rec.base

    def add_hls(self, url: str, part: str, state) -> str:
        """The local address of the channel ("main") or the current commercial break ("ad") of a live playlist; `state` is a core.twitch.AdState."""
        s = self._start()
        self._n += 1
        token = f"s{self._n}"
        s.entries[token] = ("hls", url, part, state)               # type: ignore[attr-defined]
        return f"http://127.0.0.1:{s.server_address[1]}/{token}"

    def stop(self) -> None:
        if self._server:
            self._stop_recordings()
            self._server.shutdown()
            self._server = None
