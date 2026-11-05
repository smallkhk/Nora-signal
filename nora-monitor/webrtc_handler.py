"""
WebRTC screen streaming via aiortc.
An asyncio event loop runs in a background daemon thread.
All public functions are thread-safe and called from Socket.IO handlers.
"""
import asyncio
import fractions
import logging
import threading
import time

_log = logging.getLogger("webrtc")
import json as _json

try:
    import av
    import numpy as np
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc import RTCConfiguration, RTCIceServer
    from aiortc.mediastreams import MediaStreamTrack
    _OK = True
except ImportError:
    _OK = False

# ── Asyncio event loop in a dedicated daemon thread ───────────────────────────

_loop: asyncio.AbstractEventLoop = None
_ready = threading.Event()


def _run_loop():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _ready.set()
    _loop.run_forever()


_loop_thread = threading.Thread(target=_run_loop, daemon=True, name="webrtc-loop")
_loop_thread.start()
_ready.wait(timeout=5)


def _submit(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop)


# ── Per-track queues and shared last frame ────────────────────────────────────
# All accesses to _track_queues happen inside the asyncio event loop thread
# (ScreenVideoTrack.__init__, stop(), and _put()) so no lock is needed.

_track_queues: dict = {}   # id(track) -> asyncio.Queue
_current_frame = None      # most recent av.VideoFrame in yuv420p


# ── Per-connection video track ────────────────────────────────────────────────

class ScreenVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self):
        super().__init__()
        self._queue = asyncio.Queue(maxsize=3)
        self._start_ts: float = None
        _track_queues[id(self)] = self._queue

    def stop(self):
        _track_queues.pop(id(self), None)
        super().stop()

    async def recv(self):
        global _current_frame
        try:
            f = await asyncio.wait_for(self._queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            # Return last frame (or blank) on static screen
            f = _current_frame
            if f is None:
                f = av.VideoFrame(width=1280, height=720, format="yuv420p")

        if self._start_ts is None:
            self._start_ts = time.monotonic()

        elapsed = time.monotonic() - self._start_ts
        out = f.copy()
        out.pts = int(elapsed * 90000)
        out.time_base = fractions.Fraction(1, 90000)
        return out


# ── RC handler (set via init()) ───────────────────────────────────────────────

_rc_handler = None


def init(rc_handler):
    """Register the RC command handler called when data channel messages arrive."""
    global _rc_handler
    _rc_handler = rc_handler


# ── Peer connection pool ──────────────────────────────────────────────────────

_peers: dict = {}   # sid -> RTCPeerConnection

_STUN_CFG = RTCConfiguration(iceServers=[
    RTCIceServer(urls=["stun:stun.l.google.com:19302",
                       "stun:stun1.l.google.com:19302"]),
    # TURN relay (self-hosted on the nora relay VPS). Required when either
    # end has symmetric NAT that blocks direct peer-to-peer UDP.
    RTCIceServer(urls=["turn:162.35.37.67:3478?transport=udp",
                       "turn:162.35.37.67:3478?transport=tcp"],
                 username="nora",
                 credential="noraturn42"),
])


async def _do_create_offer(sid: str) -> RTCSessionDescription:
    pc = RTCPeerConnection(configuration=_STUN_CFG)
    _peers[sid] = pc

    pc.addTrack(ScreenVideoTrack())

    dc = pc.createDataChannel("rc", ordered=False, maxRetransmits=0)

    @dc.on("message")
    def _on_dc_msg(message):
        if _rc_handler is None:
            return
        try:
            _rc_handler(_json.loads(message))
        except Exception:
            pass

    @pc.on("connectionstatechange")
    async def _on_conn_state():
        if pc.connectionState in ("failed", "closed"):
            _peers.pop(sid, None)
            try:
                await pc.close()
            except Exception:
                pass

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Vanilla ICE: wait until all candidates are gathered (max 5 s)
    if pc.iceGatheringState != "complete":
        done = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def _on_ice_state():
            if pc.iceGatheringState == "complete":
                done.set()

        try:
            await asyncio.wait_for(done.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

    return pc.localDescription


async def _do_set_answer(sid: str, sdp: str, type_: str) -> None:
    pc = _peers.get(sid)
    if pc:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=type_))


async def _do_close(sid: str) -> None:
    pc = _peers.pop(sid, None)
    if pc:
        try:
            await pc.close()
        except Exception:
            pass


# ── Frame push (called from capture thread) ───────────────────────────────────

def push_bgr(bgr_ndarray):
    """Push a BGR numpy frame. No-op when no peers are connected."""
    if not _OK or not _peers or _loop is None:
        return
    try:
        # np.ascontiguousarray is required: the [::-1] slice produces a
        # negative-stride view that av / memoryview reject with ValueError.
        rgb = np.ascontiguousarray(bgr_ndarray[:, :, ::-1])
        f = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        f = f.reformat(format="yuv420p")
    except Exception:
        return
    _distribute_frame(f)


def push_rgb(rgb_ndarray):
    """Push an RGB numpy frame. No-op when no peers are connected."""
    if not _OK or not _peers or _loop is None:
        return
    try:
        rgb = np.ascontiguousarray(rgb_ndarray)
        f = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        f = f.reformat(format="yuv420p")
    except Exception:
        return
    _distribute_frame(f)


def _distribute_frame(av_frame):
    global _current_frame
    _current_frame = av_frame

    async def _put():
        for q in list(_track_queues.values()):
            if q.full():
                try:
                    q.get_nowait()
                except Exception:
                    pass
            try:
                q.put_nowait(av_frame)
            except Exception:
                pass

    asyncio.run_coroutine_threadsafe(_put(), _loop)


# ── Public API ────────────────────────────────────────────────────────────────

def available() -> bool:
    return _OK


def create_offer(sid: str, callback) -> None:
    if not _OK:
        callback(None, None)
        return

    async def _do():
        try:
            desc = await _do_create_offer(sid)
            callback(desc.sdp, desc.type)
        except Exception as exc:
            _log.error("create_offer: %s", exc)
            callback(None, None)

    _submit(_do())


def set_answer(sid: str, sdp: str, type_: str = "answer") -> None:
    if _OK:
        _submit(_do_set_answer(sid, sdp, type_))


def close(sid: str) -> None:
    if _OK:
        _submit(_do_close(sid))
