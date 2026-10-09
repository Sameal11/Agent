"""Parse a ClawHub/OpenClaw SKILL.md: optional YAML frontmatter, then markdown instructions.

Format per https://docs.openclaw.ai/clawhub/skill-format. Runtime requirements live under
`metadata.openclaw` (aliases `clawdbot`, `clawdis`). `metadata` may be block YAML or an
inline JSON object; YAML parses both.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

SKILL_FILE_NAMES = ("SKILL.md", "skill.md", "skills.md")   # legacy names are accepted too
_META_KEYS = ("openclaw", "clawdbot", "clawdis")


@dataclass
class SkillManifest:
    name: str
    description: str
    version: str = ""
    homepage: str = ""
    requires_bins: list[str] = field(default_factory=list)
    requires_any_bins: list[str] = field(default_factory=list)
    requires_env: list[str] = field(default_factory=list)
    optional_env: list[str] = field(default_factory=list)
    os: list[str] = field(default_factory=list)
    install: list[dict] = field(default_factory=list)
    body: str = ""


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []


def split_frontmatter(text: str) -> tuple[dict, str]:
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        data = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML frontmatter: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data, text[end + 4:].lstrip("\n")


def parse_skill_md(text: str, fallback_name: str = "") -> SkillManifest:
    front, body = split_frontmatter(text)
    meta = front.get("metadata") or {}
    if isinstance(meta, str):          # some skills store the JSON as a quoted string
        try:
            meta = yaml.safe_load(meta) or {}
        except yaml.YAMLError:
            meta = {}
    runtime = next((meta[k] for k in _META_KEYS if isinstance(meta, dict) and isinstance(meta.get(k), dict)), {})
    requires = runtime.get("requires") or {}
    env_vars = runtime.get("envVars") or []
    required_env = set(_strings(requires.get("env")))
    if runtime.get("primaryEnv"):
        required_env.add(str(runtime["primaryEnv"]))
    optional_env: set[str] = set()
    for var in env_vars if isinstance(env_vars, list) else []:
        if not (isinstance(var, dict) and var.get("name")):
            continue
        if var.get("required") is False:   # documented: optional only when explicitly false
            optional_env.add(str(var["name"]))
        else:
            required_env.add(str(var["name"]))
    return SkillManifest(
        name=str(front.get("name") or fallback_name),
        description=str(front.get("description") or "").strip(),
        version=str(front.get("version") or ""),
        homepage=str(front.get("homepage") or runtime.get("homepage") or ""),
        requires_bins=_strings(requires.get("bins")),
        requires_any_bins=_strings(requires.get("anyBins")),
        requires_env=sorted(required_env),
        optional_env=sorted(optional_env - required_env),
        os=[o.lower() for o in _strings(runtime.get("os"))],
        install=[i for i in (runtime.get("install") or []) if isinstance(i, dict)],
        body=body,
    )
