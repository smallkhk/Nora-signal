"""
Auto-updater for NoraMonitor.
Checks the GitHub releases API once per hour. If a newer release tag is found,
downloads the new EXE, writes a detached batch script that waits for this
process to exit, swaps the file, then restarts — transparent to the user.
"""
import os
import sys
import json
import time
import tempfile
import threading
import subprocess
import urllib.request

REPO         = "smallkhk/Nora-signal"
_APP_DIR     = os.path.join(os.path.expandvars("%APPDATA%"), "NoraMonitor")
_TAG_FILE    = os.path.join(_APP_DIR, "release_tag.txt")
_CHECK_DELAY = 15        # seconds after startup before first check
_CHECK_EVERY = 3600      # re-check interval (1 hour)


def _get_latest_release():
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "NoraMonitor-Updater/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _read_current_tag(build_tag):
    """Return the tag we are currently running — either the embedded build tag
    or whatever we last wrote to disk (first-run fallback)."""
    if build_tag and build_tag != "nora-monitor-UNKNOWN":
        return build_tag
    try:
        with open(_TAG_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


def _write_tag(tag):
    try:
        os.makedirs(_APP_DIR, exist_ok=True)
        with open(_TAG_FILE, "w") as f:
            f.write(tag)
    except Exception:
        pass


def _find_exe_asset(assets):
    for a in assets:
        if a.get("name", "").lower() == "noramonitor.exe":
            return a
    # fallback: any .exe
    for a in assets:
        if a.get("name", "").lower().endswith(".exe"):
            return a
    return None


def _do_update(dl_url, latest_tag):
    """Download new EXE, write swap batch, launch it detached, exit."""
    os.makedirs(_APP_DIR, exist_ok=True)
    new_exe = os.path.join(_APP_DIR, "NoraMonitor_update.exe")
    current_exe = os.path.join(_APP_DIR, "NoraMonitor.exe")

    # If not running from the permanent location, fall back to sys.executable
    if not getattr(sys, "frozen", False):
        return  # dev mode — don't self-update

    urllib.request.urlretrieve(dl_url, new_exe)

    pid = os.getpid()
    bat = os.path.join(tempfile.gettempdir(), "nora_update.bat")
    tag_escaped = latest_tag.replace('"', '')
    with open(bat, "w") as f:
        f.write(
            f"@echo off\n"
            f":wait\n"
            f"tasklist /FI \"PID eq {pid}\" 2>nul | find \"{pid}\" >nul\n"
            f"if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\n"
            f"move /y \"{new_exe}\" \"{current_exe}\"\n"
            f"echo {tag_escaped}> \"{_TAG_FILE}\"\n"
            f"start \"\" \"{current_exe}\"\n"
            f"del \"%~f0\"\n"
        )

    subprocess.Popen(
        ["cmd.exe", "/c", bat],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    time.sleep(1)
    sys.exit(0)


def check_once(build_tag="nora-monitor-UNKNOWN", log=None):
    def _log(msg):
        if log:
            log(f"[updater] {msg}")

    try:
        data = _get_latest_release()
        latest_tag = data.get("tag_name", "")
        if not latest_tag:
            return

        current_tag = _read_current_tag(build_tag)

        # On very first run with no stored tag, just record what we have
        if not current_tag:
            _write_tag(latest_tag)
            return

        if latest_tag == current_tag:
            return  # already up to date

        assets = data.get("assets", [])
        asset  = _find_exe_asset(assets)
        if not asset:
            _log("No EXE asset in latest release — skipping")
            return

        _log(f"Update found: {current_tag} → {latest_tag}. Downloading…")
        _do_update(asset["browser_download_url"], latest_tag)

    except Exception as e:
        _log(f"Check failed: {e}")


def start(build_tag="nora-monitor-UNKNOWN", log=None):
    """Start background update loop. Call once from app.py."""
    def _loop():
        time.sleep(_CHECK_DELAY)
        while True:
            check_once(build_tag, log)
            time.sleep(_CHECK_EVERY)

    threading.Thread(target=_loop, daemon=True, name="updater").start()
