"""The shell tool. All safety logic lives in security/pipeline.py —
this module's only job is building the argv and calling it.

NOTE: this is *not* a security sandbox. The workspace is only the starting
directory; a command can `cd` or use absolute paths anywhere the server's user can
reach. Protection comes from the policy blocklist and human approval, not from the cwd.
"""
from __future__ import annotations

from linux_mcp.config import settings
from linux_mcp.schemas import ShellArgs, ToolResult
from linux_mcp.security import policy
from linux_mcp.security.pipeline import guarded_execute
from linux_mcp.tools import applications
from linux_mcp.utils import workspace

# A command that starts a GUI app is given this long to finish; if it is still running it is
# left running (not killed) and reported as started.
GUI_GRACE_SECONDS = 3


def _resolve_cwd(cwd: str | None) -> str:
    """The shell always starts inside the workspace and cannot be pointed outside it. The
    command may still read system paths; this only fixes where it runs from."""
    root = workspace.workspace_root()
    target = workspace.resolve_within(cwd) if cwd else root   # raises on an escaping cwd
    if not target.is_dir():
        raise ValueError(f"cwd is not a directory inside the workspace: {target}")
    return str(target)


def run_shell(args: ShellArgs) -> ToolResult:
    try:
        cwd = _resolve_cwd(args.cwd)
    except (ValueError, OSError) as e:
        return ToolResult(ok=False, error=str(e))
    launches_gui = bool(set(policy.programs(args.command)) & applications.gui_executables())
    return guarded_execute(
        tool_name="shell",
        argv=["bash", "-c", args.command],
        description=args.command,
        readonly=False,
        cwd=cwd,
        timeout=args.timeout_seconds or settings.shell_timeout_seconds,
        detach_after=GUI_GRACE_SECONDS if launches_gui else None,
    )


TOOL_SPEC = {
    "name": "shell",
    "description": "Run a bash command (starts in the workspace directory). Output is combined "
                   "stdout+stderr; a non-zero exit is reported as '[exit code: N]'. "
                   "Commands have no stdin and are killed after the timeout. Graphical apps (firefox, ...) "
                   "are left running in the background automatically. To open a web page, prefer open_url.",
    "args_model": ShellArgs,
    "handler": run_shell,
}