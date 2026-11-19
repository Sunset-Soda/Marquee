"""Entry point: detect the desktop and start the Qt frontend."""
from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__
from .core import desktop


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="marquee", description=f"{APP_NAME}: media library and in-app player")
    ap.add_argument("--debug-appearance", action="store_true",
                    help="print the detected desktop, toolkits and system appearance, then exit")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    ap.add_argument("files", nargs="*", help="media files or folders to add to the library")
    args, rest = ap.parse_known_args(argv)

    dt = desktop.detect()
    if args.debug_appearance:
        print(desktop.describe(dt))
        return 0
    from .core import logfile
    logfile.start()
    desktop.prepare_qt_environment(dt)
    from .qt.app import run
    return run(args.files, rest, dt)


if __name__ == "__main__":
    sys.exit(main())
