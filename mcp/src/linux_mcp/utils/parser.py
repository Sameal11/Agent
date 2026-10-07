"""Shared text-parsing helpers for adapter output (key:value blocks, etc)."""
from __future__ import annotations


def parse_kv_lines(text: str, sep: str = ":") -> dict:
    out = {}
    for line in text.splitlines():
        if sep in line:
            k, v = line.split(sep, 1)
            out[k.strip()] = v.strip()
    return out
