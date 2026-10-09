"""File read/write, sandboxed to the workspace directory. read_file is a
direct readonly access (no gating needed beyond the sandbox check).
write_file goes through the same guarded_call pipeline every other
mutating tool uses, so it gets the same policy/confirmation/audit
behavior as a shell command, without needing to shell out to `tee`."""
from __future__ import annotations

import os

from linux_mcp.schemas import ReadFileArgs, ToolResult, WriteFileArgs
from linux_mcp.security.pipeline import guarded_call
from linux_mcp.utils import workspace


def _safe_path(path: str) -> str:
    """Resolve `path` inside the workspace or raise (realpath + commonpath; see
    utils/workspace.py). Shared with shell so there is one jail, not two."""
    return str(workspace.resolve_within(path))


def read_file(args: ReadFileArgs) -> ToolResult:
    try:
        with open(_safe_path(args.path), "rb") as f:
            return ToolResult(ok=True, data=f.read(args.max_bytes).decode("utf-8", errors="replace"))
    except Exception as e:
        return ToolResult(ok=False, error=str(e))


def write_file(args: WriteFileArgs) -> ToolResult:
    try:                      # reject an escaping path before anyone is asked to approve it
        _safe_path(args.path)
    except ValueError as e:
        return ToolResult(ok=False, error=str(e))

    def do_write():
        p = _safe_path(args.path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        mode = "a" if args.append else "w"
        with open(p, mode, encoding="utf-8") as f:
            f.write(args.content)
        return f"Wrote {len(args.content)} chars to {args.path}"

    # The description is what the human sees when approving, so include the size and mode.
    verb = "append" if args.append else "overwrite"
    return guarded_call(
        tool_name="filesystem",
        action=do_write,
        description=f"{verb} {args.path} ({len(args.content)} chars)",
        audit_label=["write_file", args.path],
    )


READ_TOOL_SPEC = {"name": "read_file", "description": "Read a file from the workspace.",
                   "args_model": ReadFileArgs, "handler": read_file}
WRITE_TOOL_SPEC = {"name": "write_file", "description": "Write a file in the workspace.",
                    "args_model": WriteFileArgs, "handler": write_file, "gate": "filesystem"}
