"""Threshold rules: turn a probe snapshot + thresholds into firing alerts.

Pure and AI-free: every alert is a deterministic numeric/boolean comparison, so
:func:`evaluate` is fully testable with fixture snapshots. Each :class:`Alert`
carries a stable ``key`` used by :mod:`spark.monitor.state` for edge-triggering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Alert:
    key: str  # stable id for edge-triggering, e.g. "memory_used_pct" / "disk:/"
    severity: str  # "critical" | "warning"
    message: str
    value: Optional[float] = None
    threshold: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "severity": self.severity,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
        }


def _limit(thresholds: dict, key: str) -> Optional[float]:
    raw = thresholds.get(key)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _memory(snap: dict, th: dict, out: list) -> None:
    data = snap.get("memory") or {}
    used = data.get("used_pct")
    limit = _limit(th, "memory_used_pct")
    if used is not None and limit is not None and used >= limit:
        out.append(
            Alert(
                "memory_used_pct",
                "critical",
                f"memory {used:.0f}% used (>= {limit:.0f}%)",
                round(used, 1),
                limit,
            )
        )
    swap = data.get("swap_used_pct")
    swap_limit = _limit(th, "swap_used_pct")
    if swap is not None and swap_limit is not None and swap >= swap_limit:
        out.append(
            Alert(
                "swap_used_pct",
                "warning",
                f"swap {swap:.0f}% used (>= {swap_limit:.0f}%)",
                round(swap, 1),
                swap_limit,
            )
        )


def _disk(snap: dict, th: dict, out: list) -> None:
    limit = _limit(th, "disk_used_pct")
    if limit is None:
        return
    for fs in (snap.get("disk") or {}).get("filesystems", []):
        pct = fs.get("used_pct")
        if pct is not None and pct >= limit:
            mount = fs.get("mount", "?")
            out.append(
                Alert(
                    f"disk:{mount}",
                    "critical",
                    f"filesystem {mount} {pct:.0f}% full (>= {limit:.0f}%)",
                    round(pct, 1),
                    limit,
                )
            )


def _thermal(snap: dict, th: dict, out: list) -> None:
    hottest = (snap.get("thermal") or {}).get("hottest_c")
    limit = _limit(th, "thermal_max_c")
    if hottest is not None and limit is not None and hottest >= limit:
        out.append(
            Alert(
                "thermal_max_c",
                "critical",
                f"hottest sensor {hottest:.1f} C (>= {limit:.0f} C)",
                round(hottest, 1),
                limit,
            )
        )


def _gpu(snap: dict, th: dict, out: list) -> None:
    limit = _limit(th, "gpu_temp_c")
    if limit is None:
        return
    temp = _as_float(((snap.get("gpu") or {}).get("gpu") or {}).get("temperature.gpu"))
    if temp is not None and temp >= limit:
        out.append(
            Alert("gpu_temp_c", "critical", f"GPU {temp:.0f} C (>= {limit:.0f} C)", temp, limit)
        )


def _load(snap: dict, th: dict, out: list) -> None:
    limit = _limit(th, "load_per_core")
    if limit is None:
        return
    per_core = (snap.get("load") or {}).get("per_core")
    if per_core is not None and per_core >= limit:
        out.append(
            Alert(
                "load_per_core",
                "warning",
                f"load {per_core:.2f}/core (>= {limit:.2f})",
                round(per_core, 2),
                limit,
            )
        )


def _contention(snap: dict, th: dict, out: list) -> None:
    data = snap.get("contention") or {}
    iowait = data.get("iowait_pct")
    io_limit = _limit(th, "iowait_pct")
    if iowait is not None and io_limit is not None and iowait >= io_limit:
        out.append(
            Alert(
                "iowait_pct",
                "warning",
                f"iowait {iowait:.0f}% (>= {io_limit:.0f}%) — CPUs waiting on I/O",
                round(iowait, 1),
                io_limit,
            )
        )
    blocked = _as_float(data.get("blocked_procs"))
    blk_limit = _limit(th, "blocked_procs")
    if blocked is not None and blk_limit is not None and blocked >= blk_limit:
        out.append(
            Alert(
                "blocked_procs",
                "warning",
                f"{blocked:.0f} processes blocked on I/O (>= {blk_limit:.0f})",
                round(blocked, 1),
                blk_limit,
            )
        )


def _containers(snap: dict, th: dict, out: list) -> None:
    if not th.get("container_unhealthy"):
        return
    for container in (snap.get("containers") or {}).get("containers", []):
        if "unhealthy" in str(container.get("status", "")).lower():
            name = container.get("name", "?")
            out.append(Alert(f"container:{name}", "critical", f"container '{name}' is unhealthy"))


def _availability(snap: dict, th: dict, out: list) -> None:
    if not th.get("subsystem_down"):
        return
    available = snap.get("available") or {}
    # Only the tool-backed subsystems that should be present on the Spark — alert
    # if nvidia-smi or docker (and thus the probe) goes dark.
    for sub in ("gpu", "containers"):
        if available.get(sub) is False:
            out.append(
                Alert(
                    f"subsystem_down:{sub}",
                    "critical",
                    f"subsystem '{sub}' is unavailable (probe tool missing or failing)",
                )
            )


def evaluate(snapshot: dict, thresholds: dict) -> list[Alert]:
    """Return the alerts currently firing for ``snapshot`` under ``thresholds``."""
    alerts: list[Alert] = []
    for check in (_memory, _disk, _thermal, _gpu, _load, _contention, _containers, _availability):
        check(snapshot, thresholds, alerts)
    return alerts
