"""Unit tests for the pure swap-grow planner in :mod:`spark.swap.grow`.

The planner builds a destructive (swapoff-based) resize *plan* but never
executes it, so every test below feeds a synthetic ``state`` dict and asserts
on the returned :class:`GrowPlan` (or the raised :class:`CliError`). No root,
no subprocess, no real filesystem access is required to run these.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import spark.swap.grow as grow
from spark.cli._errors import CliError
from spark.swap.grow import GrowPlan, build_grow_plan

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


# --- criterion 1: exact ordered in-place resize plan ----------------------


def test_builds_exact_ordered_resize_plan() -> None:
    target = 32 * GiB
    plan = build_grow_plan(target, state=_state())

    assert isinstance(plan, GrowPlan)
    assert plan.swapfile == "/swap.img"
    assert plan.target_size_bytes == target
    assert plan.current_size_bytes == 8 * GiB
    assert plan.persistent is True
    assert plan.warnings == []

    argvs = [step["argv"] for step in plan.steps]
    assert argvs[:5] == [
        ["swapoff", "/swap.img"],
        ["fallocate", "-l", str(target), "/swap.img"],
        ["chmod", "600", "/swap.img"],
        ["mkswap", "/swap.img"],
        ["swapon", "/swap.img"],
    ]

    for step in plan.steps:
        assert set(step.keys()) == {"desc", "argv"}
        assert isinstance(step["desc"], str) and step["desc"]
        assert isinstance(step["argv"], list)
        assert all(isinstance(tok, str) for tok in step["argv"])


def test_permanent_path_ensures_fstab_when_state_cannot_tell() -> None:
    # The documented state schema carries no fstab info, so the planner must
    # include an *ensure* step with a real (non-empty) argv.
    plan = build_grow_plan(32 * GiB, state=_state())
    assert len(plan.steps) == 6
    fstab_step = plan.steps[5]
    assert "fstab" in fstab_step["desc"].lower()
    assert fstab_step["argv"]  # non-empty: an actual ensure command


def test_permanent_fstab_present_is_note_not_edit() -> None:
    state = _state()
    state["fstab_present"] = True
    plan = build_grow_plan(16 * GiB, state=state)

    assert plan.persistent is True
    fstab_step = plan.steps[-1]
    assert "fstab" in fstab_step["desc"].lower()
    assert fstab_step["argv"] == []  # note, not an edit


def test_ephemeral_skips_fstab_and_warns_boot_only() -> None:
    plan = build_grow_plan(16 * GiB, state=_state(), ephemeral=True)

    assert plan.persistent is False
    assert len(plan.steps) == 5  # only the five resize steps
    assert all("fstab" not in step["desc"].lower() for step in plan.steps)
    assert any("boot" in w.lower() for w in plan.warnings)


# --- criterion 2: refuse a non-swapfile setup -----------------------------


def test_refuses_when_no_swapfile_backed_swap() -> None:
    state = _state()
    state["backing"]["swapfile"] = None
    state["devices"] = [
        {
            "name": "/dev/zram0",
            "type": "partition",
            "size_bytes": 8 * GiB,
            "used_bytes": 0,
            "priority": 100,
        }
    ]

    with pytest.raises(CliError) as excinfo:
        build_grow_plan(16 * GiB, state=state)
    assert excinfo.value.code == 1
    assert excinfo.value.remediation


# --- criterion 3: refuse on insufficient disk + purity --------------------


def test_refuses_when_insufficient_disk() -> None:
    state = _state()
    state["backing"]["free_bytes"] = 1 * GiB  # need 32-8 = 24 GiB, only 1 free

    with pytest.raises(CliError) as excinfo:
        build_grow_plan(32 * GiB, state=state)
    assert excinfo.value.code == 2
    assert excinfo.value.remediation


def test_planner_is_pure_never_shells_out() -> None:
    src = Path(grow.__file__).read_text(encoding="utf-8")
    # Pure: builds argv only — it imports nothing that could execute or mutate.
    assert "import subprocess" not in src
    assert "subprocess" not in src
    assert "import os" not in src
    assert "open(" not in src
    # And building a plan returns cleanly without any system access.
    plan = build_grow_plan(16 * GiB, state=_state())
    assert isinstance(plan, GrowPlan)


# --- swapoff-under-pressure (ENOMEM) hazard -------------------------------


def test_enomem_warning_present_when_used_swap_exceeds_available_ram() -> None:
    state = _state()
    state["mem"]["available_bytes"] = 2 * GiB
    state["mem"]["swap_used_bytes"] = 6 * GiB  # used swap cannot fit in RAM

    plan = build_grow_plan(32 * GiB, state=state)
    assert any("swapoff" in w.lower() and "second swapfile" in w.lower() for w in plan.warnings)
    # Hazard is a warning, not a hard block — the resize plan is still returned.
    assert plan.steps[0]["argv"] == ["swapoff", "/swap.img"]


def test_enomem_warning_absent_when_used_swap_fits_in_ram() -> None:
    state = _state()
    state["mem"]["available_bytes"] = 100 * GiB
    state["mem"]["swap_used_bytes"] = 2 * GiB

    plan = build_grow_plan(32 * GiB, state=state)
    assert not any("second swapfile" in w.lower() for w in plan.warnings)


# --- current-size resolution + misc safety --------------------------------


def test_current_size_from_matching_device_and_custom_path() -> None:
    state = _state()
    state["backing"]["swapfile"] = "/mnt/swap.bin"
    state["devices"] = [
        {
            "name": "/mnt/swap.bin",
            "type": "file",
            "size_bytes": 4 * GiB,
            "used_bytes": 0,
            "priority": -2,
        }
    ]

    plan = build_grow_plan(20 * GiB, state=state, swapfile="/mnt/swap.bin")
    assert plan.current_size_bytes == 4 * GiB
    assert plan.steps[0]["argv"] == ["swapoff", "/mnt/swap.bin"]
    assert plan.steps[1]["argv"] == ["fallocate", "-l", str(20 * GiB), "/mnt/swap.bin"]


def test_current_size_zero_for_fresh_file() -> None:
    state = _state()
    state["devices"] = []  # no existing file device yet
    plan = build_grow_plan(16 * GiB, state=state)
    assert plan.current_size_bytes == 0


def test_nonpositive_target_is_user_error() -> None:
    with pytest.raises(CliError) as excinfo:
        build_grow_plan(0, state=_state())
    assert excinfo.value.code == 1


def test_unknown_free_space_warns_not_blocks() -> None:
    state = _state()
    state["backing"]["free_bytes"] = None  # cannot verify
    plan = build_grow_plan(32 * GiB, state=state)
    assert isinstance(plan, GrowPlan)
    assert any(("disk" in w.lower()) or ("free" in w.lower()) for w in plan.warnings)


def test_target_smaller_than_current_is_user_error() -> None:
    # Baseline /swap.img is 8 GiB; a "grow" to 4 GiB would truncate it.
    with pytest.raises(CliError) as excinfo:
        build_grow_plan(4 * GiB, state=_state())
    assert excinfo.value.code == 1
    assert "grow" in str(excinfo.value).lower()


def test_target_equal_to_current_is_user_error() -> None:
    # No-op "grow" to the current 8 GiB size is rejected rather than planned.
    with pytest.raises(CliError) as excinfo:
        build_grow_plan(8 * GiB, state=_state())
    assert excinfo.value.code == 1
