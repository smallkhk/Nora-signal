"""
Privacy Screen / Display Lock for NoraMonitor.

Covers all monitors with an always-on-top overlay and blocks physical
keyboard/mouse input while allowing injected (remote-control) input through.

Emergency local exit: Ctrl + Alt + Shift + F12  (bypasses PIN).
"""
import ctypes
import ctypes.wintypes
import threading

try:
    import tkinter as tk
    _TK_OK = True
except ImportError:
    _TK_OK = False

# ── Win32 constants ───────────────────────────────────────────────────────────
WH_KEYBOARD_LL  = 13
WH_MOUSE_LL     = 14
LLKHF_INJECTED  = 0x10   # keyboard: event injected via SendInput / keybd_event
LLMHF_INJECTED  = 0x01   # mouse: event injected

VK_F12      = 0x7B
VK_CONTROL  = 0x11
VK_MENU     = 0x12   # Alt
VK_SHIFT    = 0x10

LLKBDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
)
LLMSPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
)


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      ctypes.c_uint32),
        ("scanCode",    ctypes.c_uint32),
        ("flags",       ctypes.c_uint32),
        ("time",        ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),   # ULONG_PTR
    ]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt_x",        ctypes.c_long),     # POINT x
        ("pt_y",        ctypes.c_long),     # POINT y
        ("mouseData",   ctypes.c_uint32),
        ("flags",       ctypes.c_uint32),
        ("time",        ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),   # ULONG_PTR
    ]


# ── Global state ──────────────────────────────────────────────────────────────
_active_screen = None
_state_lock    = threading.Lock()


class PrivacyScreen:
    """Full-screen lock overlay. Blocks physical input; remote-injected input passes through."""

    def __init__(
        self,
        message="Remote support session in progress.\nPlease do not touch the computer.",
        pin=None,
    ):
        self.message  = message
        self.pin      = str(pin) if pin else None
        self._root    = None
        self._active  = False
        self._thread  = None
        # Keep ctypes callback refs alive — GC would collect them otherwise
        self._kbd_cb  = None
        self._ms_cb   = None
        self._h_kbd   = None
        self._h_ms    = None

    @property
    def active(self):
        return self._active

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        if self._active:
            return False
        self._active = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="privacy-screen"
        )
        self._thread.start()
        return True

    def stop(self, pin=None):
        """Stop the screen. Returns (ok, reason)."""
        if self.pin and str(pin or "") != self.pin:
            return False, "wrong_pin"
        self._shutdown()
        return True, "stopped"

    def force_stop(self):
        """Emergency stop — bypasses PIN."""
        self._shutdown()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _shutdown(self):
        self._active = False
        with _state_lock:
            global _active_screen
            if _active_screen is self:
                _active_screen = None
        root = self._root
        if root:
            try:
                root.after(0, root.destroy)
            except Exception:
                pass

    def _run(self):
        try:
            if _TK_OK:
                self._run_tk()
            else:
                import time
                while self._active:
                    time.sleep(0.5)
        finally:
            self._remove_hooks()
            self._active = False
            with _state_lock:
                global _active_screen
                if _active_screen is self:
                    _active_screen = None

    def _run_tk(self):
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()

        # Virtual screen covers every connected monitor
        vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN

        root = tk.Tk()
        self._root = root

        root.overrideredirect(True)
        root.wm_attributes("-topmost", True)
        root.configure(bg="#090909")
        root.geometry(f"{vw}x{vh}+{vx}+{vy}")
        root.protocol("WM_DELETE_WINDOW", lambda: None)

        wrap_px = min(vw - 160, 900)

        center = tk.Frame(root, bg="#090909")
        center.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            center, text="\U0001F512",
            font=("Segoe UI", 72), fg="#6366f1", bg="#090909",
        ).pack(pady=(0, 18))

        tk.Label(
            center, text=self.message,
            font=("Segoe UI", 24, "bold"), fg="#f4f4f5", bg="#090909",
            justify="center", wraplength=wrap_px,
        ).pack(pady=(0, 14))

        tk.Label(
            center,
            text=(
                "A remote support session is in progress.\n"
                "Do not close or interact with this screen.\n\n"
                "Emergency exit: Ctrl + Alt + Shift + F12"
            ),
            font=("Segoe UI", 13), fg="#52525b", bg="#090909",
            justify="center",
        ).pack()

        # Install hooks before entering mainloop so they are processed by it
        self._install_hooks()

        # Re-assert always-on-top every 250 ms
        def _keep_top():
            try:
                root.wm_attributes("-topmost", True)
                root.lift()
                if self._active:
                    root.after(250, _keep_top)
            except Exception:
                pass

        root.after(50, _keep_top)
        root.mainloop()

    def _install_hooks(self):
        user32   = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        screen   = self   # capture for closures

        def _kbd_hook(nCode, wParam, lParam):
            if nCode >= 0:
                kb = ctypes.cast(
                    lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)
                ).contents
                injected = bool(kb.flags & LLKHF_INJECTED)
                if not injected:
                    # Emergency exit: Ctrl + Alt + Shift + F12
                    if (
                        kb.vkCode == VK_F12
                        and user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                        and user32.GetAsyncKeyState(VK_MENU)    & 0x8000
                        and user32.GetAsyncKeyState(VK_SHIFT)   & 0x8000
                    ):
                        screen.force_stop()
                        return 1
                    return 1   # swallow all other physical keystrokes
            return user32.CallNextHookEx(screen._h_kbd, nCode, wParam, lParam)

        def _ms_hook(nCode, wParam, lParam):
            if nCode >= 0:
                ms = ctypes.cast(
                    lParam, ctypes.POINTER(_MSLLHOOKSTRUCT)
                ).contents
                injected = bool(ms.flags & LLMHF_INJECTED)
                if not injected:
                    return 1   # swallow all physical mouse events
            return user32.CallNextHookEx(screen._h_ms, nCode, wParam, lParam)

        self._kbd_cb = LLKBDPROC(_kbd_hook)
        self._ms_cb  = LLMSPROC(_ms_hook)

        hmod = kernel32.GetModuleHandleW(None)
        self._h_kbd = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kbd_cb, hmod, 0
        )
        self._h_ms = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._ms_cb, hmod, 0
        )

    def _remove_hooks(self):
        user32 = ctypes.windll.user32
        for h in (self._h_kbd, self._h_ms):
            if h:
                try:
                    user32.UnhookWindowsHookEx(h)
                except Exception:
                    pass
        self._h_kbd = None
        self._h_ms  = None


# ── Module-level helpers ──────────────────────────────────────────────────────

def start_privacy_screen(message=None, pin=None):
    global _active_screen
    with _state_lock:
        if _active_screen and _active_screen.active:
            return False, "already_active"
    ps = PrivacyScreen(
        message=message or "Remote support session in progress.\nPlease wait.",
        pin=pin,
    )
    with _state_lock:
        _active_screen = ps
    ok = ps.start()
    return ok, "started" if ok else "failed"


def stop_privacy_screen(pin=None):
    with _state_lock:
        ps = _active_screen
    if ps is None:
        return True, "not_active"
    return ps.stop(pin=pin)


def get_status():
    with _state_lock:
        ps = _active_screen
    return ps is not None and ps.active
