"""
logs/log_analyzer.py

Controlled Linux log retrieval. Never dumps entire logs — always scoped by
service name, recency, and line count. Uses journalctl (systemd) with a
fallback to reading /var/log/syslog directly if journalctl isn't available
(e.g. some minimal WSL setups without systemd running).
"""

import subprocess
import shutil
import os


MAX_LINES_DEFAULT = 50


def _run_readonly(tokens: list, timeout: int = 10) -> dict:
    try:
        proc = subprocess.run(tokens, capture_output=True, text=True, timeout=timeout)
        return {"stdout": proc.stdout, "stderr": proc.stderr, "return_code": proc.returncode}
    except FileNotFoundError:
        return {"stdout": "", "stderr": f"{tokens[0]} not found", "return_code": -1}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "timed out", "return_code": -1}


def get_service_status(service_name: str) -> dict:
    """Real systemctl status for a named service. Read-only."""
    safe_name = "".join(c for c in service_name if c.isalnum() or c in "-_.@")
    if not safe_name:
        return {"error": "Invalid service name."}

    if shutil.which("systemctl") is None:
        return {"error": "systemctl not available on this system (no systemd)."}

    result = _run_readonly(["systemctl", "status", safe_name, "--no-pager", "-l"])
    active = _run_readonly(["systemctl", "is-active", safe_name])
    enabled = _run_readonly(["systemctl", "is-enabled", safe_name])

    return {
        "service": safe_name,
        "is_active": active["stdout"].strip(),
        "is_enabled": enabled["stdout"].strip(),
        "status_output": result["stdout"] or result["stderr"],
    }


def get_service_logs(service_name: str, lines: int = MAX_LINES_DEFAULT, since: str = "1 hour ago") -> dict:
    """
    Retrieve ONLY the recent, relevant journal lines for a specific service —
    never the entire journal. Falls back to grepping /var/log/syslog for the
    service name if journalctl is unavailable.
    """
    safe_name = "".join(c for c in service_name if c.isalnum() or c in "-_.@")
    lines = max(1, min(lines, 200))  # hard cap so we never ship an entire log to the LLM

    if shutil.which("journalctl") is not None:
        result = _run_readonly([
            "journalctl", "-u", safe_name, "-n", str(lines),
            "--since", since, "--no-pager", "-o", "short-iso",
        ])
        if result["return_code"] == 0 and result["stdout"].strip():
            return {"source": "journalctl", "service": safe_name, "log_lines": result["stdout"].splitlines()}

    # Fallback: grep syslog directly, still bounded by `lines`
    syslog_path = "/var/log/syslog"
    if os.path.exists(syslog_path) and os.access(syslog_path, os.R_OK):
        grep_result = _run_readonly(["grep", "-i", safe_name, syslog_path])
        matched = grep_result["stdout"].splitlines()[-lines:]
        return {"source": "syslog_grep", "service": safe_name, "log_lines": matched}

    return {"source": "none", "service": safe_name, "log_lines": [],
            "note": "No accessible log source found (no journalctl, no readable /var/log/syslog)."}


def get_recent_errors(lines: int = MAX_LINES_DEFAULT, since: str = "1 hour ago") -> dict:
    """Recent system-wide error-priority log entries only (priority <= err)."""
    lines = max(1, min(lines, 200))
    if shutil.which("journalctl") is not None:
        result = _run_readonly([
            "journalctl", "-p", "err", "-n", str(lines),
            "--since", since, "--no-pager", "-o", "short-iso",
        ])
        return {"source": "journalctl", "log_lines": result["stdout"].splitlines()}
    return {"source": "none", "log_lines": [], "note": "journalctl not available."}


if __name__ == "__main__":
    import json
    print(json.dumps(get_service_status("ssh"), indent=2)[:1000])
    print(json.dumps(get_service_logs("ssh", lines=10), indent=2)[:1000])
