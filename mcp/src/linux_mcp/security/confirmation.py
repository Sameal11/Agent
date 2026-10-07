"""Pluggable human-confirmation hook.

The MCP protocol itself has no built-in "ask the user" primitive that
works synchronously mid-tool-call the way a CLI input() does, so this
module defines the interface tools call against, and server.py wires it
to whatever the actual client supports.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional

ConfirmFn = Callable[[str], bool]

_confirm_fn: Optional[ConfirmFn] = None


def sanitize_for_display(text: str) -> str:
    """Make untrusted text safe to show to a human deciding whether to approve it.
    Control characters (notably ESC, which starts terminal escape sequences that can
    hide or rewrite text) are shown as visible \\xNN escapes instead of being emitted."""
    return "".join(ch if ch.isprintable() or ch == " " else f"\\x{ord(ch):02x}" for ch in str(text))


def _approved_commands() -> set[str]:
    path = os.getenv("MCP_APPROVAL_PATH")
    if not path:
        return set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item).strip() for item in data if str(item).strip()}


def _agent_form(command: str) -> str:
    """'[shell] ls -la' -> 'shell(ls -la)', the format the agent stores approvals in."""
    m = re.match(r"^\[(\w+)\]\s+(.*)$", command, re.S)
    return f"{m.group(1)}({m.group(2)})" if m else command


def set_confirmation_handler(fn: Optional[ConfirmFn]) -> None:
    global _confirm_fn
    _confirm_fn = fn


def get_confirmation_handler() -> Optional[ConfirmFn]:
    return _confirm_fn


def confirm(command: str) -> bool:
    command_text = str(command).strip()
    approved = _approved_commands()
    # EXACT match only. This used to be `any(item in command_text ...)`, so a stored
    # approval of "ls" would also have approved "rm -rf ~ # ls".
    if approved and (command_text in approved or _agent_form(command_text) in approved):
        return True

    if _confirm_fn is None:
        return False  # fail closed

    try:
        return bool(_confirm_fn(command))
    except Exception:
        return False
