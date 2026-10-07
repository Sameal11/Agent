"""Detect the Linux distribution via /etc/os-release."""
from __future__ import annotations

import platform


def detect_distro() -> dict:
    info = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    info[k] = v.strip('"')
    except FileNotFoundError:
        pass
    info.setdefault("NAME", platform.system())
    return info
