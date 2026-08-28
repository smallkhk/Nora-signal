import os
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

_controller = None


def init(controller_fn):
    global _controller
    _controller = controller_fn


@app.route("/")
def index():
    return render_template("viewer.html")


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
