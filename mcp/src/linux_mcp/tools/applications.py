"""Installed GUI application discovery (.desktop files)."""
from __future__ import annotations

import configparser
import glob
import os
import re
import shlex

from linux_mcp.schemas import ToolResult

DESKTOP_FILE_DIRS = ["/usr/share/applications", "~/.local/share/applications"]


def _iter_entries():
    """Yield the [Desktop Entry] section of every installed .desktop file."""
    for d in DESKTOP_FILE_DIRS:
        # glob does not expand "~": the per-user directory was never scanned.
        for path in sorted(glob.glob(os.path.join(os.path.expanduser(d), "*.desktop"))):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(path, encoding="utf-8")
                yield cp["Desktop Entry"]
            except (configparser.Error, KeyError, OSError, UnicodeError):
                continue


# Launchers that appear as the Exec= program of many unrelated entries.
_GENERIC_LAUNCHERS = {"env", "sh", "bash", "flatpak", "snap", "gtk-launch", "xdg-open", "python", "python3"}


def gui_executables() -> set[str]:
    """Program names of installed graphical applications (Terminal=false, shown in menus)."""
    names = set()
    for entry in _iter_entries():
        if entry.get("Terminal", "false").lower() == "true" or entry.get("NoDisplay", "false").lower() == "true":
            continue
        try:
            parts = shlex.split(entry.get("Exec", ""))
        except ValueError:
            continue
        while parts and (parts[0] == "env" or re.match(r"^\w+=", parts[0])):
            parts.pop(0)
        if parts and os.path.basename(parts[0]) not in _GENERIC_LAUNCHERS:
            names.add(os.path.basename(parts[0]))
    return names


def list_applications(_args=None) -> ToolResult:
    apps = [{"name": e.get("Name", ""), "exec": e.get("Exec", "")}
            for e in _iter_entries() if e.get("NoDisplay", "false").lower() != "true"]
    return ToolResult(ok=True, data=apps)


TOOL_SPEC = {"name": "list_applications", "description": "List installed GUI applications.",
             "args_model": None, "handler": list_applications}