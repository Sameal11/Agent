# linux-mcp

MCP server exposing Linux system-control tools (shell, filesystem, processes,
services, packages, network, applications, browser, desktop) to an LLM agent.

## Architecture

```
src/linux_mcp/
├── server.py         MCP entry point: registers tools, wires confirmation
├── config.py          Loads config/*.yaml + env, single source of truth
├── schemas.py          Pydantic models for every tool's args (typed, validated)
│
├── tools/               One module per tool family. None of them implement
│                         permissions/policy/confirmation/audit themselves —
│                         they build a command (or a Python action) and hand
│                         it to security/pipeline.py, which is the only place
│                         that logic exists.
│
├── security/
│   ├── pipeline.py       THE single execution pipeline. guarded_execute()
│   │                     for subprocess commands, guarded_call() for
│   │                     non-subprocess mutations (e.g. writing a file).
│   │                     Every tool routes through one of these two
│   │                     functions instead of reimplementing the gates.
│   ├── policy.py         Hard-blocked command patterns + auto-approve list
│   ├── confirmation.py   Pluggable "ask the human" hook
│   ├── permissions.py    Reads config/permissions.yaml (which tools are on)
│   └── audit.py          Append-only JSONL log of every command run
│
├── adapters/            Package-manager-specific implementations, all
│   ├── base.py           conforming to adapters/base.py's PackageAdapter
│   ├── apt.py            interface. tools/packages.py never imports a
│   ├── pacman.py         specific adapter directly — it asks
│   ├── dnf.py            adapters.detect_package_adapter() for whichever
│   ├── systemd.py        one is available on this machine.
│   └── generic.py
│
├── discovery/            What does this machine support? (distro, package
│                          manager, installed commands) — checked at startup.
│
├── resources/            MCP *resources* (read-only addressable data),
│                          distinct from tools (callable actions). Empty
│                          scaffold — fill in as needed.
│
└── utils/
    ├── executor.py       The ONLY module allowed to call subprocess directly.
    ├── logging.py         General app logs (separate from security/audit.py).
    ├── parser.py
    └── platform.py
```

## Security model, in one sentence

**Every mutating action passes through `security/pipeline.py`, and nowhere
else implements these gates:** permissions (`is this tool on?`) → policy
(`is this exact command ever allowed?`) → confirmation (`did a human say
yes, this one time?`) → execute + audit (`run it, and log it`).

A tool module's job is only to build the command (or the Python action)
and describe it in plain English — `guarded_execute()` / `guarded_call()`
do the rest. This is deliberate: a new safety rule, or a fix to how
confirmation works, is a one-file change, not an N-file change across
every tool.

`open_url` (browser) always asks for approval, whatever else is configured, and only
accepts `http(s)` URLs. `take_screenshot` (desktop) is **not implemented** and is disabled
in `config/permissions.yaml` so the model is not offered a tool that can only fail.

## How approval works

* **Hard block** (`security/policy.py`): catastrophic commands (`rm -rf /`, `~`, `/etc`...,
  `mkfs`, `dd of=/dev/sdX`, fork bombs, recursive `chmod`/`chown` of system dirs) never run,
  even from `bash -c '...'` or behind `sudo`. Plain `echo rm -rf /` or `man mkfs` are fine.
* **`shell`**: read-only commands (`ls`, `pwd`, `cat`, `grep`, `date`, `hostname`, ...) run
  without asking; anything else (including `kill`/`pkill`, pipes, redirects) asks.
  `date` and `hostname` are only auto-approved in their read-only forms.
* **Every other mutating tool** asks each time (`confirm: true` in `permissions.yaml`).
* A tool runs only if its own entry **and** its internal gate entry are enabled.
* With `agent.py`, the *agent* asks you first (every tool not on its read-only list) and tells
  the server to trust that (`MCP_TRUST_CLIENT_APPROVAL=1`). Run the server on its own and it
  asks on `/dev/tty`, and refuses if there is none.

## Notes

* The **workspace** (`workspace/` in this folder, created automatically) is only the starting
  directory for `shell`, and the *sandbox* for `read_file` / `write_file`. The shell itself is
  **not** sandboxed: use the policy and the approval prompt, not the cwd, as the safety net.
* `install_package` and `control_service start/stop/...` need root. If the server runs as a
  normal user they fail with a permission error that is reported back to the model.
