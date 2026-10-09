"""Create/locate a Python virtualenv INSIDE the workspace, so `pip install` never touches
the host's global Python (which on Arch is externally managed and breaks easily).

The model calls ensure_python_env, then uses the returned pip/python paths:
    <workspace>/.venv/bin/pip install <pkg>
    <workspace>/.venv/bin/python <script>.py
Those run via the shell tool (which still asks for approval), but land in the workspace venv.
"""
from __future__ import annotations

from linux_mcp.schemas import ToolResult
from linux_mcp.security.pipeline import guarded_execute
from linux_mcp.utils import workspace

VENV_DIRNAME = ".venv"


def ensure_python_env(_args=None) -> ToolResult:
    root = workspace.workspace_root()
    venv = root / VENV_DIRNAME
    python = venv / "bin" / "python"
    pip = venv / "bin" / "pip"
    usage = (f"Install deps with '{pip} install <pkg>' and run code with '{python} <script>.py'. "
             "Do NOT pip-install globally or with sudo.")

    if python.exists():
        return ToolResult(ok=True, data={"status": "exists", "python": str(python),
                                         "pip": str(pip), "note": usage})
    # python3 -m venv only creates a directory tree; no network, nothing global.
    result = guarded_execute(
        tool_name="ensure_python_env",
        argv=["python3", "-m", "venv", str(venv)],
        description=f"create a Python virtualenv at {venv}",
        readonly=False, cwd=str(root), timeout=120,
    )
    if not result.ok:
        return result
    return ToolResult(ok=True, data={"status": "created", "python": str(python),
                                     "pip": str(pip), "note": usage})


TOOL_SPEC = {
    "name": "ensure_python_env",
    "description": "Create (or locate) a Python virtualenv in the workspace and return its python/pip "
                   "paths. Use it before installing Python packages so they go in the workspace, not the "
                   "host's global Python. For OS packages use install_package instead.",
    "args_model": None,
    "handler": ensure_python_env,
}
