"""Read-only host-telemetry probes for the DGX Spark.

Each domain module (:mod:`memory`, :mod:`disk`, :mod:`gpu`, :mod:`thermal`,
:mod:`network`, :mod:`containers`, :mod:`processes`) exposes a ``collect()``
that returns a JSON-friendly *report* dict (see :mod:`spark.probe._report`).
:mod:`status` aggregates them into an anomalies-first headline.

Design rules that keep this package honest:

* **Zero runtime dependencies.** Kernel telemetry is read straight from
  ``/proc`` and ``/sys`` with the stdlib; domain tools (``nvidia-smi``,
  ``docker``, ``ip``) are shelled out via :mod:`spark.probe._run`.
* **Graceful, never fatal.** A missing tool or unreadable node yields a report
  with ``available: false`` and a remediation hint — collectors never raise, so
  descriptive verbs always exit 0 (``doctor`` remains the health gate).
* **Testable off-Spark.** Every collector takes an injectable file root and/or
  command runner so the suite passes on x86 CI with no GPU, docker, or aarch64.
"""

from __future__ import annotations
