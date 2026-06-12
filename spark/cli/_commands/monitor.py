"""``dgx-spark-cli monitor`` — the deterministic, AI-free watchdog.

Periodically evaluates the :mod:`spark.probe` collectors against configured
thresholds and POSTs to a generic webhook when a catastrophe condition crosses.
Runs always-on as a systemd ``--user`` service this CLI installs and manages.

Verbs:

* ``check``    — evaluate now, print firing alerts (no webhook, no state change)
* ``once``     — one cycle: evaluate, deliver edge-triggered events, update state
* ``run``      — foreground loop (what the systemd unit runs)
* ``test``     — POST a synthetic alert to verify the webhook
* ``config``   — show resolved config / ``--init`` a scaffold
* ``install`` / ``enable`` / ``disable`` / ``status`` / ``uninstall`` — systemd
* ``overview`` — describe the monitor surface
"""

from __future__ import annotations

import argparse

from spark.cli._errors import EXIT_ENV_ERROR, CliError
from spark.cli._output import emit_diagnostic, emit_result, render_sections
from spark.monitor import config as mconfig
from spark.monitor import engine, systemd
from spark.monitor.config import default_state_path
from spark.monitor.rules import evaluate
from spark.monitor.state import load_state

_SUBJECT_CONFIG = "monitor config"


def _load(args: argparse.Namespace) -> mconfig.Config:
    return mconfig.load(getattr(args, "config", None))


def _json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _emit(subject: str, sections: list, payload: dict, json_mode: bool) -> None:
    if json_mode:
        emit_result(payload, json_mode=True)
    else:
        emit_result(render_sections(subject, sections), json_mode=False)


# --- overview -------------------------------------------------------------


def _overview_sections() -> list:
    return [
        {
            "title": "What",
            "items": [
                "Deterministic, AI-free watchdog over the spark.probe collectors.",
                "POSTs to a generic webhook when a threshold is crossed, and again "
                "when it clears (edge-triggered — no spam).",
                "On 'run', posts a one-shot 'started watching' liveness alert "
                "(toggle: notify_on_start).",
            ],
        },
        {
            "title": "Verbs",
            "items": [
                "check — evaluate now, print firing alerts (no webhook)",
                "once — one cycle: evaluate + deliver + update state",
                "run — foreground loop (the systemd ExecStart)",
                "test — POST a synthetic alert to verify the webhook",
                "config [--init] — show/scaffold thresholds + webhook",
                "install/enable/disable/status/uninstall — systemd --user service",
            ],
        },
        {
            "title": "Config",
            "items": [
                f"file: {mconfig.default_config_path()}",
                "env override: DGX_SPARK_WEBHOOK_URL",
                "watches: memory, swap, disk, thermal, GPU temp, load, I/O "
                "contention (iowait + blocked procs), container health, "
                "subsystem availability",
            ],
        },
    ]


def cmd_overview(args: argparse.Namespace) -> int:
    _emit(
        "dgx-spark-cli monitor",
        _overview_sections(),
        {"subject": "dgx-spark-cli monitor", "sections": _overview_sections()},
        _json(args),
    )
    return 0


# --- check ----------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    cfg = _load(args)
    snap = engine.snapshot()
    alerts = [a.to_dict() for a in evaluate(snap, cfg.thresholds)]
    sections = [{"title": "Host", "items": [f"host: {snap.get('host', '?')}"]}]
    if alerts:
        sections.append(
            {"title": "Firing", "items": [f"[{a['severity']}] {a['message']}" for a in alerts]}
        )
    else:
        sections.append({"title": "Firing", "items": ["all clear — no thresholds crossed"]})
    payload = {
        "subject": "monitor check",
        "host": snap.get("host", "?"),
        "alerts": alerts,
        "thresholds": cfg.thresholds,
        "sections": sections,
    }
    _emit("monitor check", sections, payload, _json(args))
    return 0


# --- once -----------------------------------------------------------------


def cmd_once(args: argparse.Namespace) -> int:
    cfg = _load(args)
    result = engine.run_once(cfg, state_path=default_state_path())
    events = result["events"]
    if not cfg.webhook_url:
        delivery = (
            "no webhook configured (set DGX_SPARK_WEBHOOK_URL or run 'monitor config --init')"
        )
    elif not events:
        delivery = "no transitions this cycle"
    elif result["sent"]:
        delivery = f"delivered {len(events)} event(s)"
    else:
        delivery = f"delivery FAILED: {result.get('error')}"
    sections = [
        {
            "title": "Cycle",
            "items": [
                f"cycle: {result['cycle']}",
                f"firing now: {len(result['alerts'])}",
                f"transitions: {len(events)}",
                f"delivery: {delivery}",
            ],
        }
    ]
    if events:
        sections.append(
            {
                "title": "Events",
                "items": [
                    f"{e['status']}: {e['alert'].get('message', e['alert'].get('key'))}"
                    for e in events
                ],
            }
        )
    payload = {"subject": "monitor once", **result, "sections": sections}
    _emit("monitor once", sections, payload, _json(args))
    return 0


