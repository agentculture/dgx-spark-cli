"""Edge-triggered alert state: fire on transition, resolve on recovery.

The monitor must catch catastrophes without spamming, so it tracks which alert
keys are currently firing (persisted as JSON) and emits an event only when a key
*enters* the firing set, *leaves* it (resolved), or has been firing for
``renotify_cycles`` cycles without a fresh notification. :func:`diff` is pure.
"""

from __future__ import annotations

import json
from pathlib import Path

from spark.monitor.rules import Alert

_EMPTY = {"firing": {}, "cycle": 0}


def load_state(path: str | Path) -> dict:
    """Load persisted state, or a fresh empty state on any read/parse error."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_EMPTY, firing={})
    if not isinstance(data, dict):
        return dict(_EMPTY, firing={})
    firing = data.get("firing")
    return {
        "firing": firing if isinstance(firing, dict) else {},
        "cycle": int(data.get("cycle", 0) or 0),
    }


def save_state(path: str | Path, state: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state) + "\n", encoding="utf-8")


def diff(
    prev_firing: dict,
    alerts: list[Alert],
    cycle: int,
    renotify_cycles: int,
) -> tuple[list[dict], dict]:
    """Compute edge-triggered events and the next firing map.

    Returns ``(events, new_firing)`` where each event is
    ``{"status": "firing"|"resolved", "alert": {...}}`` and ``new_firing`` maps
    each still-firing key to the cycle it was last notified.
    """
    current = {a.key: a for a in alerts}
    events: list[dict] = []
    new_firing: dict = {}

    for key, alert in current.items():
        last = prev_firing.get(key)
        seen = last is not None
        renotify_due = seen and renotify_cycles > 0 and (cycle - int(last)) >= renotify_cycles
        if not seen or renotify_due:
            events.append({"status": "firing", "alert": alert.to_dict()})
            new_firing[key] = cycle
        else:
            new_firing[key] = int(last)  # still firing; not yet time to re-notify

    for key in prev_firing:
        if key not in current:
            events.append({"status": "resolved", "alert": {"key": key}})

    return events, new_firing
