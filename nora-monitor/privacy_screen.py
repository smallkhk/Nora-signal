"""
Privacy Screen / Display Lock for NoraMonitor.

Pure Win32 implementation — no tkinter / TCL/TK dependency.
Uses user32 + gdi32 (always present on Windows) for the overlay window.

Overlay modes
  default  – dark screen with message text (Win32 GDI)
  image    – base64-encoded PNG/JPG stretched fullscreen (Win32 GDI + PIL)
  html     – arbitrary HTML rendered via Edge WebView2 (pywebview)

Emergency local exit: Ctrl + Alt + Shift + F12  (bypasses PIN).
"""
import base64
import ctypes
import ctypes.wintypes
import io
import os
import tempfile
import threading

try:
    import webview as _webview
    _WEBVIEW_OK = True
except ImportError:
    _WEBVIEW_OK = False

# ── Win32 constants ───────────────────────────────────────────────────────────
WH_KEYBOARD_LL  = 13
WH_MOUSE_LL     = 14
LLKHF_INJECTED  = 0x10
LLMHF_INJECTED  = 0x01

VK_F12     = 0x7B
VK_CONTROL = 0x11
VK_MENU    = 0x12
VK_SHIFT   = 0x10

WM_PAINT      = 0x000F
WM_ERASEBKGND = 0x0014
WM_CLOSE      = 0x0010
WM_DESTROY    = 0x0002
WM_TIMER      = 0x0113
WM_APP_QUIT   = 0x8001   # private message used by _shutdown()

WS_POPUP      = 0x80000000
WS_VISIBLE    = 0x10000000
WS_EX_TOPMOST = 0x00000008
SWP_NOSIZE    = 0x0001
SWP_NOMOVE    = 0x0002
SW_SHOW       = 5
PM_REMOVE     = 0x0001
DT_CENTER     = 0x00000001
DT_WORDBREAK  = 0x00000010
SRCCOPY       = 0x00CC0020
TRANSPARENT   = 1


# ── ctypes types ──────────────────────────────────────────────────────────────
LLKBDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
LLMSPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.wintypes.HWND, ctypes.c_uint,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.c_uint32), ("scanCode", ctypes.c_uint32),
                 ("flags",  ctypes.c_uint32), ("time",     ctypes.c_uint32),
                 ("dwExtraInfo", ctypes.c_size_t)]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long),
                 ("mouseData", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                 ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize",        ctypes.c_uint),
        ("style",         ctypes.c_uint),
        ("lpfnWndProc",   WNDPROC),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     ctypes.wintypes.HANDLE),
        ("hIcon",         ctypes.wintypes.HANDLE),
        ("hCursor",       ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HANDLE),
        ("lpszMenuName",  ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm",       ctypes.wintypes.HANDLE),
    ]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", ctypes.wintypes.HANDLE), ("fErase", ctypes.wintypes.BOOL),
                 ("rcPaint", ctypes.wintypes.RECT), ("fRestore", ctypes.wintypes.BOOL),
                 ("fIncUpdate", ctypes.wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.wintypes.HWND), ("message", ctypes.c_uint),
                 ("wParam", ctypes.wintypes.WPARAM), ("lParam", ctypes.wintypes.LPARAM),
                 ("time", ctypes.c_uint), ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize",          ctypes.c_uint32),
        ("biWidth",         ctypes.c_int32),
        ("biHeight",        ctypes.c_int32),
        ("biPlanes",        ctypes.c_uint16),
        ("biBitCount",      ctypes.c_uint16),
        ("biCompression",   ctypes.c_uint32),
        ("biSizeImage",     ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed",       ctypes.c_uint32),
        ("biClrImportant",  ctypes.c_uint32),
    ]


# ── Global state ──────────────────────────────────────────────────────────────
_active_screen = None
_state_lock    = threading.Lock()


