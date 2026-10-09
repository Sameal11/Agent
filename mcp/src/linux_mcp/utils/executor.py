"""Single choke point for every subprocess call in this codebase.

Nothing outside this module should call subprocess directly — that keeps
timeouts, output limits, and audit logging consistent no matter which
tool or adapter is running a command.

Design notes (each one fixes a real failure seen with the previous version):

* ``stdin=DEVNULL``: the MCP server talks to its client over its own stdin/stdout.
  A child that inherited stdin (``cat``, ``read``, ``apt-get`` prompting...) would
  swallow protocol bytes and hang the session.
* Output goes to a temp *file*, not a pipe. With a pipe, ``bash -c "firefox &"``
  never returned: the backgrounded GUI app kept the pipe open and we waited on it
  until the timeout. With a file we only wait for the process we started.
* Children run in their own session so a timeout can kill the whole process group,
  not just the top-level ``bash``.
* Every failure mode (missing binary, bad cwd, timeout, runaway output) becomes a
  ``CommandError`` with a readable message and an audit entry — never a raw
  FileNotFoundError that surfaces as an opaque "Unexpected error".
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time

from linux_mcp.config import settings
from linux_mcp.security.audit import log_command

_MAX_CAPTURE_BYTES = 8 * 1024 * 1024  # kill the command if it writes more than this
_POLL_SECONDS = 0.1


class CommandError(RuntimeError):
    pass


def _tail(text: str, limit: int | None = None) -> str:
    limit = limit or settings.max_output_chars
    return text if len(text) <= limit else "…[truncated]\n" + text[-limit:]


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait()


def _fail(argv: list[str], readonly: bool, message: str) -> CommandError:
    log_command(argv, exit_code=-1, readonly=readonly, note=message)
    return CommandError(message)

def _read_tail(out, limit: int | None = None) -> str:
    limit = limit or settings.max_output_chars
    size = os.fstat(out.fileno()).st_size
    out.seek(max(0, size - limit * 4))
    return out.read().decode("utf-8", errors="replace")


def _run(argv: list[str], cwd: str | None, timeout: int, readonly: bool,
         detach_after: float | None = None, env: dict | None = None,
         max_output: int | None = None) -> tuple[int | None, str, int]:
    """Run argv, return (exit_code, combined stdout+stderr tail, pid).

    detach_after: if the process is still running after this many seconds, stop waiting and
    leave it running (exit_code is then None). For GUI apps, which don't return until closed.
    Without it a long-running command is killed at `timeout`."""
    if not argv:
        raise _fail(argv, readonly, "empty command")
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                start_new_session=True, env=env,
            )
        except OSError as e:  # missing binary, bad cwd, permission denied...
            raise _fail(argv, readonly, f"cannot run {argv[0]!r}: {e}") from e

        start = time.monotonic()
        deadline = start + timeout
        while True:
            try:
                proc.wait(timeout=_POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                if os.fstat(out.fileno()).st_size > _MAX_CAPTURE_BYTES:
                    _kill_group(proc)
                    raise _fail(argv, readonly, f"killed: output exceeded {_MAX_CAPTURE_BYTES} bytes") from None
                now = time.monotonic()
                if detach_after is not None and now - start >= detach_after:
                    log_command(argv, exit_code=0, readonly=readonly,
                                note=f"still running after {detach_after:g}s; left running (pid {proc.pid})")
                    return None, _tail(_read_tail(out, max_output), max_output), proc.pid
                if now >= deadline:
                    _kill_group(proc)
                    raise _fail(
                        argv, readonly,
                        f"timed out after {timeout}s and was killed. If this starts a GUI app or a "
                        "server, launch it detached instead (e.g. `nohup cmd >/dev/null 2>&1 &`).",
                    ) from None

        text = _read_tail(out, max_output)

    log_command(argv, exit_code=proc.returncode, readonly=readonly)
    return proc.returncode, _tail(text, max_output), proc.pid


def run_readonly(argv: list[str], cwd: str | None = None, timeout: int = 30,
                 allow_nonzero: bool = False, env: dict | None = None,
                 max_output: int | None = None) -> str:
    """Run a command that only reads system state (no confirmation needed,
    but still audited). Raises CommandError on failure unless allow_nonzero.
    max_output overrides the default output cap (for large read-only dumps)."""
    rc, text, _ = _run(argv, cwd, timeout, readonly=True, env=env, max_output=max_output)
    if rc != 0 and not allow_nonzero:
        raise CommandError(f"{' '.join(argv)} failed (exit {rc}): {text.strip()}")
    return text


def run_check(argv: list[str], timeout: int = 30) -> bool:
    """True iff the command exits 0. Output is discarded."""
    rc, _, _ = _run(argv, None, timeout, readonly=True)
    return rc == 0


def run_mutating(argv: list[str], cwd: str | None = None, timeout: int = 120,
                 detach_after: float | None = None) -> str:
    """Run a command that changes system state. A non-zero exit is reported in the text.
    With detach_after, a command still running after that many seconds is left running."""
    rc, text, pid = _run(argv, cwd, timeout, readonly=False, detach_after=detach_after)
    if rc is None:
        return (f"{text}\n[still running after {detach_after:g}s - left running in the background "
                f"(pid {pid}); it was NOT stopped. Treat this as started successfully.]")
    return text if rc == 0 else f"{text}\n[exit code: {rc}]"

def spawn_detached(argv: list[str], cwd: str | None = None) -> int:
    """Start a long-lived process (e.g. a browser) and return immediately with its pid."""
    if not argv:
        raise _fail(argv, False, "empty command")
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as e:
        raise _fail(argv, False, f"cannot run {argv[0]!r}: {e}") from e
    log_command(argv, exit_code=0, readonly=False, note=f"detached pid={proc.pid}")
    return proc.pid
