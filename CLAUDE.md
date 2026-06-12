# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is (read first)

`dgx-spark-cli` is an **AgentCulture mesh agent** scaffolded from the
`culture-agent-template` (whose CLI was cited from teken's `python-cli`
reference). Its *intended* domain is operating an NVIDIA DGX Spark
(Grace-Blackwell) workstation — device setup, health/monitoring, local AI/ML
workload management — but **none of that domain logic exists yet**. What's here
today is the agent-first scaffold: an introspection CLI, a mesh identity, the
vendored skill kit, and a build/CI/deploy baseline. New domain functionality is
added as **noun groups** (see "Adding a command" below).

This file is dual-purpose: it's both this guidance doc *and* the agent's runtime
prompt — `doctor`'s `backend-consistency` invariant requires it to exist because
`culture.yaml` declares `backend: claude`.

## The binary is `spark`, not `dgx-spark-cli`

`[project.scripts]` registers **`spark`** (`spark = "spark.cli:main"`). All the
help text, README, `learn`/`explain` output, and `prog=` strings say
`dgx-spark-cli` — that's the *display* name, not the command. Use one of:

```bash
uv run spark <verb>      # the installed console script
python -m spark <verb>   # module entry (spark/__main__.py)
```

`uv run dgx-spark-cli …` does **not** work. (The README's `dgx-spark-cli whoami`
examples are display-name artifacts.)

## Common commands

```bash
uv sync                                   # install deps (creates .venv)
uv run pytest -n auto                      # full suite, parallel (xdist)
uv run pytest tests/test_cli.py::test_whoami_text   # a single test
uv run spark whoami                        # run the CLI

# Lint — all five must pass (CI `lint` job runs exactly these):
uv run black --check spark tests
uv run isort --check-only spark tests
uv run flake8 spark tests
uv run bandit -c pyproject.toml -r spark
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"

uv run teken cli doctor . --strict         # the agent-first rubric gate (CI enforces)
```

Black/isort/flake8 are pinned to **line length 100**. Coverage gate is
`fail_under = 60` (`[tool.coverage.report]`).

## Architecture

### Agent-first CLI (`spark/cli/`)

`main()` (`spark/cli/__init__.py`) builds an argparse tree, then `_dispatch()`
invokes the matched handler. Two hard contracts run through every command:

- **Error contract** (`spark/cli/_errors.py`, `_output.py`): every failure
  raises `CliError(code, message, remediation)`. `_dispatch` catches it, routes
  it through `emit_error`, and returns the code — **no Python traceback ever
  reaches stderr**. Any non-`CliError` exception is wrapped into one. Even
  argparse failures are routed: `_CliArgumentParser.error()` overrides the
  default `prog: error:` / exit-2 behavior to emit the `error:` / `hint:` shape
  and exit 1. Because parse errors fire *before* `args.json` exists, `main()`
  peeks raw argv for `--json` and stashes it on `_CliArgumentParser._json_hint`.
- **Output contract** (`_output.py`): **results → stdout, diagnostics/errors →
  stderr, never mixed.** Every command takes `--json`; JSON mode routes
  structured payloads to the same split streams.
- **Exit codes**: `0` success, `1` user error, `2` environment error, `3+`
  reserved (`spark/cli/_errors.py`).

Command modules live in `spark/cli/_commands/`. Each exposes a
`register(sub)` that adds its subparser and sets `func`. Global verbs:
`whoami`, `learn`, `explain`, `overview`, `doctor`. Noun groups (e.g. `cli`)
register a subparser that itself has sub-verbs.

