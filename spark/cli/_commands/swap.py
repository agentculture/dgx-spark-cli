"""``dgx-spark-cli swap`` — swap inspection, history, and the guarded grow.

The ``swap`` noun is the CLI shell around the five :mod:`spark.swap` domain
modules. It is a thin layer: parse args, call a domain function, route the
structured result to stdout and any diagnostics/warnings to stderr (the strict
:mod:`spark.cli._output` split), and translate failures into :class:`CliError`.

Verbs:

* ``status``   — read-only swap/memory snapshot + a short sar trend summary
* ``grow``     — the guarded mutator (dry-run by default; ``--apply`` runs it)
* ``history``  — top per-process swap/RSS consumers over a recent window
* ``sample``   — take one snapshot for the history store (timer-driven)
* ``overview`` — describe the swap surface (descriptive; tolerates a stray arg)

``grow`` is the only mutator and it is dry-run by default: without ``--apply``
it previews the exact step plan and changes nothing. ``--apply`` requires root
(the executor raises ``CliError`` exit 2 with a ``sudo`` hint otherwise).
"""

from __future__ import annotations

import argparse
import re

from spark.cli._errors import EXIT_USER_ERROR, CliError
from spark.cli._output import emit_diagnostic, emit_result, render_sections
from spark.probe._run import run_capture
from spark.swap.apply import apply_grow_plan
from spark.swap.grow import build_grow_plan
from spark.swap.history import query, record
from spark.swap.sar import read_swap_trend
from spark.swap.state import collect_swap_state

# Real grows do destructive, slow work (swapoff/mkswap on a large file); the
# default 5s run_capture timeout can abort them, so --apply uses a longer one.
_APPLY_STEP_TIMEOUT = 600.0


# ---------------------------------------------------------------------------
# Small parsers (CLI-layer concern — the domain takes bytes / seconds)
# ---------------------------------------------------------------------------

# digits (optional decimal) + optional binary unit (K/M/G/T/P) + optional i/B.
# Suffixes are binary (1024-based): 32G == 32GiB == 32GB. A bare number is bytes.
# All quantifiers are BOUNDED ({1,20}/{0,6}/{0,4}) so the pattern cannot backtrack
# super-linearly (ReDoS); callers strip() first, so no leading/trailing \s* is needed.
_SIZE_RE = re.compile(r"^(\d{1,20}(?:\.\d{1,6})?)\s{0,4}([KMGTPkmgtp]?)[iI]?[bB]?$")
_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}

_DURATION_RE = re.compile(r"^(\d{1,20}(?:\.\d{1,6})?)\s{0,4}([smhdSMHD]?)$")
_DURATION_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_size(text: str) -> int:
    """Parse a human size (``32G``/``32GiB``/``16g``/``32000000000``) into bytes.

    Suffixes are binary (1024-based) and case-insensitive; a bare number is
    bytes. Raises :class:`CliError` (exit 1) on an unparseable or non-positive
    value.
    """
    match = _SIZE_RE.match((text or "").strip())
    if match is None:
        raise CliError(
            EXIT_USER_ERROR,
            f"invalid size: {text!r}",
            remediation="pass a size like 32G, 32GiB, 16g, or a raw byte count.",
        )
    value = int(float(match.group(1)) * _SIZE_UNITS[match.group(2).upper()])
    if value <= 0:
        raise CliError(
            EXIT_USER_ERROR,
            f"invalid size: {text!r} resolves to {value} bytes (must be positive).",
            remediation="pass a positive size like 32G.",
        )
    return value


def _parse_duration(text: str) -> int:
    """Parse a duration (``1h``/``30m``/``2d``/``3600``) into whole seconds.

    Suffixes are case-insensitive; a bare number is seconds. Raises
    :class:`CliError` (exit 1) on an unparseable or non-positive value.
    """
    match = _DURATION_RE.match((text or "").strip())
    if match is None:
        raise CliError(
            EXIT_USER_ERROR,
            f"invalid window: {text!r}",
            remediation="pass a duration like 1h, 30m, 2d, or a raw second count.",
        )
    seconds = int(float(match.group(1)) * _DURATION_UNITS[match.group(2).lower()])
    if seconds <= 0:
        raise CliError(
            EXIT_USER_ERROR,
            f"invalid window: {text!r} resolves to {seconds}s (must be positive).",
            remediation="pass a positive duration like 1h.",
        )
    return seconds


