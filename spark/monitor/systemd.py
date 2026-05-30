"""systemd ``--user`` supervision for the monitor.

Generates and manages a user-level unit so the watchdog runs always-on with
auto-restart and journald logs — the way persistent services run on the Spark.
The unit text is pure (testable); every ``systemctl``/``loginctl`` call goes
through :mod:`spark.probe._run` (``shutil.which`` — graceful when absent, and no
bandit B607 partial-path).
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path
from typing import Optional

from spark.monitor.config import config_home, default_config_path
from spark.probe._run import run_capture, run_tool

UNIT_NAME = "dgx-spark-monitor.service"


def unit_dir() -> Path:
    return config_home() / "systemd" / "user"


def unit_path() -> Path:
    return unit_dir() / UNIT_NAME


def exec_start(config_path: Optional[str] = None) -> str:
    """ExecStart line: the running interpreter + module entry (PATH-independent)."""
    cfg = config_path or str(default_config_path())
    return f"{sys.executable} -m spark monitor run --config {cfg}"


def unit_text(config_path: Optional[str] = None) -> str:
    return (
        "[Unit]\n"
        "Description=DGX Spark monitor (dgx-spark-cli watchdog)\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start(config_path)}\n"
        "Restart=on-failure\n"
        "RestartSec=10\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def install(config_path: Optional[str] = None) -> Path:
    """Write the unit file and reload the user manager. Returns the unit path."""
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text(config_path), encoding="utf-8")
    run_tool("systemctl", ["--user", "daemon-reload"])
    return path


def enable(*, linger: bool = True) -> tuple[bool, Optional[str]]:
    out = run_tool("systemctl", ["--user", "enable", "--now", UNIT_NAME])
    if linger:
        # Lets the user service keep running after logout / across reboots.
        run_tool("loginctl", ["enable-linger", getpass.getuser()])
    if out is None:
        return False, "systemctl --user enable failed (is the user manager running?)"
    return True, None


def disable() -> tuple[bool, Optional[str]]:
    out = run_tool("systemctl", ["--user", "disable", "--now", UNIT_NAME])
    if out is None:
        return False, "systemctl --user disable failed"
    return True, None


def _query(args: list[str]) -> str:
    result = run_capture("systemctl", args)
    if result is None:
        return "unknown"
    return (result[1] or "").strip() or "unknown"


def status() -> dict:
    """Report unit presence + active/enabled state (via is-active/is-enabled)."""
    return {
        "unit": UNIT_NAME,
        "unit_path": str(unit_path()),
        "installed": unit_path().is_file(),
        "active": _query(["--user", "is-active", UNIT_NAME]),
        "enabled": _query(["--user", "is-enabled", UNIT_NAME]),
    }


def uninstall() -> Path:
    disable()
    path = unit_path()
    if path.is_file():
        path.unlink()
    run_tool("systemctl", ["--user", "daemon-reload"])
    return path
