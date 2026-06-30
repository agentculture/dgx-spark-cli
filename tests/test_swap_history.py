"""Tests for the per-process memory/swap history store + sampler (t3).

All hermetic: the store lives under ``tmp_path``, ``/proc`` is faked under
``tmp_path``, and ``now`` is injected. Nothing here requires root, reads the
real ``/proc``, or writes to the real XDG state dir.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from spark.cli._errors import CliError
from spark.swap import history

# --- fake /proc helpers ---------------------------------------------------


def _write_status(
    proc_root: Path,
    pid: int,
    *,
    name: str,
    rss_kb: int | None,
    swap_kb: int | None,
) -> None:
    """Create ``<proc_root>/<pid>/status`` with the given fields.

    ``rss_kb=None`` omits the ``VmRSS:`` line (a kernel thread); ``swap_kb=None``
    omits the ``VmSwap:`` line (process without swap accounting).
    """
    pdir = proc_root / str(pid)
    pdir.mkdir(parents=True, exist_ok=True)
    lines = [f"Name:\t{name}", "State:\tS (sleeping)"]
    if rss_kb is not None:
        lines.append(f"VmRSS:\t{rss_kb:>8} kB")
    if swap_kb is not None:
        lines.append(f"VmSwap:\t{swap_kb:>8} kB")
    (pdir / "status").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_lines(store_dir: Path, name: str = "proc-history.jsonl") -> list[dict]:
    text = (store_dir / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _store_size(store_dir: Path) -> int:
    return sum(p.stat().st_size for p in store_dir.glob("proc-history.jsonl*"))


# --- default_store_dir ----------------------------------------------------


def test_default_store_dir_uses_xdg_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert history.default_store_dir() == tmp_path / "dgx-spark"


# --- AC1: record() appends per-process JSONL with a shared ts -------------


def test_record_writes_per_process_jsonl_with_shared_ts(tmp_path) -> None:
    proc = tmp_path / "proc"
    store = tmp_path / "store"
    _write_status(proc, 1234, name="alpha", rss_kb=500, swap_kb=10)
    _write_status(proc, 5678, name="beta", rss_kb=100, swap_kb=200)
    # A kernel thread (no VmRSS) must be skipped.
    _write_status(proc, 2, name="kworker", rss_kb=None, swap_kb=None)

    written = history.record(now=1000.0, store_dir=store, proc_root=str(proc))

    assert written == 2  # kworker skipped
    rows = {r["pid"]: r for r in _read_lines(store)}
    assert set(rows) == {1234, 5678}
    assert all(r["ts"] == 1000.0 for r in rows.values())  # shared snapshot ts
    assert rows[1234] == {
        "ts": 1000.0,
        "pid": 1234,
        "comm": "alpha",
        "rss_kb": 500,
        "swap_kb": 10,
    }
    assert rows[5678]["swap_kb"] == 200
    assert rows[5678]["rss_kb"] == 100


def test_record_missing_vmswap_treated_as_zero(tmp_path) -> None:
    proc = tmp_path / "proc"
    store = tmp_path / "store"
    _write_status(proc, 42, name="noswap", rss_kb=80, swap_kb=None)

    assert history.record(now=5.0, store_dir=store, proc_root=str(proc)) == 1
    (row,) = _read_lines(store)
    assert row["swap_kb"] == 0
    assert row["rss_kb"] == 80


def test_record_skips_unreadable_and_nondigit_entries(tmp_path) -> None:
    proc = tmp_path / "proc"
    store = tmp_path / "store"
    _write_status(proc, 7, name="good", rss_kb=64, swap_kb=0)
    # A pid directory with no status file at all (vanished/unreadable pid).
    (proc / "999").mkdir(parents=True)
    # A non-numeric entry under /proc (e.g. "self", "meminfo") must be ignored.
    (proc / "self").mkdir()
    (proc / "self" / "status").write_text("Name:\tx\nVmRSS:\t1 kB\n", encoding="utf-8")
    (proc / "meminfo").write_text("MemTotal: 1 kB\n", encoding="utf-8")

    # A single bad pid is never fatal.
    written = history.record(now=1.0, store_dir=store, proc_root=str(proc))
    assert written == 1
    (row,) = _read_lines(store)
    assert row["pid"] == 7


def test_record_returns_zero_when_no_processes(tmp_path) -> None:
    proc = tmp_path / "proc"
    proc.mkdir()
    store = tmp_path / "store"
    assert history.record(now=1.0, store_dir=store, proc_root=str(proc)) == 0


def test_record_raises_clierror_when_store_unwritable(tmp_path) -> None:
    proc = tmp_path / "proc"
    _write_status(proc, 1, name="x", rss_kb=1, swap_kb=0)
    # Point the store at a path whose parent is a file -> mkdir fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir\n", encoding="utf-8")
    with pytest.raises(CliError) as exc:
        history.record(now=1.0, store_dir=blocker / "store", proc_root=str(proc))
    assert exc.value.code == 2  # environment error


# --- AC2: bounded retention / rotation -----------------------------------


def test_bounded_retention_rotation(tmp_path) -> None:
    proc = tmp_path / "proc"
    store = tmp_path / "store"
    for pid in (101, 102, 103):
        _write_status(proc, pid, name=f"p{pid}", rss_kb=pid, swap_kb=pid)

    max_bytes = 1000  # comfortably larger than one ~3-line snapshot
    last_ts = 0.0
    for i in range(500):  # far more data than the cap can hold
        last_ts = 1000.0 + i
        history.record(now=last_ts, store_dir=store, proc_root=str(proc), max_bytes=max_bytes)

    # Hard invariant: the store is self-pruning and can never grow without
    # bound. Total bytes across active + the single rotation <= 2 * max_bytes.
    assert _store_size(store) <= 2 * max_bytes

    # ...and recent data survives: the latest snapshot is still queryable.
    rows = history.query(window_seconds=10, store_dir=store, now=last_ts)
    assert rows, "recent data must survive rotation"
    assert {r["pid"] for r in rows} == {101, 102, 103}


# --- AC3: query() top consumers / window / robustness --------------------


def test_query_ranks_by_peak_swap_then_rss(tmp_path) -> None:
    store = tmp_path / "store"
    proc = tmp_path / "proc"
    # Snapshot at t=100: pid 1 has low swap, pid 2 high swap.
    _write_status(proc, 1, name="one", rss_kb=10, swap_kb=5)
    _write_status(proc, 2, name="two", rss_kb=10, swap_kb=900)
    _write_status(proc, 3, name="three", rss_kb=999, swap_kb=5)  # swap ties pid1
    history.record(now=100.0, store_dir=store, proc_root=str(proc))

    # Snapshot at t=101: pid 1 spikes its swap -> peak must reflect the spike.
    _write_status(proc, 1, name="one", rss_kb=10, swap_kb=50)
    history.record(now=101.0, store_dir=store, proc_root=str(proc))

    rows = history.query(window_seconds=60, top_n=10, store_dir=store, now=101.0)
    pids = [r["pid"] for r in rows]
    # two (900) > one (peak 50) > three (5, but rss 999 beats pid... no tie here)
    assert pids[0] == 2
    by_pid = {r["pid"]: r for r in rows}
    assert by_pid[1]["peak_swap_kb"] == 50  # peak across the two snapshots
    assert by_pid[2]["peak_swap_kb"] == 900
    # one (swap 50) ranks above three (swap 5)
    assert pids.index(1) < pids.index(3)
    # shape of a row
    assert set(by_pid[2]) == {"pid", "comm", "peak_swap_kb", "peak_rss_kb"}
    assert by_pid[3]["peak_rss_kb"] == 999


def test_query_tiebreaks_equal_swap_by_peak_rss(tmp_path) -> None:
    store = tmp_path / "store"
    proc = tmp_path / "proc"
    _write_status(proc, 10, name="lo", rss_kb=100, swap_kb=7)
    _write_status(proc, 20, name="hi", rss_kb=900, swap_kb=7)  # same swap, more rss
    history.record(now=50.0, store_dir=store, proc_root=str(proc))

    rows = history.query(window_seconds=60, store_dir=store, now=50.0)
    assert [r["pid"] for r in rows] == [20, 10]


def test_query_top_n_limits_results(tmp_path) -> None:
    store = tmp_path / "store"
    proc = tmp_path / "proc"
    for pid in range(1, 6):
        _write_status(proc, pid, name=f"p{pid}", rss_kb=pid, swap_kb=pid * 10)
    history.record(now=10.0, store_dir=store, proc_root=str(proc))

    rows = history.query(window_seconds=60, top_n=2, store_dir=store, now=10.0)
    assert len(rows) == 2
    assert [r["pid"] for r in rows] == [5, 4]  # highest swap first


def test_query_filters_out_samples_outside_window(tmp_path) -> None:
    store = tmp_path / "store"
    proc = tmp_path / "proc"
    _write_status(proc, 1, name="old", rss_kb=10, swap_kb=10)
    history.record(now=1000.0, store_dir=store, proc_root=str(proc))
    # pid 1 exits before the next snapshot; only pid 2 is live at t=2000.
    shutil.rmtree(proc / "1")
    _write_status(proc, 2, name="new", rss_kb=10, swap_kb=20)
    history.record(now=2000.0, store_dir=store, proc_root=str(proc))

    # Window [2000-60, 2000] excludes the t=1000 sample.
    rows = history.query(window_seconds=60, store_dir=store, now=2000.0)
    assert [r["pid"] for r in rows] == [2]


def test_query_empty_or_missing_store_returns_empty_list(tmp_path) -> None:
    missing = tmp_path / "does-not-exist"
    assert history.query(window_seconds=60, store_dir=missing) == []

    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "proc-history.jsonl").write_text("", encoding="utf-8")
    assert history.query(window_seconds=60, store_dir=empty, now=1.0) == []


def test_query_tolerates_malformed_jsonl_lines(tmp_path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    good = json.dumps({"ts": 5.0, "pid": 9, "comm": "ok", "rss_kb": 1, "swap_kb": 2})
    (store / "proc-history.jsonl").write_text(
        "not json at all\n"
        + "{partial\n"
        + json.dumps(["a", "list", "not", "dict"])
        + "\n"
        + json.dumps({"ts": "bad", "pid": 1, "comm": "x", "rss_kb": 1, "swap_kb": 1})
        + "\n"
        + good
        + "\n",
        encoding="utf-8",
    )
    rows = history.query(window_seconds=60, store_dir=store, now=5.0)
    assert [r["pid"] for r in rows] == [9]
