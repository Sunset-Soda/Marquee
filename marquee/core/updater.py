"""Finding out whether a newer Marquee has been released, and (for the AppImage) swapping it in.

Releases live on GitHub; the AppImage is attached to each one. Nothing here talks to anything else, and a downloaded
file is only used when it is the right size, matches GitHub's checksum when GitHub gives one, and looks like a program."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import urllib.request

REPO = "Sunset-Soda/Marquee"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES = f"https://github.com/{REPO}/releases"
ASSET = "Marquee-x86_64.AppImage"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
UA = "Marquee-updater"


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.3.6' -> (1, 3, 6). Anything after the numbers (a suffix such as -rc1) is ignored; no numbers gives ()."""
    m = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _get_json(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000).decode("utf-8", "replace"))


def check(current: str, fetch=None) -> dict:
    """Ask GitHub for the newest release. Returns {latest, newer, notes, page, asset, size, sha256}; raises when it cannot be reached."""
    data = (fetch or _get_json)(API)
    tag = str(data.get("tag_name") or "")
    if not parse_version(tag):
        raise ValueError("the newest release has no version number")
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
    digest = str((asset or {}).get("digest") or "")
    return {
        "latest": tag.lstrip("vV"),
        "newer": is_newer(tag, current),
        "notes": str(data.get("body") or "").strip(),
        "page": str(data.get("html_url") or RELEASES),
        "asset": str((asset or {}).get("browser_download_url") or ""),
        "size": int((asset or {}).get("size") or 0),
        "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
    }


def appimage_path(environ=None) -> str:
    """The AppImage file this copy of Marquee is running from, when it can be replaced in place; otherwise ''."""
    env = os.environ if environ is None else environ
    p = env.get("APPIMAGE", "")
    if p and os.path.isfile(p) and os.access(os.path.dirname(p) or ".", os.W_OK) and os.access(p, os.W_OK):
        return p
    return ""


def install(target: str, info: dict, progress=None, allowed: str = DOWNLOADS, opener=None) -> None:
    """Download the release's AppImage and put it in place of `target`. The running program is not disturbed (it keeps its own copy
    until it exits); nothing at `target` changes unless the whole download checks out."""
    url = info.get("asset", "")
    if not url.startswith(allowed):
        raise ValueError("the release has no AppImage to download")
    part = target + ".new"
    sha = hashlib.sha256()
    got = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with (opener or urllib.request.urlopen)(req, timeout=30) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress(min(1.0, got / total))
        want = int(info.get("size") or 0)
        if want and got != want:
            raise ValueError(f"the download was cut short ({got} of {want} bytes)")
        if info.get("sha256") and sha.hexdigest() != info["sha256"]:
            raise ValueError("the download does not match its checksum")
        with open(part, "rb") as f:
            if f.read(4) != b"\x7fELF":
                raise ValueError("the download is not a program")
        mode = os.stat(target).st_mode if os.path.exists(target) else 0o755
        os.chmod(part, (mode | stat.S_IXUSR) & 0o7777)
        os.replace(part, target)
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
