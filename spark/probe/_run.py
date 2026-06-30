"""Subprocess + file-read helpers for host probes (graceful, zero deps).

External tools are resolved with :func:`shutil.which` so a missing tool degrades
to ``None`` (reported as ``available: false``) instead of raising — and so we
always invoke an *absolute* path, which keeps bandit's B607 (partial executable
path) quiet. ``/proc`` and ``/sys`` reads are wrapped so a missing or unreadable
node yields ``None`` rather than an :class:`OSError`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional, Sequence

# A runner takes (tool_name, args) and returns stdout, or None when the tool is
# absent / failed. Injectable so tests can supply canned output without a real
# nvidia-smi / docker / ip on the box.
Runner = Callable[[str, Sequence[str]], Optional[str]]

DEFAULT_TIMEOUT = 5.0


def run_tool(
    name: str,
    args: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[str]:
    """Run ``name args`` and return stdout, or ``None`` if absent/failed/timed out.

    The executable is resolved via :func:`shutil.which`, so a tool that is not
    installed returns ``None`` and we only ever exec an absolute path.
    """
    exe = shutil.which(name)
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def default_runner(name: str, args: Sequence[str]) -> Optional[str]:
    """The production :data:`Runner` — a thin wrapper over :func:`run_tool`."""
    return run_tool(name, args)


def run_capture(
    name: str,
    args: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[tuple[int, str]]:
    """Like :func:`run_tool` but return ``(returncode, stdout)`` even on non-zero exit.

    Needed for tools that convey state through the exit code (e.g.
    ``systemctl --user is-active`` exits 3 for an inactive unit while still
    printing ``inactive``). Returns ``None`` only when the tool is absent or
    could not be launched.
    """
    exe = shutil.which(name)
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # Surface stderr when the tool wrote nothing to stdout — e.g. a failing
    # swapoff/mkswap prints its reason to stderr, and callers that convey the
    # outcome through the returned text would otherwise lose it. Tools that
    # report via stdout (e.g. `systemctl is-active` -> "inactive") are unaffected.
    output = proc.stdout
    if not output and proc.stderr:
        output = proc.stderr
    return proc.returncode, output


def read_text(path: str | Path) -> Optional[str]:
    """Read a ``/proc`` or ``/sys`` node, returning ``None`` on any OSError."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_first_line(path: str | Path) -> Optional[str]:
    """Return the first line of ``path`` (stripped), or ``None`` if unreadable."""
    text = read_text(path)
    if text is None:
        return None
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""
