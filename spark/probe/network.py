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


def collect(runner: Optional[Runner] = None) -> dict:
    """Return a network report using ``runner`` (injectable; defaults to ip)."""
    run = runner or default_runner
    out = run("ip", ["-br", "addr"])
    if out is None:
        return unavailable("network", "ip -br addr", "install iproute2 (the 'ip' command)")

    interfaces: list[dict] = []
    veth_count = 0
    bridge_count = 0
    reachable: list[str] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        raw_name, state = parts[0], parts[1]
        name = raw_name.split("@")[0]
        kind = _classify(name)
        ipv4s = _ipv4s(parts[2:])
        # "Reachable" = real LAN/VPN addresses, not docker bridge gateways or
        # container veth/loopback links.
        if kind in ("wifi", "ethernet", "tailscale", "other"):
            for addr in ipv4s:
                if not addr.startswith(("127.", "169.254.")):
                    reachable.append(addr)
        if kind == "veth":
            veth_count += 1
            continue
        if kind == "bridge":
            bridge_count += 1
        interfaces.append({"name": name, "kind": kind, "state": state, "ipv4": ipv4s})

    routes = _default_routes(run)

    sections: list[dict[str, object]] = []
    if routes:
        sections.append(
            {
                "title": "Default route",
                "items": [f"via {r['via']} dev {r['dev']} (src {r['src']})" for r in routes],
            }
        )

    iface_items = []
    for iface in interfaces:
        if iface["kind"] == "loopback":
            continue
        addrs = ", ".join(iface["ipv4"]) or "(no ipv4)"
        iface_items.append(f"{iface['name']} [{iface['kind']}] {iface['state']}: {addrs}")
    if iface_items:
        sections.append({"title": "Interfaces", "items": iface_items})

    summary = []
    if reachable:
        summary.append("reachable IPv4: " + ", ".join(sorted(set(reachable))))
    summary.append(f"docker bridges: {bridge_count}")
    summary.append(f"container veth pairs: {veth_count}")
    sections.append({"title": "Summary", "items": summary})

    warnings: list[str] = []
    if not routes:
        warnings.append("no default route — host may be offline")

    data = {
        "interfaces": interfaces,
        "default_routes": routes,
        "veth_count": veth_count,
        "bridge_count": bridge_count,
        "reachable_ipv4": sorted(set(reachable)),
    }
    return report("network", source="ip", sections=sections, warnings=warnings, data=data)
