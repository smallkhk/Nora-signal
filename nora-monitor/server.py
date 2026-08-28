import os
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

_controller = None
_recorder = None


def init(controller_fn, recorder):
    global _controller, _recorder
    _controller = controller_fn
    _recorder = recorder


@app.route("/")
def index():
    return render_template("viewer.html")


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
    return jsonify({
        "recording": _recorder.is_recording,
        "file": _recorder.filename,
    })


@socketio.on("command")
def on_command(data):
    if _controller:
        try:
            _controller(data)
        except Exception:
            pass


def broadcast_frame(b64):
    socketio.emit("frame", {"data": b64}, namespace="/")


def broadcast_key(char):
    socketio.emit("key", {"char": char}, namespace="/")


def run(host="0.0.0.0", port=9090):
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
