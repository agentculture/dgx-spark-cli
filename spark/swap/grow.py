"""Pure planner for the ``spark swap grow`` workflow.

This module builds an ordered, executable *plan* for resizing a file-backed
swap area (default ``/swap.img``) in place. It is a SAFETY-CRITICAL planner for
a destructive operation, so it is deliberately a **pure function**: it only
reads the caller-supplied ``state`` snapshot and returns a :class:`GrowPlan`
describing the argv steps an executor (task t5) will run. It never executes a
command, reads a file, or mutates the host itself.

The plan it emits performs an in-place resize, in this exact order::

    swapoff -> fallocate -l <bytes> -> chmod 600 -> mkswap -> swapon

On the default (permanent) path it then ensures an ``/etc/fstab`` entry so the
resized swap survives a reboot. ``--ephemeral`` (``ephemeral=True``) skips fstab
handling and warns that the change lasts only for the current boot.

It refuses (raising :class:`CliError`) rather than emitting an unsafe plan when:

* there is no file-backed swap to grow (zram-only / partition-only host) -> a
  user error (exit 1); or
* the backing filesystem lacks the free space the resize requires -> an
  environment error (exit 2).

It also surfaces the *swapoff-under-pressure* hazard as a prominent warning:
``swapoff`` must page all in-use swap back into RAM, and if that does not fit it
fails with ENOMEM and can destabilize the host. So when used swap exceeds
available RAM the plan recommends adding a second swapfile (which avoids
``swapoff``) instead of resizing in place. This is a warning, not a hard block.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from spark.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError

_FSTAB_PATH = "/etc/fstab"


@dataclass
class GrowPlan:
    """An ordered, dry-run plan describing how to grow a swapfile.

    ``steps`` is the ordered list of ``{"desc": str, "argv": list[str]}`` an
    executor runs. A step with an empty ``argv`` is a note (nothing to run).
    """

    steps: list[dict]
    persistent: bool
    swapfile: str
    target_size_bytes: int
    current_size_bytes: int
    warnings: list[str] = field(default_factory=list)


def _human(num_bytes: int) -> str:
    """Render a byte count for human-readable step/warning text."""
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024.0 or unit == "PiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PiB"  # pragma: no cover  # unreachable: loop returns first


def _current_size_bytes(state: dict, swapfile: str) -> int:
    """Size of the existing file-backed device we are growing, else 0 (fresh file).

    Matches the file device whose name is the swapfile we operate on (normally
    equal to ``state["backing"]["swapfile"]``).
    """
    for dev in state.get("devices") or []:
        if dev.get("type") == "file" and dev.get("name") == swapfile:
            return int(dev.get("size_bytes") or 0)
    return 0


def _fstab_line(swapfile: str) -> str:
    return f"{swapfile} none swap sw 0 0"


def _validate_growable(state: dict, swapfile: str, target_size_bytes: int):
    """Refuse unsafe grows; return ``(current_size_bytes, free_bytes)``.

    Raises :class:`CliError` for a non-swapfile host (exit 1) or an insufficient
    backing filesystem (exit 2). Pure; no system access.
    """
    backing = state.get("backing") or {}
    devices = state.get("devices") or []
    has_file_device = any(dev.get("type") == "file" for dev in devices)

    # Refusal (exit 1): nothing file-backed to grow (zram-only / partition-only host).
    if backing.get("swapfile") is None and not has_file_device:
        raise CliError(
            EXIT_USER_ERROR,
            "No file-backed swap to grow: this host has only zram/partition swap.",
            remediation=(
                "In-place grow only applies to a swapfile. Create one first "
                "(allocate a file, mkswap, swapon, add an fstab entry) before growing."
            ),
        )

    current = _current_size_bytes(state, swapfile)
    free_bytes = backing.get("free_bytes")
    needed = target_size_bytes - current

    # Refusal (exit 2): the resize would not fit on the backing filesystem.
    if free_bytes is not None and needed > free_bytes:
        raise CliError(
            EXIT_ENV_ERROR,
            (
                f"Not enough free space on the swap backing store: the resize needs "
                f"{_human(needed)} more, but only {_human(free_bytes)} is free."
            ),
            remediation=(
                "Free up space on the swapfile's filesystem, choose a smaller target "
                "size, or place the swapfile on a larger volume."
            ),
        )
    return current, free_bytes


def _grow_warnings(mem: dict, free_bytes, ephemeral: bool) -> list[str]:
    """Build the (non-blocking) hazard warnings for a grow plan."""
    warnings: list[str] = []

    # Hazard: swapoff must page all in-use swap back into RAM; if it does not fit,
    # swapoff fails with ENOMEM. Warn (do not block) and recommend a 2nd swapfile.
    swap_used = int(mem.get("swap_used_bytes") or 0)
    available = int(mem.get("available_bytes") or 0)
    if swap_used > available:
        warnings.append(
            "WARNING: swapoff must move "
            f"{_human(swap_used)} of in-use swap back into RAM, but only "
            f"{_human(available)} is available — swapoff may fail with ENOMEM and "
            "destabilize the host. Prefer adding a SECOND swapfile (which avoids "
            "swapoff) over this in-place resize."
        )

    if free_bytes is None:
        warnings.append(
            "WARNING: free disk space on the swap backing store is unknown; "
            "could not verify the resize will fit."
        )

    if ephemeral:
        warnings.append(
            "Ephemeral resize: this applies to the current boot only and will not "
            "persist across a reboot (no fstab entry is written)."
        )
    return warnings


def _resize_steps(swapfile: str, target_size_bytes: int) -> list[dict]:
    """The ordered in-place resize steps (swapoff -> fallocate -> chmod -> mkswap -> swapon)."""
    return [
        {"desc": f"Disable swap on {swapfile} (swapoff)", "argv": ["swapoff", swapfile]},
        {
            "desc": f"Resize {swapfile} to {_human(target_size_bytes)} (fallocate)",
            "argv": ["fallocate", "-l", str(target_size_bytes), swapfile],
        },
        {
            "desc": f"Restrict {swapfile} to root-only (chmod 600)",
            "argv": ["chmod", "600", swapfile],
        },
        {"desc": f"Format {swapfile} as swap (mkswap)", "argv": ["mkswap", swapfile]},
        {"desc": f"Re-enable swap on {swapfile} (swapon)", "argv": ["swapon", swapfile]},
    ]


def _fstab_step(state: dict, swapfile: str) -> dict:
    """The persistence step: a note if fstab already has the entry, else an idempotent ensure."""
    if state.get("fstab_present") is True:
        # State tells us the entry already exists: a note, not an edit step.
        return {
            "desc": f"fstab entry for {swapfile} already present; no change needed",
            "argv": [],
        }
    # Cannot tell from state -> emit an idempotent ensure step.
    quoted = shlex.quote(_fstab_line(swapfile))
    return {
        "desc": f"Ensure persistent fstab entry for {swapfile}",
        "argv": [
            "sh",
            "-c",
            f"grep -qxF {quoted} {_FSTAB_PATH} || printf '%s\\n' {quoted} >> {_FSTAB_PATH}",
        ],
    }


def build_grow_plan(
    target_size_bytes: int,
    *,
    state: dict,
    ephemeral: bool = False,
    swapfile: str = "/swap.img",
) -> GrowPlan:
    """Build a :class:`GrowPlan` for an in-place swapfile resize.

    ``target_size_bytes`` is already in bytes (human-size parsing belongs to the
    CLI layer). ``state`` is the documented swap/memory snapshot. Raises
    :class:`CliError` (never returns a plan) when the operation is unsafe; see
    the module docstring for the refusal conditions.
    """
    if target_size_bytes <= 0:
        raise CliError(
            EXIT_USER_ERROR,
            f"Invalid target swap size: {target_size_bytes} bytes (must be positive).",
            remediation="Pass a positive target size (the CLI parses e.g. 32G into bytes).",
        )

    current, free_bytes = _validate_growable(state, swapfile, target_size_bytes)
    warnings = _grow_warnings(state.get("mem") or {}, free_bytes, ephemeral)

    steps = _resize_steps(swapfile, target_size_bytes)
    persistent = not ephemeral
    if persistent:
        steps.append(_fstab_step(state, swapfile))

    return GrowPlan(
        steps=steps,
        persistent=persistent,
        swapfile=swapfile,
        target_size_bytes=target_size_bytes,
        current_size_bytes=current,
        warnings=warnings,
    )
