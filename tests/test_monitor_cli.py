"""CLI integration tests for the `monitor` noun.

XDG dirs are redirected to tmp paths so tests never touch the real config/state,
and systemd-mutating verbs stub `run_tool` so no real units/linger are created.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spark.cli import main


@pytest.fixture(autouse=True)
def _isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("DGX_SPARK_WEBHOOK_URL", raising=False)


def test_monitor_bare_is_overview(capsys) -> None:
    rc = main(["monitor"])
    assert rc == 0
    assert "# dgx-spark-cli monitor" in capsys.readouterr().out


def test_overview_json(capsys) -> None:
    rc = main(["monitor", "overview", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "dgx-spark-cli monitor"
    assert isinstance(payload["sections"], list)


def test_check_json(capsys) -> None:
    rc = main(["monitor", "check", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "monitor check"
    assert "alerts" in payload and "thresholds" in payload


def test_config_invalid_without_webhook(capsys) -> None:
    rc = main(["monitor", "config", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert any("webhook_url" in e for e in payload["errors"])


def test_config_init_writes_file(tmp_path, capsys) -> None:
    rc = main(["monitor", "config", "--init", "--json"])
    assert rc == 0
    written = json.loads(capsys.readouterr().out)["path"]
    assert written.endswith("dgx-spark/monitor.json")
    assert json.loads(Path(written).read_text())["webhook_url"]


def test_once_no_webhook_exits_zero(capsys) -> None:
    rc = main(["monitor", "once", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "monitor once"
    assert payload["delivered"] is False


def test_test_without_webhook_errors(capsys) -> None:
    rc = main(["monitor", "test"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "hint:" in err


def test_run_requires_webhook(capsys) -> None:
    rc = main(["monitor", "run"])
    assert rc == 2
    assert "not configured" in capsys.readouterr().err


def test_run_happy_path_calls_loop(monkeypatch) -> None:
    monkeypatch.setenv("DGX_SPARK_WEBHOOK_URL", "https://example.com/hook")
    # cmd_run now fires a startup alert before the loop — keep it off the network.
    monkeypatch.setattr("spark.monitor.notify.post", lambda url, payload, **kw: (True, None))
    called = {}
    monkeypatch.setattr(
        "spark.cli._commands.monitor.engine.run_loop",
        lambda cfg, **kw: called.setdefault("ran", True),
    )
    rc = main(["monitor", "run"])
    assert rc == 0 and called.get("ran")


def test_test_with_webhook_posts(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DGX_SPARK_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr("spark.monitor.notify.post", lambda url, payload, **kw: (True, None))
    rc = main(["monitor", "test", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_status_json_shape(capsys) -> None:
    rc = main(["monitor", "status", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"]["unit"] == "dgx-spark-monitor.service"
    assert "firing" in payload


def test_install_writes_unit(monkeypatch, capsys) -> None:
    monkeypatch.setattr("spark.monitor.systemd.run_tool", lambda _n, _a: "ok")
    rc = main(["monitor", "install", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["unit_path"].endswith("dgx-spark-monitor.service")


def test_enable_disable_uninstall_stubbed(monkeypatch, capsys) -> None:
    monkeypatch.setattr("spark.monitor.systemd.run_tool", lambda _n, _a: "ok")
    monkeypatch.setattr("spark.monitor.systemd.run_capture", lambda _n, _a: (0, "active"))
    assert main(["monitor", "enable", "--no-linger", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["monitor", "disable", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["monitor", "uninstall", "--json"]) == 0


def test_run_accepts_json_flag(monkeypatch) -> None:
    monkeypatch.setenv("DGX_SPARK_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr("spark.monitor.notify.post", lambda url, payload, **kw: (True, None))
    monkeypatch.setattr("spark.cli._commands.monitor.engine.run_loop", lambda cfg, **kw: 0)
    assert main(["monitor", "run", "--json"]) == 0


def test_run_posts_startup_alert(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DGX_SPARK_WEBHOOK_URL", "https://example.com/hook")
    posts = []

    def fake_post(url, payload, **kw):
        posts.append(payload)
        return True, None

    monkeypatch.setattr("spark.monitor.notify.post", fake_post)
    monkeypatch.setattr("spark.cli._commands.monitor.engine.run_loop", lambda cfg, **kw: 0)
    rc = main(["monitor", "run"])
    assert rc == 0
    event = posts[0]["events"][0]
    assert event["status"] == "started" and event["alert"]["key"] == "monitor_started"
    assert "startup alert -> sent" in capsys.readouterr().err


def test_run_notify_on_start_false_suppresses_alert(monkeypatch, tmp_path) -> None:
    cfg_path = tmp_path / "m.json"
    cfg_path.write_text(
        json.dumps({"webhook_url": "https://example.com/hook", "notify_on_start": False})
    )
    posts = []

    def fake_post(url, payload, **kw):
        posts.append(payload)
        return True, None

    monkeypatch.setattr("spark.monitor.notify.post", fake_post)
    monkeypatch.setattr("spark.cli._commands.monitor.engine.run_loop", lambda cfg, **kw: 0)
    rc = main(["monitor", "run", "--config", str(cfg_path)])
    assert rc == 0
    assert posts == []  # startup alert suppressed by notify_on_start=false


def test_enable_failure_routes_to_stderr(monkeypatch, capsys) -> None:
    # systemctl "fails" -> failure must go to stderr (CliError), not stdout.
    monkeypatch.setattr("spark.monitor.systemd.run_tool", lambda _n, _a: None)
    rc = main(["monitor", "enable", "--no-linger"])
    assert rc == 2
    out, err = capsys.readouterr()
    assert out == ""  # results stream stays clean
    assert err.startswith("error:") and "hint:" in err


def test_descriptive_verbs_tolerate_stray_positional(capsys) -> None:
    for verb in ("overview", "check", "status"):
        assert main(["monitor", verb, "stray-arg"]) == 0
        assert capsys.readouterr().out.startswith("#")


def test_monitor_unknown_flag_structured_error(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["monitor", "check", "--bogus"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("error:") and "hint:" in err
