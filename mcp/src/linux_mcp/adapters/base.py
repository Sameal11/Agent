"""Common contract every package-manager adapter must implement.

packages.py depends only on this interface, never on a specific adapter,
so adding a new distro's package manager means writing one new adapter
file and nothing else.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from linux_mcp.validation import PACKAGE_NAME_RE

MAX_SEARCH_RESULTS = 50


@dataclass
class PackageInfo:
    name: str
    version: str
    installed: bool


class PackageAdapter(ABC):
    """One subclass per package manager (apt, pacman, dnf, ...)."""

    name: str  # e.g. "apt"

    @abstractmethod
    def is_available(self) -> bool:
        """True if this package manager exists on the current machine."""

    @abstractmethod
    def search(self, query: str) -> list[PackageInfo]:
        """At most MAX_SEARCH_RESULTS results, using a constant number of subprocesses."""

    @abstractmethod
    def list_installed(self) -> list[PackageInfo]:
        ...

    @staticmethod
    def validate_package_name(package: str) -> str:
        """Reject anything that could be parsed as an option (e.g. '-oDPkg::Pre-Invoke::=cmd')."""
        if not PACKAGE_NAME_RE.match(package):
            raise ValueError(f"Invalid package name: {package!r}")
        return package

    @abstractmethod
    def install_command(self, package: str) -> list[str]:
        """Return the argv for installing `package`. Caller executes it
        through utils.executor so every adapter goes through the same
        sandboxing/logging/confirmation path — adapters never call
        subprocess directly. Implementations must call
        validate_package_name() first (raises ValueError)."""

    @abstractmethod
    def is_installed(self, package: str) -> bool:
        ...
