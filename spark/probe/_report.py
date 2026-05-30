"""Report model, unit formatting, and text rendering shared by host probes.

A *report* is a plain JSON-friendly dict::

    {
        "subject": "memory",
        "available": True,
        "source": "/proc/meminfo, /proc/swaps",
        "warnings": ["swap 73% used (11.0/15.0 GiB) — memory pressure"],
        "sections": [{"title": "Usage", "items": ["...", ...]}],
        "data": {...},          # raw numbers for programmatic consumers
        "remediation": "...",   # present only when unavailable
    }

Text mode renders ``warnings`` first (an "Attention" section) so the few facts
that matter right now lead; JSON mode emits the dict verbatim.
"""

from __future__ import annotations

from typing import Iterable, Optional

from spark.cli._output import render_sections

Section = dict[str, object]


def report(
    subject: str,
    *,
    available: bool = True,
    source: str = "",
    sections: Optional[Iterable[Section]] = None,
    warnings: Optional[Iterable[str]] = None,
    data: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    """Build a report dict with every field defaulted to a stable shape."""
    return {
        "subject": subject,
        "available": available,
        "source": source,
        "warnings": list(warnings or []),
        "sections": list(sections or []),
        "data": dict(data or {}),
    }


def unavailable(subject: str, source: str, remediation: str) -> dict[str, object]:
    """A report for a subsystem whose backing tool/node is absent."""
    rep = report(subject, available=False, source=source)
    rep["remediation"] = remediation
    return rep


def render_report_text(rep: dict[str, object]) -> str:
    """Render a report as sectioned markdown, warnings first."""
    sections: list[Section] = []
    warnings = rep.get("warnings") or []
    if warnings:
        sections.append({"title": "Attention", "items": list(warnings)})
    if not rep.get("available", True):
        note = f"unavailable (source: {rep.get('source', '?')})"
        items: list[str] = [note]
        remediation = rep.get("remediation")
        if remediation:
            items.append(f"hint: {remediation}")
        sections.append({"title": "Status", "items": items})
    sections.extend(rep.get("sections", []) or [])
    if not sections:
        sections.append({"title": "Status", "items": ["no data"]})
    return render_sections(str(rep.get("subject", "")), sections)


_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]


def human_bytes(num: Optional[float]) -> str:
    """Format a byte count as a human-readable string (``None`` -> ``"n/a"``)."""
    if num is None:
        return "n/a"
    size = float(num)
    for unit in _UNITS:
        if abs(size) < 1024.0 or unit == _UNITS[-1]:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} {_UNITS[-1]}"  # pragma: no cover - loop always returns


def human_duration(seconds: Optional[float]) -> str:
    """Format a duration in seconds as ``"7d 12h 23m"`` (``None`` -> ``"n/a"``)."""
    if seconds is None:
        return "n/a"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def pct(part: float, whole: float) -> Optional[float]:
    """Percentage ``part`` is of ``whole`` (``None`` when ``whole`` is zero)."""
    if not whole:
        return None
    return 100.0 * part / whole
