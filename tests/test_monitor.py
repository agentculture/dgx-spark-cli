"""Unit tests for the AI-free monitor (config, rules, state, notify, engine, systemd).

Pure/deterministic: thresholds and edge-triggering are tested directly, webhook
delivery uses an injected opener (no network), and systemd calls are stubbed.
"""

from __future__ import annotations

import json
import urllib.error

from spark.monitor import config as mconfig
from spark.monitor import engine, notify, state, systemd
from spark.monitor.config import Config
from spark.monitor.rules import Alert, evaluate

# A snapshot that crosses every default threshold.
_HOT_SNAPSHOT = {
    "host": "h",
    "available": {"memory": True, "disk": True, "thermal": True, "gpu": True, "containers": True},
    "memory": {"used_pct": 95.0, "swap_used_pct": 88.0},
    "disk": {"filesystems": [{"mount": "/", "used_pct": 96.0}]},
    "thermal": {"hottest_c": 95.0},
    "gpu": {"gpu": {"temperature.gpu": "90"}},
    "containers": {"containers": [{"name": "c1", "status": "Up (unhealthy)"}]},
    "load": {"per_core": 5.0},
}


class _Resp:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def close(self) -> None:  # pragma: no cover - trivial
        pass


# --- config ---------------------------------------------------------------


def test_config_defaults_invalid_without_webhook() -> None:
    cfg = Config()
    errors = mconfig.validate(cfg)
    assert any("webhook_url" in e for e in errors)


def test_config_load_file_and_env_override(tmp_path, monkeypatch) -> None:
    path = tmp_path / "monitor.json"
    path.write_text(json.dumps({"webhook_url": "https://file", "interval_seconds": 5}))
    monkeypatch.delenv("DGX_SPARK_WEBHOOK_URL", raising=False)
    cfg = mconfig.load(str(path))
    assert cfg.webhook_url == "https://file"
    assert cfg.interval_seconds == 5
    # env wins over file
    cfg2 = mconfig.load(str(path), environ={"DGX_SPARK_WEBHOOK_URL": "https://env"})
    assert cfg2.webhook_url == "https://env"


def test_config_bad_scheme_and_format() -> None:
    bad = Config(webhook_url="file:///etc/passwd", webhook_format="carrier-pigeon")
    errors = mconfig.validate(bad)
    assert any("http(s)" in e for e in errors)
    assert any("webhook_format" in e for e in errors)


def test_config_init_file_roundtrips(tmp_path) -> None:
    path = mconfig.init_file(str(tmp_path / "m.json"))
    assert path.is_file()
    data = json.loads(path.read_text())
    assert "thresholds" in data and "webhook_url" in data


def test_config_corrupt_file_falls_back(tmp_path) -> None:
    path = tmp_path / "monitor.json"
    path.write_text("{not json")
    cfg = mconfig.load(str(path), environ={})
    assert cfg.thresholds == mconfig.DEFAULT_THRESHOLDS


# --- rules ----------------------------------------------------------------


def test_evaluate_fires_all_default_conditions() -> None:
    keys = {a.key for a in evaluate(_HOT_SNAPSHOT, mconfig.DEFAULT_THRESHOLDS)}
    assert "memory_used_pct" in keys
    assert "swap_used_pct" in keys
    assert "disk:/" in keys
    assert "thermal_max_c" in keys
    assert "gpu_temp_c" in keys
    assert "load_per_core" in keys
    assert "container:c1" in keys


def test_evaluate_clear_snapshot_is_silent() -> None:
    calm = {
        "host": "h",
        "available": {"gpu": True, "containers": True},
        "memory": {"used_pct": 10.0, "swap_used_pct": 1.0},
        "disk": {"filesystems": [{"mount": "/", "used_pct": 5.0}]},
        "thermal": {"hottest_c": 40.0},
        "gpu": {"gpu": {"temperature.gpu": "45"}},
        "containers": {"containers": [{"name": "ok", "status": "Up (healthy)"}]},
        "load": {"per_core": 0.1},
    }
    assert evaluate(calm, mconfig.DEFAULT_THRESHOLDS) == []


