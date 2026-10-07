"""Fallback adapter selection: picks the first available PackageAdapter
for the current machine. Used by tools/packages.py so it never needs
to know which distro it's running on."""
from __future__ import annotations

from linux_mcp.adapters.apt import AptAdapter
from linux_mcp.adapters.base import PackageAdapter
from linux_mcp.adapters.dnf import DnfAdapter
from linux_mcp.adapters.pacman import PacmanAdapter

_CANDIDATES: list[type[PackageAdapter]] = [AptAdapter, PacmanAdapter, DnfAdapter]


def detect_package_adapter() -> PackageAdapter | None:
    for cls in _CANDIDATES:
        instance = cls()
        if instance.is_available():
            return instance
    return None
