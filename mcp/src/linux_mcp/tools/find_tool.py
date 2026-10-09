"""find_tool: 'which installed tool does X?'

A small model cannot hold the syntax or even the names of the hundreds of tools on a Kali
box. Rather than exposing every tool as its own MCP tool (which would overflow an 8k context
and wreck tool selection), the agent keeps ONE shell tool and looks up the right program
here: a local, CPU-only ranked search over the installed packages' names and descriptions
(from the package database in a single query), plus a small curated map of common
security/ops intents. No GPU, no embeddings, no network - BM25 over a few hundred short
documents is instant.

For a tool that is not installed yet, use search_packages (the repositories), then
install_package / `sudo <pm> install`.
"""
from __future__ import annotations

import math
import re
import threading

from linux_mcp.adapters import detect_package_adapter
from linux_mcp.schemas import FindToolArgs, ToolResult

_adapter = detect_package_adapter()
_TOKEN = re.compile(r"[a-z0-9.+-]+")

# Task-phrasing words that carry no tool meaning; dropped from queries so they neither match
# unrelated packages nor dilute the real terms.
_STOP = set("""a an the for of to and or with in into from on at by my your this that these it
is are be how do i you want need me using use get find list all any some program tool command
run make create generate file files data thing stuff please can could would help want""".split())

# A few high-value intents where the package description alone ranks weakly. Tokens in the
# task (left) boost the named packages (right) if they are installed. Kept short on purpose:
# the BM25 index over real descriptions does most of the work.
_INTENT_HINTS = {
    "port scan": ["nmap", "masscan", "rustscan"],
    "live host": ["nmap", "fping", "arp-scan", "netdiscover"],
    "directory brute": ["gobuster", "ffuf", "dirb", "feroxbuster", "dirsearch"],
    "directory fuzz": ["ffuf", "gobuster", "wfuzz"],
    "subdomain": ["gobuster", "sublist3r", "amass", "assetfinder"],
    "password crack": ["hashcat", "john", "hydra"],
    "brute force login": ["hydra", "medusa", "ncrack"],
    "sql injection": ["sqlmap"],
    "web vulnerability": ["nikto", "wapiti", "nuclei"],
    "packet capture": ["tcpdump", "tshark", "wireshark"],
    "packet sniff": ["tcpdump", "tshark", "bettercap"],
    "exploit framework": ["metasploit", "msfconsole"],
    "wireless wifi": ["aircrack-ng", "wifite", "kismet"],
    "reverse engineer": ["radare2", "ghidra", "gdb"],
    "markdown pdf": ["pandoc", "wkhtmltopdf", "weasyprint"],
    "document convert": ["pandoc", "libreoffice"],
    "dns lookup": ["dig", "host", "nslookup", "dnsrecon", "drill"],
    "download url": ["curl", "wget", "aria2c"],
    "compress archive": ["tar", "gzip", "zip", "7z", "zstd"],
    "extract archive": ["tar", "unzip", "7z"],
}