def test_evaluate_null_threshold_disables_check() -> None:
    th = dict(mconfig.DEFAULT_THRESHOLDS, memory_used_pct=None)
    keys = {a.key for a in evaluate(_HOT_SNAPSHOT, th)}
    assert "memory_used_pct" not in keys


def test_evaluate_boundary_is_inclusive() -> None:
    snap = {"memory": {"used_pct": 92.0}}  # exactly at default threshold
    keys = {a.key for a in evaluate(snap, mconfig.DEFAULT_THRESHOLDS)}
    assert "memory_used_pct" in keys


def test_evaluate_subsystem_down() -> None:
    snap = {"available": {"gpu": False, "containers": True}}
    keys = {a.key for a in evaluate(snap, {"subsystem_down": True})}
    assert "subsystem_down:gpu" in keys


# --- state (edge-triggering) ----------------------------------------------


def test_state_diff_fire_resolve_renotify() -> None:
    a = Alert("memory_used_pct", "critical", "mem hot")
    # new -> firing
    events, firing = state.diff({}, [a], cycle=1, renotify_cycles=30)
    assert events == [{"status": "firing", "alert": a.to_dict()}]
    assert firing == {"memory_used_pct": 1}
    # still firing, not yet re-notify -> no event
    events2, firing2 = state.diff(firing, [a], cycle=2, renotify_cycles=30)
    assert events2 == []
    assert firing2 == {"memory_used_pct": 1}
    # re-notify window reached -> firing again
    events3, firing3 = state.diff(firing, [a], cycle=31, renotify_cycles=30)
    assert events3 and events3[0]["status"] == "firing"
    assert firing3 == {"memory_used_pct": 31}
    # cleared -> resolved
    events4, firing4 = state.diff(firing, [], cycle=3, renotify_cycles=30)
    assert events4 == [{"status": "resolved", "alert": {"key": "memory_used_pct"}}]
    assert firing4 == {}


def test_state_load_missing_and_corrupt(tmp_path) -> None:
    assert state.load_state(tmp_path / "nope.json") == {"firing": {}, "cycle": 0}
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    assert state.load_state(bad) == {"firing": {}, "cycle": 0}


def test_state_save_roundtrip(tmp_path) -> None:
    path = tmp_path / "sub" / "state.json"
    state.save_state(path, {"firing": {"k": 3}, "cycle": 3})
    assert state.load_state(path) == {"firing": {"k": 3}, "cycle": 3}


# --- notify ---------------------------------------------------------------


def test_render_payload_formats() -> None:
    events = [
        {"status": "firing", "alert": {"severity": "critical", "message": "boom", "key": "x"}}
    ]
    generic = notify.render_payload(events, host="h", ts="t")
    assert generic["events"] == events and generic["source"] == "dgx-spark-cli"
    slack = notify.render_payload(events, host="h", ts="t", fmt="slack")
    assert "text" in slack and "boom" in slack["text"]
    discord = notify.render_payload(events, host="h", ts="t", fmt="discord")
    assert "content" in discord and "boom" in discord["content"]


def test_post_rejects_non_http() -> None:
    ok, err = notify.post("file:///etc/passwd", {})
    assert ok is False and "http" in err


def test_post_success_and_records_request() -> None:
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        return _Resp(200)

    ok, err = notify.post("https://x/y", {"a": 1}, opener=opener)
    assert ok is True and err is None
    assert seen["url"] == "https://x/y" and seen["body"] == {"a": 1}


def test_post_http_error_and_retries() -> None:
    attempts = {"n": 0}

    def opener(req, timeout):
        attempts["n"] += 1
        raise urllib.error.URLError("down")

    ok, err = notify.post("https://x", {}, retries=2, opener=opener)
    assert ok is False and "down" in err
    assert attempts["n"] == 3  # initial + 2 retries


