"""
macOS screen capture — mss works natively, no win32 cursor needed.
Uses Quartz for cursor overlay if available.
"""
import base64
import hashlib
import threading
import time

import mss
from PIL import Image, ImageDraw
import io


def _draw_cursor(img, scale):
    try:
        import Quartz
        loc = Quartz.NSEvent.mouseLocation()
        # macOS Y is flipped (0 = bottom)
        screen_h = Quartz.CGDisplayPixelsHigh(Quartz.CGMainDisplayID())
        cx = int(loc.x * scale)
        cy = int((screen_h - loc.y) * scale)
        draw = ImageDraw.Draw(img)
        r = max(4, int(8 * scale))
        draw.ellipse([cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1], outline="white", width=2)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="black", width=1)
        draw.line([cx - r, cy, cx + r, cy], fill="white", width=2)
        draw.line([cx, cy - r, cx, cy + r], fill="white", width=2)
    except Exception:
        pass


class ScreenCapture:
    def __init__(self, on_frame, fps=10, quality=40, scale=0.5):
        self._on_frame = on_frame
        self._fps = fps
        self._quality = quality
        self._scale = scale
        self._recorder = None
        self._running = False
        self._last_hash = None

    def attach_recorder(self, recorder):
        self._recorder = recorder

    def start(self):
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True, name="screencap")
        t.start()

    def stop(self):
        self._running = False

    def _loop(self):
        interval = 1.0 / self._fps
        THUMB_W, THUMB_H = 80, 45
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            while self._running:
                t0 = time.time()
                try:
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

                    thumb = img.resize((THUMB_W, THUMB_H), Image.BILINEAR)
                    h = hashlib.md5(thumb.tobytes()).digest()
                    changed = h != self._last_hash
                    self._last_hash = h

                    if not changed and not self._recorder:
                        elapsed = time.time() - t0
                        sleep = interval - elapsed
                        if sleep > 0:
                            time.sleep(sleep)
                        continue

                    if self._scale != 1.0:
                        w = int(img.width * self._scale)
                        h_px = int(img.height * self._scale)
                        img = img.resize((w, h_px), Image.LANCZOS)

                    _draw_cursor(img, self._scale)

                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=self._quality)
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    if changed:
                        self._on_frame(b64)
                    if self._recorder:
                        self._recorder.write_frame(buf.getvalue())
                except Exception:
                    pass
                elapsed = time.time() - t0
                sleep = interval - elapsed
                if sleep > 0:
                    time.sleep(sleep)
