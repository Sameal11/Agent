"""Decide whether a ClawHub skill may be installed. Used by both clawhub_inspect (report
only) and clawhub_install (re-runs everything on the exact version being installed).

Checks, in order; any "block" stops the install and cannot be overridden by the model:
  1. ClawHub /verify: not malware-blocked and ClawScan verdict clean. A suspicious verdict
     blocks unless the USER set `clawhub.allow_unverified: true` in config/default.yaml.
  2. Bundle structure (bundle.read_bundle): no traversal, symlinks, executables bits,
     encrypted entries, size bombs.
  3. Local static scan (scanner.scan_bundle).
  4. Platform: a skill restricted to other OSes (e.g. ["macos"]) is blocked on Linux.
Missing binaries / env vars and undeclared credential env vars are reported as warnings:
they make a skill not work yet, not dangerous.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from linux_mcp.clawhub import bundle, scanner
from linux_mcp.clawhub.client import ClawHubClient, SkillRef
from linux_mcp.clawhub.skill_md import SkillManifest, parse_skill_md
from linux_mcp.config import settings


@dataclass
class Inspection:
    ref: SkillRef
    version: str = ""
    publisher: str = ""
    official_publisher: bool = False
    verify: dict = field(default_factory=dict)
    manifest: SkillManifest | None = None
    files: dict[str, bytes] = field(default_factory=dict)
    findings: list[scanner.Finding] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def installable(self) -> bool:
        return not self.blockers

    def report(self) -> dict:
        m = self.manifest
        return {
            "ref": str(self.ref), "version": self.version,
            "publisher": self.publisher, "official_publisher": self.official_publisher,
            "clawhub_verdict": {"ok": self.verify.get("ok"), "decision": self.verify.get("decision"),
                                "security": (self.verify.get("security") or {}).get("status"),
                                "reasons": self.verify.get("reasons")},
            "description": m.description if m else "",
            "requires": {"bins": m.requires_bins, "any_bins": m.requires_any_bins,
                         "env": m.requires_env, "os": m.os} if m else {},
            "files": sorted(p for p in self.files if p not in bundle.REGISTRY_FILES),
            "installable": self.installable,
            "blocked_because": self.blockers,
            "warnings": self.warnings,
            "findings": [f.as_dict() for f in self.findings],
        }


def _allow_unverified() -> bool:
    return bool((settings.raw.get("clawhub") or {}).get("allow_unverified", False))


def inspect(ref: SkillRef, client: ClawHubClient | None = None, version: str | None = None) -> Inspection:
    client = client or ClawHubClient()
    ins = Inspection(ref=ref)

    detail = client.skill(ref)
    owner = detail.get("owner") or {}
    ins.publisher = owner.get("displayName") or owner.get("handle") or ref.owner
    # The detail endpoint has no "official" flag; only search results carry it.
    ins.official_publisher = any(r["ref"] == str(ref) and r["official_publisher"]
                                 for r in client.search(ref.slug, limit=20))
    if (owner.get("handle") or ref.owner).lower() != ref.owner:
        ins.blockers.append(f"registry returned publisher '{owner.get('handle')}', not '{ref.owner}'")

    ins.verify = client.verify(ref)
    ins.version = version or str(ins.verify.get("version") or (detail.get("latestVersion") or {}).get("version") or "")
    if not ins.version:
        ins.blockers.append("ClawHub reported no version for this skill")
        return ins
    if version and ins.verify.get("version") and str(ins.verify["version"]) != version:
        ins.blockers.append(f"version {version} is no longer the verified latest "
                            f"({ins.verify['version']}); inspect again")
    if (ins.verify.get("publisherHandle") or ref.owner).lower() != ref.owner:
        ins.blockers.append("verification belongs to a different publisher")
    if not ins.verify.get("ok"):
        status = (ins.verify.get("security") or {}).get("status") or ins.verify.get("decision")
        msg = f"ClawHub verification did not pass (security: {status}; reasons: {ins.verify.get('reasons')})"
        (ins.warnings if _allow_unverified() else ins.blockers).append(msg)

    try:
        ins.files = bundle.read_bundle(client.download(ref, ins.version))
        md_name = bundle.skill_md_name(ins.files)
        ins.manifest = parse_skill_md(ins.files[md_name].decode("utf-8", errors="replace"), ref.slug)
    except (bundle.BundleError, ValueError) as e:
        ins.blockers.append(f"invalid bundle: {e}")
        return ins

    ins.findings = scanner.scan_bundle(ins.files)
    for f in ins.findings:
        text = f"{f.file}:{f.line}: {f.message}" if f.line else f"{f.file}: {f.message}"
        (ins.blockers if f.severity == "block" else ins.warnings).append(text)

    m = ins.manifest
    if m.os and "linux" not in m.os:
        ins.blockers.append(f"skill only supports {', '.join(m.os)}")
    missing_bins = [b for b in m.requires_bins if shutil.which(b) is None]
    if missing_bins:
        ins.warnings.append(f"needs programs that are not installed: {', '.join(missing_bins)}")
    if m.requires_any_bins and not any(shutil.which(b) for b in m.requires_any_bins):
        ins.warnings.append(f"needs one of: {', '.join(m.requires_any_bins)}")
    missing_env = [e for e in m.requires_env if not os.environ.get(e)]
    if missing_env:
        ins.warnings.append(f"needs environment variables that are not set: {', '.join(missing_env)}")
    undeclared = scanner.undeclared_env(ins.files, set(m.requires_env) | set(m.optional_env))
    if undeclared:
        ins.warnings.append(f"reads credentials it does not declare: {', '.join(undeclared)}")
    return ins
