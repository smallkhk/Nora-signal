import os
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

_controller = None
_recorder = None
_camera = None


def init(controller_fn, recorder, camera):
    global _controller, _recorder, _camera
    _controller = controller_fn
    _recorder = recorder
    _camera = camera


@app.route("/")
def index():
    return render_template("viewer.html")


# ── Recording ────────────────────────────────────────────────────────────────
@app.route("/record/start", methods=["POST"])
def record_start():
    if not _recorder:
        return jsonify({"ok": False})
    fname = _recorder.start()
    socketio.emit("record_state", {"recording": True, "file": fname})
    return jsonify({"ok": True, "file": fname})


@app.route("/record/stop", methods=["POST"])
def record_stop():
    if not _recorder:
        return jsonify({"ok": False})
    fname = _recorder.stop()
    socketio.emit("record_state", {"recording": False, "file": fname})
    return jsonify({"ok": True, "file": fname})


@app.route("/record/status")
def record_status():
    if not _recorder:
        return jsonify({"recording": False})
    return jsonify({"recording": _recorder.is_recording, "file": _recorder.filename})


# ── Camera ───────────────────────────────────────────────────────────────────
@app.route("/camera/start", methods=["POST"])
def camera_start():
    if not _camera:
        return jsonify({"ok": False})
    ok = _camera.start()
    socketio.emit("camera_state", {"active": ok})
    return jsonify({"ok": ok})


@app.route("/camera/stop", methods=["POST"])
def camera_stop():
    if not _camera:
        return jsonify({"ok": False})
    _camera.stop()
    socketio.emit("camera_state", {"active": False})
    return jsonify({"ok": True})


# ── Control ──────────────────────────────────────────────────────────────────
@socketio.on("command")
def on_command(data):
    if _controller:
        try:
            _controller(data)
        except Exception:
            pass


# ── Broadcast helpers ────────────────────────────────────────────────────────
def broadcast_frame(b64):
    socketio.emit("frame", {"data": b64})


def broadcast_key(char):
    socketio.emit("key", {"char": char})


def broadcast_camera(b64):
    socketio.emit("camera_frame", {"data": b64})


def run(host="0.0.0.0", port=9090):
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
