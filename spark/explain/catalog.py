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

## Verbs

- `dgx-spark-cli whoami` — identity probe from `culture.yaml`.
- `dgx-spark-cli learn` — structured self-teaching prompt.
- `dgx-spark-cli explain <path>` — markdown docs for any noun/verb.
- `dgx-spark-cli overview` — descriptive snapshot of the agent.
- `dgx-spark-cli doctor` — check the agent-identity invariants.
- `dgx-spark-cli cli overview` — describe the CLI surface.

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
}
