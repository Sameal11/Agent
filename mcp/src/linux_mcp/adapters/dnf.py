from __future__ import annotations

import shutil

from linux_mcp.adapters.base import MAX_SEARCH_RESULTS, PackageAdapter, PackageInfo
from linux_mcp.utils.executor import run_check, run_readonly


class DnfAdapter(PackageAdapter):
    name = "dnf"

    def is_available(self) -> bool:
        return shutil.which("dnf") is not None

    def search(self, query: str) -> list[PackageInfo]:
        out = run_readonly(["dnf", "search", query], allow_nonzero=True)
        installed = {p.name for p in self.list_installed()}
        results = []
        for line in out.splitlines():
            # Result lines look like:  "python3-requests.noarch : HTTP library"
            if " : " not in line or line.startswith(("=", " ", "Last metadata")):
                continue
            name_arch = line.split(" : ", 1)[0].strip()
            name = name_arch.rsplit(".", 1)[0] if "." in name_arch else name_arch
            if name:
                results.append(PackageInfo(name=name, version="", installed=name in installed))
                if len(results) >= MAX_SEARCH_RESULTS:
                    break
        return results

    def list_installed(self) -> list[PackageInfo]:
        out = run_readonly(["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\n"])
        results = []
        for line in out.splitlines():
            if "\t" in line:
                name, version = line.split("\t", 1)
                results.append(PackageInfo(name=name, version=version, installed=True))
        return results

    def install_command(self, package: str) -> list[str]:
        return ["dnf", "install", "-y", self.validate_package_name(package)]

    def is_installed(self, package: str) -> bool:
        # Was `package.split(".")[0] in out`: a substring test, so "vim" matched "vim-minimal".
        return run_check(["rpm", "-q", package])

    def describe_installed(self) -> list[tuple[str, str]]:
        out = run_readonly(["rpm", "-qa", "--qf", "%{NAME}\t%{SUMMARY}\n"],
                           allow_nonzero=True, timeout=60, max_output=8_000_000)
        return [(l.split("\t", 1)[0], l.split("\t", 1)[1].strip()) for l in out.splitlines() if "\t" in l]

    def exists(self, package: str) -> bool:
        return run_check(["dnf", "-q", "info", self.validate_package_name(package)])

    def owner_of(self, path: str) -> str | None:
        out = run_readonly(["rpm", "-qf", "--", path], allow_nonzero=True).strip()
        return out if out and "not owned" not in out and "No such file" not in out else None

    def providers_of(self, command: str) -> list[str] | None:
        out = run_readonly(["dnf", "-q", "provides", f"*/bin/{command}"], allow_nonzero=True)
        # "nmap-3:7.92-1.fc39.x86_64 : Network exploration tool"
        return sorted({line.split(" : ", 1)[0].rsplit("-", 2)[0] for line in out.splitlines()
                       if " : " in line and not line.startswith(" ")})
