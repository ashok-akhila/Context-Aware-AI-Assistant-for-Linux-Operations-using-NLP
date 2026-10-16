"""
operations/file_operations.py

Builds concrete shell commands for common file operations from structured
parameters (never from raw user/LLM strings directly). Every command built
here still passes through security.command_validator before execution.
"""

import shlex


def build_delete_command(path: str) -> str:
    return f"rm {shlex.quote(path)}"


def build_delete_recursive_command(path: str) -> str:
    return f"rm -r {shlex.quote(path)}"


def build_list_directory_command(path: str) -> str:
    return f"ls -lah {shlex.quote(path)}"


def build_find_files_command(base_path: str, name_pattern: str) -> str:
    # kept as a single find invocation (no pipes) to satisfy the validator
    return f"find {shlex.quote(base_path)} -iname {shlex.quote(name_pattern)}"


def build_change_permissions_command(path: str, mode: str) -> str:
    # mode expected like '644' / '755' — validated by caller before building
    if not mode.isdigit() or not (3 <= len(mode) <= 4):
        raise ValueError("mode must be an octal permission string like '644'")
    return f"chmod {mode} {shlex.quote(path)}"


def build_copy_command(src: str, dst: str) -> str:
    return f"cp {shlex.quote(src)} {shlex.quote(dst)}"


def build_move_command(src: str, dst: str) -> str:
    return f"mv {shlex.quote(src)} {shlex.quote(dst)}"
