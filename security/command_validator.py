"""
security/command_validator.py

Validates an LLM-proposed structured action BEFORE it's ever shown to the
user or considered for execution. Combines risk classification with
structural checks (shell metacharacter injection, disallowed binaries,
sudo policy).

This is the gatekeeper described in spec §22: the LLM never gets raw shell
access — it proposes a structured action, and THIS module decides whether
that action is even eligible to proceed to the confirmation step.
"""

import shlex
from security.risk_classifier import classify_risk, requires_confirmation, CRITICAL

# Binaries we will never execute, full stop, regardless of risk framing.
HARD_BLOCKED_BINARIES = {
    "mkfs", "fdisk", "parted", "dd", "shutdown", "reboot", "halt", "poweroff",
    "init", "telinit", ":(){:|:&};:",
}

# Shell features we refuse to allow in a generated command, because they
# make static risk analysis unreliable (chaining, substitution, redirection
# into arbitrary places, background execution).
DISALLOWED_SHELL_TOKENS = ["&&", "||", ";", "`", "$(", "|", ">", ">>", "<"]


class ValidationResult:
    def __init__(self, allowed: bool, risk_level: str, reason: str, requires_confirm: bool):
        self.allowed = allowed
        self.risk_level = risk_level
        self.reason = reason
        self.requires_confirm = requires_confirm

    def to_dict(self):
        return {
            "allowed": self.allowed,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "requires_confirmation": self.requires_confirm,
        }


def validate_command(command: str, allow_pipes_for_readonly: bool = True) -> ValidationResult:
    """
    Full validation pipeline for a single proposed shell command string.
    Returns a ValidationResult; callers must check .allowed before doing
    anything else with the command.
    """
    if not command or not command.strip():
        return ValidationResult(False, CRITICAL, "Empty command.", False)

    cmd = command.strip()

    # 1. Never allow sudo to be auto-approved — it always needs explicit,
    #    high-friction confirmation regardless of the underlying binary.
    # (handled downstream by risk classifier forcing HIGH minimum)

    # 2. Reject shell injection surface. We allow a narrow pipe exception for
    #    a couple of common, genuinely read-only diagnostic chains (e.g.
    #    "ps aux | grep ssh"), but only if every segment is itself LOW risk.
    try:
        tokens = shlex.split(cmd)
    except ValueError as e:
        return ValidationResult(False, CRITICAL, f"Unparseable command syntax: {e}", False)

    if not tokens:
        return ValidationResult(False, CRITICAL, "Empty command after parsing.", False)

    first_binary = tokens[0].replace("sudo", "").strip() or (tokens[1] if len(tokens) > 1 else "")

    if any(b in cmd for b in HARD_BLOCKED_BINARIES):
        return ValidationResult(False, CRITICAL, "Command uses a hard-blocked, always-dangerous utility.", False)

    disallowed_found = [t for t in DISALLOWED_SHELL_TOKENS if t in cmd]
    if disallowed_found:
        if allow_pipes_for_readonly and disallowed_found == ["|"]:
            # Split on pipe and risk-check every segment independently.
            segments = [s.strip() for s in cmd.split("|")]
            segment_risks = [classify_risk(s) for s in segments]
            if all(r["level"] == "low" and not r["blocked"] for r in segment_risks):
                pass  # all segments are read-only; allow the pipe through
            else:
                return ValidationResult(
                    False, CRITICAL,
                    "Piped command contains a non-read-only segment; piping is only allowed "
                    "when every segment is a low-risk read-only command.",
                    False,
                )
        else:
            return ValidationResult(
                False, CRITICAL,
                f"Command contains disallowed shell metacharacters {disallowed_found}. "
                "Chaining/redirection/substitution is not permitted in generated commands.",
                False,
            )

    # 3. Deterministic risk classification (backend authority, not the LLM's).
    risk = classify_risk(cmd)
    if risk["blocked"]:
        return ValidationResult(False, risk["level"], risk["reason"], False)

    return ValidationResult(
        True,
        risk["level"],
        risk["reason"],
        requires_confirm=requires_confirmation(risk["level"]),
    )


if __name__ == "__main__":
    tests = [
        "df -h",
        "ps aux | grep ssh",
        "ps aux | grep ssh && rm -rf /",
        "rm -rf /",
        "systemctl restart ssh; rm important.txt",
        "cat /etc/passwd > /tmp/leak.txt",
        "kill -9 1234",
    ]
    for t in tests:
        r = validate_command(t)
        print(f"{t!r:45} -> {r.to_dict()}")
