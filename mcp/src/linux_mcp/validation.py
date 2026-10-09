"""Shared input patterns, kept dependency-free so schemas and adapters can both import them.

Why these exist: package names and unit names are passed to ``apt-get`` / ``pacman`` /
``systemctl`` as argv elements. Without validation a value such as
``-oDPkg::Pre-Invoke::=<cmd>`` (apt) or ``--host=...`` (systemctl) would be parsed as an
*option*, which turns a harmless-looking argument into command execution.
Requiring the first character to be alphanumeric rules that out.
"""
from __future__ import annotations

import re

PACKAGE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9+._:@-]{0,127}$"
UNIT_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9:_.@-]{0,127}$"
SEARCH_QUERY_PATTERN = r"^[^\s-][^\r\n]{0,99}$"
HTTP_URL_PATTERN = r"^https?://[^\s]+$"
# A bare executable name: no path, cannot start with "-" (it is passed as an argv element).
PROGRAM_NAME_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9+._-]{0,63}$"

PACKAGE_NAME_RE = re.compile(PACKAGE_NAME_PATTERN)
UNIT_NAME_RE = re.compile(UNIT_NAME_PATTERN)
SKILL_NAME_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
