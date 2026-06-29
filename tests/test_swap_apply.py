"""Unit tests for the privileged swap-grow executor in :mod:`spark.swap.apply`.

These exercise the SAFETY-CRITICAL guard logic — the root check, the ``--apply``
gate, and abort-on-failure — without ever running a real swap command or
requiring root. A fake runner records every ``(name, args)`` it is asked to run,
and a fake ``geteuid`` simulates root / non-root, so the tests assert *which*
mutating commands would (or would not) have fired by inspecting the recorder.

GrowPlan inputs are built from the real planner (:func:`build_grow_plan`) fed a
hand-built ``state`` dict, keeping these tests honest against the actual plan
shape rather than a hand-faked one.
"""

from __future__ import annotations

import pytest

from spark.cli._errors import CliError
from spark.swap.apply import apply_grow_plan
from spark.swap.grow import build_grow_plan

GiB = 1024**3


def _state(**overrides: object) -> dict:
    """A healthy, file-backed-swap baseline; each call returns a fresh dict."""
    state: dict = {
        "available": True,
        "swappiness": 60,
        "mem": {
            "total_bytes": 128 * GiB,
            "available_bytes": 100 * GiB,
            "free_bytes": 90 * GiB,
            "used_bytes": 28 * GiB,
            "used_pct": 21.9,
            "swap_total_bytes": 8 * GiB,
            "swap_free_bytes": 8 * GiB,
            "swap_used_bytes": 0,
            "swap_used_pct": 0.0,
        },
        "devices": [
            {
                "name": "/swap.img",
                "type": "file",
                "size_bytes": 8 * GiB,
                "used_bytes": 0,
                "priority": -2,
            },
        ],
        "backing": {
            "swapfile": "/swap.img",
            "fs_type": "ext4",
            "mount": "/",
            "free_bytes": 500 * GiB,
        },
    }
    for key, value in overrides.items():
        state[key] = value
    return state


class FakeRunner:
    """Records every call; returns ``(0, "")`` unless told to fail / be absent.

    ``fail_on`` names a tool that returns a non-zero ``(1, output)``; ``absent``
    names tools that return ``None`` (mirroring ``run_capture`` when a tool is
    not installed / cannot launch).
    """

    def __init__(self, fail_on: str | None = None, absent=None) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._fail_on = fail_on
        self._absent = set(absent or ())

    def __call__(self, name, args):
        self.calls.append((name, list(args)))
        if name in self._absent:
            return None
        if self._fail_on is not None and name == self._fail_on:
            return (1, f"{name}: simulated failure (ENOMEM)")
        return (0, "")

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class CountingEuid:
    """A ``geteuid`` stub that records how many times it was consulted."""

    def __init__(self, value: int) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        return self.value


def _root() -> int:
    return 0


def _nonroot() -> int:
    return 1000


# --- criterion 1: apply as non-root refuses and mutates nothing -----------


def test_apply_as_nonroot_raises_and_runs_nothing() -> None:
    plan = build_grow_plan(32 * GiB, state=_state())
    runner = FakeRunner()
    euid = CountingEuid(1000)

    with pytest.raises(CliError) as excinfo:
        apply_grow_plan(plan, apply=True, runner=runner, geteuid=euid)

    err = excinfo.value
    assert err.code == 2  # environment error
    assert "sudo" in err.remediation
    assert "--apply" in err.remediation
    assert runner.calls == []  # ZERO calls: nothing was mutated
    assert euid.calls >= 1  # privilege was actually checked


# --- criterion 2: dry-run executes nothing regardless of euid -------------


@pytest.mark.parametrize("euid_value", [0, 1000])
def test_dry_run_executes_nothing_and_previews_plan(euid_value: int) -> None:
    # A state whose used-swap exceeds available RAM produces an ENOMEM warning,
    # letting us assert warnings flow through into the preview.
    state = _state()
    state["mem"]["available_bytes"] = 2 * GiB
    state["mem"]["swap_used_bytes"] = 6 * GiB
    plan = build_grow_plan(32 * GiB, state=state)
    runner = FakeRunner()
    euid = CountingEuid(euid_value)

    result = apply_grow_plan(plan, apply=False, runner=runner, geteuid=euid)

    assert runner.calls == []  # ZERO calls, regardless of euid
    assert euid.calls == 0  # dry-run does not even consult privilege
    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["swapfile"] == "/swap.img"
    assert result["target_size_bytes"] == 32 * GiB
    assert result["persistent"] is True
    assert result["warnings"] == plan.warnings
    assert result["warnings"]  # non-empty: the ENOMEM hazard warning

    preview = [(s["desc"], s["argv"]) for s in result["steps"]]
    plan_steps = [(s["desc"], s["argv"]) for s in plan.steps]
    assert preview == plan_steps