def _json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _fmt_bytes(num_bytes: object) -> str:
    """Render a byte count as a human GiB/MiB string (None -> 'unknown')."""
    if num_bytes is None:
        return "unknown"
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return str(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024.0 or unit == "PiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PiB"  # pragma: no cover


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------

_OVERVIEW_SECTIONS = [
    {
        "title": "What",
        "items": [
            "Swap inspection, per-process history, and the guarded swap grow.",
            "Read verbs are descriptive (exit 0 even when a subsystem is absent); "
            "grow is the only mutator and is dry-run unless --apply is passed.",
        ],
    },
    {
        "title": "Verbs",
        "items": [
            "status — swap/memory snapshot + a short sar trend summary",
            "grow <size> [--apply] [--ephemeral] — resize the swapfile (dry-run default)",
            "history [--window DUR] [--top N] — top per-process swap/RSS consumers",
            "sample — take one snapshot for the history store (timer-driven)",
            "overview — this description",
        ],
    },
]


def _overview_payload() -> dict:
    return {"subject": "dgx-spark-cli swap", "sections": _OVERVIEW_SECTIONS}


def cmd_overview(args: argparse.Namespace) -> int:
    # `target` is accepted and ignored: descriptive verbs must never hard-fail
    # on a stray positional (the overview contract). Always exits 0.
    if _json(args):
        emit_result(_overview_payload(), json_mode=True)
    else:
        emit_result(render_sections("dgx-spark-cli swap", _OVERVIEW_SECTIONS), json_mode=False)
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _trend_summary(trend: dict) -> dict:
    """Reduce a sar trend series to {recent, avg, samples} (None when empty)."""
    series = trend.get("series") or []
    pcts = [s["swap_used_pct"] for s in series if isinstance(s.get("swap_used_pct"), (int, float))]
    recent = pcts[-1] if pcts else None
    avg = round(sum(pcts) / len(pcts), 2) if pcts else None
    return {"recent_swap_used_pct": recent, "avg_swap_used_pct": avg, "samples": len(pcts)}


def _mem_section(mem: dict) -> dict:
    return {
        "title": "Memory",
        "items": [
            f"total: {_fmt_bytes(mem.get('total_bytes'))}",
            f"available: {_fmt_bytes(mem.get('available_bytes'))}",
            f"used: {mem.get('used_pct', 0.0)}%",
        ],
    }


def _swap_section(state: dict, mem: dict) -> dict:
    return {
        "title": "Swap",
        "items": [
            f"total: {_fmt_bytes(mem.get('swap_total_bytes'))}",
            f"used: {_fmt_bytes(mem.get('swap_used_bytes'))} ({mem.get('swap_used_pct', 0.0)}%)",
            f"swappiness: {state.get('swappiness')}",
            f"devices: {len(state.get('devices') or [])}",
        ],
    }


def _devices_section(devices: list) -> dict | None:
    """Render the per-device section, or None when there are no devices."""
    if not devices:
        return None
    items = []
    for dev in devices:
        size = dev.get("size_bytes") or 0
        used = dev.get("used_bytes") or 0
        pct = round(used / size * 100, 1) if size else 0.0
        items.append(
            f"{dev.get('name')} ({dev.get('type')}): "
            f"{_fmt_bytes(size)} used {pct}% prio {dev.get('priority')}"
        )
    return {"title": "Devices", "items": items}


def _trend_section(trend: dict, summary: dict) -> dict:
    if not trend.get("available"):
        return {"title": "Trend", "items": ["unavailable (no sysstat/sar history)"]}
    return {
        "title": "Trend",
        "items": [
            f"source: {trend.get('source')}",
            f"recent swap used: {summary['recent_swap_used_pct']}%",
            f"avg swap used: {summary['avg_swap_used_pct']}%",
            f"samples: {summary['samples']}",
        ],
    }


def _status_sections(state: dict, trend: dict, summary: dict) -> list:
    if not state.get("available"):
        sections = [{"title": "Swap", "items": ["unavailable (could not read /proc swap sources)"]}]
    else:
        mem = state.get("mem") or {}
        sections = [_mem_section(mem), _swap_section(state, mem)]
        devices_section = _devices_section(state.get("devices") or [])
        if devices_section is not None:
            sections.append(devices_section)
    sections.append(_trend_section(trend, summary))
    return sections


def cmd_status(args: argparse.Namespace) -> int:
    state = collect_swap_state()
    trend = read_swap_trend()
    summary = _trend_summary(trend)
    if _json(args):
        emit_result(
            {"subject": "swap status", "state": state, "trend": trend, "trend_summary": summary},
            json_mode=True,
        )
    else:
        emit_result(
            render_sections("swap status", _status_sections(state, trend, summary)),
            json_mode=False,
        )
    return 0


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def cmd_history(args: argparse.Namespace) -> int:
    window_seconds = _parse_duration(getattr(args, "window", None) or "1h")
    top_n = int(getattr(args, "top", 10) or 10)
    rows = query(window_seconds, top_n)
    if _json(args):
        emit_result(
            {"subject": "swap history", "window_seconds": window_seconds, "top": rows},
            json_mode=True,
        )
    elif not rows:
        emit_result(
            "swap history: no history recorded yet "
            "(run 'dgx-spark-cli swap sample', or schedule it on a timer)",
            json_mode=False,
        )
    else:
        items = [
            f"pid {r['pid']} {r['comm']}: peak swap {_fmt_bytes(r['peak_swap_kb'] * 1024)}, "
            f"peak rss {_fmt_bytes(r['peak_rss_kb'] * 1024)}"
            for r in rows
        ]
        sections = [{"title": f"Top consumers (last {window_seconds}s)", "items": items}]
        emit_result(render_sections("swap history", sections), json_mode=False)
    return 0


# ---------------------------------------------------------------------------
# sample
# ---------------------------------------------------------------------------


def cmd_sample(args: argparse.Namespace) -> int:
    written = record()
    if _json(args):
        emit_result({"subject": "swap sample", "samples_written": written}, json_mode=True)
    else:
        emit_result(f"swap sample: wrote {written} process sample(s)", json_mode=False)
    return 0


# ---------------------------------------------------------------------------
# grow (the guarded mutator)
# ---------------------------------------------------------------------------


def _emit_grow_result(result: dict, json_mode: bool) -> None:
    """Emit the structured grow result to stdout (full dict in JSON, summary text)."""
    if json_mode:
        emit_result(result, json_mode=True)
        return
    if result.get("dry_run"):
        sections = [
            {
                "title": "Planned (dry-run — nothing changed)",
                "items": [
                    f"swapfile: {result.get('swapfile')}",
                    f"target size: {_fmt_bytes(result.get('target_size_bytes'))}",
                    f"persistent: {result.get('persistent')}",
                    f"steps: {len(result.get('steps') or [])} (pass --apply to execute)",
                ],
            }
        ]
        emit_result(render_sections("swap grow", sections), json_mode=False)
        return
    items = []
    for step in result.get("executed") or []:
        if step.get("ran"):
            items.append(f"ran: {step['desc']} (rc {step['returncode']})")
        else:
            items.append(f"note: {step['desc']}")
    sections = [{"title": "Executed", "items": items or ["nothing to run"]}]
    emit_result(render_sections("swap grow (applied)", sections), json_mode=False)


def cmd_grow(args: argparse.Namespace) -> int:
    size_bytes = _parse_size(args.size)
    state = collect_swap_state()
    # build_grow_plan raises CliError (exit 1/2) on an unsafe request; let it
    # propagate so _dispatch renders the error: / hint: shape.
    plan = build_grow_plan(size_bytes, state=state, ephemeral=bool(args.ephemeral))
    json_mode = _json(args)

    # Always surface the plan's hazard warnings (e.g. swapoff ENOMEM) on stderr,
    # for both the dry-run and the --apply path.
    for warning in plan.warnings:
        emit_diagnostic(warning)

    if args.apply:
        # apply_grow_plan raises CliError(2) when not root — propagate it.
        result = apply_grow_plan(
            plan,
            apply=True,
            runner=lambda name, a: run_capture(name, a, timeout=_APPLY_STEP_TIMEOUT),
        )
    else:
        # Dry-run (default): preview the exact step plan on stderr, structured
        # result on stdout, mutate nothing.
        result = apply_grow_plan(plan, apply=False)
        emit_diagnostic("WARNING: this will modify swap (dry-run — nothing changed; pass --apply)")
        for step in result.get("steps") or []:
            argv = " ".join(step["argv"]) if step.get("argv") else "(note — nothing to run)"
            emit_diagnostic(f"  - {step['desc']}: {argv}")

    _emit_grow_result(result, json_mode)
    return 0


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def _no_verb(args: argparse.Namespace) -> int:
    return cmd_overview(args)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")


def _add_ignored(parser: argparse.ArgumentParser) -> None:
    # Descriptive verbs must never hard-fail on a stray positional.
    parser.add_argument("ignored", nargs="*", help=argparse.SUPPRESS)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("swap", help="Swap inspection, history, and the guarded grow.")
    _add_json(p)
    p.set_defaults(func=_no_verb, json=False)
    noun = p.add_subparsers(dest="swap_command", parser_class=type(p))

    ov = noun.add_parser("overview", help="Describe the swap surface.")
    _add_json(ov)
    ov.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes the swap noun itself.",
    )
    _add_ignored(ov)
    ov.set_defaults(func=cmd_overview)

    status = noun.add_parser("status", help="Swap/memory snapshot + a short sar trend summary.")
    _add_json(status)
    _add_ignored(status)
    status.set_defaults(func=cmd_status)

    history = noun.add_parser("history", help="Top per-process swap/RSS consumers (recent window).")
    _add_json(history)
    history.add_argument("--window", help="History window (e.g. 1h, 30m, 2d; default 1h).")
    history.add_argument("--top", type=int, default=10, help="Max consumers to show (default 10).")
    history.set_defaults(func=cmd_history)

    sample = noun.add_parser("sample", help="Take one snapshot for the history store.")
    _add_json(sample)
    sample.set_defaults(func=cmd_sample)

    grow = noun.add_parser("grow", help="Resize the swapfile (dry-run unless --apply).")
    _add_json(grow)
    grow.add_argument("size", help="Target swap size (e.g. 32G, 32GiB, or a raw byte count).")
    grow.add_argument(
        "--apply",
        action="store_true",
        help="Execute the grow (requires root); without it, dry-run only.",
    )
    grow.add_argument(
        "--ephemeral",
        action="store_true",
        help="Skip the persistent fstab entry (this boot only).",
    )
    grow.set_defaults(func=cmd_grow)
