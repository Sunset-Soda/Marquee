"""Fetching YouTube's stream addresses.

googlevideo.com turns away clients that don't look like a real browser, by their connection fingerprint as well as their
headers. yt-dlp gets in by using curl_cffi (which impersonates Chrome) when it is installed, so this does the same, and
falls back to plain urllib over IPv4 when it isn't.
"""
from __future__ import annotations

import functools
import http.client
import io
import urllib.error
import urllib.request


def _ipv4_opener() -> urllib.request.OpenerDirector:
    class H(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(functools.partial(http.client.HTTPConnection, source_address=("0.0.0.0", 0)), req)

    class S(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(functools.partial(http.client.HTTPSConnection, source_address=("0.0.0.0", 0)), req)
    return urllib.request.build_opener(H, S)


class _Curl:
    """Gives a curl_cffi streaming response the few urllib methods we use: status, headers, read, close."""

    def __init__(self, r):
        self._r = r
        self.status = r.status_code
        self.headers = r.headers
        self._it = r.iter_content(chunk_size=64 * 1024)
        self._buf = b""

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self._buf) < n:
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        if n < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self):
        try:
            self._r.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def curl_available() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except Exception:
        return False


def open_stream(url: str, headers: dict, timeout: float = 20.0):
    """A streaming response for url. Raises urllib.error.HTTPError for 4xx/5xx, OSError when it can't connect."""
    if curl_available():
        from curl_cffi import requests
        kw = {}
        try:
            from curl_cffi.const import CurlOpt
            kw["curl_options"] = {CurlOpt.IPRESOLVE: 1}              # IPv4 only: the address is tied to the IP that asked
        except Exception:
            pass
        try:
            r = requests.get(url, headers=headers, impersonate="chrome", stream=True, timeout=timeout, **kw)
        except Exception as e:
            raise OSError(str(e))
        if r.status_code >= 400:
            body = b""
            try:
                body = r.content[:200]
            except Exception:
                pass
            r.close()
            raise urllib.error.HTTPError(url, r.status_code, "", None, io.BytesIO(body))   # type: ignore[arg-type]
        return _Curl(r)
    return _ipv4_opener().open(urllib.request.Request(url, headers=headers), timeout=timeout)
