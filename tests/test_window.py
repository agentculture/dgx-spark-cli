"""window / drop-caches / grant-permissions with faked subprocess + sudo."""

from __future__ import annotations

import json

import pytest

from spark.cli import main
from spark.window import grant
from spark.window import window as win
from spark.window.ctx import Ctx

SYSCTL_LISTING = "User x may run:\n    (root) NOPASSWD: /usr/sbin/sysctl -w vm.drop_caches\\=3\n"
TEE_LISTING = "    (root) NOPASSWD: /usr/bin/tee /proc/sys/vm/drop_caches\n"
ALL_LISTING = "    (ALL : ALL) NOPASSWD: ALL\n"
PASSWORD_ALL = "    (ALL : ALL) ALL\n"
WINDOW_LISTING = (
    SYSCTL_LISTING + "    (root) NOPASSWD: /usr/bin/nvidia-smi -lgc *, /usr/bin/nvidia-smi -rgc\n"
)
CLOCKS_OK = "Supported Clocks\n    Memory : 1 MHz\n        Graphics : 2 MHz\n"


class Fake:
    def __init__(self, listing: str = "", clocks: str = "Supported Clocks : N/A", fail=()):
        self.listing, self.clocks, self.fail = listing, clocks, set(fail)
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        if argv[:3] == ["sudo", "-n", "-l"]:
            return (0, self.listing, "") if self.listing else (1, "", "password required")
        if argv[0] == "nvidia-smi":
            return 0, self.clocks, ""
        if any(f in argv for f in self.fail):
            return 1, "", "boom"
        return 0, "", ""

    def argvs(self):
        return [c[0] for c in self.calls]


def ctx(tmp_path, fake, clk=1000) -> Ctx:
    return Ctx(
        proc=fake,
        state_file=tmp_path / "w.json",
        power=lambda: {"data": {"clocks_sm_mhz": clk, "clocks_max_sm_mhz": 3003, "pstate": "P0"}},
        thermal=lambda: {"data": {"sensors": [{"name": "tj", "temp_c": 50.0}]}},
        which=lambda n: "/usr/bin/" + n,
        user=lambda: "tester",
    )


def test_store_then_restore_returns_to_stored_state(tmp_path):
    f = Fake(WINDOW_LISTING, CLOCKS_OK)
    c = ctx(tmp_path, f)
    assert win.store(c)["stored"] is True
    r = win.lock(c)
    assert r["ok"] and r["active"] is True
    assert ["sudo", "-n", "/usr/bin/nvidia-smi", "-lgc", "3003,3003"] in f.argvs()
    # second store while active is refused
    s = win.store(c)
    assert (s["ok"], s["reason"], s["exit"]) == (False, "window_active", 1)
    r = win.restore(c)
    assert r["ok"] and r["restored"] is True and r["stored"] is False and r["active"] is False
    assert ["sudo", "-n", "/usr/bin/nvidia-smi", "-rgc"] in f.argvs()
    assert not (tmp_path / "w.json").exists()


def test_restore_without_state_is_ok_noop(tmp_path):
    r = win.restore(ctx(tmp_path, Fake()))
    assert (r["ok"], r["restored"], r["exit"]) == (True, False, 0)


def test_gb10_lock_unsupported_never_touches_clocks(tmp_path):
    f = Fake(WINDOW_LISTING)
    c = ctx(tmp_path, f)
    win.store(c)
    r = win.lock(c)
    assert (r["ok"], r["reason"], r["exit"], r["supported"]) == (False, "unsupported", 2, False)
    assert all("-lgc" not in argv for argv in f.argvs())


def test_lock_requires_store_and_grant(tmp_path):
    c = ctx(tmp_path, Fake(WINDOW_LISTING, CLOCKS_OK))
    assert win.lock(c)["reason"] == "no_stored_state"
    c2 = ctx(tmp_path / "b", Fake(SYSCTL_LISTING, CLOCKS_OK))
    win.store(c2)
    r = win.lock(c2)
    assert (r["reason"], r["exit"]) == ("not_granted", 2)


def test_lock_failure_reported(tmp_path):
    c = ctx(tmp_path, Fake(WINDOW_LISTING, CLOCKS_OK, fail=["-lgc"]))
    win.store(c)
    r = win.lock(c)
    assert (r["reason"], r["exit"], r["active"]) == ("failed", 2, False)


