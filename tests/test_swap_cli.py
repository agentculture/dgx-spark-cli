"""Tests for the ``dgx-spark-cli swap`` noun CLI (t6).

These drive the CLI entry point (:func:`spark.cli.main`) and monkeypatch the
five :mod:`spark.swap` domain functions where they are imported into
:mod:`spark.cli._commands.swap`, so the tests are hermetic (no root, no real
swap device, no history store on disk).
"""

from __future__ import annotations

import json

import pytest

from spark.cli import main
from spark.cli._commands import swap as swap_cmd

_GIB = 1024**3


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _growable_state() -> dict:
    """A swap state with a file-backed device that build_grow_plan accepts."""
    return {
        "available": True,
        "swappiness": 60,
        "mem": {
            "total_bytes": 128 * _GIB,
            "available_bytes": 100 * _GIB,
            "free_bytes": 90 * _GIB,
            "used_bytes": 28 * _GIB,
            "used_pct": 21.8,
            "swap_total_bytes": 8 * _GIB,
            "swap_free_bytes": 8 * _GIB,
            "swap_used_bytes": 0,
            "swap_used_pct": 0.0,
        },
        "devices": [
            {
                "name": "/swap.img",
                "type": "file",
                "size_bytes": 8 * _GIB,
                "used_bytes": 0,
                "priority": -2,
            }
        ],
        "backing": {
            "swapfile": "/swap.img",
            "fs_type": "ext4",
            "mount": "/",
            "free_bytes": 100 * _GIB,
        },
    }


def _unavailable_state() -> dict:
    return {
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


def _trend(series: list | None = None, available: bool = True) -> dict:
    return {
        "available": available,
        "source": "sar" if available else None,
        "series": series or [],
    }


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


def test_swap_no_verb_prints_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["swap"])
    assert rc == 0
    assert "swap" in capsys.readouterr().out.lower()


