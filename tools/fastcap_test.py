"""
Standalone fastcap.Host tester.

Lets you try different monitor indexes / resolutions without rebuilding
SyncHost, to figure out which combination actually produces T_VIDEO
packets on the relay.

Usage (PowerShell):
    python fastcap_test.py              # monitor=0, res=720 (default)
    python fastcap_test.py --monitor 1
    python fastcap_test.py --monitor 0 --res 1080 --fps 15

Reads session + password from %APPDATA%\\SyncHost\\fastcap.pw
(auto-generates a fresh session if the file is missing).

Run on the PC while watching VPS logs:
    sudo journalctl -u nora-relay -f | grep -E "udp_rx|fc_vid"

If you see [udp_rx] type=VIDEO lines appear, that monitor value works.
"""
import argparse
import hashlib
import os
import platform
import secrets
import sys
import time

APP_DIR = os.path.join(os.path.expandvars("%APPDATA%"), "SyncHost")
PW_FILE = os.path.join(APP_DIR, "fastcap.pw")
RELAY   = os.environ.get("FASTCAP_RELAY", "162.35.37.67:5000")


def _session():
    name = platform.node() or "host"
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def _password():
    if os.path.exists(PW_FILE):
        with open(PW_FILE) as f:
            return f.read().strip()
    pw = secrets.token_urlsafe(8)
    os.makedirs(APP_DIR, exist_ok=True)
    with open(PW_FILE, "w") as f:
        f.write(pw)
    return pw


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--monitor", type=int, default=0)
    p.add_argument("--res",     type=int, default=720)
    p.add_argument("--fps",     type=int, default=30)
    p.add_argument("--bitrate", type=int, default=4000)
    p.add_argument("--seconds", type=int, default=60, help="how long to stream")
    args = p.parse_args()

    try:
        import fastcap
    except ImportError:
        print("ERROR: fastcap not installed. Install the wheel first:")
        print("  pip install fastcap-1.0.0-cp39-abi3-win_amd64.whl")
        sys.exit(1)

    sess = _session()
    pw   = _password()

    print(f"[+] relay    = {RELAY}")
    print(f"[+] session  = {sess}  (wire SID = ascii first 8 chars)")
    print(f"[+] password = {pw}")
    print(f"[+] monitor  = {args.monitor}")
    print(f"[+] res      = {args.res}")
    print(f"[+] fps      = {args.fps}")
    print(f"[+] bitrate  = {args.bitrate}")
    print()

    try:
        h = fastcap.Host(RELAY, sess, pw, args.fps, args.bitrate,
                         args.monitor, args.res)
        h.start()
        print("[+] Host.start() returned OK")
        try:
            h.viewer_connected()
            print("[+] Host.viewer_connected() returned OK")
        except Exception as e:
            print(f"[!] viewer_connected() raised: {e}")
    except Exception as e:
        print(f"[!] Host construction/start failed: {e}")
        sys.exit(1)

    print()
    print(f"[+] Streaming for {args.seconds}s… watch VPS logs now:")
    print(f"      sudo journalctl -u nora-relay -f | grep udp_rx")
    print()
    print("    If you see 'type=VIDEO' lines, this monitor index works.")
    print("    If only 'type=HELLO_HOST' lines, try --monitor 1 or run")
    print("    this on a non-RDP console session.")
    print()

    try:
        for i in range(args.seconds):
            time.sleep(1)
            if i % 10 == 9:
                print(f"    [{i+1}s elapsed, still streaming]")
    except KeyboardInterrupt:
        pass

    print("\n[+] Stopping Host...")
    try:
        h.stop()
    except Exception:
        pass
    print("[+] Done.")


if __name__ == "__main__":
    main()
