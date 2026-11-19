"""Qt 6 + Kirigami frontend.

Appearance is entirely delegated: Kirigami picks the Breeze style and KDE colour scheme / accent colour on
Plasma, and follows the platform theme (the XDG portal, see core/desktop.py) everywhere else. This file sets
no palette, no style and no fonts.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .. import APP_ID, APP_NAME, __version__
from ..core.desktop import Desktop
from ..core.library import Library


def run(files: list[str], qt_args: list[str], dt: Desktop) -> int:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QIcon
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        print(f"marquee: Qt frontend needs PySide6 (Qt 6): {e}", file=sys.stderr)
        return 1
    from .controller import Controller
    from .youtube_ctl import YoutubeController
    from .shorts_ctl import ShortsController
    from .podcasts_ctl import PodcastsController
    from .twitch_ctl import TwitchController
    from .ytmusic_ctl import YtMusicController

    # QApplication (not QGuiApplication) so Kirigami can load qqc2-desktop-style, which draws native Breeze
    # controls on Plasma. Qt 6 scales per monitor and fractionally by default; nothing to configure for high DPI.
    try:
        from PySide6.QtGui import QSurfaceFormat
        fmt = QSurfaceFormat.defaultFormat()
        fmt.setAlphaBufferSize(8)          # lets the window be translucent, so KWin blur effects can show through
        QSurfaceFormat.setDefaultFormat(fmt)
    except ImportError:
        pass
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("marquee")
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationDomain("marquee.github.io")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon.fromTheme(APP_ID, QIcon(str(Path(__file__).with_name("icon.svg")))))

    lib = Library().load()
    lib.ensure_samples()
    controller = Controller(lib)

    try:
        from .mpris import Mpris
        controller.mpris = Mpris(controller)
        controller.mpris.command.connect(controller.mediaCommand)
    except Exception as e:
        print("marquee: media keys unavailable:", e, file=sys.stderr)

    engine = QQmlApplicationEngine()
    youtube = YoutubeController(controller)
    engine.rootContext().setContextProperty("marquee", controller)
    engine.rootContext().setContextProperty("youtube", youtube)
    ytmusic = YtMusicController(controller, youtube)
    engine.rootContext().setContextProperty("ytmusic", ytmusic)
    shorts = ShortsController(youtube)
    engine.rootContext().setContextProperty("shorts", shorts)
    podcasts = PodcastsController()
    engine.rootContext().setContextProperty("podcasts", podcasts)
    twitch = TwitchController(youtube)
    engine.rootContext().setContextProperty("twitch", twitch)
    engine.rootContext().setContextProperty("desktopName", dt.label)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    if not engine.rootObjects():
        print("marquee: could not load the QML interface. Is KDE Kirigami (org.kde.kirigami, Qt 6) installed?\n"
              "  Arch: kirigami  Fedora: kf6-kirigami  Debian/Ubuntu: qml6-module-org-kde-kirigami", file=sys.stderr)
        return 1
    if files:
        controller.addUrls([QUrl.fromLocalFile(f) for f in files])
    return app.exec()
