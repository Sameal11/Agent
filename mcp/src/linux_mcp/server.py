"""MCP server entry point.

Wires together:
  - discovery.capability_report()   -> logged at startup
  - security.confirmation           -> how a human approves an action (see _default_confirm)
  - tools.ALL_TOOL_SPECS            -> registered as MCP tools, gated by permissions.yaml

IMPORTANT: with the stdio transport, this process's stdout *is* the protocol channel.
Nothing else may ever be written to it. Logs and prompts go to stderr or /dev/tty.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from linux_mcp.config import settings
from linux_mcp.discovery import capability_report
from linux_mcp.security import confirmation, permissions
from linux_mcp.tools import ALL_TOOL_SPECS
from linux_mcp.utils.logging import get_logger

# Set per-request from the client's _meta; read by _default_confirm.
_client_approved_cmd: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "client_approved_cmd", default=None
)
log = get_logger(__name__)

app = Server("linux-mcp")


def _ask_on_tty(command: str) -> bool:
    """Prompt on the controlling terminal (never on stdout — that is the MCP channel)."""
    shown = confirmation.sanitize_for_display(command)
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write(f"\n[MCP Security] Approval required: {shown}\nAllow this action? [y/N]: ")
            tty.flush()
            answer = tty.readline().strip().lower()
    except OSError as e:
        log.warning("No interactive terminal available; denying: %s (%s)", shown, e)
        return False
    if answer in ("y", "yes"):
        return True
    log.warning("User denied command: %s", shown)
    return False


def _default_confirm(command: str) -> bool:
    # 0. The client (agent) already asked the human and told us to trust it.
    if os.environ.get("MCP_TRUST_CLIENT_APPROVAL") == "1":
        return True
    # 1. Client approved this exact action via request _meta.
    pre = _client_approved_cmd.get()
    if pre is not None:
        bare = re.sub(r"^\[\w+\]\s+", "", command.strip())
        if pre.strip() in (command.strip(), bare):
            return True
    # 2. Ask on the terminal; fail closed if there is none.
    return _ask_on_tty(command)


confirmation.set_confirmation_handler(_default_confirm)


def _enabled(spec: dict) -> bool:
    """A tool is usable only if its own switch AND its internal gate (if any) are on."""
    gate = spec.get("gate")
    return permissions.tool_enabled(spec["name"]) and (gate is None or permissions.tool_enabled(gate))


def _render(data) -> str:
    if data is None:
        text = ""
    elif isinstance(data, str):
        text = data
    else:  # structured results as JSON (not Python repr) so the model can read them
        text = json.dumps(data, default=str, ensure_ascii=False)
    limit = settings.max_output_chars
    return text if len(text) <= limit else text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    tools = []
    for spec in ALL_TOOL_SPECS:
        if not _enabled(spec):
            continue
        schema = spec["args_model"].model_json_schema() if spec["args_model"] else {"type": "object", "properties": {}}
        tools.append(types.Tool(name=spec["name"], description=spec["description"], inputSchema=schema))
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    spec = next((s for s in ALL_TOOL_SPECS if s["name"] == name), None)
    if spec is None:
        raise ValueError(f"Unknown tool: {name}")
    # Hiding a tool from list_tools is not enough: a client can call any name it knows.
    if not _enabled(spec):
        raise PermissionError(f"Error: tool '{name}' is disabled in permissions.yaml")

    # Approval comes from request _meta (set by the agent), never from arguments.
    meta = app.request_context.meta
    approved = getattr(meta, "approved_command", None) if meta else None
    token = _client_approved_cmd.set(approved)
    try:
        args = spec["args_model"](**(arguments or {})) if spec["args_model"] else None
        # Handlers block (subprocess, tty prompt); keep the event loop responsive.
        # to_thread copies the context, so _client_approved_cmd is visible inside.
        if args is not None:
            result = await asyncio.to_thread(spec["handler"], args)
        else:
            result = await asyncio.to_thread(spec["handler"])
    finally:
        _client_approved_cmd.reset(token)

    if not result.ok:
        # Raising makes the MCP layer return isError=True, which clients can detect.
        raise RuntimeError(f"Error: {result.error}")
    return [types.TextContent(type="text", text=_render(result.data))]


async def run_stdio():
    from mcp.server.stdio import stdio_server

    enabled = [s["name"] for s in ALL_TOOL_SPECS if _enabled(s)]
    log.info("Capabilities detected: %s", capability_report())
    log.info("Tools enabled (%d): %s", len(enabled), ", ".join(enabled))
    if not enabled:
        log.warning("No tools are enabled — check %s", settings.permissions_path)
    async with stdio_server() as (read, write):
        await app.run(read, write, InitializationOptions(
            server_name="linux-mcp",
            server_version="0.1.0",
            capabilities=app.get_capabilities(notification_options=NotificationOptions(), experimental_capabilities={}),
        ))
