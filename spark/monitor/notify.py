"""Webhook delivery: stdlib ``urllib`` POST, generic JSON or chat presets.

Never raises into the monitor loop — a failed POST returns ``(False, error)`` so
a flaky endpoint can't crash the watchdog. Only ``http(s)`` URLs are accepted
(no ``file://`` etc.). The ``opener`` is injectable so tests need no network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Optional

_SEVERITY_EMOJI = {"critical": "\U0001f534", "warning": "\U0001f7e0"}  # red / orange circle
_RESOLVED_EMOJI = "✅"  # check mark

# An opener takes (Request, timeout) and returns a response-like object.
Opener = Callable[[urllib.request.Request, float], object]


def _render_text(events: list[dict], host: str, ts: str) -> str:
    lines = [f"{host} — dgx-spark monitor @ {ts}"]
    for event in events:
        alert = event.get("alert", {})
        if event.get("status") == "resolved":
            lines.append(f"{_RESOLVED_EMOJI} resolved: {alert.get('key')}")
        else:
            emoji = _SEVERITY_EMOJI.get(alert.get("severity"), "\U0001f514")
            lines.append(f"{emoji} {alert.get('message', alert.get('key'))}")
    return "\n".join(lines)


def render_payload(
    events: list[dict],
    *,
    host: str,
    ts: str,
    source: str = "dgx-spark-cli",
    fmt: str = "generic",
    snapshot: Optional[dict] = None,
) -> dict:
    """Build the webhook body for ``events`` in the configured ``fmt``."""
    if fmt == "slack":
        return {"text": _render_text(events, host, ts)}
    if fmt == "discord":
        return {"content": _render_text(events, host, ts)}
    return {
        "source": source,
        "host": host,
        "ts": ts,
        "events": events,
        "snapshot": snapshot or {},
    }


def _default_open(req: urllib.request.Request, timeout: float) -> object:
    # Scheme is allow-listed by the caller (post) before we get here.
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310


def post(
    url: str,
    payload: dict,
    *,
    timeout: float = 10.0,
    retries: int = 2,
    opener: Optional[Opener] = None,
) -> tuple[bool, Optional[str]]:
    """POST ``payload`` as JSON to ``url``. Returns ``(ok, error)``; never raises."""
    if not str(url).startswith(("http://", "https://")):
        return False, "webhook_url must be an http(s) URL"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    do_open = opener or _default_open
    last_error = "unknown error"
    for _ in range(max(1, retries + 1)):
        try:
            resp = do_open(request, timeout)
            code = int(getattr(resp, "status", None) or getattr(resp, "code", 200))
            closer = getattr(resp, "close", None)
            if callable(closer):
                closer()
            if 200 <= code < 300:
                return True, None
            last_error = f"HTTP {code}"
        except urllib.error.HTTPError as err:
            last_error = f"HTTP {err.code}"
        except (OSError, ValueError) as err:
            # urllib.error.URLError is a subclass of OSError, so it's covered.
            last_error = str(err)
    return False, last_error
