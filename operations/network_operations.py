"""
operations/network_operations.py

All network diagnostics here are read-only (LOW risk) — connectivity
checks, interface status, DNS lookups. No firewall/interface modification
commands are built by this module.
"""

import shlex


def build_interface_status_command() -> str:
    return "ip addr"


def build_connectivity_check_command(host: str = "8.8.8.8") -> str:
    safe_host = shlex.quote(host)
    return f"ping -c 4 {safe_host}"


def build_dns_check_command(domain: str = "google.com") -> str:
    safe_domain = shlex.quote(domain)
    return f"nslookup {safe_domain}"


def build_route_command() -> str:
    return "ip route"