* Secrets: the server only imports `LINUX_MCP_*` / `MCP_*` keys from `.env`, and `agent.py`
  strips `LLM_*` and anything named like a key/token/secret from the server's environment, so
  commands the model runs cannot read your API key.
* Don't add an `__init__.py` to the folder named `mcp/`: it would shadow the `mcp` library
  that `agent.py` imports.
* Tests: run `pytest` from the project root (agent + server) or from `mcp/`.

## Research-first tools

`find_tool` answers "which installed tool does X?" without exposing every binary as its own
MCP tool (which would overflow a small model's context). It ranks the installed packages'
names and descriptions - read from the package database in one query (`tools/find_tool.py`,
a pure-Python BM25 index, no GPU or network) - plus a small curated map of common
security/ops intents (`port scan` -> nmap, `crack password hashes` -> hashcat/john, ...).
For a tool that is not installed, `search_packages` searches the repositories.


So a small model does not run commands from a misremembered syntax (it used `nmap -sP`,
which this nmap renamed to `-sn`), three read-only tools check the real machine first:

* `environment_info` - OS, distro family, package manager, desktop, browsers, installed tools.
* `verify_command` - for each program in a command: installed? which package owns it (or, if
  missing, would provide it)? is each flag in the installed version's `--help`?
* `tool_docs` - a program's own tldr/man/`--help`, filtered to a query (e.g. one option).

The agent (`agent.py`) runs `verify_command` automatically before every `shell` command. If a
program is missing or a flag is undocumented, it returns the findings instead of running and
the model fixes the command (via `tool_docs` or by installing the tool). Re-submitting the
same command runs it, so a flag the tool still accepts (an old alias) is not blocked forever.
Reading a program's help runs it with `--help`, so it is done only for package-managed
binaries in system directories - never GUI apps, workspace scripts, or power/disk commands.

## ClawHub skills

`clawhub/` + `tools/clawhub.py` let the agent find and install skills from ClawHub
(clawhub.ai, OpenClaw's registry): `clawhub_search` -> `clawhub_inspect` -> `clawhub_install`
-> `use_skill`. A skill is a `SKILL.md` of instructions the model follows with its existing
tools, not new executable tools; everything a skill asks to run still goes through approval.

Install gate (`clawhub/gate.py`), any failure refuses the install:
* ClawHub's own `/verify` must pass (clean ClawScan verdict). The search API's
  `nonSuspiciousOnly` filter does not exclude "suspicious" skills, so it is not used as a gate.
  Only `clawhub.allow_unverified: true` in `config/default.yaml` relaxes this.
* Bundle checks (`clawhub/bundle.py`): no path traversal, symlinks, encrypted entries, oversize.
* Local scan (`clawhub/scanner.py`): pipe-to-shell installers, decode-and-run, password-protected
  payloads, bundled executables, credential-store reads, instructions to deceive the user.
* Skills are always addressed as `owner/slug` (slugs are shared by different publishers).
* The agent shows the inspection report (publisher, verdict, warnings) in the approval prompt
  and pins the inspected version, so a newer upload cannot replace what was reviewed.

Skills install into `<workdir>/skills/<slug>` with `.clawhub/origin.json` and
`<workdir>/.clawhub/lock.json` in OpenClaw's format, so the `clawhub` CLI sees them. The workdir
defaults to the project root (`CLAWHUB_WORKDIR` or `clawhub.workdir` override it).

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
```

## Adding a new tool

1. Add its args model to `schemas.py`.
2. Write the handler in `tools/<name>.py`. If it runs a subprocess, call
   `security.pipeline.guarded_execute(...)`. If it mutates state some
   other way (writing a file, calling a library), call
   `security.pipeline.guarded_call(...)`. Don't reimplement permissions/
   policy/confirmation/audit in the tool module — that logic lives in
   exactly one place.
3. Add an entry to `config/permissions.yaml`.
4. Register its `TOOL_SPEC` in `tools/__init__.py`'s `ALL_TOOL_SPECS`.
5. Add a test — if it's the pipeline's gating you're testing, add it to
   `tests/test_pipeline.py` rather than duplicating gate-by-gate checks
   per tool.

## Adding a new package manager

Implement `adapters/base.PackageAdapter` in a new file (see `adapters/apt.py`
for the shortest example), then add the class to `_CANDIDATES` in
`adapters/generic.py`. Nothing else needs to change.
