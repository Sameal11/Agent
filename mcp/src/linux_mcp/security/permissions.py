"""Thin query layer over permissions.yaml, re-exported from config.settings
so tool modules only need one import."""
from __future__ import annotations

from linux_mcp.config import settings

tool_enabled = settings.tool_enabled
requires_confirmation = settings.requires_confirmation
