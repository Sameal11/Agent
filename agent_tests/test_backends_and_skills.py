"""Claude backend translation, append-only history, and the skill-install review flow."""
import json
from types import SimpleNamespace

import pytest

import agent
import llm_anthropic
from anthropic.types.beta import BetaTextBlock, BetaThinkingBlock, BetaToolUseBlock


# ---------- history translation ----------
def test_convert_history_merges_parallel_tool_results_and_replays_raw_blocks():
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"},
           {"type": "tool_use", "id": "a", "name": "get_weather", "input": {"location": "Chennai"}},
           {"type": "tool_use", "id": "b", "name": "list_skills", "input": {}}]
    history = [
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": "", "tool_calls": [], "_anthropic_content": raw},
        {"role": "tool", "tool_call_id": "a", "content": '{"temperature": "34°C"}'},
        {"role": "tool", "tool_call_id": "b", "content": "Error: boom"},
        {"role": "user", "content": "thanks"},
    ]
    msgs = llm_anthropic.convert_history(history)
    assert msgs[1] == {"role": "assistant", "content": raw}          # thinking block unchanged
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["a", "b"]   # one user message
    assert msgs[2]["content"][1]["is_error"] is True
    assert msgs[3] == {"role": "user", "content": "thanks"}


def test_convert_history_from_openai_format_reply():
    history = [{"role": "user", "content": "x"},
               {"role": "assistant", "content": "checking", "tool_calls": [
                   {"id": "c1", "type": "function", "function": {"name": "shell", "arguments": '{"command": "ls"}'}}]},
               {"role": "tool", "tool_call_id": "c1", "content": "a.txt"}]
    msgs = llm_anthropic.convert_history(history)
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "shell", "input": {"command": "ls"}}


