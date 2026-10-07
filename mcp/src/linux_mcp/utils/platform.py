"""Small OS/platform helpers shared across tools."""
from __future__ import annotations

import os


def is_root() -> bool:
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False
