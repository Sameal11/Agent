"""install_package routes through sudo-in-terminal (non-interactive), and an identical
failing command is refused on repeat instead of looping to the step limit."""
from types import SimpleNamespace

import pytest

import agent
import linux_mcp.adapters as adapters_mod


class Session:
    """verify_command returns all-clear; shell returns whatever `shell_out` is set to."""
    def __init__(self, shell_out="ok"):
        self.calls = []
        self.shell_out = shell_out

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        text = '{"programs": []}' if name == "verify_command" else (
            self.shell_out if name == "shell" else "ok")
        return SimpleNamespace(content=[SimpleNamespace(text=text)],
                               isError=text.startswith("Error"))


class FakeAdapter:
    name = "pacman"
    def __init__(self, known): self.known = known
    def exists(self, p): return p in self.known
    def install_command(self, p): return ["pacman", "-S", "--noconfirm", "--needed", p]


@pytest.fixture
def install_env(monkeypatch):
    ran = []
    monkeypatch.setattr(agent, "run_in_terminal",
                        lambda cmd, cwd=None, timeout=0: ran.append(cmd) or "resolving...\ndone")
    monkeypatch.setattr(adapters_mod, "detect_package_adapter", lambda: FakeAdapter({"pandoc-cli"}))
    return ran


async def test_install_runs_noninteractive_in_terminal(install_env):
    bot = agent.Agent(Session(), [])
    out = await bot.call_tool_with_confirmation("install_package", {"package": "pandoc-cli"}, lambda s: True)
    assert "done" in out
    # --noconfirm so pacman never stops at a [Y/n] prompt; run with sudo in our terminal.
    assert install_env == ["sudo pacman -S --noconfirm --needed pandoc-cli"]
    assert bot.session.calls == []        # the server's install_package is not used


async def test_install_shows_root_note_in_approval(install_env):
    bot = agent.Agent(Session(), [])
    asked = []
    await bot.call_tool_with_confirmation("install_package", {"package": "pandoc-cli"},
                                          lambda s: asked.append(s) or True)
    assert "--noconfirm" in asked[0] and "ROOT" in asked[0]


async def test_install_unknown_package_rejected_without_asking(install_env):
    bot = agent.Agent(Session(), [])
    asked = []
    out = await bot.call_tool_with_confirmation("install_package", {"package": "panduck"},
                                                lambda s: asked.append(s) or True)
    assert "no package named 'panduck'" in out and asked == [] and install_env == []


async def test_install_declined_does_not_run(install_env):
    bot = agent.Agent(Session(), [])
    out = await bot.call_tool_with_confirmation("install_package", {"package": "pandoc-cli"}, lambda s: False)
    assert out.startswith("Blocked") and install_env == []


async def test_repeated_failing_command_is_refused(monkeypatch):
    bot = agent.Agent(Session(shell_out="pandoc: command not found\n[exit code: 127]"), [])
    cmd = "pandoc DSA_Guide/topics.txt -o out.pdf"
    first = await bot.call_tool_with_confirmation("shell", {"command": cmd}, lambda s: True)
    assert "command not found" in first                    # ran once
    second = await bot.call_tool_with_confirmation("shell", {"command": cmd}, lambda s: True)
    assert "already ran and failed" in second              # blocked on repeat
    # verify_command(once) + shell(once) only; the repeat never reached the server again
    assert [n for n, _ in bot.session.calls] == ["verify_command", "shell"]


async def test_successful_command_not_blocked_on_repeat(monkeypatch):
    bot = agent.Agent(Session(shell_out="file listing"), [])
    cmd = "ls -la"
    a = await bot.call_tool_with_confirmation("shell", {"command": cmd}, lambda s: True)
    b = await bot.call_tool_with_confirmation("shell", {"command": cmd}, lambda s: True)
    assert a == b == "file listing"


def test_looks_failed():
    assert agent.looks_failed("x\n[exit code: 1]")
    assert agent.looks_failed("bash: pandoc: command not found")
    assert agent.looks_failed("Error: boom")
    assert not agent.looks_failed("all good")
    assert not agent.looks_failed("Blocked: user did not approve this action.")


async def test_successful_install_clears_command_not_found_failures(install_env):
    bot = agent.Agent(Session(), [])
    bot._failed_cmds = {"pandoc a -o b": "pandoc: command not found\n[exit code: 127]",
                        "nmap --badflag x": "unrecognized option '--badflag'\n[exit code: 1]"}
    await bot.call_tool_with_confirmation("install_package", {"package": "pandoc-cli"}, lambda s: True)
    # the missing-tool failure is cleared (can retry after install); the bad-flag one stays blocked
    assert "pandoc a -o b" not in bot._failed_cmds
    assert "nmap --badflag x" in bot._failed_cmds
