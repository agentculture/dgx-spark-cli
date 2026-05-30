"""Deterministic, AI-free watchdog for the DGX Spark.

``spark monitor`` periodically runs the :mod:`spark.probe` collectors, compares
their numbers against configured thresholds, and POSTs to a generic webhook when
a catastrophe condition crosses (and again when it clears). No model, no
inference — just threshold comparison, designed to run always-on as a
systemd ``--user`` service the CLI installs and manages.

Modules:

* :mod:`spark.monitor.config` — thresholds, webhook target, intervals (JSON +
  env), all stdlib.
* :mod:`spark.monitor.rules` — ``evaluate(snapshot, thresholds) -> [Alert]``,
  pure and fully testable.
* :mod:`spark.monitor.state` — edge-triggering (fire on transition, resolve on
  recovery, re-notify slowly) so a standing condition doesn't spam.
* :mod:`spark.monitor.notify` — ``urllib`` webhook POST that never raises into
  the loop; generic JSON or Slack/Discord chat presets.
* :mod:`spark.monitor.engine` — snapshot -> evaluate -> diff -> notify -> persist.
* :mod:`spark.monitor.systemd` — generate and manage the user-level unit.
"""

from __future__ import annotations
