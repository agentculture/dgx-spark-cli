# dgx-spark-cli ships a 'swap' command group: it shows swap pressure at a glance, safely grows swap (permanent by default, with a warning before applying and an --ephemeral escape hatch), and adds per-process memory/swap telemetry history so you can analyze WHAT is eating memory over time — not just that swap is full.

> dgx-spark-cli ships a 'swap' command group: it shows swap pressure at a glance, safely grows swap (permanent by default, with a warning before applying and an --ephemeral escape hatch), and adds per-process memory/swap telemetry history so you can analyze WHAT is eating memory over time — not just that swap is full.

## Audience

- A DGX Spark (Grace-Blackwell) operator running local AI/ML workloads who hits sustained swap pressure and needs to both relieve it and diagnose the cause from the spark CLI.

## Before → After

- Before: Swap sits at ~99.99% for days (sar confirms avg 99.65%), RAM ~89% used with %commit ~129% (overcommitted). The operator has no spark-native way to see swap pressure, grow swap safely, or find which process is the memory hog over time.
- After: From spark, the operator inspects swap/memory state, sees per-process memory history to identify the hog, and grows swap with one guarded command that is permanent by default and warns before acting.

## Why it matters

- On a unified-memory Grace-Blackwell box, sustained swap exhaustion risks OOM kills of ML workloads; relieving pressure AND attributing it to a process is what prevents recurrence, not just a one-time bump.

## Requirements

- Swap increase is PERMANENT by default (survives reboot) with an --ephemeral (this-boot-only) flag.
  - honesty: Reboot survival is verifiable (fstab/state assertion) and --ephemeral is genuinely non-persistent.
- A warning is shown and explicit confirmation required before any swap mutation.
  - honesty: No code path mutates swap without the warning+confirmation gate (an explicit non-interactive override, if any, is the only exception).
- Per-process and system memory/swap telemetry is recorded over time and queryable for analysis.
  - honesty: A sampler writes per-process samples on a schedule with BOUNDED retention/rotation so history can never fill the disk.
- All read/inspect verbs work without root and never emit a Python traceback (route every failure through CliError).
  - honesty: A test drives each verb's failure modes and asserts the CliError error:/hint: shape with no Python traceback.

## Honesty conditions

- All three capabilities ship as working 'spark swap' verbs (status, guarded grow, per-process history), each with --json and tests.
- Every read/inspect verb produces useful output without root on this box's setup (swapfile /swap.img, sysstat present).
- The pressure is real and CLI-observable today: sar/proc show sustained ~100% swap util, reproducible before the feature lands.
- End-to-end demo: grow relieves swap pressure AND a history verb names the top consuming process — both shown working together.
- The deliverable includes the diagnostic (history), not just the bump — because without attribution swap refills after a grow.
- On a non-swapfile setup (zram/partition-only) grow refuses with a clear CliError instead of mutating something it doesn't understand.
- 'swap status' returns quickly, read-only, exit 0 with a missing subsystem — covered by a test injecting an absent probe.
- A test asserts: default grow yields a reboot-surviving config, --ephemeral yields none, and no mutation happens without confirmation.
- history returns top-N consumers over a window from recorded samples; an empty store degrades to a clear message, not a crash.

## Success signals

- 'spark swap status [--json]' reports current swap+memory pressure and recent trend, runs read-only without root, returns exit 0 even when a subsystem is missing.
- 'spark swap grow <size>' prints the exact plan + a warning and mutates only after explicit confirmation; default config survives reboot (fstab entry present), --ephemeral makes no fstab change — asserted by a test on fstab state.
- Per-process memory/swap history is queryable (e.g. 'spark swap history' / a processes-history verb) and surfaces the top consumers over a time window.

## Scope / boundaries

- v1 swap mutation targets the existing swapfile model (/swap.img on ext4, already in fstab); zram and dedicated swap partitions are out of scope for v1.

## Non-goals

- Not a memory autopilot: spark never auto-kills processes or grows swap unattended; mutation is always operator-initiated and confirmed.
- Does not replace sar/sysstat or the monitor watchdog, and ships no GUI/dashboard — it surfaces and queries telemetry from the CLI.

## Assumptions

- Default grow strategy is resizing the existing /swap.img in place (NVMe root has 2.7T free); fstab already references it so resize alone persists.
- System-stat history is read from the already-running sysstat/sar (7-day retention, 10-min cadence); spark adds the missing per-process history layer rather than re-collecting system stats.

## Decisions

- Privilege model: read/inspect verbs need no root; mutation uses plan+run-as-root. Without root, print the plan+warning and exit with a 'sudo spark swap grow ... --apply' hint; with euid 0, perform it. No self-invoked sudo.
- Confirmation model: 'swap grow' is DRY-RUN by default (prints warning + exact command plan, mutates nothing, exit 0); a deliberate second run with --apply performs it. --apply is also the unattended/automation path — no interactive prompt, no separate --yes flag.
- Grow default strategy: resize existing /swap.img in place (swapoff -> fallocate -> mkswap -> swapon); fstab already references it so it persists. --ephemeral performs the live grow but makes no fstab change.
- History model: hybrid. Read existing sysstat/sar for system trend (CPU/mem/swap/IO, 9d already on disk); add a NEW bounded per-process sampler writing JSONL under ~/.local/state/dgx-spark/ with rotation/retention.
