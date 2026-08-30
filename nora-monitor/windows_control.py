"""
PostMessage-based window control — sends keyboard and mouse input directly
to any window's message queue without stealing focus or moving the cursor.
Also provides virtual desktop management via Win+Ctrl hotkeys.
"""

import time

try:
    import win32api, win32con, win32gui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import pyautogui
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False


# ── Window enumeration ────────────────────────────────────────────────────────

def list_windows():
    if not HAS_WIN32:
        return []
    results = []
    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                try:
                    rect = list(win32gui.GetWindowRect(hwnd))
                except Exception:
                    rect = [0, 0, 0, 0]
                results.append({"hwnd": hwnd, "title": title, "rect": rect})
    win32gui.EnumWindows(_cb, None)
    return results


# ── Keyboard (PostMessage) ────────────────────────────────────────────────────

_JS_VK = {
    'Enter': 0x0D, 'Backspace': 0x08, 'Tab': 0x09, 'Escape': 0x1B,
    'Delete': 0x2E, 'Insert': 0x2D,
    'ArrowLeft': 0x25, 'ArrowRight': 0x27,
    'ArrowUp': 0x26,  'ArrowDown': 0x28,
    'Home': 0x24, 'End': 0x23, 'PageUp': 0x21, 'PageDown': 0x22,
    'Shift': 0x10, 'Control': 0x11, 'Alt': 0x12, 'Meta': 0x5B,
    ' ': 0x20, 'Space': 0x20, 'CapsLock': 0x14,
    **{f'F{i}': 0x6F + i for i in range(1, 13)},
}


def send_key(hwnd, js_key):
    """Send a single key to hwnd without stealing focus."""
    if not HAS_WIN32:
        return
    vk = _JS_VK.get(js_key)
    if vk:
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
        time.sleep(0.01)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0xC0000001)
    elif len(js_key) == 1:
        # Printable character — WM_CHAR is enough for most text editors
        win32api.PostMessage(hwnd, win32con.WM_CHAR, ord(js_key), 0)


# ── Mouse (PostMessage) ───────────────────────────────────────────────────────

_BTN_DOWN = [win32con.WM_LBUTTONDOWN, win32con.WM_RBUTTONDOWN, win32con.WM_MBUTTONDOWN]
_BTN_UP   = [win32con.WM_LBUTTONUP,   win32con.WM_RBUTTONUP,   win32con.WM_MBUTTONUP  ]
_BTN_MK   = [win32con.MK_LBUTTON,     win32con.MK_RBUTTON,     win32con.MK_MBUTTON    ]


def send_mouse(hwnd, screen_x, screen_y, action="click", button=0):
    """
    Send a mouse event to hwnd at the given *screen* coordinates.
    Converts to window client coordinates internally; cursor never moves.
    action: "move" | "down" | "up" | "click"
    button: 0=left, 1=right, 2=middle
    """
    if not HAS_WIN32:
        return
    try:
        cx, cy = win32gui.ScreenToClient(hwnd, (int(screen_x), int(screen_y)))
    except Exception:
        return
    lp = (cy << 16) | (cx & 0xFFFF)
    b = max(0, min(button, 2))

    if action == "move":
        win32api.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lp)
    elif action in ("click", "down"):
        win32api.PostMessage(hwnd, _BTN_DOWN[b], _BTN_MK[b], lp)
        if action == "click":
            time.sleep(0.05)
            win32api.PostMessage(hwnd, _BTN_UP[b], 0, lp)
    elif action == "up":
        win32api.PostMessage(hwnd, _BTN_UP[b], 0, lp)
    elif action == "dblclick":
        win32api.PostMessage(hwnd, _BTN_DOWN[b], _BTN_MK[b], lp)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, _BTN_UP[b], 0, lp)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, _BTN_DOWN[b], _BTN_MK[b], lp)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, _BTN_UP[b], 0, lp)


# ── Virtual desktop controls ──────────────────────────────────────────────────

def desktop_new():
    if HAS_PYAUTOGUI:
        pyautogui.hotkey('win', 'ctrl', 'd')

def desktop_left():
    if HAS_PYAUTOGUI:
        pyautogui.hotkey('win', 'ctrl', 'left')

def desktop_right():
    if HAS_PYAUTOGUI:
        pyautogui.hotkey('win', 'ctrl', 'right')

def desktop_close():
    if HAS_PYAUTOGUI:
        pyautogui.hotkey('win', 'ctrl', 'f4')
