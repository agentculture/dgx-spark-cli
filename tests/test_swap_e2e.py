"""End-to-end operator-story demo for the ``dgx-spark-cli swap`` noun (t7).

This module drives the CLI through its real entry point (:func:`spark.cli.main`)
exactly as :mod:`tests.test_cli` does, but in one cohesive scenario: a box under
sustained swap pressure, an operator investigating and planning relief.

The four steps encode the full acceptance story:

1. **Pressure is visible** — ``spark swap status`` surfaces a box under heavy
   swap pressure (c3: pressure observable; c2: works without root).
2. **Grow proposes relief (dry-run)** — ``spark swap grow <size>`` defaults to
   dry-run, mutates nothing, and surfaces the plan (c4: grow relieves; h4: plan
   surfaced).
3. **History names the hog** — ``spark swap history`` names the top consuming
   process (c11/h4: history tells you *who*; c5: diagnostic included).
4. **Graceful degradation** — each read verb exits 0 with a useful message even
   when a subsystem is absent (c2/c15: no traceback, no unhealthy exit).

All monkeypatches target the names *as imported* into
:mod:`spark.cli._commands.swap` (matching the t6 pattern from
:mod:`tests.test_swap_cli`). No real /proc/swaps, sar, or root access required.
"""

from __future__ import annotations

import json

import pytest

from spark.cli import main
from spark.cli._commands import swap as swap_cmd

_GIB = 1024**3

# ---------------------------------------------------------------------------
# Shared scenario data — "DGX Spark under heavy swap pressure"
# ---------------------------------------------------------------------------

_PRESSURE_STATE: dict = {
    "available": True,
    "swappiness": 60,
    "mem": {
        "total_bytes": 128 * _GIB,
        "available_bytes": 2 * _GIB,
        "free_bytes": 512 * 1024**2,
        "used_bytes": 126 * _GIB,
        "used_pct": 98.4,
        "swap_total_bytes": 8 * _GIB,
        "swap_free_bytes": 128 * 1024**2,
        "swap_used_bytes": int(7.875 * _GIB),
        "swap_used_pct": 98.4,  # ← heavy swap pressure
    },
    "devices": [
        {
            "name": "/swap.img",
            "type": "file",
            "size_bytes": 8 * _GIB,
            "used_bytes": int(7.875 * _GIB),
            "priority": -2,
        }
    ],
    "backing": {
        "swapfile": "/swap.img",
        "fs_type": "ext4",
        "mount": "/",
        "free_bytes": 200 * _GIB,
    },
}

_PRESSURE_TREND: dict = {
    "available": True,
    "source": "sar",
    "series": [
        {"ts": "10:00:00", "swap_used_pct": 95.0, "mem_used_pct": 97.0},
        {"ts": "10:10:00", "swap_used_pct": 97.2, "mem_used_pct": 97.5},
        {"ts": "10:20:00", "swap_used_pct": 98.4, "mem_used_pct": 98.4},
    ],
}

_HOG_ROWS: list[dict] = [
    {"pid": 7777, "comm": "vllm", "peak_swap_kb": 4 * 1024 * 1024, "peak_rss_kb": 8 * 1024 * 1024},
    {"pid": 8888, "comm": "python3", "peak_swap_kb": 512 * 1024, "peak_rss_kb": 2 * 1024 * 1024},
]

_UNAVAIL_STATE: dict = {
    "available": False,
    "swappiness": None,
    "mem": {
        "total_bytes": 0,
        "available_bytes": 0,
        "free_bytes": 0,
        "used_bytes": 0,
        "used_pct": 0.0,
        "swap_total_bytes": 0,
        "swap_free_bytes": 0,
        "swap_used_bytes": 0,
        "swap_used_pct": 0.0,
    },
    "devices": [],
    "backing": {"swapfile": None, "fs_type": None, "mount": None, "free_bytes": None},
}

_UNAVAIL_TREND: dict = {"available": False, "source": None, "series": []}


# ---------------------------------------------------------------------------
# Story step 1 — Pressure is visible (c3, c2)
# ---------------------------------------------------------------------------


