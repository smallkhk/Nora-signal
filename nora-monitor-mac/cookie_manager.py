"""
macOS cookie export/import for Chrome, Edge, Brave, Firefox.
Chrome on macOS encrypts cookies with the macOS Keychain (v10 format).
"""
import os
import shutil
import sqlite3
import tempfile
import base64
import json
from pathlib import Path


def _get_chrome_key(browser_name="Chrome"):
    """Get the AES key from macOS Keychain for Chrome cookie decryption."""
    try:
        import subprocess
        service = {
            "Chrome": "Chrome Safe Storage",
            "Edge": "Microsoft Edge Safe Storage",
            "Brave": "Brave Safe Storage",
            "Chromium": "Chromium Safe Storage",
        }.get(browser_name, "Chrome Safe Storage")
        result = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", service],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        import hashlib
        key = hashlib.pbkdf2_hmac("sha1", result.encode(), b"saltysalt", 1003, dklen=16)
        return key
    except Exception:
        return None


def _decrypt_cookie_value(encrypted_value, key):
    """Decrypt Chrome v10 cookie (AES-CBC, 16-byte key, IV = space*16)."""
    try:
        if not encrypted_value or not encrypted_value.startswith(b"v10"):
            return encrypted_value.decode(errors="replace") if encrypted_value else ""
        from Cryptodome.Cipher import AES
        iv = b" " * 16
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted_value[3:])
        pad_len = decrypted[-1]
        return decrypted[:-pad_len].decode(errors="replace")
    except Exception:
        return ""


def _export_chromium(profile_path, browser, key):
    cookies_db = profile_path / "Cookies"
    if not cookies_db.exists():
        return []
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp_path = tmp.name
    shutil.copy2(cookies_db, tmp_path)
    rows = []
    try:
        con = sqlite3.connect(tmp_path)
        cur = con.execute(
            "SELECT host_key, name, encrypted_value, path, expires_utc, "
            "is_secure, is_httponly, samesite FROM cookies"
        )
        for host, name, enc_val, path, expires, secure, httponly, samesite in cur.fetchall():
            value = _decrypt_cookie_value(enc_val, key) if key else ""
            rows.append({
                "browser": browser,
                "domain": host,
                "name": name,
                "value": value,
                "path": path,
                "expires": expires,
                "secure": bool(secure),
                "httpOnly": bool(httponly),
                "sameSite": samesite,
            })
        con.close()
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return rows


def export_all():
    home = Path.home()
    app_support = home / "Library" / "Application Support"
    all_cookies = []
    browsers = {
        "Chrome": app_support / "Google" / "Chrome" / "Default",
        "Edge": app_support / "Microsoft Edge" / "Default",
        "Brave": app_support / "BraveSoftware" / "Brave-Browser" / "Default",
        "Chromium": app_support / "Chromium" / "Default",
    }
    for name, profile in browsers.items():
        if profile.exists():
            try:
                key = _get_chrome_key(name)
                all_cookies.extend(_export_chromium(profile, name, key))
            except Exception:
                pass
    return all_cookies


def import_all(cookies):
    # Cookie import on macOS is complex (Keychain re-encryption), return info
    return {
        "ok": False,
        "message": "Cookie import not supported on macOS — use exported JSON to restore manually",
    }
