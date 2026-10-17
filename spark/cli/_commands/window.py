"""``spark window|drop-caches|grant-permissions`` — the machine-CLI window contract.

Result objects always go to stdout (even when ``ok`` is false) so a caller
reads one JSON object; the exit code is 0/1/2 per the contract.
"""

from __future__ import annotations

import argparse
import sys

from spark.cli._output import emit_result
from spark.window import grant
from spark.window import window as win
from spark.window.ctx import make_ctx


def _emit(obj: dict, args: argparse.Namespace) -> int:
    code = int(obj.pop("exit", 0))
    if getattr(args, "json", False):
        emit_result(obj, json_mode=True)
    else:
        lines = [f"{k}: {v}" for k, v in obj.items() if k not in ("temps_c", "detail", "clocks")]
        if "clocks" in obj:
            lines.append(f"clocks: {obj['clocks']}")
        emit_result("\n".join(lines), json_mode=False)
    return code


def _cmd_window(verb):
    def run(args: argparse.Namespace) -> int:
        return _emit(getattr(win, verb)(make_ctx()), args)

    return run


def _cmd_drop(args: argparse.Namespace) -> int:
    return _emit(grant.drop_caches(make_ctx()), args)


def _confirm(rules: list[str]) -> bool:
    sys.stderr.write("Install these NOPASSWD sudo rules (not full sudo)?\n")
    for rule in rules:
        sys.stderr.write(f"  {rule}\n")
    sys.stderr.write("[y/N] ")
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def _cmd_grant(args: argparse.Namespace) -> int:
    ctx = make_ctx()
    sup = win.supported(ctx)
    if args.check:
        obj = grant.check(ctx, sup)
        obj["exit"] = 0
    else:
        obj = grant.install(ctx, sup, args.yes, _confirm)
    return _emit(obj, args)


def register(sub: argparse._SubParsersAction) -> None:
    w = sub.add_parser("window", help="Benchmark clock window: show|store|lock|restore.")
    wsub = w.add_subparsers(dest="window_verb", parser_class=type(w))
    for verb, text in (
        ("show", "Report clock/temp state and whether a window is active."),
        ("store", "Save the current state so restore can return to it."),
        ("lock", "Lock GPU clocks to max (needs store; unsupported on GB10 unless proven)."),
        ("restore", "Return to the stored state, then delete it."),
    ):
        p = wsub.add_parser(verb, help=text)
        p.add_argument("--json", action="store_true", help="Emit one JSON object.")
        p.set_defaults(func=_cmd_window(verb))
    w.add_argument("--json", action="store_true", help="Emit one JSON object.")
    w.set_defaults(func=_cmd_window("show"))

    d = sub.add_parser("drop-caches", help="sync, then clear the page cache (needs the grant).")
    d.add_argument("--json", action="store_true", help="Emit one JSON object.")
    d.set_defaults(func=_cmd_drop)

    g = sub.add_parser("grant-permissions", help="Install the passwordless sudo rules needed.")
    g.add_argument("--check", action="store_true", help="Only report; change nothing.")
    g.add_argument("--yes", action="store_true", help="Skip the install consent prompt.")
    g.add_argument("--json", action="store_true", help="Emit one JSON object.")
    g.set_defaults(func=_cmd_grant)
