"""
context/process_info.py

Real running-process information via psutil. No process is ever invented.
"""

import psutil
import getpass


def get_all_processes(sort_by: str = "cpu", limit: int = 15) -> list:
    """
    Real snapshot of running processes. sort_by: 'cpu' or 'memory'.
    Two-pass cpu_percent is used so the first reading isn't always 0.0.
    """
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "status"]):
        try:
            p.cpu_percent(None)  # prime the internal counter
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    import time
    time.sleep(0.3)

    for p in psutil.process_iter(["pid", "name", "username", "status"]):
        try:
            info = p.info
            cpu = p.cpu_percent(None)
            mem = p.memory_percent()
            procs.append({
                "pid": info["pid"],
                "name": info["name"],
                "username": info.get("username"),
                "status": info.get("status"),
                "cpu_percent": round(cpu, 1),
                "memory_percent": round(mem, 2),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
    procs.sort(key=lambda x: x[key], reverse=True)
    return procs[:limit]


def get_my_processes(limit: int = 15) -> list:
    """Real processes owned by the current user only."""
    me = getpass.getuser()
    all_procs = get_all_processes(sort_by="cpu", limit=1000)
    mine = [p for p in all_procs if p.get("username") == me]
    return mine[:limit]


def get_process_by_pid(pid: int) -> dict:
    """Detailed real info for a single process, for confirming before any kill action."""
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            return {
                "pid": p.pid,
                "name": p.name(),
                "username": p.username(),
                "status": p.status(),
                "cpu_percent": p.cpu_percent(interval=0.3),
                "memory_percent": round(p.memory_percent(), 2),
                "cmdline": p.cmdline(),
                "create_time": p.create_time(),
            }
    except psutil.NoSuchProcess:
        return {"error": f"No process with pid {pid} exists."}
    except psutil.AccessDenied:
        return {"error": f"Access denied reading process {pid}."}


if __name__ == "__main__":
    import json
    print(json.dumps({
        "top_cpu": get_all_processes(sort_by="cpu", limit=5),
        "my_processes": get_my_processes(limit=5),
    }, indent=2))
