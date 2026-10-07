from __future__ import annotations

import shutil

from linux_mcp.adapters.base import MAX_SEARCH_RESULTS, PackageAdapter, PackageInfo
from linux_mcp.utils.executor import run_check, run_readonly


class PacmanAdapter(PackageAdapter):
    name = "pacman"

    def is_available(self) -> bool:
        return shutil.which("pacman") is not None

    def search(self, query: str) -> list[PackageInfo]:
        # pacman -Ss exits 1 when nothing matches; that is "no results", not an error.
        out = run_readonly(["pacman", "-Ss", query], allow_nonzero=True)
        results = []
        for line in out.splitlines():
            # Result header:  repo/name version [group] [installed]
            # (description lines are indented)
            if line.startswith((" ", "\t")) or "/" not in line:
                continue
            head = line.split()
            name = head[0].split("/", 1)[1]
            version = head[1] if len(head) > 1 else ""
            results.append(PackageInfo(name=name, version=version, installed="[installed" in line))
            if len(results) >= MAX_SEARCH_RESULTS:
                break
        return results

    def list_installed(self) -> list[PackageInfo]:
        out = run_readonly(["pacman", "-Q"])
        results = []
        for line in out.splitlines():
            if " " in line:
                name, version = line.split(" ", 1)
                results.append(PackageInfo(name=name, version=version, installed=True))
        return results

    def install_command(self, package: str) -> list[str]:
        return ["pacman", "-S", "--noconfirm", "--needed", self.validate_package_name(package)]

    def is_installed(self, package: str) -> bool:
        # Exit status is the answer. The old version returned bool(output), but for a
        # missing package pacman prints "error: package 'x' was not found" — so every
        # package looked installed.
        return run_check(["pacman", "-Q", package])
