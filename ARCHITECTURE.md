# Architecture

This document describes how Claw is put together: the processes, the module layout, the
execution and safety pipeline, the research-first flow, the ClawHub integration, the two LLM
backends, and every configuration knob. For a feature overview see [README.md](README.md).

---

## 1. Two processes, two connections

Claw is an **agent client** (`agent.py`) that drives an LLM and a **tool server**
(`mcp/`, the `linux-mcp` package) that performs system actions. They are separate processes:

```
┌──────────────────────┐   OpenAI chat API (HTTP)        ┌─────────────────────┐
│                      │ ──────────────────────────────▶ │  LLM backend        │
│  agent.py            │   or Anthropic Messages API      │  vLLM/Ollama/Claude │
│  (the ReAct loop,    │ ◀────────────────────────────── └─────────────────────┘
│   approvals, CLI,    │
│   URL/verify gates)  │   MCP over stdio (JSON-RPC)      ┌─────────────────────┐
│                      │ ──────────────────────────────▶ │  linux-mcp server   │
│                      │   list_tools / call_tool         │  (tools + security) │
└──────────────────────┘ ◀────────────────────────────── └─────────────────────┘
```

- The agent **launches the server as a subprocess** and speaks MCP to it over stdio.
- Because stdio *is* the MCP channel, the server never writes to stdout except protocol;
  logs go to stderr, and interactive prompts (when the server runs standalone) go to
  `/dev/tty`.
- The agent owns the terminal, so **human approval happens in the agent**. It tells the
  server to trust that via `MCP_TRUST_CLIENT_APPROVAL=1`.

---

## 2. The agentic loop (`agent.py`)

Per user turn:

1. Append the user message (with the current local time) to history.
2. Call the model (`_complete()` dispatches to the configured backend).
3. If the reply has **no tool calls**:
   - If it *looks like* a tool call written as text (a `use_skill "x"` in a code block),
     push back (up to 3×) telling the model it did not run — small models do this often.
   - Otherwise, it's the final answer; render and stop.
4. For each tool call, run it through `call_tool_with_confirmation()` (gates below), record
   the result, and loop.

Key agent-side logic:

| Concern | Where | What it does |
|---|---|---|
| URL gate | `host_is_known` | `open_url` is refused unless the host (or a parent domain) came from the user or a tool result this session |
| Shell verify gate | `_verify_shell` | runs `verify_command` once per command; holds (without running) if a program is missing or an undocumented **short** flag is present |
| sudo routing | `run_in_terminal` | `sudo`/`doas` commands run in the agent's own terminal so sudo can prompt; policy + `check_elevation` reject password-outside-prompt forms first |
| Skill install review | `_install_with_review` | runs `clawhub_inspect`, shows the report in the approval prompt, pins the inspected version |
| Read-only allowlist | `READ_ONLY_TOOLS` | these tools never prompt; everything else asks |

History handling differs by backend (see §6): the OpenAI backend trims/shrinks old history;
the Claude backend is strictly append-only.

---

## 3. The tool server (`mcp/src/linux_mcp/`)

