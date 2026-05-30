"""``spark containers`` — running Docker containers and their health.

On the Spark, GPU/ML workloads run as containers (vllm, NIM services, …), so
this is the workload layer. Reads ``docker ps`` (running only) and flags any
container reporting ``(unhealthy)``. Images served from ``nvcr.io`` are tagged
as GPU-likely (a heuristic, not authoritative). Graceful: no docker / daemon
down -> unavailable.
"""

from __future__ import annotations

import json
from typing import Optional

from spark.probe._report import report, unavailable
from spark.probe._run import Runner, default_runner


def _is_gpu_image(image: str) -> bool:
    image = image.lower()
    return image.startswith("nvcr.io/") or any(
        tag in image for tag in ("cuda", "vllm", "nim/", "tensorrt", "nemo")
    )


def collect(runner: Optional[Runner] = None) -> dict:
    """Return a containers report using ``runner`` (injectable; defaults to docker)."""
    run = runner or default_runner
    out = run("docker", ["ps", "--format", "{{json .}}"])
    if out is None:
        return unavailable(
            "containers",
            "docker ps",
            "install docker and ensure the daemon is running ('docker ps')",
        )

    containers: list[dict] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = obj.get("Names", "?")
        status = obj.get("Status", "?")
        image = obj.get("Image", "?")
        containers.append(
            {
                "name": name,
                "status": status,
                "image": image,
                "state": obj.get("State", ""),
                "gpu": _is_gpu_image(image),
            }
        )

    warnings: list[str] = []
    items: list[str] = []
    gpu_count = 0
    for c in containers:
        if c["gpu"]:
            gpu_count += 1
        tag = " [gpu]" if c["gpu"] else ""
        items.append(f"{c['name']}{tag}  {c['status']}  ({c['image']})")
        if "unhealthy" in c["status"].lower():
            warnings.append(f"container '{c['name']}' is unhealthy")

    title = f"Running containers ({len(containers)}, {gpu_count} GPU-likely)"
    sections = [{"title": title, "items": items or ["no running containers"]}]
    return report(
        "containers",
        source="docker ps",
        sections=sections,
        warnings=warnings,
        data={"containers": containers},
    )
