"""Keep a copy of everything Marquee prints (its own notes and Qt/QML's) in a log file you can hand over when something goes wrong.

Each line carries the time it was printed; ffmpeg's repeated Late SEI warnings are left out.
The file is ~/.local/share/marquee/marquee.log; the one from the previous run is kept as marquee.log.1.
Everything is still printed to the terminal as before."""
from __future__ import annotations

import os
import threading
from pathlib import Path


def log_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "marquee" / "marquee.log"


def start() -> Path | None:
    """Copy standard error into the log file for the rest of the run. Returns the file, or None if that wasn't possible."""
    try:
        import datetime
        import sys
        from .. import __version__
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            os.replace(path, path.with_name(path.name + ".1"))
        out = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        out.write(f"Marquee {__version__}, started {datetime.datetime.now():%Y-%m-%d %H:%M:%S}, Python {sys.version.split()[0]}\n")
        real = os.dup(2)                                    # the terminal
        r, w = os.pipe()
        os.dup2(w, 2)                                       # from now on stderr (Python's and Qt's) goes through the pipe
        os.close(w)

        def pump():
            import time
            noise = ("Late SEI is not implemented", "streams.videolan.org/upload")    # ffmpeg warnings that say nothing and fill the log
            buf = b""
            with os.fdopen(r, "rb", buffering=0) as src:
                while True:
                    data = src.read(4096)
                    if not data:
                        return
                    try:
                        os.write(real, data)
                    except OSError:
                        pass
                    buf += data
                    *lines, buf = buf.split(b"\n")
                    for ln in lines:
                        text = ln.decode("utf-8", "replace")
                        if any(n in text for n in noise):
                            continue
                        out.write(time.strftime("%H:%M:%S ") + text + "\n")
        threading.Thread(target=pump, daemon=True).start()
        return path
    except Exception:
        return None
