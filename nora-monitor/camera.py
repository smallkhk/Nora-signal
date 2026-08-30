import base64
import io
import threading
import time


class CameraCapture:
    def __init__(self, on_frame, fps=8, quality=40):
        self._on_frame = on_frame
        self._fps = fps
        self._quality = quality
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="camera")
        self._thread.start()
        return True

    def stop(self):
        self._running = False

    def _loop(self):
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                self._running = False
                return
            interval = 1.0 / self._fps
            while self._running:
                t0 = time.time()
                ret, frame = cap.read()
                if ret:
                    try:
                        encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._quality]
                        _, buf = cv2.imencode(".jpg", frame, encode_params)
                        b64 = base64.b64encode(buf.tobytes()).decode()
                        self._on_frame(b64)
                    except Exception:
                        pass
                elapsed = time.time() - t0
                sleep = interval - elapsed
                if sleep > 0:
                    time.sleep(sleep)
            cap.release()
        except Exception:
            pass
        self._running = False