```
linux_mcp/
├── server.py          MCP entry: registers tools, wires confirmation, renders results
├── config.py          Loads config/*.yaml + env; single source of settings
├── schemas.py         Pydantic arg models for every tool (→ JSON Schema)
├── validation.py      Shared input patterns (package/unit/url/program/skill names)
│
├── tools/             One module per tool family — build a command/action, hand it to the
│   ├── system.py        pipeline. They never implement permissions/policy/confirm/audit.
│   ├── research.py      environment_info · verify_command · tool_docs
│   ├── find_tool.py     local BM25 "which installed tool does X" + curated intents
│   ├── filesystem.py    read_file · write_file (jailed to the workspace)
│   ├── pyenv.py         ensure_python_env (workspace venv for pip deps)
│   ├── shell.py         shell (starts in workspace; GUI apps auto-detached)
│   ├── process.py       list_processes · kill_process
│   ├── services.py      control_service (systemd)
│   ├── packages.py      search_packages · install_package (via adapters)
│   ├── network.py       network_info      · applications.py  list_applications
│   ├── web.py           web_search (DDG-lite → Bing) · fetch_page (HTML→text) · weather.py
│   ├── browser.py       open_url (xdg-open, http(s) only, always confirms)
│   ├── clawhub.py       clawhub_search/inspect/install · list_skills · use_skill · remove_skill
│   └── desktop.py       take_screenshot (stub, disabled)
│
├── security/
│   ├── pipeline.py      THE one execution pipeline (guarded_execute / guarded_call)
│   ├── policy.py        hard blocklist, auto-approve read-only cmds, sudo/flag parsing
│   ├── confirmation.py  pluggable "ask the human" hook + stored-approval matching
│   ├── permissions.py   reads permissions.yaml
│   └── audit.py         append-only JSONL log of every command
│
├── adapters/          package-manager implementations behind one interface
│   ├── base.py          PackageAdapter (search, install, exists, owner_of, providers_of,
│   ├── pacman.py         describe_installed, …)
│   ├── apt.py           dnf.py            generic.py (picks the adapter by distro family)
│
├── clawhub/           ClawHub registry integration
│   ├── client.py        HTTP client (owner/slug refs, search, verify, download)
│   ├── skill_md.py      SKILL.md frontmatter + body parser
│   ├── bundle.py        safe in-memory ZIP reader (traversal/symlink/zip-bomb checks)
│   ├── scanner.py       local static scan of skill content
│   ├── gate.py          install decision (verify + bundle + scan + OS)
│   └── store.py         on-disk install in OpenClaw's lock/origin format
│
├── discovery/         what does this machine support? (os, package manager, commands)
└── utils/
    ├── executor.py      the ONLY module that calls subprocess
    ├── http.py          minimal http(s) client (research + clawhub), http-only redirects
    ├── logging.py · parser.py · platform.py
```

---

## 4. The security pipeline (`security/`)

Every system-touching action calls `guarded_execute()` (subprocess) or `guarded_call()`
(any other mutation, e.g. writing a file). Both run the **same gate**, in order:

```
1. permissions.tool_enabled(tool)      is this tool switched on in permissions.yaml?
2. policy.check(command)               is this command EVER allowed? (hard blocklist)
3. confirmation.confirm(...)           did a human approve this exact action once?
4. executor.run_* + audit.log_command  run it, and log it either way
```

- **`policy.py`** is token-aware, not substring matching: `echo rm -rf /` and `man mkfs` are
  fine; `sudo rm -r -f /`, `bash -c 'rm -rf /'`, `rm -rf ~` are blocked. It parses through
  wrappers (`sudo`, `nohup`, `env`, …) and `sh -c '…'` payloads (`invocations()`), and
  decides which commands are read-only enough to skip confirmation.
- **`executor.py`** is the single subprocess choke point: stdin is `/dev/null` (so children
  can't eat the MCP channel), output goes to a temp file (so a backgrounded GUI app can't
  hang the read), processes run in their own session (so timeouts kill the whole group), and
  output is capped (overridable per call for large read-only dumps like `pacman -Qi`).
- **Approval** is pluggable (`confirmation.set_confirmation_handler`). With `agent.py` the
  agent asks the human and sets `MCP_TRUST_CLIENT_APPROVAL=1`; standalone, the server asks
  on `/dev/tty` and fails closed if there is none. Approval text is sanitized so control
  characters in a command can't hide or rewrite part of it.

---

## 4a. Workspace isolation

`utils/workspace.py` is the single path jail. `read_file`, `write_file` and the shell's
working directory all resolve through `resolve_within()`, which does
`realpath(root / path)` + `commonpath(root, …)` — so `../../../../etc/shadow`, an absolute
path, or a symlink planted inside the workspace are all rejected. The workspace is a
dedicated `~/agent_workspace` (configurable via `LINUX_MCP_WORKSPACE`), created on demand.

