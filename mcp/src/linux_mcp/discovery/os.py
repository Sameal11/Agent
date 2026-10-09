"""Detect the Linux distribution via /etc/os-release."""
from __future__ import annotations

import platform

# os-release ID / ID_LIKE values -> the family whose package manager and paths apply.
# Kali and Ubuntu are ID_LIKE=debian; Manjaro/EndeavourOS are ID_LIKE=arch.
_FAMILIES = {
    "arch": "arch", "manjaro": "arch", "endeavouros": "arch", "garuda": "arch", "artix": "arch",
    "debian": "debian", "ubuntu": "debian", "kali": "debian", "linuxmint": "debian",
    "pop": "debian", "parrot": "debian", "raspbian": "debian",
    "fedora": "fedora", "rhel": "fedora", "centos": "fedora", "rocky": "fedora", "almalinux": "fedora",
}

# Which package manager each family uses, in order of preference.
FAMILY_PACKAGE_MANAGERS = {"arch": ["pacman"], "debian": ["apt"], "fedora": ["dnf"]}


def detect_distro(path: str = "/etc/os-release") -> dict:
    info = {}
    try:
        with open(path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    info[k] = v.strip('"')
    except FileNotFoundError:
        pass
    info.setdefault("NAME", platform.system())
    return info


def os_family(info: dict | None = None) -> str | None:
    """'arch', 'debian', 'fedora', or None. ID is checked before ID_LIKE, which is a
    space-separated list (Ubuntu: "debian", Rocky: "rhel centos fedora")."""
    info = detect_distro() if info is None else info
    for ident in [info.get("ID", "")] + info.get("ID_LIKE", "").split():
        family = _FAMILIES.get(ident.lower())
        if family:
            return family
    return None
