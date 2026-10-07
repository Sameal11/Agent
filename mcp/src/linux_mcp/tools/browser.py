"""Open a URL in the user's default browser via xdg-open.

HIGH RISK category, so force_confirm=True: it always asks, regardless of anything else.
Only http(s) URLs are accepted (enforced by the schema and re-checked here) — xdg-open
would otherwise hand file:// or custom-scheme URLs to arbitrary desktop handlers.
"""
from __future__ import annotations

import re
import shutil

from linux_mcp.schemas import OpenUrlArgs, ToolResult
from linux_mcp.security.pipeline import guarded_execute
from linux_mcp.validation import HTTP_URL_PATTERN

_URL_RE = re.compile(HTTP_URL_PATTERN)


def open_url(args: OpenUrlArgs) -> ToolResult:
    if not _URL_RE.match(args.url):
        return ToolResult(ok=False, error="Only http(s) URLs are allowed")
    if shutil.which("xdg-open") is None:
        return ToolResult(ok=False, error="xdg-open is not installed; cannot open a browser here")
    return guarded_execute(
        tool_name="browser",
        argv=["xdg-open", args.url],
        description=f"open URL in browser: {args.url}",
        readonly=False,
        force_confirm=True,
        detached=True,
    )


TOOL_SPEC = {"name": "open_url", "description": "Open an http(s) URL in the default web browser.",
             "args_model": OpenUrlArgs, "handler": open_url, "gate": "browser"}
