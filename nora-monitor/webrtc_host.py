"""
webrtc_host.py - WebRTC HOST (runs on the Windows machine being viewed/controlled).

Captures the screen -> WebRTC video track (aiortc negotiates H.264/VP8, adaptive
bitrate + loss recovery built in), and receives mouse/keyboard over a datachannel
-> SendInput. Works against a browser hub OR the iOS app.

    pip install aiortc av mss numpy websockets
    python webrtc_host.py --signal ws://162.35.37.67:5001 --password testpass123

IMPORTANT: on a bad/mobile network you MUST have a TURN server or you'll get the
"connects but no frames" problem. Fill TURN_* below (run coturn on your VPS).
"""
import argparse
import asyncio
import ctypes
import fractions
import hashlib
import socket
import sys
from ctypes import wintypes as wt

import numpy as np
import mss
import av
import websockets
from aiortc import (RTCPeerConnection, RTCConfiguration, RTCIceServer,
                    RTCSessionDescription, VideoStreamTrack)

# --- ICE: STUN is enough on good networks; TURN is REQUIRED on NAT/mobile -------
STUN_URL = "stun:stun.l.google.com:19302"
TURN_URL = "turn:162.35.37.67:3478"     # <-- your coturn; set to None to disable
TURN_USER = "nora"
TURN_PASS = "noraturn42"
FPS = 20

# ------------------------------------------------------------- SendInput (input)
user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
ULONG_PTR = ctypes.c_size_t
LONG = ctypes.c_long
DWORD = wt.DWORD if sys.platform == "win32" else ctypes.c_uint32
WORD = ctypes.c_uint16
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class _IU(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("u", _IU)]


if user32:
    user32.SendInput.restype = ctypes.c_uint
    user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]


def _send(*items):
    if not user32:
        return
    arr = (INPUT * len(items))(*items)
    user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))


def inject_mouse(nx, ny, flags, data=0):
    ax = max(0, min(65535, int(nx * 65535)))
    ay = max(0, min(65535, int(ny * 65535)))
    mi = MOUSEINPUT(ax, ay, data & 0xFFFFFFFF,
                    flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0)
    _send(INPUT(INPUT_MOUSE, _IU(mi=mi)))


def inject_key(vk, up):
    ki = KEYBDINPUT(vk & 0xFFFF, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    _send(INPUT(INPUT_KEYBOARD, _IU(ki=ki)))


MOUSE_BTN = {
    ("left", False): MOUSEEVENTF_LEFTDOWN, ("left", True): MOUSEEVENTF_LEFTUP,
    ("right", False): MOUSEEVENTF_RIGHTDOWN, ("right", True): MOUSEEVENTF_RIGHTUP,
    ("middle", False): MOUSEEVENTF_MIDDLEDOWN, ("middle", True): MOUSEEVENTF_MIDDLEUP,
}


def handle_input(m):
    t = m.get("t")
    if t == "mouse_move":
        inject_mouse(m["x"], m["y"], MOUSEEVENTF_MOVE)
    elif t == "mouse_btn":
        # move to the point first so the click lands exactly at the cursor
        inject_mouse(m["x"], m["y"], MOUSEEVENTF_MOVE)
        f = MOUSE_BTN.get((m["button"], m["up"]))
        if f is not None:
            inject_mouse(m["x"], m["y"], f)
    elif t == "wheel":
        inject_mouse(m["x"], m["y"], MOUSEEVENTF_WHEEL, m["delta"])
    elif t == "key":
        inject_key(m["vk"], m["up"])


# ----------------------------------------------------------------- screen track
class ScreenTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self._sct = mss.mss()
        self._mon = self._sct.monitors[0]

    def _grab(self):
        shot = self._sct.grab(self._mon)
        arr = np.asarray(shot)[:, :, :3]          # BGRA -> BGR
        h, w = arr.shape[0], arr.shape[1]
        return np.ascontiguousarray(arr[:h - h % 2, :w - w % 2])

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        loop = asyncio.get_event_loop()
        img = await loop.run_in_executor(None, self._grab)
        frame = av.VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame


def ice_servers():
    servers = [RTCIceServer(urls=[STUN_URL])]
    if TURN_URL:
        servers.append(RTCIceServer(urls=[TURN_URL], username=TURN_USER, credential=TURN_PASS))
    return servers


async def wait_ice(pc):
    if pc.iceGatheringState == "complete":
        return
    fut = asyncio.get_event_loop().create_future()

    @pc.on("icegatheringstatechange")
    def _():
        if pc.iceGatheringState == "complete" and not fut.done():
            fut.set_result(None)
    if pc.iceGatheringState == "complete" and not fut.done():
        fut.set_result(None)
    await fut


PCS = set()


async def make_pc(ws):
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers()))
    PCS.add(pc)
    pc.addTrack(ScreenTrack())
    ch = pc.createDataChannel("input")

    @ch.on("message")
    def on_message(msg):
        try:
            import json
            handle_input(json.loads(msg))
        except Exception:
            pass

    @pc.on("connectionstatechange")
    async def _():
        print("[pc]", pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            PCS.discard(pc)

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await wait_ice(pc)
    import json
    await ws.send(json.dumps({"type": "offer", "sdp": pc.localDescription.sdp}))
    print("[pc] offer sent")
    return pc


async def run(signal_url, session, password):
    import json
    async with websockets.connect(signal_url, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "join", "role": "host",
                                  "session": session, "password": password}))
        print(f"[host] joined session={session}  pw={password}")
        pc = None
        async for raw in ws:
            m = json.loads(raw)
            t = m.get("type")
            if t == "viewer-join":
                print("[host] viewer joined -> offering")
                pc = await make_pc(ws)
            elif t == "answer" and pc:
                await pc.setRemoteDescription(RTCSessionDescription(m["sdp"], "answer"))
                print("[host] answer set")
            elif t == "error":
                print("[host] signaling error:", m.get("msg"))


def make_session():
    return hashlib.sha256(socket.gethostname().encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signal", default="ws://162.35.37.67:5001")
    ap.add_argument("--password", required=True)
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    session = args.session or make_session()
    print(f"[host] signal={args.signal}")
    print(f"[host] session={session}  (enter this + password in the viewer)")
    try:
        asyncio.run(run(args.signal, session, args.password))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
