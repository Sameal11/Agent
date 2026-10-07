"""Read-only network info: interfaces and connections."""
from __future__ import annotations

import psutil

from linux_mcp.schemas import ToolResult


def network_info(_args=None) -> ToolResult:
    data = {"interfaces": {k: [a._asdict() for a in v] for k, v in psutil.net_if_addrs().items()}}
    try:
        data["connections"] = [c._asdict() for c in psutil.net_connections(kind="inet")[:50]]
    except psutil.AccessDenied:
        data["connections"] = "unavailable: permission denied (needs elevated privileges on this system)"
    return ToolResult(ok=True, data=data)


TOOL_SPEC = {"name": "network_info", "description": "List network interfaces and active connections.",
             "args_model": None, "handler": network_info}
