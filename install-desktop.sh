#!/usr/bin/env bash
# Registers Marquee with the desktop (menu entry + icon) so the taskbar and window show the right icon.
# Needed on Wayland, where the window icon comes from the .desktop file and not from the app itself.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
id=io.github.marquee.Marquee
apps="$HOME/.local/share/applications"
icons="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$apps" "$icons"
cp "$here/data/$id.svg" "$icons/$id.svg"
sed "s|^Exec=.*|Exec=env PYTHONPATH=$here python3 -m marquee --toolkit qt %F|" "$here/data/$id.desktop" > "$apps/$id.desktop"
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
echo "Installed. Quit Marquee, then start it again (from the menu or with python3 -m marquee --toolkit qt)."
