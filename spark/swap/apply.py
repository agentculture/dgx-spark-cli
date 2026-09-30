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
``mkswap`` on the still-live swapfile. If a step fails *after* ``swapoff``
succeeded (fallocate / chmod / mkswap error or timeout), the executor first
attempts a best-effort ``swapon <swapfile>`` so the host is not left without
swap, and reports the recovery outcome in the error. Steps with an empty ``argv`` are
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


def _raise_step_failure(plan, message, remediation, *, swap_off, runner, diagnostic):
    """Raise the step-failure :class:`CliError`, recovering swap first if needed.

    When ``swap_off`` is true the swapfile was already disabled by this run, so
    try ``swapon <swapfile>`` (best effort: after a failed mkswap the file may
    no longer carry a swap signature) and fold the outcome into the error.
    """
    if not swap_off:
        raise CliError(EXIT_ENV_ERROR, message, remediation=remediation)

    swapfile = plan.swapfile
    if diagnostic is not None:
        diagnostic(f"recovery: re-enabling swap after the failed step: swapon {swapfile}")
    try:
        recovered = runner("swapon", [swapfile])
    except Exception:  # pragma: no cover - defensive; runners return None on error
        recovered = None
    if recovered is not None and recovered[0] == 0:
        if diagnostic is not None:
            diagnostic(f"recovery: swapon {swapfile} succeeded")
        raise CliError(
            EXIT_ENV_ERROR,
            f"{message} (recovery: swap on {swapfile} was re-enabled at its previous size)",
            remediation=remediation,
        )
    detail = "not found or could not be launched" if recovered is None else recovered[1].strip()
    if diagnostic is not None:
        diagnostic(f"recovery: swapon {swapfile} failed: {detail}")
    raise CliError(
        EXIT_ENV_ERROR,
        f"{message} (recovery: swapon {swapfile} failed: {detail}; swap on it is OFF)",
        remediation=(
            f"Swap on {swapfile} is disabled. Re-enable it with `sudo swapon {swapfile}` "
            f"(if that fails, recreate it: `sudo mkswap {swapfile} && sudo swapon {swapfile}`), "
            "then re-run `spark swap grow <size> --apply`."
        ),
    )


def apply_grow_plan(
    plan: GrowPlan,
    *,
    apply: bool = False,
    runner=run_capture,
    geteuid=os.geteuid,
    diagnostic=None,
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
    diagnostic:
        Optional ``(str) -> None`` sink for progress diagnostics (the CLI passes
        its stderr emitter). Used to log the best-effort recovery ``swapon``.

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
        (the plan aborts before any later step runs). If the failure happens
        after ``swapoff`` succeeded (and before the plan's own ``swapon``), a
        best-effort ``swapon <swapfile>`` recovery is attempted first and its
        outcome is included in the error.
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
    # ``swap_off`` tracks the window between a successful ``swapoff`` and the
    # plan's own ``swapon``: a failure inside it leaves the host with swap
    # disabled AND the file gone from /proc/swaps (so a plain re-run would be
    # refused by the planner), hence the best-effort recovery below.
    executed: list[dict] = []
    swap_off = False
    for step in plan.steps:
        desc = step.get("desc", "")
        argv = list(step.get("argv") or [])

        if not argv:
            # Informational note — there is nothing to run.
            executed.append({"desc": desc, "argv": [], "returncode": None, "ran": False})
            continue

        result = runner(argv[0], argv[1:])
        if result is None:
            # run_capture returns None when the tool is absent / cannot launch
            # (or timed out).
            _raise_step_failure(
                plan,
                f"swap grow step failed: {desc}: command '{argv[0]}' not found, "
                "could not be launched, or timed out",
                "Install the swap tooling (util-linux provides "
                "swapoff/swapon/mkswap/fallocate) and re-run with --apply.",
                swap_off=swap_off,
                runner=runner,
                diagnostic=diagnostic,
            )

        returncode, output = result
        if returncode != 0:
            _raise_step_failure(
                plan,
                f"swap grow step failed: {desc}: {output.strip()}",
                "The grow aborted before later steps to avoid acting on a "
                "live swapfile. Fix the cause shown above, then re-run "
                "`spark swap grow <size> --apply`.",
                swap_off=swap_off,
                runner=runner,
                diagnostic=diagnostic,
            )

        if argv[0] == "swapoff":
            swap_off = True
        elif argv[0] == "swapon":
            swap_off = False
        executed.append({"desc": desc, "argv": argv, "returncode": returncode, "ran": True})

    return {
        "applied": True,
        "dry_run": False,
        "executed": executed,
        "warnings": list(plan.warnings),
    }
