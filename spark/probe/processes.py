"""``spark processes`` — top processes by resident memory.

Reads ``/proc/<pid>/status`` directly (pure stdlib, no ``ps`` dependency) and
ranks by ``VmRSS``. RSS is the right lens on a unified-memory box: it is the
share of the one shared pool a process actually holds resident. Kernel threads
(no ``VmRSS``) are skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from spark.probe import _run
from spark.probe._report import human_bytes, pct, report, unavailable

_TOP_N = 10
_CMD_MAX = 60


def _meminfo_total(proc_root: Path) -> Optional[int]:
    text = _run.read_text(proc_root / "meminfo")
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                return None
    return None


def _read_proc(pdir: Path) -> Optional[dict]:
    if not pdir.name.isdigit():
        return None
    status = _run.read_text(pdir / "status")
    if status is None:
        return None
    rss: Optional[int] = None
    name: Optional[str] = None
    state: Optional[str] = None
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            try:
                rss = int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                rss = None
        elif line.startswith("Name:"):
            name = line.partition(":")[2].strip()
        elif line.startswith("State:"):
            field = line.partition(":")[2].strip()
            state = field.split()[0] if field else None
    if rss is None:
        return None  # kernel thread / unreadable — no resident memory
    cmdline = _run.read_text(pdir / "cmdline") or ""
    cmd = cmdline.replace("\x00", " ").strip() or name or "?"
    return {
        "pid": int(pdir.name),
        "name": name or "?",
        "state": state or "?",
        "rss_bytes": rss,
        "cmd": cmd,
    }


def collect(proc_root: str = "/proc", top_n: int = _TOP_N) -> dict:
    """Return a top-processes report from ``proc_root`` (injectable for tests)."""
    root = Path(proc_root)
    if not root.is_dir():
        return unavailable("processes", proc_root, "read /proc on a Linux host")

    total_mem = _meminfo_total(root)
    procs: list[dict] = []
    for pdir in root.iterdir():
        info = _read_proc(pdir)
        if info is not None:
            procs.append(info)
    procs.sort(key=lambda p: p["rss_bytes"], reverse=True)
    top = procs[:top_n]

    items: list[str] = []
    for proc in top:
        mem_pct = pct(proc["rss_bytes"], total_mem) if total_mem else None
        pct_str = f" ({mem_pct:.1f}%)" if mem_pct is not None else ""
        cmd = proc["cmd"]
        if len(cmd) > _CMD_MAX:
            cmd = cmd[: _CMD_MAX - 3] + "..."
        rss = human_bytes(proc["rss_bytes"])
        items.append(f"{proc['pid']:>8} {proc['state']} {rss:>10}{pct_str}  {cmd}")

    title = f"Top {len(top)} by resident memory ({len(procs)} processes)"
    sections = [{"title": title, "items": items or ["no processes readable"]}]
    return report(
        "processes", source=proc_root, sections=sections, data={"count": len(procs), "top": top}
    )
