import os
import json
import sqlite3
import shutil
import tempfile
import ctypes
import ctypes.wintypes
from pathlib import Path


def _copy_locked(src, dst):
    try:
        GENERIC_READ = 0x80000000
        FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
        OPEN_EXISTING = 3
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


def _chromium_history(profile_path, browser, limit=500):
    history_db = profile_path / "History"
    if not history_db.exists():
        return []
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp_path = tmp.name
    _copy_locked(history_db, tmp_path)
    rows = []
    try:
        con = sqlite3.connect(tmp_path)
        cur = con.execute(
            "SELECT url, title, visit_count, last_visit_time "
            "FROM urls ORDER BY last_visit_time DESC LIMIT ?",
            (limit,),
        )
        for url, title, visits, last_visit in cur.fetchall():
            ts = 0
            if last_visit:
                ts = int((last_visit / 1_000_000) - 11644473600)
            rows.append({
                "browser": browser,
                "url": url or "",
                "title": title or "",
                "visits": visits or 0,
                "last_visit_ts": ts,
            })
        con.close()
    finally:
        os.unlink(tmp_path)
    return rows


def _firefox_history(limit=500):
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
        places_db = profile_dir / "places.sqlite"
        if not places_db.exists():
            continue
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
            tmp_path = tmp.name
        _copy_locked(places_db, tmp_path)
        try:
            con = sqlite3.connect(tmp_path)
            cur = con.execute(
                "SELECT p.url, p.title, p.visit_count, "
                "  (SELECT MAX(v.visit_date) FROM moz_historyvisits v WHERE v.place_id = p.id) "
                "FROM moz_places p WHERE p.visit_count > 0 "
                "ORDER BY p.last_visit_date DESC LIMIT ?",
                (limit,),
            )
            for url, title, visits, last_visit in cur.fetchall():
                ts = int(last_visit / 1_000_000) if last_visit else 0
                rows.append({
                    "browser": "Firefox",
                    "url": url or "",
                    "title": title or "",
                    "visits": visits or 0,
                    "last_visit_ts": ts,
                })
            con.close()
        finally:
            os.unlink(tmp_path)
    return rows


def export_all(limit=500):
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    all_history = []
    browsers = {
        "Chrome": local / "Google" / "Chrome" / "User Data" / "Default",
        "Edge": local / "Microsoft" / "Edge" / "User Data" / "Default",
    }
    for name, profile in browsers.items():
        if profile.exists():
            try:
                all_history.extend(_chromium_history(profile, name, limit))
            except Exception:
                pass
    try:
        all_history.extend(_firefox_history(limit))
    except Exception:
        pass
    all_history.sort(key=lambda x: x.get("last_visit_ts", 0), reverse=True)
    return all_history
