"""``spark power`` — GB10 power draw, SM clocks and P-state via nvidia-smi.

The GB10 has no ``nvpmodel`` and no per-rail sensors (unlike Jetson), so this
reports only what ``nvidia-smi`` actually returns. ``power.limit`` is ``[N/A]``
on the GB10; it is reported as unavailable (``None`` plus a warning), never
guessed. Reads are graceful: no ``nvidia-smi`` -> unavailable.
"""

from __future__ import annotations

from typing import Optional

from spark.probe._report import report, unavailable
from spark.probe._run import Runner, default_runner

_FIELDS = ["power.draw", "power.limit", "clocks.sm", "clocks.max.sm", "pstate"]


def _clean(value: str) -> Optional[str]:
    cleaned = value.strip()
    if not cleaned or cleaned.upper().startswith("[N/A") or cleaned.upper() == "N/A":
        return None
    return cleaned


def _num(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _mhz(value: Optional[str]) -> Optional[int]:
    num = _num(value)
    return int(num) if num is not None else None


def _fmt(value: object, unit: str = "") -> str:
    return f"{value}{unit}" if value is not None else "n/a"


def collect(runner: Optional[Runner] = None) -> dict:
    """Return a power report using ``runner`` (injectable; defaults to nvidia-smi)."""
    run = runner or default_runner
    out = run(
        "nvidia-smi",
        ["--query-gpu=" + ",".join(_FIELDS), "--format=csv,noheader,nounits"],
    )
    if out is None:
        return unavailable("power", "nvidia-smi", "install NVIDIA drivers / run on the DGX Spark")

    line = next((row for row in out.splitlines() if row.strip()), "")
    fields = [f.strip() for f in line.split(",")]
    fields += [""] * (len(_FIELDS) - len(fields))
    raw = {key: _clean(fields[i]) for i, key in enumerate(_FIELDS)}

    data = {
        "power_draw_w": _num(raw["power.draw"]),
        "power_limit_w": _num(raw["power.limit"]),
        "clocks_sm_mhz": _mhz(raw["clocks.sm"]),
        "clocks_max_sm_mhz": _mhz(raw["clocks.max.sm"]),
        "pstate": raw["pstate"],
    }
    warnings = []
    if data["power_limit_w"] is None:
        warnings.append("power.limit is not reported by nvidia-smi on this GPU (N/A)")

    sections = [
        {
            "title": "GPU power",
            "items": [
                f"power draw: {_fmt(data['power_draw_w'], ' W')}",
                f"power limit: {_fmt(data['power_limit_w'], ' W')}",
                f"sm clock: {_fmt(data['clocks_sm_mhz'], ' MHz')}"
                f" (max {_fmt(data['clocks_max_sm_mhz'], ' MHz')})",
                f"pstate: {_fmt(data['pstate'])}",
            ],
        }
    ]
    return report("power", source="nvidia-smi", sections=sections, warnings=warnings, data=data)
