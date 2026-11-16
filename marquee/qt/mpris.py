"""Media key and desktop media-controls support through MPRIS (org.mpris.MediaPlayer2).

KDE, GNOME and most desktops send the keyboard's play/pause/next/previous keys to whatever player is on the
session bus under org.mpris.MediaPlayer2.*, and show it in their media widget. This registers Marquee there.
Everything is best effort: if QtDBus or the session bus is missing, the app simply runs without media keys.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import ClassInfo, Property, QObject, Signal, Slot
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusMessage, QDBusObjectPath

SERVICE = "org.mpris.MediaPlayer2.marquee"
PATH = "/org/mpris/MediaPlayer2"
IFACE_ROOT = "org.mpris.MediaPlayer2"
IFACE_PLAYER = "org.mpris.MediaPlayer2.Player"


@ClassInfo({"D-Bus Interface": IFACE_ROOT})
class _Root(QDBusAbstractAdaptor):

    def __init__(self, owner):
        super().__init__(owner)
        self._o = owner

    CanQuit = Property(bool, lambda self: False)
    CanRaise = Property(bool, lambda self: False)
    HasTrackList = Property(bool, lambda self: False)
    Identity = Property(str, lambda self: "Marquee")
    DesktopEntry = Property(str, lambda self: "io.github.marquee.Marquee")
    SupportedUriSchemes = Property(list, lambda self: [])
    SupportedMimeTypes = Property(list, lambda self: [])

    @Slot()
    def Raise(self):
        pass

    @Slot()
    def Quit(self):
        pass


@ClassInfo({"D-Bus Interface": IFACE_PLAYER})
class _Player(QDBusAbstractAdaptor):

    def __init__(self, owner):
        super().__init__(owner)
        self._o = owner

    PlaybackStatus = Property(str, lambda self: self._o.status)
    Metadata = Property("QVariantMap", lambda self: self._o.metadata())
    Rate = Property(float, lambda self: 1.0)
    MinimumRate = Property(float, lambda self: 1.0)
    MaximumRate = Property(float, lambda self: 1.0)
    Volume = Property(float, lambda self: 1.0)
    CanGoNext = Property(bool, lambda self: True)
    CanGoPrevious = Property(bool, lambda self: True)
    CanPlay = Property(bool, lambda self: True)
    CanPause = Property(bool, lambda self: True)
    CanSeek = Property(bool, lambda self: False)
    CanControl = Property(bool, lambda self: True)

    @Slot()
    def Next(self): self._o.command.emit("next")

    @Slot()
    def Previous(self): self._o.command.emit("previous")

    @Slot()
    def Pause(self): self._o.command.emit("pause")

    @Slot()
    def PlayPause(self): self._o.command.emit("playpause")

    @Slot()
    def Stop(self): self._o.command.emit("stop")

    @Slot()
    def Play(self): self._o.command.emit("play")


class Mpris(QObject):
    command = Signal(str)          # play, pause, playpause, next, previous, stop

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = "Stopped"
        self._meta = {"title": "", "artist": "", "art": ""}
        self.ok = False
        try:
            bus = QDBusConnection.sessionBus()
            if not bus.isConnected():
                return
            self._root = _Root(self)
            self._player = _Player(self)
            if not bus.registerService(SERVICE):
                print("marquee: media keys: could not claim", SERVICE, file=sys.stderr)
                return
            self.ok = bus.registerObject(PATH, self, QDBusConnection.ExportAdaptors)
        except Exception as e:                                   # never let this stop the app
            print("marquee: media keys unavailable:", e, file=sys.stderr)

    def metadata(self) -> dict:
        m = {"mpris:trackid": QDBusObjectPath(PATH + "/track/0")}
        if self._meta["title"]:
            m["xesam:title"] = self._meta["title"]
        if self._meta["artist"]:
            m["xesam:artist"] = [self._meta["artist"]]
        if self._meta["art"]:
            m["mpris:artUrl"] = self._meta["art"]
        return m

    def update(self, title: str, artist: str, art: str, playing: bool, active: bool) -> None:
        if not self.ok:
            return
        status = ("Playing" if playing else "Paused") if active else "Stopped"
        meta = {"title": title, "artist": artist, "art": art}
        changed = {}
        if status != self.status:
            self.status = status
            changed["PlaybackStatus"] = status
        if meta != self._meta:
            self._meta = meta
            changed["Metadata"] = self.metadata()
        if not changed:
            return
        try:
            msg = QDBusMessage.createSignal(PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged")
            msg.setArguments([IFACE_PLAYER, changed, []])
            QDBusConnection.sessionBus().send(msg)
        except Exception as e:
            print("marquee: media keys:", e, file=sys.stderr)
