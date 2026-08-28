import cv2
import numpy as np
import threading
import time
import os
from datetime import datetime


class Recorder:
    def __init__(self, output_dir="recordings", fps=10):
        self._output_dir = output_dir
        self._fps = fps
        self._writer = None
        self._lock = threading.Lock()
        self._recording = False
        self._filename = None
        os.makedirs(output_dir, exist_ok=True)

    def start(self):
        with self._lock:
            if self._recording:
                return None
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._filename = os.path.join(self._output_dir, f"session_{ts}.mp4")
            self._writer = None  # initialized on first frame
            self._recording = True
            return self._filename

    def stop(self):
        with self._lock:
            self._recording = False
            if self._writer:
                self._writer.release()
                self._writer = None
            fname = self._filename
            self._filename = None
            return fname

    def write_frame(self, pil_image):
        with self._lock:
            if not self._recording:
                return
            frame = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
            h, w = frame.shape[:2]
            if self._writer is None:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self._writer = cv2.VideoWriter(self._filename, fourcc, self._fps, (w, h))
            self._writer.write(frame)

    @property
    def is_recording(self):
        return self._recording

    @property
    def filename(self):
        return self._filename
