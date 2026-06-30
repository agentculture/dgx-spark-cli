# Build Plan — dgx-spark-cli ships a 'swap' command group: it shows swap pressure at a glance, safely grows swap (permanent by default, with a warning before applying and an --ephemeral escape hatch), and adds per-process memory/swap telemetry history so you can analyze WHAT is eating memory over time — not just that swap is full.

slug: `dgx-spark-cli-ships-a-swap-command-group-it-shows` · status: `exported` · from frame: `dgx-spark-cli-ships-a-swap-command-group-it-shows`

> dgx-spark-cli ships a 'swap' command group: it shows swap pressure at a glance, safely grows swap (permanent by default, with a warning before applying and an --ephemeral escape hatch), and adds per-process memory/swap telemetry history so you can analyze WHAT is eating memory over time — not just that swap is full.

## Tasks

### t1 — Swap state inspection module (spark/swap/state.py)

- covers: c9, h2, h7
- acceptance:
  - Parses /proc/swaps + /proc/meminfo + swappiness into a structured swap-state dict (devices, sizes, used_pct, backing fs) with no root.
  - A missing probe source sets available:false and never raises — a test injects an absent /proc node and asserts clean structured output.

### t2 — System-trend reader over existing sysstat/sar (spark/swap/sar.py)

- covers: h3, c14
- acceptance:
  - Reads recent swap/mem trend from sar (prefers sadf -j JSON) into a normalized series; absent sysstat returns available:false without raising.
  - A captured-sar fixture test asserts the parsed series; a second test asserts graceful degradation when the tool is missing.

### t3 — Per-process memory/swap history store + sampler (spark/swap/history.py)

- covers: c11, h9, c14, h12, h5
- acceptance:
  - record() appends one per-process sample (pid, comm, VmRSS, VmSwap from /proc/[pid]/status) as JSONL under XDG state ~/.local/state/dgx-spark/.
  - Bounded retention/rotation is enforced — a test writes past the cap and asserts the store is pruned and never exceeds the configured size/age.
  - query(window, top_n) returns top consumers over a window; an empty store returns a clear empty result, not an exception.

### t4 — Swap grow planner — pure plan builder (spark/swap/grow.py)

- covers: c8, h6, c12, h10
- acceptance:
  - Given size + current state, builds the exact ordered plan for in-place /swap.img resize (swapoff->fallocate->mkswap->swapon); permanent path asserts the fstab entry, --ephemeral omits fstab changes.
  - On a non-swapfile setup (zram-only / swap partition) it raises CliError with remediation instead of a plan — test asserts the refusal.
  - Refuses when free disk is insufficient for the new size; planner is pure (no mutation) and unit-tested without root.

### t5 — Privileged grow executor — plan+run-as-root (spark/swap/apply.py)

- depends on: t4
- covers: c10, c13, h8, h11
- acceptance:
  - Executes a grow Plan only with --apply AND euid==0; without root it prints the plan+warning and exits env-error with a 'sudo ... --apply' hint, mutating nothing.
  - Without --apply nothing mutates (dry-run) regardless of euid — a mock-runner test asserts zero mutating calls.
  - With --apply+root the steps run via the _run helper; fstab persistence applied by default / skipped with --ephemeral — asserted against a mock runner + temp fstab.

### t6 — swap noun CLI module + wiring + catalog + learn + overview (spark/cli/_commands/swap.py)

- depends on: t1, t2, t3, t4, t5
- covers: c1, c15, h1, h13
- acceptance:
  - Registers a 'swap' noun (verbs: status, grow, history, sample, overview); every verb supports --json and routes all failures through CliError with no traceback — a failure-mode test asserts the error:/hint: shape.
  - Wired into _build_parser; explain catalog has an entry for every new swap path (test_every_catalog_path_resolves passes); 'swap overview' exists (rubric overview_cli_noun_exists); 'learn' mentions swap; full suite + 'teken cli doctor . --strict' green.

### t7 — End-to-end demo test: pressure -> grow plan -> history attributes the hog (tests/test_swap_e2e.py)

- depends on: t6
- covers: c2, c3, c4, c5, h4
- acceptance:
  - One test shows status surfacing sustained swap pressure from real /proc, grow producing a relief plan, and history naming the top consuming process — the full operator story.
  - Runs without root and with a subsystem absent, asserting useful output + exit 0 on the read paths.

### t8 — Version bump, CHANGELOG, README + operator sampler-timer doc

- depends on: t6
- acceptance:
  - pyproject.toml version bumped + Keep-a-Changelog entry added (version-check CI passes).
  - README documents the swap verbs and how to install the sampler timer so per-process history collects 'on a schedule'.

## Risks

- [unknown_nonblocking] Resize-in-place needs swapoff, but swap is ~100% full AND RAM ~89% used — swapoff may fail (ENOMEM) with nowhere to move swapped pages. Planner/executor must detect this and recommend the add-second-swapfile fallback. (task t4)
- [unknown_nonblocking] sar/sadf output format and locale vary across sysstat versions; sadf -j JSON may be unavailable — reader must degrade gracefully. (task t2)
- [follow_up] Privileged grow path can only be mock-tested in CI; real swapoff/fallocate/mkswap/swapon + fstab edit need root and are verified manually on the box. (task t5)