def test_convert_tools():
    t = [{"type": "function", "function": {"name": "get_weather", "description": "d",
                                           "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}}}]
    assert llm_anthropic.convert_tools(t) == [{"name": "get_weather", "description": "d",
                                               "input_schema": t[0]["function"]["parameters"]}]


# ---------- complete() against a mocked SDK ----------
def _response(blocks, stop_reason="tool_use", stop_details=None):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, stop_details=stop_details)


class Recorder(list):
    """Requests sent to the mocked SDK; .respond() sets the response to return."""
    response = None

    def respond(self, response):
        self.response = response


@pytest.fixture
def sent(monkeypatch):
    calls = Recorder()
    monkeypatch.setattr(llm_anthropic.client.beta.messages, "create",
                        lambda **kw: calls.append(kw) or calls.response)
    return calls


def test_complete_request_shape_and_tool_calls(sent):
    sent.respond(_response([
        BetaThinkingBlock(type="thinking", thinking="", signature="s1"),
        BetaTextBlock(type="text", text="Checking."),
        BetaToolUseBlock(type="tool_use", id="t1", name="get_weather", input={"location": "Chennai"})]))
    reply = llm_anthropic.complete("SYS", [{"role": "user", "content": "hi"}], [])
    kw = sent[0]
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == "SYS"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"] == {"effort": "high"} and kw["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in kw and "tool_choice" not in kw     # Opus 5.5: adaptive by default
    assert reply.content == "Checking."
    assert reply.tool_calls[0].function.name == "get_weather"
    assert json.loads(reply.tool_calls[0].function.arguments) == {"location": "Chennai"}
    stored = reply.model_dump()
    assert stored["_anthropic_content"][0] == {"type": "thinking", "thinking": "", "signature": "s1"}


def test_refusal_is_reported_and_partial_content_dropped(sent):
    sent.respond(_response([BetaTextBlock(type="text", text="partial")], "refusal",
                           SimpleNamespace(category="cyber")))
    reply = llm_anthropic.complete("S", [{"role": "user", "content": "x"}], [])
    assert "declined" in reply.content and "cyber" in reply.content
    assert reply.tool_calls is None and "_anthropic_content" not in reply.model_dump()


def test_max_tokens_drops_truncated_tool_call(sent):
    sent.respond(_response([BetaTextBlock(type="text", text="Writing"),
                            BetaToolUseBlock(type="tool_use", id="t", name="write_file", input={"path": "a"})],
                           "max_tokens"))
    reply = llm_anthropic.complete("S", [{"role": "user", "content": "x"}], [])
    assert reply.tool_calls is None
    assert all(b["type"] != "tool_use" for b in reply.raw_content) and "cut off" in reply.content


# ---------- agent: frozen prompt, append-only history ----------
class Session:
    def __init__(self, results=None):
        self.calls, self.results = [], results or {}

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        text = self.results.get(name, "ok")
        return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=text.startswith("Error"))


async def test_anthropic_history_is_append_only_and_prompt_frozen(monkeypatch):
    monkeypatch.setattr(agent, "PROVIDER", "anthropic")
    seen = []

    def fake_complete(system, history, tools):
        seen.append((system, [dict(m) for m in history]))
        return llm_anthropic.Reply(content="done", raw_content=[{"type": "text", "text": "done"}])
    monkeypatch.setattr(llm_anthropic, "complete", fake_complete)
    bot = agent.Agent(Session(), [], skills="weather: Get weather")
    for i in range(agent.Agent.MAX_TURNS_KEPT + 3):     # more turns than the local-model trim keeps
        await bot.run(f"turn {i}", ask=lambda s: True)
    systems = {s for s, _ in seen}
    assert len(systems) == 1 and "weather: Get weather" in systems.pop()
    first, last = seen[0][1], seen[-1][1]
    assert last[:len(first)] == first                     # earlier messages untouched
    assert "(local time:" in last[-1]["content"]          # time travels in the user message


async def test_openai_backend_never_sees_private_keys(monkeypatch):
    monkeypatch.setattr(agent, "PROVIDER", "openai")
    sent = []
    msg = SimpleNamespace(content="ok", tool_calls=None,
                          model_dump=lambda exclude_none=True: {"role": "assistant", "content": "ok"})
    monkeypatch.setattr(agent.llm.chat.completions, "create",
                        lambda **kw: sent.append(kw) or SimpleNamespace(choices=[SimpleNamespace(message=msg)]))
    bot = agent.Agent(Session(), [])
    bot.history.append({"role": "assistant", "content": "x", "_anthropic_content": [{"type": "text"}]})
    await bot.run("hi", ask=lambda s: True)
    assert all(not k.startswith("_") for m in sent[0]["messages"] for k in m)


# ---------- skill install review ----------
REPORT = {"ref": "steipete/weather", "version": "1.0.0", "publisher": "Peter Steinberger",
          "official_publisher": True, "clawhub_verdict": {"security": "clean"},
          "description": "Get current weather", "files": ["SKILL.md"], "installable": True,
          "blocked_because": [], "warnings": ["needs programs that are not installed: curl"]}


async def test_install_shows_report_and_pins_version():
    s = Session({"clawhub_inspect": json.dumps(REPORT), "clawhub_install": '{"installed": "steipete/weather"}'})
    bot = agent.Agent(s, [])
    asked = []
    out = await bot.call_tool_with_confirmation("clawhub_install", {"ref": "steipete/weather"},
                                                lambda t: asked.append(t) or True)
    assert "installed" in out
    assert "Peter Steinberger (official publisher)" in asked[0] and "WARNING: needs programs" in asked[0]
    assert s.calls == [("clawhub_inspect", {"ref": "steipete/weather"}),
                       ("clawhub_install", {"ref": "steipete/weather", "version": "1.0.0"})]


async def test_blocked_skill_refused_without_asking():
    report = dict(REPORT, installable=False, blocked_because=["ClawHub verification did not pass"])
    s = Session({"clawhub_inspect": json.dumps(report)})
    asked = []
    out = await agent.Agent(s, []).call_tool_with_confirmation(
        "clawhub_install", {"ref": "x/y"}, lambda t: asked.append(t) or True)
    assert out.startswith("Install refused") and asked == [] and [c[0] for c in s.calls] == ["clawhub_inspect"]


async def test_declined_install_never_reaches_server():
    s = Session({"clawhub_inspect": json.dumps(REPORT)})
    out = await agent.Agent(s, []).call_tool_with_confirmation("clawhub_install", {"ref": "steipete/weather"},
                                                               lambda t: False)
    assert out.startswith("Blocked: user did not approve") and [c[0] for c in s.calls] == ["clawhub_inspect"]


def test_skills_index():
    listed = json.dumps({"skills": [{"name": "weather", "description": "Get weather"}]})
    assert agent.skills_index(listed) == "weather: Get weather"
    assert agent.skills_index('{"skills": []}') == agent.skills_index("Error: x") == "none"
