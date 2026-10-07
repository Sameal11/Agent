"""Detect which package manager this machine uses."""
from __future__ import annotations

from linux_mcp.adapters import detect_package_adapter


def detected_package_manager() -> str | None:
    adapter = detect_package_adapter()
    return adapter.name if adapter else None
