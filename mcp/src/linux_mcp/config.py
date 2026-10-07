"""Loads and merges config/default.yaml + config/permissions.yaml + env vars.

This is the single place that owns "where did this setting come from".
Precedence, low to high: built-in defaults -> default.yaml -> env vars.
permissions.yaml is read separately (it is policy, not a tunable).

Relative paths (in YAML *or* env vars) are resolved against the project root, never
against whatever directory the server happened to be launched from. Previously a
relative ``./workspace`` was resolved against the CWD, and a relative
LINUX_MCP_PERMISSIONS silently resolved to a non-existent file -> zero tools.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]



def _load_server_env_file(path: Path) -> None:
    """Import ONLY the server's own settings (LINUX_MCP_*, MCP_*) from .env.

    The same .env also holds the agent's LLM_URL / LLM_KEY. A blanket load_dotenv() put
    those into this process's environment, and every command the model runs inherits it —
    so `printenv` handed the model the API key even though the agent had scrubbed it.
    Real environment variables win over the file, as with load_dotenv().
    """
    if not path.exists():
        return
    for key, value in dotenv_values(path).items():
        if value is not None and key.startswith(("LINUX_MCP_", "MCP_")):
            os.environ.setdefault(key, value)


_load_server_env_file(REPO_ROOT / ".env")


@dataclass
class Settings:
    config_path: Path
    permissions_path: Path
    log_level: str
    audit_log_path: Path
    workspace_root: Path
    max_output_chars: int = 20_000
    shell_timeout_seconds: int = 60
    raw: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)

    def tool_enabled(self, tool_name: str) -> bool:
        return bool(self.permissions.get("tools", {}).get(tool_name, {}).get("enabled", False))

    def requires_confirmation(self, tool_name: str) -> bool:
        return bool(self.permissions.get("tools", {}).get(tool_name, {}).get("confirm", True))


def _resolve(p: str | os.PathLike) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def _load_yaml(path: Path, *, required: bool = False) -> dict:
    if not path.exists():
        if required:
            raise FileNotFoundError(
                f"Required config file not found: {path}. "
                "Check LINUX_MCP_PERMISSIONS / LINUX_MCP_CONFIG (relative paths are "
                f"resolved against {REPO_ROOT})."
            )
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    config_path = _resolve(os.getenv("LINUX_MCP_CONFIG") or REPO_ROOT / "config" / "default.yaml")
    permissions_path = _resolve(
        os.getenv("LINUX_MCP_PERMISSIONS") or REPO_ROOT / "config" / "permissions.yaml"
    )
    raw = _load_yaml(config_path)

    return Settings(
        config_path=config_path,
        permissions_path=permissions_path,
        log_level=os.getenv("LINUX_MCP_LOG_LEVEL", "INFO"),
        audit_log_path=_resolve(os.getenv("LINUX_MCP_AUDIT_LOG") or raw.get("audit_log", "audit.log")),
        workspace_root=_resolve(os.getenv("LINUX_MCP_WORKSPACE") or raw.get("workspace_root", "workspace")),
        max_output_chars=int(raw.get("max_output_chars", 20_000)),
        shell_timeout_seconds=int(raw.get("shell_timeout_seconds", 60)),
        raw=raw,
        permissions=_load_yaml(permissions_path, required=True),
    )


settings = load_settings()
