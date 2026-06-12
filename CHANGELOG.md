# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-06-12

### Added

- Monitor I/O-contention alerts: new `iowait_pct` (default 25%) and `blocked_procs` (default 8) thresholds, backed by a `spark.probe.contention` collector that samples `/proc/stat` for iowait %% and the blocked-process (D-state) count. Surfaces swap-thrash / I/O-starvation that the capacity thresholds (memory/swap %% full) miss.

## [0.4.0] - 2026-06-02

### Added

- `monitor run` posts a one-shot "started watching" liveness alert on startup, so a watchdog that silently fails to come up is noticed by the absence of its heartbeat rather than the absence of an alert that should have fired. A failed startup POST is logged but never blocks the loop.
- `notify_on_start` config flag (default `true`) toggles the startup alert; it is surfaced in `monitor config` and the monitor.json scaffold.
- Env-gated live webhook test (`tests/test_monitor_live.py`, reads `DGX_SPARK_TEST_WEBHOOK_URL`) that POSTs a real startup alert and asserts 2xx delivery; auto-detects Discord/Slack/generic format and self-skips when the variable is unset (fork PRs / contributors).

## [0.3.1] - 2026-05-30

### Fixed

- Webhook delivery now sends an explicit `User-Agent` header (`dgx-spark-cli-monitor/<version>`). Discord and other Cloudflare-fronted webhooks reject the default `Python-urllib/x.y` User-Agent with HTTP 403, so `monitor test` and `monitor run` failed to deliver to a Discord webhook.

## [0.3.0] - 2026-05-30

### Added

- `monitor` — a deterministic, AI-free watchdog. It evaluates the machine-scope collectors against configurable thresholds and POSTs to a generic webhook when a catastrophe condition crosses, and again when it clears. Verbs: `check` (dry run), `once` (one cycle), `run` (foreground loop), `test` (synthetic alert), `config [--init]`, and `install`/`enable`/`disable`/`status`/`uninstall` for a systemd `--user` service the CLI installs and manages.
- Edge-triggered alerting: fires on OK->ALERT, sends a `resolved` event on recovery, and re-notifies a standing condition only every `renotify_cycles` cycles — so it catches catastrophes without spamming. Alert state is persisted under `$XDG_STATE_HOME`; undelivered events are retried rather than dropped.
- Zero-dependency webhook delivery (`urllib`): http(s)-only (scheme allowlist), bounded retries and timeouts, and it never raises into the loop. Payloads are generic JSON, or Slack/Discord chat presets via `webhook_format`.
- Watches memory %, swap %, disk %, hottest sensor, GPU temp, load-per-core, container health, and subsystem availability (nvidia-smi/docker going dark). Thresholds and the webhook live in `~/.config/dgx-spark/monitor.json` (a `null` threshold disables that check); `DGX_SPARK_WEBHOOK_URL` overrides the webhook.

### Changed

- Added `spark.probe._run.run_capture`, which returns `(returncode, stdout)` even on a non-zero exit — for tools that convey state through the exit code (e.g. `systemctl --user is-active`).

## [0.2.0] - 2026-05-30

### Added

- Machine-scope host-telemetry verbs for the DGX Spark: `status` (machine-wide scope, anomalies first), `memory`, `gpu`, `disk`, `thermal`, `containers`, `network`, and `processes`. All are read-only, support `--json`, route results to stdout / diagnostics to stderr, and exit 0 even when a subsystem is absent (reporting `available: false`) — `doctor` remains the health gate.
- `spark gpu` derives GPU-attributed memory by summing per-process compute-app usage, because the GB10's unified LPDDR5X pool makes `nvidia-smi`'s aggregate VRAM report `[N/A]`. `spark memory` reports the shared CPU+GPU pool and flags swap pressure.
- New zero-runtime-dependency `spark.probe` package: kernel telemetry is read from `/proc` and `/sys`, while `nvidia-smi`, `docker`, and `ip` are resolved via `shutil.which` and shelled out, degrading gracefully when absent. Every collector takes an injectable file root / command runner so the suite runs on x86 CI with no GPU, docker, or aarch64.
- `explain` catalog entries and `learn`/`overview` command maps for all eight new verbs.

### Changed

- Extracted the section renderer into `spark.cli._output.render_sections`, now shared by `overview` and the new probe verbs so they speak one text format.
- Machine-scope verbs now accept and ignore stray positional arguments (exit 0), matching `overview`'s "descriptive verbs never hard-fail" contract.

## [0.1.2] - 2026-05-30

### Changed

- Replaced the seed CLAUDE.md bootstrap placeholder with a full runtime prompt via /init — documents the agent-first CLI architecture (CliError/output contracts, zero-runtime-dependency rule), the rubric constraints for adding commands, the version-bump-every-PR rule, and flags that the installed console script is spark, not dgx-spark-cli.

### Fixed

- **`explain spark` now resolves to the root entry.** The agent-first rubric's `explain_self` check probes the `[project.scripts]` entry name (`spark`), but the explain catalog only aliased the root under the dist name (`dgx-spark-cli`), so `teken cli doctor . --strict` failed `explain_self` in CI. Added a `("spark",)` catalog alias (mirroring `("dgx-spark-cli",)`) and a regression test.

## [0.1.1] - 2026-05-26

### Changed

- **CI gates on the SonarCloud quality gate**
  ([issue #3](https://github.com/agentculture/dgx-spark-cli/issues/3)) —
  added `sonar.qualitygate.wait=true` to `sonar-project.properties` so a failing
  gate fails the `test` job when `SONAR_TOKEN` is set. Token-less repos and fork
  PRs remain green (the scan step is guarded by `if: env.SONAR_TOKEN != ''`).

## [0.1.0] - 2026-05-26

### Added

- **Onboarded into the AgentCulture mesh** ([issue #1](https://github.com/agentculture/dgx-spark-cli/issues/1)).
- **Agent-first CLI** cited from teken's (`afi-cli`) `python-cli` reference
  (`teken cli cite`) — verbs `whoami`, `learn`, `explain`, `overview`, `doctor`,
  and the `cli` noun group. Runtime is self-contained (`dependencies = []`);
  `teken>=0.8` is a dev dependency only. Passes the seven-bundle agent-first
  rubric (`teken cli doctor . --strict`). `doctor` checks the agent-identity
  invariants (prompt-file-present, backend-consistency, skills-present).
- **Mesh identity**: `culture.yaml` (`suffix: dgx-spark-cli`,
  `backend: claude`) and the matching `CLAUDE.md` prompt file.
- **Canonical guildmaster skill kit** (11 skills) vendored under
  `.claude/skills/` (cite-don't-import): `agent-config`, `assign-to-workforce`,
  `cicd`, `communicate`, `doc-test-alignment`, `pypi-maintainer`, `run-tests`,
  `sonarclaude`, `spec-to-plan`, `think`, `version-bump`. Every `SKILL.md`
  carries `type: command` (load-bearing for the culture/claude backend);
  `cicd` / `communicate` consumer-identifying prose adapted, all script bodies
  verbatim. Provenance in `docs/skill-sources.md`. Three skills (`think`,
  `spec-to-plan`, `assign-to-workforce`) originate in `devague`, re-broadcast
  via guildmaster.
- **Build + deploy baseline**: `pyproject.toml` (hatchling), `tests/` (pytest,
  xdist, coverage), `.github/workflows/{tests,publish}.yml` (CI rubric/lint gate,
  PyPI Trusted Publishing), `.flake8`, `.markdownlint-cli2.yaml`,
  `sonar-project.properties`, and `.claude/skills.local.yaml.example`.

### Changed

### Fixed
