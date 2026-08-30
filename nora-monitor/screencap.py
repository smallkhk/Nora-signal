import base64
import threading
import time

import mss
import mss.tools
from PIL import Image
import io


class ScreenCapture:
    def __init__(self, on_frame, fps=8, quality=35, scale=0.5):
        self._on_frame = on_frame
        self._fps = fps
        self._quality = quality
        self._scale = scale
        self._recorder = None
        self._running = False

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
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            while self._running:
                t0 = time.time()
                try:
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    if self._scale != 1.0:
                        w = int(img.width * self._scale)
                        h = int(img.height * self._scale)
                        img = img.resize((w, h), Image.BILINEAR)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=self._quality)
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    self._on_frame(b64)
                    if self._recorder:
                        self._recorder.write_frame(buf.getvalue())
                except Exception:
                    pass
                elapsed = time.time() - t0
                sleep = interval - elapsed
                if sleep > 0:
                    time.sleep(sleep)
