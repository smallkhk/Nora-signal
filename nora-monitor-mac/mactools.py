"""
macOS equivalents of wintools.py — uses osascript, psutil, subprocess, AppKit/Quartz
where available. No Windows-only imports.
"""
import os
import subprocess
import threading
from pathlib import Path


# ── Notifications ─────────────────────────────────────────────────────────────

def send_notification(title, message):
    try:
        script = (
            f'display notification "{message}" with title "{title}"'
        )
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Wallpaper ─────────────────────────────────────────────────────────────────

def get_wallpaper():
    try:
        script = (
            'tell application "Finder" to get POSIX path of '
            '(desktop picture as alias)'
        )
        result = subprocess.check_output(
            ["osascript", "-e", script],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return {"path": result, "error": None}
    except Exception as e:
        return {"path": "", "error": str(e)}


def set_wallpaper(path):
    try:
        path = os.path.abspath(path)
        if not os.path.exists(path):
            return {"ok": False, "error": "File not found"}
        script = (
            f'tell application "Finder" to set desktop picture to '
            f'POSIX file "{path}"'
        )
        subprocess.check_call(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Installed applications ────────────────────────────────────────────────────

def list_installed_programs():
    programs = []
    seen = set()
    app_dirs = [
        Path("/Applications"),
        Path.home() / "Applications",
    ]
    for app_dir in app_dirs:
        if not app_dir.exists():
            continue
        for app in app_dir.glob("*.app"):
            name = app.stem
            if name in seen:
                continue
            seen.add(name)
            # Try to get version from Info.plist
            version = ""
            publisher = ""
            try:
                plist_path = app / "Contents" / "Info.plist"
                if plist_path.exists():
                    import plistlib
                    with open(plist_path, "rb") as f:
                        plist = plistlib.load(f)
                    version = plist.get("CFBundleShortVersionString", "")
                    publisher = plist.get("CFBundleIdentifier", "").split(".")[1] if "." in plist.get("CFBundleIdentifier", "") else ""
            except Exception:
                pass
            programs.append({
                "name": name,
                "version": version,
                "publisher": publisher,
                "size_kb": 0,
            })
    programs.sort(key=lambda x: x["name"].lower())
    return programs


# ── Network connections ───────────────────────────────────────────────────────

def list_network_connections():
    try:
        import psutil, socket
        conns = []
        for c in psutil.net_connections(kind="inet"):
            try:
                proc_name = ""
                if c.pid:
                    try:
                        proc_name = psutil.Process(c.pid).name()
                    except Exception:
                        pass
                conns.append({
                    "proto": "TCP" if c.type == 1 else "UDP",
                    "local": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                    "remote": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
                    "status": c.status if hasattr(c, "status") else "",
                    "pid": c.pid or 0,
                    "process": proc_name,
                })
            except Exception:
                pass
        return conns
    except Exception:
        return []


# ── Startup / LaunchAgents ────────────────────────────────────────────────────

def list_startup_programs():
    import plistlib
    items = []
    dirs = [
        Path.home() / "Library" / "LaunchAgents",
        Path("/Library/LaunchAgents"),
        Path("/Library/LaunchDaemons"),
    ]
    for d in dirs:
        if not d.exists():
            continue
        scope = str(d)
        for plist_file in d.glob("*.plist"):
            try:
                with open(plist_file, "rb") as f:
                    plist = plistlib.load(f)
                label = plist.get("Label", plist_file.stem)
                program = plist.get("Program", "") or " ".join(
                    plist.get("ProgramArguments", [])
                )
                items.append({
                    "name": label,
                    "command": program,
                    "scope": scope,
                    "plist": str(plist_file),
                })
            except Exception:
                pass
    return items


def remove_startup_program(name, scope="user"):
    try:
        # Find by label
        import plistlib
        search_dirs = [
            Path.home() / "Library" / "LaunchAgents",
            Path("/Library/LaunchAgents"),
            Path("/Library/LaunchDaemons"),
        ]
        for d in search_dirs:
            if not d.exists():
                continue
            for plist_file in d.glob("*.plist"):
                try:
                    with open(plist_file, "rb") as f:
                        plist = plistlib.load(f)
                    if plist.get("Label") == name or plist_file.stem == name:
                        # Unload then remove
                        subprocess.run(
                            ["launchctl", "unload", str(plist_file)],
                            stderr=subprocess.DEVNULL,
                        )
                        plist_file.unlink()
                        return {"ok": True}
                except Exception:
                    pass
        return {"ok": False, "error": "Item not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Volume control ────────────────────────────────────────────────────────────

def volume_control(action):
    try:
        if action == "mute":
            script = "set volume with output muted"
        elif action == "unmute":
            script = "set volume without output muted"
        elif action == "up":
            script = "set volume output volume ((output volume of (get volume settings)) + 10)"
        elif action == "down":
            script = "set volume output volume ((output volume of (get volume settings)) - 10)"
        else:
            return {"ok": False, "error": "Unknown action"}
        subprocess.check_call(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Monitors ──────────────────────────────────────────────────────────────────

def list_monitors():
    try:
        import mss
        with mss.mss() as sct:
            monitors = []
            for i, m in enumerate(sct.monitors):
                monitors.append({
                    "index": i,
                    "left": m["left"],
                    "top": m["top"],
                    "width": m["width"],
                    "height": m["height"],
                    "label": "All Monitors" if i == 0 else f"Monitor {i}",
                })
            return monitors
    except Exception:
        return []


# ── Running apps (for window list) ───────────────────────────────────────────

def list_windows():
    try:
        script = '''
tell application "System Events"
    set appList to {}
    repeat with proc in (processes whose background only is false)
        set appList to appList & {name of proc & "|" & unix id of proc as text}
    end repeat
    return appList
end tell
'''
        output = subprocess.check_output(
            ["osascript", "-e", script],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        windows = []
        for item in output.split(", "):
            parts = item.strip().split("|")
            if len(parts) == 2:
                windows.append({
                    "title": parts[0],
                    "hwnd": parts[1],
                    "process": parts[0],
                })
        return windows
    except Exception:
        return []
