"""``dgx-spark-cli learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.
"""

from __future__ import annotations

import argparse

from spark import __version__
from spark.cli._output import emit_result

_TEXT = """\
dgx-spark-cli — a clonable template for AgentCulture mesh agents.

Purpose
-------
Scaffold for a new Culture mesh agent: an agent-first CLI (cited from the teken
`python-cli` reference), an identity (culture.yaml + CLAUDE.md), the canonical
guildmaster skill kit under .claude/skills/, and a deploy/CI baseline. Clone it,
rename the package, and edit culture.yaml to mint a new agent.

Command map
-----------
Commands by path:

  dgx-spark-cli whoami             Identity from culture.yaml.
  dgx-spark-cli learn              This self-teaching prompt.
  dgx-spark-cli explain <path>...  Markdown docs for any noun/verb path.
  dgx-spark-cli overview           Descriptive snapshot of the agent.
  dgx-spark-cli doctor             Check the agent-identity invariants.
  dgx-spark-cli cli overview       Describe the CLI surface itself.

Machine scope (DGX Spark host telemetry)
----------------------------------------
  dgx-spark-cli status             Machine-wide scope, anomalies first.
  dgx-spark-cli memory             Unified RAM + swap (CPU and GPU share it).
  dgx-spark-cli gpu                Blackwell GB10: util, temp, power, processes.
  dgx-spark-cli disk               Filesystem usage for real block devices.
  dgx-spark-cli thermal            SoC thermal zones and hwmon sensors.
  dgx-spark-cli containers         Running Docker containers and health.
  dgx-spark-cli network            Interfaces, routes, reachable addresses.
  dgx-spark-cli processes          Top processes by resident memory.
These are read-only and exit 0 even when a subsystem is absent.

Monitoring (AI-free background watchdog)
----------------------------------------
  dgx-spark-cli monitor check      Evaluate thresholds now (no webhook).
  dgx-spark-cli monitor once       One cycle: evaluate + webhook + state.
  dgx-spark-cli monitor run        Foreground watch loop (systemd ExecStart).
  dgx-spark-cli monitor test       POST a synthetic alert to the webhook.
  dgx-spark-cli monitor config     Show/scaffold thresholds + webhook.
  dgx-spark-cli monitor install    Manage a systemd --user service.
Webhooks on catastrophes (memory/disk/thermal/GPU/containers); no AI.

Machine-readable output
-----------------------
Every command supports --json. Errors in JSON mode emit
{"code", "message", "remediation"} to stderr. Stdout and stderr never mix.

Exit-code policy
----------------
  0 success
  1 user-input error (bad flag, bad path, missing arg)
  2 environment / setup error
  3+ reserved

More detail
-----------
  dgx-spark-cli explain dgx-spark-cli
"""


def _as_json_payload() -> dict[str, object]:
    return {
        "tool": "dgx-spark-cli",
        "version": __version__,
        "purpose": "Clonable scaffold for a new AgentCulture mesh agent.",
        "commands": [
            {"path": ["whoami"], "summary": "Identity probe from culture.yaml."},
            {"path": ["learn"], "summary": "Self-teaching prompt."},
            {"path": ["explain"], "summary": "Markdown docs by path."},
            {"path": ["overview"], "summary": "Descriptive snapshot of the agent."},
            {"path": ["doctor"], "summary": "Check the agent-identity invariants."},
            {"path": ["cli", "overview"], "summary": "Describe the CLI surface."},
            {"path": ["status"], "summary": "Machine-wide scope, anomalies first."},
            {"path": ["memory"], "summary": "Unified RAM + swap snapshot."},
            {"path": ["gpu"], "summary": "Blackwell GB10 GPU snapshot."},
            {"path": ["disk"], "summary": "Filesystem usage."},
            {"path": ["thermal"], "summary": "Thermal zones and hwmon sensors."},
            {"path": ["containers"], "summary": "Running Docker containers and health."},
            {"path": ["network"], "summary": "Interfaces, routes, reachable addresses."},
            {"path": ["processes"], "summary": "Top processes by resident memory."},
            {"path": ["monitor", "check"], "summary": "Evaluate alert thresholds now."},
            {"path": ["monitor", "once"], "summary": "One monitor cycle + webhook delivery."},
            {"path": ["monitor", "run"], "summary": "Foreground watchdog loop."},
            {"path": ["monitor", "config"], "summary": "Show/scaffold monitor config."},
        ],
        "exit_codes": {
            "0": "success",
            "1": "user-input error",
            "2": "environment/setup error",
        },
        "json_support": True,
        "explain_pointer": "dgx-spark-cli explain <path>",
    }


def cmd_learn(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        emit_result(_as_json_payload(), json_mode=True)
    else:
        emit_result(_TEXT, json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_learn)
