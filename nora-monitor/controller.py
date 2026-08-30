import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def handle_command(data):
    action = data.get("action")
    try:
        if action == "mousemove":
            sw = data.get("sw", 1)
            sh = data.get("sh", 1)
            x = data.get("x", 0)
            y = data.get("y", 0)
            sw = sw or 1
            sh = sh or 1
            screen_w, screen_h = pyautogui.size()
            abs_x = int(x / sw * screen_w)
            abs_y = int(y / sh * screen_h)
            pyautogui.moveTo(abs_x, abs_y)

        elif action == "mousedown":
            btn = _btn(data.get("button", 0))
            pyautogui.mouseDown(button=btn)

        elif action == "mouseup":
            btn = _btn(data.get("button", 0))
            pyautogui.mouseUp(button=btn)

        elif action == "scroll":
            delta = data.get("delta", 0)
            pyautogui.scroll(delta)

        elif action == "keydown":
            key = data.get("key", "")
            pg_key = _map_key(key)
            if pg_key:
                pyautogui.keyDown(pg_key)

        elif action == "keyup":
            key = data.get("key", "")
            pg_key = _map_key(key)
            if pg_key:
                pyautogui.keyUp(pg_key)

    except Exception:
        pass


def _btn(button):
    return {0: "left", 1: "middle", 2: "right"}.get(button, "left")


def _map_key(key):
    special = {
        "Enter": "enter", "Backspace": "backspace", "Tab": "tab",
        "Escape": "esc", "Delete": "delete", "Insert": "insert",
        "Home": "home", "End": "end", "PageUp": "pageup", "PageDown": "pagedown",
        "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
        "Shift": "shift", "Control": "ctrl", "Alt": "alt", "Meta": "winleft",
        "CapsLock": "capslock", "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4",
        "F5": "f5", "F6": "f6", "F7": "f7", "F8": "f8", "F9": "f9",
        "F10": "f10", "F11": "f11", "F12": "f12",
        " ": "space",
    }
    if key in special:
        return special[key]
    if len(key) == 1:
        return key
    return None
