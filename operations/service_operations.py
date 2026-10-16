"""
operations/service_operations.py

Builds systemctl commands for service troubleshooting. Restart/stop/start
are always high-risk and always require confirmation — enforced downstream
by the risk classifier, not just here.
"""

import re


def _safe_service_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\-_.@]", "", name)
    if not cleaned:
        raise ValueError("Invalid service name.")
    return cleaned


def build_status_command(service_name: str) -> str:
    return f"systemctl status {_safe_service_name(service_name)}"


def build_restart_command(service_name: str) -> str:
    return f"sudo systemctl restart {_safe_service_name(service_name)}"


def build_start_command(service_name: str) -> str:
    return f"sudo systemctl start {_safe_service_name(service_name)}"


def build_stop_command(service_name: str) -> str:
    return f"sudo systemctl stop {_safe_service_name(service_name)}"


def build_enable_command(service_name: str) -> str:
    return f"sudo systemctl enable {_safe_service_name(service_name)}"
