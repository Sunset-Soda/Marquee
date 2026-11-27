#!/usr/bin/env python3
"""Diagnostic only: what does YouTube offer for playing a running live stream from its true beginning?

    python3 tools/livestart_probe.py <video id or watch address>

For each way of asking, this lists the formats yt-dlp marks as playable from the start, then asks the first
piece, the second and the newest for a few bytes, and prints what came back. It changes nothing."""
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, ".")
from marquee.core.youtube import find_ytdlp, js_runtime_args   # noqa: E402

CLIENTS = ["", "android_vr", "tv", "mweb", "web", "web_safari", "ios"]


def boxes(data: bytes):
    out, i = [], 0
    while i + 8 <= len(data):
        size = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8].decode("latin1")
        if not re.fullmatch(r"[\w ]{4}", kind):
            break
        out.append(f"{kind}({size})")
        if size < 8:
            break
        i += size
    return out


def get(url, headers, n=2048):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(n)
            h = {k: v for k, v in r.headers.items() if k.lower().startswith("x-") or k.lower() in ("content-length", "content-type")}
            return r.status, h, body
    except urllib.error.HTTPError as e:
        return e.code, {}, b""
    except Exception as e:                                   # noqa: BLE001
        return str(e), {}, b""


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    vid = sys.argv[1]
    page = vid if "://" in vid else f"https://www.youtube.com/watch?v={vid}"
    exe = find_ytdlp() or "yt-dlp"
    for client in CLIENTS:
        print(f"\n===== client: {client or '(yt-dlp default)'} =====", flush=True)
        cmd = [exe, "-J", "--no-warnings", "--live-from-start", "--force-ipv4", *js_runtime_args()]
        if client:
            cmd += ["--extractor-args", f"youtube:player_client={client}"]
        p = subprocess.run(cmd + [page], capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            print("  yt-dlp failed:", (p.stderr.strip().splitlines() or ["?"])[-1][:200])
            continue
        info = json.loads(p.stdout)
        fmts = info.get("formats", [])
        print(f"  {len(fmts)} formats, live_status={info.get('live_status')}")
        for f in fmts:
            if f.get("vcodec") == "none" and f.get("acodec") == "none":
                continue
            kind = "video" if f.get("vcodec") not in (None, "none") else "audio"
            print(f"  - id={f.get('format_id')} {kind} {f.get('height') or ''} proto={f.get('protocol')} from_start={f.get('is_from_start')} "
                  f"dur={f.get('target_duration_sec')} codec={f.get('vcodec') if kind == 'video' else f.get('acodec')} ext={f.get('ext')}")
        starts = [f for f in fmts if f.get("is_from_start")]
        pick = []
        for want in ("video", "audio"):
            c = [f for f in starts if (f.get("vcodec") not in (None, "none")) == (want == "video")]
            c.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
            if c:
                pick.append(c[-1])
        for f in pick:
            url, hd = f["url"], f.get("http_headers") or {}
            sep = "&" if "?" in url else "?"
            print(f"  >> probing {f.get('format_id')} ({'video' if f.get('vcodec') != 'none' else 'audio'})")
            for sq in ("0", "1", "999999999"):
                st, h, body = get(f"{url}{sep}sq={sq}", hd)
                print(f"     sq={sq}: status={st} headers={h} boxes={boxes(body)}")
            st, h, body = get(url, hd)
            print(f"     no sq: status={st} headers={h} boxes={boxes(body)}")
        if not starts:
            print("  (no format marked from-start for this client)")


if __name__ == "__main__":
    main()
