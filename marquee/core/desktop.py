"""Desktop environment detection and per-toolkit environment preparation.

This module never forces a theme. It picks which native frontend to start and, for Qt apps running outside
KDE, points Qt at the XDG desktop portal so dark/light follows the system instead of falling back to a
hard-coded palette.
"""
from __future__ import annotations

import os
import sys
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Mapping

GNOME_LIKE = {"GNOME", "UNITY", "BUDGIE", "PANTHEON", "COSMIC", "ENDLESS", "DEEPIN"}


@dataclass
class Desktop:
    names: list[str] = field(default_factory=list)   # upper-case XDG_CURRENT_DESKTOP entries
    session: str = ""                                 # "wayland" | "x11" | ""
    family: str = "other"                             # "kde" | "gnome" | "lxqt" | "other"

    @property
    def label(self) -> str:
        return ":".join(self.names) if self.names else "unknown"


def detect(env: Mapping[str, str] | None = None) -> Desktop:
    env = os.environ if env is None else env
    raw = env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION") or ""
    names = [n.strip().upper() for n in re.split(r"[:;]", raw) if n.strip()]
    if env.get("KDE_FULL_SESSION") == "true" and "KDE" not in names:
        names.append("KDE")
    if "KDE" in names or "PLASMA" in names:
        family = "kde"
    elif any(n in GNOME_LIKE for n in names):
        family = "gnome"
    elif "LXQT" in names:
        family = "lxqt"
    else:
        family = "other"
    return Desktop(names=names, session=(env.get("XDG_SESSION_TYPE") or "").lower(), family=family)


TOOLKIT_ERRORS: dict[str, str] = {}     # why a toolkit could not be used, filled in by available_toolkits()


def available_toolkits() -> dict[str, bool]:
    """Whether each toolkit can really be imported, not just found on the path (a broken PyGObject is common)."""
    TOOLKIT_ERRORS.clear()

    def qt() -> bool:
        try:
            import PySide6.QtQml  # noqa: F401
            import PySide6.QtQuick  # noqa: F401
            import PySide6.QtWidgets  # noqa: F401
            import PySide6.QtMultimedia  # noqa: F401
            return True
        except ImportError as e:
            TOOLKIT_ERRORS["qt"] = f"{e} (python: {sys.executable})"
            return False

    def gtk() -> bool:
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Adw", "1")
            return True
        except (ImportError, ValueError) as e:
            TOOLKIT_ERRORS["gtk"] = str(e)
            return False
    return {"qt": qt(), "gtk": gtk()}


def choose_toolkit(desktop: Desktop, preference: str = "auto", available: Mapping[str, bool] | None = None) -> str:
    """Pick "qt" or "gtk". KDE Plasma and LXQt get Qt; GNOME and everything else get GTK."""
    available = dict(available_toolkits() if available is None else available)
    if preference in ("qt", "gtk"):
        wanted = preference
    else:
        wanted = "qt" if desktop.family in ("kde", "lxqt") else "gtk"
    if available.get(wanted):
        return wanted
    other = "gtk" if wanted == "qt" else "qt"
    if available.get(other):
        why = TOOLKIT_ERRORS.get(wanted, "not installed")
        print(f"marquee: the {wanted.upper()} frontend is not usable ({why}). Using {other.upper()} instead. "
              f"Install the {wanted.upper()} packages from the README, or run with --toolkit {wanted} to see the error.",
              file=sys.stderr)
        return other
    raise RuntimeError(
        "Neither PySide6 (Qt 6) nor PyGObject (GTK 4 + libadwaita) is installed. See README.md for the packages."
    )


def prepare_qt_environment(desktop: Desktop, env: dict | None = None) -> None:
    """Called before QGuiApplication exists. Never overrides anything the user or the desktop already set."""
    env = os.environ if env is None else env
    if desktop.family != "kde" and "QT_QPA_PLATFORMTHEME" not in env:
        # Qt >= 6.5 can read colour scheme and accent from the XDG settings portal (GNOME, Cinnamon, XFCE with portals, ...).
        env["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"
    # Deliberately no QT_QUICK_CONTROLS_STYLE: on Plasma that selects Breeze via Kirigami, elsewhere the user's own style wins.


def prepare_gtk_environment(desktop: Desktop, env: dict | None = None) -> None:
    """libadwaita reads colour scheme and accent from the portal on its own. Nothing to force."""
    return None


# ---- diagnostics (--debug-appearance) ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: float = 1.5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def portal_appearance() -> dict:
    """What the XDG settings portal reports, via gdbus when present."""
    out: dict = {}
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return out
    base = [gdbus, "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
            "--object-path", "/org/freedesktop/portal/desktop", "--method", "org.freedesktop.portal.Settings.Read",
            "org.freedesktop.appearance"]
    scheme = _run(base + ["color-scheme"])
    m = re.search(r"uint32 (\d)", scheme)
    if m:
        out["color-scheme"] = {"0": "no preference", "1": "dark", "2": "light"}.get(m.group(1), m.group(1))
    accent = _run(base + ["accent-color"])
    m = re.findall(r"[-+]?\d*\.\d+|\d+", accent)
    if len(m) >= 3:
        r, g, b = (max(0, min(255, round(float(x) * 255))) for x in m[:3])
        out["accent-color"] = f"#{r:02x}{g:02x}{b:02x}"
    return out


def describe(desktop: Desktop, available: Mapping[str, bool] | None = None) -> str:
    available = dict(available_toolkits() if available is None else available)
    lines = [
        f"Desktop:        {desktop.label} ({desktop.family})",
        f"Session:        {desktop.session or 'unknown'}",
        f"Toolkits:       Qt 6 (PySide6) {'yes' if available.get('qt') else 'no'}, GTK 4 (PyGObject) {'yes' if available.get('gtk') else 'no'}",
    ]
    try:
        lines.append(f"Chosen:         {choose_toolkit(desktop, 'auto', available)}")
    except RuntimeError as e:
        lines.append(f"Chosen:         none ({e})")
    for name, why in TOOLKIT_ERRORS.items():
        lines.append(f"{name.upper()} problem:    {why}")
    portal = portal_appearance()
    lines.append(f"Portal scheme:  {portal.get('color-scheme', 'unavailable')}")
    lines.append(f"Portal accent:  {portal.get('accent-color', 'unavailable')}")
    if desktop.family == "kde":
        kde = _run(["kreadconfig6", "--group", "General", "--key", "ColorScheme", "--file", "kdeglobals"])
        lines.append(f"KDE scheme:     {kde or 'unavailable'}")
    for k in ("QT_QPA_PLATFORMTHEME", "QT_QUICK_CONTROLS_STYLE", "QT_SCALE_FACTOR", "GDK_SCALE", "GTK_THEME"):
        if os.environ.get(k):
            lines.append(f"{k + ':':<16}{os.environ[k]}")
    return "\n".join(lines)
