import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def handle_command(cmd):
    action = cmd.get("action")

    if action == "mousemove":
        x, y = cmd.get("x", 0), cmd.get("y", 0)
        sw, sh = cmd.get("sw", 1), cmd.get("sh", 1)
        sx, sy = pyautogui.size()
        ax = int(x * sx / sw)
        ay = int(y * sy / sh)
        pyautogui.moveTo(ax, ay)

    elif action == "mousedown":
        button = _button(cmd.get("button", 0))
        pyautogui.mouseDown(button=button)

    elif action == "mouseup":
        button = _button(cmd.get("button", 0))
        pyautogui.mouseUp(button=button)

    elif action == "click":
        button = _button(cmd.get("button", 0))
        pyautogui.click(button=button)

    elif action == "scroll":
        pyautogui.scroll(int(cmd.get("delta", 0)))

    elif action == "keydown":
        key = _map_key(cmd.get("key", ""))
        if key:
            pyautogui.keyDown(key)

    elif action == "keyup":
        key = _map_key(cmd.get("key", ""))
        if key:
            pyautogui.keyUp(key)

    elif action == "type":
        text = cmd.get("text", "")
        if text:
            pyautogui.typewrite(text, interval=0.02)


def _button(code):
    return {0: "left", 1: "middle", 2: "right"}.get(code, "left")


def _map_key(key):
    mapping = {
        "Enter": "enter", "Backspace": "backspace", "Tab": "tab",
        "Escape": "esc", "Delete": "delete", "ArrowUp": "up",
        "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
        "Control": "ctrl", "Shift": "shift", "Alt": "alt",
        "Meta": "win", "CapsLock": "capslock", "Home": "home",
        "End": "end", "PageUp": "pageup", "PageDown": "pagedown",
        "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4", "F5": "f5",
        "F6": "f6", "F7": "f7", "F8": "f8", "F9": "f9", "F10": "f10",
        "F11": "f11", "F12": "f12",
    }
    if key in mapping:
        return mapping[key]
    if len(key) == 1:
        return key
    return None
