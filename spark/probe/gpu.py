"""``spark gpu`` — Blackwell GB10 snapshot via nvidia-smi.

The Spark's GB10 shares one memory pool with the Grace CPU — there is no
discrete VRAM — so ``nvidia-smi`` reports ``memory.total``/``memory.used`` as
``[N/A]``. This collector surfaces utilization, temperature, power and clocks
honestly and points at ``spark memory`` for the shared pool instead of
inventing a VRAM number. Reads are graceful: no ``nvidia-smi`` -> unavailable.
"""

from __future__ import annotations

from typing import Optional

from spark.probe._report import human_bytes, report, unavailable
from spark.probe._run import Runner, default_runner

_HOT_C = 80.0

_QUERY_FIELDS = [
    "name",
    "utilization.gpu",
    "utilization.memory",
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "memory.total",
    "memory.used",
    "clocks.sm",
    "fan.speed",
]


def _na(value: str) -> Optional[str]:
    """Return a cleaned value, or ``None`` for nvidia-smi's ``[N/A]`` tokens."""
    cleaned = value.strip()
    if not cleaned or cleaned.upper().startswith("[N/A") or cleaned.upper() == "N/A":
        return None
    return cleaned


def _fmt(value: Optional[str], unit: str = "") -> str:
    return f"{value}{unit}" if value is not None else "n/a"


def _compute_apps(run: Runner) -> list[dict]:
    out = run(
        "nvidia-smi",
        ["--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
    )
    apps: list[dict] = []
    if not out:
        return apps
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        apps.append(
            {
                "pid": parts[0] if parts else "?",
                "name": parts[1] if len(parts) > 1 else "?",
                "used_mib": _na(parts[2]) if len(parts) > 2 else None,
            }
        )
    return apps


def collect(runner: Optional[Runner] = None) -> dict:
    """Return a GPU report using ``runner`` (injectable; defaults to nvidia-smi)."""
    run = runner or default_runner
    out = run(
        "nvidia-smi",
        ["--query-gpu=" + ",".join(_QUERY_FIELDS), "--format=csv,noheader,nounits"],
    )
    if out is None:
        return unavailable("gpu", "nvidia-smi", "install NVIDIA drivers / run on the DGX Spark")

    line = next((row for row in out.splitlines() if row.strip()), "")
    fields = [f.strip() for f in line.split(",")]
    fields += [""] * (len(_QUERY_FIELDS) - len(fields))
    vals = {key: _na(fields[i]) for i, key in enumerate(_QUERY_FIELDS)}

    temp = vals["temperature.gpu"]
    warnings: list[str] = []
    if temp is not None:
        try:
            if float(temp) >= _HOT_C:
                warnings.append(f"GPU at {temp} C")
        except ValueError:
            pass

    # Compute apps DO report per-process memory even though the aggregate
    # memory.total/used is N/A on unified architecture. Summing them is the
    # closest honest answer to "GPU memory used" on the Spark.
    apps = _compute_apps(run)
    gpu_mem_mib = sum(int(a["used_mib"]) for a in apps if a["used_mib"] and a["used_mib"].isdigit())

    if vals["memory.total"] is None:
        if apps and gpu_mem_mib:
            attributed = human_bytes(gpu_mem_mib * 1024 * 1024)
            mem_line = (
                f"memory: unified (no discrete VRAM); ~{attributed} attributed to "
                "GPU processes (see 'spark memory')"
            )
        else:
            mem_line = "memory: unified with system RAM (no discrete VRAM); see 'spark memory'"
    else:
        mem_line = f"memory: {_fmt(vals['memory.used'])} / {vals['memory.total']} MiB"

    sections = [
        {
            "title": vals["name"] or "NVIDIA GPU",
            "items": [
                f"utilization: {_fmt(vals['utilization.gpu'], '%')}"
                f" (mem ctrl {_fmt(vals['utilization.memory'], '%')})",
                f"temperature: {_fmt(temp, ' C')}",
                f"power: {_fmt(vals['power.draw'], ' W')} / {_fmt(vals['power.limit'], ' W')}",
                f"sm clock: {_fmt(vals['clocks.sm'], ' MHz')}",
                f"fan: {_fmt(vals['fan.speed'], '%')}",
                mem_line,
            ],
        }
    ]

    if apps:
        app_items = [f"{a['pid']:>8} {a['name']} ({_fmt(a['used_mib'], ' MiB')})" for a in apps]
    else:
        app_items = ["none"]
    sections.append({"title": "GPU compute processes", "items": app_items})

    data = {"gpu": vals, "compute_apps": apps, "gpu_attributed_mib": gpu_mem_mib}
    return report("gpu", source="nvidia-smi", sections=sections, warnings=warnings, data=data)
