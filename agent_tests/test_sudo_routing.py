"""sudo commands run in the agent's terminal (where sudo can prompt), never via the server."""
from types import SimpleNamespace

import pytest

import agent


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        # Shell commands are auto-verified first; return an all-clear report so the command proceeds.
        text = '{"programs": []}' if name == "verify_command" else "server ran it"
        return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)


@pytest.fixture
def bot(monkeypatch):
    ran = []
    monkeypatch.setattr(agent, "run_in_terminal", lambda cmd, cwd=None, timeout=0: ran.append(cmd) or "root output")
    b = agent.Agent(FakeSession(), [])
    b.ran = ran
    return b


async def test_sudo_runs_in_terminal_after_approval(bot):
    asked = []
    out = await bot.call_tool_with_confirmation(
        "shell", {"command": "sudo nmap -O 192.168.0.1"}, lambda s: asked.append(s) or True)
    assert out == "root output" and bot.ran == ["sudo nmap -O 192.168.0.1"]
    # The command runs in the terminal, not via the server; only the read-only verify call may hit it.
    assert [n for n, _ in bot.session.calls] == ["verify_command"]
    assert "ROOT" in asked[0] and "password" in asked[0]


async def test_denied_sudo_never_runs(bot):
    out = await bot.call_tool_with_confirmation("shell", {"command": "sudo ls /root"}, lambda s: False)
    assert out.startswith("Blocked: user did not approve") and bot.ran == []


@pytest.mark.parametrize("command", [
    "echo hunter2 | sudo -S ls",
    "sudo -u root -S ls",
    "SUDO_ASKPASS=/tmp/x sudo -A ls",
    "sudo rm -rf /",
])
async def test_refused_before_asking(bot, command):
    asked = []
    out = await bot.call_tool_with_confirmation("shell", {"command": command}, lambda s: asked.append(s) or True)
    assert out.startswith("Blocked") and asked == [] and bot.ran == []


async def test_plain_shell_still_goes_to_server(bot):
    out = await bot.call_tool_with_confirmation("shell", {"command": "nmap -sn 192.168.0.0/24"}, lambda s: True)
    assert out == "server ran it" and bot.ran == []


def test_run_in_terminal_captures_output_and_exit_code():
    out = agent.run_in_terminal("echo hello; echo oops >&2; exit 3", timeout=10)
    assert "hello" in out and "oops" in out and out.endswith("[exit code: 3]")


def test_run_in_terminal_timeout():
    assert agent.run_in_terminal("sleep 30", timeout=1).startswith("Error: timed out")
