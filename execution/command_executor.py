"""
execution/command_executor.py

Executes an ALREADY-VALIDATED, ALREADY-CONFIRMED command against the real
system and returns the actual stdout/stderr/return code. Never fakes output.

Deliberately uses shell=False + shlex.split so shell metacharacters (which
command_validator already rejects earlier in the pipeline) can't do anything
even if they somehow got this far — defense in depth.
"""

import subprocess
import shlex
import time

from security.command_validator import validate_command


class ExecutionResult:
    def __init__(self, executed: bool, command: str, stdout: str = "", stderr: str = "",
                 return_code: int = None, duration_seconds: float = 0.0, block_reason: str = None):
        self.executed = executed
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code
        self.duration_seconds = duration_seconds
        self.block_reason = block_reason

    def to_dict(self):
        return {
            "executed": self.executed,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "success": self.executed and self.return_code == 0,
            "duration_seconds": round(self.duration_seconds, 3),
            "block_reason": self.block_reason,
        }


def execute_command(command: str, user_confirmed: bool, timeout_seconds: int = 20) -> ExecutionResult:
    """
    The ONLY function in the whole app that actually runs a shell command.
    Every call path (system monitoring, service checks, cleanup ops) must
    funnel through here so the safety gate can never be bypassed.

    Rules enforced here, non-negotiably:
      1. Command must pass validate_command() (structural + risk check).
      2. If the risk tier requires confirmation, user_confirmed must be True.
      3. Never shell=True. Never sudo unless the user typed a command that
         itself already contains 'sudo' AND explicitly confirmed it.
    """
    validation = validate_command(command)

    if not validation.allowed:
        return ExecutionResult(
            executed=False, command=command,
            block_reason=f"BLOCKED by safety layer: {validation.reason}",
        )

    if validation.requires_confirm and not user_confirmed:
        return ExecutionResult(
            executed=False, command=command,
            block_reason="Confirmation required but not given. Command was not executed.",
        )

    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return ExecutionResult(executed=False, command=command, block_reason=f"Parse error: {e}")

    start = time.time()
    try:
        proc = subprocess.run(
            tokens,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        duration = time.time() - start
        return ExecutionResult(
            executed=True,
            command=command,
            stdout=proc.stdout,
            stderr=proc.stderr,
            return_code=proc.returncode,
            duration_seconds=duration,
        )
    except FileNotFoundError:
        return ExecutionResult(
            executed=False, command=command,
            block_reason=f"Command not found: '{tokens[0]}' is not an installed/known binary.",
        )
    except subprocess.TimeoutExpired:
        return ExecutionResult(
            executed=False, command=command,
            block_reason=f"Command timed out after {timeout_seconds}s and was killed.",
        )
    except PermissionError:
        return ExecutionResult(
            executed=False, command=command,
            block_reason="Permission denied attempting to execute this command.",
        )


if __name__ == "__main__":
    # LOW risk, no confirmation needed
    r1 = execute_command("df -h", user_confirmed=False)
    print("df -h (no confirm needed):", r1.to_dict())

    # HIGH risk, confirmation withheld -> should be blocked
    r2 = execute_command("kill -9 999999", user_confirmed=False)
    print("kill without confirm (should block):", r2.to_dict())

    # CRITICAL -> always blocked even with confirmation
    r3 = execute_command("rm -rf /", user_confirmed=True)
    print("rm -rf / even WITH confirm (must stay blocked):", r3.to_dict())
