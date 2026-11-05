"""
socks5_server.py — SOCKS5 proxy + ngrok TCP tunnel for public access.

start(port, token) → starts proxy + opens ngrok TCP tunnel
stop()             → closes both
status()           → current state
"""

import os
import socket
import select
import struct
import threading
import time

_server_sock   = None
_running       = False
_lock          = threading.Lock()
_port          = 1080
_client_count  = 0
_started_at    = 0.0
_public_host   = None
_public_port   = None
_ngrok_tunnel  = None

_TOKEN_FILE = os.path.join(os.path.expandvars("%APPDATA%"), "SyncHost", "ngrok.token")


def _load_token():
    try:
        with open(_TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def save_token(token: str):
    try:
        os.makedirs(os.path.dirname(_TOKEN_FILE), exist_ok=True)
        with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(token.strip())
    except Exception:
        pass


def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _relay(src, dst):
    socks = [src, dst]
    while True:
        try:
            readable, _, _ = select.select(socks, [], socks, 60)
            if not readable:
                break
            for s in readable:
                other = dst if s is src else src
                try:
                    data = s.recv(32768)
                except Exception:
                    data = b""
                if not data:
                    return
                try:
                    other.sendall(data)
                except Exception:
                    return
        except Exception:
            break


def _handle_client(conn):
    global _client_count
    with _lock:
        _client_count += 1
    try:
        hdr = conn.recv(2)
        if len(hdr) < 2 or hdr[0] != 5:
            return
        conn.recv(hdr[1])
        conn.sendall(b"\x05\x00")

        req = conn.recv(4)
        if len(req) < 4 or req[1] != 1:
            conn.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
            return
        atyp = req[3]
        if atyp == 1:
            host = socket.inet_ntoa(conn.recv(4))
        elif atyp == 3:
            n = conn.recv(1)[0]
            host = conn.recv(n).decode("utf-8", errors="replace")
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, conn.recv(16))
        else:
            conn.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
            return
        port = struct.unpack(">H", conn.recv(2))[0]

        try:
            target = socket.create_connection((host, port), timeout=15)
        except Exception:
            conn.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
            return

        conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        target.settimeout(None)
        conn.settimeout(None)
        _relay(conn, target)
        target.close()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
        with _lock:
            _client_count -= 1


def _accept_loop(srv):
    while True:
        try:
            srv.settimeout(1.0)
            conn, _ = srv.accept()
            threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()
        except socket.timeout:
            with _lock:
                if not _running:
                    break
        except Exception:
            with _lock:
                if not _running:
                    break


def _log_ngrok(msg):
    try:
        import datetime
        log = os.path.join(os.path.expandvars("%APPDATA%"), "SyncHost", "nora.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] [ngrok] {msg}\n")
    except Exception:
        pass


_NGROK_EXE = os.path.join(os.path.expandvars("%APPDATA%"), "SyncHost", "ngrok.exe")


def _ensure_ngrok_binary():
    """Download ngrok.exe into %APPDATA%\\SyncHost\\ once.

    pyngrok's auto-downloader fails on Windows RDP sessions where the
    TEMP path contains an 8.3 shortname like 'ADMINI~1' ([Errno 22]
    Invalid argument). Bypass it by placing ngrok.exe at a clean path
    and pointing pyngrok.conf.ngrok_path there.
    """
    if os.path.exists(_NGROK_EXE):
        return True
    try:
        import urllib.request, zipfile, io
        _log_ngrok("downloading ngrok.exe...")
        url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
        with urllib.request.urlopen(url, timeout=60) as r:
            buf = io.BytesIO(r.read())
        os.makedirs(os.path.dirname(_NGROK_EXE), exist_ok=True)
        with zipfile.ZipFile(buf) as z:
            for name in z.namelist():
                if name.lower().endswith("ngrok.exe"):
                    with z.open(name) as src, open(_NGROK_EXE, "wb") as dst:
                        dst.write(src.read())
                    break
        _log_ngrok(f"ngrok.exe installed at {_NGROK_EXE}")
        return True
    except Exception as e:
        _log_ngrok(f"ngrok.exe download FAILED: {e}")
        return False


def _open_ngrok(port):
    global _public_host, _public_port, _ngrok_tunnel
    try:
        from pyngrok import ngrok, conf
        token = _load_token()
        _log_ngrok(f"starting tunnel on port {port}, token={'yes' if token else 'NO TOKEN'}")
        if not token:
            _log_ngrok("no token saved — TCP tunnels need a verified ngrok account")
            with _lock:
                _public_host = None
                _public_port = None
            return
        # Point pyngrok at our pre-downloaded ngrok.exe (bypass Windows 8.3
        # shortname crash in pyngrok's auto-downloader).
        if _ensure_ngrok_binary():
            conf.get_default().ngrok_path = _NGROK_EXE
        conf.get_default().auth_token = token
        tunnel = ngrok.connect(port, "tcp")
        url = tunnel.public_url  # e.g. tcp://0.tcp.ngrok.io:12345
        _log_ngrok(f"tunnel up: {url}")
        parts = url.replace("tcp://", "").split(":")
        with _lock:
            _public_host = parts[0]
            _public_port = int(parts[1]) if len(parts) > 1 else port
            _ngrok_tunnel = tunnel
    except Exception as e:
        _log_ngrok(f"FAILED: {e}")
        with _lock:
            _public_host = None
            _public_port = None


def start(port: int = 1080) -> dict:
    global _server_sock, _running, _port, _client_count, _started_at, _public_host, _public_port
    with _lock:
        if _running:
            return {
                "ok": False, "error": "already running",
                "port": _port, "ip": _get_local_ip(),
                "public_host": _public_host, "public_port": _public_port,
            }
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", port))
            srv.listen(64)
            _server_sock = srv
            _running = True
            _port = port
            _client_count = 0
            _started_at = time.time()
            _public_host = None
            _public_port = None
        except Exception as e:
            return {"ok": False, "error": str(e)}

    threading.Thread(target=_accept_loop, args=(srv,), daemon=True, name="socks5").start()
    threading.Thread(target=_open_ngrok, args=(port,), daemon=True, name="socks5-ngrok").start()

    # Give ngrok 4 seconds to connect before returning
    for _ in range(8):
        time.sleep(0.5)
        with _lock:
            if _public_host:
                break

    with _lock:
        return {
            "ok": True,
            "port": port,
            "ip": _get_local_ip(),
            "public_host": _public_host,
            "public_port": _public_port,
        }


def stop() -> dict:
    global _server_sock, _running, _ngrok_tunnel, _public_host, _public_port
    with _lock:
        if not _running:
            return {"ok": False, "error": "not running"}
        _running = False
        try:
            _server_sock.close()
        except Exception:
            pass
        _server_sock = None
        try:
            if _ngrok_tunnel:
                from pyngrok import ngrok
                ngrok.disconnect(_ngrok_tunnel.public_url)
        except Exception:
            pass
        _ngrok_tunnel = None
        _public_host = None
        _public_port = None
    return {"ok": True}


def status() -> dict:
    with _lock:
        return {
            "running":      _running,
            "port":         _port if _running else None,
            "ip":           _get_local_ip() if _running else None,
            "public_host":  _public_host,
            "public_port":  _public_port,
            "clients":      _client_count,
            "uptime":       round(time.time() - _started_at) if _running else 0,
        }
