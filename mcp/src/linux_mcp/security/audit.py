"""Append-only audit log of every command run, readonly or mutating.

``exit_code`` is the process exit status, or -1 when the command never produced one
(timeout, missing binary, ...), -2 when it was refused before running. ``note`` says
*why*, so a -1/-2 is never an unexplained mystery.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from linux_mcp.config import settings


def log_command(argv: list[str], exit_code: int, readonly: bool, note: str | None = None) -> None:
    entry = {
        "ts": time.time(),
        "argv": argv,
        "exit_code": exit_code,
        "readonly": readonly,
    }
    if note:
        entry["note"] = note
    path: Path = settings.audit_log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
