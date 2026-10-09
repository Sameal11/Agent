"""Claude (Anthropic API) backend for agent.py.

agent.py keeps its conversation in OpenAI chat format, because its other backend is any
OpenAI-compatible server (vLLM, Ollama, LM Studio). This module translates that history to
the Messages API and returns a reply shaped like an OpenAI message, so the agent loop does
not care which backend is running.

Claude-specific rules this follows:
  * Assistant turns are replayed with their ORIGINAL content blocks (thinking blocks
    included, unchanged), kept under the private key `_anthropic_content`; regenerating
    them from text would invalidate the thinking blocks.
  * The caller must keep the system prompt, tools and earlier messages byte-identical
    (preserved thinking: an edited prefix is a 400 on newer accounts). agent.py therefore
    freezes the system prompt per session and never trims history on this backend.
  * Refusals: `fallbacks: "default"` lets the API re-run a declined request on Anthropic's
    recommended fallback model server-side; a refusal of the whole chain is reported to
    the user instead of being treated as an answer.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import anthropic

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("ANTHROPIC_EFFORT", "high")     # Opus 5.5 defaults to "medium"; agentic work wants more
MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "16000"))
FALLBACK_BETA = "server-side-fallback-2026-07-01"   # pairs with fallbacks="default" (not the array form)

client = anthropic.Anthropic(max_retries=3)


@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    function: _Function
    type: str = "function"


@dataclass
class Reply:
    """Duck-types the OpenAI ChatCompletionMessage fields agent.py uses."""
    content: str | None
    tool_calls: list[_ToolCall] | None = None
    raw_content: list[dict] = field(default_factory=list)

    def model_dump(self, exclude_none: bool = True) -> dict:
        msg = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                 for c in self.tool_calls]
        if self.raw_content:
            msg["_anthropic_content"] = self.raw_content
        return msg


def convert_tools(openai_tools: list[dict]) -> list[dict]:
    return [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
             "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
            for t in openai_tools]


def convert_history(history: list[dict]) -> list[dict]:
    """OpenAI-format history -> Messages API messages. Consecutive tool results become one
    user message (splitting them teaches the model to stop calling tools in parallel)."""
    messages: list[dict] = []
    for m in history:
        role = m["role"]
        if role == "assistant":
            content = m.get("_anthropic_content")
            if content is None:   # e.g. a reply written by the other backend earlier
                content = [{"type": "text", "text": m.get("content") or "(no text)"}]
                for call in m.get("tool_calls") or []:
                    content.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"],
                                    "input": json.loads(call["function"]["arguments"] or "{}")})
            messages.append({"role": "assistant", "content": content})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"] or "(empty)"}
            if str(m["content"]).startswith(("Error", "Blocked")):
                block["is_error"] = True
            if messages and messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list) \
                    and messages[-1]["content"] and messages[-1]["content"][0].get("type") == "tool_result":
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
        else:   # user
            messages.append({"role": "user", "content": m["content"]})
    return messages


def complete(system: str, history: list[dict], tools: list[dict]) -> Reply:
    kwargs = {"tools": convert_tools(tools)} if tools else {}
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=convert_history(history),
        output_config={"effort": EFFORT},
        cache_control={"type": "ephemeral"},   # the frozen system prompt + tools + history prefix
        betas=[FALLBACK_BETA],
        fallbacks="default",
        **kwargs,
    )
    if response.stop_reason == "refusal":
        details = response.stop_details
        category = getattr(details, "category", None) if details else None
        # Discard partial content: it is not an answer, and storing it would replay it.
        return Reply(content=f"The model declined this request (safety category: {category or 'unspecified'}).")

    blocks = [b.model_dump(exclude_none=True) for b in response.content]
    if response.stop_reason == "max_tokens":
        # A tool_use cut off mid-arguments has invalid input and would have no result.
        blocks = [b for b in blocks if b.get("type") != "tool_use"]
        blocks.append({"type": "text", "text": "\n[reply cut off: output limit reached]"})
    text = "\n".join(b["text"] for b in blocks if b.get("type") == "text").strip() or None
    calls = [_ToolCall(b["id"], _Function(b["name"], json.dumps(b.get("input") or {})))
             for b in blocks if b.get("type") == "tool_use"]
    return Reply(content=text, tool_calls=calls or None, raw_content=blocks)


def check_connection() -> None:
    """Fail fast with a clear message (bad key, unknown model) before the first prompt."""
    client.models.retrieve(MODEL)
