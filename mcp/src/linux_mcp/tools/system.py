"""Read-only system info: CPU, RAM, disk, OS. No confirmation needed —
nothing here mutates state, so these bypass the policy/confirmation
pipeline that shell.py uses (there's nothing risky to gate)."""
from __future__ import annotations

import platform

import psutil

from linux_mcp.schemas import ToolResult


def get_system_info(_args=None) -> ToolResult:
    data = {
        "os": platform.platform(),
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "cpu_count": psutil.cpu_count(),
        "memory": dict(psutil.virtual_memory()._asdict()),
        "disk": dict(psutil.disk_usage("/")._asdict()),
    }
    return ToolResult(ok=True, data=data)


TOOL_SPEC = {
    "name": "system_info",
    "description": "Get CPU, RAM, disk, and OS info.",
    "args_model": None,
    "handler": get_system_info,
}
