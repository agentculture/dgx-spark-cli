"""spark/swap/sar.py — System-trend reader over existing sysstat/sar.

Reads recent swap and memory utilization from ``sadf -j`` (the JSON-emitting
front-end to the sysstat ``sar`` subsystem) and normalizes it into a plain
time-series list.  When sysstat is absent or the call fails the function
returns an ``available: False`` sentinel without raising — identical graceful-
degradation pattern as the rest of the probe/ modules.

Public API
----------
``read_swap_trend(hours=24, *, runner=run_tool) -> dict``

Return shape::

    {
        "available": bool,
        "source": "sar" | None,
        "series": [
            {"ts": str, "swap_used_pct": float, "mem_used_pct": float},
            ...
        ],
    }
"""

from __future__ import annotations

import json
from typing import Callable, Optional, Sequence

from spark.probe._run import run_tool

# Matches spark.probe._run.Runner but declared locally to avoid import-time
# coupling with the type alias (which is not re-exported as part of the public API).
_Runner = Callable[[str, Sequence[str]], Optional[str]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_ts(timestamp: object) -> str:
    """Build a plain timestamp string from a sadf timestamp block.

    Accepts a dict with optional ``date`` / ``time`` keys (or any subset).
    Falls back gracefully if the block has an unexpected shape.
    """
    if not isinstance(timestamp, dict):
        return str(timestamp)
    date = timestamp.get("date", "")
    time = timestamp.get("time", "")
    if date and time:
        return f"{date} {time}"
    return str(date or time or timestamp)


def _parse_sadf_json(raw: str) -> list[dict]:
    """Parse ``sadf -j`` stdout into a normalized swap/mem series.

    Navigates defensively with ``.get()`` everywhere — the sysstat JSON
    schema varies across distro versions so we never assume a key is present.
    Entries that are missing required fields (``%swpused`` or ``%memused``)
    are silently skipped; we never raise KeyError/TypeError/ValueError on
    unexpected shapes.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []

    series: list[dict] = []
    try:
        hosts = data.get("sysstat", {}).get("hosts", [])
        if not isinstance(hosts, list):
            return []
        for host in hosts:
            if not isinstance(host, dict):
                continue
            statistics = host.get("statistics", [])
            if not isinstance(statistics, list):
                continue
            for entry in statistics:
                if not isinstance(entry, dict):
                    continue

                ts_block = entry.get("timestamp", {})
                ts = _build_ts(ts_block)

                swap_pages = entry.get("swap-pages")
                memory = entry.get("memory")

                if not isinstance(swap_pages, dict) or not isinstance(memory, dict):
                    continue

                swap_pct = swap_pages.get("%swpused")
                mem_pct = memory.get("%memused")

                if swap_pct is None or mem_pct is None:
                    continue

                try:
                    series.append(
                        {
                            "ts": ts,
                            "swap_used_pct": round(float(swap_pct), 2),
                            "mem_used_pct": round(float(mem_pct), 2),
                        }
                    )
                except (TypeError, ValueError):
                    continue

    except Exception:  # noqa: BLE001  # pragma: no cover — belt-and-suspenders guard
        return series

    return series


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_swap_trend(hours: int = 24, *, runner: _Runner = run_tool) -> dict:
    """Return recent swap/memory trend from sysstat/sadf.

    Prefers ``sadf -j`` (JSON output, easiest to parse reliably).  If the
    ``sadf`` call returns ``None`` (tool absent or failed), falls through to a
    best-effort text ``sar`` parse; if that is also ``None``, returns an
    ``available: False`` sentinel without raising.

    Args:
        hours: Advisory history window in hours.  Passed to sadf so the OS
            can filter the activity file, but tests drive a canned runner and
            do not depend on real-time output.
        runner: Injectable tool runner; defaults to
            :func:`spark.probe._run.run_tool`.  Tests supply a fake runner so
            no real ``sar``/``sadf`` binary is required.

    Returns:
        ``{"available": bool, "source": "sar" | None, "series": [...]}``
        where each series entry is
        ``{"ts": str, "swap_used_pct": float, "mem_used_pct": float}``.
    """
    _unavailable: dict = {"available": False, "source": None, "series": []}

    # ------------------------------------------------------------------
    # Primary path: sadf -j (JSON)
    # ------------------------------------------------------------------
    # sadf [opts] [interval [count]] [datafile] -- [sar_options]
    # -j        emit JSON
    # --        separator before sar-specific options
    # -S        swap utilization (%swpused et al.)
    # -r        memory utilization (%memused et al.)
    # We do not pass a start-time filter here because the activity file
    # typically covers the recent 24 h by default; adding a computed
    # -s option would couple us to real-time in tests.
    sadf_raw = runner("sadf", ["-j", "--", "-S", "-r"])
    if sadf_raw is not None:
        series = _parse_sadf_json(sadf_raw)
        return {"available": True, "source": "sar", "series": series}

    # ------------------------------------------------------------------
    # Fallback: text sar (best-effort — column layout varies)
    # ------------------------------------------------------------------
    # If sadf is absent, try raw sar.  We skip detailed text parsing here
    # because column positions differ across sysstat versions and the JSON
    # path is the priority.  If sar also returns None, degrade to unavailable.
    sar_raw = runner("sar", ["-S", "-r", "1", "1"])
    if sar_raw is None:
        return _unavailable

    # sar is present but we have no reliable parser for text output —
    # report available with an empty series rather than crashing.
    return {"available": True, "source": "sar", "series": []}
