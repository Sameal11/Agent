from __future__ import annotations

import shutil

from linux_mcp.adapters.base import MAX_SEARCH_RESULTS, PackageAdapter, PackageInfo
from linux_mcp.utils.executor import run_readonly


class AptAdapter(PackageAdapter):
    name = "apt"

    def is_available(self) -> bool:
        return shutil.which("apt-get") is not None

    def search(self, query: str) -> list[PackageInfo]:
        out = run_readonly(["apt-cache", "search", query])
        installed = {p.name for p in self.list_installed()}  # one call, not one per result
        results = []
        for line in out.splitlines():
            if " - " in line:
                pkg_name = line.split(" - ", 1)[0].strip()
                results.append(PackageInfo(name=pkg_name, version="", installed=pkg_name in installed))
                if len(results) >= MAX_SEARCH_RESULTS:
                    break
        return results

    def list_installed(self) -> list[PackageInfo]:
        out = run_readonly(["dpkg-query", "-W", "-f=${Package}\t${Version}\n"])
        results = []
        for line in out.splitlines():
            if "\t" in line:
                name, version = line.split("\t", 1)
                results.append(PackageInfo(name=name, version=version, installed=True))
        return results

    def install_command(self, package: str) -> list[str]:
        return ["apt-get", "install", "-y", self.validate_package_name(package)]

    def is_installed(self, package: str) -> bool:
        out = run_readonly(["dpkg-query", "-W", "-f=${Status}", package], allow_nonzero=True)
        return "install ok installed" in out
