"""Adapter for controlling systemd units. Separate from PackageAdapter —
this is a service-control adapter, used by tools/services.py."""
from __future__ import annotations

from linux_mcp.utils.executor import run_readonly
from linux_mcp.validation import UNIT_NAME_RE

VALID_ACTIONS = {"start", "stop", "restart", "status", "enable", "disable"}


def build_command(unit: str, action: str) -> list[str]:
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unsupported systemd action: {action}")
    if not UNIT_NAME_RE.match(unit):
        raise ValueError(f"Invalid unit name: {unit!r}")
    cmd = ["systemctl", action]
    if action == "status":
        cmd.append("--no-pager")
    return [*cmd, unit]


def status(unit: str) -> str:
    return run_readonly(build_command(unit, "status"), allow_nonzero=True)
