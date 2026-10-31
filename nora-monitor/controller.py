import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

# Use win32api for SetCursorPos — much lower overhead than pyautogui.moveTo
try:
    import win32api as _w32
    _W32_OK = True
except ImportError:
    _W32_OK = False


def handle_command(data):
    action = data.get("action")
    try:
        # ── Mouse position helpers ─────────────────────────────────────────
        if action in ("mousemove", "mouse_click", "mouse_right",
                      "mousedown", "mouseup"):
            sw = data.get("sw") or 1
            sh = data.get("sh") or 1
            x  = data.get("x", 0)
            y  = data.get("y", 0)
            sw_screen, sh_screen = pyautogui.size()
            abs_x = int(x / sw * sw_screen)
            abs_y = int(y / sh * sh_screen)

        # ── Move ──────────────────────────────────────────────────────────
        if action == "mousemove":
            if _W32_OK:
                _w32.SetCursorPos((abs_x, abs_y))
            else:
                pyautogui.moveTo(abs_x, abs_y)

        # ── Click (hub sends this for simple left-click) ──────────────────
        elif action == "mouse_click":
            pyautogui.click(abs_x, abs_y)

        # ── Right-click ───────────────────────────────────────────────────
        elif action == "mouse_right":
            pyautogui.rightClick(abs_x, abs_y)

        # ── Button down / up (for drag) ───────────────────────────────────
        elif action == "mousedown":
            btn = _btn(data.get("button", 0))
            pyautogui.mouseDown(button=btn)

        elif action == "mouseup":
            btn = _btn(data.get("button", 0))
            pyautogui.mouseUp(button=btn)

        # ── Scroll — accepts both "delta" (float) and "dy" (pixels) ───────
        elif action == "scroll":
            delta = data.get("delta")
            if delta is None:
                # dy is raw browser deltaY pixels; 100px ≈ 1 detent
                dy = data.get("dy", 0)
                clicks = -int(dy / 100) or (-1 if dy > 0 else 1)
            else:
                clicks = int(delta)
            pyautogui.scroll(clicks)

        # ── Key down / up ─────────────────────────────────────────────────
        elif action == "keydown":
            key  = data.get("key", "")
            code = data.get("code", "")
            pg   = _map_key(key, code)
            if pg:
                pyautogui.keyDown(pg)

        elif action == "keyup":
            key  = data.get("key", "")
            code = data.get("code", "")
            pg   = _map_key(key, code)
            if pg:
                pyautogui.keyUp(pg)

        # ── Type text (kb-bar / clipboard paste) ─────────────────────────
        elif action == "type":
            text = data.get("text", "")
            if text:
                try:
                    import pyperclip
                    prev = pyperclip.paste()
                    pyperclip.copy(text)
                    pyautogui.hotkey("ctrl", "v")
                    # Restore clipboard after a short delay
                    import threading, time
                    def _restore():
                        time.sleep(0.3)
                        try: pyperclip.copy(prev)
                        except Exception: pass
                    threading.Thread(target=_restore, daemon=True).start()
                except Exception:
                    pyautogui.typewrite(text, interval=0.02)

    except Exception:
        pass


def _btn(button):
    return {0: "left", 1: "middle", 2: "right"}.get(int(button), "left")


def _map_key(key, code=""):
    special = {
        # Navigation / editing
        "Enter": "enter", "Return": "enter",
        "Backspace": "backspace", "Tab": "tab",
        "Escape": "esc", "Delete": "delete", "Insert": "insert",
        "Home": "home", "End": "end",
        "PageUp": "pageup", "PageDown": "pagedown",
        # Arrow keys
        "ArrowUp": "up", "ArrowDown": "down",
        "ArrowLeft": "left", "ArrowRight": "right",
        # Modifier keys
        "Shift": "shift", "ShiftLeft": "shiftleft", "ShiftRight": "shiftright",
        "Control": "ctrl", "ControlLeft": "ctrlleft", "ControlRight": "ctrlright",
        "Alt": "alt", "AltLeft": "altleft",
        "AltRight": "altright",          # AltGr on many keyboards
        "Meta": "winleft", "OS": "winleft",
        # Lock keys
        "CapsLock": "capslock", "NumLock": "numlock", "ScrollLock": "scrolllock",
        # Function keys
        "F1": "f1",   "F2": "f2",   "F3": "f3",   "F4": "f4",
        "F5": "f5",   "F6": "f6",   "F7": "f7",   "F8": "f8",
        "F9": "f9",   "F10": "f10", "F11": "f11", "F12": "f12",
        "F13": "f13", "F14": "f14", "F15": "f15",
        # Whitespace
        " ": "space",
        # Punctuation (some browsers send the symbol as key)
        ";": ";", "=": "=", ",": ",", "-": "-", ".": ".",
        "/": "/", "`": "`", "[": "[", "\\": "\\", "]": "]", "'": "'",
        # Print screen / pause
        "PrintScreen": "printscreen", "Pause": "pause",
        # Context menu
        "ContextMenu": "apps",
        # Media keys
        "AudioVolumeMute": "volumemute", "VolumeMute": "volumemute",
        "AudioVolumeDown": "volumedown", "VolumeDown": "volumedown",
        "AudioVolumeUp": "volumeup",   "VolumeUp": "volumeup",
        "MediaTrackNext": "nexttrack",  "MediaTrackPrevious": "prevtrack",
        "MediaPlayPause": "playpause",  "MediaStop": "stop",
    }

    if key in special:
        return special[key]

    # For letter keys use code to get the physical (unshifted) key
    # so that Shift+KeyA sends keyDown('a') while Shift is already held
    if code.startswith("Key") and len(code) == 4:
        return code[3].lower()          # "KeyA" → "a"
    if code.startswith("Digit") and len(code) == 6:
        return code[5]                  # "Digit1" → "1"
    if code.startswith("Numpad") and len(code) == 7:
        c = code[6]
        return "num" + c if c.isdigit() else None

    # Single printable char fallback
    if len(key) == 1:
        return key.lower()

    return None
