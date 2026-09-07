"""
cookie_manager.py — Programmatic wrapper for export/import cookie scripts.
Called by server.py and relay_client.py in response to socket events.
"""

import json
import os
from pathlib import Path


def export_all() -> list:
    """Return all browser cookies as a list of dicts (Chrome, Edge, Firefox)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("export_cookies", Path(__file__).parent / "export_cookies.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

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
            all_cookies.extend(mod.export_chromium_cookies(paths["profile"], paths["local_state"], name))

    all_cookies.extend(mod.export_firefox_cookies())
    return all_cookies


def import_all(cookies: list) -> dict:
    """Import a cookies list into Chrome, Edge, and Firefox. Returns {ok, message}."""
    import importlib.util, io, contextlib
    spec = importlib.util.spec_from_file_location("import_cookies", Path(__file__).parent / "import_cookies.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    local = Path(os.environ.get("LOCALAPPDATA", ""))
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            chrome_c = [c for c in cookies if c["browser"] == "Chrome"]
            edge_c   = [c for c in cookies if c["browser"] == "Edge"]
            ff_c     = [c for c in cookies if c["browser"] == "Firefox"]

            if chrome_c:
                mod.import_chromium_cookies(
                    local / "Google"    / "Chrome" / "User Data" / "Default",
                    local / "Google"    / "Chrome" / "User Data" / "Local State",
                    "Chrome", chrome_c,
                )
            if edge_c:
                mod.import_chromium_cookies(
                    local / "Microsoft" / "Edge"   / "User Data" / "Default",
                    local / "Microsoft" / "Edge"   / "User Data" / "Local State",
                    "Edge", edge_c,
                )
            if ff_c:
                mod.import_firefox_cookies(ff_c)

        return {"ok": True, "message": out.getvalue().strip()}
    except Exception as e:
        return {"ok": False, "message": str(e)}
