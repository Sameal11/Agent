"""Detect which package manager this machine uses."""
from __future__ import annotations


def detected_package_manager() -> str | None:
    # Imported here: adapters.generic imports discovery.os, so a module-level import
    # would make `import linux_mcp.adapters` circular.
    from linux_mcp.adapters import detect_package_adapter

    adapter = detect_package_adapter()
    return adapter.name if adapter else None
