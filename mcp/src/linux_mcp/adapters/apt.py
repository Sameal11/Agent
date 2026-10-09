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

    def describe_installed(self) -> list[tuple[str, str]]:
        # ${Description} is the full synopsis; keep its first line (the short description).
        out = run_readonly(["dpkg-query", "-W", "-f=${Package}\t${Description}\n"],
                           allow_nonzero=True, timeout=60, max_output=8_000_000)
        result = []
        for line in out.splitlines():
            if "\t" in line:
                name, desc = line.split("\t", 1)
                result.append((name, desc.split("\n", 1)[0].strip()))
        return result

    def exists(self, package: str) -> bool:
        # Exits 100 ("E: No packages found") for unknown and purely virtual packages.
        return run_check(["apt-cache", "show", "--no-all-versions", self.validate_package_name(package)])

    def owner_of(self, path: str) -> str | None:
        # "coreutils: /usr/bin/ls". /bin and /usr/bin are merged on modern Debian/Ubuntu/Kali,
        # but dpkg records the path that was shipped, so try both.
        candidates = [path]
        if path.startswith("/usr/bin/"):
            candidates.append("/bin/" + path[len("/usr/bin/"):])
        for p in candidates:
            out = run_readonly(["dpkg-query", "-S", "--", p], allow_nonzero=True)
            if ": " in out and "no path found" not in out:
                return out.split(":", 1)[0].strip()
        return None

    def providers_of(self, command: str) -> list[str] | None:
        if shutil.which("apt-file") is None:
            return None
        out = run_readonly(["apt-file", "search", "--regexp", f"/s?bin/{command}$"], allow_nonzero=True)
        return sorted({line.split(":", 1)[0] for line in out.splitlines() if ": " in line})
