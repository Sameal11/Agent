"""List/inspect/kill processes. list_processes is a direct psutil read
(no shell command involved, so it bypasses the pipeline entirely — see
tools/system.py for the same pattern). kill_process goes through the
shared pipeline since sending a signal is a mutating action worth
auditing and gating the same way a shell command would be."""
from __future__ import annotations

import os
import signal

import psutil

from linux_mcp.schemas import KillProcessArgs, ListProcessesArgs, ToolResult
from linux_mcp.security.pipeline import guarded_call


def list_processes(args: ListProcessesArgs) -> ToolResult:
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "memory_percent"]):
        if args.name_filter and args.name_filter.lower() not in (p.info["name"] or "").lower():
            continue
        procs.append(p.info)
    procs.sort(key=lambda i: i.get("memory_percent") or 0.0, reverse=True)
    return ToolResult(ok=True, data=procs[: args.limit])


def kill_process(args: KillProcessArgs) -> ToolResult:
    if args.pid in (os.getpid(), os.getppid()):
        return ToolResult(ok=False, error="Refusing to signal the MCP server or the agent that launched it.")
    try:
        name = psutil.Process(args.pid).name()
    except psutil.Error:
        name = "?"
    sig = getattr(signal, args.signal)

    def do_kill() -> str:
        os.kill(args.pid, sig)
        return f"Sent {args.signal} to pid {args.pid} ({name})"

    return guarded_call(
        tool_name="kill_process",
        action=do_kill,
        description=f"kill pid {args.pid} ({name}) with {args.signal}",
        audit_label=["kill", f"-{args.signal}", str(args.pid)],
    )


LIST_TOOL_SPEC = {"name": "list_processes", "description": "List running processes (highest memory first).",
                   "args_model": ListProcessesArgs, "handler": list_processes}
KILL_TOOL_SPEC = {"name": "kill_process", "description": "Send SIGTERM/SIGKILL/SIGINT/SIGHUP to a process by pid.",
                   "args_model": KillProcessArgs, "handler": kill_process}
