"""
Standalone WebRTC loopback test.

Runs a self-contained aiortc + mss capture + tiny HTTP server on
http://127.0.0.1:8899/. Open the URL in any browser on the same machine
(or forward the port via RDP tunnel), click "Start", and you should see
your desktop streamed over WebRTC within 1-2 seconds.

If this works: aiortc + mss capture + H.264 / VP8 encoding works on this
machine, so any WebRTC failures elsewhere are in signaling or iOS code.
If this fails: aiortc or screen capture is broken in this environment
(e.g. RDP session limitations) and WebRTC won't work at all here.

Usage (PowerShell):
    pip install aiortc aiohttp mss av numpy
    python webrtc_test.py
"""
import asyncio
import fractions
import json
import sys
import time
from pathlib import Path

try:
    from aiohttp import web
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
    from aiortc.mediastreams import VideoStreamTrack
    import mss
    import numpy as np
    from av import VideoFrame
except ImportError as e:
    print(f"ERROR: missing dep: {e}")
    print("Install: pip install aiortc aiohttp mss av numpy")
    sys.exit(1)


HTML = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>WebRTC Loopback Test</title>
<style>
body { font-family: -apple-system, system-ui, sans-serif; margin: 20px; background: #111; color: #eee }
video { width: 100%; max-width: 1200px; background: #000; border: 1px solid #333 }
button { padding: 10px 20px; font-size: 16px; cursor: pointer; margin: 10px 0 }
#log { white-space: pre-wrap; font-family: SF Mono, Menlo, monospace;
       font-size: 12px; background: #000; padding: 10px; max-height: 300px; overflow: auto }
.ok { color: #4ade80 } .err { color: #f87171 }
</style>
</head>
<body>
<h1>WebRTC Loopback Test</h1>
<button id="btn">Start</button>
<div><video id="v" autoplay playsinline muted></video></div>
<h3>Log</h3>
<div id="log"></div>
<script>
const log = (msg, cls="") => {
  const el = document.getElementById("log");
  const line = document.createElement("div");
  if (cls) line.className = cls;
  line.textContent = new Date().toLocaleTimeString() + " " + msg;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
};
document.getElementById("btn").addEventListener("click", async () => {
  log("Creating peer connection…");
  const pc = new RTCPeerConnection({
    iceServers: [{urls: ["stun:stun.l.google.com:19302"]}]
  });
  pc.ontrack = (e) => {
    log("remote track received", "ok");
    document.getElementById("v").srcObject = e.streams[0];
  };
  pc.oniceconnectionstatechange = () => log("ice state: " + pc.iceConnectionState);
  pc.onconnectionstatechange = () => log("conn state: " + pc.connectionState);

  pc.addTransceiver("video", {direction: "recvonly"});

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  log("offer created, waiting for ICE gather…");

  await new Promise((res) => {
    if (pc.iceGatheringState === "complete") return res();
    const t = setTimeout(res, 5000);
    pc.onicegatheringstatechange = () => {
      if (pc.iceGatheringState === "complete") { clearTimeout(t); res(); }
    };
  });

  log("POST /offer");
  const r = await fetch("/offer", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({sdp: pc.localDescription.sdp, type: pc.localDescription.type})
  });
  if (!r.ok) { log("server error " + r.status, "err"); return; }
  const answer = await r.json();
  await pc.setRemoteDescription(answer);
  log("answer applied, negotiating…", "ok");
});
</script>
</body>
</html>"""


class ScreenTrack(VideoStreamTrack):
    """Grabs the primary monitor via mss and emits frames at the configured fps."""

    kind = "video"

    def __init__(self, fps: int = 15, scale: float = 0.75):
        super().__init__()
        self._fps = fps
        self._interval = 1.0 / fps
        self._scale = scale
        self._sct = mss.mss()
        self._mon = self._sct.monitors[1]  # primary monitor
        self._start = None
        self._frame_count = 0
        print(f"[ScreenTrack] monitor={self._mon}  fps={fps}  scale={scale}")

    async def recv(self):
        if self._start is None:
            self._start = time.monotonic()
        # Pace to self._fps
        target = self._start + self._frame_count * self._interval
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._frame_count += 1

        shot = self._sct.grab(self._mon)
        bgra = np.array(shot)
        # Down-scale
        if self._scale != 1.0:
            h, w = bgra.shape[:2]
            import cv2 as _cv2
            bgra = _cv2.resize(bgra, (int(w * self._scale), int(h * self._scale)),
                                interpolation=_cv2.INTER_LINEAR)
        rgb = bgra[:, :, :3][:, :, ::-1]  # BGRA → RGB
        frame = VideoFrame.from_ndarray(np.ascontiguousarray(rgb), format="rgb24")
        frame.pts = self._frame_count
        frame.time_base = fractions.Fraction(1, self._fps)
        if self._frame_count % 30 == 1:
            print(f"[ScreenTrack] sent frame #{self._frame_count} ({rgb.shape[1]}x{rgb.shape[0]})")
        return frame


pcs = set()


async def offer(request):
    data = await request.json()
    print(f"[server] /offer received ({len(data['sdp'])} bytes)")

    cfg = RTCConfiguration(iceServers=[
        RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
    ])
    pc = RTCPeerConnection(configuration=cfg)
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_state():
        print(f"[server] connection state: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed"):
            pcs.discard(pc)

    try:
        track = ScreenTrack(fps=15, scale=0.75)
    except Exception as e:
        print(f"[server] ScreenTrack init FAILED: {e}")
        return web.json_response({"error": f"screen capture failed: {e}"}, status=500)

    pc.addTrack(track)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type=data["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    print("[server] answer sent")
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


async def index(request):
    return web.Response(text=HTML, content_type="text/html")


async def main():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8899)
    await site.start()

    print()
    print("═" * 60)
    print("  WebRTC loopback test server running")
    print("═" * 60)
    print()
    print("  Open in a browser on this machine:")
    print("     http://127.0.0.1:8899/")
    print()
    print("  Click 'Start' — you should see this desktop within 1-2s.")
    print()
    print("  Logs below show each frame + ICE / connection state.")
    print("  Press Ctrl+C to stop.")
    print("═" * 60)
    print()

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        for pc in list(pcs):
            await pc.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
