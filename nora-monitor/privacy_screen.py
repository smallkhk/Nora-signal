"""
Privacy Screen / Display Lock for NoraMonitor.
Pure Win32 implementation — no tkinter / TCL/TK dependency.

Overlay modes
  default  – dark screen with message text (Win32 GDI)
  image    – base64-encoded PNG/JPG stretched fullscreen (Win32 GDI + PIL)
  html     – arbitrary HTML rendered via Edge WebView2 (pywebview)

Emergency local exit: Ctrl + Alt + Shift + F12  (bypasses PIN).
"""
import base64
import ctypes
import ctypes.wintypes as wt
import io
import os
import tempfile
import threading


# ── Win32 DLL handles ────────────────────────────────────────────────────────
_user32   = ctypes.WinDLL("user32",   use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_gdi32    = ctypes.WinDLL("gdi32",    use_last_error=True)

# ── Primitive types ──────────────────────────────────────────────────────────
HANDLE  = ctypes.c_void_p
WPARAM  = ctypes.c_size_t
LPARAM  = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
UINT    = ctypes.c_uint
INT     = ctypes.c_int


def H(v):
    """Wrap a handle value as c_void_p — prevents 64-bit truncation."""
    return v if isinstance(v, ctypes.c_void_p) else ctypes.c_void_p(v)


# ── Callback types ────────────────────────────────────────────────────────────
WNDPROC  = ctypes.WINFUNCTYPE(LRESULT, HANDLE, UINT, WPARAM, LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, INT, WPARAM, LPARAM)


# ── Structures ────────────────────────────────────────────────────────────────
class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize",        UINT),
        ("style",         UINT),
        ("lpfnWndProc",   WNDPROC),
        ("cbClsExtra",    INT),
        ("cbWndExtra",    INT),
        ("hInstance",     HANDLE),
        ("hIcon",         HANDLE),
        ("hCursor",       HANDLE),
        ("hbrBackground", HANDLE),
        ("lpszMenuName",  wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
        ("hIconSm",       HANDLE),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd",     HANDLE),
        ("message",  UINT),
        ("wParam",   WPARAM),
        ("lParam",   LPARAM),
        ("time",     wt.DWORD),
        ("pt",       wt.POINT),
        ("lPrivate", wt.DWORD),
    ]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc",        HANDLE),
        ("fErase",     wt.BOOL),
        ("rcPaint",    wt.RECT),
        ("fRestore",   wt.BOOL),
        ("fIncUpdate", wt.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      wt.DWORD),
        ("scanCode",    wt.DWORD),
        ("flags",       wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt",          wt.POINT),
        ("mouseData",   wt.DWORD),
        ("flags",       wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


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


# ── Win32 function declarations ───────────────────────────────────────────────
def _proto(dll, name, restype, *argtypes):
    f = getattr(dll, name)
    f.restype   = restype
    f.argtypes  = list(argtypes)
    return f


_PRECT = ctypes.POINTER(wt.RECT)
_PPAINTSTRUCT = ctypes.POINTER(_PAINTSTRUCT)
_PMSG = ctypes.POINTER(_MSG)

_GetModuleHandleW   = _proto(_kernel32, "GetModuleHandleW",   HANDLE, wt.LPCWSTR)
_GetSystemMetrics   = _proto(_user32,   "GetSystemMetrics",   INT,    INT)
_SetProcessDPIAware = _proto(_user32,   "SetProcessDPIAware", wt.BOOL)
_RegisterClassExW   = _proto(_user32,   "RegisterClassExW",   wt.ATOM, ctypes.POINTER(_WNDCLASSEXW))
_CreateWindowExW    = _proto(_user32,   "CreateWindowExW",    HANDLE,
                              wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                              INT, INT, INT, INT, HANDLE, HANDLE, HANDLE, ctypes.c_void_p)
_DefWindowProcW     = _proto(_user32,   "DefWindowProcW",     LRESULT, HANDLE, UINT, WPARAM, LPARAM)
_ShowWindow         = _proto(_user32,   "ShowWindow",         wt.BOOL, HANDLE, INT)
_UpdateWindow       = _proto(_user32,   "UpdateWindow",       wt.BOOL, HANDLE)
_SetForegroundWindow= _proto(_user32,   "SetForegroundWindow",wt.BOOL, HANDLE)
_SetWindowPos       = _proto(_user32,   "SetWindowPos",       wt.BOOL, HANDLE, HANDLE, INT, INT, INT, INT, UINT)
_DestroyWindow      = _proto(_user32,   "DestroyWindow",      wt.BOOL, HANDLE)
_PostMessageW       = _proto(_user32,   "PostMessageW",       wt.BOOL, HANDLE, UINT, WPARAM, LPARAM)
_PostQuitMessage    = _proto(_user32,   "PostQuitMessage",    None,    INT)
_SetTimer           = _proto(_user32,   "SetTimer",           ctypes.c_size_t, HANDLE, ctypes.c_size_t, UINT, ctypes.c_void_p)
_KillTimer          = _proto(_user32,   "KillTimer",          wt.BOOL, HANDLE, ctypes.c_size_t)
_GetMessageW        = _proto(_user32,   "GetMessageW",        wt.BOOL, _PMSG, HANDLE, UINT, UINT)
_TranslateMessage   = _proto(_user32,   "TranslateMessage",   wt.BOOL, _PMSG)
_DispatchMessageW   = _proto(_user32,   "DispatchMessageW",   LRESULT, _PMSG)
_BeginPaint         = _proto(_user32,   "BeginPaint",         HANDLE,  HANDLE, _PPAINTSTRUCT)
_EndPaint           = _proto(_user32,   "EndPaint",           wt.BOOL, HANDLE, _PPAINTSTRUCT)
_GetClientRect      = _proto(_user32,   "GetClientRect",      wt.BOOL, HANDLE, _PRECT)
_FillRect           = _proto(_user32,   "FillRect",           INT,     HANDLE, _PRECT, HANDLE)
_DrawTextW          = _proto(_user32,   "DrawTextW",          INT,     HANDLE, wt.LPCWSTR, INT, _PRECT, UINT)
_InvalidateRect     = _proto(_user32,   "InvalidateRect",     wt.BOOL, HANDLE, _PRECT, wt.BOOL)
_SetWindowsHookExW  = _proto(_user32,   "SetWindowsHookExW",  HANDLE,  INT, HOOKPROC, HANDLE, wt.DWORD)
_UnhookWindowsHookEx= _proto(_user32,   "UnhookWindowsHookEx",wt.BOOL, HANDLE)
_CallNextHookEx     = _proto(_user32,   "CallNextHookEx",     LRESULT, HANDLE, INT, WPARAM, LPARAM)
_ShowCursor         = _proto(_user32,   "ShowCursor",         INT,     wt.BOOL)
_GetAsyncKeyState   = _proto(_user32,   "GetAsyncKeyState",   ctypes.c_short, INT)

_CreateSolidBrush   = _proto(_gdi32,    "CreateSolidBrush",   HANDLE,  wt.DWORD)
_CreateFontW        = _proto(_gdi32,    "CreateFontW",        HANDLE,
                              INT, INT, INT, INT, INT,
                              wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
                              wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR)
_SelectObject       = _proto(_gdi32,    "SelectObject",       HANDLE,  HANDLE, HANDLE)
_DeleteObject       = _proto(_gdi32,    "DeleteObject",       wt.BOOL, HANDLE)
_SetTextColor       = _proto(_gdi32,    "SetTextColor",       wt.DWORD,HANDLE, wt.DWORD)
_SetBkMode          = _proto(_gdi32,    "SetBkMode",          INT,     HANDLE, INT)
_StretchDIBits      = _proto(_gdi32,    "StretchDIBits",      INT,
                              HANDLE, INT, INT, INT, INT, INT, INT, INT, INT,
                              ctypes.c_void_p, ctypes.POINTER(_BITMAPINFOHEADER),
                              UINT, wt.DWORD)
_UnregisterClassW   = _proto(_user32,   "UnregisterClassW",   wt.BOOL, wt.LPCWSTR, HANDLE)
_SetLayeredWindowAttributes = _proto(_user32, "SetLayeredWindowAttributes", wt.BOOL,
                                      HANDLE, wt.DWORD, ctypes.c_byte, wt.DWORD)
try:
    _SetWindowDisplayAffinity = _proto(_user32, "SetWindowDisplayAffinity", wt.BOOL,
                                        HANDLE, wt.DWORD)
except AttributeError:
    _SetWindowDisplayAffinity = None

# ── Win32 constants ───────────────────────────────────────────────────────────
CLASS_NAME      = "PrivacyScreenOverlay"  # fixed name — lets FindWindowW locate the overlay

WS_POPUP        = 0x80000000
WS_VISIBLE      = 0x10000000
WS_EX_TOPMOST   = 0x00000008
WS_EX_TOOLWINDOW= 0x00000080
WS_EX_LAYERED   = 0x00080000
WS_EX_TRANSPARENT = 0x00000020  # click-through: injected input falls to the desktop behind
WS_EX_NOACTIVATE  = 0x08000000  # never steal keyboard focus from the controlled app
CS_VREDRAW, CS_HREDRAW = 0x0001, 0x0002

WM_DESTROY      = 0x0002
WM_PAINT        = 0x000F
WM_CLOSE        = 0x0010
WM_ERASEBKGND   = 0x0014
WM_DISPLAYCHANGE= 0x007E
WM_TIMER        = 0x0113
WM_KEYDOWN      = 0x0100
WM_KEYUP        = 0x0101
WM_SYSKEYDOWN   = 0x0104
WM_SYSKEYUP     = 0x0105

WH_KEYBOARD_LL  = 13
WH_MOUSE_LL     = 14
HC_ACTION       = 0

HWND_TOPMOST    = -1
SWP_NOSIZE      = 0x0001
SWP_NOMOVE      = 0x0002
SWP_NOACTIVATE  = 0x0010
SWP_SHOWWINDOW  = 0x0040

SW_SHOW         = 5
TRANSPARENT     = 1
SRCCOPY         = 0x00CC0020
DT_CENTER       = 0x0001
DT_VCENTER      = 0x0004
DT_SINGLELINE   = 0x0020
DT_WORDBREAK    = 0x0010

VK_F12          = 0x7B
VK_SHIFT        = 0x10
VK_CONTROL      = 0x11
VK_MENU         = 0x12
VK_LSHIFT, VK_RSHIFT   = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU     = 0xA4, 0xA5

TIMER_ID        = 1
SW_HIDE         = 0
SW_SHOWNA       = 8                   # show without activating (keeps focus on controlled app)
LWA_ALPHA       = 0x00000002          # SetLayeredWindowAttributes: use bAlpha channel
WDA_EXCLUDEFROMCAPTURE = 0x00000011   # overlay invisible to ALL screen-capture APIs (Win10 2004+)

INJECT_TAG      = 0x1EADBEEF  # dwExtraInfo marker: injected remote-control input

# ── Global state ──────────────────────────────────────────────────────────────
_active_screen = None
_state_lock    = threading.Lock()


class PrivacyScreen:
    """Full-screen lock overlay. Pure Win32 GDI — no tkinter dependency."""

    def __init__(self, message=None, pin=None, overlay_image=None, overlay_html=None):
        self.message       = message or "Remote support session in progress.\nPlease wait."
        self.pin           = str(pin) if pin else None
        self.overlay_image = overlay_image
        self.overlay_html  = overlay_html
        self._last_error    = None
        self._hwnd          = H(None)
        self._webview_proc  = None
        self._tmp_html      = None
        self._active       = False
        self._thread       = None
        # Keep callback objects alive (GC protection)
        self._wnd_proc_cb  = None
        self._kb_proc_cb   = None
        self._ms_proc_cb   = None
        self._kb_hook      = H(None)
        self._ms_hook      = H(None)
        self._brush        = H(None)
        # image pixel data
        self._img_pixels   = None
        self._img_bih      = None
        self._img_w        = 0
        self._img_h        = 0
        # keyboard state
        self._pressed      = set()
        # virtual screen geometry (updated on WM_DISPLAYCHANGE)
        self._vx = self._vy = self._vw = self._vh = 0

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
        if self._hwnd.value:
            try:
                _PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        proc = self._webview_proc
        if proc:
            try:
                proc.terminate()
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
            if self.overlay_html:
                self._run_webview()
            else:
                self._run_win32()
        except Exception as e:
            self._last_error = str(e)
        finally:
            self._remove_hooks()
            self._active = False
            if self._brush.value:
                _DeleteObject(self._brush)
                self._brush = H(None)
            with _state_lock:
                global _active_screen
                if _active_screen is self:
                    _active_screen = None

    # ── DPI + screen geometry ─────────────────────────────────────────────────

    def _set_dpi_aware(self):
        try:
            f = _proto(_user32, "SetProcessDpiAwarenessContext", wt.BOOL, HANDLE)
            if f(H(-4)):
                return
        except (AttributeError, OSError):
            pass
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
            return
        except (AttributeError, OSError):
            pass
        try:
            _SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

    def _read_virtual_screen(self):
        self._vx = _GetSystemMetrics(76)
        self._vy = _GetSystemMetrics(77)
        self._vw = _GetSystemMetrics(78)
        self._vh = _GetSystemMetrics(79)
        if self._vw <= 0 or self._vh <= 0:
            self._vx, self._vy = 0, 0
            self._vw = _GetSystemMetrics(0)
            self._vh = _GetSystemMetrics(1)

    # ── Win32 GDI renderer ────────────────────────────────────────────────────

    def _run_win32(self):
        self._set_dpi_aware()
        self._read_virtual_screen()

        if self.overlay_image:
            self._prepare_image(self._vw, self._vh)

        hinst        = H(_GetModuleHandleW(None))
        cls_name     = CLASS_NAME
        self._brush  = H(_CreateSolidBrush(0x00101010))

        screen = self

        def wnd_proc(hwnd, msg, wparam, lparam):
            hwnd = H(hwnd)
            try:
                if msg == WM_TIMER:
                    _SetWindowPos(hwnd, H(HWND_TOPMOST), 0, 0, 0, 0,
                                  SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
                    return 0
                if msg == WM_ERASEBKGND:
                    return 1
                if msg == WM_PAINT:
                    screen._paint(hwnd)
                    return 0
                if msg == WM_DISPLAYCHANGE:
                    screen._read_virtual_screen()
                    _SetWindowPos(hwnd, H(HWND_TOPMOST),
                                  screen._vx, screen._vy, screen._vw, screen._vh,
                                  SWP_NOACTIVATE | SWP_SHOWWINDOW)
                    _InvalidateRect(hwnd, None, True)
                    return 0
                if msg == WM_CLOSE:
                    _DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    _KillTimer(hwnd, TIMER_ID)
                    screen._remove_hooks()
                    _PostQuitMessage(0)
                    return 0
            except Exception:
                pass
            return _DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_cb = WNDPROC(wnd_proc)

        wc = _WNDCLASSEXW()
        wc.cbSize        = ctypes.sizeof(_WNDCLASSEXW)
        wc.style         = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc   = self._wnd_proc_cb
        wc.hInstance     = hinst
        wc.hCursor       = H(None)
        wc.hbrBackground = self._brush
        wc.lpszClassName = cls_name

        atom = _RegisterClassExW(ctypes.byref(wc))
        if not atom:
            err = ctypes.get_last_error()
            if err != 1410:
                raise RuntimeError(f"RegisterClassExW failed err={err}")

        self._hwnd = H(_CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE,
            cls_name, "",
            WS_POPUP | WS_VISIBLE,
            self._vx, self._vy, self._vw, self._vh,
            None, None, hinst, None,
        ))
        if not self._hwnd.value:
            _UnregisterClassW(cls_name, hinst)
            raise RuntimeError(f"CreateWindowExW failed err={ctypes.get_last_error()}")

        _SetLayeredWindowAttributes(self._hwnd, 0, 255, LWA_ALPHA)
        if _SetWindowDisplayAffinity is not None:
            _SetWindowDisplayAffinity(self._hwnd, WDA_EXCLUDEFROMCAPTURE)
        _ShowWindow(self._hwnd, SW_SHOWNA)
        _UpdateWindow(self._hwnd)
        _SetTimer(self._hwnd, TIMER_ID, 250, None)

        self._install_hooks(hinst)

        for _ in range(50):
            if _ShowCursor(False) < 0:
                break

        try:
            msg_obj = _MSG()
            while True:
                r = _GetMessageW(ctypes.byref(msg_obj), None, 0, 0)
                if r == 0 or r == -1:
                    break
                _TranslateMessage(ctypes.byref(msg_obj))
                _DispatchMessageW(ctypes.byref(msg_obj))
        finally:
            for _ in range(50):
                if _ShowCursor(True) >= 0:
                    break
            _UnregisterClassW(cls_name, hinst)

    def _paint(self, hwnd):
        ps  = _PAINTSTRUCT()
        hdc = H(_BeginPaint(hwnd, ctypes.byref(ps)))
        try:
            if not hdc.value:
                return
            rc = wt.RECT()
            _GetClientRect(hwnd, ctypes.byref(rc))

            if self._img_pixels and self._img_bih:
                _StretchDIBits(hdc,
                               0, 0, self._vw, self._vh,
                               0, 0, self._img_w, self._img_h,
                               self._img_pixels,
                               ctypes.byref(self._img_bih),
                               0, SRCCOPY)
            else:
                _FillRect(hdc, ctypes.byref(rc), self._brush)
                _SetBkMode(hdc, TRANSPARENT)
                _SetTextColor(hdc, 0x00F4F4F5)
                height = max(24, self._vh // 20)
                font = H(_CreateFontW(-height, 0, 0, 0, 400, 0, 0, 0,
                                      1, 0, 0, 5, 0, "Segoe UI"))
                old = H(_SelectObject(hdc, font))
                txt_rc = wt.RECT(self._vw // 6, self._vh // 3,
                                 5 * self._vw // 6, 2 * self._vh // 3)
                _DrawTextW(hdc, self.message, -1,
                           ctypes.byref(txt_rc), DT_CENTER | DT_WORDBREAK)
                _SelectObject(hdc, old)
                _DeleteObject(font)
        finally:
            _EndPaint(hwnd, ctypes.byref(ps))

    def _prepare_image(self, vw, vh):
        try:
            from PIL import Image
            raw     = base64.b64decode(self.overlay_image)
            img     = Image.open(io.BytesIO(raw)).convert("RGB").resize((vw, vh), Image.LANCZOS)
            r, g, b = img.split()
            bgr_img = Image.merge("RGB", (b, g, r))
            self._img_pixels = bgr_img.tobytes()
            self._img_w = vw
            self._img_h = vh
            bih = _BITMAPINFOHEADER()
            bih.biSize      = ctypes.sizeof(_BITMAPINFOHEADER)
            bih.biWidth     = vw
            bih.biHeight    = -vh
            bih.biPlanes    = 1
            bih.biBitCount  = 24
            bih.biCompression = 0
            self._img_bih = bih
        except Exception:
            pass

    # ── HTML renderer (Edge kiosk) ────────────────────────────────────────────

    def _run_webview(self):
        import subprocess
        self._set_dpi_aware()
        self._read_virtual_screen()

        fd, path = tempfile.mkstemp(suffix=".html", prefix="nora_prv_")
        self._tmp_html = path
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.overlay_html)

        url = "file:///" + path.replace("\\", "/")

        edge_exe = self._find_edge()
        if not edge_exe:
            self._run_win32()
            return

        proc = subprocess.Popen([
            edge_exe, "--kiosk", url,
            "--edge-kiosk-type=fullscreen",
            "--no-first-run",
            "--disable-extensions",
        ])
        self._webview_proc = proc

        # Hidden window — provides the message loop LL hooks require
        hinst    = H(_GetModuleHandleW(None))
        cls_name = f"NoraPrivacyMsg_{id(self)}"
        screen   = self

        def wnd_proc(hwnd, msg, wparam, lparam):
            hwnd = H(hwnd)
            try:
                if msg == WM_TIMER:
                    if not screen._active:
                        _PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    return 0
                if msg == WM_CLOSE:
                    _DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    _KillTimer(hwnd, TIMER_ID)
                    _PostQuitMessage(0)
                    return 0
            except Exception:
                pass
            return _DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_cb = WNDPROC(wnd_proc)

        wc = _WNDCLASSEXW()
        wc.cbSize        = ctypes.sizeof(_WNDCLASSEXW)
        wc.lpfnWndProc   = self._wnd_proc_cb
        wc.hInstance     = hinst
        wc.lpszClassName = cls_name
        _RegisterClassExW(ctypes.byref(wc))

        msg_hwnd = H(_CreateWindowExW(
            0, cls_name, "", 0,
            0, 0, 0, 0, None, None, hinst, None,
        ))
        self._hwnd = msg_hwnd
        _SetTimer(msg_hwnd, TIMER_ID, 250, None)
        self._install_hooks(hinst)

        try:
            msg_obj = _MSG()
            while True:
                r = _GetMessageW(ctypes.byref(msg_obj), None, 0, 0)
                if r == 0 or r == -1:
                    break
                _TranslateMessage(ctypes.byref(msg_obj))
                _DispatchMessageW(ctypes.byref(msg_obj))
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
            _UnregisterClassW(cls_name, hinst)

    def _find_edge(self):
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe") as k:
                return winreg.QueryValue(k, None)
        except Exception:
            pass
        return None

    # ── Win32 hooks ───────────────────────────────────────────────────────────

    def _install_hooks(self, hinst):
        screen = self

        def _kb_hook(nCode, wParam, lParam):
            if nCode == HC_ACTION:
                try:
                    kb = _KBDLLHOOKSTRUCT.from_address(lParam)
                    if kb.dwExtraInfo == INJECT_TAG:
                        return _CallNextHookEx(screen._kb_hook, nCode, wParam, lParam)
                    vk       = kb.vkCode
                    is_down  = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    if is_down:
                        screen._pressed.add(vk)
                    else:
                        screen._pressed.discard(vk)

                    def _dn(*vks):
                        return any(v in screen._pressed for v in vks)

                    if (is_down and vk == VK_F12
                            and _dn(VK_LCONTROL, VK_RCONTROL, VK_CONTROL)
                            and _dn(VK_LMENU, VK_RMENU, VK_MENU)
                            and _dn(VK_LSHIFT, VK_RSHIFT, VK_SHIFT)):
                        screen.force_stop()
                        return 1
                except Exception:
                    pass
                return 1   # swallow every key
            return _CallNextHookEx(screen._kb_hook, nCode, wParam, lParam)

        def _ms_hook(nCode, wParam, lParam):
            if nCode == HC_ACTION:
                try:
                    ms = _MSLLHOOKSTRUCT.from_address(lParam)
                    if ms.dwExtraInfo == INJECT_TAG:
                        return _CallNextHookEx(screen._ms_hook, nCode, wParam, lParam)
                except Exception:
                    pass
                return 1   # swallow all local mouse input
            return _CallNextHookEx(screen._ms_hook, nCode, wParam, lParam)

        self._kb_proc_cb = HOOKPROC(_kb_hook)
        self._ms_proc_cb = HOOKPROC(_ms_hook)
        self._kb_hook = H(_SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc_cb, hinst, 0))
        self._ms_hook = H(_SetWindowsHookExW(WH_MOUSE_LL,    self._ms_proc_cb, hinst, 0))

    def _remove_hooks(self):
        for attr in ("_kb_hook", "_ms_hook"):
            h = getattr(self, attr)
            if h and h.value:
                try:
                    _UnhookWindowsHookEx(h)
                except Exception:
                    pass
                setattr(self, attr, H(None))


# ── Module-level API ──────────────────────────────────────────────────────────

def start_privacy_screen(message=None, pin=None, overlay_image=None, overlay_html=None):
    import time
    global _active_screen
    with _state_lock:
        if _active_screen and _active_screen.active:
            return False, "already_active"
    ps = PrivacyScreen(
        message=message,
        pin=pin,
        overlay_image=overlay_image,
        overlay_html=overlay_html,
    )
    with _state_lock:
        _active_screen = ps
    ps.start()
    time.sleep(2.0)
    if not ps._active:
        err = ps._last_error or "Win32 window creation error"
        return False, f"failed ({err})"
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


def get_overlay_hwnd():
    """Return the integer HWND of the active overlay, or None."""
    with _state_lock:
        ps = _active_screen
    if ps is None or not ps.active:
        return None
    return ps._hwnd.value


def hide_overlay():
    """
    Temporarily hide the overlay (call before a desktop screenshot).
    Returns the HWND integer so the caller can pass it to show_overlay().
    Returns None if no overlay is active.
    """
    with _state_lock:
        ps = _active_screen
    if ps is None or not ps.active or not ps._hwnd.value:
        return None
    hwnd = ctypes.c_void_p(ps._hwnd.value)
    _ShowWindow(hwnd, SW_HIDE)
    return ps._hwnd.value


def show_overlay(hwnd_val=None):
    """
    Restore the overlay after hide_overlay().
    Pass the value returned by hide_overlay(), or leave None to auto-detect.
    """
    if hwnd_val is None:
        with _state_lock:
            ps = _active_screen
        if ps is None or not ps._hwnd.value:
            return
        hwnd_val = ps._hwnd.value
    hwnd = ctypes.c_void_p(hwnd_val)
    _ShowWindow(hwnd, SW_SHOW)
    _SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                  SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