What is confined vs. not, on purpose:

- **Confined:** the agent's own file writes/reads and the shell's `cwd`. Downloads (`curl -O`)
  land in the workspace because that's where the shell runs. Python deps go in a workspace
  venv (`ensure_python_env` → `.venv/bin/pip`), never the host's global Python.
- **Not confined (by design, for a system agent):** a shell command may still *read* system
  paths (`/etc/os-release`, logs) and, through `install_package`/`sudo`, change global state.
  The hard policy blocklist guards against catastrophic system writes; approval guards the rest.

This is path discipline, not an OS sandbox. For true confinement of what a command can touch,
run the shell under bubblewrap/a container with the workspace bind-mounted — a possible
future `sandbox: strict` mode; it is deliberately not forced, since it would block the
network/system tools a system agent needs.

---

## 5. Research-first flow

```
user asks → model → (shell command)
                      │
          agent._verify_shell  ──▶  verify_command (server)
                      │                 │ for each program in the command:
                      │                 │  • installed? (shutil.which)
                      │                 │  • which package owns it / would provide it (adapter)
                      │                 │  • is each flag in the installed --help?
                      │                 │    (dispatchers: merge `tool --help` + `tool <sub> --help`)
                      ▼
        missing program OR undocumented short flag?
            yes → return findings, DO NOT run; model fixes via tool_docs / install
            no  → approval → run
```

- `tool_docs` reads a program's own `tldr` / `man` / `--help` (tldr/man used when present),
  filtered to a query — used to find the *correct* option.
- `find_tool` answers "which installed tool does X". It builds a BM25 index over the
  installed packages' name + description (one package-DB query, cached in-process) and a
  small curated intent map (`port scan`→nmap, `crack password hashes`→hashcat/john, …).
  CPU-only, no embeddings, no network.
- Help is only executed for package-managed binaries in system directories — never GUI apps,
  workspace/home scripts, or power/disk commands (`dd`, `mkfs`, `rm`, `reboot`, …).

---

## 6. LLM backends

`LLM_PROVIDER` selects the backend; the agent keeps its history in OpenAI chat format either
way and translates on demand.

| | OpenAI-compatible (`openai`, default) | Anthropic (`anthropic`) |
|---|---|---|
| Module | `openai` SDK in `agent.py` | `llm_anthropic.py` |
| Endpoint | `LLM_URL` (vLLM/Ollama/LM Studio) | Anthropic Messages API |
| Model | `LLM_MODEL` | `ANTHROPIC_MODEL` (default `claude-opus-5-5`) |
| History | trimmed + shrunk to fit small context | **append-only** (preserved thinking) |
| System prompt | rebuilt per turn allowed | **frozen per session** |
| Extras | — | refusal fallback (`fallbacks:"default"`), prompt caching, effort |

`llm_anthropic.complete()` converts OpenAI-format history to Messages API blocks (merging
parallel tool results into one user message, replaying assistant turns with their original
content blocks including thinking), and returns an OpenAI-shaped reply so the loop is
backend-agnostic. Refusals are reported to the user, not treated as answers.

---

## 7. ClawHub integration

A ClawHub skill is **not** executable code with a tool schema — it is a `SKILL.md` of
instructions (plus files) that the model follows using the tools it already has. "Installing"
a skill makes its instructions loadable with `use_skill`; it grants no new capability, and
anything a skill says to run still goes through the normal shell approval.

**Flow:** `clawhub_search` → `clawhub_inspect` → `clawhub_install` → `use_skill`.

**Install gate (`clawhub/gate.py`) — any failure refuses the install:**

1. `GET /verify` — not malware-blocked, ClawScan verdict clean. (The search API's
   `nonSuspiciousOnly` filter does **not** reliably exclude suspicious skills, so it is not
   used as the gate.) Relaxable only by the user via `clawhub.allow_unverified`.
2. `bundle.read_bundle` — reject path traversal, absolute paths, symlinks, encrypted entries,
   too many/too large files, and zip bombs (measured on decompressed bytes).
