"""Markdown catalog for ``dgx-spark-cli explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple,
the console-script name ``("spark",)``, and the display name
``("dgx-spark-cli",)`` all resolve to the root entry. ``explain spark`` must
resolve because the agent-first rubric's ``explain_self`` check probes the
``[project.scripts]`` entry name (``spark``), not the dist name.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# dgx-spark-cli

A clonable template for AgentCulture mesh agents. It carries an agent-first CLI
(cited from the teken `python-cli` reference), a mesh identity (`culture.yaml` +
`CLAUDE.md`), the canonical guildmaster skill kit under `.claude/skills/`, and a
buildable/deployable package baseline. Clone it, rename the package, edit
`culture.yaml`, and you have a new agent.

## Agent verbs

- `dgx-spark-cli whoami` — identity probe from `culture.yaml`.
- `dgx-spark-cli learn` — structured self-teaching prompt.
- `dgx-spark-cli explain <path>` — markdown docs for any noun/verb.
- `dgx-spark-cli overview` — descriptive snapshot of the agent.
- `dgx-spark-cli doctor` — check the agent-identity invariants.
- `dgx-spark-cli cli overview` — describe the CLI surface.

## Machine-scope verbs (DGX Spark host telemetry)

- `dgx-spark-cli status` — machine-wide scope, anomalies first (the headline).
- `dgx-spark-cli memory` — unified RAM + swap (CPU and GPU share one pool).
- `dgx-spark-cli gpu` — Blackwell GB10: utilization, temp, power, GPU processes.
- `dgx-spark-cli disk` — filesystem usage for real block devices.
- `dgx-spark-cli thermal` — SoC thermal zones and hwmon sensors.
- `dgx-spark-cli containers` — running Docker containers and health.
- `dgx-spark-cli network` — interfaces, default route, reachable addresses.
- `dgx-spark-cli processes` — top processes by resident memory.

All machine-scope verbs are read-only, support `--json`, and exit 0 even when a
subsystem is absent (it reports `available: false`). `doctor` is the health gate.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `dgx-spark-cli explain whoami`
- `dgx-spark-cli explain doctor`
"""

_WHOAMI = """\
# dgx-spark-cli whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    dgx-spark-cli whoami
    dgx-spark-cli whoami --json
"""

_LEARN = """\
# dgx-spark-cli learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    dgx-spark-cli learn
    dgx-spark-cli learn --json
"""

_EXPLAIN = """\
# dgx-spark-cli explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    dgx-spark-cli explain dgx-spark-cli
    dgx-spark-cli explain whoami
    dgx-spark-cli explain --json <path>
"""

_OVERVIEW = """\
# dgx-spark-cli overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    dgx-spark-cli overview
    dgx-spark-cli overview --json
"""

_DOCTOR = """\
# dgx-spark-cli doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`claude` → `CLAUDE.md`), plus a
skills-present check. Exits 1 when unhealthy.

## Usage

    dgx-spark-cli doctor
    dgx-spark-cli doctor --json
"""

_CLI = """\
# dgx-spark-cli cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    dgx-spark-cli cli overview
    dgx-spark-cli cli overview --json
"""


_STATUS = """\
# dgx-spark-cli status

Machine-wide scope of the DGX Spark, anomalies first. Calls every domain
collector once and prints a Host header, an Attention block (merged warnings
from all subsystems), and a compact one-liner per subsystem. The headline entry
point — drill into any line with its verb (`memory`, `gpu`, …).

Read-only; exits 0 even if a subsystem is unavailable.

## Usage

    dgx-spark-cli status
    dgx-spark-cli status --json
"""

_MEMORY = """\
# dgx-spark-cli memory

Unified memory + swap snapshot from `/proc/meminfo`. On the GB10 the Grace CPU
and Blackwell GPU share ONE ~128 GiB LPDDR5X pool — there is no separate VRAM —
so memory pressure here is a GPU-workload signal too. Warns on low available
memory or heavy swap use.

## Usage

    dgx-spark-cli memory
    dgx-spark-cli memory --json
"""

_GPU = """\
# dgx-spark-cli gpu

Blackwell GB10 snapshot via `nvidia-smi`: utilization, temperature, power,
clocks, and GPU compute processes. Because memory is unified, `nvidia-smi`
reports aggregate `memory.total/used` as `[N/A]`; this verb instead sums the
per-process compute-app memory to report how much of the shared pool is
attributed to the GPU. Unavailable (exit 0) when no `nvidia-smi` is present.

## Usage

    dgx-spark-cli gpu
    dgx-spark-cli gpu --json
"""

_DISK = """\
# dgx-spark-cli disk

Filesystem usage for real (non-virtual) block devices, read via `/proc/mounts`
and `os.statvfs` — no `df` dependency. Virtual filesystems and snap `loop`
mounts are filtered out. Warns when a filesystem is >=85% full.

## Usage

    dgx-spark-cli disk
    dgx-spark-cli disk --json
"""

_THERMAL = """\
# dgx-spark-cli thermal

SoC thermal zones (`/sys/class/thermal`) and hwmon sensors
(`/sys/class/hwmon`: nvme, wifi PHY, …) in Celsius. No `lm-sensors` dependency.
GPU die temperature comes from `nvidia-smi` (see `gpu`). Warns on any sensor at
or above 85 C.

## Usage

    dgx-spark-cli thermal
    dgx-spark-cli thermal --json
"""

_CONTAINERS = """\
# dgx-spark-cli containers

Running Docker containers via `docker ps`, with health. On the Spark the GPU/ML
workloads run as containers (vllm, NIM services), so this is the workload layer.
Images served from `nvcr.io` are tagged GPU-likely (heuristic). Warns on any
container reporting `(unhealthy)`. Unavailable (exit 0) when docker is absent or
the daemon is down.

## Usage

    dgx-spark-cli containers
    dgx-spark-cli containers --json
"""

_NETWORK = """\
# dgx-spark-cli network

Interfaces, default route, and reachable addresses, summarized from `ip -br
addr` and `ip route show default`. Named interfaces (wifi/ethernet/tailscale/
bridges) are listed with their IPv4; the many container `veth` pairs are rolled
up to a count. "Reachable" excludes docker bridge gateways and link-local.

## Usage

    dgx-spark-cli network
    dgx-spark-cli network --json
"""

_PROCESSES = """\
# dgx-spark-cli processes

Top processes by resident memory (`VmRSS`), read straight from `/proc` — no
`ps` dependency. RSS is the right lens on a unified-memory box: the share of the
one shared pool a process holds resident. Kernel threads (no `VmRSS`) are
skipped.

## Usage

    dgx-spark-cli processes
    dgx-spark-cli processes --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("spark",): _ROOT,
    ("dgx-spark-cli",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
    ("status",): _STATUS,
    ("memory",): _MEMORY,
    ("gpu",): _GPU,
    ("disk",): _DISK,
    ("thermal",): _THERMAL,
    ("containers",): _CONTAINERS,
    ("network",): _NETWORK,
    ("processes",): _PROCESSES,
}
