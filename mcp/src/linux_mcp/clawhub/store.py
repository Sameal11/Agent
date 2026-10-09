"""Installed skills on disk, in the same layout as the `clawhub` CLI and OpenClaw.

    <workdir>/skills/<slug>/SKILL.md ...              the skill's files
    <workdir>/skills/<slug>/.clawhub/origin.json      per-skill install marker
    <workdir>/.clawhub/lock.json                      workspace lockfile

Field names and shapes follow OpenClaw's src/skills/lifecycle/clawhub-store.ts
(normalizeClawHubSkillOrigin / recordClawHubSkillInstall), so `clawhub list`, `clawhub
update` and OpenClaw's skill status accept skills installed here. The lockfile is keyed
by slug, so only one publisher's skill per slug can be installed in a workdir.
`installedAt` is written once and reused for both files: OpenClaw compares them with
strict equality (see openclaw/clawhub#2559).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from linux_mcp.clawhub.bundle import REGISTRY_FILES
from linux_mcp.clawhub.client import SkillRef
from linux_mcp.clawhub.skill_md import SKILL_FILE_NAMES, SkillManifest, parse_skill_md
from linux_mcp.config import settings

DOT_DIR = ".clawhub"


def workdir() -> Path:
    """CLAWHUB_WORKDIR (the official CLI's variable), else `clawhub.workdir` from
    default.yaml, else the project root."""
    configured = os.environ.get("CLAWHUB_WORKDIR") or (settings.raw.get("clawhub") or {}).get("workdir")
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (settings.config_path.parent.parent / path).resolve()
    return settings.config_path.parent.parent.parent.resolve()


def skills_dir() -> Path:
    return workdir() / "skills"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def read_lock() -> dict:
    path = workdir() / DOT_DIR / "lock.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "skills": {}}
    if data.get("version") != 1 or not isinstance(data.get("skills"), dict):
        raise ValueError(f"Malformed ClawHub lockfile at {path}; repair or remove it first.")
    return data


def installed_owner(slug: str) -> str | None:
    entry = read_lock()["skills"].get(slug)
    return entry.get("ownerHandle") if entry else None


def install(ref: SkillRef, version: str, files: dict[str, bytes], registry: str) -> Path:
    """Write the (already validated) files and the origin/lock records. The skill appears
    atomically: files go to a temp dir first, then replace any previous version."""
    root = skills_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = root / ref.slug
    staging = Path(tempfile.mkdtemp(dir=root, prefix=f".{ref.slug}-"))
    try:
        for rel, content in files.items():
            if rel in REGISTRY_FILES:
                continue
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            dest.chmod(0o644)   # never executable as installed; running a script is a separate, approved step
        installed_at = int(time.time() * 1000)
        origin = {"version": 1, "registry": registry, "slug": ref.slug, "ownerHandle": ref.owner,
                  "installedVersion": version, "installedAt": installed_at}
        _write_json(staging / DOT_DIR / "origin.json", origin)
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    lock = read_lock()
    lock["skills"][ref.slug] = {"version": version, "registry": registry,
                                "installedAt": installed_at, "ownerHandle": ref.owner}
    _write_json(workdir() / DOT_DIR / "lock.json", lock)
    return target


def remove(slug: str) -> None:
    target = skills_dir() / slug
    if target.is_dir():
        shutil.rmtree(target)
    lock = read_lock()
    if lock["skills"].pop(slug, None) is not None:
        _write_json(workdir() / DOT_DIR / "lock.json", lock)


def _skill_file(folder: Path) -> Path | None:
    return next((folder / n for n in SKILL_FILE_NAMES if (folder / n).is_file()), None)


def list_installed() -> list[dict]:
    """Every skill folder under skills/, ClawHub-installed or dropped in by hand."""
    lock = read_lock()["skills"]
    out = []
    root = skills_dir()
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")) if root.is_dir() else []:
        md = _skill_file(folder)
        if md is None:
            continue
        try:
            manifest = parse_skill_md(md.read_text(encoding="utf-8", errors="replace"), folder.name)
        except ValueError:
            continue
        entry = lock.get(folder.name) or {}
        out.append({"name": folder.name, "description": manifest.description[:200],
                    "source": f"clawhub:{entry['ownerHandle']}/{folder.name}@{entry.get('version')}"
                    if entry.get("ownerHandle") else "local"})
    return out


def load(slug: str) -> tuple[SkillManifest, Path]:
    folder = (skills_dir() / slug).resolve()
    if folder.parent != skills_dir().resolve() or not folder.is_dir():
        raise FileNotFoundError(f"No installed skill named '{slug}'. Call list_skills.")
    md = _skill_file(folder)
    if md is None:
        raise FileNotFoundError(f"Skill '{slug}' has no SKILL.md")
    return parse_skill_md(md.read_text(encoding="utf-8", errors="replace"), slug), folder

