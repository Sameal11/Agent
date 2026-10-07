"""GUI/keyboard/mouse/screenshot control. NOT IMPLEMENTED — and disabled in
permissions.yaml so it is not advertised to the model. It returns immediately instead of
asking a human for approval of an action that can only fail."""
from __future__ import annotations

from linux_mcp.schemas import ToolResult


def take_screenshot(_args=None) -> ToolResult:
    return ToolResult(ok=False, error="take_screenshot is not implemented yet")


TOOL_SPEC = {"name": "take_screenshot", "description": "Capture the current screen (not implemented).",
             "args_model": None, "handler": take_screenshot, "gate": "desktop"}
