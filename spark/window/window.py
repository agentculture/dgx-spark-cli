"""``spark window`` — store / lock / restore / show a benchmark clock window.

The GB10 has no ``jetson_clocks``. A lock is only offered when ``nvidia-smi``
reports supported clocks (``Supported Clocks`` not ``N/A``); otherwise
``supported`` is false and ``lock`` exits 2 ``unsupported`` (frame park v2).
No clock is ever changed outside ``lock``/``restore``.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from spark.window import grant
from spark.window.ctx import Ctx


def supported(ctx: Ctx) -> bool:
    """True only if nvidia-smi lists real supported clocks (capability check)."""
    if ctx.which("nvidia-smi") is None:
        return False
    rc, out, _ = ctx.proc(["nvidia-smi", "-q", "-d", "SUPPORTED_CLOCKS"], None)
    if rc != 0:
        return False
    seen = False
    for line in out.splitlines():
        if "Supported Clocks" in line:
            seen = True
            if "N/A" in line.split(":", 1)[-1]:
                return False
            continue
        if seen and "MHz" in line:
            return True
    return False


def _load(ctx: Ctx) -> Optional[dict]:
    try:
        return json.loads(ctx.state_file.read_text())
    except (OSError, ValueError):
        return None


def _save(ctx: Ctx, state: dict) -> None:
    ctx.state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = ctx.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, ctx.state_file)


def _clear(ctx: Ctx) -> None:
    try:
        ctx.state_file.unlink()
    except OSError:
        pass


def snapshot(ctx: Ctx) -> dict:
    """Current state object (the ``show`` shape); unreadable fields are null."""
    pw = (ctx.power() or {}).get("data") or {}
    th = (ctx.thermal() or {}).get("data") or {}
    temps = {s["name"]: s["temp_c"] for s in th.get("sensors", []) if "name" in s}
    state = _load(ctx)
    return {
        "ok": True,
        "reason": None,
        "active": bool(state and state.get("locked")),
        "stored": state is not None,
        "supported": supported(ctx),
        "power_mode": None,
        "clocks": {
            "gpu_mhz": pw.get("clocks_sm_mhz"),
            "gpu_min_mhz": None,
            "gpu_max_mhz": pw.get("clocks_max_sm_mhz"),
            "emc_mhz": None,
        },
        "fan_profile": None,
        "temps_c": temps,
        "detail": {
            "pstate": pw.get("pstate"),
            "power_draw_w": pw.get("power_draw_w"),
            "state_file": str(ctx.state_file),
        },
    }


def _result(ctx: Ctx, action: str, ok: bool, reason: Optional[str], code: int, **extra) -> dict:
    obj = snapshot(ctx)
    obj.update(ok=ok, reason=reason, action=action, exit=code, **extra)
    return obj


def show(ctx: Ctx) -> dict:
    obj = snapshot(ctx)
    obj["exit"] = 0
    return obj


def store(ctx: Ctx) -> dict:
    state = _load(ctx)
    if state and state.get("locked"):
        return _result(ctx, "store", False, "window_active", 1)
    pre = snapshot(ctx)
    _save(ctx, {"locked": False, "clocks": pre["clocks"], "temps_c": pre["temps_c"]})
    return _result(ctx, "store", True, None, 0)


def lock(ctx: Ctx) -> dict:
    if not supported(ctx):
        return _result(ctx, "lock", False, "unsupported", 2)
    state = _load(ctx)
    if state is None:
        return _result(ctx, "lock", False, "no_stored_state", 1)
    if ctx.which("nvidia-smi") is None:
        return _result(ctx, "lock", False, "tool_missing", 2)
    if grant.detect_window(grant.listing(ctx)) is None:
        return _result(ctx, "lock", False, "not_granted", 2)
    top = state["clocks"].get("gpu_max_mhz")
    if top is None:
        return _result(ctx, "lock", False, "failed", 2, stderr="max clock unreadable")
    rc, out, err = ctx.proc(["sudo", "-n", grant.NVIDIA_SMI, "-lgc", f"{top},{top}"], None)
    if rc != 0:
        return _result(ctx, "lock", False, "failed", 2, stderr=(out + err).strip())
    state["locked"] = True
    _save(ctx, state)
    return _result(ctx, "lock", True, None, 0)


def restore(ctx: Ctx) -> dict:
    state = _load(ctx)
    if state is None:
        return _result(ctx, "restore", True, None, 0, restored=False)
    if state.get("locked"):
        if grant.detect_window(grant.listing(ctx)) is None:
            return _result(ctx, "restore", False, "not_granted", 2)
        rc, out, err = ctx.proc(["sudo", "-n", grant.NVIDIA_SMI, "-rgc"], None)
        if rc != 0:
            return _result(ctx, "restore", False, "failed", 2, stderr=(out + err).strip())
    _clear(ctx)
    return _result(ctx, "restore", True, None, 0, restored=True)
