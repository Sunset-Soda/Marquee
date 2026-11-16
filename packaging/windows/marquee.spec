# PyInstaller spec for the Windows build. Run from the repo root: pyinstaller packaging/windows/marquee.spec
import os
from PyInstaller.utils.hooks import collect_data_files
datas = [("marquee/qt/qml", "marquee/qt/qml"), ("marquee/qt/icon.svg", "marquee/qt")]
kdir = os.environ.get("KIRIGAMI_DIR")          # holds the built org/kde/kirigami QML module
if kdir:
    datas.append((os.path.join(kdir, "org"), "PySide6/qml/org"))
a = Analysis(["marquee/__main__.py"], datas=datas, hiddenimports=["marquee.qt.app"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Marquee", console=False,
          icon="packaging/windows/marquee.ico" if os.path.exists("packaging/windows/marquee.ico") else None)
coll = COLLECT(exe, a.binaries, a.datas, name="Marquee")