def test_swap_overview_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["swap", "overview", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "dgx-spark-cli swap"
    assert payload["sections"]


def test_swap_overview_tolerates_stray_positional(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["swap", "overview", "some/bogus/path"])
    assert rc == 0
    assert capsys.readouterr().out  # still describes the noun, exits 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_swap_status_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(swap_cmd, "collect_swap_state", _growable_state)
    monkeypatch.setattr(
        swap_cmd,
        "read_swap_trend",
        lambda: _trend([{"ts": "t0", "swap_used_pct": 1.0, "mem_used_pct": 20.0}]),
    )
    rc = main(["swap", "status", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "swap status"
    assert payload["state"]["available"] is True
    assert payload["trend_summary"]["recent_swap_used_pct"] == 1.0


def test_swap_status_text(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(swap_cmd, "collect_swap_state", _growable_state)
    monkeypatch.setattr(swap_cmd, "read_swap_trend", lambda: _trend([]))
    rc = main(["swap", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "# swap status" in out
    assert "/swap.img" in out


def test_swap_status_unavailable_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(swap_cmd, "collect_swap_state", _unavailable_state)
    monkeypatch.setattr(swap_cmd, "read_swap_trend", lambda: _trend(available=False))
    rc = main(["swap", "status", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"]["available"] is False
    assert payload["trend"]["available"] is False


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_swap_history_empty_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(swap_cmd, "query", lambda *a, **k: [])
    rc = main(["swap", "history", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["top"] == []


def test_swap_history_empty_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(swap_cmd, "query", lambda *a, **k: [])
    rc = main(["swap", "history"])
    assert rc == 0
    assert "no history recorded yet" in capsys.readouterr().out


def test_swap_history_with_rows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    rows = [{"pid": 4242, "comm": "vllm", "peak_swap_kb": 2048, "peak_rss_kb": 4096}]
    captured: dict = {}

    def _fake_query(window_seconds, top_n=10, **k):
        captured["window"] = window_seconds
        captured["top"] = top_n
        return rows

    monkeypatch.setattr(swap_cmd, "query", _fake_query)
    rc = main(["swap", "history", "--window", "2h", "--top", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pid 4242 vllm" in out
    assert captured["window"] == 7200  # 2h parsed to seconds
    assert captured["top"] == 5


# ---------------------------------------------------------------------------
# sample
# ---------------------------------------------------------------------------


def test_swap_sample_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(swap_cmd, "record", lambda *a, **k: 42)
    rc = main(["swap", "sample", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["samples_written"] == 42


def test_swap_sample_text(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(swap_cmd, "record", lambda *a, **k: 7)
    rc = main(["swap", "sample"])
    assert rc == 0
    assert "wrote 7" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# grow — dry-run (default) mutates nothing
# ---------------------------------------------------------------------------


def _spy_apply(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace apply_grow_plan with a spy that records the apply= flag."""
    calls: dict = {}

    def _fake(plan, *, apply=False, runner=None, geteuid=None):
        calls["apply"] = apply
        calls["runner"] = runner
        return {
            "applied": apply,
            "dry_run": not apply,
            "swapfile": plan.swapfile,
            "target_size_bytes": plan.target_size_bytes,
            "persistent": plan.persistent,
            "steps": [{"desc": s["desc"], "argv": list(s.get("argv") or [])} for s in plan.steps],
            "warnings": list(plan.warnings),
        }

    monkeypatch.setattr(swap_cmd, "apply_grow_plan", _fake)
    return calls


def test_swap_grow_dry_run_mutates_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(swap_cmd, "collect_swap_state", _growable_state)
    calls = _spy_apply(monkeypatch)
    rc = main(["swap", "grow", "32G"])
    assert rc == 0
    captured = capsys.readouterr()
    assert calls["apply"] is False  # default = dry-run, no mutation
    assert "WARNING: this will modify swap" in captured.err
    assert captured.out  # structured plan result on stdout


def test_swap_grow_dry_run_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(swap_cmd, "collect_swap_state", _growable_state)
    _spy_apply(monkeypatch)
    rc = main(["swap", "grow", "32GiB", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    assert payload["target_size_bytes"] == 32 * _GIB


def test_swap_grow_apply_non_root_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import os

    if os.geteuid() == 0:  # pragma: no cover - CI runs unprivileged
        pytest.skip("must run as non-root to observe the privilege error")
    monkeypatch.setattr(swap_cmd, "collect_swap_state", _growable_state)
    # Real apply_grow_plan + real os.geteuid: --apply when non-root raises CliError(2).
    rc = main(["swap", "grow", "32G", "--apply"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err
    assert "--apply" in err
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# failure modes — error: / hint: shape, never a traceback (covers h13)
# ---------------------------------------------------------------------------


def test_swap_grow_invalid_size_errors(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["swap", "grow", "not-a-size"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err
    assert "Traceback" not in err


def test_swap_history_invalid_window_errors(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["swap", "history", "--window", "soon"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err
    assert "Traceback" not in err


def test_swap_grow_refuses_without_file_swap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    # build_grow_plan raises CliError(1) when there is no file-backed swap;
    # the CLI must surface it as error:/hint:, no traceback.
    monkeypatch.setattr(swap_cmd, "collect_swap_state", _unavailable_state)
    rc = main(["swap", "grow", "32G"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# size / duration parsers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("32G", 32 * _GIB),
        ("32GiB", 32 * _GIB),
        ("32GB", 32 * _GIB),
        ("16g", 16 * _GIB),
        ("32000000000", 32_000_000_000),
        ("512M", 512 * 1024**2),
        ("1T", 1024**4),
    ],
)
def test_parse_size_accepts_human_forms(text: str, expected: int) -> None:
    assert swap_cmd._parse_size(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "-5G", "G", "3.2.1G"])
def test_parse_size_rejects_garbage(bad: str) -> None:
    from spark.cli._errors import CliError

    with pytest.raises(CliError) as exc:
        swap_cmd._parse_size(bad)
    assert exc.value.code == 1


@pytest.mark.parametrize(
    "text,expected",
    [("1h", 3600), ("30m", 1800), ("2d", 172800), ("90", 90), ("45s", 45)],
)
def test_parse_duration(text: str, expected: int) -> None:
    assert swap_cmd._parse_duration(text) == expected