3. `scanner.scan_bundle` — flag pipe-to-shell installers, decode-and-run, password-protected
   payloads, bundled executables, credential/wallet reads, and prompt-injection text.
4. OS match, plus advisory warnings for missing bins/env vars and undeclared credential vars.

Skills are addressed as `owner/slug` (slugs are **not** unique across publishers). The agent
shows the inspection report in the approval prompt and pins the inspected version. Installed
skills land in OpenClaw's on-disk format: `<workdir>/skills/<slug>/` with
`.clawhub/origin.json` and `<workdir>/.clawhub/lock.json`, so the real `clawhub` CLI
recognizes them. Loaded skill text is wrapped as untrusted data with a stated precedence.

---

## 8. Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `LLM_PROVIDER` | agent | `openai` (default) or `anthropic` |
| `LLM_URL` | agent | OpenAI-compatible base URL |
| `LLM_MODEL` | agent | model id for the OpenAI backend |
| `LLM_KEY` | agent | API key for that server |
| `LLM_TIMEOUT` | agent | request timeout (s) |
| `LLM_MAX_TOOL_CHARS` / `LLM_MAX_HISTORY_CHARS` | agent | context trimming budgets (OpenAI backend) |
| `ANTHROPIC_API_KEY` / `ant auth login` | Claude backend | credentials |
| `ANTHROPIC_MODEL` | Claude backend | default `claude-opus-5-5` |
| `ANTHROPIC_EFFORT` | Claude backend | reasoning effort (default `high`) |
| `ANTHROPIC_MAX_TOKENS` | Claude backend | max output tokens |
| `AGENT_SUDO_TIMEOUT` | agent | seconds allowed for a sudo command incl. typing the password |
| `AGENT_MAX_STEPS` | agent | max tool-use iterations per user turn (default 20) |
| `MCP_SERVER_CMD` | agent | override how the server subprocess is launched |
| `MCP_TRUST_CLIENT_APPROVAL` | server | set by agent: trust the client's human approval |
| `MCP_APPROVAL_PATH` | server | file of pre-approved actions |
| `LINUX_MCP_CONFIG` / `LINUX_MCP_PERMISSIONS` | server | override config/permission file paths |
| `LINUX_MCP_WORKSPACE` | server | workspace root for shell/read/write |
| `LINUX_MCP_AUDIT_LOG` / `LINUX_MCP_LOG_LEVEL` | server | audit log path / log level |
| `CLAWHUB_REGISTRY` (`CLAWDHUB_REGISTRY`) | clawhub | registry base URL (default clawhub.ai) |
| `CLAWHUB_WORKDIR` | clawhub | where skills install (default: project root) |

Relative paths in config or env resolve against the project root, never the launch directory.

---

## 9. Extending the project

**Add a tool:** add its args model to `schemas.py`; write `tools/<name>.py` routing any
mutation through `guarded_execute`/`guarded_call`; register its spec in
`tools/__init__.py::ALL_TOOL_SPECS`; add it to `config/permissions.yaml`; add a test. If it
reads state only, also add it to `READ_ONLY_TOOLS` in `agent.py`.

**Add a package manager:** implement `adapters/base.PackageAdapter` in a new file and add the
class to `_CANDIDATES` in `adapters/generic.py`; map its distro IDs in `discovery/os.py`.

**Add an LLM backend:** implement a `complete(system, history, tools) -> Reply` module like
`llm_anthropic.py` and dispatch to it in `Agent._complete()`.

---

## 10. Repository layout (root)

```
agent.py              the agent client, ReAct loop, CLI, approval & research gates
llm_anthropic.py      Claude (Anthropic API) backend
requirements.txt      runtime deps          pytest.ini   test config (agent_tests + mcp/tests)
agent_tests/          tests for the agent client
mcp/                  the linux-mcp tool server (package, config, src, tests, README)
```

Run the whole suite with `pytest` from the project root (151 tests).
