"""
UDP diagnostic for fastcap relay.
Run on the PC: python test_udp.py
"""

import socket
import json
import struct
import time
import urllib.request

RELAY_HOST = "162.35.37.67"
RELAY_TCP  = 5000
RELAY_UDP  = 5000

T_HELLO_HOST = 1
T_VIDEO      = 3
T_PING       = 6

def http_get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

def make_session_bytes(session_hex):
    # relay accepts both raw hex and ASCII-encoded first 8 chars
    try:
        return bytes.fromhex(session_hex[:16])
    except Exception:
        return b"\x00" * 8

print("=" * 55)
print("  Fastcap UDP Diagnostic")
print("=" * 55)

# ── Step 1: get session from relay ────────────────────────
print(f"\n[1] Fetching session info from relay http://{RELAY_HOST}:{RELAY_TCP}/api/fc_pw ...")
info = http_get(f"http://{RELAY_HOST}:{RELAY_TCP}/api/fc_pw")
if "error" in info:
    print(f"    FAIL — cannot reach relay HTTP: {info['error']}")
    print("    → The relay server may be down or TCP 5000 is blocked.")
    exit(1)

session_hex = info.get("session", "")
agent       = info.get("agent", "?")
print(f"    OK   — agent={agent}  session={session_hex[:16]}...")

if not session_hex:
    print("    FAIL — no fastcap session registered. Is SyncHost running?")
    exit(1)

sid = make_session_bytes(session_hex)

# ── Step 2: send HELLO_HOST via UDP ──────────────────────
print(f"\n[2] Sending T_HELLO_HOST UDP → {RELAY_HOST}:{RELAY_UDP} ...")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(3)
pkt = bytes([T_HELLO_HOST]) + sid + b"\x00" * 8   # type + session + dummy token
try:
    sock.sendto(pkt, (RELAY_HOST, RELAY_UDP))
    print("    OK   — packet sent")
except Exception as e:
    print(f"    FAIL — sendto error: {e}")
    print("    → UDP outbound from this PC may be blocked by firewall.")
    exit(1)

time.sleep(0.5)

# ── Step 3: check relay status ────────────────────────────
print(f"\n[3] Checking relay status ...")
status = http_get(f"http://{RELAY_HOST}:{RELAY_TCP}/api/status")
fc_sessions = status.get("fc_sessions", {})

sid_hex = sid.hex()
found = None
for k, v in fc_sessions.items():
    if k == sid_hex or k.startswith(sid_hex[:8]):
        found = v
        break

if found:
    alive = found.get("host_alive", False)
    last  = found.get("host_last_seen")
    print(f"    OK   — relay sees this session. host_alive={alive}  last_seen={last}s ago")
    if alive:
        print("\n  RESULT: UDP PC → relay is WORKING.")
        print("  Problem is likely in the viewer side (NoraViewer) or SID mismatch.")
    else:
        print("\n  RESULT: Relay got the packet but host marked NOT alive.")
        print("  Possible: SID mismatch between fastcap_host and what relay expects.")
else:
    print(f"    FAIL — relay has no record of session {sid_hex[:16]}...")
    print("\n  RESULT: UDP packets from PC are NOT reaching the relay.")
    print("  → UDP port 5000 outbound is blocked (router/ISP/firewall).")

# ── Step 4: send a fake video frame ──────────────────────
print(f"\n[4] Sending fake T_VIDEO packet ...")
fake_frame = b"\x00\x00\x01\x67" + b"\x00" * 100  # fake H.264 SPS NAL
fid = struct.pack(">I", 1)
idx = struct.pack(">H", 0)
cnt = struct.pack(">H", 1)
vpkt = bytes([T_VIDEO]) + sid + b"\x00"*8 + fid + idx + cnt + fake_frame
try:
    sock.sendto(vpkt, (RELAY_HOST, RELAY_UDP))
    print("    OK   — fake video packet sent")
except Exception as e:
    print(f"    FAIL — {e}")

sock.close()
print("\nDone.")
