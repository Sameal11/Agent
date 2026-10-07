"""Aggregate capability report: what can this server actually do here?
server.py calls this at startup to decide which tools to register."""
from __future__ import annotations

from linux_mcp.discovery.commands import available_commands
from linux_mcp.discovery.os import detect_distro
from linux_mcp.discovery.packages import detected_package_manager


def capability_report() -> dict:
    return {
        "distro": detect_distro(),
        "package_manager": detected_package_manager(),
        "commands": available_commands(),
    }
