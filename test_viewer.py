"""
NoraViewer UDP diagnostic — run on VPS (Linux) terminal.
Tests whether the relay correctly authenticates a viewer and routes video.

Usage:
    python3 test_viewer.py
"""

import socket
import json
import hashlib
import struct
import time
import urllib.request

RELAY_HOST = "162.35.37.67"
RELAY_PORT = 5000

T_HELLO_HOST = 1
T_HELLO_VIEW = 2
T_VIDEO      = 3
T_PING       = 6


def http_get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


print("=" * 55)
print("  NoraViewer UDP Diagnostic  (run on VPS)")
print("=" * 55)

# ── Step 1: get session + password from relay ─────────────
print(f"\n[1] Fetching fastcap info from http://{RELAY_HOST}:{RELAY_PORT}/api/fc_pw ...")
info = http_get(f"http://{RELAY_HOST}:{RELAY_PORT}/api/fc_pw")
if "error" in info:
    print(f"    FAIL — {info['error']}")
    exit(1)

session_full = info.get("session", "")
password     = info.get("password", "")
agent        = info.get("agent", "?")

if not session_full or not password:
    print("    FAIL — missing session or password. Is SyncHost running on the PC?")
    exit(1)

print(f"    OK   — agent={agent}  session={session_full[:16]}...")

# ── Step 2: compute SID and token ────────────────────────
# fastcap.Host() sends SID as ASCII bytes of first 8 chars of session string
sid = session_full[:8].encode("ascii")
sid_hex = sid.hex()   # e.g. "3939636664626662"

# Token = sha256(password + sid_hex).digest()[:8]
token = hashlib.sha256(password.encode() + sid_hex.encode()).digest()[:8]

print(f"\n[2] Computed SID (ASCII wire encoding): {sid_hex}")
print(f"    Token (first 8 bytes of sha256):     {token.hex()}")

# ── Step 3: send T_HELLO_VIEW ────────────────────────────
print(f"\n[3] Sending T_HELLO_VIEW → {RELAY_HOST}:{RELAY_PORT} ...")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(4)

pkt = bytes([T_HELLO_VIEW]) + sid + token   # 1 + 8 + 8 = 17 bytes
try:
    sock.sendto(pkt, (RELAY_HOST, RELAY_PORT))
    print("    OK   — packet sent")
except Exception as e:
    print(f"    FAIL — sendto: {e}")
    exit(1)

# ── Step 4: wait for T_HELLO_VIEW ack ────────────────────
print(f"\n[4] Waiting for T_HELLO_VIEW ack from relay ...")
ack_ok = False
try:
    data, addr = sock.recvfrom(256)
    if len(data) >= 1 and data[0] == T_HELLO_VIEW:
        ack_ok = True
        print(f"    OK   — relay ACK'd the viewer auth! (from {addr})")
    else:
        print(f"    WARN — got unexpected packet type={data[0]} len={len(data)}")
except socket.timeout:
    print("    FAIL — no reply within 4 seconds (token mismatch or relay not receiving?)")

# ── Step 5: check status — did viewer_count go up? ───────
print(f"\n[5] Checking relay /api/status for viewer registration ...")
time.sleep(0.3)
status = http_get(f"http://{RELAY_HOST}:{RELAY_PORT}/api/status")
fc_sessions = status.get("fc_sessions", {})
found = None
for k, v in fc_sessions.items():
    if k == sid_hex or k.startswith(sid_hex[:8]):
        found = (k, v)
        break

if found:
    k, v = found
    vcnt  = v.get("viewer_count", 0)
    alive = v.get("host_alive", False)
    print(f"    OK   — session {k[:16]}...  host_alive={alive}  viewer_count={vcnt}")
    if vcnt > 0:
        print("    Relay REGISTERED this VPS as a viewer.")
    else:
        print("    Relay found session but viewer_count=0 — ACK packet may have been lost.")
else:
    print(f"    FAIL — relay has no record of session SID {sid_hex[:16]}...")

# ── Step 6: listen for a video frame ─────────────────────
print(f"\n[6] Listening for T_VIDEO frames for 6 seconds ...")
sock.settimeout(1)
frames = 0
t_end = time.time() + 6
while time.time() < t_end:
    try:
        data, addr = sock.recvfrom(65536)
        if len(data) >= 1:
            t = data[0]
            if t == T_VIDEO:
                frames += 1
                if frames == 1:
                    print(f"    GOT first video frame!  len={len(data)}  from {addr}")
            elif t == T_PING:
                pass  # keepalive, ignore
    except socket.timeout:
        pass

sock.close()

# ── Summary ───────────────────────────────────────────────
print(f"\n{'='*55}")
if ack_ok and frames > 0:
    print(f"  RESULT: PASS — viewer auth OK, got {frames} video frame(s).")
    print("  NoraViewer should work. Download and test it now.")
elif ack_ok and frames == 0:
    print(f"  RESULT: PARTIAL — viewer auth OK but NO video received.")
    print("  PC may not be streaming. Check SyncHost is active and hub is open.")
elif not ack_ok:
    print(f"  RESULT: FAIL — viewer auth rejected or relay did not reply.")
    print("  Possible causes:")
    print("    • Token mismatch (SID encoding bug)")
    print("    • Relay not updated — run: systemctl status nora-relay")
    print("    • UDP port 5000 blocked on VPS (check: ufw status)")
print(f"{'='*55}")
