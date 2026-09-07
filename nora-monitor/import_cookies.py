"""
import_cookies.py  —  Run on your TARGET Windows PC
Reads cookies_export.json and writes cookies into Chrome, Edge, and Firefox.

Requirements (install once):
    pip install pycryptodome pywin32

Usage:
    python import_cookies.py
    (cookies_export.json must be in the same folder)

IMPORTANT: Close all browser windows before running.
"""

import os
import json
import shutil
import sqlite3
import secrets
import base64
import tempfile
from pathlib import Path


# ── Chrome / Edge helpers ──────────────────────────────────────────────────────

def _generate_and_save_key(local_state_path: Path) -> bytes:
    """Generate a fresh AES-256 key, store it encrypted with DPAPI in Local State."""
    import win32crypt

    new_key = secrets.token_bytes(32)

    dpapi_blob = win32crypt.CryptProtectData(b"DPAPI" + new_key, None, None, None, None, 0)
    encoded = base64.b64encode(b"DPAPI" + dpapi_blob).decode()

    with open(local_state_path, encoding="utf-8") as f:
        state = json.load(f)

    state.setdefault("os_crypt", {})["encrypted_key"] = encoded

    with open(local_state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    return new_key


def _encrypt_value(key: bytes, value: str) -> bytes:
    from Crypto.Cipher import AES

    nonce = secrets.token_bytes(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(value.encode("utf-8"))
    return b"v10" + nonce + ciphertext + tag


def import_chromium_cookies(
    profile_path: Path, local_state_path: Path, browser: str, cookies: list
) -> None:
    if not profile_path.exists():
        print(f"  [{browser}] Profile path not found, skipping.")
        return

    cookie_db = profile_path / "Cookies"
    # Create DB if missing
    cookie_db.parent.mkdir(parents=True, exist_ok=True)

    key = _generate_and_save_key(local_state_path)

    # Back up existing cookies
    if cookie_db.exists():
        shutil.copy2(cookie_db, cookie_db.with_suffix(".bak"))
        print(f"  [{browser}] Backed up existing cookies to Cookies.bak")

    con = sqlite3.connect(cookie_db)
    con.execute(
        """CREATE TABLE IF NOT EXISTS cookies (
            creation_utc     INTEGER NOT NULL UNIQUE PRIMARY KEY,
            host_key         TEXT NOT NULL,
            name             TEXT NOT NULL,
            value            TEXT NOT NULL,
            path             TEXT NOT NULL,
            expires_utc      INTEGER NOT NULL,
            is_secure        INTEGER NOT NULL,
            is_httponly      INTEGER NOT NULL,
            last_access_utc  INTEGER NOT NULL,
            has_expires      INTEGER NOT NULL DEFAULT 1,
            is_persistent    INTEGER NOT NULL DEFAULT 1,
            priority         INTEGER NOT NULL DEFAULT 1,
            encrypted_value  BLOB DEFAULT '',
            samesite         INTEGER NOT NULL DEFAULT -1,
            source_scheme    INTEGER NOT NULL DEFAULT 0,
            source_port      INTEGER NOT NULL DEFAULT -1,
            is_same_party    INTEGER NOT NULL DEFAULT 0,
            last_update_utc  INTEGER NOT NULL DEFAULT 0
        )"""
    )

    inserted = 0
    now_utc = 13_000_000_000_000_000  # approx Chrome epoch for "now"
    for i, c in enumerate(cookies):
        enc = _encrypt_value(key, c["value"])
        try:
            con.execute(
                """INSERT OR REPLACE INTO cookies
                   (creation_utc, host_key, name, value, path, expires_utc,
                    is_secure, is_httponly, last_access_utc, has_expires,
                    is_persistent, priority, encrypted_value, samesite,
                    source_scheme, source_port, is_same_party, last_update_utc)
                   VALUES (?,?,?,?,?,?,?,?,?,1,1,1,?,?,?,443,0,?)""",
                (
                    now_utc + i,
                    c["host"],
                    c["name"],
                    "",  # plaintext value is empty; browser uses encrypted_value
                    c["path"],
                    c["expires_utc"],
                    int(c["is_secure"]),
                    int(c["is_httponly"]),
                    now_utc,
                    enc,
                    c["samesite"],
                    c["source_scheme"],
                    now_utc,
                ),
            )
            inserted += 1
        except sqlite3.Error:
            pass

    con.commit()
    con.close()
    print(f"  [{browser}] Imported {inserted} cookies.")


# ── Firefox helpers ────────────────────────────────────────────────────────────

def import_firefox_cookies(cookies: list) -> None:
    appdata = Path(os.environ.get("APPDATA", ""))
    profiles_ini = appdata / "Mozilla" / "Firefox" / "profiles.ini"
    if not profiles_ini.exists():
        print("  [Firefox] profiles.ini not found, skipping.")
        return

    import configparser

    cfg = configparser.ConfigParser()
    cfg.read(profiles_ini)

    # Pick the default profile
    profile_dir = None
    for section in cfg.sections():
        if not section.startswith("Profile"):
            continue
        if cfg.get(section, "Default", fallback="0") != "1":
            continue
        rel_path = cfg.get(section, "Path", fallback=None)
        if not rel_path:
            continue
        is_relative = cfg.get(section, "IsRelative", fallback="1") == "1"
        profile_dir = (
            (appdata / "Mozilla" / "Firefox" / rel_path) if is_relative else Path(rel_path)
        )
        break

    if not profile_dir:
        print("  [Firefox] No default profile found, skipping.")
        return

    cookie_db = profile_dir / "cookies.sqlite"
    if cookie_db.exists():
        shutil.copy2(cookie_db, cookie_db.with_suffix(".bak"))
        print("  [Firefox] Backed up existing cookies to cookies.sqlite.bak")

    con = sqlite3.connect(cookie_db)
    con.execute(
        """CREATE TABLE IF NOT EXISTS moz_cookies (
            id          INTEGER PRIMARY KEY,
            originAttributes TEXT NOT NULL DEFAULT '',
            name        TEXT,
            value       TEXT,
            host        TEXT,
            path        TEXT,
            expiry      INTEGER,
            lastAccessed INTEGER,
            creationTime INTEGER,
            isSecure    INTEGER,
            isHttpOnly  INTEGER,
            appId       INTEGER DEFAULT 0,
            inBrowserElement INTEGER DEFAULT 0,
            sameSite    INTEGER DEFAULT 0,
            rawSameSite INTEGER DEFAULT 0,
            schemeMap   INTEGER DEFAULT 0
        )"""
    )

    inserted = 0
    import time

    now_us = int(time.time() * 1_000_000)
    for c in cookies:
        expiry = c["expires_utc"] // 1_000_000  # Chrome stores microseconds; Firefox uses seconds
        try:
            con.execute(
                """INSERT OR REPLACE INTO moz_cookies
                   (name, value, host, path, expiry, lastAccessed, creationTime,
                    isSecure, isHttpOnly, sameSite, rawSameSite)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    c["name"],
                    c["value"],
                    c["host"],
                    c["path"],
                    expiry,
                    now_us,
                    now_us,
                    int(c["is_secure"]),
                    int(c["is_httponly"]),
                    c["samesite"],
                    c["samesite"],
                ),
            )
            inserted += 1
        except sqlite3.Error:
            pass

    con.commit()
    con.close()
    print(f"  [Firefox] Imported {inserted} cookies.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    export_file = Path("cookies_export.json")
    if not export_file.exists():
        print("ERROR: cookies_export.json not found. Copy it from your source PC first.")
        return

    with open(export_file, encoding="utf-8") as f:
        all_cookies = json.load(f)

    print(f"Loaded {len(all_cookies)} cookies from {export_file.name}\n")

    appdata_local = Path(os.environ.get("LOCALAPPDATA", ""))

    chrome_cookies = [c for c in all_cookies if c["browser"] == "Chrome"]
    edge_cookies   = [c for c in all_cookies if c["browser"] == "Edge"]
    ff_cookies     = [c for c in all_cookies if c["browser"] == "Firefox"]

    if chrome_cookies:
        print(f"Importing {len(chrome_cookies)} Chrome cookies...")
        import_chromium_cookies(
            appdata_local / "Google" / "Chrome" / "User Data" / "Default",
            appdata_local / "Google" / "Chrome" / "User Data" / "Local State",
            "Chrome",
            chrome_cookies,
        )

    if edge_cookies:
        print(f"\nImporting {len(edge_cookies)} Edge cookies...")
        import_chromium_cookies(
            appdata_local / "Microsoft" / "Edge" / "User Data" / "Default",
            appdata_local / "Microsoft" / "Edge" / "User Data" / "Local State",
            "Edge",
            edge_cookies,
        )

    if ff_cookies:
        print(f"\nImporting {len(ff_cookies)} Firefox cookies...")
        import_firefox_cookies(ff_cookies)

    print("\nAll done! Open your browsers — you should still be logged in.")
    print("If something looks wrong, restore the *.bak files.")


if __name__ == "__main__":
    main()
