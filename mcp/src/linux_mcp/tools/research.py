"""Research-first tools: look things up on THIS machine before running a command.

A small model's memory of command syntax is often wrong for the installed version (it used
`nmap -sP`, which this nmap renamed to `-sn`). These read-only tools replace recall with
evidence from the local system:

  environment_info  what OS / family / package manager / desktop / tools are really here
  tool_docs         an installed program's own --help / man / tldr, filtered to a query
  verify_command    for each program in a command: does it exist, which package owns it (or
                    would provide it), and is each flag documented by the installed version

Reading a program's help runs that program with --help, so it is done only for
package-managed binaries in system directories, never for GUI apps, scripts in the
workspace/home, or power/disk commands.
"""
from __future__ import annotations

import functools
import os
import platform
import re
import shutil

from linux_mcp.adapters import detect_package_adapter
from linux_mcp.discovery.commands import available_commands
from linux_mcp.discovery.os import detect_distro, os_family
from linux_mcp.schemas import ToolDocsArgs, ToolResult, VerifyCommandArgs
from linux_mcp.security import policy
from linux_mcp.tools import applications
from linux_mcp.utils import executor

_adapter = detect_package_adapter()

# Shell builtins and keywords: never on PATH, always "present", nothing to verify.
_BUILTINS = set("""
. : [ [[ ]] alias bg bind break builtin case cd command continue declare dirs disown do done
echo elif else esac eval exec exit export false fg fi for function getopts hash help history if
in jobs let local logout popd printf pushd pwd read readonly return select set shift shopt
source test then time times trap true type typeset ulimit umask unalias unset until wait while
""".split())

# Never run just to read their help: an unknown flag here would mean doing something.
_NEVER_PROBE = {"reboot", "poweroff", "halt", "shutdown", "init", "telinit", "kexec",
                "mkfs", "fdisk", "parted", "wipefs", "dd", "shred", "rm", "mv"}
_SYSTEM_DIRS = ("/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/", "/usr/local/bin/", "/usr/local/sbin/", "/opt/")

# Interpreters/wrappers: later arguments belong to a script or another command, not to them.
_NO_FLAG_CHECK = {"python", "python3", "node", "perl", "ruby", "php", "java", "bash", "sh",
                  "zsh", "dash", "fish", "env", "xargs", "sudo", "doas", "nohup", "setsid"}

_FLAG_IN_HELP = re.compile(r"(?<![\w-])(--?[A-Za-z][A-Za-z0-9][A-Za-z0-9-]*|-[A-Za-z0-9])")
_FLAG_TOKEN = re.compile(r"^--?[A-Za-z0-9]")

# Tools whose options live under an operation/subcommand, not in the top-level --help
# (pacman's --needed is under `pacman -S --help`; git's under `git commit -h`).
_DISPATCHERS = {"pacman", "git", "apt", "apt-get", "dnf", "systemctl", "journalctl", "docker",
                "podman", "npm", "cargo", "pip", "pip3", "kubectl", "nmcli", "gh", "flatpak"}
_OP_TOKEN = re.compile(r"^-{0,2}[A-Za-z][A-Za-z0-9-]*$")


def environment_info(_args=None) -> ToolResult:
    """Ground truth for the model's first move on any task: what this machine is and has."""
    distro = detect_distro()
    browsers = [b for b in ("firefox", "chromium", "google-chrome", "google-chrome-stable",
                            "brave", "brave-browser", "xdg-open") if shutil.which(b)]
    desktop = os.getenv("XDG_CURRENT_DESKTOP") or os.getenv("XDG_SESSION_TYPE") or (
        "x11" if os.getenv("DISPLAY") else "none (headless)")
    data = {
        "os": platform.platform(terse=True),
        "distro": distro.get("PRETTY_NAME") or distro.get("NAME"),
        "family": os_family(distro),
        "package_manager": _adapter.name if _adapter else None,
        "desktop": desktop,
        "session_type": os.getenv("XDG_SESSION_TYPE"),
        "browsers": browsers,
        "common_tools": available_commands(),
        "is_root": os.geteuid() == 0 if hasattr(os, "geteuid") else False,
    }
    return ToolResult(ok=True, data=data)


def _resolve_probeable(program: str) -> str | None:
    """Absolute path of `program` if it is safe to run with --help, else None."""
    base = os.path.basename(program)
    if base in _NEVER_PROBE or base in applications.gui_executables():
        return None
    path = shutil.which(program)
    if not path:
        return None
    real = os.path.realpath(path)
    if not real.startswith(_SYSTEM_DIRS):   # not a workspace/home script
        return None
    return path


def _run_help(path: str, prefix: tuple, flags: tuple) -> str:
    for flag in flags:
        try:
            out = executor.run_readonly([path, *prefix, flag], timeout=10, allow_nonzero=True)
        except executor.CommandError:
            out = ""
        if out and out.strip():
            return out[:40_000]
    return ""


@functools.lru_cache(maxsize=256)
def _help_text(path: str) -> str:
    """Top-level --help / -h output of a binary. Empty string if it produced none."""
    return _run_help(path, (), ("--help", "-h"))


@functools.lru_cache(maxsize=256)
def _subcommand_help(path: str, op: str) -> str:
    """Help for one operation/subcommand, e.g. `pacman -S -h` or `git commit -h`. Tries -h
    first: on some tools `<sub> --help` opens a pager, while -h prints usage directly."""
    return _run_help(path, (op,), ("-h", "--help"))


