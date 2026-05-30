"""``spark status`` — machine-wide scope, anomalies first.

Calls every domain collector once and assembles a one-screen headline: a Host
header, an Attention block merging all subsystem warnings, then a compact
one-liner per subsystem. This is the "good scope of the machine" entry point;
drill into any line with the matching verb (``spark memory``, ``spark gpu``, …).
"""

from __future__ import annotations

from typing import Optional

from spark.probe import containers, disk, gpu, host, memory, network, processes, thermal
from spark.probe._report import human_bytes, human_duration, report
from spark.probe._run import Runner, default_runner


def _host_items(facts: dict, gpu_name: str) -> list[str]:
    model = facts.get("model")
    if model:
        model = model.replace("_", " ")  # DMI product_name uses underscores
    elif "GB10" in gpu_name:
        model = "NVIDIA DGX Spark (GB10)"
    cpu_models = facts.get("cpu_models") or []
    cpu = f"{facts.get('cpu_count', '?')} cores"
    if cpu_models:
        cpu += " (" + ", ".join(cpu_models) + ")"
    items = [
        f"host: {facts.get('hostname', '?')}",
        f"model: {model or 'unknown'}",
        f"os: {facts.get('os', 'unknown')} ({facts.get('kernel', '?')} {facts.get('arch', '')})",
        f"cpu: {cpu}",
        f"uptime: {human_duration(facts.get('uptime_seconds'))}, load {facts.get('loadavg', '?')}",
    ]
    return items


def _line(rep: dict, builder) -> str:
    """Return a one-liner for a subsystem, or an 'unavailable' note."""
    if not rep.get("available", True):
        return f"unavailable ({rep.get('source', '?')})"
    try:
        return builder(rep.get("data", {}))
    except (KeyError, TypeError, ValueError, IndexError):  # pragma: no cover - defensive
        return "n/a"


def _memory_line(data: dict) -> str:
    used_pct = data.get("used_pct")
    pct = f" ({used_pct:.0f}%)" if used_pct is not None else ""
    used = human_bytes(data.get("used_bytes"))
    total = human_bytes(data.get("total_bytes"))
    free = human_bytes(data.get("available_bytes"))
    swap_used = human_bytes(data.get("swap_used_bytes"))
    swap_total = human_bytes(data.get("swap_total_bytes"))
    return f"{used} / {total}{pct} used, {free} free; swap {swap_used}/{swap_total}"


def _gpu_line(data: dict) -> str:
    g = data.get("gpu", {}) or {}
    util = g.get("utilization.gpu")
    temp = g.get("temperature.gpu")
    power = g.get("power.draw")
    attributed = data.get("gpu_attributed_mib") or 0
    mem = human_bytes(attributed * 1024 * 1024) if attributed else "n/a"
    return (
        f"{g.get('name', 'GPU')}: util {util or 'n/a'}%, {temp or 'n/a'} C, "
        f"{power or 'n/a'} W; ~{mem} attributed (unified)"
    )


def _disk_line(data: dict) -> str:
    filesystems = data.get("filesystems", [])
    root = next((f for f in filesystems if f.get("mount") == "/"), None)
    root = root or (filesystems[0] if filesystems else None)
    if not root:
        return "no real filesystems"
    pct = root.get("used_pct")
    pct_str = f" ({pct:.0f}%)" if pct is not None else ""
    return (
        f"{root.get('mount')}: {human_bytes(root.get('used_bytes'))} / "
        f"{human_bytes(root.get('total_bytes'))}{pct_str} used, "
        f"{human_bytes(root.get('free_bytes'))} free"
    )


def _thermal_line(data: dict) -> str:
    hottest = data.get("hottest_c")
    count = len(data.get("sensors", []))
    return f"hottest {hottest:.1f} C across {count} sensors" if hottest is not None else "n/a"


def _containers_line(data: dict) -> str:
    items = data.get("containers", [])
    gpu_count = sum(1 for c in items if c.get("gpu"))
    unhealthy = sum(1 for c in items if "unhealthy" in str(c.get("status", "")).lower())
    note = f", {unhealthy} unhealthy" if unhealthy else ""
    return f"{len(items)} running ({gpu_count} GPU-likely){note}"


def _network_line(data: dict) -> str:
    reachable = data.get("reachable_ipv4", [])
    routes = data.get("default_routes", [])
    dev = routes[0]["dev"] if routes else "?"
    addrs = ", ".join(reachable) if reachable else "none"
    return f"reachable {addrs}; default via {dev}; {data.get('bridge_count', 0)} bridges"


def _processes_line(data: dict) -> str:
    top = data.get("top", [])
    count = data.get("count", 0)
    if not top:
        return f"{count} processes"
    first = top[0]
    return f"{count} processes; top: {first.get('name')} ({human_bytes(first.get('rss_bytes'))})"


def collect(runner: Optional[Runner] = None) -> dict:
    """Return the machine-wide status report (anomalies first)."""
    run = runner or default_runner
    facts = host.facts(run)
    subs = {
        "memory": memory.collect(),
        "gpu": gpu.collect(run),
        "disk": disk.collect(),
        "thermal": thermal.collect(),
        "containers": containers.collect(run),
        "network": network.collect(run),
        "processes": processes.collect(),
    }

    warnings: list[str] = []
    for name, rep in subs.items():
        for warning in rep.get("warnings", []):
            warnings.append(f"[{name}] {warning}")

    gpu_name = ((subs["gpu"].get("data") or {}).get("gpu") or {}).get("name") or ""
    sections = [{"title": "Host", "items": _host_items(facts, gpu_name)}]

    builders = {
        "memory": _memory_line,
        "gpu": _gpu_line,
        "disk": _disk_line,
        "thermal": _thermal_line,
        "containers": _containers_line,
        "network": _network_line,
        "processes": _processes_line,
    }
    summary_items = [f"{name}: {_line(subs[name], builders[name])}" for name in builders]
    sections.append({"title": "Subsystems", "items": summary_items})
    sections.append(
        {
            "title": "Drill down",
            "items": ["spark <memory|gpu|disk|thermal|containers|network|processes> [--json]"],
        }
    )

    data = {"host": facts, "subsystems": {k: v.get("data", {}) for k, v in subs.items()}}
    return report("status", source="aggregate", sections=sections, warnings=warnings, data=data)
