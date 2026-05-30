"""Monitor engine: snapshot -> evaluate -> diff -> notify -> persist.

:func:`run_once` performs a single cycle; :func:`run_loop` repeats it on an
interval with a clean SIGTERM/SIGINT shutdown so systemd can stop it gracefully.
The webhook ``opener`` and the snapshot are injectable for tests.
"""

from __future__ import annotations

import datetime
import signal
import time
from typing import Callable, Optional

from spark.monitor import notify, state
from spark.monitor.config import Config
from spark.monitor.rules import evaluate
from spark.probe import containers, disk, gpu, host, memory, thermal


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def snapshot(runner: Optional[Callable] = None) -> dict:
    """Collect the probe data the rules need, as one plain dict."""
    mem = memory.collect()
    dsk = disk.collect()
    therm = thermal.collect()
    g = gpu.collect(runner)
    cnt = containers.collect(runner)
    facts = host.facts(runner)

    load1 = (facts.get("loadavg") or "0 0 0").split()[0]
    try:
        load1f = float(load1)
    except ValueError:
        load1f = 0.0
    cpu = facts.get("cpu_count") or 1
    per_core = load1f / cpu if cpu else load1f

    return {
        "host": facts.get("hostname", "?"),
        "available": {
            "memory": mem.get("available", False),
            "disk": dsk.get("available", False),
            "thermal": therm.get("available", False),
            "gpu": g.get("available", False),
            "containers": cnt.get("available", False),
        },
        "memory": mem.get("data", {}),
        "disk": dsk.get("data", {}),
        "thermal": therm.get("data", {}),
        "gpu": g.get("data", {}),
        "containers": cnt.get("data", {}),
        "load": {"load1": load1f, "cpu_count": cpu, "per_core": per_core},
    }


def run_once(
    config: Config,
    *,
    state_path,
    snap: Optional[dict] = None,
    opener: Optional[notify.Opener] = None,
) -> dict:
    """Run one evaluation cycle; deliver any edge-triggered events. Never raises."""
    snap = snap if snap is not None else snapshot()
    alerts = evaluate(snap, config.thresholds)

    prev = state.load_state(state_path)
    cycle = int(prev.get("cycle", 0)) + 1
    events, new_firing = state.diff(prev.get("firing", {}), alerts, cycle, config.renotify_cycles)

    sent = False
    error: Optional[str] = None
    deliverable = bool(events) and bool(config.webhook_url)
    if deliverable:
        payload = notify.render_payload(
            events,
            host=snap.get("host", "?"),
            ts=_now_iso(),
            fmt=config.webhook_format,
            snapshot={"available": snap.get("available", {})},
        )
        sent, error = notify.post(
            config.webhook_url,
            payload,
            timeout=config.timeout_seconds,
            retries=config.retries,
            opener=opener,
        )

    # Only commit the new firing map (advance last-notified cycles, drop resolved
    # keys) once the events are actually delivered. If there was nothing to send,
    # or delivery failed / no webhook is configured, keep the prior map so the
    # next cycle re-detects the same transitions and retries — no silent drop.
    committed = (not events) or (bool(config.webhook_url) and sent)
    persist = new_firing if committed else prev.get("firing", {})
    state.save_state(state_path, {"firing": persist, "cycle": cycle})

    return {
        "cycle": cycle,
        "alerts": [a.to_dict() for a in alerts],
        "events": events,
        "sent": sent,
        "delivered": deliverable,
        "error": error,
    }


def run_loop(
    config: Config,
    *,
    state_path,
    sleep: Callable[[float], None] = time.sleep,
    emit: Optional[Callable[[dict], None]] = None,
    max_cycles: Optional[int] = None,
) -> int:
    """Run cycles until a stop signal (or ``max_cycles``). Returns cycles run."""
    stop = {"flag": False}

    def _handler(_signum, _frame):
        stop["flag"] = True

    # signal.signal only works in the main thread; under a test runner or a
    # worker thread we simply run without the graceful-stop handler.
    handlers = None
    try:
        handlers = (
            signal.signal(signal.SIGTERM, _handler),
            signal.signal(signal.SIGINT, _handler),
        )
    except ValueError:
        handlers = None
    ran = 0
    try:
        while not stop["flag"]:
            result = run_once(config, state_path=state_path)
            ran += 1
            if emit is not None:
                emit(result)
            if max_cycles is not None and ran >= max_cycles:
                break
            # Interruptible sleep: wake promptly on a stop signal.
            waited = 0.0
            while waited < config.interval_seconds and not stop["flag"]:
                sleep(1.0)
                waited += 1.0
    finally:
        if handlers is not None:
            signal.signal(signal.SIGTERM, handlers[0])
            signal.signal(signal.SIGINT, handlers[1])
    return ran
