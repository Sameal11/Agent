"""ClawHub registry client (public, unauthenticated endpoints only).

Behavior checked against the live registry (2026-10), where it differs from the published
docs/OpenAPI:
  * Slugs are NOT unique: `weather` belongs to several publishers, and a bare-slug lookup
    returns 409 AMBIGUOUS_SKILL_SLUG. Every call here therefore takes owner + slug, sent as
    the `ownerHandle` query parameter (accepted by skills/{slug}, /verify, /scan, /file and
    /download, though the OpenAPI spec lists it on none of them).
  * Search results are shaped `{install: {reference: "owner/slug"}, native: {owner, skill}}`,
    not the flat `{slug, ownerHandle}` the docs show; both shapes are accepted.
  * `nonSuspiciousOnly=true` on search does NOT exclude skills whose ClawScan verdict is
    "suspicious" (15 of 50 sampled skills failed /verify yet were still returned). Only
    /verify is a reliable gate, so install calls it for every skill.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass

from linux_mcp.utils import http

DEFAULT_REGISTRY = "https://clawhub.ai"
MAX_BUNDLE_BYTES = 50 * 1024 * 1024    # ClawHub's own server-side bundle limit

# Publisher handles: lowercase letters, digits, '-', '.', '_', starting and ending alphanumeric.
_HANDLE = r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?"
_SLUG = r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?"
REF_RE = re.compile(rf"^@?(?P<owner>{_HANDLE})/(?P<slug>{_SLUG})$")


class ClawHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillRef:
    owner: str
    slug: str

    @classmethod
    def parse(cls, ref: str) -> "SkillRef":
        m = REF_RE.match(ref.strip().lower())
        if not m:
            raise ClawHubError(
                f"'{ref}' is not an owner-qualified skill reference. Use 'owner/slug' exactly as "
                "clawhub_search returned it (slugs are shared by different publishers).")
        return cls(m["owner"], m["slug"])

    def __str__(self) -> str:
        return f"{self.owner}/{self.slug}"


def registry_url() -> str:
    """CLAWHUB_REGISTRY (the official CLI's variable) or the public registry."""
    url = os.environ.get("CLAWHUB_REGISTRY") or os.environ.get("CLAWDHUB_REGISTRY") or DEFAULT_REGISTRY
    if urllib.parse.urlparse(url).scheme != "https":
        raise ClawHubError(f"CLAWHUB_REGISTRY must be an https URL, got {url!r}")
    return url.rstrip("/")


class ClawHubClient:
    def __init__(self, base: str | None = None, timeout: float = 20):
        self.base = (base or registry_url()).rstrip("/")
        self.timeout = timeout

    # ---- transport ----
    def _get(self, path: str, params: dict | None = None, max_bytes: int = 5_000_000) -> http.Response:
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        for attempt in range(3):
            try:
                resp = http.request(url, max_bytes=max_bytes, timeout=self.timeout,
                                    headers={"Accept": "application/json, application/zip"})
            except http.FetchError as e:
                raise ClawHubError(f"ClawHub unreachable ({e.kind}): {e}") from e
            if resp.status != 429:
                return resp
            # Documented: honor Retry-After on 429, with a cap so a tool call never hangs.
            time.sleep(min(float(resp.headers.get("retry-after", "2") or 2), 10) + attempt)
        raise ClawHubError("ClawHub rate limit exceeded; try again in a minute.")

    def _json(self, path: str, params: dict | None = None) -> dict:
        resp = self._get(path, params)
        text = resp.text()
        if resp.status == 404:
            raise ClawHubError("Skill not found on ClawHub.")
        if resp.status == 409:
            raise ClawHubError(f"Ambiguous skill reference: {text[:300]}")
        if resp.status != 200:
            raise ClawHubError(f"ClawHub returned HTTP {resp.status}: {text[:200]}")
        try:
            return json.loads(text)
        except ValueError as e:
            raise ClawHubError("ClawHub returned a non-JSON response") from e

    # ---- endpoints ----
    def search(self, query: str, limit: int = 8) -> list[dict]:
        data = self._json("/api/v1/search", {"q": query, "limit": limit})
        return [self._summarize(r) for r in data.get("results", [])]

    @staticmethod
    def _summarize(r: dict) -> dict:
        native = r.get("native") or {}
        owner = native.get("owner") or r.get("owner") or {}
        ref = (r.get("install") or {}).get("reference") or (
            f"{r.get('ownerHandle') or owner.get('handle')}/{r.get('slug')}")
        return {
            "ref": ref,
            "name": r.get("displayName") or r.get("slug"),
            "summary": (r.get("summary") or (native.get("skill") or {}).get("summary") or "")[:240],
            "publisher": owner.get("displayName") or owner.get("handle"),
            "official_publisher": bool(owner.get("official")),
            "downloads": r.get("downloads"),
        }

    def skill(self, ref: SkillRef) -> dict:
        return self._json(f"/api/v1/skills/{ref.slug}", {"ownerHandle": ref.owner})

    def verify(self, ref: SkillRef) -> dict:
        """ClawHub's Skill Card verification. `ok` is true only when the version is not
        malware-blocked and its ClawScan verdict is clean."""
        return self._json(f"/api/v1/skills/{ref.slug}/verify", {"ownerHandle": ref.owner})

    def download(self, ref: SkillRef, version: str | None = None) -> bytes:
        resp = self._get("/api/v1/download", {"slug": ref.slug, "ownerHandle": ref.owner, "version": version},
                         max_bytes=MAX_BUNDLE_BYTES + 1)
        if resp.status != 200:
            raise ClawHubError(f"Download failed: HTTP {resp.status} {resp.text()[:200]}")
        if resp.content_type != "application/zip":
            # GitHub-backed skills return a JSON handoff to a GitHub archive instead of bytes.
            raise ClawHubError("This skill is hosted on GitHub, not ClawHub; only hosted skills are supported.")
        if len(resp.body) > MAX_BUNDLE_BYTES:
            raise ClawHubError("Skill bundle is larger than ClawHub's 50MB limit")
        return resp.body
