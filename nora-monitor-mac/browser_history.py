"""
macOS browser history — Chrome, Edge, Firefox, Safari.
On macOS Chrome/Edge don't lock the SQLite file so simple copy works.
Safari uses a binary plist format.
"""
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


def _copy_safe(src, dst):
    try:
        shutil.copy2(src, dst)
    except Exception:
        pass


def _chromium_history(profile_path, browser, limit=500):
    history_db = profile_path / "History"
    if not history_db.exists():
        return []
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp_path = tmp.name
    _copy_safe(history_db, tmp_path)
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
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return rows


def _firefox_history(limit=500):
    home = Path.home()
    profiles_dir = home / "Library" / "Application Support" / "Firefox" / "Profiles"
    if not profiles_dir.exists():
        return []
    rows = []
    for profile_dir in profiles_dir.iterdir():
        if not profile_dir.is_dir():
            continue
        places_db = profile_dir / "places.sqlite"
        if not places_db.exists():
            continue
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
            tmp_path = tmp.name
        _copy_safe(places_db, tmp_path)
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
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return rows


def _safari_history(limit=500):
    try:
        import plistlib
        home = Path.home()
        history_plist = home / "Library" / "Safari" / "History.plist"
        if not history_plist.exists():
            return []
        with tempfile.NamedTemporaryFile(delete=False, suffix=".plist") as tmp:
            tmp_path = tmp.name
        _copy_safe(history_plist, tmp_path)
        rows = []
        try:
            with open(tmp_path, "rb") as f:
                data = plistlib.load(f)
            items = data.get("WebHistoryDates", [])
            for item in items[:limit]:
                url = item.get("", "") or item.get("URL", "")
                title = item.get("title", "")
                ts_str = item.get("lastVisitedDate", "")
                ts = 0
                if ts_str:
                    try:
                        from datetime import datetime
                        dt = datetime(2001, 1, 1)
                        ts = int(dt.timestamp()) + int(float(ts_str))
                    except Exception:
                        pass
                rows.append({
                    "browser": "Safari",
                    "url": url,
                    "title": title,
                    "visits": 1,
                    "last_visit_ts": ts,
                })
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return rows
    except Exception:
        return []


def export_all(limit=500):
    home = Path.home()
    app_support = home / "Library" / "Application Support"
    all_history = []

    browsers = {
        "Chrome": app_support / "Google" / "Chrome" / "Default",
        "Edge": app_support / "Microsoft Edge" / "Default",
        "Brave": app_support / "BraveSoftware" / "Brave-Browser" / "Default",
        "Chromium": app_support / "Chromium" / "Default",
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

    try:
        all_history.extend(_safari_history(limit))
    except Exception:
        pass

    all_history.sort(key=lambda x: x.get("last_visit_ts", 0), reverse=True)
    return all_history
