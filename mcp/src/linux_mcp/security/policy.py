"""Allow/block rules for shell commands, independent of per-tool permissions.

permissions.yaml controls which TOOLS are enabled; this module controls
which COMMAND PATTERNS are ever allowed to run, even from an enabled tool.
Both layers must pass before anything executes.

Two separate questions are answered here:
  check()               -> is this command EVER allowed?  (hard block, no bypass)
  needs_confirmation()  -> must a human approve THIS one?  (False only for a short
                           list of clearly read-only commands)

Blocking is token-aware rather than a naive substring match, so `echo rm -rf /`,
`man mkfs` or `grep dd= file` are not false positives, while `sudo rm -r -f /`,
`bash -c 'rm -rf /'` and `rm -rf ~` are caught.
"""
from __future__ import annotations

import os
import re
import shlex

# Patterns that are catastrophic wherever they appear in the text.
HARD_BLOCK = [
    re.compile(r":\(\)\s*\{.*\}\s*;\s*:"),                                # fork bomb
    re.compile(r"--no-preserve-root"),                                     # rm safety override
    re.compile(r">\s*/dev/(sd|hd|vd|xvd|nvme|mmcblk|disk)"),               # redirect onto a disk
]

# Directories that must never be the target of a recursive delete/chmod/chown.
CRITICAL_PATHS = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64", "/opt",
    "/proc", "/root", "/sbin", "/srv", "/sys", "/usr", "/var",
    "~", "$HOME", "${HOME}",
}

_WRAPPERS = {"sudo", "doas", "env", "command", "nohup", "time", "exec", "nice", "ionice",
             "setsid", "stdbuf", "timeout", "builtin"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
_BLOCKDEV = re.compile(r"^of=/dev/(sd|hd|vd|xvd|nvme|mmcblk|disk|loop)")

# Commands that execute immediately WITHOUT asking for approval. Read-only only:
# kill/pkill/killall were removed (they change state), and `date` / `hostname` are
# only auto-approved in their query forms (see _safe_args) because with arguments
# they set the system clock / hostname.
AUTO_APPROVE = re.compile(
    r"^\s*(ls|pwd|whoami|date|echo|cat|head|tail|wc|find|grep|df|du|ps|"
    r"uname|which|id|hostname|uptime|free)(\s|$)"
)

# Anything that can chain, substitute, redirect, or background.
SHELL_META = re.compile(r"[;&|<>`\n\r]|\$\(|\$\{")

# find flags that delete, execute, or write files
DANGEROUS_FIND = re.compile(r"\s-(delete|exec|execdir|ok|okdir|fprint\w*|fls)\b")

_DATE_SAFE_FLAGS = {"-u", "--utc", "--universal", "-R", "--rfc-email"}
_HOSTNAME_SAFE_FLAGS = {"-f", "--fqdn", "--long", "-s", "--short", "-i", "--ip-address", "-I",
                        "--all-ip-addresses", "-d", "--domain", "-a", "--alias", "-A",
                        "--all-fqdns", "-y", "--yp", "--nis"}


class PolicyViolation(RuntimeError):
    pass


def _segments(command: str, depth: int = 0) -> list[list[str]]:
    """Split a command line into simple commands (token lists), recursing into
    `sh -c '...'` / `eval '...'` payloads."""
    if depth > 3:
        return []
    text = command.replace("\n", " ; ")
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:  # unbalanced quotes etc. -> best effort
        tokens = text.split()

    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok and all(c in "();<>|&" for c in tok):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)

    out = list(segments)
    for seg in segments:
        head = os.path.basename(seg[0])
        if head in _SHELLS:
            for i, tok in enumerate(seg[1:], start=1):
                if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", tok) and i + 1 < len(seg):
                    out.extend(_segments(seg[i + 1], depth + 1))
                    break
        elif head == "eval":
            out.extend(_segments(" ".join(seg[1:]), depth + 1))
    return out


def _command_candidates(seg: list[str]) -> list[tuple[str, list[str]]]:
    """(basename, args) pairs for tokens that could be the command being run."""
    i = 0
    while i < len(seg) and re.match(r"^\w+=", seg[i]):  # leading VAR=value
        i += 1
    if i >= len(seg):
        return []
    if os.path.basename(seg[i]) in _WRAPPERS:
        return [(os.path.basename(t), seg[j + 1:]) for j, t in enumerate(seg[i:], start=i)]
    return [(os.path.basename(seg[i]), seg[i + 1:])]


def _is_critical(path: str) -> bool:
    p = path.replace("/*", "").rstrip("/") or "/"
    return p in CRITICAL_PATHS


def _violation(name: str, args: list[str]) -> str | None:
    flags = [a for a in args if a.startswith("-")]
    targets = [a for a in args if not a.startswith("-")]
    if name == "rm":
        recursive = any(a == "--recursive" or (not a.startswith("--") and re.search(r"[rR]", a))
                        for a in flags)
        if recursive and any(_is_critical(t) for t in targets):
            return "recursive delete of a system or home directory"
    elif name.startswith("mkfs"):
        return "creating a filesystem"
    elif name == "dd":
        if any(_BLOCKDEV.match(a) for a in args):
            return "dd onto a block device"
    elif name in {"chmod", "chown", "chgrp"}:
        recursive = any(a == "--recursive" or (not a.startswith("--") and "R" in a) for a in flags)
        if recursive and any(_is_critical(t) for t in targets):
            return f"recursive {name} of a system directory"
    return None

def programs(command: str) -> list[str]:
    """Basename of the program each simple command in `command` would run, looking through
    VAR=x prefixes, wrappers (nohup, setsid, env, sudo...) and `sh -c '...'` payloads."""
    names: list[str] = []
    for seg in _segments(command):
        i, wrapped = 0, False
        while i < len(seg):
            tok = seg[i]
            if re.match(r"^\w+=", tok):
                pass
            elif os.path.basename(tok) in _WRAPPERS:
                wrapped = True
            elif wrapped and tok.startswith("-"):
                pass
            else:
                names.append(os.path.basename(tok))
                break
            i += 1
    return names

def check(command: str) -> None:
    """Raises PolicyViolation if the command matches a hard-blocked pattern."""
    for pattern in HARD_BLOCK:
        if pattern.search(command):
            raise PolicyViolation(f"Command matches a blocked pattern: {command!r}")
    for seg in _segments(command):
        for name, args in _command_candidates(seg):
            why = _violation(name, args)
            if why:
                raise PolicyViolation(f"Blocked ({why}): {command!r}")


def _safe_args(word: str, args: list[str]) -> bool:
    if word == "date":
        return all(a.startswith("+") or a in _DATE_SAFE_FLAGS or a.startswith(("--rfc-3339", "-I", "--iso-8601"))
                   for a in args)
    if word == "hostname":
        return all(a in _HOSTNAME_SAFE_FLAGS for a in args)
    return True


def needs_confirmation(command: str) -> bool:
    """Returns True if approval is needed (anything NOT clearly safe)."""
    # Any chaining/redirection/substitution anywhere -> require approval.
    if SHELL_META.search(command):
        return True
    if DANGEROUS_FIND.search(command):
        return True
    m = AUTO_APPROVE.match(command)
    if not m:
        return True
    parts = command.split()
    return not _safe_args(m.group(1), parts[1:])
