"""
operations/process_operations.py

Builds process-termination commands. Killing is always high-risk and
always requires confirmation with the actual process detail shown first
(see context/process_info.get_process_by_pid).
"""


def build_kill_command(pid: int, force: bool = False) -> str:
    if not isinstance(pid, int) or pid <= 1:
        raise ValueError("Refusing to build a kill command for an invalid or PID 1 (init) target.")
    signal = "-9" if force else "-15"
    return f"kill {signal} {pid}"
