"""Basic host facts for the ``status`` header.

Hostname, hardware model, OS, kernel/arch, CPU summary, uptime and load — read
from the stdlib + ``/proc`` + ``/etc/os-release``, with ``lscpu`` (optional) for
CPU model names. Not a verb of its own; folded into ``spark status``.
"""

from __future__ import annotations

import os
import socket
from typing import Optional

from spark.probe import _run
from spark.probe._run import Runner, default_runner

_MODEL_PATHS = (
    "/proc/device-tree/model",
    "/sys/devices/virtual/dmi/id/product_name",
    "/sys/class/dmi/id/product_name",
)


def _os_pretty(path: str = "/etc/os-release") -> Optional[str]:
    text = _run.read_text(path)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.partition("=")[2].strip().strip('"')
    return None


def _model() -> Optional[str]:
    for path in _MODEL_PATHS:
        value = _run.read_first_line(path)
        if value:
            cleaned = value.replace("\x00", "").strip()
            if cleaned:
                return cleaned
    return None


def _uptime_seconds(path: str = "/proc/uptime") -> Optional[float]:
    line = _run.read_first_line(path)
    if not line:
        return None
    try:
        return float(line.split()[0])
    except (IndexError, ValueError):
        return None


def _loadavg(path: str = "/proc/loadavg") -> Optional[str]:
    line = _run.read_first_line(path)
    if not line:
        return None
    parts = line.split()
    return f"{parts[0]} {parts[1]} {parts[2]}" if len(parts) >= 3 else None


def _cpu_models(run: Runner) -> list[str]:
    out = run("lscpu", [])
    if not out:
        return []
    models: list[str] = []
    for line in out.splitlines():
        if line.strip().startswith("Model name:"):
            name = line.partition(":")[2].strip()
            if name and name not in models:
                models.append(name)
    return models


def facts(runner: Optional[Runner] = None) -> dict:
    """Return host facts (best-effort; any field may be ``None``)."""
    run = runner or default_runner
    uname = os.uname()
    return {
        "hostname": socket.gethostname(),
        "model": _model(),
        "os": _os_pretty(),
        "kernel": uname.release,
        "arch": uname.machine,
        "cpu_count": os.cpu_count(),
        "cpu_models": _cpu_models(run),
        "uptime_seconds": _uptime_seconds(),
        "loadavg": _loadavg(),
    }
