"""
context/file_info.py

Real file existence/permission/ownership analysis — answers "why can't I
access X" using actual stat() calls, not guesses.
"""

import os
import stat
import pwd
import grp
import getpass


def _mode_to_string(mode: int) -> str:
    return stat.filemode(mode)


def _owner_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _group_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def analyze_path(path: str) -> dict:
    """
    Real analysis of a file/directory: existence, permissions, owner, group,
    and whether the CURRENT user can read/write/execute it, plus the same
    for the parent directory (a common reason for "permission denied").
    """
    abspath = os.path.abspath(os.path.expanduser(path))
    current_user = getpass.getuser()
    current_uid = os.getuid()

    result = {
        "requested_path": path,
        "resolved_path": abspath,
        "exists": os.path.exists(abspath),
        "current_user": current_user,
    }

    if not result["exists"]:
        parent = os.path.dirname(abspath) or "/"
        result["parent_directory"] = _analyze_existing(parent, current_uid)
        result["note"] = "Path does not exist. See parent_directory for whether it could be created here."
        return result

    st = os.stat(abspath)
    result.update({
        "is_directory": os.path.isdir(abspath),
        "is_symlink": os.path.islink(abspath),
        "size_bytes": st.st_size,
        "permissions_octal": oct(stat.S_IMODE(st.st_mode)),
        "permissions_string": _mode_to_string(st.st_mode),
        "owner": _owner_name(st.st_uid),
        "group": _group_name(st.st_gid),
        "current_user_can_read": os.access(abspath, os.R_OK),
        "current_user_can_write": os.access(abspath, os.W_OK),
        "current_user_can_execute": os.access(abspath, os.X_OK),
    })

    parent = os.path.dirname(abspath) or "/"
    result["parent_directory"] = _analyze_existing(parent, current_uid)
    return result


def _analyze_existing(dirpath: str, current_uid: int) -> dict:
    try:
        st = os.stat(dirpath)
        return {
            "path": dirpath,
            "exists": True,
            "permissions_string": _mode_to_string(st.st_mode),
            "owner": _owner_name(st.st_uid),
            "current_user_can_write": os.access(dirpath, os.W_OK),
            "current_user_can_execute": os.access(dirpath, os.X_OK),
        }
    except (FileNotFoundError, PermissionError) as e:
        return {"path": dirpath, "exists": False, "error": str(e)}


if __name__ == "__main__":
    import json
    print(json.dumps(analyze_path("/etc/shadow"), indent=2))
    print(json.dumps(analyze_path("/home/claude/ai-linux-assistant/app.py"), indent=2))