def _flags_in(help_text: str) -> set[str]:
    return set(_FLAG_IN_HELP.findall(help_text))


def _known_flags(path: str, base: str, args: list[str]) -> set[str]:
    known = _flags_in(_help_text(path))
    if base in _DISPATCHERS:
        op = next((a for a in args if _OP_TOKEN.match(a)), None)
        if op:
            known |= _flags_in(_subcommand_help(path, op))
    return known


def _classify_flags(args: list[str], known: set[str]) -> dict:
    """Split the flag tokens in `args` into documented / undocumented by the installed help.
    Advisory only: a flag is 'undocumented' when neither it nor its single-letter parts
    appear in help, which still cannot prove the tool rejects it."""
    documented, undocumented = [], []
    for tok in args:
        flag = tok.split("=", 1)[0]
        if not _FLAG_TOKEN.match(flag) or flag in ("-", "--"):
            continue
        if flag in known:
            documented.append(flag)
            continue
        # A combined GNU short group like -la is -l -a; documented if every letter is.
        if re.fullmatch(r"-[A-Za-z0-9]+", flag) and all(f"-{c}" in known for c in flag[1:]):
            documented.append(flag)
        else:
            undocumented.append(flag)
    return {"documented": documented, "undocumented": undocumented}


def verify_command(args: VerifyCommandArgs) -> ToolResult:
    invocations = policy.invocations(args.command)
    if not invocations:
        return ToolResult(ok=False, error="Could not parse any program from the command.")

    programs = []
    all_found = True
    for program, prog_args in invocations:
        base = os.path.basename(program)
        if base in _BUILTINS:
            programs.append({"program": base, "status": "builtin"})
            continue

        entry: dict = {"program": base}
        path = shutil.which(program)
        if not path:
            all_found = False
            entry["status"] = "missing"
            providers = _adapter.providers_of(base) if _adapter else None
            if providers:
                entry["install_hint"] = f"provided by: {', '.join(providers[:5])}"
            elif providers is None and _adapter:
                entry["install_hint"] = (f"run `search_packages {base}` to find the package "
                                         f"({_adapter.name} file list not downloaded)")
            programs.append(entry)
            continue

        entry["status"] = "installed"
        entry["path"] = path
        if _adapter:
            owner = _adapter.owner_of(os.path.realpath(path))
            if owner:
                entry["package"] = owner

        if base not in _NO_FLAG_CHECK:
            probe = _resolve_probeable(program)
            help_text = _help_text(probe) if probe else ""
            if help_text:
                flags = _classify_flags(prog_args, _known_flags(probe, base, prog_args))
                if flags["documented"] or flags["undocumented"]:
                    entry["flags"] = flags
                if flags["undocumented"]:
                    entry["flag_note"] = (
                        f"{', '.join(flags['undocumented'])} not found in this version's --help; "
                        "confirm with tool_docs before relying on it.")
            elif probe:
                entry["flag_note"] = "this program has no --help to check flags against"
        programs.append(entry)

    return ToolResult(ok=all_found, data={
        "command": args.command,
        "all_programs_installed": all_found,
        "programs": programs,
    })


def tool_docs(args: ToolDocsArgs) -> ToolResult:
    path = _resolve_probeable(args.program)
    if path is None:
        if shutil.which(args.program) is None:
            providers = _adapter.providers_of(args.program) if _adapter else None
            hint = f" It may be provided by: {', '.join(providers[:5])}." if providers else ""
            return ToolResult(ok=False, error=f"'{args.program}' is not installed.{hint}")
        return ToolResult(ok=False, error=f"'{args.program}' is not a documentable command-line tool here.")

    text, source = "", ""
    # tldr (practical examples) and man give better docs than --help when present.
    if shutil.which("tldr"):
        try:
            text, source = executor.run_readonly(["tldr", args.program], timeout=10, allow_nonzero=True), "tldr"
        except executor.CommandError:
            text = ""
    if not text.strip() and shutil.which("man"):
        try:
            env = {**os.environ, "MANPAGER": "cat", "PAGER": "cat", "MANWIDTH": "100"}
            text = executor.run_readonly(["man", args.program], timeout=10, allow_nonzero=True, env=env)
            source = "man"
        except executor.CommandError:
            text = ""
    if not text.strip():
        text, source = _help_text(path), "--help"
    if not text.strip():
        return ToolResult(ok=False, error=f"'{args.program}' produced no documentation.")

    if args.query:
        q = args.query.lower()
        hits = [ln for ln in text.splitlines() if q in ln.lower()]
        text = "\n".join(hits) if hits else f"(no line matches '{args.query}'; showing the top)\n" + text
    return ToolResult(ok=True, data={"program": args.program, "source": source,
                                     "docs": text[:args.max_chars]})


ENV_SPEC = {"name": "environment_info", "args_model": None, "handler": environment_info,
            "description": "Report this machine's OS, distro family, package manager, desktop, browsers "
                           "and installed tools. Call it before assuming what is available here."}
VERIFY_SPEC = {"name": "verify_command", "args_model": VerifyCommandArgs, "handler": verify_command,
               "description": "Before running a shell command, check it: for each program, whether it is "
                              "installed, which package owns it (or would provide it), and whether each flag "
                              "is in the installed version's --help. Use it to avoid guessed syntax."}
DOCS_SPEC = {"name": "tool_docs", "args_model": ToolDocsArgs, "handler": tool_docs,
             "description": "Show an installed program's own documentation (tldr/man/--help), optionally only "
                            "lines matching a query (e.g. an option). Use it to confirm exact syntax."}
