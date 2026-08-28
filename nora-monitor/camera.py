import base64
import io
import threading
import time

import cv2
from PIL import Image


class CameraCapture:
    def __init__(self, on_frame, fps=10, quality=55):
        self._on_frame = on_frame
        self._interval = 1.0 / fps
        self._quality = quality
        self._running = False
        self._thread = None
        self._cap = None

    def start(self):
        if self._running:
            return True
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return False
        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._cap:
            self._cap.release()
            self._cap = None

    def _loop(self):
        while self._running and self._cap:
            t0 = time.monotonic()
            try:
                ret, frame = self._cap.read()
                if ret:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(rgb)
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
