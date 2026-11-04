"""
socks5_server.py — Minimal SOCKS5 (no-auth, CONNECT only) proxy server.

Start with start(); stop with stop(); query with status().
Binds to 0.0.0.0 on the given port so the LAN IP works for phone clients.
"""

import socket
import select
import threading
import struct
import time

_server_sock  = None
_server_thread = None
_running = False
_lock    = threading.Lock()
_port    = 1080
_client_count = 0
_started_at   = 0.0


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
    """Bidirectional relay between two sockets until one side closes."""
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


def _handle_client(conn, addr):
    global _client_count
    with _lock:
        _client_count += 1
    try:
        # Greeting: VER(1) NMETHODS(1) METHODS(NMETHODS)
        hdr = conn.recv(2)
        if len(hdr) < 2 or hdr[0] != 5:
            return
        nmethods = hdr[1]
        conn.recv(nmethods)
        # Reply: VER(1) METHOD(1) — 0x00 = no auth
        conn.sendall(b"\x05\x00")

        # Request: VER(1) CMD(1) RSV(1) ATYP(1) ...
        req = conn.recv(4)
        if len(req) < 4 or req[0] != 5 or req[1] != 1:  # only CONNECT
            conn.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
            return
        atyp = req[3]
        if atyp == 1:    # IPv4
            raw = conn.recv(4)
            host = socket.inet_ntoa(raw)
        elif atyp == 3:  # domain
            n = conn.recv(1)[0]
            host = conn.recv(n).decode("utf-8", errors="replace")
        elif atyp == 4:  # IPv6
            raw = conn.recv(16)
            host = socket.inet_ntop(socket.AF_INET6, raw)
        else:
            conn.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
            return
        port_raw = conn.recv(2)
        port = struct.unpack(">H", port_raw)[0]

        # Connect to target
        try:
            target = socket.create_connection((host, port), timeout=15)
        except Exception:
            conn.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
            return

        # Success reply
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
            conn, addr = srv.accept()
            t = threading.Thread(target=_handle_client, args=(conn, addr), daemon=True)
            t.start()
        except socket.timeout:
            with _lock:
                if not _running:
                    break
        except Exception:
            with _lock:
                if not _running:
                    break


def start(port: int = 1080) -> dict:
    global _server_sock, _server_thread, _running, _port, _client_count, _started_at
    with _lock:
        if _running:
            return {"ok": False, "error": "already running", "port": _port, "ip": _get_local_ip()}
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
        except Exception as e:
            return {"ok": False, "error": str(e)}

    _server_thread = threading.Thread(target=_accept_loop, args=(srv,), daemon=True, name="socks5")
    _server_thread.start()
    return {"ok": True, "port": port, "ip": _get_local_ip()}


def stop() -> dict:
    global _server_sock, _running
    with _lock:
        if not _running:
            return {"ok": False, "error": "not running"}
        _running = False
        try:
            _server_sock.close()
        except Exception:
            pass
        _server_sock = None
    return {"ok": True}


def status() -> dict:
    with _lock:
        return {
            "running":    _running,
            "port":       _port if _running else None,
            "ip":         _get_local_ip() if _running else None,
            "clients":    _client_count,
            "uptime":     round(time.time() - _started_at) if _running else 0,
        }
