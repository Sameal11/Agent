"""A tool call written as text is sent back to the model once instead of shown as the answer."""
import json
from types import SimpleNamespace

import pytest

import agent

TOOLS = ["web_search", "get_weather", "shell"]


@pytest.mark.parametrize("text, expected", [
    ('To check, let\'s search.\n\n```shell\nweb_search "weather in Chennai"\n```', "web_search"),
    ('get_weather(location="Chennai")', "get_weather"),
    ('<tool_call>{"name": "get_weather", "arguments": {"location": "Chennai"}}</tool_call>', "get_weather"),
    ("It is 34°C and clear in Chennai (source: open-meteo).", None),
    ("I used web_search and found the official site.", None),   # prose mention
])
def test_detection(text, expected):
    assert agent.unexecuted_tool_call(text, TOOLS) == expected


def _msg(content=None, tool_calls=None):
    m = SimpleNamespace(content=content, tool_calls=tool_calls)
    m.model_dump = lambda exclude_none=True: {k: v for k, v in
                                              {"role": "assistant", "content": content}.items() if v is not None}
    return m


async def test_run_nudges_once_then_uses_tool(monkeypatch):
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="get_weather",
                                                             arguments=json.dumps({"location": "Chennai"})))
    replies = iter([_msg('```shell\nget_weather "Chennai"\n```'),
                    _msg(tool_calls=[call]),
                    _msg("Chennai: clear sky, 34°C (feels 38.6°C).")])
    monkeypatch.setattr(agent.llm.chat.completions, "create",
                        lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=next(replies))]))

    class Session:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(content=[SimpleNamespace(text='{"temperature": "34.0°C"}')], isError=False)

    bot = agent.Agent(Session(), [{"function": {"name": n}} for n in TOOLS])
    monkeypatch.setattr(bot, "_show", lambda *a: None)
    reply = await bot.run("tell chennai weather", ask=lambda s: True)
    assert reply == "Chennai: clear sky, 34°C (feels 38.6°C)."
    assert sum("did not run" in str(m.get("content")) for m in bot.history) == 1
