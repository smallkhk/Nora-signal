import os
import platform
import socket
import time

import psutil


def get_info():
    info = {}
    try:
        uname = platform.uname()
        info["hostname"] = uname.node
        info["os"] = f"{uname.system} {uname.release}"
        info["os_version"] = uname.version
        info["arch"] = uname.machine
        info["processor"] = uname.processor or platform.processor() or "?"
    except Exception:
        pass

    try:
        info["cpu_cores_physical"] = psutil.cpu_count(logical=False)
        info["cpu_cores_logical"] = psutil.cpu_count(logical=True)
        info["cpu_percent"] = psutil.cpu_percent(interval=0.5)
    except Exception:
        pass

    try:
        mem = psutil.virtual_memory()
        info["ram_total_gb"] = round(mem.total / (1024 ** 3), 1)
        info["ram_used_gb"] = round(mem.used / (1024 ** 3), 1)
        info["ram_percent"] = mem.percent
    except Exception:
        pass

    try:
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "mount": part.mountpoint,
                    "fs": part.fstype,
                    "total_gb": round(usage.total / (1024 ** 3), 1),
                    "used_gb": round(usage.used / (1024 ** 3), 1),
                    "percent": usage.percent,
                })
            except Exception:
                pass
        info["disks"] = disks
    except Exception:
        pass

    try:
        boot = psutil.boot_time()
        uptime_s = int(time.time() - boot)
        days, rem = divmod(uptime_s, 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        info["uptime"] = f"{days}d {hours}h {mins}m"
        info["uptime_seconds"] = uptime_s
    except Exception:
        pass

    try:
        addrs = []
        for iface, snics in psutil.net_if_addrs().items():
            for snic in snics:
                if snic.family == socket.AF_INET and not snic.address.startswith("127."):
                    addrs.append({"iface": iface, "ip": snic.address})
        info["network"] = addrs
    except Exception:
        pass

    try:
        info["username"] = os.getlogin()
    except Exception:
        try:
            info["username"] = os.environ.get("USERNAME") or os.environ.get("USER", "?")
        except Exception:
            pass

    try:
        bat = psutil.sensors_battery()
        if bat:
            info["battery"] = {"percent": bat.percent, "plugged": bat.power_plugged}
    except Exception:
        pass

    return info
