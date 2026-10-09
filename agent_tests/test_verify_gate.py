"""The agent verifies a shell command against the installed tools before running it."""
import json
from types import SimpleNamespace

import pytest

import agent


class Session:
    """Records tool calls; verify_command returns whatever report the test queues."""
    def __init__(self, report):
        self.calls = []
        self.report = report

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        text = json.dumps(self.report) if name == "verify_command" else "command output"
        return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)


CLEAN = {"programs": [{"program": "nmap", "status": "installed",
                       "flags": {"documented": ["-sn"], "undocumented": []}}]}
BAD_FLAG = {"programs": [{"program": "nmap", "status": "installed",
                          "flags": {"documented": [], "undocumented": ["-sP"]},
                          "flag_note": "-sP not found in this version's --help; confirm with tool_docs"}]}
MISSING = {"programs": [{"program": "gobuster", "status": "missing",
                         "install_hint": "run `search_packages gobuster` to find the package"}]}


async def test_clean_command_runs(monkeypatch):
    monkeypatch.setattr(agent, "PROVIDER", "openai")
    bot = agent.Agent(Session(CLEAN), [])
    out = await bot.call_tool_with_confirmation("shell", {"command": "nmap -sn 192.168.0.0/24"}, lambda s: True)
    assert out == "command output"
    assert [n for n, _ in bot.session.calls] == ["verify_command", "shell"]


async def test_undocumented_flag_returns_findings_without_running(monkeypatch):
    bot = agent.Agent(Session(BAD_FLAG), [])
    asked = []
    out = await bot.call_tool_with_confirmation("shell", {"command": "nmap -sP 192.168.0.1"},
                                                lambda s: asked.append(s) or True)
    assert "Did not run yet" in out and "-sP not in this version" in out and "tool_docs" in out
    assert asked == []                                   # never asked to approve
    assert [n for n, _ in bot.session.calls] == ["verify_command"]   # shell not called


async def test_missing_program_returns_findings(monkeypatch):
    bot = agent.Agent(Session(MISSING), [])
    out = await bot.call_tool_with_confirmation("shell", {"command": "gobuster dir -u http://x"}, lambda s: True)
    assert "gobuster is NOT installed" in out and "search_packages gobuster" in out
    assert "shell" not in [n for n, _ in bot.session.calls]


async def test_resubmitting_same_command_runs_it(monkeypatch):
    """A flag the tool actually accepts (old alias) must not be blocked forever."""
    bot = agent.Agent(Session(BAD_FLAG), [])
    first = await bot.call_tool_with_confirmation("shell", {"command": "nmap -sP x"}, lambda s: True)
    assert "Did not run yet" in first
    second = await bot.call_tool_with_confirmation("shell", {"command": "nmap -sP x"}, lambda s: True)
    assert second == "command output"
    # Verified once; the re-submit short-circuits and runs without re-checking.
    assert [n for n, _ in bot.session.calls] == ["verify_command", "shell"]


async def test_verifier_unavailable_does_not_block(monkeypatch):
    class Broken(Session):
        async def call_tool(self, name, arguments):
            self.calls.append((name, dict(arguments)))
            text = "not json at all" if name == "verify_command" else "command output"
            return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)
    bot = agent.Agent(Broken(None), [])
    out = await bot.call_tool_with_confirmation("shell", {"command": "nmap -sn x"}, lambda s: True)
    assert out == "command output"


LONG_FLAG = {"programs": [{"program": "pacman", "status": "installed",
                           "flags": {"documented": ["-S"], "undocumented": ["--frobnicate"]},
                           "flag_note": "--frobnicate not found"}]}


async def test_undocumented_long_flag_does_not_block(monkeypatch):
    """A long --flag is often real but missing from summary help; surface, don't block."""
    bot = agent.Agent(Session(LONG_FLAG), [])
    out = await bot.call_tool_with_confirmation("shell", {"command": "pacman -S --frobnicate x"},
                                                lambda s: True)
    assert out == "command output"   # ran after approval, not held
    assert [n for n, _ in bot.session.calls] == ["verify_command", "shell"]
