import os
import threading
import time


class Recorder:
    def __init__(self, output_dir, fps=10):
        self._output_dir = output_dir
        self._fps = fps
        self._writer = None
        self._lock = threading.Lock()
        self._recording = False

    def start(self):
        with self._lock:
            if self._recording:
                return False
            try:
                import cv2
                import numpy as np
                ts = time.strftime("%Y%m%d_%H%M%S")
                path = os.path.join(self._output_dir, f"rec_{ts}.avi")
                os.makedirs(self._output_dir, exist_ok=True)
                self._pending_writer = {"path": path, "writer": None, "np": np, "cv2": cv2}
                self._recording = True
                return True
            except Exception:
                return False

    def stop(self):
        with self._lock:
            self._recording = False
            if self._writer:
                try: self._writer.release()
                except Exception: pass
                self._writer = None

    def write_frame(self, jpeg_bytes):
        if not self._recording:
            return
        try:
            import cv2
            import numpy as np
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return
            with self._lock:
                if not self._recording:
                    return
                if self._writer is None and hasattr(self, "_pending_writer"):
                    h, w = frame.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"XVID")
                    self._writer = cv2.VideoWriter(
                        self._pending_writer["path"], fourcc, self._fps, (w, h)
                    )
                if self._writer:
                    self._writer.write(frame)
        except Exception:
            pass
