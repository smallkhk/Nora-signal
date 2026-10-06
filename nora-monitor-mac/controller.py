"""
macOS remote control — pyautogui works on macOS for mouse/keyboard.
"""
import pyautogui
import threading

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


def handle_command(data):
    action = data.get("action", "")
    try:
        if action == "mouse_click":
            x = data.get("x", 0)
            y = data.get("y", 0)
            import mss
            with mss.mss() as sct:
                m = sct.monitors[1]
                ax = int(x * m["width"])
                ay = int(y * m["height"])
            pyautogui.click(ax, ay)

        elif action == "mouse_right":
            x = data.get("x", 0)
            y = data.get("y", 0)
            import mss
            with mss.mss() as sct:
                m = sct.monitors[1]
                ax = int(x * m["width"])
                ay = int(y * m["height"])
            pyautogui.rightClick(ax, ay)

        elif action == "scroll":
            dy = data.get("dy", 0)
            pyautogui.scroll(-dy)

        elif action == "type":
            text = data.get("text", "")
            pyautogui.typewrite(text, interval=0.02)

        elif action == "key":
            key = data.get("key", "")
            if key:
                pyautogui.press(key)

        elif action == "start_recording":
            pass

        elif action == "stop_recording":
            pass

    except Exception:
        pass
