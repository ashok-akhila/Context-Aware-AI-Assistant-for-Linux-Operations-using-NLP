"""
context/system_info.py

Collects REAL system information from the Linux environment the app is
running on. Nothing here is invented or simulated — every value comes from
platform/os/psutil calls against the live machine.
"""

import os
import platform
import getpass
import psutil
import time


def get_distro_info() -> dict:
    """Read /etc/os-release for distro name/version (standard on Ubuntu/Debian/etc)."""
    info = {"name": "unknown", "version": "unknown", "pretty_name": "unknown"}
    os_release_path = "/etc/os-release"
    if os.path.exists(os_release_path):
        try:
            with open(os_release_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    value = value.strip('"')
                    if key == "NAME":
                        info["name"] = value
                    elif key == "VERSION_ID":
                        info["version"] = value
                    elif key == "PRETTY_NAME":
                        info["pretty_name"] = value
        except OSError:
            pass
    return info


def is_wsl() -> bool:
    """Detect whether we're running inside WSL (WSL kernels mention 'microsoft')."""
    try:
        with open("/proc/version", "r") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def get_current_user_context() -> dict:
    """Real current user, uid/gid, privilege level, home dir, shell, cwd."""
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER", "unknown")

    uid = os.getuid() if hasattr(os, "getuid") else None
    gid = os.getgid() if hasattr(os, "getgid") else None
    is_root = (uid == 0) if uid is not None else False

    # Check sudo group membership (does NOT mean currently elevated — just capability)
    has_sudo_group = False
    try:
        import grp
        sudo_group_names = {"sudo", "wheel", "admin"}
        user_groups = {g.gr_name for g in grp.getgrall() if user in g.gr_mem}
        primary_gid = gid
        if primary_gid is not None:
            try:
                primary_group = grp.getgrgid(primary_gid).gr_name
                user_groups.add(primary_group)
            except KeyError:
                pass
        has_sudo_group = bool(user_groups & sudo_group_names)
    except (ImportError, PermissionError, OSError):
        pass

    return {
        "username": user,
        "uid": uid,
        "gid": gid,
        "is_root": is_root,
        "has_sudo_capability": has_sudo_group,
        "home_directory": os.path.expanduser("~"),
        "shell": os.environ.get("SHELL", "unknown"),
        "current_working_directory": os.getcwd(),
    }


def get_cpu_info() -> dict:
    """Real CPU usage snapshot. interval=0.5 gives an accurate (non-zero) reading."""
    percent = psutil.cpu_percent(interval=0.5)

    freq_mhz = None
    try:
        freq = psutil.cpu_freq()
        if freq is not None:
            freq_mhz = round(freq.current, 1)
    except (NotImplementedError, OSError, FileNotFoundError):
        # cpu_freq() throws on some WSL2/container/VM setups without cpufreq
        # support rather than returning None — treat that the same as
        # "unavailable" instead of letting it crash the whole endpoint.
        freq_mhz = None

    try:
        load_avg = os.getloadavg() if hasattr(os, "getloadavg") else None
    except OSError:
        load_avg = None

    return {
        "usage_percent": percent,
        "core_count_logical": psutil.cpu_count(logical=True),
        "core_count_physical": psutil.cpu_count(logical=False),
        "current_freq_mhz": freq_mhz,
        "load_average_1_5_15": load_avg,
    }


def get_memory_info() -> dict:
    """Real RAM and swap usage."""
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_gb": round(vm.total / (1024 ** 3), 2),
        "used_gb": round(vm.used / (1024 ** 3), 2),
        "available_gb": round(vm.available / (1024 ** 3), 2),
        "used_percent": vm.percent,
        "swap_total_gb": round(swap.total / (1024 ** 3), 2),
        "swap_used_gb": round(swap.used / (1024 ** 3), 2),
        "swap_percent": swap.percent,
    }


def get_uptime() -> dict:
    boot_ts = psutil.boot_time()
    uptime_seconds = time.time() - boot_ts
    return {
        "boot_time_epoch": boot_ts,
        "uptime_hours": round(uptime_seconds / 3600, 2),
    }


def get_full_system_context() -> dict:
    """
    The single entry point other modules call to get a full, real snapshot.
    This is what gets attached to LLM prompts as ground-truth context.
    """
    distro = get_distro_info()
    return {
        "hostname": platform.node(),
        "kernel_version": platform.release(),
        "platform_system": platform.system(),  # e.g. 'Linux'
        "machine_arch": platform.machine(),
        "distro": distro,
        "is_wsl": is_wsl(),
        "python_version": platform.python_version(),
        "user": get_current_user_context(),
        "cpu": get_cpu_info(),
        "memory": get_memory_info(),
        "uptime": get_uptime(),
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_full_system_context(), indent=2))
