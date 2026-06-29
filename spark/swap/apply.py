"""Privileged executor for a swapfile :class:`GrowPlan` — the run-as-root half.

This is the SAFETY-CRITICAL counterpart to the pure planner in
:mod:`spark.swap.grow`. The planner builds an ordered list of destructive,
privileged argv steps (``swapoff -> fallocate -> chmod -> mkswap -> swapon``,
plus an optional ``/etc/fstab`` ensure on the permanent path); this module
*executes* those steps — but only behind two hard guards:

* ``apply=False`` (the default) is a **dry run**: it runs nothing and returns a
  structured preview of the plan.
* ``apply=True`` requires **root** (``geteuid() == 0``); otherwise it runs
  nothing and raises :class:`CliError` (exit 2) with a ``sudo`` hint.

When it does run, it executes the steps strictly in order and **aborts on the
first failure**. A mid-resize abort is the whole point: if ``swapoff`` fails
(e.g. ENOMEM under memory pressure) we must NOT go on to ``fallocate`` /
``mkswap`` on the still-live swapfile. Steps with an empty ``argv`` are
informational notes — recorded as not-run, never executed.

Like every domain module here it never prints and never calls ``sys.exit`` — it
raises :class:`CliError` or returns structured data, and the CLI layer renders.
"""

from __future__ import annotations

import os

from spark.cli._errors import EXIT_ENV_ERROR, CliError
from spark.probe._run import run_capture
from spark.swap.grow import GrowPlan


def _preview_steps(plan: GrowPlan) -> list[dict]:
    """Copy the plan's steps into the public ``{desc, argv}`` preview shape."""
    return [
        {"desc": step.get("desc", ""), "argv": list(step.get("argv") or [])} for step in plan.steps
    ]


def apply_grow_plan(
    plan: GrowPlan,
    *,
    apply: bool = False,
    runner=run_capture,
    geteuid=os.geteuid,
) -> dict:
    """Execute (or preview) a swapfile :class:`GrowPlan`.

    Parameters
    ----------
    plan:
        The :class:`GrowPlan` to run, as produced by
        :func:`spark.swap.grow.build_grow_plan`.
    apply:
        ``False`` (default) previews only and runs nothing. ``True`` executes
        the plan — which requires root.
    runner:
        A ``(name, args) -> (returncode, output) | None`` callable. Defaults to
        :func:`spark.probe._run.run_capture`; inject a fake in tests. ``None``
        means the tool was absent / could not launch and is treated as failure.
    geteuid:
        A ``() -> int`` callable returning the effective uid. Defaults to
        :func:`os.geteuid`; inject a fake in tests to simulate root / non-root.

    Returns
    -------
    dict
        Dry run::

            {"applied": False, "dry_run": True, "swapfile": ...,
             "target_size_bytes": ..., "persistent": ...,
             "steps": [{"desc", "argv"}, ...], "warnings": [...]}

        Applied (root)::

            {"applied": True, "dry_run": False,
             "executed": [{"desc", "argv", "returncode", "ran"}, ...],
             "warnings": [...]}

        In ``executed`` a note step (empty ``argv``) is recorded with
        ``ran=False`` and ``returncode=None``.

    Raises
    ------
    CliError
        With code 2 when ``apply=True`` but not root, or when a step fails
        (the plan aborts before any later step runs).
    """
    # Guard 1: dry run is the default — execute nothing, just preview the plan.
    if not apply:
        return {
            "applied": False,
            "dry_run": True,
            "swapfile": plan.swapfile,
            "target_size_bytes": plan.target_size_bytes,
            "persistent": plan.persistent,
            "steps": _preview_steps(plan),
            "warnings": list(plan.warnings),
        }

    # Guard 2: applying mutates privileged, destructive state — require root.
    if geteuid() != 0:
        raise CliError(
            EXIT_ENV_ERROR,
            "growing swap requires root",
            remediation=(
                "re-run as root, e.g.: sudo spark swap grow <size> --apply "
                f"(grows {plan.swapfile} to {plan.target_size_bytes} bytes)"
            ),
        )

    # Execute each step in order; abort immediately on the first failure.
    executed: list[dict] = []
    for step in plan.steps:
        desc = step.get("desc", "")
        argv = list(step.get("argv") or [])

        if not argv:
            # Informational note — there is nothing to run.
            executed.append({"desc": desc, "argv": [], "returncode": None, "ran": False})
            continue

        result = runner(argv[0], argv[1:])
        if result is None:
            # run_capture returns None when the tool is absent / cannot launch.
            raise CliError(
                EXIT_ENV_ERROR,
                f"swap grow step failed: {desc}: command '{argv[0]}' not found "
                "or could not be launched",
                remediation=(
                    "Install the swap tooling (util-linux provides "
                    "swapoff/swapon/mkswap/fallocate) and re-run with --apply."
                ),
            )

        returncode, output = result
        if returncode != 0:
            raise CliError(
                EXIT_ENV_ERROR,
                f"swap grow step failed: {desc}: {output.strip()}",
                remediation=(
                    "The grow aborted before later steps to avoid acting on a "
                    "live swapfile. Fix the cause shown above, then re-run "
                    "`spark swap grow <size> --apply`."
                ),
            )

        executed.append({"desc": desc, "argv": argv, "returncode": returncode, "ran": True})

    return {
        "applied": True,
        "dry_run": False,
        "executed": executed,
        "warnings": list(plan.warnings),
    }
