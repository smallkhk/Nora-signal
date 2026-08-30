import threading
import base64

SAMPLE_RATE = 16000
CHUNK = 2048  # 128ms per chunk


class MicCapture:
    def __init__(self, on_chunk):
        self._on_chunk = on_chunk
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mic")
        self._thread.start()
        return True

    def stop(self):
        self._running = False

    def _loop(self):
        try:
            import sounddevice as sd
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype="int16", blocksize=CHUNK) as stream:
                while self._running:
                    data, _ = stream.read(CHUNK)
                    b64 = base64.b64encode(data.tobytes()).decode()
                    self._on_chunk(b64)
        except Exception:
            pass
        self._running = False
