"""Read a downloaded skill ZIP safely, entirely in memory, before anything touches disk.

Rejected outright: absolute paths, `..` components, symlinks and other special files,
more than MAX_FILES entries, files over MAX_FILE_BYTES, and archives whose *uncompressed*
size exceeds MAX_TOTAL_BYTES (zip bombs) - checked against the real decompressed bytes,
not the sizes the archive claims.
"""
from __future__ import annotations

import io
import posixpath
import stat
import zipfile

from linux_mcp.clawhub.skill_md import SKILL_FILE_NAMES

MAX_FILES = 500
MAX_FILE_BYTES = 10 * 1024 * 1024       # ClawHub's own per-file download limit
MAX_TOTAL_BYTES = 50 * 1024 * 1024      # ClawHub's own bundle limit
# Written by ClawHub into every download; not part of the publisher's skill.
REGISTRY_FILES = {"_meta.json", "skill-card.md"}


class BundleError(ValueError):
    pass


def _safe_name(name: str) -> str:
    if "\\" in name or "\x00" in name:
        raise BundleError(f"unsafe path in bundle: {name!r}")
    norm = posixpath.normpath(name)
    if name.startswith("/") or norm.startswith("../") or norm == ".." or "/../" in f"/{name}/":
        raise BundleError(f"path escapes the skill folder: {name!r}")
    return norm


def read_bundle(data: bytes) -> dict[str, bytes]:
    """Return {relative path: bytes} for every regular file, or raise BundleError."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise BundleError("download is not a valid ZIP archive") from e
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise BundleError(f"bundle has {len(infos)} files (limit {MAX_FILES})")
    files: dict[str, bytes] = {}
    total = 0
    for info in infos:
        name = _safe_name(info.filename)
        mode = info.external_attr >> 16
        if mode and not stat.S_ISREG(mode):
            raise BundleError(f"bundle contains a symlink or special file: {name}")
        if info.flag_bits & 0x1:
            raise BundleError(f"bundle contains an encrypted file: {name}")
        with zf.open(info) as fh:
            content = fh.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise BundleError(f"{name} is larger than {MAX_FILE_BYTES // 1024 // 1024}MB")
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise BundleError("bundle expands to more than 50MB")
        if name in files:
            raise BundleError(f"duplicate path in bundle: {name}")
        files[name] = content
    if not any(n in files for n in SKILL_FILE_NAMES):
        raise BundleError("bundle has no SKILL.md")
    return files


def skill_md_name(files: dict[str, bytes]) -> str:
    return next(n for n in SKILL_FILE_NAMES if n in files)
