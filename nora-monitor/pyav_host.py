"""
pyav_host.py - PyAV H.264 screen broadcaster for the Nora relay.

Drop-in alternative to the fastcap wheel. Auto-picks the best encoder:
    h264_qsv   (Intel QuickSync)
    h264_nvenc (NVIDIA)
    h264_amf   (AMD)
    libx264    (pure software — works on ANY CPU)

pip install av mss numpy "python-socketio[client]"
python pyav_host.py --relay https://YOUR_VPS:5000 --password SECRET
"""
import argparse
import ctypes
import hashlib
import json
import platform
import secrets
import socket
import struct
import sys
import threading
import time
from ctypes import wintypes as wt
from urllib.parse import urlparse

import av
import mss
import numpy as np
import socketio

# ── relay packet types ────────────────────────────────────────────────────────
T_HELLO_HOST = 1
T_VIDEO      = 3
T_INPUT      = 4
T_KEYREQ     = 5

FPS     = 30
BITRATE = 8_000_000
KEYINT  = FPS * 2
UDP_MTU = 1200

# ── Windows input injection ───────────────────────────────────────────────────
_user32 = None
if sys.platform == "win32":
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _ULONG_PTR = ctypes.c_size_t
        _LONG      = ctypes.c_long
        _DWORD     = wt.DWORD
        _WORD      = ctypes.c_uint16
        INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
        MOUSEEVENTF_MOVE       = 0x0001
        MOUSEEVENTF_ABSOLUTE   = 0x8000
        MOUSEEVENTF_VIRTUALDESK= 0x4000
        MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP   = 0x0002, 0x0004
        MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
        MOUSEEVENTF_MIDDLEDOWN,MOUSEEVENTF_MIDDLEUP= 0x0020, 0x0040
        MOUSEEVENTF_WHEEL      = 0x0800
        KEYEVENTF_KEYUP        = 0x0002
        INJECT_TAG             = 0x1EADBEEF
        MOUSE_BTN = {
            ("left", False): MOUSEEVENTF_LEFTDOWN, ("left", True): MOUSEEVENTF_LEFTUP,
            ("right",False): MOUSEEVENTF_RIGHTDOWN,("right",True): MOUSEEVENTF_RIGHTUP,
            ("middle",False):MOUSEEVENTF_MIDDLEDOWN,("middle",True):MOUSEEVENTF_MIDDLEUP,
        }

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", _LONG), ("dy", _LONG), ("mouseData", _DWORD),
                        ("dwFlags", _DWORD), ("time", _DWORD), ("dwExtraInfo", _ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", _WORD), ("wScan", _WORD), ("dwFlags", _DWORD),
                        ("time", _DWORD), ("dwExtraInfo", _ULONG_PTR)]

        class _IU(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", _DWORD), ("u", _IU)]

        _user32.SendInput.restype  = ctypes.c_uint
        _user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
    except Exception:
        _user32 = None


def _send_input(*items):
    if not _user32:
        return
    arr = (INPUT * len(items))(*items)
    _user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))


def _inject_mouse(nx, ny, flags, data=0):
    ax = max(0, min(65535, int(nx * 65535)))
    ay = max(0, min(65535, int(ny * 65535)))
    mi = MOUSEINPUT(ax, ay, data & 0xFFFFFFFF,
                    flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, INJECT_TAG)
    _send_input(INPUT(INPUT_MOUSE, _IU(mi=mi)))


def _inject_key(vk, up):
    ki = KEYBDINPUT(vk & 0xFFFF, 0, KEYEVENTF_KEYUP if up else 0, 0, INJECT_TAG)
    _send_input(INPUT(INPUT_KEYBOARD, _IU(ki=ki)))


def handle_input(payload_bytes):
    try:
        m = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return
    if not _user32:
        return
    t = m.get("t")
    if t == "mouse_move":
        _inject_mouse(m["x"], m["y"], MOUSEEVENTF_MOVE)
    elif t == "mouse_btn":
        f = MOUSE_BTN.get((m.get("button"), bool(m.get("up"))))
        if f:
            _inject_mouse(m["x"], m["y"], f)
    elif t == "wheel":
        _inject_mouse(m["x"], m["y"], MOUSEEVENTF_WHEEL, int(m.get("delta", 0)))
    elif t == "key":
        _inject_key(int(m.get("vk", 0)), bool(m.get("up")))


# ── encoder ───────────────────────────────────────────────────────────────────
HW_PIXFMT = {"h264_qsv": "nv12", "h264_nvenc": "nv12",
             "h264_amf": "nv12", "libx264": "yuv420p"}


def _build_ctx(name, w, h):
    cc = av.CodecContext.create(name, "w")
    cc.width, cc.height = w, h
    cc.pix_fmt = HW_PIXFMT[name]
    cc.framerate = av.Rational(FPS, 1)
    cc.time_base = av.Rational(1, FPS)
    cc.bit_rate  = BITRATE
    cc.gop_size  = KEYINT
    if name == "libx264":
        cc.options = {"preset": "ultrafast", "tune": "zerolatency",
                      "x264-params": f"repeat-headers=1:annexb=1:keyint={KEYINT}:scenecut=0:bframes=0"}
    else:
        cc.options = {"usage": "lowlatency", "repeat_headers": "1",
                      "rc": "cbr", "delay": "0", "bf": "0"}
    return cc