def test_show_shape(tmp_path):
    r = win.show(ctx(tmp_path, Fake()))
    assert r["ok"] and r["reason"] is None and r["clocks"]["gpu_mhz"] == 1000
    assert r["temps_c"] == {"tj": 50.0} and r["power_mode"] is None and r["fan_profile"] is None


def test_clocks_locked_is_null_in_every_show_shaped_payload(tmp_path):
    c = ctx(tmp_path, Fake())
    payloads = [win.show(c), win.store(c), win.lock(c), win.restore(c)]
    for r in payloads:
        assert "clocks_locked" in r and r["clocks_locked"] is None


@pytest.mark.parametrize(
    "listing,rule",
    [(SYSCTL_LISTING, "sysctl"), (TEE_LISTING, "tee"), (ALL_LISTING, "all")],
)
def test_existing_rule_detected_and_reused(tmp_path, listing, rule):
    f = Fake(listing)
    out = grant.install(ctx(tmp_path, f), False, assume_yes=True)
    assert out["drop_caches"]["rule"] == rule and out["installed"] is False and out["exit"] == 0
    assert not any(a[:2] == ["sudo", "install"] for a in f.argvs())
    assert not any(a[0] == "visudo" for a in f.argvs())


def test_password_all_is_not_a_grant(tmp_path):
    assert grant.check(ctx(tmp_path, Fake(PASSWORD_ALL)), False)["drop_caches"]["granted"] is False


def test_drop_caches_argv_per_rule(tmp_path):
    f = Fake(SYSCTL_LISTING)
    assert grant.drop_caches(ctx(tmp_path, f)) == {
        "exit": 0,
        "ok": True,
        "reason": None,
        "cleared": True,
        "rule": "sysctl",
    }
    assert ["sudo", "-n", "/usr/sbin/sysctl", "-w", "vm.drop_caches=3"] in f.argvs()
    assert ["sync"] in f.argvs()
    f = Fake(TEE_LISTING)
    grant.drop_caches(ctx(tmp_path, f))
    assert (["sudo", "-n", "tee", "/proc/sys/vm/drop_caches"], "3") in f.calls


def test_no_grant_exit_and_reason_no_prompt(tmp_path):
    f = Fake("")
    out = grant.drop_caches(ctx(tmp_path, f))
    assert (out["exit"], out["ok"], out["reason"]) == (2, False, "not_granted")
    assert all(a[:2] == ["sudo", "-n"] or a[0] == "sync" for a in f.argvs())
    assert not any(a == ["sync"] for a in f.argvs())  # nothing ran past the check


def test_install_declined_exit_1(tmp_path):
    f = Fake(PASSWORD_ALL)
    out = grant.install(ctx(tmp_path, f), False, False, confirm=lambda r: False)
    assert (out["reason"], out["exit"], out["installed"]) == ("declined", 1, False)
    assert not any(a[:2] == ["sudo", "install"] for a in f.argvs())


def test_install_runs_visudo_then_one_install(tmp_path):
    f = Fake(PASSWORD_ALL)
    out = grant.install(ctx(tmp_path, f), False, True)
    assert out["installed"] is True and out["exit"] == 0
    argvs = f.argvs()
    assert argvs[1][:2] == ["visudo", "-cf"]
    inst = [a for a in argvs if a[:2] == ["sudo", "install"]]
    assert len(inst) == 1 and inst[0][-1] == "/etc/sudoers.d/spark-lobes"
    assert out["rules"] == ["tester ALL=(ALL) NOPASSWD: /usr/sbin/sysctl -w vm.drop_caches=3"]


def test_install_visudo_rejects(tmp_path):
    f = Fake(PASSWORD_ALL, fail=["visudo"])
    out = grant.install(ctx(tmp_path, f), False, True)
    assert (out["reason"], out["exit"]) == ("failed", 2)
    assert not any(a[:2] == ["sudo", "install"] for a in f.argvs())


def test_cli_json_single_object_and_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("spark.cli._commands.window.make_ctx", lambda: ctx(tmp_path, Fake()))
    rc = main(["drop-caches", "--json"])
    assert rc == 2
    obj = json.loads(capsys.readouterr().out)
    assert obj["reason"] == "not_granted" and "exit" not in obj
    rc = main(["grant-permissions", "--check", "--json"])
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0 and obj["drop_caches"]["granted"] is False
    rc = main(["window", "store", "--json"])
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0 and obj["action"] == "store" and obj["stored"] is True
    rc = main(["window", "lock", "--json"])
    assert rc == 2 and json.loads(capsys.readouterr().out)["reason"] == "unsupported"
