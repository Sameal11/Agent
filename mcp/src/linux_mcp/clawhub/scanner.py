"""Local static checks on a skill bundle, independent of ClawHub's own scanning.

ClawHub's /verify verdict is the primary gate, but Unit 42 reported malicious skills that
stayed on ClawHub for months after VirusTotal/ClawScan screening was added. These rules
cover the patterns documented in those campaigns (ClawHavoc et al.):
  - a "Prerequisites" step that pipes a remote script into a shell, or decodes a blob and
    runs it;
  - a password-protected archive to download and run (defeats scanners);
  - native executables shipped inside a text skill;
  - reading credential stores (SSH keys, cloud credentials, browser logins, wallets);
  - instructions aimed at the agent itself (ignore prior instructions, hide actions from
    the user) - a SKILL.md is pasted into the model's context, so this is prompt injection.

"block" findings stop the install. "warn" findings are shown to the user at the approval
prompt. Matching is per line so findings can point at file:line.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from linux_mcp.clawhub.bundle import REGISTRY_FILES


@dataclass
class Finding:
    severity: str        # "block" | "warn"
    code: str
    file: str
    line: int
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


_I = re.IGNORECASE
LINE_RULES: list[tuple[str, str, re.Pattern, str]] = [
    ("block", "remote_script_to_shell",
     re.compile(r"\b(curl|wget|fetch)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b", _I),
     "downloads a script and pipes it straight into a shell"),
    ("block", "decode_and_execute",
     re.compile(r"base64\s+(-d|--decode)\b[^\n]*\|\s*(sudo\s+)?(ba|z)?sh\b|\beval\s*\(\s*(atob|base64)", _I),
     "decodes hidden content and executes it"),
    ("block", "password_protected_payload",
     re.compile(r"\b(zip|rar|7z|archive)\b.{0,60}\bpassword\b|\bpassword\b.{0,60}\.(zip|rar|7z)\b", _I),
     "refers to a password-protected archive (a common way to hide payloads from scanners)"),
    ("block", "credential_store_access",
     re.compile(r"~/\.ssh/|\.ssh/id_|\.aws/credentials|\.config/gcloud|\.kube/config|"
                r"Login Data|\.mozilla/firefox/[^\s]*logins|wallet\.dat|\.electrum|"
                r"Library/Application Support/[^\s]*(Exodus|MetaMask)", _I),
     "reads a credential or wallet store"),
    ("block", "agent_instruction_override",
     re.compile(r"\bignore (all |any )?(previous|prior|above|earlier) (instructions|rules)|"
                r"\b(do not|don't|never) (tell|inform|show|mention (this )?to) the user\b|"
                r"\bwithout (asking|telling|notifying) the user\b|\bdisregard (your|the) (system|safety)", _I),
     "tries to override the agent's instructions or hide actions from the user"),
    ("warn", "paste_into_terminal",
     re.compile(r"\b(copy|paste)\b.{0,40}\b(terminal|shell|command line)\b", _I),
     "asks for a command to be pasted into a terminal"),
    ("warn", "make_executable_and_run",
     re.compile(r"chmod\s+\+x\s+\S+\s*(&&|;)\s*\./", _I),
     "marks a downloaded file executable and runs it"),
    ("warn", "raw_ip_url",
     re.compile(r"https?://\d{1,3}(\.\d{1,3}){3}\b"),
     "contacts a raw IP address instead of a domain"),
    ("warn", "persistence",
     re.compile(r"\bcrontab\b|/etc/systemd/system/|\.bashrc|\.zshrc|\.profile\b|autostart", _I),
     "modifies startup files or schedules itself"),
    ("warn", "privilege_escalation",
     re.compile(r"\bsudo\b|\bchmod\s+[0-7]*[4-7][0-7]{3}\b|setuid", _I),
     "asks for root privileges"),
]

_LONG_BLOB = re.compile(r"[A-Za-z0-9+/=]{400,}")
_EXEC_MAGIC = {b"\x7fELF": "Linux executable", b"MZ": "Windows executable",
               b"\xcf\xfa\xed\xfe": "macOS executable", b"\xca\xfe\xba\xbe": "macOS executable"}


def scan_bundle(files: dict[str, bytes]) -> list[Finding]:
    findings: list[Finding] = []
    for path, content in sorted(files.items()):
        if path in REGISTRY_FILES:
            continue
        kind = next((label for magic, label in _EXEC_MAGIC.items() if content.startswith(magic)), None)
        if kind:
            findings.append(Finding("block", "bundled_executable", path, 0, f"contains a {kind}"))
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding("warn", "binary_file", path, 0, "non-text file (not scanned)"))
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for severity, code, pattern, message in LINE_RULES:
                if pattern.search(line):
                    findings.append(Finding(severity, code, path, lineno, message))
            if _LONG_BLOB.search(line):
                findings.append(Finding("warn", "encoded_blob", path, lineno, "long encoded blob"))
    return findings


def undeclared_env(files: dict[str, bytes], declared: set[str]) -> list[str]:
    """Credential-looking env vars the code reads but the frontmatter does not declare -
    the same 'metadata mismatch' ClawHub's own analysis flags."""
    pattern = re.compile(r"\$\{?([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))\b|"
                         r"(?:os\.environ|getenv|process\.env)[\[.(\"' ]+([A-Z][A-Z0-9_]*)")
    used: set[str] = set()
    for path, content in files.items():
        if path in REGISTRY_FILES:
            continue
        for m in pattern.finditer(content.decode("utf-8", errors="ignore")):
            used.add(m.group(1) or m.group(2))
    return sorted(used - declared)
