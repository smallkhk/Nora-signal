"""
Standalone browser password tester.
Run from any folder — no project files needed.
Requires: pip install pycryptodome pywin32 psutil
"""
import base64, json, os, shutil, sqlite3, struct, tempfile, ctypes

_LOCAL = os.environ.get("LOCALAPPDATA", "")
_BROWSERS = {
    "Chrome": os.path.join(_LOCAL, r"Google\Chrome\User Data"),
    "Edge":   os.path.join(_LOCAL, r"Microsoft\Edge\User Data"),
}

# ── Extract AES master key via DPAPI ─────────────────────────────────────────

def _get_key(local_state_path):
    with open(local_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    blob = base64.b64decode(data["os_crypt"]["encrypted_key"])[5:]
    import win32crypt
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]

# ── Decrypt one password blob ─────────────────────────────────────────────────

def _decrypt(blob, key):
    if not blob:
        return ""
    b = bytes(blob)
    prefix = b[:3]
    print(f"      [blob prefix={prefix!r} len={len(b)}]")

    if prefix in (b"v10", b"v11"):
        from Crypto.Cipher import AES
        iv  = b[3:15]
        enc = b[15:-16]
        tag = b[-16:]
        try:
            return AES.new(key, AES.MODE_GCM, iv).decrypt_and_verify(enc, tag).decode("utf-8", errors="replace")
        except Exception as e:
            return f"[AES-GCM failed: {e}]"

    if prefix == b"v20":
        # Chrome 127+ App-Bound Encryption: extra 32-byte header before nonce
        from Crypto.Cipher import AES
        iv  = b[35:47]
        enc = b[47:-16]
        tag = b[-16:]
        try:
            return AES.new(key, AES.MODE_GCM, iv).decrypt_and_verify(enc, tag).decode("utf-8", errors="replace")
        except Exception as e:
            return f"[v20 AES-GCM failed: {e}]"

    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(b, None, None, None, 0)[1].decode("utf-8", errors="replace")
    except Exception as e:
        return f"[DPAPI failed: {e}  | first8={b[:8].hex()}]"

# ── Main ──────────────────────────────────────────────────────────────────────

for browser, user_data in _BROWSERS.items():
    if not os.path.isdir(user_data):
        continue
    local_state = os.path.join(user_data, "Local State")
    if not os.path.isfile(local_state):
        continue

    print(f"\n{'='*50}\n {browser}\n{'='*50}")

    try:
        key = _get_key(local_state)
        print(f"  Key: {key.hex()}")
    except Exception as e:
        print(f"  [FAIL key] {e}")
        continue

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

        entries = [(url, usr, _decrypt(pw, key)) for url, usr, pw in rows if url or usr]
        if not entries:
            continue
        print(f"\n  [{profile}] — {len(entries)} entries")
        for url, usr, pw in entries:
            print(f"    {url}")
            print(f"      user: {usr}")
            print(f"      pass: {pw}")
