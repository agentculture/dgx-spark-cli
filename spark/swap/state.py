"""Swap state inspection — ``collect_swap_state()``.

Reads ``/proc/swaps``, ``/proc/meminfo``, ``/proc/sys/vm/swappiness``, and
``/proc/mounts`` to return a structured snapshot of the system's swap
configuration and usage.  All ``/proc`` reads go through the injectable
``read`` parameter so tests can supply fake content without root or a real
swap device.  Stdlib-only — see the zero-runtime-dependency rule in CLAUDE.md.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from spark.probe._run import read_text

# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------


def _parse_meminfo(text: str) -> dict[str, int]:
    """Parse ``/proc/meminfo`` (kB values) into a ``name -> bytes`` mapping."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        rest = rest.strip()
        if not rest:
            continue
        value = rest.split()[0]
        try:
            out[key.strip()] = int(value) * 1024  # kB -> bytes
        except ValueError:
            continue
    return out


def _parse_swaps(text: str) -> list[dict]:
    """Parse ``/proc/swaps`` rows (excluding the header) into device dicts.

    ``/proc/swaps`` format (header + data rows)::

        Filename   Type      Size    Used  Priority
        /swap.img  file      8388604 0     -2

    ``Size`` and ``Used`` are in KiB; we convert to bytes on the way out.
    """
    devices: list[dict] = []
    lines = text.splitlines()
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) < 5:
            continue
        name = parts[0]
        raw_type = parts[1]
        try:
            size_kib = int(parts[2])
            used_kib = int(parts[3])
            priority = int(parts[4])
        except ValueError:
            continue
        devices.append(
            {
                "name": name,
                "type": "file" if raw_type == "file" else "partition",
                "size_bytes": size_kib * 1024,
                "used_bytes": used_kib * 1024,
                "priority": priority,
            }
        )
    return devices


def _find_mount(swapfile: str, mounts_text: str) -> tuple[Optional[str], Optional[str]]:
    """Longest-prefix match of ``swapfile`` against ``/proc/mounts`` entries.

    Returns ``(mount, fs_type)`` or ``(None, None)`` when no match is found.
    Only matches mount points that are a proper directory prefix (avoids
    ``/home`` accidentally matching ``/homelander/swap``).
    """
    best_mount: Optional[str] = None
    best_fs: Optional[str] = None
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = parts[1]
        fs_type = parts[2]
        # Normalise: "/" stays as-is; others must not have trailing slash
        norm = "/" if mount == "/" else mount.rstrip("/")
        # Accept only if mount is "/" (matches every absolute path) or
        # the swapfile lives directly beneath this mount point.
        if norm == "/" or swapfile.startswith(norm + "/"):
            if best_mount is None or len(norm) > len(best_mount):
                best_mount = norm
                best_fs = fs_type
    return best_mount, best_fs


# ---------------------------------------------------------------------------
# Zero-value schema builder (used when a required source is unavailable)
# ---------------------------------------------------------------------------


def _unavailable_result() -> dict:
    """Full schema with ``available=False`` and zero/``None`` everywhere."""
    return {
        "available": False,
        "swappiness": None,
        "mem": {
            "total_bytes": 0,
            "available_bytes": 0,
            "free_bytes": 0,
            "used_bytes": 0,
            "used_pct": 0.0,
            "swap_total_bytes": 0,
            "swap_free_bytes": 0,
            "swap_used_bytes": 0,
            "swap_used_pct": 0.0,
        },
        "devices": [],
        "backing": {
            "swapfile": None,
            "fs_type": None,
            "mount": None,
            "free_bytes": None,
        },
    }


def _compute_mem(mem_raw: dict) -> dict:
    """Derive the ``mem`` block (bytes + percentages) from parsed /proc/meminfo."""
    total = mem_raw.get("MemTotal", 0)
    available_mem = mem_raw.get("MemAvailable", mem_raw.get("MemFree", 0))
    free = mem_raw.get("MemFree", 0)
    used = max(total - available_mem, 0)
    swap_total = mem_raw.get("SwapTotal", 0)
    swap_free = mem_raw.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)
    return {
        "total_bytes": total,
        "available_bytes": available_mem,
        "free_bytes": free,
        "used_bytes": used,
        "used_pct": round(used / total * 100, 2) if total else 0.0,
        "swap_total_bytes": swap_total,
        "swap_free_bytes": swap_free,
        "swap_used_bytes": swap_used,
        "swap_used_pct": round(swap_used / swap_total * 100, 2) if swap_total else 0.0,
    }


def _read_swappiness(read: Callable[[str], Optional[str]]) -> Optional[int]:
    """Read /proc/sys/vm/swappiness as an int, or None when absent/unparseable."""
    text = read("/proc/sys/vm/swappiness")
    if text is None:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _compute_backing(devices: list, read, statvfs) -> dict:
    """Resolve the backing filesystem (mount, fs type, free bytes) of the first
    file-type swap device; all-``None`` when there is no file-backed swap."""
    backing: dict = {"swapfile": None, "fs_type": None, "mount": None, "free_bytes": None}
    first_file = next((d for d in devices if d["type"] == "file"), None)
    if first_file is None:
        return backing
    swapfile = first_file["name"]
    backing["swapfile"] = swapfile
    mounts_text = read("/proc/mounts")
    if mounts_text is None:
        return backing
    mount, fs_type = _find_mount(swapfile, mounts_text)
    backing["mount"] = mount
    backing["fs_type"] = fs_type
    if mount is not None:
        try:
            sv = statvfs(mount)
            backing["free_bytes"] = sv.f_bavail * sv.f_frsize
        except OSError:
            pass
    return backing


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def collect_swap_state(
    *,
    read: Callable[[str], Optional[str]] = read_text,
    statvfs=os.statvfs,
) -> dict:
    """Collect a structured swap-state snapshot from ``/proc`` sources.

    Parameters
    ----------
    read:
        A ``(path: str) -> str | None`` callable.  Defaults to
        :func:`spark.probe._run.read_text` which silently returns ``None``
        on any :class:`OSError`.  Inject a fake in tests.
    statvfs:
        A callable with the same signature as :func:`os.statvfs`.  Used to
        determine backing-filesystem free space; inject a fake in tests.

    Returns
    -------
    dict
        Keys: ``available`` (bool), ``swappiness`` (int | None), ``mem``
        (dict), ``devices`` (list[dict]), ``backing`` (dict).
        ``available`` is ``False`` when either ``/proc/meminfo`` or
        ``/proc/swaps`` cannot be read; the rest of the schema is still
        returned with zero / ``None`` values.  This function never raises.
    """
    meminfo_text = read("/proc/meminfo")
    swaps_text = read("/proc/swaps")

    # Both sources are required; degrade gracefully if either is absent.
    if meminfo_text is None or swaps_text is None:
        return _unavailable_result()

    devices = _parse_swaps(swaps_text)
    return {
        "available": True,
        "swappiness": _read_swappiness(read),
        "mem": _compute_mem(_parse_meminfo(meminfo_text)),
        "devices": devices,
        "backing": _compute_backing(devices, read, statvfs),
    }
