"""Injectable environment for the window/grant verbs (fakeable in tests)."""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# A sudo/process runner: (argv, stdin_text) -> (returncode, stdout, stderr).
# A tool that cannot be launched returns (127, "", reason).
Proc = Callable[[list[str], Optional[str]], tuple[int, str, str]]


def real_proc(argv: list[str], stdin: Optional[str] = None) -> tuple[int, str, str]:
    """Run ``argv`` (never through a shell); never raises."""
    try:
        p = subprocess.run(  # nosec B603 - argv list, no shell
            argv, input=stdin, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as err:
        return 127, "", str(err)
    return p.returncode, p.stdout, p.stderr


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "spark" / "window.json"


def _collect_power() -> dict:
    from spark.probe import power

    return power.collect()


def _collect_thermal() -> dict:
    from spark.probe import thermal

    return thermal.collect()


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


@dataclass
class Ctx:
    proc: Proc = real_proc
    state_file: Path = field(default_factory=state_path)
    power: Callable[[], dict] = _collect_power
    thermal: Callable[[], dict] = _collect_thermal
    which: Callable[[str], Optional[str]] = _which
    user: Callable[[], str] = lambda: os.environ.get("SUDO_USER") or getpass.getuser()


def make_ctx() -> Ctx:
    return Ctx()
