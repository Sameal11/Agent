"""Package search/install. search_packages is readonly and goes straight
to the adapter; install_package goes through the shared pipeline so it's
gated and audited the same way shell commands are.

Note: installing needs root. Run the server as a user that can do that, or
the command will fail with a permission error that is reported back to the model."""
from __future__ import annotations

from dataclasses import asdict

from linux_mcp.adapters import detect_package_adapter
from linux_mcp.schemas import PackageInstallArgs, PackageQueryArgs, ToolResult
from linux_mcp.security.pipeline import guarded_execute

_adapter = detect_package_adapter()


def search_packages(args: PackageQueryArgs) -> ToolResult:
    if _adapter is None:
        return ToolResult(ok=False, error="No supported package manager found")
    try:
        return ToolResult(ok=True, data=[asdict(p) for p in _adapter.search(args.query)])
    except Exception as e:  # CommandError, timeouts, ... -> a result, not a crash
        return ToolResult(ok=False, error=f"Package search failed: {e}")


def install_package(args: PackageInstallArgs) -> ToolResult:
    if _adapter is None:
        return ToolResult(ok=False, error="No supported package manager found")
    try:  # the schema already checks the name; the adapter re-checks (defence in depth)
        argv = _adapter.install_command(args.package)
    except ValueError as e:
        return ToolResult(ok=False, error=str(e))
    return guarded_execute(
        tool_name="packages",
        argv=argv,
        description=f"install package {args.package} via {_adapter.name}",
        readonly=False,
        timeout=300,
    )


SEARCH_TOOL_SPEC = {"name": "search_packages", "description": "Search for a package (max 50 results).",
                     "args_model": PackageQueryArgs, "handler": search_packages}
INSTALL_TOOL_SPEC = {"name": "install_package", "description": "Install a package by exact name.",
                      "args_model": PackageInstallArgs, "handler": install_package, "gate": "packages"}
