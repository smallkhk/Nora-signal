import threading
import time
import ctypes


def _read_clipboard():
    try:
        ctypes.windll.user32.OpenClipboard(0)
        CF_UNICODETEXT = 13
        handle = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = ctypes.windll.kernel32.GlobalLock(handle)
        text = ctypes.wstring_at(ptr)
        ctypes.windll.kernel32.GlobalUnlock(handle)
        return text
    except Exception:
        return ""
    finally:
        try:
            ctypes.windll.user32.CloseClipboard()
        except Exception:
            pass


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
                text = _read_clipboard()
                if text and text != self._last:
                    self._last = text
                    self._on_change(text)
            except Exception:
                pass
            time.sleep(self._interval)