class PrivacyScreen:
    """Full-screen lock overlay. No tkinter dependency — pure Win32 GDI."""

    def __init__(
        self,
        message="Remote support session in progress.\nPlease do not touch the computer.",
        pin=None,
        overlay_image=None,
        overlay_html=None,
    ):
        self.message       = message
        self.pin           = str(pin) if pin else None
        self.overlay_image = overlay_image
        self.overlay_html  = overlay_html
        self._hwnd         = None
        self._webview_win  = None
        self._tmp_html     = None
        self._active       = False
        self._thread       = None
        self._wndproc_ref  = None   # keep WNDPROC alive (GC protection)
        self._kbd_cb       = None
        self._ms_cb        = None
        self._h_kbd        = None
        self._h_ms         = None
        # image pixel data stored for WM_PAINT
        self._img_pixels   = None
        self._img_bih      = None
        self._img_w        = 0
        self._img_h        = 0

    @property
    def active(self):
        return self._active

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        if self._active:
            return False
        self._active = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="privacy-screen")
        self._thread.start()
        return True

    def stop(self, pin=None):
        if self.pin and str(pin or "") != self.pin:
            return False, "wrong_pin"
        self._shutdown()
        return True, "stopped"

    def force_stop(self):
        self._shutdown()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _shutdown(self):
        self._active = False
        with _state_lock:
            global _active_screen
            if _active_screen is self:
                _active_screen = None
        hwnd = self._hwnd
        if hwnd:
            try:
                ctypes.windll.user32.PostMessageW(hwnd, WM_APP_QUIT, 0, 0)
            except Exception:
                pass
        wv = self._webview_win
        if wv:
            try:
                wv.destroy()
            except Exception:
                pass
        if self._tmp_html:
            try:
                os.unlink(self._tmp_html)
            except Exception:
                pass
            self._tmp_html = None

    def _run(self):
        try:
            if self.overlay_html and _WEBVIEW_OK:
                self._run_webview()
            else:
                self._run_win32()
        except Exception:
            pass
        finally:
            self._remove_hooks()
            self._active = False
            with _state_lock:
                global _active_screen
                if _active_screen is self:
                    _active_screen = None

    # ── Win32 GDI renderer ────────────────────────────────────────────────────

    def _run_win32(self):
        user32   = ctypes.windll.user32
        gdi32    = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32

        user32.SetProcessDPIAware()
        vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN

        hinst     = kernel32.GetModuleHandleW(None)
        cls_name  = f"NoraPrivacy_{id(self)}"

        # Pre-decode image (if any) so WM_PAINT stays fast
        if self.overlay_image:
            self._prepare_image(vw, vh)

        screen = self

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_PAINT:
                ps = _PAINTSTRUCT()
                hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                if screen._img_pixels and screen._img_bih:
                    # Stretch image to fill screen via StretchDIBits
                    gdi32.StretchDIBits(
                        hdc,
                        0, 0, vw, vh,
                        0, 0, screen._img_w, screen._img_h,
                        screen._img_pixels,
                        ctypes.byref(screen._img_bih),
                        0,        # DIB_RGB_COLORS
                        SRCCOPY,
                    )
                else:
                    # Dark background + message
                    rect = ctypes.wintypes.RECT(0, 0, vw, vh)
                    bg = gdi32.CreateSolidBrush(0x00090909)
                    user32.FillRect(hdc, ctypes.byref(rect), bg)
                    gdi32.DeleteObject(bg)
                    gdi32.SetBkMode(hdc, TRANSPARENT)
                    gdi32.SetTextColor(hdc, 0x00F4F4F5)
                    txt_rect = ctypes.wintypes.RECT(vw // 6, vh // 3, 5 * vw // 6, 2 * vh // 3)
                    user32.DrawTextW(hdc, screen.message, -1,
                                     ctypes.byref(txt_rect), DT_CENTER | DT_WORDBREAK)
                user32.EndPaint(hwnd, ctypes.byref(ps))
                return 0

            elif msg == WM_ERASEBKGND:
                return 1   # we handle background in WM_PAINT

            elif msg == WM_TIMER:
                if not screen._active:
                    user32.PostQuitMessage(0)
                else:
                    # Keep window strictly on top
                    user32.SetWindowPos(
                        ctypes.wintypes.HWND(hwnd),
                        ctypes.wintypes.HWND(-1),   # HWND_TOPMOST
                        0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE,
                    )
                return 0

            elif msg == WM_APP_QUIT:
                user32.PostQuitMessage(0)
                return 0

            elif msg in (WM_CLOSE, WM_DESTROY):
                return 0   # ignore — only _shutdown() can close us

            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = WNDPROC(wnd_proc)
        self._wndproc_ref = proc   # prevent GC

        # Register window class
        wc = _WNDCLASSEX()
        wc.cbSize        = ctypes.sizeof(_WNDCLASSEX)
        wc.lpfnWndProc   = proc
        wc.hInstance     = hinst
        wc.hbrBackground = gdi32.GetStockObject(4)   # BLACK_BRUSH
        wc.lpszClassName = cls_name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise RuntimeError(f"RegisterClassExW failed: {kernel32.GetLastError()}")

        user32.CreateWindowExW.restype = ctypes.wintypes.HWND
        hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST, cls_name, "",
            WS_POPUP | WS_VISIBLE,
            vx, vy, vw, vh,
            None, None, hinst, None,
        )
        if not hwnd:
            user32.UnregisterClassW(cls_name, hinst)
            raise RuntimeError(f"CreateWindowExW failed: {kernel32.GetLastError()}")

        self._hwnd = hwnd
        user32.SetWindowPos(
            ctypes.wintypes.HWND(hwnd), ctypes.wintypes.HWND(-1),
            0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE,
        )
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.UpdateWindow(hwnd)

        self._install_hooks()
        user32.SetTimer(hwnd, 1, 250, None)   # keep-on-top + alive check

        msg_obj = _MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg_obj), None, 0, 0)
            if r <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg_obj))
            user32.DispatchMessageW(ctypes.byref(msg_obj))

        user32.KillTimer(hwnd, 1)
        try:
            user32.DestroyWindow(hwnd)
        except Exception:
            pass
        user32.UnregisterClassW(cls_name, hinst)

    def _prepare_image(self, vw, vh):
        """Decode base64 image and store BGR pixel bytes for StretchDIBits."""
        try:
            from PIL import Image
            raw   = base64.b64decode(self.overlay_image)
            img   = Image.open(io.BytesIO(raw)).convert("RGB").resize((vw, vh), Image.LANCZOS)
            r, g, b = img.split()
            bgr_img = Image.merge("RGB", (b, g, r))   # GDI expects BGR
            self._img_pixels = bgr_img.tobytes()
            self._img_w = vw
            self._img_h = vh
            bih = _BITMAPINFOHEADER()
            bih.biSize      = ctypes.sizeof(_BITMAPINFOHEADER)
            bih.biWidth     = vw
            bih.biHeight    = -vh   # negative = top-down scanlines
            bih.biPlanes    = 1
            bih.biBitCount  = 24
            bih.biCompression = 0   # BI_RGB
            self._img_bih = bih
        except Exception:
            pass   # fall back to text overlay

    # ── pywebview renderer (HTML mode) ────────────────────────────────────────

    def _run_webview(self):
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        vx = user32.GetSystemMetrics(76)
        vy = user32.GetSystemMetrics(77)
        vw = user32.GetSystemMetrics(78)
        vh = user32.GetSystemMetrics(79)

        fd, path = tempfile.mkstemp(suffix=".html", prefix="nora_prv_")
        self._tmp_html = path
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.overlay_html)

        self._install_hooks()

        win = _webview.create_window(
            title="",
            url=f"file:///{path.replace(os.sep, '/')}",
            x=vx, y=vy, width=vw, height=vh,
            frameless=True, on_top=True, background_color="#090909",
        )
        self._webview_win = win
        _webview.start()

    # ── Win32 hooks ───────────────────────────────────────────────────────────

    def _install_hooks(self):
        user32   = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        screen   = self

        def _kbd_hook(nCode, wParam, lParam):
            if nCode >= 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if not (kb.flags & LLKHF_INJECTED):
                    if (kb.vkCode == VK_F12
                            and user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                            and user32.GetAsyncKeyState(VK_MENU)    & 0x8000
                            and user32.GetAsyncKeyState(VK_SHIFT)   & 0x8000):
                        screen.force_stop()
                        return 1
                    return 1
            return user32.CallNextHookEx(screen._h_kbd, nCode, wParam, lParam)

        def _ms_hook(nCode, wParam, lParam):
            if nCode >= 0:
                ms = ctypes.cast(lParam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                if not (ms.flags & LLMHF_INJECTED):
                    return 1
            return user32.CallNextHookEx(screen._h_ms, nCode, wParam, lParam)

        self._kbd_cb = LLKBDPROC(_kbd_hook)
        self._ms_cb  = LLMSPROC(_ms_hook)
        hmod = kernel32.GetModuleHandleW(None)
        self._h_kbd = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kbd_cb, hmod, 0)
        self._h_ms  = user32.SetWindowsHookExW(WH_MOUSE_LL,    self._ms_cb,  hmod, 0)

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

def start_privacy_screen(message=None, pin=None, overlay_image=None, overlay_html=None):
    import time
    global _active_screen
    with _state_lock:
        if _active_screen and _active_screen.active:
            return False, "already_active"
    ps = PrivacyScreen(
        message=message or "Remote support session in progress.\nPlease wait.",
        pin=pin,
        overlay_image=overlay_image,
        overlay_html=overlay_html,
    )
    with _state_lock:
        _active_screen = ps
    ps.start()
    time.sleep(2.0)   # wait for Win32 window to appear
    if not ps._active:
        return False, "failed (Win32 window creation error)"
    return True, "started"


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
