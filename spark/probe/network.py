"""``spark network`` — interfaces, default route, and reachable addresses.

Summarizes ``ip -br addr`` and ``ip route show default`` rather than dumping
every veth: named interfaces (wifi/ethernet/tailscale/bridges) are listed with
their IPv4, while the many container ``veth`` pairs are rolled up to a count.
Graceful: no ``ip`` -> unavailable.
"""

from __future__ import annotations

import re
from typing import Optional

from spark.probe._report import report, unavailable
from spark.probe._run import Runner, default_runner

_IPV4 = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})/\d+$")


def _classify(name: str) -> str:
    if name == "lo":
        return "loopback"
    if name.startswith("tailscale"):
        return "tailscale"
    if name.startswith("veth"):
        return "veth"
    if name.startswith(("br-", "docker")):
        return "bridge"
    if name.startswith("wl"):
        return "wifi"
    if name.startswith(("en", "eth")):
        return "ethernet"
    return "other"


def _ipv4s(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for tok in tokens:
        match = _IPV4.match(tok)
        if match:
            out.append(match.group(1))
    return out


def _default_routes(run: Runner) -> list[dict]:
    out = run("ip", ["route", "show", "default"])
    routes: list[dict] = []
    if not out:
        return routes
    for line in out.splitlines():
        parts = line.split()
        if not parts or parts[0] != "default":
            continue
        info = {"via": None, "dev": None, "src": None}
        for i, tok in enumerate(parts):
            if tok in info and i + 1 < len(parts):
                info[tok] = parts[i + 1]
        routes.append(info)
    return routes


def _is_reachable(kind: str, addr: str) -> bool:
    # Real LAN/VPN addresses, not docker bridge gateways or container/loopback.
    return kind in ("wifi", "ethernet", "tailscale", "other") and not addr.startswith(
        ("127.", "169.254.")
    )


def _parse_addr(out: str) -> tuple[list[dict], int, int, list[str]]:
    """Parse ``ip -br addr`` into (named interfaces, veth#, bridge#, reachable)."""
    interfaces: list[dict] = []
    veth_count = 0
    bridge_count = 0
    reachable: list[str] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].split("@")[0]
        state = parts[1]
        kind = _classify(name)
        ipv4s = _ipv4s(parts[2:])
        reachable.extend(a for a in ipv4s if _is_reachable(kind, a))
        if kind == "veth":
            veth_count += 1
            continue
        if kind == "bridge":
            bridge_count += 1
        interfaces.append({"name": name, "kind": kind, "state": state, "ipv4": ipv4s})
    return interfaces, veth_count, bridge_count, reachable


def _build_sections(
    interfaces: list[dict], routes: list[dict], reachable: list[str], veths: int, bridges: int
) -> list[dict]:
    sections: list[dict] = []
    if routes:
        sections.append(
            {
                "title": "Default route",
                "items": [f"via {r['via']} dev {r['dev']} (src {r['src']})" for r in routes],
            }
        )
    iface_items = [
        f"{i['name']} [{i['kind']}] {i['state']}: {', '.join(i['ipv4']) or '(no ipv4)'}"
        for i in interfaces
        if i["kind"] != "loopback"
    ]
    if iface_items:
        sections.append({"title": "Interfaces", "items": iface_items})
    summary = []
    if reachable:
        summary.append("reachable IPv4: " + ", ".join(sorted(set(reachable))))
    summary.append(f"docker bridges: {bridges}")
    summary.append(f"container veth pairs: {veths}")
    sections.append({"title": "Summary", "items": summary})
    return sections


def collect(runner: Optional[Runner] = None) -> dict:
    """Return a network report using ``runner`` (injectable; defaults to ip)."""
    run = runner or default_runner
    out = run("ip", ["-br", "addr"])
    if out is None:
        return unavailable("network", "ip -br addr", "install iproute2 (the 'ip' command)")

    interfaces, veth_count, bridge_count, reachable = _parse_addr(out)
    routes = _default_routes(run)
    sections = _build_sections(interfaces, routes, reachable, veth_count, bridge_count)
    warnings = [] if routes else ["no default route — host may be offline"]
    data = {
        "interfaces": interfaces,
        "default_routes": routes,
        "veth_count": veth_count,
        "bridge_count": bridge_count,
        "reachable_ipv4": sorted(set(reachable)),
    }
    return report("network", source="ip", sections=sections, warnings=warnings, data=data)
