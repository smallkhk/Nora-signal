# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

_datas    = []
_binaries = []
_hidden   = []

for _pkg in [
    'PyQt5',
    'socketio', 'engineio',
    'av',
    'requests', 'certifi', 'urllib3', 'charset_normalizer', 'idna',
    'websocket',
]:
    try:
        d, b, h = collect_all(_pkg)
        _datas    += d
        _binaries += b
        _hidden   += h
    except Exception:
        pass

_datas += collect_data_files('certifi')

a = Analysis(
    ['nora_viewer.py'],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hidden + [
        'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
        'PyQt5.QtNetwork', 'PyQt5.sip',
        'socketio', 'socketio.exceptions',
        'engineio', 'engineio.async_drivers',
        'engineio.async_drivers.threading',
        'engineio.transports',
        'av', 'av.video', 'av.video.frame', 'av.codec',
        'av.codec.context', 'av.packet',
        'requests', 'certifi', 'urllib3',
        'websocket', 'websocket._core',
        'fractions', 'struct', 'hashlib', 'threading',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
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
    a.zipfiles,
    a.datas,
    [],
    name='NoraViewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll', 'python3*.dll', 'Qt5*.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
