import base64
import io
import threading
import time

import mss
import mss.tools
from PIL import Image


class ScreenCapture:
    def __init__(self, on_frame, fps=10, quality=50, scale=0.5):
        self._on_frame = on_frame
        self._interval = 1.0 / fps
        self._quality = quality
        self._scale = scale
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        with mss.mss() as sct:
            monitor = sct.monitors[0]  # full virtual screen
            while self._running:
                t0 = time.monotonic()
                try:
                    raw = sct.grab(monitor)
                    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                    if self._scale != 1.0:
                        w = int(img.width * self._scale)
                        h = int(img.height * self._scale)
                        img = img.resize((w, h), Image.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=self._quality)
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    self._on_frame(b64)
                except Exception:
                    pass
                elapsed = time.monotonic() - t0
                wait = self._interval - elapsed
                if wait > 0:
                    time.sleep(wait)