def make_encoder(w, h):
    for name in ("h264_qsv", "h264_nvenc", "h264_amf", "libx264"):
        try:
            # probe-encode one blank frame to verify initialization
            probe = _build_ctx(name, w, h)
            test  = np.zeros((h, w, 3), np.uint8)
            vf    = av.VideoFrame.from_ndarray(test, format="bgr24").reformat(format=HW_PIXFMT[name])
            vf.pts = 0
            list(probe.encode(vf))
            # fresh context — probe frame must NOT be in the real stream
            cc = _build_ctx(name, w, h)
            print(f"[enc] {name} ({'software' if name == 'libx264' else 'hardware'})")
            return cc, HW_PIXFMT[name]
        except Exception as e:
            print(f"[enc] {name} unavailable: {e}")
    raise RuntimeError("no usable H.264 encoder")


# ── host ──────────────────────────────────────────────────────────────────────
class PyAvHost:
    def __init__(self, relay_url, password):
        self.password = password
        # session matches fastcap_host.py's make_session()
        name = platform.node() or "host"
        self.session  = hashlib.sha256(name.encode()).hexdigest()[:16]
        # wire SID = ASCII bytes of first 8 chars of session hex
        self.sid      = self.session[:8].encode("ascii")
        self.sid_hex  = self.sid.hex()
        self.ag_name  = platform.node() or "PyAvHost"

        p = urlparse(relay_url)
        self._relay_addr = (p.hostname, p.port or (443 if p.scheme == "https" else 5000))
        self._relay_url  = relay_url

        self._force_idr = threading.Event()
        self._running   = True
        self._pts       = 0

        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self._udp.bind(("0.0.0.0", 0))   # bind so relay can send T_KEYREQ back

        self._sio = socketio.Client(ssl_verify=True, reconnection=True)
        self._setup_sio()

    def _setup_sio(self):
        sio = self._sio

        @sio.on("connect")
        def _on_connect():
            print("[sio] connected — registering")
            sio.emit("register", {"name": self.ag_name})
            sio.emit("fastcap_info", {
                "session":  self.session,
                "password": self.password,
            })

        @sio.on("fastcap_viewer_join")
        def _on_viewer_join(data=None):
            print("[sio] viewer joined — forcing IDR")
            self._force_idr.set()

        @sio.on("disconnect")
        def _on_disconnect():
            print("[sio] disconnected")

    def _hello_loop(self):
        pkt = bytes([T_HELLO_HOST]) + self.sid
        while self._running:
            try:
                self._udp.sendto(pkt, self._relay_addr)
            except Exception:
                pass
            time.sleep(2)

    def _udp_recv_loop(self):
        """Handle T_KEYREQ and T_INPUT from relay."""
        self._udp.settimeout(1.0)
        while self._running:
            try:
                data, _ = self._udp.recvfrom(65536)
            except socket.timeout:
                continue
            except Exception:
                break
            if len(data) < 1:
                continue
            t = data[0]
            if t == T_KEYREQ:
                self._force_idr.set()
            elif t == T_INPUT and len(data) > 17:
                handle_input(data[17:])

    def _send_frame(self, frame_id, data, is_key):
        total = max(1, (len(data) + UDP_MTU - 1) // UDP_MTU)
        for i in range(total):
            chunk = data[i * UDP_MTU:(i + 1) * UDP_MTU]
            hdr   = bytes([T_VIDEO]) + self.sid + struct.pack(">IHH", frame_id & 0xFFFFFFFF, i, total)
            try:
                self._udp.sendto(hdr + chunk, self._relay_addr)
            except Exception:
                pass

    def run(self):
        self._sio.connect(self._relay_url, transports=["websocket", "polling"])

        with mss.mss() as sct:
            mon = sct.monitors[0]
            w = mon["width"]  - (mon["width"]  % 2)
            h = mon["height"] - (mon["height"] % 2)

        print(f"[cap] {w}x{h}")
        cc, pixfmt = make_encoder(w, h)

        threading.Thread(target=self._hello_loop,  daemon=True).start()
        threading.Thread(target=self._udp_recv_loop, daemon=True).start()

        period   = 1.0 / FPS
        frame_id = 0

        with mss.mss() as sct:
            mon   = sct.monitors[0]
            mon_r = {"top": mon["top"], "left": mon["left"], "width": w, "height": h}
            while self._running:
                t0  = time.time()
                shot = sct.grab(mon_r)
                img  = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(h, w, 4)[:, :, :3]
                vf   = av.VideoFrame.from_ndarray(np.ascontiguousarray(img), format="bgr24")
                vf   = vf.reformat(format=pixfmt)
                vf.pts = self._pts
                self._pts += 1
                if self._force_idr.is_set():
                    self._force_idr.clear()
                    try:
                        vf.pict_type = av.video.frame.PictureType.I
                    except Exception:
                        pass
                try:
                    for pkt in cc.encode(vf):
                        frame_id += 1
                        self._send_frame(frame_id, bytes(pkt), bool(pkt.is_keyframe))
                except Exception as e:
                    print(f"[enc] error: {e}")
                elapsed = time.time() - t0
                if elapsed < period:
                    time.sleep(period - elapsed)


def main():
    ap = argparse.ArgumentParser(description="PyAV screen host for Nora relay")
    ap.add_argument("--relay",    required=True,  help="Relay URL e.g. http://1.2.3.4:5000")
    ap.add_argument("--password", default=None,   help="Viewer password (auto-generated if omitted)")
    args = ap.parse_args()

    pw = args.password or secrets.token_urlsafe(8)
    print(f"[host] relay={args.relay}")
    print(f"[host] password={pw}  ← enter this in the hub viewer")
    PyAvHost(args.relay, pw).run()


if __name__ == "__main__":
    main()
