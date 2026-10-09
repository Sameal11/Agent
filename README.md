# Claw — a local, self-hosted system agent for Linux

Claw is a "Jarvis-like" AI agent that runs **on your own machine** and operates it through
tools: the shell, files, processes, services, packages, the network, the browser, and more.
It is built for Arch, Ubuntu, and Kali, detects the host OS and adapts (pacman vs apt vs
dnf, distro-specific paths), and can discover and install **skills** from
[ClawHub](https://clawhub.ai) at runtime.

It is designed to work with a **small local model** (e.g. Qwen2.5-7B via vLLM/Ollama) with no
GPU-grade API bill, and optionally with the **Claude API**. Because small models misremember
command syntax and invent facts, Claw is built around a **Research-First** principle: it
verifies commands, tool syntax, package names, and URLs against the real machine *before*
acting, instead of trusting the model's memory.

> ⚠️ **This agent executes real commands on your computer.** Every state-changing action asks
> for your approval, and a hard policy blocklist refuses catastrophic commands outright — but
> run it only on a machine where you accept that risk, ideally a VM for untrusted tasks.
> Intended for authorized use on your own systems, security learning, and CTF-style practice.

---

## Why Research-First?

A 7B model will happily run `nmap -sP` (an old flag this version renamed to `-sn`), install
`python3-pip` on Arch (where it's `python-pip`), or open a made-up URL. Claw closes that gap:

| The model wants to… | Claw does first | Result |
|---|---|---|
| run a shell command | `verify_command` — checks every program exists and every flag is in the installed `--help` | holds and reports the fix if a program is missing or a flag is unknown |
| install a package | checks the package exists in the repos for *this* distro | a guessed name is rejected before you're asked |
| open a URL | requires the domain to come from you or a tool result (e.g. `web_search`) | invented URLs are refused |
| use an unknown tool | `find_tool` ranks the *installed* tools for the task | the model picks a real tool instead of guessing |

Everything that changes the system still routes through a single approval + audit pipeline.

---

## Features

- **OS-aware.** Detects distro family from `/etc/os-release` and selects the right package
  manager (pacman / apt / dnf) and paths. Works on Arch, Ubuntu, Kali, and relatives.
- **Research-first guardrails.** `environment_info`, `verify_command`, `tool_docs`, and
  `find_tool` ground the model in what's actually installed. Shell commands are
  auto-verified before they run.
- **ClawHub skills at runtime.** Search, inspect, install, and use skills from
  [clawhub.ai](https://clawhub.ai) (OpenClaw's registry). Installs are gated by ClawHub's
  own security verdict **plus** local bundle and static-content checks, and always ask for
  approval. Installed skills use OpenClaw's on-disk format.
- **Two LLM backends.** Any OpenAI-compatible server (vLLM, Ollama, LM Studio — the default)
  or the **Claude API** (`LLM_PROVIDER=anthropic`), selected by one env var.
- **Safety by construction.** One execution pipeline enforces: tool enabled? → command ever
  allowed (hard blocklist)? → human approved this once? → run + audit. `sudo` prompts for
  your password in your own terminal; the password never touches the model, history, or logs.
- **Answers, not link dumps.** Built-in `get_weather` (Open-Meteo) and a resilient
  `web_search` (DuckDuckGo-lite → Bing fallback) so the agent reports facts instead of
  pasting URLs.
- **A readable CLI.** Markdown-rendered replies, panelled approval prompts, compact tool
  activity.

---

## Install

Requires **Python 3.10+** (developed on 3.14) and a Linux host.

```bash
git clone <your-fork-url> Agent && cd Agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -e mcp            # installs the linux-mcp server package
```

Configure the model backend (see **Backends** below), then run:

```bash
python agent.py
```

---

## Backends

Claw talks to the model over one of two backends, chosen by `LLM_PROVIDER`.

### Local / OpenAI-compatible (default)

Point it at any server that speaks the OpenAI chat API — vLLM, Ollama, LM Studio, etc.
Put these in `mcp/.env` (see `mcp/.env.example`) or export them:

```bash
export LLM_PROVIDER=openai                       # the default
export LLM_URL="http://localhost:8000/v1"
export LLM_MODEL="Qwen/Qwen2.5-7B-Instruct-AWQ"
export LLM_KEY="your-server-key-or-x"
```

### Claude API (optional)

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY="sk-ant-..."            # or run `ant auth login`
export ANTHROPIC_MODEL="claude-opus-5-5"         # default
```

The Claude backend keeps history append-only (for preserved thinking), freezes the system
prompt per session, and enables server-side refusal fallback.

---

## Usage

```
$ python agent.py
╭────────────────────────────────────────────╮
│ Claw  a local system agent                   │
│   model  Qwen/Qwen2.5-7B @ http://…/v1       │
│   server python -m linux_mcp                 │
│   type exit to leave                         │
╰────────────────────────────────────────────╯
  24 tools ready

you> find live hosts on my network
  ▸ verify_command  {"command":"nmap -sn 192.168.0.0/24"}
  ▸ shell           nmap -sn 192.168.0.0/24
╭─ agent ──────────────────────────────────────╮
│ 3 hosts are up: 192.168.0.1 (router), …       │
╰──────────────────────────────────────────────╯
```

A `shell` command that uses a wrong flag is caught before it runs:

```
you> scan 192.168.0.1 for its OS
  ▸ verify_command  {"command":"nmap -sP 192.168.0.1"}
    Did not run yet: nmap -sP not in this version's --help; find the right option with tool_docs.
  ▸ tool_docs       {"program":"nmap","query":"ping scan"}  →  -sn: Ping Scan - disable port scan
  (needs root) ⚠ approve: shell: sudo nmap -O 192.168.0.1   [sudo will ask for your password]
```

---

## Tools

The MCP server exposes 26 enabled tools (one, `take_screenshot`, is stubbed and disabled).

**Research-first**
`environment_info` · `verify_command` · `tool_docs` · `find_tool`

**Workspace**
`read_file` · `write_file` · `ensure_python_env` (workspace venv)

**System & files**
`system_info` · `read_file` · `write_file` · `shell` · `list_processes` · `kill_process`
· `control_service` · `network_info` · `list_applications`

**Packages**
`search_packages` · `install_package` (distro-aware via pacman/apt/dnf adapters)

**Web & info**
`web_search` · `fetch_page` (read a page as text) · `open_url` · `get_weather`

**ClawHub skills**
`clawhub_search` · `clawhub_inspect` · `clawhub_install` · `list_skills` · `use_skill`
· `remove_skill`

---

## Safety model (one sentence)

**Every mutating action passes through `security/pipeline.py`, and nowhere else implements
these gates:** is the tool enabled (`permissions.yaml`) → is this command ever allowed (hard
blocklist, no bypass) → did a human approve this one time → run it and log it.

- **Hard blocks** (never run, even via `sudo`/`bash -c`): `rm -rf /` and other critical-path
  recursive deletes, `mkfs`, `dd of=/dev/sdX`, fork bombs, `--no-preserve-root`, etc.
- **`sudo`** runs in the agent's own terminal so you type the password into sudo directly;
  `sudo -S`/`-A`/`SUDO_ASKPASS` (password-outside-the-prompt) are refused.
- **ClawHub skills** are checked (registry verdict + bundle safety + local static scan + OS
  match) and shown to you before install; skill text is treated as untrusted data.
- **Workspace isolation**: `read_file`/`write_file` and the shell's working directory are
  confined to a dedicated `~/agent_workspace` (realpath+commonpath jail — no `../` or symlink
  escape); Python deps install into a workspace venv. OS packages still install globally via
  `install_package`, and system inspection (reading /etc, `systemctl status`, `nmap`) is allowed.
- **Secrets** (`LLM_*`, anything that looks like a key/token) are scrubbed from the
  environment of every command the model runs.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

---

## Configuration

- `mcp/config/permissions.yaml` — which tools are enabled, and which require confirmation.
- `mcp/config/default.yaml` — workspace path, output caps, timeouts, and ClawHub options
  (`workdir`, `allow_unverified`).
- Environment variables are documented in [ARCHITECTURE.md](ARCHITECTURE.md#8-environment-variables).

---

## Testing

```bash
pytest            # from the project root: runs agent_tests/ + mcp/tests/
```

151 tests covering the security pipeline, OS/package detection, research-first verification,
ClawHub install gating, both LLM backends, and the agent's approval/verify logic. Network-
dependent code (web search, weather, ClawHub) is faked so the suite runs offline.

---

## Limitations

- A small local model can still **hallucinate facts** (specs, model numbers) when writing
  prose — research-first checks commands and URLs, not the truth of generated text. Use a
  stronger model, or verify important content yourself.
- `find_tool` ranks **installed** packages; its quality depends on the package descriptions
  present on the box (richest on Kali). For tools you don't have, use `search_packages`.
- The web scrapers (`web_search`) depend on page layouts and may need updating if those
  change; the tests ship saved page samples to make breakage obvious.
- Not a sandbox: the shell is not jailed. Protection comes from the policy blocklist and
  your approval, not from confinement.

---

## Credits

Inspired by OpenDevin and Computer-Use agents. Integrates with
[ClawHub](https://clawhub.ai) (OpenClaw's skill registry). This is an independent,
educational project and is not affiliated with those projects.
