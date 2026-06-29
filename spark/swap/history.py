"""Per-process memory/swap history: a bounded JSONL store + sampler.

``spark swap`` needs to answer "what has been eating RAM/swap lately?" without a
running daemon and without an external time-series database. This module is the
storage layer behind that: :func:`record` samples ``/proc/<pid>/status`` once
and appends one JSONL line per process; :func:`query` aggregates the recent
window into a top-N ranking. Pure stdlib (the zero-runtime-dependency rule), and
everything is injectable (``now``, ``store_dir``, ``proc_root``) so the sampler
is hermetically testable.

Storage layout (under :func:`default_store_dir`, i.e.
``$XDG_STATE_HOME/dgx-spark``)::

    proc-history.jsonl      active file, appended to
    proc-history.jsonl.1    the single previous rotation

JSONL line schema (one line per process per snapshot)::

    {"ts": float, "pid": int, "comm": str, "rss_kb": int, "swap_kb": int}

Bounded retention / the rotation invariant
-------------------------------------------
The store is *self-pruning*: it can never grow without bound. The active file is
kept at or below ``max_bytes``. When an append would push it over ``max_bytes``,
the active file is rotated to ``proc-history.jsonl.1`` (overwriting the single
previous rotation — at most ONE rotation is retained) and a fresh active file is
started. :func:`query` reads across the active file and the one rotation.

Documented cap: total bytes across active + rotation never exceed
``2 * max_bytes`` (= ``(rotations + 1) * max_bytes`` with ``rotations == 1``),
*provided a single snapshot fits within* ``max_bytes`` — which it always does in
practice (a few hundred bytes per process, against a multi-MB default). Old data
is dropped as new data arrives, so recent samples always survive a rotation.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from spark.cli._errors import EXIT_ENV_ERROR, CliError
from spark.monitor.config import state_home
from spark.probe import _run

_APP = "dgx-spark"
_ACTIVE_NAME = "proc-history.jsonl"
_ROTATED_NAME = "proc-history.jsonl.1"

# A few MB: large enough that one snapshot (a few hundred bytes per process)
# is a rounding error, small enough that the on-disk footprint stays trivial.
DEFAULT_MAX_BYTES = 5_000_000


def default_store_dir() -> Path:
    """Return the default store directory: ``$XDG_STATE_HOME/dgx-spark``."""
    return state_home() / _APP


def _parse_status(text: str) -> tuple[Optional[str], Optional[int], int]:
    """Pull ``(comm, rss_kb, swap_kb)`` from a ``/proc/<pid>/status`` body.

    ``comm`` comes from ``Name:``, ``rss_kb`` from ``VmRSS:``, ``swap_kb`` from
    ``VmSwap:`` (all kB). ``rss_kb`` is ``None`` when the process has no
    ``VmRSS:`` line at all (a kernel thread) — the caller skips those. A missing
    ``VmSwap:`` is treated as ``0``.
    """
    comm: Optional[str] = None
    rss_kb: Optional[int] = None
    swap_kb = 0
    for line in text.splitlines():
        if line.startswith("Name:"):
            comm = line.partition(":")[2].strip()
        elif line.startswith("VmRSS:"):
            try:
                rss_kb = int(line.split()[1])
            except (IndexError, ValueError):
                rss_kb = None
        elif line.startswith("VmSwap:"):
            try:
                swap_kb = int(line.split()[1])
            except (IndexError, ValueError):
                swap_kb = 0
    return comm, rss_kb, swap_kb


def _collect_samples(proc_root: str, ts: float) -> list[dict]:
    """Read each ``<proc_root>/<pid>/status`` into a sample dict.

    A single unreadable / vanished pid is skipped, never fatal: per-pid reads go
    through :func:`spark.probe._run.read_text`, which swallows ``OSError`` (and
    thus ``FileNotFoundError`` / ``PermissionError`` / ``ProcessLookupError``)
    and returns ``None``.
    """
    root = Path(proc_root)
    try:
        entries = list(root.iterdir())
    except OSError:
        return []

    samples: list[dict] = []
    for pdir in entries:
        if not pdir.name.isdigit():
            continue  # "self", "meminfo", … — not a process
        text = _run.read_text(pdir / "status")
        if text is None:
            continue  # vanished / unreadable pid — skip, never fatal
        comm, rss_kb, swap_kb = _parse_status(text)
        if rss_kb is None:
            continue  # kernel thread / no resident memory
        samples.append(
            {
                "ts": ts,
                "pid": int(pdir.name),
                "comm": comm or "?",
                "rss_kb": rss_kb,
                "swap_kb": swap_kb,
            }
        )
    return samples


def _append(store_dir: Path, payload: str, max_bytes: int) -> None:
    """Append ``payload`` to the active file, rotating to honor ``max_bytes``.

    Rotation is *before* the write: if the existing active file plus the new
    payload would exceed ``max_bytes``, the active file is moved onto the single
    rotation slot (overwriting it) and a fresh active file is started. This keeps
    the active file <= ``max_bytes`` (given a snapshot that fits) and the total
    <= ``2 * max_bytes``.
    """
    active = store_dir / _ACTIVE_NAME
    rotated = store_dir / _ROTATED_NAME
    data = payload.encode("utf-8")
    try:
        store_dir.mkdir(parents=True, exist_ok=True)
        try:
            active_size = active.stat().st_size
        except FileNotFoundError:
            active_size = 0
        if active_size > 0 and active_size + len(data) > max_bytes:
            os.replace(active, rotated)  # overwrite the single previous rotation
        with active.open("a", encoding="utf-8") as fh:
            fh.write(payload)
    except OSError as exc:
        raise CliError(
            EXIT_ENV_ERROR,
            f"could not write swap history store at {store_dir}: {exc}",
            remediation="check that $XDG_STATE_HOME (or ~/.local/state) is writable",
        ) from exc


def record(
    now: float | None = None,
    *,
    store_dir: str | Path | None = None,
    proc_root: str = "/proc",
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> int:
    """Sample ``/proc`` once and append one JSONL line per process.

    Reads each ``<proc_root>/<pid>/status``, tags every line with one shared
    snapshot ``ts`` (``now`` or :func:`time.time`), appends them to the bounded
    store under ``store_dir`` (default :func:`default_store_dir`), enforces
    rotation, and returns the number of samples written. Unreadable pids and
    kernel threads are skipped. Returns ``0`` when nothing was sampled.
    """
    ts = time.time() if now is None else float(now)
    base = default_store_dir() if store_dir is None else Path(store_dir)

    samples = _collect_samples(proc_root, ts)
    if not samples:
        return 0

    payload = "".join(json.dumps(s) + "\n" for s in samples)
    _append(base, payload, max_bytes)
    return len(samples)


def _iter_rows(store_dir: Path):
    """Yield parsed dict rows from the rotation then the active file.

    Malformed / partial JSONL lines and non-dict payloads are skipped so a
    corrupt tail can never make :func:`query` raise.
    """
    for name in (_ROTATED_NAME, _ACTIVE_NAME):
        text = _run.read_text(store_dir / name)
        if text is None:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(row, dict):
                yield row


def _parse_sample_row(row: dict):
    """Coerce a stored JSONL row to ``(ts, pid, comm, rss_kb, swap_kb)`` or None."""
    try:
        return (
            float(row["ts"]),
            int(row["pid"]),
            str(row["comm"]),
            int(row.get("rss_kb", 0)),
            int(row.get("swap_kb", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _accumulate(groups: dict, pid: int, comm: str, rss_kb: int, swap_kb: int) -> None:
    """Fold one sample into the per-(pid, comm) peak-swap / peak-rss accumulator."""
    key = (pid, comm)
    existing = groups.get(key)
    if existing is None:
        groups[key] = {
            "pid": pid,
            "comm": comm,
            "peak_swap_kb": swap_kb,
            "peak_rss_kb": rss_kb,
        }
        return
    if swap_kb > existing["peak_swap_kb"]:
        existing["peak_swap_kb"] = swap_kb
    if rss_kb > existing["peak_rss_kb"]:
        existing["peak_rss_kb"] = rss_kb


def query(
    window_seconds: int,
    top_n: int = 10,
    *,
    store_dir: str | Path | None = None,
    now: float | None = None,
) -> list[dict]:
    """Aggregate recent samples into a top-N memory/swap ranking.

    Reads the store (active + the one rotation), keeps samples whose ``ts`` falls
    in ``[now - window_seconds, now]``, groups by ``(pid, comm)``, takes the peak
    ``swap_kb`` and peak ``rss_kb`` per group, and returns up to ``top_n`` dicts
    ``{"pid", "comm", "peak_swap_kb", "peak_rss_kb"}`` sorted by ``peak_swap_kb``
    descending (tiebreak: ``peak_rss_kb`` descending).

    An empty or missing store returns ``[]`` (never raises).
    """
    base = default_store_dir() if store_dir is None else Path(store_dir)
    end = time.time() if now is None else float(now)
    start = end - float(window_seconds)

    groups: dict[tuple[int, str], dict] = {}
    for row in _iter_rows(base):
        parsed = _parse_sample_row(row)
        if parsed is None:
            continue
        ts, pid, comm, rss_kb, swap_kb = parsed
        if ts < start or ts > end:
            continue
        _accumulate(groups, pid, comm, rss_kb, swap_kb)

    ranked = sorted(
        groups.values(),
        key=lambda d: (d["peak_swap_kb"], d["peak_rss_kb"]),
        reverse=True,
    )
    return ranked[:top_n]
