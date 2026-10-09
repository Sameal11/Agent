"""The agent's workspace: the one directory it may create files in and work from.

Workspace isolation, in one place so read_file, write_file and shell all share it:
  * read_file / write_file may only touch paths inside the workspace.
  * shell starts in the workspace and cannot be pointed (cwd) outside it.

This confines the agent's OWN artifacts (code it writes, files it downloads, documents it
builds) to the workspace. It is NOT a full OS sandbox: a shell command can still *read*
system paths and, through sudo / install_package, change global state on purpose — that is
required for a system agent. True confinement of what a command can touch needs an OS
sandbox (bubblewrap / container); this module provides the path discipline, the hard policy
blocklist provides the catastrophe guard, and approval provides the human check.

Traversal protection uses realpath (resolving `..` and symlinks) + commonpath, so neither
`../../../../etc/shadow` nor a symlink planted inside the workspace can escape.
"""
from __future__ import annotations

import os
from pathlib import Path

from linux_mcp.config import settings


def workspace_root() -> Path:
    """Absolute, symlink-resolved workspace path. Created if missing."""
    root = Path(settings.workspace_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return Path(os.path.realpath(root))


def resolve_within(path: str) -> Path:
    """Resolve `path` (relative to the workspace, or absolute) and require it to stay inside
    the workspace. Raises ValueError on any escape."""
    root = workspace_root()
    # Path('/ws') / '/etc/x' == Path('/etc/x'): an absolute path is taken as-is, then rejected.
    candidate = os.path.realpath(root / path)
    if os.path.commonpath([str(root), candidate]) != str(root):
        raise ValueError(f"path escapes the workspace ({root}): {path!r}")
    return Path(candidate)


def is_within(path: str | os.PathLike) -> bool:
    root = str(workspace_root())
    try:
        return os.path.commonpath([root, os.path.realpath(path)]) == root
    except ValueError:      # different drives / relative mismatch
        return False
