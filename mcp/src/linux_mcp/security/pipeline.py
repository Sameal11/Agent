"""The one execution pipeline every tool goes through.

Every tool that touches the system calls `guarded_execute()` (subprocess) or
`guarded_call()` (anything else, e.g. writing a file), describing WHAT it wants to
do, and the pipeline decides whether it is allowed to happen. The gate logic lives
in exactly one function (`_gate`); the two entry points only differ in how they
perform the action.

Order is the same for every caller:
  1. Is this tool enabled at all?           (permissions.yaml)
  2. Is this exact action ever allowed?      (policy hard-blocklist — no bypass)
  3. Does a human need to approve this time? (see `_needs_approval`)
  4. Run it, and log it either way.          (utils.executor + security.audit)
"""
from __future__ import annotations

from typing import Callable, Optional

from linux_mcp.schemas import ToolResult
from linux_mcp.security import confirmation, permissions, policy
from linux_mcp.security.audit import log_command
from linux_mcp.utils import executor

# Tools whose `description` IS a literal shell command, so policy's command-level
# auto-approve list is meaningful for them. For every other tool the description is
# English ("kill pid 5 with SIGTERM") and must never be matched against that list —
# "kill pid 5..." used to start with the auto-approved word `kill`.
_COMMAND_TOOLS = frozenset({"shell"})


def _needs_approval(tool_name: str, description: str, force_confirm: bool) -> bool:
    if force_confirm:
        return True
    if tool_name in _COMMAND_TOOLS:
        # permissions.yaml `confirm` cannot express "per command", so for shell the
        # policy decides: read-only commands run, everything else asks.
        return policy.needs_confirmation(description)
    return permissions.requires_confirmation(tool_name)


def _gate(tool_name: str, description: str, readonly: bool, force_confirm: bool) -> Optional[ToolResult]:
    """Returns a failing ToolResult if the action must not run, else None."""
    if not permissions.tool_enabled(tool_name):
        return ToolResult(ok=False, error=f"Tool '{tool_name}' is disabled in permissions.yaml")

    try:
        policy.check(description)
    except policy.PolicyViolation as e:
        log_command([tool_name, description], exit_code=-2, readonly=readonly, note="blocked by policy")
        return ToolResult(ok=False, error=str(e))

    if not readonly and _needs_approval(tool_name, description, force_confirm):
        if not confirmation.confirm(f"[{tool_name}] {description}"):
            log_command([tool_name, description], exit_code=-2, readonly=readonly, note="not approved")
            return ToolResult(ok=False, error="Action needs approval; none given.")
    return None


def guarded_execute(
    tool_name: str,
    argv: list[str],
    description: str,
    readonly: bool = False,
    cwd: Optional[str] = None,
    timeout: int = 60,
    force_confirm: bool = False,
    allow_nonzero: bool = False,
    detached: bool = False,
    detach_after: Optional[float] = None,
) -> ToolResult:
    """The single choke point for every subprocess action in this server.

    allow_nonzero: for readonly commands whose non-zero exit still carries the answer
                   (e.g. `systemctl status` exits 3 for an inactive unit).
    detached:      start the process and return immediately with its pid (browsers).
    detach_after:  wait this long, then leave a still-running process alone (GUI apps).
    """
    blocked = _gate(tool_name, description, readonly, force_confirm)
    if blocked:
        return blocked

    try:
        if detached:
            return ToolResult(ok=True, data=f"Started (pid {executor.spawn_detached(argv, cwd=cwd)})")
        if readonly:
            output = executor.run_readonly(argv, cwd=cwd, timeout=timeout, allow_nonzero=allow_nonzero)
        else:
            output = executor.run_mutating(argv, cwd=cwd, timeout=timeout, detach_after=detach_after)
        return ToolResult(ok=True, data=output)
    except executor.CommandError as e:
        return ToolResult(ok=False, error=str(e))  # already audited by the executor
    except Exception as e:
        log_command(argv, exit_code=-1, readonly=readonly, note=f"unexpected: {e}")
        return ToolResult(ok=False, error=f"Unexpected error: {e}")

def guarded_call(
    tool_name: str,
    action: Callable[[], str],
    description: str,
    readonly: bool = False,
    force_confirm: bool = False,
    audit_label: Optional[list[str]] = None,
) -> ToolResult:
    """Same gates as guarded_execute, for actions that aren't a subprocess command —
    e.g. writing a file with Python's `open()`. `action` is a zero-arg callable that
    performs the mutation and returns a string result."""
    blocked = _gate(tool_name, description, readonly, force_confirm)
    if blocked:
        return blocked

    label = audit_label or [tool_name, description]
    try:
        output = action()
        log_command(label, exit_code=0, readonly=readonly)
        return ToolResult(ok=True, data=output)
    except Exception as e:
        log_command(label, exit_code=-1, readonly=readonly, note=str(e))
        return ToolResult(ok=False, error=str(e))
