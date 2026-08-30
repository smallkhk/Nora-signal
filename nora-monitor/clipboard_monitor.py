import threading
import time


class ClipboardMonitor:
    def __init__(self, on_change, interval=0.8):
        self._on_change = on_change
        self._interval = interval
        self._last = ""
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                import pyperclip
                text = pyperclip.paste() or ""
                if text and text != self._last:
                    self._last = text
                    self._on_change(text)
            except Exception:
                pass
            time.sleep(self._interval)
