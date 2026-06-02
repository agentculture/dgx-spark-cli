# dgx-spark-cli

Agent and CLI for operating an NVIDIA DGX Spark (Grace-Blackwell) workstation — device setup, health/monitoring, and local AI/ML workload management.

## What you get

- **An agent-first CLI** cited from [teken](https://github.com/agentculture/teken)
  (`afi-cli`) — the runtime package has no third-party dependencies.
- **A mesh identity** — `culture.yaml` (`suffix` + `backend`) and the matching
  prompt file (`CLAUDE.md` for `backend: claude`).
- **The canonical guildmaster skill kit** (11 skills) under `.claude/skills/`,
  vendored cite-don't-import. See [`docs/skill-sources.md`](docs/skill-sources.md).
- **A build + deploy baseline** — pytest, lint, the agent-first rubric gate, and
  PyPI Trusted Publishing wired into GitHub Actions.

## Quickstart

```bash
uv sync
uv run pytest -n auto                 # run the test suite
uv run dgx-spark-cli whoami  # identity from culture.yaml
uv run dgx-spark-cli learn   # self-teaching prompt (add --json)
uv run teken cli doctor . --strict    # the agent-first rubric gate CI runs
```

## CLI

| Verb | What it does |
|------|--------------|
| `whoami` | Report this agent's nick, version, backend, and model from `culture.yaml`. |
| `learn` | Print a structured self-teaching prompt. |
| `explain <path>` | Markdown docs for any noun/verb path. |
| `overview` | Read-only descriptive snapshot of the agent. |
| `doctor` | Check the agent-identity invariants (prompt-file-present, backend-consistency). |
| `cli overview` | Describe the CLI surface itself. |

### Machine scope (DGX Spark host telemetry)

The Spark *is* the system, so these read-only verbs sit at the top level:

| Verb | What it does |
|------|--------------|
| `status` | Machine-wide scope, anomalies first — the headline. |
| `memory` | Unified RAM + swap (the GB10 shares one pool across CPU and GPU). |
| `gpu` | Blackwell GB10: utilization, temp, power, clocks, and GPU processes. |
| `disk` | Filesystem usage for real block devices (via `/proc/mounts` + `statvfs`). |
| `thermal` | SoC thermal zones and hwmon sensors (no `lm-sensors` needed). |
| `containers` | Running Docker containers and their health. |
| `network` | Interfaces, default route, and reachable addresses. |
| `processes` | Top processes by resident memory (via `/proc`). |

They have zero runtime dependencies — kernel telemetry is read from `/proc` and
`/sys`, while `nvidia-smi`, `docker`, and `ip` are shelled out and degrade
gracefully (a missing tool reports `available: false` and still exits 0).
`doctor` remains the health gate. Because the GB10 has no discrete VRAM,
`nvidia-smi` reports aggregate GPU memory as `[N/A]`; `gpu` instead sums
per-process compute-app memory so you can see how much of the shared pool the
GPU holds.

Every command supports `--json`. Results go to stdout, errors/diagnostics to
stderr (never mixed). Exit codes: `0` success, `1` user error, `2` environment
error, `3+` reserved.

### Monitoring (`monitor`) — AI-free webhook watchdog

`monitor` turns the collectors into a deterministic, always-on watchdog. It
evaluates the same numbers against configurable thresholds and POSTs to a
**generic webhook** when a catastrophe condition crosses — and again when it
clears (edge-triggered, so a standing condition doesn't spam). No AI, no new
dependencies (`urllib` does the POST).

```bash
dgx-spark-cli monitor config --init     # scaffold ~/.config/dgx-spark/monitor.json
export DGX_SPARK_WEBHOOK_URL=https://…  # or put webhook_url in the config
dgx-spark-cli monitor check             # dry run: what's firing right now
dgx-spark-cli monitor test              # POST a synthetic alert
dgx-spark-cli monitor install           # write the systemd --user unit
dgx-spark-cli monitor enable            # start it always-on (+ linger)
dgx-spark-cli monitor status            # service + currently firing alerts
```

| Verb | What it does |
|------|--------------|
| `monitor check` | Evaluate thresholds now (no webhook, no state change). |
| `monitor once` | One cycle: evaluate, deliver transitions, update state. |
| `monitor run` | Foreground watch loop (the systemd `ExecStart`). |
| `monitor test` | POST a synthetic alert to verify the webhook. |
| `monitor config [--init]` | Show resolved config / write a scaffold. |
| `monitor install\|enable\|disable\|status\|uninstall` | Manage the systemd `--user` service. |

Watches memory %, swap %, disk %, hottest sensor, GPU temp, load-per-core,
container health, and subsystem availability. Thresholds live in the config
(`null` disables a check); `webhook_format` is `generic` (default), `slack`, or
`discord`.

When `monitor run` starts (the systemd `ExecStart`), it POSTs a one-shot
**"started watching"** liveness alert — so a watchdog that silently fails to come
up is noticed by the absence of its heartbeat, not just the absence of an alert.
A failed startup POST is logged, never fatal. Set `notify_on_start: false` in the
config to disable it.

## Make it your own

1. Rename the package `spark/` and the `dgx-spark-cli`
   CLI/dist name throughout `pyproject.toml`, the package, `tests/`, and
   `sonar-project.properties`.
2. Edit `culture.yaml` with your `suffix` and `backend`.
3. Rewrite `CLAUDE.md` for your agent and run `/init`.
4. Re-vendor only the skills you need from guildmaster (see
   [`docs/skill-sources.md`](docs/skill-sources.md)).

See [`CLAUDE.md`](CLAUDE.md) for the full conventions (version-bump-every-PR,
the `cicd` PR lane, deploy setup).

## License

MIT — see [`LICENSE`](LICENSE).