# --- run (foreground loop) ------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if getattr(args, "interval", None):
        cfg.interval_seconds = int(args.interval)
    errors = mconfig.validate(cfg)
    if errors:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="monitor is not configured: " + "; ".join(errors),
            remediation="run 'dgx-spark-cli monitor config --init' then set webhook_url "
            "(or export DGX_SPARK_WEBHOOK_URL)",
        )

    json_mode = _json(args)

    def _emit_cycle(result: dict) -> None:
        if not result["events"]:
            return  # stay quiet on no-change cycles
        if json_mode:
            emit_result(result, json_mode=True)  # structured event stream on stdout
            return
        if not result["delivered"]:
            outcome = "not delivered"
        elif result["sent"]:
            outcome = "sent"
        else:
            outcome = f"FAILED ({result.get('error')})"
        emit_diagnostic(
            f"[monitor] cycle {result['cycle']}: {len(result['events'])} event(s) -> {outcome}"
        )

    emit_diagnostic(
        f"[monitor] watching every {cfg.interval_seconds}s -> {cfg.webhook_url} "
        f"({cfg.webhook_format}); Ctrl-C to stop"
    )
    # One-shot "started working" liveness ping, before the loop. notify_started
    # is bounded and never raises, so a slow/dead webhook neither stalls nor
    # crashes startup — a failed ping is just a diagnostic.
    if cfg.notify_on_start and cfg.webhook_url:
        ok, error = engine.notify_started(cfg)
        outcome = "sent" if ok else f"FAILED ({error})"
        emit_diagnostic(f"[monitor] startup alert -> {outcome}")
    engine.run_loop(cfg, state_path=default_state_path(), emit=_emit_cycle)
    return 0


# --- test -----------------------------------------------------------------


def cmd_test(args: argparse.Namespace) -> int:
    from spark.monitor import notify

    cfg = _load(args)
    if not cfg.webhook_url:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="no webhook configured",
            remediation="set webhook_url in the config or export DGX_SPARK_WEBHOOK_URL",
        )
    event = {
        "status": "firing",
        "alert": {
            "key": "monitor_test",
            "severity": "warning",
            "message": "test alert from dgx-spark-cli monitor",
        },
    }
    payload = notify.render_payload(
        [event], host="dgx-spark-cli", ts="(test)", fmt=cfg.webhook_format
    )
    ok, error = notify.post(
        cfg.webhook_url, payload, timeout=cfg.timeout_seconds, retries=cfg.retries
    )
    if not ok:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"webhook POST failed: {error}",
            remediation="check the URL is reachable and accepts POST JSON",
        )
    if _json(args):
        emit_result(
            {"subject": "monitor test", "webhook": cfg.webhook_url, "ok": True, "error": None},
            json_mode=True,
        )
    else:
        emit_result(f"monitor test -> {cfg.webhook_url}\nok", json_mode=False)
    return 0


# --- config ---------------------------------------------------------------


def _config_sections(cfg: mconfig.Config, errors: list) -> list:
    sections = [
        {
            "title": "Delivery",
            "items": [
                f"webhook: {cfg.webhook_url or '(unset)'}",
                f"format: {cfg.webhook_format}",
                f"interval: {cfg.interval_seconds}s, re-notify every {cfg.renotify_cycles} cycles",
                f"startup alert: {'on' if cfg.notify_on_start else 'off'} (notify_on_start)",
                f"source: {cfg.source_path or '(defaults — no file)'}",
            ],
        },
        {"title": "Thresholds", "items": [f"{k}: {v}" for k, v in cfg.thresholds.items()]},
    ]
    if errors:
        sections.insert(0, {"title": "Invalid", "items": errors})
    return sections


def cmd_config(args: argparse.Namespace) -> int:
    json_mode = _json(args)
    if getattr(args, "init", False):
        path = mconfig.init_file(getattr(args, "config", None))
        if json_mode:
            emit_result(
                {"subject": _SUBJECT_CONFIG, "action": "init", "path": str(path)}, json_mode=True
            )
        else:
            emit_result(
                f"wrote scaffold config: {path}\nedit webhook_url, then 'monitor test'.",
                json_mode=False,
            )
    else:
        cfg = _load(args)
        errors = mconfig.validate(cfg)
        if json_mode:
            body = cfg.to_dict()
            body.update({"source_path": cfg.source_path, "valid": not errors, "errors": errors})
            emit_result({"subject": _SUBJECT_CONFIG, **body}, json_mode=True)
        else:
            emit_result(
                render_sections(_SUBJECT_CONFIG, _config_sections(cfg, errors)), json_mode=False
            )
    return 0


# --- systemd management ---------------------------------------------------


