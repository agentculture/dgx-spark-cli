"""``spark disk`` — usage for real (non-virtual) block filesystems.

Reads ``/proc/mounts`` and calls :func:`os.statvfs` per mount — pure stdlib, no
``df`` dependency. Virtual filesystems (tmpfs, cgroup, …) and snap ``loop``
mounts are filtered out so the report shows real storage only.
"""

from __future__ import annotations

import os
from typing import Callable, Iterator, Optional

from spark.probe import _run
from spark.probe._report import human_bytes, pct, report, unavailable

_FULL_PCT = 85.0  # warn at/above this used percentage

# Pseudo / virtual filesystem types we never report as "disk".
_SKIP_FSTYPES = {
    "proc",
    "sysfs",
    "cgroup",
    "cgroup2",
    "devtmpfs",
    "tmpfs",
    "devpts",
    "mqueue",
    "debugfs",
    "tracefs",
    "securityfs",
    "pstore",
    "bpf",
    "autofs",
    "configfs",
    "fusectl",
    "hugetlbfs",
    "squashfs",
    "overlay",
    "nsfs",
    "ramfs",
    "binfmt_misc",
    "fuse.portal",
    "efivarfs",
    "rpc_pipefs",
}

Statvfs = Callable[[str], os.statvfs_result]


def _parse_mounts(text: str) -> Iterator[tuple[str, str, str]]:
    """Yield ``(device, mountpoint, fstype)`` for real block mounts."""
    seen: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        source, target, fstype = parts[0], parts[1], parts[2]
        if fstype in _SKIP_FSTYPES or not source.startswith("/dev/"):
            continue
        if source.startswith("/dev/loop"):
            continue
        if target in seen:
            continue
        seen.add(target)
        # /proc/mounts octal-escapes spaces etc. in the mountpoint (\040).
        target = target.encode("utf-8", "replace").decode("unicode_escape")
        yield source, target, fstype


def collect(mounts_path: str = "/proc/mounts", statvfs: Optional[Statvfs] = None) -> dict:
    """Return a disk report from ``mounts_path`` using ``statvfs`` (injectable)."""
    vfs = statvfs or os.statvfs
    text = _run.read_text(mounts_path)
    if text is None:
        return unavailable("disk", mounts_path, "read /proc/mounts on a Linux host")

    sections: list[dict[str, object]] = []
    warnings: list[str] = []
    rows: list[dict[str, object]] = []
    for source, target, fstype in _parse_mounts(text):
        try:
            st = vfs(target)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total == 0:
            continue
        free = st.f_bavail * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
        used_pct = pct(used, total)
        used_suffix = f" ({used_pct:.0f}%)" if used_pct is not None else ""
        sections.append(
            {
                "title": target,
                "items": [
                    f"device: {source} ({fstype})",
                    f"size: {human_bytes(total)}",
                    f"used: {human_bytes(used)}{used_suffix}",
                    f"free: {human_bytes(free)}",
                ],
            }
        )
        rows.append(
            {
                "device": source,
                "mount": target,
                "fstype": fstype,
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "used_pct": used_pct,
            }
        )
        if used_pct is not None and used_pct >= _FULL_PCT:
            warnings.append(f"{target} is {used_pct:.0f}% full ({human_bytes(free)} free)")

    if not sections:
        sections.append({"title": "Filesystems", "items": ["no real block filesystems found"]})
    return report(
        "disk", source=mounts_path, sections=sections, warnings=warnings, data={"filesystems": rows}
    )
