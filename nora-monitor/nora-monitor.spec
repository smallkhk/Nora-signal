# PyInstaller spec — builds a single .exe with no console window
# Run: pyinstaller nora-monitor.spec

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect Flask template/static files
datas = [
    ("templates", "templates"),
    ("static",    "static"),
]
datas += collect_data_files("flask")
datas += collect_data_files("flask_socketio")
datas += collect_data_files("engineio")
datas += collect_data_files("socketio")

hiddenimports = (
    collect_submodules("flask")
    + collect_submodules("flask_socketio")
    + collect_submodules("engineio")
    + collect_submodules("socketio")
    + collect_submodules("gevent")
    + collect_submodules("geventwebsocket")
    + collect_submodules("pynput")
    + ["pynput.keyboard._win32", "pynput.mouse._win32"]
)

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NoraMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,       # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="icon.ico",   # Uncomment and add an .ico to set a custom icon
)
