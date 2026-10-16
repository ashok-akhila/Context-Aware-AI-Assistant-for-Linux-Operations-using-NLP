"""
context/disk_info.py

Real disk/partition usage collection, plus on-demand "largest directories"
analysis. No sizes are ever invented — everything comes from psutil.disk_usage
and os.walk over the real filesystem.
"""

import os
import psutil


def get_partitions() -> list:
    """Real mounted partitions with usage. Skips pseudo-filesystems that error out."""
    partitions = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, FileNotFoundError, OSError):
            continue
        partitions.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": round(usage.total / (1024 ** 3), 2),
            "used_gb": round(usage.used / (1024 ** 3), 2),
            "free_gb": round(usage.free / (1024 ** 3), 2),
            "used_percent": usage.percent,
        })
    return partitions


def get_root_disk_summary() -> dict:
    """Quick summary of the root '/' filesystem — usually what users mean by 'my disk'."""
    usage = psutil.disk_usage("/")
    return {
        "mountpoint": "/",
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "free_gb": round(usage.free / (1024 ** 3), 2),
        "used_percent": usage.percent,
    }


def find_largest_directories(base_path: str = "/", top_n: int = 10, max_depth: int = 2) -> list:
    """
    Walk the real filesystem (bounded by max_depth to stay fast/safe) and
    report the largest immediate subdirectories under base_path.
    Directories we can't read (permission denied) are skipped, not guessed at.
    """
    results = []
    base_path = os.path.abspath(base_path)

    try:
        entries = [
            os.path.join(base_path, d)
            for d in os.listdir(base_path)
            if os.path.isdir(os.path.join(base_path, d)) and not os.path.islink(os.path.join(base_path, d))
        ]
    except (PermissionError, FileNotFoundError):
        return results

    for entry in entries:
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(entry, onerror=lambda e: None):
                # depth limiting relative to `entry`
                depth = dirpath[len(entry):].count(os.sep)
                if depth >= max_depth:
                    dirnames[:] = []
                for fname in filenames:
                    fpath = os.path.join(dirpath, fname)
                    try:
                        if not os.path.islink(fpath):
                            total_size += os.path.getsize(fpath)
                    except (OSError, FileNotFoundError):
                        continue
        except (PermissionError, OSError):
            continue

        results.append({
            "path": entry,
            "size_mb": round(total_size / (1024 ** 2), 2),
        })

    results.sort(key=lambda x: x["size_mb"], reverse=True)
    return results[:top_n]


def find_largest_files(base_path: str = "/", top_n: int = 10, max_depth: int = 4, min_size_mb: float = 1.0) -> list:
    """Find the largest individual files under base_path, real filesystem walk."""
    base_path = os.path.abspath(base_path)
    candidates = []

    for dirpath, dirnames, filenames in os.walk(base_path, onerror=lambda e: None):
        depth = dirpath[len(base_path):].count(os.sep)
        if depth >= max_depth:
            dirnames[:] = []
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                if os.path.islink(fpath):
                    continue
                size = os.path.getsize(fpath)
                size_mb = size / (1024 ** 2)
                if size_mb >= min_size_mb:
                    candidates.append({"path": fpath, "size_mb": round(size_mb, 2)})
            except (OSError, FileNotFoundError):
                continue

    candidates.sort(key=lambda x: x["size_mb"], reverse=True)
    return candidates[:top_n]


if __name__ == "__main__":
    import json
    print(json.dumps({
        "partitions": get_partitions(),
        "root_summary": get_root_disk_summary(),
        "largest_dirs_under_var": find_largest_directories("/var", top_n=5),
    }, indent=2))
