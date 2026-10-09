"""The agent refuses to open URLs whose domain did not come from the user or a tool."""
from types import SimpleNamespace

import pytest

import agent


def test_hosts_in_and_known():
    known = agent.hosts_in("see https://www.SRMIST.edu.in/students/ and http://a.b.org/x)")
    assert known == {"srmist.edu.in", "a.b.org"}
    assert agent.host_is_known("https://sp.srmist.edu.in/portal", known)      # subdomain
    assert agent.host_is_known("https://www.srmist.edu.in/", known)
    assert not agent.host_is_known("https://srmist-portal.com/", known)        # look-alike
    assert not agent.host_is_known("https://evilsrmist.edu.in/", known)        # suffix, not subdomain
    assert not agent.host_is_known("not a url", known)


def test_bare_domains_only_when_asked():
    assert agent.hosts_in("open github.com please") == set()
    assert agent.hosts_in("open github.com please", bare_domains=True) == {"github.com"}


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(text="Started (pid 1)")], isError=False)


@pytest.fixture
def bot():
    return agent.Agent(FakeSession(), [])


async def test_invented_url_blocked_before_asking(bot):
    asked = []
    out = await bot.call_tool_with_confirmation(
        "open_url", {"url": "https://srm-portal.example/login"}, lambda s: asked.append(s) or True)
    assert out.startswith("Blocked") and "web_search" in out
    assert asked == [] and bot.session.calls == []


async def test_url_from_search_result_is_allowed(bot):
    bot.known_hosts |= agent.hosts_in('{"url": "https://sp.srmist.edu.in/portal"}')
    out = await bot.call_tool_with_confirmation(
        "open_url", {"url": "https://sp.srmist.edu.in/portal"}, lambda s: True)
    assert out == "Started (pid 1)"


async def test_user_approval_still_required(bot):
    bot.known_hosts.add("github.com")
    out = await bot.call_tool_with_confirmation("open_url", {"url": "https://github.com"}, lambda s: False)
    assert out.startswith("Blocked: user did not approve")


async def test_fetch_page_also_gated_by_known_host(bot):
    out = await bot.call_tool_with_confirmation(
        "fetch_page", {"url": "https://invented-xyz.example/page"}, lambda s: True)
    assert out.startswith("Blocked") and "web_search" in out
    bot.known_hosts.add("nmap.org")
    allowed = agent.host_is_known("https://nmap.org/book", bot.known_hosts)
    assert allowed
