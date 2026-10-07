"""systemd service control, entirely through the shared pipeline. Status
checks are marked readonly (skip confirmation); start/stop/restart/
enable/disable are not."""
from __future__ import annotations

from linux_mcp.adapters.systemd import build_command
from linux_mcp.schemas import ServiceControlArgs, ToolResult
from linux_mcp.security.pipeline import guarded_execute


def control_service(args: ServiceControlArgs) -> ToolResult:
    try:
        argv = build_command(args.unit, args.action)
    except ValueError as e:  # was uncaught -> surfaced as a raw exception
        return ToolResult(ok=False, error=str(e))
    return guarded_execute(
        tool_name="services",
        argv=argv,
        description=f"{args.action} service {args.unit}",
        readonly=(args.action == "status"),
        # `systemctl status` exits 3 for an inactive unit, 4 if it doesn't exist. The status
        # text is the whole point, and used to be thrown away as a "failure".
        allow_nonzero=(args.action == "status"),
    )


TOOL_SPEC = {"name": "control_service", "description": "start/stop/restart/status/enable/disable a systemd unit.",
             "args_model": ServiceControlArgs, "handler": control_service, "gate": "services"}
