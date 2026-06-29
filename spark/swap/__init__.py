"""Swap management and memory/swap telemetry for dgx-spark-cli.

Domain logic for the ``spark swap`` noun group: read-only swap/memory state
inspection, system-trend reading over the existing sysstat/sar history, a
bounded per-process memory/swap history store, and the (dry-run by default)
swap-grow planner/executor. Stdlib only — see the zero-runtime-dependency
rule in CLAUDE.md.
"""