- `whoami` reads identity from **`culture.yaml`**, located by walking up from
  `__file__` (not the cwd — it must report *this agent's* identity, not whatever
  `culture.yaml` sits in the caller's directory). Parsed by a hand-rolled
  scanner, not a YAML lib — see the zero-dependency rule below.
- `explain <path>` resolves a path-tuple key against the catalog in
  `spark/explain/catalog.py` (`ENTRIES`); unknown paths raise `CliError`.
- `doctor` mirrors the `steward doctor` invariants: `prompt-file-present`,
  `backend-consistency` (`claude`→`CLAUDE.md`, `acp`→`AGENTS.md`,
  `gemini`→`GEMINI.md`), plus `skills-present`. Exits 1 when unhealthy.
- `overview` / `cli overview` share render helpers in `overview.py`. Descriptive
  verbs must never hard-fail on a bad path — `overview` accepts and ignores a
  `target` positional so a stray arg still exits 0.

### The zero-runtime-dependency rule

`pyproject.toml` has `dependencies = []` and must stay that way. `teken` is a
**dev-only** dependency (the rubric gate). This is why `whoami` parses
`culture.yaml` by hand instead of importing PyYAML. Don't add a runtime dep to
solve something the stdlib can do.

## Adding a command (and the rubric constraints)

The agent-first rubric (`teken cli doctor . --strict`, CI-enforced) imposes
real shape requirements — satisfy all of them when extending the CLI:

1. Add a `spark/cli/_commands/<name>.py` with a `register(sub)`, and wire it
   into `_build_parser()` in `spark/cli/__init__.py`.
2. Add an `explain` catalog entry in `spark/explain/catalog.py` for every new
   noun/verb path — `tests/test_cli.py::test_every_catalog_path_resolves`
   walks `known_paths()` and fails if any path has no entry.
3. **Any noun that has action-verbs must also expose `overview`** (the
   `overview_cli_noun_exists` rubric check — this is why the `cli` noun exists
   even though it has no real verbs yet).
4. Keep `learn` ≥200 chars and mentioning purpose, command map, exit codes,
   `--json`, and `explain` (rubric-checked).
5. Support `--json` on the new verb; route failures through `CliError`.

## PR workflow — version-bump is mandatory

**Every PR must bump the version**, even docs/config/CI-only changes. The CI
`version-check` job (`.github/workflows/tests.yml`) blocks merge if
`pyproject.toml`'s version equals `main`'s. Use the `/version-bump` skill (or
`.claude/skills/version-bump/scripts/bump.py`) — it updates `pyproject.toml` and
prepends a Keep-a-Changelog entry to `CHANGELOG.md`. `spark/__init__.py` reads
its `__version__` from package metadata, so there's no second literal to sync.

Branch naming: `fix/`, `feat/`, `docs/`, `skill/`. PR creation and review-reply
go through the **`cicd` skill** (`workflow.sh open` / `read` / `reply` /
`status` / `await`), which wraps `agex pr`. The standing AgentCulture default
when a branch is ready is **push and open a PR** — don't pause for a
merge/keep/discard menu. The `cicd` and `communicate` skills require `agex`
(and `agtag` for `communicate`) on PATH.

## CI/CD

- `tests.yml` — `test` (pytest + coverage → SonarCloud gate, guarded by
  `SONAR_TOKEN`), `lint` (the five linters + rubric gate), `version-check`.
- `publish.yml` — TestPyPI on PR (dev version), PyPI on push to `main`, both via
  **Trusted Publishing** (OIDC, no tokens). Fork PRs skip publish.

## Vendored skills — cite, don't edit

`.claude/skills/` is vendored **cite-don't-import** from `guildmaster` (and
`ask-colleague` from its origin, `colleague`); provenance + re-sync procedure in
`docs/skill-sources.md`. Treat script bodies as read-only — they're excluded from
markdownlint and Sonar. To update a skill, **re-vendor from upstream rather than
hand-editing**. A needed change to the upstream body is *requested* there, not
made by us (see the repo-boundary rule below) — then re-vendored here byte-for-byte
once upstream ships it. Every `SKILL.md` must carry `type: command` in frontmatter
(load-bearing for the culture/claude `core.skill_loader`).

## Repo boundary — only write here; request upstream via `/communicate`

This agent **only writes to its own repository (`dgx-spark-cli`).** It never
edits, commits to, or opens PRs in sibling/upstream repos (`colleague`,
`guildmaster`, `steward`, …) — even when a fix obviously belongs there (e.g. a
qodo finding on the vendored `ask-colleague` wrapper, whose origin is
`colleague`).

When a change is needed upstream, **request it via the `/communicate` skill** — it
files a tracked GitHub issue on the sibling repo (auto-signed
`- dgx-spark-cli (Claude)`). The upstream owner/agent makes the change in their
repo; we then **re-vendor** the result here (cite-don't-import). The local half of
the loop (re-vendor + version bump + PR in *this* repo) is ours; the upstream half
is theirs. Do not branch, edit, or PR in another repo's checkout to "save a round
trip" — that crosses the boundary and collides with whoever owns it.
