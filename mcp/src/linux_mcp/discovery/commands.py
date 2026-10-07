"""Discover which commands are actually installed on this machine."""
from __future__ import annotations

import shutil

CANDIDATE_COMMANDS = ["curl", "wget", "git", "docker", "systemctl", "ss", "ip"]


def available_commands() -> list[str]:
    return [c for c in CANDIDATE_COMMANDS if shutil.which(c)]
