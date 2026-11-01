"""
Remote input injection — uses SendInput with INJECT_TAG so injected events
pass through the privacy-screen LL hooks while the screen is locked.
INJECT_TAG must equal privacy_screen.INJECT_TAG.
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

INJECT_TAG = 0x1EADBEEF

# ── SendInput structures ──────────────────────────────────────────────────────
ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE    = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE        = 0x0001
MOUSEEVENTF_ABSOLUTE    = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN    = 0x0002
MOUSEEVENTF_LEFTUP      = 0x0004
MOUSEEVENTF_RIGHTDOWN   = 0x0008
MOUSEEVENTF_RIGHTUP     = 0x0010
MOUSEEVENTF_MIDDLEDOWN  = 0x0020
MOUSEEVENTF_MIDDLEUP    = 0x0040
MOUSEEVENTF_WHEEL       = 0x0800
KEYEVENTF_KEYUP         = 0x0002

SM_XVIRTUALSCREEN  = 76
SM_YVIRTUALSCREEN  = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   wt.DWORD),
        ("dwFlags",     wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         wt.WORD),
        ("wScan",       wt.WORD),
        ("dwFlags",     wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.SendInput.restype = ctypes.c_uint
_u32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int]
_u32.GetSystemMetrics.restype = ctypes.c_int
_u32.GetSystemMetrics.argtypes = [ctypes.c_int]


def _send(*inputs):
    arr = (_INPUT * len(inputs))(*inputs)
    _u32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _vscreen():
    x = _u32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = _u32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = max(1, _u32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    h = max(1, _u32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    return x, y, w, h


def _primary_screen():
    return (max(1, _u32.GetSystemMetrics(0)),
            max(1, _u32.GetSystemMetrics(1)))


def _to_vdesk_frac(abs_x, abs_y):
    """Convert absolute primary-screen pixel coords to virtual-desktop 0..1 fractions."""
    vx, vy, vw, vh = _vscreen()
    return (abs_x - vx) / vw, (abs_y - vy) / vh


def _inject_mouse(nx, ny, flags, data=0):
    ax = max(0, min(65535, int(nx * 65535)))
    ay = max(0, min(65535, int(ny * 65535)))
    mi = _MOUSEINPUT(ax, ay, data & 0xFFFFFFFF,
                     flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                     0, INJECT_TAG)
    _send(_INPUT(INPUT_MOUSE, _INPUTunion(mi=mi)))


def _inject_key(vk, up):
    flags = KEYEVENTF_KEYUP if up else 0
    ki = _KEYBDINPUT(vk & 0xFFFF, 0, flags, 0, INJECT_TAG)
    _send(_INPUT(INPUT_KEYBOARD, _INPUTunion(ki=ki)))


# ── Key map: JS key name → Windows VK code ───────────────────────────────────
_JS_VK = {
    'Enter': 0x0D, 'Backspace': 0x08, 'Tab': 0x09, 'Escape': 0x1B,
    'Delete': 0x2E, 'Insert': 0x2D,
    'ArrowLeft': 0x25, 'ArrowRight': 0x27,
    'ArrowUp': 0x26,   'ArrowDown': 0x28,
    'Home': 0x24, 'End': 0x23, 'PageUp': 0x21, 'PageDown': 0x22,
    'Shift': 0xA0, 'Control': 0xA2, 'Alt': 0xA4, 'Meta': 0x5B,
    ' ': 0x20, 'Space': 0x20, 'CapsLock': 0x14,
    **{f'F{i}': 0x6F + i for i in range(1, 13)},
}
for _c in 'abcdefghijklmnopqrstuvwxyz':
    _JS_VK[_c] = ord(_c.upper())
for _c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
    _JS_VK[_c] = ord(_c)
for _c in '0123456789':
    _JS_VK[_c] = ord(_c)

_MOUSE_BTN_FLAGS = {
    (0, False): MOUSEEVENTF_LEFTDOWN,   (0, True): MOUSEEVENTF_LEFTUP,
    (1, False): MOUSEEVENTF_RIGHTDOWN,  (1, True): MOUSEEVENTF_RIGHTUP,
    (2, False): MOUSEEVENTF_MIDDLEDOWN, (2, True): MOUSEEVENTF_MIDDLEUP,
}

try:
    import pyperclip as _pyperclip
    _HAS_CLIP = True
except ImportError:
    _HAS_CLIP = False


def handle_command(data):
    action = data.get("action")
    try:
        if action in ("mousemove", "mouse_click", "mouse_right",
                      "mousedown", "mouseup"):
            sw = data.get("sw") or 1
            sh = data.get("sh") or 1
            x  = data.get("x", 0)
            y  = data.get("y", 0)
            pw, ph = _primary_screen()
            abs_x = int(x / sw * pw)
            abs_y = int(y / sh * ph)
            nx, ny = _to_vdesk_frac(abs_x, abs_y)

        if action == "mousemove":
            _inject_mouse(nx, ny, MOUSEEVENTF_MOVE)

        elif action == "mouse_click":
            _inject_mouse(nx, ny, MOUSEEVENTF_LEFTDOWN)
            time.sleep(0.05)
            _inject_mouse(nx, ny, MOUSEEVENTF_LEFTUP)

        elif action == "mouse_right":
            _inject_mouse(nx, ny, MOUSEEVENTF_RIGHTDOWN)
            time.sleep(0.05)
            _inject_mouse(nx, ny, MOUSEEVENTF_RIGHTUP)

        elif action == "mousedown":
            b = data.get("button", 0)
            _inject_mouse(nx, ny, _MOUSE_BTN_FLAGS.get((b, False), MOUSEEVENTF_LEFTDOWN))

        elif action == "mouseup":
            b = data.get("button", 0)
            _inject_mouse(nx, ny, _MOUSE_BTN_FLAGS.get((b, True), MOUSEEVENTF_LEFTUP))

        elif action == "scroll":
            delta = data.get("delta")
            if delta is None:
                dy = data.get("dy", 0)
                delta = -int(dy / 100) or (-1 if dy > 0 else 1)
            _inject_mouse(0.5, 0.5, MOUSEEVENTF_WHEEL, int(delta) * 120)

        elif action == "keydown":
            vk = _JS_VK.get(data.get("key") or data.get("code", ""))
            if vk:
                _inject_key(vk, False)

        elif action == "keyup":
            vk = _JS_VK.get(data.get("key") or data.get("code", ""))
            if vk:
                _inject_key(vk, True)

        elif action == "type":
            text = data.get("text", "")
            if text and _HAS_CLIP:
                try:
                    prev = _pyperclip.paste()
                    _pyperclip.copy(text)
                    _inject_key(0xA2, False)  # VK_LCONTROL down
                    _inject_key(0x56, False)  # V down
                    time.sleep(0.05)
                    _inject_key(0x56, True)   # V up
                    _inject_key(0xA2, True)   # VK_LCONTROL up

                    def _restore():
                        time.sleep(0.3)
                        try:
                            _pyperclip.copy(prev)
                        except Exception:
                            pass
                    threading.Thread(target=_restore, daemon=True).start()
                except Exception:
                    pass

    except Exception:
        pass
