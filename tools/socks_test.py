"""
Standalone SOCKS5-over-ngrok tester.

Place next to ngrok.token in %APPDATA%\\SyncHost\\ and run with:
    python socks_test.py

Prints the public ngrok TCP endpoint + username/password to use in any
SOCKS5 client (Firefox, Proxifier, curl --socks5-hostname …).

Requires: pip install pyngrok
"""

import os
import socket
import struct
import select
import secrets
import string
import threading
import socketserver
import sys

# ── Config ────────────────────────────────────────────────────────────────────

APP_DIR     = os.path.join(os.path.expandvars("%APPDATA%"), "SyncHost")
TOKEN_FILE  = os.path.join(APP_DIR, "ngrok.token")
LOCAL_PORT  = 1080
BUF         = 8192
SOCKS_USER  = "nora"
SOCKS_PASS  = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))


# ── Minimal SOCKS5 server ─────────────────────────────────────────────────────

class Socks5Handler(socketserver.BaseRequestHandler):

    def handle(self):
        c = self.request
        try:
            # 1. Greeting
            hdr = c.recv(2)
            if len(hdr) < 2 or hdr[0] != 0x05:
                return
            nmeth = hdr[1]
            methods = c.recv(nmeth)
            if 0x02 not in methods:
                c.sendall(b"\x05\xff")  # no acceptable methods
                return
            c.sendall(b"\x05\x02")  # username/password

            # 2. Username/password auth (RFC 1929)
            ver = c.recv(1)
            if not ver or ver[0] != 0x01:
                return
            ulen = c.recv(1)[0]
            user = c.recv(ulen).decode("utf-8", "replace")
            plen = c.recv(1)[0]
            pwd  = c.recv(plen).decode("utf-8", "replace")
            if user != SOCKS_USER or pwd != SOCKS_PASS:
                c.sendall(b"\x01\x01")  # fail
                return
            c.sendall(b"\x01\x00")  # ok

            # 3. Request
            req = c.recv(4)
            if len(req) < 4 or req[0] != 0x05 or req[1] != 0x01:
                c.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)  # cmd not supported
                return
            atyp = req[3]
            if atyp == 0x01:  # IPv4
                addr = socket.inet_ntoa(c.recv(4))
            elif atyp == 0x03:  # domain
                dlen = c.recv(1)[0]
                addr = c.recv(dlen).decode("ascii", "replace")
            elif atyp == 0x04:  # IPv6
                addr = socket.inet_ntop(socket.AF_INET6, c.recv(16))
            else:
                c.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
                return
            port = struct.unpack(">H", c.recv(2))[0]

            # 4. Connect to target
            try:
                remote = socket.create_connection((addr, port), timeout=10)
            except Exception:
                c.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)  # connection refused
                return

            # 5. Reply success
            local_ip, local_port = remote.getsockname()[:2]
            c.sendall(b"\x05\x00\x00\x01" + socket.inet_aton(local_ip) +
                      struct.pack(">H", local_port))

            # 6. Pump data both ways
            self._pump(c, remote)
        except Exception as e:
            print(f"  handler error: {e}", flush=True)
        finally:
            try: c.close()
            except Exception: pass

    @staticmethod
    def _pump(a, b):
        try:
            while True:
                r, _, _ = select.select([a, b], [], [], 60)
                if not r:
                    return
                for s in r:
                    other = b if s is a else a
                    try:
                        data = s.recv(BUF)
                    except Exception:
                        return
                    if not data:
                        return
                    try:
                        other.sendall(data)
                    except Exception:
                        return
        finally:
            for s in (a, b):
                try: s.close()
                except Exception: pass


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # 1. Load ngrok token
    if not os.path.exists(TOKEN_FILE):
        print(f"ERROR: {TOKEN_FILE} not found. Put your ngrok auth token there.")
        sys.exit(1)
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    if not token:
        print(f"ERROR: {TOKEN_FILE} is empty.")
        sys.exit(1)

    # 2. Start local SOCKS5 server in a thread
    server = ThreadedServer(("127.0.0.1", LOCAL_PORT), Socks5Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[+] SOCKS5 listening locally on 127.0.0.1:{LOCAL_PORT}")

    # 3. Start ngrok TCP tunnel
    try:
        from pyngrok import ngrok, conf
    except ImportError:
        print("ERROR: pyngrok not installed. Run: pip install pyngrok")
        sys.exit(1)

    conf.get_default().auth_token = token
    try:
        tunnel = ngrok.connect(LOCAL_PORT, "tcp")
    except Exception as e:
        print(f"ERROR: ngrok connect failed: {e}")
        sys.exit(1)

    url = tunnel.public_url  # e.g. tcp://0.tcp.ngrok.io:12345
    hostport = url.replace("tcp://", "")
    host, port = hostport.rsplit(":", 1)

    print()
    print("═" * 60)
    print(" SOCKS5 proxy READY")
    print("═" * 60)
    print(f"  Host:     {host}")
    print(f"  Port:     {port}")
    print(f"  Username: {SOCKS_USER}")
    print(f"  Password: {SOCKS_PASS}")
    print()
    print(" Test with curl:")
    print(f"   curl --socks5-hostname {SOCKS_USER}:{SOCKS_PASS}@{hostport} https://ifconfig.me")
    print()
    print(" Or in Firefox: Settings → Network → Manual proxy config")
    print(f"   SOCKS Host: {host}  Port: {port}  SOCKS v5  Proxy DNS: on")
    print("═" * 60)
    print()
    print(" Press Ctrl+C to stop.")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\n[+] Stopping...")
        ngrok.disconnect(tunnel.public_url)
        ngrok.kill()
        server.shutdown()


if __name__ == "__main__":
    main()
