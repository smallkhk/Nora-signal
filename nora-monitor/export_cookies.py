"""
export_cookies.py  —  Run on your SOURCE Windows PC
Exports Chrome, Edge, and Firefox cookies to a portable JSON file.

Requirements (install once):
    pip install pycryptodome pywin32

Usage:
    python export_cookies.py
    -> writes cookies_export.json in the same folder
"""

import os
import json
import shutil
import sqlite3
import base64
import tempfile
from pathlib import Path

# ── Chrome / Edge helpers ──────────────────────────────────────────────────────

def _get_chrome_key(local_state_path: Path) -> bytes:
    import win32crypt
    from Crypto.Cipher import AES

    with open(local_state_path, encoding="utf-8") as f:
        state = json.load(f)

    encrypted_key = base64.b64decode(state["os_crypt"]["encrypted_key"])
    encrypted_key = encrypted_key[5:]  # strip "DPAPI" prefix
    return win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]


def _decrypt_value(key: bytes, encrypted_value: bytes) -> str:
    if not encrypted_value:
        return ""
    try:
        from Crypto.Cipher import AES
        # v10/v11 format: b"v10" + 12-byte nonce + ciphertext + 16-byte tag
        if encrypted_value[:3] in (b"v10", b"v11"):
            nonce = encrypted_value[3:15]
            tag = encrypted_value[-16:]
            ciphertext = encrypted_value[15:-16]
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8", errors="replace")
        else:
            import win32crypt
            return win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1].decode(
                "utf-8", errors="replace"
            )
    except Exception:
        return ""


def export_chromium_cookies(profile_path: Path, local_state_path: Path, browser: str) -> list:
    cookie_db = profile_path / "Cookies"
    if not cookie_db.exists():
        print(f"  [{browser}] Cookie file not found, skipping.")
        return []

    try:
        key = _get_chrome_key(local_state_path)
    except Exception as e:
        print(f"  [{browser}] Could not read encryption key: {e}")
        return []

    # Copy DB to temp so Chrome doesn't lock us out
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp_path = tmp.name
    shutil.copy2(cookie_db, tmp_path)

    rows = []
    try:
        con = sqlite3.connect(tmp_path)
        cur = con.execute(
            "SELECT host_key, name, encrypted_value, path, expires_utc, "
            "is_secure, is_httponly, samesite, source_scheme FROM cookies"
        )
        for row in cur.fetchall():
            host, name, enc_val, path, expires, secure, httponly, samesite, scheme = row
            value = _decrypt_value(key, enc_val)
            rows.append(
                {
                    "browser": browser,
                    "host": host,
                    "name": name,
                    "value": value,
                    "path": path,
                    "expires_utc": expires,
                    "is_secure": bool(secure),
                    "is_httponly": bool(httponly),
                    "samesite": samesite,
                    "source_scheme": scheme,
                }
            )
        con.close()
    finally:
        os.unlink(tmp_path)

    print(f"  [{browser}] Exported {len(rows)} cookies.")
    return rows


# ── Firefox helpers ────────────────────────────────────────────────────────────

def export_firefox_cookies() -> list:
    appdata = Path(os.environ.get("APPDATA", ""))
    profiles_ini = appdata / "Mozilla" / "Firefox" / "profiles.ini"
    if not profiles_ini.exists():
        print("  [Firefox] profiles.ini not found, skipping.")
        return []

    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(profiles_ini)

    rows = []
    for section in cfg.sections():
        if not section.startswith("Profile"):
            continue
        is_default = cfg.get(section, "Default", fallback="0") == "1"
        rel_path = cfg.get(section, "Path", fallback=None)
        if not rel_path:
            continue
        is_relative = cfg.get(section, "IsRelative", fallback="1") == "1"
        profile_dir = (
            (appdata / "Mozilla" / "Firefox" / rel_path) if is_relative else Path(rel_path)
        )
        cookie_db = profile_dir / "cookies.sqlite"
        if not cookie_db.exists():
            continue

        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
            tmp_path = tmp.name
        shutil.copy2(cookie_db, tmp_path)

        try:
            con = sqlite3.connect(tmp_path)
            cur = con.execute(
                "SELECT host, name, value, path, expiry, isSecure, isHttpOnly, sameSite "
                "FROM moz_cookies"
            )
            for row in cur.fetchall():
                host, name, value, path, expiry, secure, httponly, samesite = row
                rows.append(
                    {
                        "browser": "Firefox",
                        "host": host,
                        "name": name,
                        "value": value,
                        "path": path,
                        "expires_utc": expiry * 1_000_000,  # normalise to Chrome epoch
                        "is_secure": bool(secure),
                        "is_httponly": bool(httponly),
                        "samesite": samesite,
                        "source_scheme": 0,
                    }
                )
            con.close()
        finally:
            os.unlink(tmp_path)

        label = f"Firefox ({section}{'*' if is_default else ''})"
        print(f"  [{label}] Exported {len(rows)} cookies so far.")

    return rows


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    appdata_local = Path(os.environ.get("LOCALAPPDATA", ""))
    appdata_roaming = Path(os.environ.get("APPDATA", ""))

    browsers = {
        "Chrome": {
            "profile": appdata_local / "Google" / "Chrome" / "User Data" / "Default",
            "local_state": appdata_local / "Google" / "Chrome" / "User Data" / "Local State",
        },
        "Edge": {
            "profile": appdata_local / "Microsoft" / "Edge" / "User Data" / "Default",
            "local_state": appdata_local / "Microsoft" / "Edge" / "User Data" / "Local State",
        },
    }

    all_cookies = []

    for name, paths in browsers.items():
        if paths["profile"].exists():
            print(f"\nExporting {name}...")
            all_cookies.extend(
                export_chromium_cookies(paths["profile"], paths["local_state"], name)
            )
        else:
            print(f"\n[{name}] Not installed / profile not found, skipping.")

    print("\nExporting Firefox...")
    all_cookies.extend(export_firefox_cookies())

    out_file = Path("cookies_export.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_cookies, f, indent=2, ensure_ascii=False)

    print(f"\nDone! {len(all_cookies)} cookies saved to {out_file.resolve()}")
    print("Transfer this file to your second PC and run import_cookies.py there.")


if __name__ == "__main__":
    main()
