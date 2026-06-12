"""I/O contention snapshot — iowait % and blocked-process count.

Capacity probes (``memory``, ``disk``) answer *how full* the box is; this one
answers *how much it is waiting*. On the unified-memory DGX Spark, swap thrashing
shows up as sustained ``iowait`` and a wall of D-state (blocked) processes long
before swap is "full" — the early symptom of a janky, starved desktop. Both
signals come from ``/proc/stat``:

* ``iowait`` is the 5th field of the aggregate ``cpu`` line, cumulative jiffies
  since boot — so a percentage needs *two* samples and a delta over the gap.
* ``procs_blocked`` is an instantaneous count, read straight from the 2nd sample.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from spark.probe import _run
from spark.probe._report import pct, report, unavailable

# Warn thresholds (descriptive yellow flags — the monitor's alert thresholds in
# spark.monitor.config sit higher).
_IOWAIT_WARN_PCT = 15.0
_BLOCKED_WARN = 4

_STAT_PATH = "/proc/stat"
_SAMPLE_INTERVAL = 0.5  # seconds between the two /proc/stat reads for the delta

# /proc/stat aggregate cpu line: cpu user nice system idle iowait irq ...
_IOWAIT_FIELD = 5  # 1-based position of iowait among the numbers after "cpu"

Sampler = Callable[[], Optional[str]]


def _read_stat() -> Optional[str]:
    return _run.read_text(_STAT_PATH)


def _cpu_iowait_and_total(text: str) -> Optional[tuple[int, int]]:
    """Return ``(iowait_jiffies, total_jiffies)`` from the aggregate ``cpu`` line."""
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            try:
                nums = [int(p) for p in parts[1:]]
            except ValueError:
                return None
            if len(nums) < _IOWAIT_FIELD:
                return None
            return nums[_IOWAIT_FIELD - 1], sum(nums)
    return None


def _blocked_procs(text: str) -> Optional[int]:
    """Return the instantaneous ``procs_blocked`` count, or ``None`` if absent."""
    for line in text.splitlines():
        if line.startswith("procs_blocked"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return None
    return None


def collect(
    sampler: Sampler = _read_stat,
    *,
    sleep: Callable[[float], None] = time.sleep,
    interval: float = _SAMPLE_INTERVAL,
) -> dict:
    """Return an I/O-contention report. ``sampler``/``sleep`` are injectable for tests."""
    first = sampler()
    if first is None:
        return unavailable("contention", _STAT_PATH, "read /proc/stat on a Linux host")
    sleep(interval)
    second = sampler()
    if second is None:
        second = first  # one good read still yields blocked_procs; iowait delta is 0

    blocked = _blocked_procs(second)

    iowait_pct: Optional[float] = None
    s1 = _cpu_iowait_and_total(first)
    s2 = _cpu_iowait_and_total(second)
    if s1 is not None and s2 is not None:
        d_iowait = max(s2[0] - s1[0], 0)
        d_total = s2[1] - s1[1]
        iowait_pct = pct(d_iowait, d_total) if d_total > 0 else 0.0

    warnings: list[str] = []
    if iowait_pct is not None and iowait_pct >= _IOWAIT_WARN_PCT:
        warnings.append(f"iowait {iowait_pct:.0f}% — CPUs waiting on I/O (swap thrash / slow disk)")
    if blocked is not None and blocked >= _BLOCKED_WARN:
        warnings.append(f"{blocked} processes blocked on I/O (D-state)")

    iowait_item = f"{iowait_pct:.0f}%" if iowait_pct is not None else "n/a"
    blocked_item = str(blocked) if blocked is not None else "n/a"
    sections = [
        {
            "title": "I/O contention",
            "items": [
                f"iowait: {iowait_item} (over {interval:g}s)",
                f"blocked processes: {blocked_item}",
            ],
        },
    ]
    data = {"iowait_pct": iowait_pct, "blocked_procs": blocked}
    return report("contention", source=_STAT_PATH, sections=sections, warnings=warnings, data=data)