def test_apply_defaults_to_dry_run() -> None:
    plan = build_grow_plan(32 * GiB, state=_state())
    runner = FakeRunner()

    result = apply_grow_plan(plan, runner=runner, geteuid=_root)

    assert result["dry_run"] is True
    assert result["applied"] is False
    assert runner.calls == []


# --- criterion 3: apply as root runs every non-empty step in order --------


def test_apply_as_root_runs_all_steps_in_order_permanent() -> None:
    target = 32 * GiB
    plan = build_grow_plan(target, state=_state())
    runner = FakeRunner()

    result = apply_grow_plan(plan, apply=True, runner=runner, geteuid=_root)

    assert result["applied"] is True
    assert result["dry_run"] is False

    # The five resize steps, in exact order ...
    assert runner.calls[:5] == [
        ("swapoff", ["/swap.img"]),
        ("fallocate", ["-l", str(target), "/swap.img"]),
        ("chmod", ["600", "/swap.img"]),
        ("mkswap", ["/swap.img"]),
        ("swapon", ["/swap.img"]),
    ]
    # ... then the permanent fstab-ensure step IS executed.
    assert len(runner.calls) == 6
    fstab_name, fstab_args = runner.calls[5]
    assert fstab_name == "sh"
    assert any("/etc/fstab" in tok for tok in fstab_args)

    # The executed report mirrors what ran, in order.
    ran_argvs = [e["argv"] for e in result["executed"] if e["ran"]]
    assert ran_argvs[0] == ["swapoff", "/swap.img"]
    assert any(a and a[0] == "sh" for a in ran_argvs)  # fstab among executed argvs
    assert all(e["returncode"] == 0 for e in result["executed"] if e["ran"])


def test_apply_as_root_ephemeral_runs_no_fstab_step() -> None:
    plan = build_grow_plan(16 * GiB, state=_state(), ephemeral=True)
    runner = FakeRunner()

    result = apply_grow_plan(plan, apply=True, runner=runner, geteuid=_root)

    assert result["applied"] is True
    # Only the five resize steps; NO fstab step.
    assert runner.names == ["swapoff", "fallocate", "chmod", "mkswap", "swapon"]
    assert not any(name == "sh" for name in runner.names)
    assert all("fstab" not in " ".join(args) for _, args in runner.calls)


def test_note_step_recorded_but_not_run() -> None:
    # With fstab already present the planner emits a NOTE (empty argv), not an
    # edit step — the executor must record it without calling the runner.
    state = _state()
    state["fstab_present"] = True
    plan = build_grow_plan(16 * GiB, state=state)
    runner = FakeRunner()

    result = apply_grow_plan(plan, apply=True, runner=runner, geteuid=_root)

    # The note is skipped; only the five resize steps actually run.
    assert runner.names == ["swapoff", "fallocate", "chmod", "mkswap", "swapon"]
    notes = [e for e in result["executed"] if not e["ran"]]
    assert len(notes) == 1
    assert notes[0]["argv"] == []
    assert notes[0]["returncode"] is None
    assert "fstab" in notes[0]["desc"].lower()


# --- criterion 4: abort on the first failing step -------------------------


def test_abort_on_failure_does_not_run_later_steps() -> None:
    # swapoff fails (e.g. ENOMEM under pressure) — we must NOT proceed to the
    # destructive fallocate/mkswap/swapon on the live swapfile.
    plan = build_grow_plan(32 * GiB, state=_state())
    runner = FakeRunner(fail_on="swapoff")

    with pytest.raises(CliError) as excinfo:
        apply_grow_plan(plan, apply=True, runner=runner, geteuid=_root)

    err = excinfo.value
    assert err.code == 2
    assert "swap grow step failed" in err.message
    # swapoff was attempted ...
    assert runner.names == ["swapoff"]
    # ... and NONE of the later (destructive) steps ran.
    for later in ("fallocate", "mkswap", "swapon", "chmod"):
        assert later not in runner.names


def test_abort_when_a_later_step_fails() -> None:
    # A failure partway through (mkswap) still aborts before swapon.
    plan = build_grow_plan(32 * GiB, state=_state())
    runner = FakeRunner(fail_on="mkswap")

    with pytest.raises(CliError) as excinfo:
        apply_grow_plan(plan, apply=True, runner=runner, geteuid=_root)

    assert excinfo.value.code == 2
    assert runner.names == ["swapoff", "fallocate", "chmod", "mkswap"]
    assert "swapon" not in runner.names


def test_missing_tool_aborts_as_env_error() -> None:
    # run_capture returns None when a tool is absent; treat that as a failure
    # and abort rather than silently skipping a step.
    plan = build_grow_plan(32 * GiB, state=_state())
    runner = FakeRunner(absent=["fallocate"])

    with pytest.raises(CliError) as excinfo:
        apply_grow_plan(plan, apply=True, runner=runner, geteuid=_root)

    assert excinfo.value.code == 2
    assert runner.names == ["swapoff", "fallocate"]
    assert "mkswap" not in runner.names
