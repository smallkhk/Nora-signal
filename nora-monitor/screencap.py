import base64
import hashlib
import threading
import time

import mss

try:
    import cv2
    import numpy as np
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

try:
    from PIL import Image, ImageDraw
    import io as _io
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

try:
    import win32gui as _w32g
    _W32_OK = True
except ImportError:
    _W32_OK = False


# ── Cursor drawing ────────────────────────────────────────────────────────────

def _cursor_pos(scale):
    """Return (cx, cy) scaled cursor position, or None if hidden."""
    if not _W32_OK:
        return None
    try:
        flags, _hcursor, (cx, cy) = _w32g.GetCursorInfo()
        if not (flags & 0x1):
            return None
        return int(cx * scale), int(cy * scale)
    except Exception:
        return None


# Arrow cursor polygon (normalised to 24px height; scaled at draw time)
_ARROW_PTS_NORM = [
    (0, 0), (0, 20), (5, 14), (8, 20), (11, 18),
    (8, 12), (14, 12),
]

def _draw_cursor_cv2(bgr, scale):
    pos = _cursor_pos(scale)
    if pos is None:
        return
    cx, cy = pos
    s = max(0.6, scale)
    pts = [(int(cx + dx * s), int(cy + dy * s)) for dx, dy in _ARROW_PTS_NORM]
    pts_arr = np.array(pts, dtype=np.int32)
    cv2.fillPoly(bgr, [pts_arr], (255, 255, 255))
    cv2.polylines(bgr, [pts_arr], True, (0, 0, 0), 1)


def _draw_cursor_pil(img, scale):
    pos = _cursor_pos(scale)
    if pos is None:
        return
    cx, cy = pos
    s = max(0.6, scale)
    pts = [(int(cx + dx * s), int(cy + dy * s)) for dx, dy in _ARROW_PTS_NORM]
    draw = ImageDraw.Draw(img)
    draw.polygon(pts, fill="white", outline="black")


# ── ScreenCapture ─────────────────────────────────────────────────────────────

class ScreenCapture:
    def __init__(self, on_frame, fps=30, quality=65, scale=0.75):
        self._on_frame  = on_frame
        self._fps       = fps
        self._quality   = quality
        self._scale     = scale
        self._recorder  = None
        self._running   = False
        self._last_hash = None

    def attach_recorder(self, recorder):
        self._recorder = recorder

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="screencap").start()

    def stop(self):
        self._running = False

    def _loop(self):
        if _CV2_OK:
            self._loop_cv2()
        elif _PIL_OK:
            self._loop_pil()

    # ── cv2 path (fast) ───────────────────────────────────────────────────────

    def _loop_cv2(self):
        interval = 1.0 / self._fps
        THUMB_W, THUMB_H = 80, 45
        with mss.mss() as sct:
            mon = sct.monitors[1]
            while self._running:
                t0 = time.perf_counter()
                try:
                    shot = sct.grab(mon)
                    w, h = shot.width, shot.height
                    bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape((h, w, 4))
                    bgr  = bgra[:, :, :3]

                    # Fast change detection on tiny thumbnail
                    thumb = cv2.resize(bgr, (THUMB_W, THUMB_H),
                                       interpolation=cv2.INTER_NEAREST)
                    digest = hashlib.md5(thumb.tobytes()).digest()
                    changed = digest != self._last_hash
                    self._last_hash = digest

                    if not changed and not self._recorder:
                        self._sleep_remainder(t0, interval)
                        continue

                    scale = self._scale
                    if scale != 1.0:
                        nw = int(w * scale)
                        nh = int(h * scale)
                        bgr = cv2.resize(bgr, (nw, nh),
                                         interpolation=cv2.INTER_LINEAR)

                    _draw_cursor_cv2(bgr, scale)

                    ret, buf = cv2.imencode(
                        ".jpg", bgr,
                        [cv2.IMWRITE_JPEG_QUALITY, self._quality],
                    )
                    raw = buf.tobytes()
                    b64 = base64.b64encode(raw).decode()

                    if changed:
                        self._on_frame(b64)
                    if self._recorder:
                        self._recorder.write_frame(raw)

                except Exception:
                    pass
                self._sleep_remainder(t0, interval)

    # ── PIL path (fallback) ───────────────────────────────────────────────────

    def _loop_pil(self):
        interval = 1.0 / self._fps
        THUMB_W, THUMB_H = 80, 45
        with mss.mss() as sct:
            mon = sct.monitors[1]
            while self._running:
                t0 = time.perf_counter()
                try:
                    shot = sct.grab(mon)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

                    thumb = img.resize((THUMB_W, THUMB_H), Image.BILINEAR)
                    digest = hashlib.md5(thumb.tobytes()).digest()
                    changed = digest != self._last_hash
                    self._last_hash = digest

                    if not changed and not self._recorder:
                        self._sleep_remainder(t0, interval)
                        continue

                    scale = self._scale
                    if scale != 1.0:
                        nw = int(img.width  * scale)
                        nh = int(img.height * scale)
                        img = img.resize((nw, nh), Image.BILINEAR)

                    _draw_cursor_pil(img, scale)

                    buf = _io.BytesIO()
                    img.save(buf, format="JPEG", quality=self._quality)
                    raw = buf.getvalue()
                    b64 = base64.b64encode(raw).decode()

                    if changed:
                        self._on_frame(b64)
                    if self._recorder:
                        self._recorder.write_frame(raw)

                except Exception:
                    pass
                self._sleep_remainder(t0, interval)

    @staticmethod
    def _sleep_remainder(t0, interval):
        remaining = interval - (time.perf_counter() - t0)
        if remaining > 0:
            time.sleep(remaining)