class TestPressureVisible:
    """Step 1: ``spark swap status`` exposes a box under sustained swap pressure."""

    def test_status_text_shows_pressure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Text mode surfaces heavy swap usage — c3 (pressure observable)."""
        monkeypatch.setattr(swap_cmd, "collect_swap_state", lambda: _PRESSURE_STATE)
        monkeypatch.setattr(swap_cmd, "read_swap_trend", lambda: _PRESSURE_TREND)

        rc = main(["swap", "status"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "# swap status" in out
        # heavy swap_used_pct must appear in the rendered output
        assert "98.4" in out
        # the swapfile device must appear
        assert "/swap.img" in out

    def test_status_json_shows_pressure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """JSON mode exposes the numeric pressure values — c3, c2 (no root needed)."""
        monkeypatch.setattr(swap_cmd, "collect_swap_state", lambda: _PRESSURE_STATE)
        monkeypatch.setattr(swap_cmd, "read_swap_trend", lambda: _PRESSURE_TREND)

        rc = main(["swap", "status", "--json"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["subject"] == "swap status"
        assert payload["state"]["available"] is True
        assert payload["state"]["mem"]["swap_used_pct"] == pytest.approx(98.4)
        # trend summary should reflect the sustained pressure (recent ≥ avg)
        summary = payload["trend_summary"]
        assert summary["recent_swap_used_pct"] == pytest.approx(98.4)
        assert summary["avg_swap_used_pct"] == pytest.approx(round((95.0 + 97.2 + 98.4) / 3, 2))
        assert summary["samples"] == 3

    def test_status_text_shows_trend_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Trend section lists the sar source in text output."""
        monkeypatch.setattr(swap_cmd, "collect_swap_state", lambda: _PRESSURE_STATE)
        monkeypatch.setattr(swap_cmd, "read_swap_trend", lambda: _PRESSURE_TREND)

        rc = main(["swap", "status"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "sar" in out
        assert "Trend" in out


# ---------------------------------------------------------------------------
# Story step 2 — Grow proposes relief without mutating (c4, h4)
# ---------------------------------------------------------------------------


class TestGrowDryRun:
    """Step 2: ``spark swap grow`` defaults to dry-run; nothing is mutated."""

    def _spy_apply(self, monkeypatch: pytest.MonkeyPatch) -> dict:
        """Install a recording fake for apply_grow_plan."""
        calls: dict = {}

        def _fake(plan, *, apply=False, runner=None, geteuid=None):
            calls["apply"] = apply
            return {
                "applied": apply,
                "dry_run": not apply,
                "swapfile": plan.swapfile,
                "target_size_bytes": plan.target_size_bytes,
                "persistent": plan.persistent,
                "steps": [
                    {"desc": s["desc"], "argv": list(s.get("argv") or [])} for s in plan.steps
                ],
                "warnings": list(plan.warnings),
            }

        monkeypatch.setattr(swap_cmd, "apply_grow_plan", _fake)
        return calls

    def test_grow_dry_run_mutates_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Dry-run records apply=False and emits a plan without executing. (c4, h4)"""
        monkeypatch.setattr(swap_cmd, "collect_swap_state", lambda: _PRESSURE_STATE)
        calls = self._spy_apply(monkeypatch)

        rc = main(["swap", "grow", "32G"])

        assert rc == 0
        captured = capsys.readouterr()
        # The spy must confirm dry_run path was taken
        assert calls.get("apply") is False
        # Operator warning appears on stderr
        assert "WARNING: this will modify swap" in captured.err
        # Structured plan result surfaces on stdout
        assert captured.out.strip()

    def test_grow_dry_run_json_shows_plan(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """JSON dry-run reveals plan shape without mutation. (c4)"""
        monkeypatch.setattr(swap_cmd, "collect_swap_state", lambda: _PRESSURE_STATE)
        calls = self._spy_apply(monkeypatch)

        rc = main(["swap", "grow", "32GiB", "--json"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert payload["applied"] is False
        assert payload["target_size_bytes"] == 32 * _GIB
        assert calls.get("apply") is False

    def test_grow_text_shows_swapfile_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Text mode names the swapfile and target size in the plan output. (h4)"""
        monkeypatch.setattr(swap_cmd, "collect_swap_state", lambda: _PRESSURE_STATE)
        self._spy_apply(monkeypatch)

        rc = main(["swap", "grow", "32G"])

        assert rc == 0
        out = capsys.readouterr().out
        # The operator sees the swapfile path and that this is a dry-run
        assert "/swap.img" in out
        assert "dry-run" in out.lower()


# ---------------------------------------------------------------------------
# Story step 3 — History names the hog (c11, h4, c5)
# ---------------------------------------------------------------------------


class TestHistoryNamesHog:
    """Step 3: ``spark swap history`` identifies the top consuming process."""

    def test_history_text_names_top_consumer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Text mode includes the top consuming process name. (c11, h4, c5)"""
        monkeypatch.setattr(swap_cmd, "query", lambda *a, **k: _HOG_ROWS)

        rc = main(["swap", "history"])

        assert rc == 0
        out = capsys.readouterr().out
        # The top consumer (vllm) must appear by name
        assert "vllm" in out
        # Its PID should also appear
        assert "7777" in out

    def test_history_json_names_top_consumer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """JSON mode exposes top consumer with peak swap bytes. (c5)"""
        monkeypatch.setattr(swap_cmd, "query", lambda *a, **k: _HOG_ROWS)

        rc = main(["swap", "history", "--json"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["subject"] == "swap history"
        top = payload["top"]
        assert len(top) == 2
        assert top[0]["comm"] == "vllm"
        assert top[0]["pid"] == 7777
        assert top[0]["peak_swap_kb"] == 4 * 1024 * 1024

    def test_history_window_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Window and top-n args are parsed and forwarded to query. (c5)"""
        received: dict = {}

        def _fake_query(window_seconds, top_n=10, **k):
            received["window"] = window_seconds
            received["top_n"] = top_n
            return _HOG_ROWS

        monkeypatch.setattr(swap_cmd, "query", _fake_query)

        rc = main(["swap", "history", "--window", "2h", "--top", "3"])

        assert rc == 0
        capsys.readouterr()
        assert received["window"] == 7200  # 2h → seconds
        assert received["top_n"] == 3


# ---------------------------------------------------------------------------
# Story step 4 — Graceful degradation (c2, c15)
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Step 4: read verbs exit 0 with useful messages when subsystems are absent."""

    def test_status_unavailable_exits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``spark swap status`` exits 0 even when swap and sar are absent. (c2, c15)"""
        monkeypatch.setattr(swap_cmd, "collect_swap_state", lambda: _UNAVAIL_STATE)
        monkeypatch.setattr(swap_cmd, "read_swap_trend", lambda: _UNAVAIL_TREND)

        rc = main(["swap", "status"])

        assert rc == 0
        captured = capsys.readouterr()
        assert "unavailable" in captured.out.lower()
        assert "Traceback" not in captured.out
        assert "Traceback" not in captured.err

    def test_status_unavailable_json_exits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """JSON status with unavailable subsystems is valid JSON and exits 0. (c2)"""
        monkeypatch.setattr(swap_cmd, "collect_swap_state", lambda: _UNAVAIL_STATE)
        monkeypatch.setattr(swap_cmd, "read_swap_trend", lambda: _UNAVAIL_TREND)

        rc = main(["swap", "status", "--json"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["state"]["available"] is False
        assert payload["trend"]["available"] is False

    def test_history_empty_exits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``spark swap history`` exits 0 with a helpful message when no history exists. (c15)"""
        monkeypatch.setattr(swap_cmd, "query", lambda *a, **k: [])

        rc = main(["swap", "history"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "no history recorded yet" in out
        assert "Traceback" not in out

    def test_history_empty_json_exits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """JSON history with no rows is valid JSON with empty top list. (c15)"""
        monkeypatch.setattr(swap_cmd, "query", lambda *a, **k: [])

        rc = main(["swap", "history", "--json"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["top"] == []

    def test_no_traceback_on_any_read_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No read verb emits a Python traceback even when subsystems are unavailable. (c2)"""
        monkeypatch.setattr(swap_cmd, "collect_swap_state", lambda: _UNAVAIL_STATE)
        monkeypatch.setattr(swap_cmd, "read_swap_trend", lambda: _UNAVAIL_TREND)
        monkeypatch.setattr(swap_cmd, "query", lambda *a, **k: [])

        for argv in (
            ["swap", "overview"],
            ["swap", "overview", "--json"],
            ["swap", "status"],
            ["swap", "status", "--json"],
            ["swap", "history"],
            ["swap", "history", "--json"],
        ):
            rc = main(argv)
            captured = capsys.readouterr()
            assert rc == 0, f"Expected exit 0 for {argv}, got {rc}"
            assert "Traceback" not in captured.out, f"Traceback in stdout for {argv}"
            assert "Traceback" not in captured.err, f"Traceback in stderr for {argv}"
