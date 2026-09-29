import os
import ctypes
import ctypes.wintypes
import subprocess
import threading


def send_notification(title, message):
    try:
        ps = (
            'Add-Type -AssemblyName System.Windows.Forms;'
            '$n = New-Object System.Windows.Forms.NotifyIcon;'
            '$n.Icon = [System.Drawing.SystemIcons]::Information;'
            '$n.Visible = $true;'
            f'$n.ShowBalloonTip(5000, "{title}", "{message}", "Info");'
            'Start-Sleep 6; $n.Dispose()'
        )
        subprocess.Popen(
            ['powershell', '-Command', ps],
            creationflags=0x08000000,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_wallpaper():
    try:
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.SystemParametersInfoW(0x0073, 512, buf, 0)
        return {"path": buf.value, "error": None}
    except Exception as e:
        return {"path": "", "error": str(e)}


def set_wallpaper(path):
    try:
        path = os.path.abspath(path)
        if not os.path.exists(path):
            return {"ok": False, "error": "File not found"}
        ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, path, 3)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_installed_programs():
    try:
        import winreg
    except ImportError:
        return []
    programs = []
    paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    seen = set()
    for hive, path in paths:
        try:
            key = winreg.OpenKey(hive, path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    try:
                        name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                    except FileNotFoundError:
                        continue
                    if name in seen:
                        continue
                    seen.add(name)
                    version = ""
                    publisher = ""
                    size = 0
                    try:
                        version = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                    except Exception:
                        pass
                    try:
                        publisher = winreg.QueryValueEx(subkey, "Publisher")[0]
                    except Exception:
                        pass
                    try:
                        size = winreg.QueryValueEx(subkey, "EstimatedSize")[0]
                    except Exception:
                        pass
                    programs.append({
                        "name": name,
                        "version": version,
                        "publisher": publisher,
                        "size_kb": size,
                    })
                    winreg.CloseKey(subkey)
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass
    programs.sort(key=lambda x: x["name"].lower())
    return programs


def list_network_connections():
    try:
        import psutil
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


def list_startup_programs():
    try:
        import winreg
    except ImportError:
        return []
    items = []
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
    ]
    for hive, path, scope in keys:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
            count = winreg.QueryInfoKey(key)[1]
            for i in range(count):
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    items.append({"name": name, "command": value, "scope": scope})
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass
    return items


def remove_startup_program(name, scope="HKCU"):
    try:
        import winreg
        if scope == "HKLM":
            hive = winreg.HKEY_LOCAL_MACHINE
        else:
            hive = winreg.HKEY_CURRENT_USER
        key = winreg.OpenKey(
            hive, r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, name)
        winreg.CloseKey(key)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def volume_control(action):
    try:
        import pyautogui
        if action == "mute":
            pyautogui.press("volumemute")
        elif action == "up":
            for _ in range(5):
                pyautogui.press("volumeup")
        elif action == "down":
            for _ in range(5):
                pyautogui.press("volumedown")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
