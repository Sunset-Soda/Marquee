"""Settings > Updates: look for a newer Marquee on GitHub and, for the AppImage, install it."""
from __future__ import annotations

import subprocess
import sys
import threading

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot

from .. import __version__
from ..core import updater


def _log(msg: str) -> None:
    print(f"marquee: updater: {msg}", file=sys.stderr, flush=True)


class UpdaterController(QObject):
    changed = Signal()
    _checked = Signal("QVariant", bool)
    _failed = Signal(str, bool)
    _progress = Signal(float)
    _installed = Signal()

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self._ctl = ctl                              # the main controller: its toasts and saved preferences
        self._state = "idle"                         # idle, checking, uptodate, available, downloading, ready, error
        self._info: dict = {}
        self._message = ""
        self._pct = 0.0
        self._path = updater.appimage_path()         # the AppImage being run, when it can replace itself
        self._checked.connect(self._on_checked)
        self._failed.connect(self._on_failed)
        self._progress.connect(self._on_progress)
        self._installed.connect(self._on_installed)
        if self.autoCheck:
            QTimer.singleShot(8000, lambda: self.check(True))      # a little after start, so it never slows the window opening

    # ---- what the Settings page shows ----
    @Property(str, constant=True)
    def version(self):
        return __version__

    @Property(str, notify=changed)
    def state(self):
        return self._state

    @Property(str, notify=changed)
    def latest(self):
        return self._info.get("latest", "")

    @Property(str, notify=changed)
    def notes(self):
        return self._info.get("notes", "")

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(float, notify=changed)
    def progress(self):
        return self._pct

    @Property(bool, constant=True)
    def canInstall(self):
        return bool(self._path)

    @Property(bool, notify=changed)
    def autoCheck(self):
        return bool(self._ctl.lib.prefs.get("update_check", True))

    @Slot(bool)
    def setAutoCheck(self, on: bool):
        self._ctl.lib.prefs["update_check"] = bool(on)
        self._ctl.lib.save()
        self.changed.emit()

    # ---- actions ----
    @Slot()
    def check(self, quiet: bool = False):
        if self._state in ("checking", "downloading"):
            return
        if not quiet:
            self._state, self._message = "checking", ""
            self.changed.emit()

        def work():
            try:
                self._checked.emit(updater.check(__version__), quiet)
            except Exception as e:                    # noqa: BLE001
                self._failed.emit(str(e), quiet)
        threading.Thread(target=work, daemon=True).start()

    def _on_checked(self, info, quiet: bool):
        self._info = dict(info)
        if info["newer"]:
            _log(f"{info['latest']} is available (this is {__version__})")
            self._state = "available"
            prefs = self._ctl.lib.prefs
            if quiet and prefs.get("update_told") != info["latest"]:
                prefs["update_told"] = info["latest"]
                self._ctl.lib.save()
                self._ctl.toast.emit(f"Marquee {info['latest']} is available. Settings > Updates has it.")
        elif not quiet or self._state == "checking":
            self._state = "uptodate"
        self.changed.emit()

    def _on_failed(self, why: str, quiet: bool):
        _log(f"could not check: {why}")
        if not quiet:
            self._state, self._message = "error", "Could not reach GitHub to check. Try again in a moment."
            self.changed.emit()

    @Slot()
    def install(self):
        if self._state != "available" or not self._path:
            return
        self._state, self._pct, self._message = "downloading", 0.0, ""
        self.changed.emit()
        info, target = dict(self._info), self._path

        def work():
            try:
                updater.install(target, info, progress=self._progress.emit)
                self._installed.emit()
            except Exception as e:                    # noqa: BLE001
                _log(f"install failed: {e}")
                self._failed.emit(f"The update could not be installed: {e}", False)
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, p: float):
        self._pct = p
        self.changed.emit()

    def _on_installed(self):
        _log(f"installed {self._info.get('latest')}")
        self._state = "ready"
        self.changed.emit()

    @Slot()
    def restart(self):
        """Start the new version and close this one."""
        if self._state != "ready" or not self._path:
            return
        try:
            subprocess.Popen([self._path], start_new_session=True, stdin=subprocess.DEVNULL)
        except OSError as e:
            self._message = f"The new version is installed but could not be started: {e}"
            self.changed.emit()
            return
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    @Slot()
    def openPage(self):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(self._info.get("page") or updater.RELEASES))
