"""Shared plumbing for the host-telemetry leaf verbs.

Every probe verb (``memory``, ``disk``, ``gpu``, ``thermal``, ``network``,
``containers``, ``processes``, ``status``) does the same thing: call a
``collect()`` that returns a report dict, then emit it as text or ``--json``.
:func:`register_probe` wires one up in a couple of lines so each command module
stays a thin shell around its collector.

Probe verbs are *descriptive*: a missing subsystem is reported as
``available: false`` inside the report and still exits 0. ``doctor`` is the
health gate, not these.
"""

from __future__ import annotations

import argparse
from typing import Callable

from spark.cli._output import emit_result
from spark.probe._report import render_report_text

Collector = Callable[[], dict]


def emit_probe(collect: Collector, args: argparse.Namespace) -> int:
    """Run ``collect`` and emit its report to the right stream/format."""
    rep = collect()
    if bool(getattr(args, "json", False)):
        emit_result(rep, json_mode=True)
    else:
        emit_result(render_report_text(rep), json_mode=False)
    return 0


def register_probe(
    sub: argparse._SubParsersAction,
    name: str,
    collect: Collector,
    help_text: str,
) -> argparse.ArgumentParser:
    """Register a ``--json``-supporting leaf verb backed by ``collect``."""
    parser = sub.add_parser(name, help=help_text)
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    # Descriptive verbs must never hard-fail on a stray positional (the same
    # contract `overview` honors). Accept and ignore any extra args so e.g.
    # `spark memory /some/path` still exits 0 rather than erroring.
    parser.add_argument("ignored", nargs="*", help=argparse.SUPPRESS)
    parser.set_defaults(func=lambda args: emit_probe(collect, args))
    return parser
