#!/bin/sh
# Builds Marquee-<version>-<arch>.AppImage from this source tree.
#
#   sh packaging/build-appimage.sh              build the AppDir, then pack it (needs internet once, for appimagetool)
#   sh packaging/build-appimage.sh --appdir     only build packaging/build/Marquee.AppDir, which you can run directly
#
# The AppImage does NOT bundle Python, Qt or GTK. It runs on the system's own, which is what lets the app
# follow your KDE colour scheme or GNOME theme. Install the packages listed in README.md first.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="${ARCH:-$(uname -m)}"
VERSION="$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -n1)"
OUT="$ROOT/packaging/build"
APPDIR="$OUT/Marquee.AppDir"
ID="io.github.marquee.Marquee"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib/marquee" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps" \
         "$APPDIR/usr/share/metainfo"

cp -r "$ROOT/marquee" "$APPDIR/usr/lib/marquee/marquee"
find "$APPDIR" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$ROOT/data/$ID.desktop" "$APPDIR/$ID.desktop"
cp "$ROOT/data/$ID.desktop" "$APPDIR/usr/share/applications/"
cp "$ROOT/data/$ID.svg" "$APPDIR/$ID.svg"
cp "$ROOT/data/$ID.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
cp "$ROOT/data/$ID.metainfo.xml" "$APPDIR/usr/share/metainfo/"
ln -s "$ID.svg" "$APPDIR/.DirIcon"

cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
PY="$(command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "Marquee needs Python 3.10 or newer, installed through your distribution." >&2
    exit 1
fi
export PYTHONPATH="$HERE/usr/lib/marquee${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1      # the image is read-only
exec "$PY" -m marquee "$@"
RUN
chmod +x "$APPDIR/AppRun"
echo "AppDir ready: $APPDIR"

[ "${1:-}" = "--appdir" ] && exit 0

TOOL="$OUT/appimagetool-$ARCH.AppImage"
if [ ! -x "$TOOL" ]; then
    URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
    echo "Downloading appimagetool from $URL"
    if command -v curl >/dev/null 2>&1; then curl -fL -o "$TOOL" "$URL"; else wget -O "$TOOL" "$URL"; fi
    chmod +x "$TOOL"
fi
# APPIMAGE_EXTRACT_AND_RUN lets appimagetool itself run on systems without FUSE.
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$OUT/Marquee-$VERSION-$ARCH.AppImage"
echo "Built: $OUT/Marquee-$VERSION-$ARCH.AppImage"
