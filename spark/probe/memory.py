"""``spark memory`` — unified RAM + swap snapshot.

On the DGX Spark (GB10) there is ONE ~128 GiB LPDDR5X pool shared by the Grace
CPU and the Blackwell GPU — there is no separate VRAM. So memory pressure here
is a GPU-workload signal as much as a CPU one, and ``nvidia-smi`` reports GPU
memory as ``[N/A]`` by design (see ``spark gpu``). Reads ``/proc/meminfo``.
"""

from __future__ import annotations

from spark.probe import _run
from spark.probe._report import human_bytes, pct, report, unavailable

# Warn thresholds.
_LOW_AVAIL_FRAC = 0.10  # warn when < 10% of RAM is available
_SWAP_PRESSURE_FRAC = 0.25  # warn when > 25% of swap is in use


def _parse_meminfo(text: str) -> dict[str, int]:
    """Parse ``/proc/meminfo`` (kB values) into a name -> bytes mapping."""
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


def collect(meminfo_path: str = "/proc/meminfo") -> dict:
    """Return a memory report from ``meminfo_path`` (injectable for tests)."""
    text = _run.read_text(meminfo_path)
    if text is None:
        return unavailable("memory", meminfo_path, "read /proc/meminfo on a Linux host")

    mem = _parse_meminfo(text)
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable", mem.get("MemFree", 0))
    used = max(total - available, 0)
    swap_total = mem.get("SwapTotal", 0)
    swap_used = max(swap_total - mem.get("SwapFree", 0), 0)
    buff_cache = mem.get("Buffers", 0) + mem.get("Cached", 0)
    shmem = mem.get("Shmem", 0)

    used_pct = pct(used, total)
    swap_pct = pct(swap_used, swap_total)

    warnings: list[str] = []
    if total and available / total < _LOW_AVAIL_FRAC:
        warnings.append(
            f"only {human_bytes(available)} available of {human_bytes(total)} "
            f"({used_pct:.0f}% used) — memory pressure"
        )
    if swap_total and swap_used / swap_total > _SWAP_PRESSURE_FRAC:
        warnings.append(
            f"swap {swap_pct:.0f}% used ({human_bytes(swap_used)}/"
            f"{human_bytes(swap_total)}) — workloads spilling to disk"
        )

    used_suffix = f" ({used_pct:.0f}%)" if used_pct is not None else ""
    swap_suffix = f" ({swap_pct:.0f}%)" if swap_pct is not None else ""
    sections = [
        {
            "title": "Unified memory (shared CPU + GPU)",
            "items": [
                f"total: {human_bytes(total)}",
                f"used: {human_bytes(used)}{used_suffix}",
                f"available: {human_bytes(available)}",
                f"buffers/cache: {human_bytes(buff_cache)}",
                f"shared (shmem): {human_bytes(shmem)}",
            ],
        },
        {
            "title": "Swap",
            "items": [
                f"total: {human_bytes(swap_total)}",
                f"used: {human_bytes(swap_used)}{swap_suffix}",
            ],
        },
        {
            "title": "Note",
            "items": [
                "GB10 has no separate VRAM — GPU allocations draw on this same "
                "pool, so 'spark gpu' shows memory as N/A by design.",
            ],
        },
    ]
    data = {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used_pct": used_pct,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": swap_used,
        "swap_used_pct": swap_pct,
    }
    return report("memory", source=meminfo_path, sections=sections, warnings=warnings, data=data)
