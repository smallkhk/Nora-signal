"""
Manages the fastcap H.264/UDP remote-desktop host.

Session ID is derived from the hostname (first 8 hex chars of SHA-256).
Password is read from env FASTCAP_PASSWORD or persisted in
%APPDATA%\SyncHost\fastcap.pw (auto-generated on first run).

Call start() once; get_info() returns the dict to broadcast.
"""
import hashlib
import os
import platform
import secrets
import threading

RELAY_UDP = os.environ.get("FASTCAP_RELAY", "162.35.37.67:5000")

_APP_DIR  = os.path.join(os.path.expandvars("%APPDATA%"), "SyncHost")
_PW_FILE  = os.path.join(_APP_DIR, "fastcap.pw")

_session  = None
_password = None
_host_obj = None
_lock     = threading.Lock()


def _make_session():
    name = platform.node() or "host"
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def _load_password():
    pw = os.environ.get("FASTCAP_PASSWORD", "")
    if pw:
        return pw
    try:
        if os.path.exists(_PW_FILE):
            with open(_PW_FILE) as f:
                return f.read().strip()
    except Exception:
        pass
    pw = secrets.token_urlsafe(8)
    try:
        os.makedirs(_APP_DIR, exist_ok=True)
        with open(_PW_FILE, "w") as f:
            f.write(pw)
    except Exception:
        pass
    return pw


def start(fps=30, bitrate=4000, monitor=0, res=720):
    global _session, _password, _host_obj
    with _lock:
        if _host_obj is not None:
            return
        _session  = _make_session()
        _password = _load_password()
        import datetime
        def _flog(msg):
            try:
                with open(os.path.join(_APP_DIR, "nora.log"), "a") as f:
                    f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] [fastcap] {msg}\n")
            except Exception:
                pass
        try:
            import fastcap
            _flog(f"starting Host relay={RELAY_UDP} session={_session[:8]}… fps={fps} bitrate={bitrate}")
            _host_obj = fastcap.Host(RELAY_UDP, _session, _password, fps, bitrate, monitor, res)
            _host_obj.start()
            _flog("Host started OK")
        except Exception as e:
            _host_obj = None
            _flog(f"FAIL: {e}")


def stop():
    global _host_obj
    with _lock:
        if _host_obj:
            try:
                _host_obj.stop()
            except Exception:
                pass
            _host_obj = None


def get_info():
    """Return dict for broadcast; None if not yet started."""
    with _lock:
        if _session is None:
            return None
        return {
            "session":  _session,
            "password": _password,
        }
