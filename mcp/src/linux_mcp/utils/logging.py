"""General app logging (startup, tool registration, adapter detection).
Separate from security.audit, which is specifically the command audit
trail. This is for developer/operator visibility, not compliance."""
from __future__ import annotations

import logging
import sys

from linux_mcp.config import settings


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(settings.log_level)
    return logger
