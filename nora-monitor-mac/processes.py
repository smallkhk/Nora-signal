import psutil


def get_processes():
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
        try:
            info = p.info
            procs.append({
                "pid": info["pid"],
                "name": info["name"] or "?",
                "cpu": round(info["cpu_percent"] or 0, 1),
                "mem": round((info["memory_info"].rss if info["memory_info"] else 0) / (1024 * 1024), 1),
                "status": info["status"] or "",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x["mem"], reverse=True)
    return procs


def kill_process(pid):
    try:
        p = psutil.Process(pid)
        p.terminate()
        return True
    except Exception:
        return False
