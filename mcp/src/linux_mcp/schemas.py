"""Central place for tool argument/result schemas.

Every tool module defines its Pydantic models here (or imports from here),
so `server.py` can register tools generically instead of hand-rolling
JSON schema per tool.

Optional fields keep their ``anyOf [type, null]`` JSON schema on purpose: small models
(e.g. Qwen 7B) often send an explicit ``null`` for omitted optionals, and a plain
``type: string`` would make the server reject those calls.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from linux_mcp.validation import (
    HTTP_URL_PATTERN,
    PACKAGE_NAME_PATTERN,
    PROGRAM_NAME_PATTERN,
    SKILL_NAME_PATTERN,
    SEARCH_QUERY_PATTERN,
    UNIT_NAME_PATTERN,
)


class ToolResult(BaseModel):
    """Uniform envelope every tool handler returns."""
    ok: bool
    data: Any = None
    error: Optional[str] = None


# ---- shell ----
class ShellArgs(BaseModel):
    command: str = Field(..., description="Bash command to execute")
    cwd: Optional[str] = Field(
        None,
        description="Working directory. Relative paths are relative to the workspace "
                    "directory (the default); absolute paths are used as-is.",
    )
    timeout_seconds: Optional[int] = Field(
        None, ge=1, le=600, description="Kill the command after this many seconds (default 60)."
    )


# ---- filesystem ----
class ReadFileArgs(BaseModel):
    path: str = Field(..., description="Path relative to the workspace directory")
    max_bytes: int = Field(8000, ge=1, le=200_000)


class WriteFileArgs(BaseModel):
    path: str = Field(..., description="Path relative to the workspace directory")
    content: str = Field(..., max_length=1_000_000)
    append: bool = False


# ---- process ----
class ListProcessesArgs(BaseModel):
    name_filter: Optional[str] = None
    limit: int = Field(100, ge=1, le=1000, description="Max rows, highest memory use first")


class KillProcessArgs(BaseModel):
    # ge=2: pid 1 is init; 0 and negative values address whole process groups
    # (`kill -1` signals every process the user owns).
    pid: int = Field(..., ge=2)
    signal: Literal["SIGTERM", "SIGKILL", "SIGINT", "SIGHUP"] = "SIGTERM"


# ---- packages ----
class PackageQueryArgs(BaseModel):
    query: str = Field(..., pattern=SEARCH_QUERY_PATTERN, description="Search text (must not start with '-')")


class PackageInstallArgs(BaseModel):
    package: str = Field(..., pattern=PACKAGE_NAME_PATTERN, description="Exact package name")


# ---- services ----
class ServiceControlArgs(BaseModel):
    unit: str = Field(..., pattern=UNIT_NAME_PATTERN, description="systemd unit, e.g. nginx or nginx.service")
    action: Literal["start", "stop", "restart", "status", "enable", "disable"]

# ---- web search ----
class WebSearchArgs(BaseModel):
    query: str = Field(..., min_length=2, max_length=200, description="What to search for")
    max_results: int = Field(5, ge=1, le=10)
# ---- weather ----
class WeatherArgs(BaseModel):
    location: str = Field(..., min_length=2, max_length=100,
                          description="City name, optionally with region/country: 'Chennai' or 'Chennai, India'")
    days: int = Field(3, ge=1, le=7, description="Days of forecast, starting today")


# ---- tool knowledge base ----
class FindToolArgs(BaseModel):
    task: str = Field(..., min_length=2, max_length=200,
                      description="What you need a tool to do, in plain words (e.g. 'scan open ports')")
    limit: int = Field(6, ge=1, le=15)


# ---- ClawHub skills ----
class ClawHubSearchArgs(BaseModel):
    query: str = Field(..., min_length=2, max_length=200, description="What the skill should do")
    limit: int = Field(8, ge=1, le=20)


class ClawHubRefArgs(BaseModel):
    ref: str = Field(..., max_length=130, description="owner/slug exactly as clawhub_search returned it")


class ClawHubInstallArgs(ClawHubRefArgs):
    version: Optional[str] = Field(None, max_length=64,
                                   description="Exact version from clawhub_inspect (pins what was checked)")


class SkillNameArgs(BaseModel):
    name: str = Field(..., pattern=SKILL_NAME_PATTERN, description="Installed skill name (its folder)")


class UseSkillArgs(SkillNameArgs):
    file: Optional[str] = Field(None, max_length=200,
                                description="A supporting file inside the skill folder, instead of SKILL.md")


# ---- browser ----
class OpenUrlArgs(BaseModel):
    url: str = Field(..., max_length=2048, pattern=HTTP_URL_PATTERN, description="http(s) URL to open")


# ---- research (verify before acting) ----
class VerifyCommandArgs(BaseModel):
    command: str = Field(..., max_length=4000, description="The exact bash command you intend to run")


class ToolDocsArgs(BaseModel):
    program: str = Field(..., pattern=PROGRAM_NAME_PATTERN, description="Installed program, e.g. nmap")
    query: Optional[str] = Field(
        None, max_length=100,
        description="Only return documentation lines containing this text (e.g. an option like -sV)",
    )
    max_chars: int = Field(6000, ge=500, le=20_000)


class CheckUrlArgs(BaseModel):
    url: str = Field(..., max_length=2048, pattern=HTTP_URL_PATTERN, description="http(s) URL to check")


class FetchPageArgs(BaseModel):
    url: str = Field(..., max_length=2048, pattern=HTTP_URL_PATTERN, description="http(s) URL to read")
    find: Optional[str] = Field(
        None, max_length=100, description="Only return passages containing this text (case-insensitive)"
    )
    include_links: bool = Field(False, description="Also return the links found on the page")
    max_chars: int = Field(6000, ge=500, le=20_000)