def test_post_non_2xx_is_failure() -> None:
    ok, err = notify.post("https://x", {}, retries=0, opener=lambda r, t: _Resp(503))
    assert ok is False and "503" in err


# --- engine ---------------------------------------------------------------


def test_run_once_fires_then_edge_triggers(tmp_path) -> None:
    sp = tmp_path / "state.json"
    cfg = Config(webhook_url="https://x", thresholds=mconfig.DEFAULT_THRESHOLDS)
    sent = []

    def opener(req, timeout):
        sent.append(json.loads(req.data))
        return _Resp(200)

    r1 = engine.run_once(cfg, state_path=sp, snap=_HOT_SNAPSHOT, opener=opener)
    assert r1["sent"] is True and len(r1["events"]) >= 6
    r2 = engine.run_once(cfg, state_path=sp, snap=_HOT_SNAPSHOT, opener=opener)
    assert r2["events"] == []  # nothing changed -> no spam
    assert len(sent) == 1


def test_run_once_no_webhook_does_not_commit(tmp_path) -> None:
    sp = tmp_path / "state.json"
    cfg = Config(webhook_url=None, thresholds=mconfig.DEFAULT_THRESHOLDS)
    r = engine.run_once(cfg, state_path=sp, snap=_HOT_SNAPSHOT)
    assert r["delivered"] is False and r["events"]
    # firing not committed -> next cycle re-detects the same transitions
    persisted = json.loads(sp.read_text())
    assert persisted["firing"] == {}
    r2 = engine.run_once(cfg, state_path=sp, snap=_HOT_SNAPSHOT)
    assert len(r2["events"]) == len(r["events"])


def test_run_once_delivery_failure_retries(tmp_path) -> None:
    sp = tmp_path / "state.json"
    cfg = Config(webhook_url="https://x", thresholds=mconfig.DEFAULT_THRESHOLDS)

    def failing(req, timeout):
        raise urllib.error.URLError("nope")

    r = engine.run_once(cfg, state_path=sp, snap=_HOT_SNAPSHOT, opener=failing)
    assert r["delivered"] is True and r["sent"] is False
    assert json.loads(sp.read_text())["firing"] == {}  # not committed -> retry next cycle


def test_run_loop_max_cycles(tmp_path) -> None:
    cfg = Config(webhook_url="https://x", interval_seconds=1, thresholds={})
    results = []
    ran = engine.run_loop(
        cfg,
        state_path=tmp_path / "s.json",
        sleep=lambda _s: None,
        emit=results.append,
        max_cycles=3,
    )
    assert ran == 3 and len(results) == 3


def test_snapshot_runs_on_host() -> None:
    snap = engine.snapshot(runner=lambda _n, _a: None)  # tool-backed subsystems dark
    assert "memory" in snap and "available" in snap
    assert snap["available"]["gpu"] is False  # fake runner -> nvidia-smi "absent"


# --- systemd --------------------------------------------------------------


def test_unit_text_and_exec_start() -> None:
    txt = systemd.unit_text("/tmp/cfg.json")
    assert "[Service]" in txt and "Restart=on-failure" in txt
    assert "-m spark monitor run --config /tmp/cfg.json" in txt
    assert systemd.exec_start("/x.json").endswith("-m spark monitor run --config /x.json")


def test_systemd_install_uninstall(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("spark.monitor.systemd.run_tool", lambda _n, _a: "ok")
    path = systemd.install("/tmp/cfg.json")
    assert path.is_file() and "dgx-spark-monitor.service" in str(path)
    removed = systemd.uninstall()
    assert not removed.is_file()


def test_systemd_enable_disable_stubbed(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("spark.monitor.systemd.run_tool", lambda n, a: calls.append((n, a)) or "ok")
    ok, err = systemd.enable(linger=False)
    assert ok is True and err is None
    ok2, _ = systemd.disable()
    assert ok2 is True


def test_systemd_status_shape() -> None:
    status = systemd.status()
    assert {"unit", "installed", "active", "enabled"} <= set(status)
