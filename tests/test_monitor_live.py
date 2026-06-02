"""Live webhook delivery test — opt-in, gated on a real endpoint.

This is the end-to-end proof that the monitor's "started watching" alert
actually delivers. It POSTs a real payload to the URL in
``DGX_SPARK_TEST_WEBHOOK_URL`` and asserts a 2xx. When that variable is unset
(contributors, fork PRs without the secret) the whole module is **skipped**, so
the offline suite stays hermetic.

Wire it in CI by injecting a GitHub secret of the same name into the test job
(see ``.github/workflows/tests.yml``). The payload format auto-detects from the
URL (Discord / Slack / generic); override with ``DGX_SPARK_TEST_WEBHOOK_FORMAT``.
"""

from __future__ import annotations

import os

import pytest

from spark.monitor import engine
from spark.monitor.config import Config

_URL = os.environ.get("DGX_SPARK_TEST_WEBHOOK_URL")

pytestmark = pytest.mark.skipif(
    not _URL, reason="DGX_SPARK_TEST_WEBHOOK_URL not set — live webhook test skipped"
)


def _format_for(url: str) -> str:
    override = os.environ.get("DGX_SPARK_TEST_WEBHOOK_FORMAT")
    if override:
        return override
    if "discord.com" in url:
        return "discord"
    if "hooks.slack.com" in url:
        return "slack"
    return "generic"


def test_startup_alert_delivers_to_real_webhook() -> None:
    cfg = Config(webhook_url=_URL, webhook_format=_format_for(_URL))
    ok, error = engine.notify_started(cfg, host="dgx-spark-cli-ci")
    assert ok, f"webhook POST failed: {error}"
