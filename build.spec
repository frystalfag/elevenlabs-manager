# -*- mode: python ; coding: utf-8 -*-
# pyinstaller build.spec -> dist/ElevenManager/ElevenManager.exe
# onedir on purpose: starts faster and antiviruses complain less than with onefile
from PyInstaller.utils.hooks import collect_all

datas = [("app/ui/styles.qss", "app/ui")]
binaries = []
hiddenimports = [
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
]

# without collect_all the exe tends to lose the qwindows platform plugin
for package in ("PyQt6", "pygame"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PyQt5", "PySide6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ElevenManager",
    console=False,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ElevenManager")
