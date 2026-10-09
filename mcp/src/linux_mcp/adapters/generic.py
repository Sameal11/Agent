"""Fallback adapter selection: picks the PackageAdapter for the current machine.
Used by tools/packages.py so it never needs to know which distro it's running on.

The distro family from /etc/os-release decides first. Probing binaries alone is wrong
on machines that have a foreign package manager installed (e.g. `apt` from the AUR on
Arch would have been picked over pacman, because apt came first in the list)."""
from __future__ import annotations

from linux_mcp.adapters.apt import AptAdapter
from linux_mcp.adapters.base import PackageAdapter
from linux_mcp.adapters.dnf import DnfAdapter
from linux_mcp.adapters.pacman import PacmanAdapter
from linux_mcp.discovery.os import FAMILY_PACKAGE_MANAGERS, os_family

_CANDIDATES: list[type[PackageAdapter]] = [AptAdapter, PacmanAdapter, DnfAdapter]


def detect_package_adapter(family: str | None = None) -> PackageAdapter | None:
    preferred = FAMILY_PACKAGE_MANAGERS.get(family or os_family() or "", [])
    ordered = sorted(_CANDIDATES, key=lambda cls: (cls.name not in preferred,))
    for cls in ordered:
        instance = cls()
        if instance.is_available():
            return instance
    return None
