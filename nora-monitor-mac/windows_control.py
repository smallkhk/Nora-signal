"""
macOS window/desktop control stubs.
Full window capture requires Screen Recording permission.
"""
import subprocess
import threading
import time
import base64
import io

import mss
from PIL import Image

_capture_thread = None
_capture_running = False


def list_windows():
    try:
        import mactools
        return mactools.list_windows()
    except Exception:
        return []


def send_key(hwnd, key):
    try:
        import pyautogui
        pyautogui.press(key)
    except Exception:
        pass


def send_mouse(hwnd, x, y, action="click", button=0):
    try:
        import pyautogui
        pyautogui.click(int(x), int(y))
    except Exception:
        pass


def start_window_capture(hwnd, on_frame):
    global _capture_thread, _capture_running
    _capture_running = True

    def _loop():
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            while _capture_running:
                try:
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    img = img.resize((int(img.width * 0.5), int(img.height * 0.5)), Image.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=40)
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    on_frame(b64)
                except Exception:
                    pass
                time.sleep(0.1)

    _capture_thread = threading.Thread(target=_loop, daemon=True)
    _capture_thread.start()


def stop_window_capture():
    global _capture_running
    _capture_running = False


# macOS doesn't have virtual desktops via simple API
def desktop_new():
    subprocess.Popen(["osascript", "-e",
        'tell application "Mission Control" to launch'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def desktop_left():
    subprocess.Popen(["osascript", "-e",
        'tell application "System Events" to key code 123 using {control down}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def desktop_right():
    subprocess.Popen(["osascript", "-e",
        'tell application "System Events" to key code 124 using {control down}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def desktop_close():
    pass