def cmd_install(args: argparse.Namespace) -> int:
    path = systemd.install(getattr(args, "config", None))
    cfg = _load(args)
    note = "" if mconfig.validate(cfg) == [] else " (config not valid yet — set webhook_url)"
    payload = {"subject": "monitor install", "unit_path": str(path)}
    if _json(args):
        emit_result(payload, json_mode=True)
    else:
        emit_result(
            f"installed unit: {path}{note}\nnext: 'dgx-spark-cli monitor enable'", json_mode=False
        )
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    ok, error = systemd.enable(linger=not getattr(args, "no_linger", False))
    if not ok:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"could not enable service: {error}",
            remediation="ensure the systemd user manager is running and the unit is installed",
        )
    if _json(args):
        emit_result({"subject": "monitor enable", "ok": True, "error": None}, json_mode=True)
    else:
        emit_result("enabled + started", json_mode=False)
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    ok, error = systemd.disable()
    # disable is idempotent (a not-loaded unit is fine), so it isn't fatal — but a
    # failure note is a diagnostic and belongs on stderr, not stdout.
    if _json(args):
        emit_result({"subject": "monitor disable", "ok": ok, "error": error}, json_mode=True)
    elif ok:
        emit_result("disabled + stopped", json_mode=False)
    else:
        emit_diagnostic(f"disable: {error}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    path = systemd.uninstall()
    if _json(args):
        emit_result({"subject": "monitor uninstall", "unit_path": str(path)}, json_mode=True)
    else:
        emit_result(f"removed unit: {path}", json_mode=False)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    svc = systemd.status()
    st = load_state(default_state_path())
    firing = sorted((st.get("firing") or {}).keys())
    sections = [
        {
            "title": "Service",
            "items": [
                f"unit: {svc['unit']}",
                f"installed: {svc['installed']}",
                f"active: {svc['active']}",
                f"enabled: {svc['enabled']}",
            ],
        },
        {
            "title": "Alert state",
            "items": [f"firing keys: {', '.join(firing) if firing else 'none'}"],
        },
    ]
    payload = {"subject": "monitor status", "service": svc, "firing": firing, "sections": sections}
    _emit("monitor status", sections, payload, _json(args))
    return 0


# --- registration ---------------------------------------------------------


def _no_verb(args: argparse.Namespace) -> int:
    return cmd_overview(args)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Path to the monitor config JSON (default: XDG).")


def _add_ignored(parser: argparse.ArgumentParser) -> None:
    # Descriptive verbs must never hard-fail on a stray positional (overview's
    # contract). Accept and ignore any extra args.
    parser.add_argument("ignored", nargs="*", help=argparse.SUPPRESS)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("monitor", help="Threshold watchdog that webhooks on catastrophes.")
    _add_json(p)
    p.set_defaults(func=_no_verb, json=False)
    noun = p.add_subparsers(dest="monitor_command", parser_class=type(p))

    ov = noun.add_parser("overview", help="Describe the monitor surface.")
    _add_json(ov)
    _add_ignored(ov)
    ov.set_defaults(func=cmd_overview)

    # Descriptive verbs (read-only) tolerate stray positionals.
    for name, func, helptext in (
        ("check", cmd_check, "Evaluate thresholds now (no webhook, no state change)."),
        ("status", cmd_status, "Show systemd service + current firing alerts."),
    ):
        verb = noun.add_parser(name, help=helptext)
        _add_json(verb)
        _add_config(verb)
        _add_ignored(verb)
        verb.set_defaults(func=func)

    once = noun.add_parser(
        "once", help="Run one cycle: evaluate, deliver transitions, update state."
    )
    _add_json(once)
    _add_config(once)
    once.set_defaults(func=cmd_once)

    run = noun.add_parser("run", help="Foreground watch loop (the systemd ExecStart).")
    _add_json(run)
    _add_config(run)
    run.add_argument("--interval", type=int, help="Override the poll interval (seconds).")
    run.set_defaults(func=cmd_run)

    test = noun.add_parser("test", help="POST a synthetic alert to verify the webhook.")
    _add_json(test)
    _add_config(test)
    test.set_defaults(func=cmd_test)

    cfg = noun.add_parser("config", help="Show resolved config, or --init a scaffold.")
    _add_json(cfg)
    _add_config(cfg)
    cfg.add_argument("--init", action="store_true", help="Write a scaffold config file.")
    cfg.set_defaults(func=cmd_config)

    inst = noun.add_parser("install", help="Write the systemd --user unit.")
    _add_json(inst)
    _add_config(inst)
    inst.set_defaults(func=cmd_install)

    en = noun.add_parser("enable", help="Enable + start the service (with linger).")
    _add_json(en)
    en.add_argument("--no-linger", action="store_true", help="Don't enable login-linger.")
    en.set_defaults(func=cmd_enable)

    dis = noun.add_parser("disable", help="Disable + stop the service.")
    _add_json(dis)
    dis.set_defaults(func=cmd_disable)

    un = noun.add_parser("uninstall", help="Disable and remove the unit file.")
    _add_json(un)
    un.set_defaults(func=cmd_uninstall)