class _Index:
    """BM25 over short documents (package name + description)."""

    K1, B = 1.5, 0.75

    def __init__(self, docs: list[tuple[str, str]]):
        self.names = [name for name, _ in docs]
        self.descs = [desc for _, desc in docs]
        # Weight the name 3x by repeating it: a query term matching the package name should
        # outrank the same term buried in some other package's description.
        self.tokens = [_TOKEN.findall((name + " ") * 3 + desc.lower()) for name, desc in docs]
        self.avg_len = (sum(len(t) for t in self.tokens) / len(self.tokens)) if self.tokens else 0.0
        self.df: dict[str, int] = {}
        for toks in self.tokens:
            for term in set(toks):
                self.df[term] = self.df.get(term, 0) + 1
        self.n = len(self.tokens)

    def _idf(self, term: str) -> float:
        n_q = self.df.get(term, 0)
        return math.log(1 + (self.n - n_q + 0.5) / (n_q + 0.5)) if n_q else 0.0

    def _query_terms(self, query: str) -> set[str]:
        terms = {t for t in _TOKEN.findall(query.lower()) if t not in _STOP and len(t) > 1}
        # Drop terms that appear in more than a third of packages (library, support, data...):
        # they match almost everything and bury the discriminating words.
        discriminating = {t for t in terms if self.df.get(t, 0) <= 0.35 * self.n}
        return discriminating or terms   # keep common terms only if nothing else is left

    def search(self, query: str, limit: int) -> list[tuple[str, str, float]]:
        q_terms = self._query_terms(query)
        if not q_terms or not self.n:
            return []
        scored = []
        for i, toks in enumerate(self.tokens):
            if not toks:
                continue
            counts = {t: toks.count(t) for t in q_terms if t in toks}
            if not counts:
                continue
            dl = len(toks)
            score = sum(self._idf(t) * (c * (self.K1 + 1)) /
                        (c + self.K1 * (1 - self.B + self.B * dl / (self.avg_len or 1)))
                        for t, c in counts.items())
            scored.append((self.names[i], self.descs[i], score))
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:limit]


_index: _Index | None = None
_index_lock = threading.Lock()


def _get_index() -> _Index:
    global _index
    with _index_lock:
        if _index is None:
            docs = _adapter.describe_installed() if _adapter else []
            _index = _Index(docs)
        return _index


def _hint_boost(query: str, installed: set[str]) -> list[str]:
    """Curated intents, matched tolerantly: a phrase word matches a query word when one is a
    prefix of the other ('port' matches 'ports', 'scan' matches 'scanning')."""
    q_tokens = _TOKEN.findall(query.lower())

    def shared_prefix(a: str, b: str) -> int:
        i = 0
        while i < len(a) and i < len(b) and a[i] == b[i]:
            i += 1
        return i

    def word_present(pword: str) -> bool:
        # Match a hint word to a query word by a 4+ char shared prefix (so 'directory' ~
        # 'directories', 'scan' ~ 'scanning', 'port' ~ 'ports'), or an exact short word.
        return any(qt == pword or shared_prefix(pword, qt) >= 4 for qt in q_tokens if len(qt) >= 3)

    boosted = []
    for phrase, tools in _INTENT_HINTS.items():
        if all(word_present(w) for w in phrase.split()):
            boosted += [t for t in tools if t in installed]
    return boosted


def find_tool(args: FindToolArgs) -> ToolResult:
    if _adapter is None:
        return ToolResult(ok=False, error="No supported package manager; cannot build the tool index.")
    index = _get_index()
    if index.n == 0:
        return ToolResult(ok=False, error="Could not read the installed package list.")

    installed = set(index.names)
    hits = index.search(args.task, args.limit + 3)
    desc_by_name = dict(zip(index.names, index.descs))

    ordered: list[str] = []
    for name in _hint_boost(args.task, installed):   # curated intents first
        if name not in ordered:
            ordered.append(name)
    for name, _desc, _score in hits:
        if name not in ordered:
            ordered.append(name)

    results = [{"tool": n, "description": desc_by_name.get(n, ""), "installed": True}
               for n in ordered[:args.limit]]
    if not results:
        return ToolResult(ok=False, error=(
            f"No installed tool matches '{args.task}'. Call search_packages to find one in the "
            "repositories, then install it."))
    return ToolResult(ok=True, data={
        "task": args.task,
        "tools": results,
        "note": "These are installed. For a tool that is not here, use search_packages (repositories). "
                "Confirm exact syntax with tool_docs before running.",
    })


TOOL_SPEC = {
    "name": "find_tool",
    "description": "Find which INSTALLED command-line tool does a task (e.g. 'scan for open ports', "
                   "'convert markdown to pdf'), ranked by the local package database. Use it before "
                   "assuming a tool's name. For a tool that is not installed, use search_packages.",
    "args_model": FindToolArgs,
    "handler": find_tool,
}
