import base64
import io
import threading
import time

import mss
from PIL import Image


class ScreenCapture:
    def __init__(self, on_frame, fps=10, quality=50, scale=0.5):
        self._on_frame = on_frame
        self._interval = 1.0 / fps
        self._quality = quality
        self._scale = scale
        self._running = False
        self._thread = None
        self._recorder = None

    def attach_recorder(self, recorder):
        self._recorder = recorder

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            while self._running:
                t0 = time.monotonic()
                try:
                    raw = sct.grab(monitor)
                    full = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

                    # Pass full-res frame to recorder if active
                    if self._recorder and self._recorder.is_recording:
                        self._recorder.write_frame(full)

                    # Downscale for streaming
                    if self._scale != 1.0:
                        w = int(full.width * self._scale)
                        h = int(full.height * self._scale)
                        img = full.resize((w, h), Image.LANCZOS)
                    else:
                        img = full

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
