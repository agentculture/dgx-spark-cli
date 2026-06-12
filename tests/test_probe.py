"""Unit tests for the host-telemetry collectors in :mod:`spark.probe`.

Every collector takes an injectable file root and/or command runner, so these
tests run deterministically on any Linux host (no GPU, docker, or aarch64
required) by feeding fixtures and fake runners.
"""

from __future__ import annotations

from types import SimpleNamespace

from spark.probe import (
    _run,
    containers,
    contention,
    disk,
    gpu,
    memory,
    network,
    processes,
    status,
    thermal,
)

# --- _run helpers ---------------------------------------------------------


def test_run_tool_absent_returns_none() -> None:
    assert _run.run_tool("definitely-not-a-real-tool-xyz", []) is None


def test_read_text_missing_returns_none() -> None:
    assert _run.read_text("/no/such/path/xyz") is None


# --- memory ---------------------------------------------------------------

_MEMINFO = """\
MemTotal:       1000 kB
MemFree:         100 kB
MemAvailable:     50 kB
Buffers:          10 kB
Cached:           20 kB
Shmem:             5 kB
SwapTotal:       200 kB
SwapFree:         40 kB
"""


def test_memory_parses_and_warns(tmp_path) -> None:
    path = tmp_path / "meminfo"
    path.write_text(_MEMINFO)
    rep = memory.collect(str(path))
    data = rep["data"]
    assert rep["available"] is True
    assert data["total_bytes"] == 1000 * 1024
    assert data["available_bytes"] == 50 * 1024
    assert data["used_bytes"] == 950 * 1024
    assert round(data["used_pct"]) == 95
    # 5% available (< 10%) and 80% swap used (> 25%) -> two warnings.
    assert len(rep["warnings"]) == 2


def test_memory_unavailable_when_missing() -> None:
    rep = memory.collect("/no/such/meminfo")
    assert rep["available"] is False
    assert rep["remediation"]


# --- contention -----------------------------------------------------------

_STAT_1 = """\
cpu  100 0 50 1000 200 0 0 0 0 0
cpu0 50 0 25 500 100 0 0 0 0 0
procs_running 2
procs_blocked 10
"""
_STAT_2 = """\
cpu  110 0 60 1100 500 0 0 0 0 0
cpu0 55 0 30 550 250 0 0 0 0 0
procs_running 1
procs_blocked 22
"""


def _two_shot(*samples):
    """A sampler that returns each ``samples`` value in turn, then the last."""
    queue = list(samples)

    def sampler():
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return sampler


def test_contention_computes_iowait_and_blocked() -> None:
    rep = contention.collect(_two_shot(_STAT_1, _STAT_2), sleep=lambda _s: None)
    data = rep["data"]
    assert rep["available"] is True
    # d_iowait = 300, d_total = (1770-1350) = 420 -> 71%; blocked from 2nd sample.
    assert round(data["iowait_pct"]) == 71
    assert data["blocked_procs"] == 22
    assert len(rep["warnings"]) == 2  # high iowait + many blocked


def test_contention_unavailable_when_stat_missing() -> None:
    rep = contention.collect(lambda: None, sleep=lambda _s: None)
    assert rep["available"] is False


def test_contention_second_read_fail_iowait_is_none_not_zero() -> None:
    # Second read fails: iowait can't be measured -> None ("n/a"), NOT a
    # misleading 0%. The instantaneous blocked count still comes through.
    rep = contention.collect(_two_shot(_STAT_1, None), sleep=lambda _s: None)
    data = rep["data"]
    assert rep["available"] is True
    assert data["iowait_pct"] is None
    assert data["blocked_procs"] == 10


# --- disk -----------------------------------------------------------------


def _fake_statvfs(_path: str):
    # 100 blocks * 1024 = 102400 total; 10 free -> 90% used; 8 avail.
    return SimpleNamespace(f_blocks=100, f_frsize=1024, f_bfree=10, f_bavail=8)


def test_disk_filters_virtual_and_warns_when_full(tmp_path) -> None:
    mounts = tmp_path / "mounts"
    mounts.write_text(
        "/dev/nvme0n1p2 / ext4 rw 0 0\n"
        "/dev/loop0 /snap/x squashfs ro 0 0\n"
        "tmpfs /run tmpfs rw 0 0\n"
        "proc /proc proc rw 0 0\n"
    )
    rep = disk.collect(str(mounts), statvfs=_fake_statvfs)
    filesystems = rep["data"]["filesystems"]
    assert len(filesystems) == 1  # loop/tmpfs/proc all filtered out
    assert filesystems[0]["mount"] == "/"
    assert filesystems[0]["used_pct"] == 90.0
    assert rep["warnings"]  # 90% >= 85% full


def test_disk_unavailable_when_missing() -> None:
    rep = disk.collect("/no/such/mounts")
    assert rep["available"] is False


# --- thermal --------------------------------------------------------------


def test_thermal_reads_zones_and_hwmon_dedups_acpitz(tmp_path) -> None:
    tz = tmp_path / "thermal"
    (tz / "thermal_zone0").mkdir(parents=True)
    (tz / "thermal_zone0" / "type").write_text("acpitz\n")
    (tz / "thermal_zone0" / "temp").write_text("56000\n")

    hw = tmp_path / "hwmon"
    (hw / "hwmon0").mkdir(parents=True)
    (hw / "hwmon0" / "name").write_text("acpitz\n")  # duplicate of zones
    (hw / "hwmon0" / "temp1_input").write_text("56000\n")
    (hw / "hwmon1").mkdir()
    (hw / "hwmon1" / "name").write_text("nvme\n")
    (hw / "hwmon1" / "temp1_input").write_text("48900\n")
    (hw / "hwmon1" / "temp1_label").write_text("Composite\n")

    rep = thermal.collect(str(tz), str(hw))
    assert rep["available"] is True
    assert rep["data"]["hottest_c"] == 56.0
    names = [s["name"] for s in rep["data"]["sensors"]]
    assert "acpitz" in names
    assert "nvme/Composite" in names
    # acpitz hwmon must be skipped (it mirrors the zone) -> appears once only.
    assert names.count("acpitz") == 1


def test_thermal_warns_when_hot(tmp_path) -> None:
    tz = tmp_path / "thermal"
    (tz / "thermal_zone0").mkdir(parents=True)
    (tz / "thermal_zone0" / "type").write_text("soc\n")
    (tz / "thermal_zone0" / "temp").write_text("90000\n")  # 90 C
    rep = thermal.collect(str(tz), str(tmp_path / "no-hwmon"))
    assert rep["warnings"]


def test_thermal_unavailable_when_empty(tmp_path) -> None:
    rep = thermal.collect(str(tmp_path / "none"), str(tmp_path / "none2"))
    assert rep["available"] is False


# --- processes ------------------------------------------------------------


def test_processes_ranks_by_rss(tmp_path) -> None:
    (tmp_path / "meminfo").write_text("MemTotal: 1000 kB\n")
    big = tmp_path / "1234"
    big.mkdir()
    (big / "status").write_text("Name:\tbig\nState:\tS (sleeping)\nVmRSS:\t 500 kB\n")
    (big / "cmdline").write_text("big\x00--flag\x00")
    small = tmp_path / "5678"
    small.mkdir()
    (small / "status").write_text("Name:\tsmall\nState:\tR\nVmRSS:\t 100 kB\n")
    (small / "cmdline").write_text("small\x00")
    # A kernel thread (no VmRSS) must be skipped.
    kthread = tmp_path / "2"
    kthread.mkdir()
    (kthread / "status").write_text("Name:\tkworker\nState:\tS\n")

    rep = processes.collect(str(tmp_path))
    top = rep["data"]["top"]
    assert rep["data"]["count"] == 2  # kworker skipped
    assert top[0]["pid"] == 1234
    assert top[0]["rss_bytes"] == 500 * 1024
    assert top[1]["pid"] == 5678


def test_processes_unavailable_when_missing() -> None:
    rep = processes.collect("/no/such/proc")
    assert rep["available"] is False


# --- gpu ------------------------------------------------------------------


def _gpu_runner(name: str, args) -> str:
    assert name == "nvidia-smi"
    joined = " ".join(args)
    if "--query-gpu" in joined:
        # name,util.gpu,util.mem,temp,power.draw,power.limit,mem.total,mem.used,sm,fan
        return "NVIDIA GB10, 0, 0, 50, 11.75, [N/A], [N/A], [N/A], 2398, [N/A]\n"
    if "--query-compute-apps" in joined:
        return "576315, VLLM::EngineCore, 72841\n4390, python3, 4969\n"
    return ""


def test_gpu_sums_attributed_memory_when_unified() -> None:
    rep = gpu.collect(runner=_gpu_runner)
    assert rep["available"] is True
    assert rep["data"]["gpu_attributed_mib"] == 72841 + 4969
    text = " ".join(rep["sections"][0]["items"])
    assert "unified" in text
    assert "attributed" in text
    assert len(rep["data"]["compute_apps"]) == 2


def test_gpu_unavailable_without_nvidia_smi() -> None:
    rep = gpu.collect(runner=lambda _n, _a: None)
    assert rep["available"] is False
    assert rep["remediation"]


# --- network --------------------------------------------------------------

_ADDR = """\
lo               UNKNOWN        127.0.0.1/8 ::1/128
wlP9s9           UP             192.168.1.157/24 fe80::1/64
tailscale0       UNKNOWN        100.127.105.72/32
br-abc123        UP             172.20.0.1/16
veth9999@if2     UP             fe80::2/64
docker0          DOWN           172.17.0.1/16
"""
_ROUTE = "default via 192.168.1.1 dev wlP9s9 proto dhcp src 192.168.1.157 metric 600\n"


def _net_runner(name: str, args):
    assert name == "ip"
    if list(args[:2]) == ["-br", "addr"]:
        return _ADDR
    if list(args[:1]) == ["route"]:
        return _ROUTE
    return None


def test_network_summarizes_and_excludes_bridge_addrs() -> None:
    rep = network.collect(runner=_net_runner)
    data = rep["data"]
    assert rep["available"] is True
    assert data["veth_count"] == 1
    assert data["bridge_count"] == 2  # br-abc123 + docker0
    # Reachable = LAN + tailscale, NOT the 172.x docker bridge gateways.
    assert set(data["reachable_ipv4"]) == {"192.168.1.157", "100.127.105.72"}
    assert data["default_routes"][0]["dev"] == "wlP9s9"


def test_network_unavailable_without_ip() -> None:
    rep = network.collect(runner=lambda _n, _a: None)
    assert rep["available"] is False


# --- containers -----------------------------------------------------------

_DOCKER = (
    '{"Names":"vllm","Status":"Up 2 days (healthy)",'
    '"Image":"nvcr.io/nvidia/vllm:26.04","State":"running"}\n'
    '{"Names":"tei","Status":"Up 7 days (unhealthy)",'
    '"Image":"qq-tei","State":"running"}\n'
)


def test_containers_flags_unhealthy_and_gpu() -> None:
    rep = containers.collect(runner=lambda _n, _a: _DOCKER)
    data = rep["data"]
    assert len(data["containers"]) == 2
    assert any("unhealthy" in w for w in rep["warnings"])
    gpu_flags = {c["name"]: c["gpu"] for c in data["containers"]}
    assert gpu_flags["vllm"] is True  # nvcr.io image
    assert gpu_flags["tei"] is False


def test_containers_unavailable_without_docker() -> None:
    rep = containers.collect(runner=lambda _n, _a: None)
    assert rep["available"] is False


# --- status (aggregator) --------------------------------------------------


def test_status_has_host_and_subsystems() -> None:
    # Fake runner makes the tool-backed subsystems unavailable; the /proc-backed
    # ones (memory/disk/thermal/processes) still read the real Linux host.
    rep = status.collect(runner=lambda _n, _a: None)
    assert rep["subject"] == "status"
    titles = [s["title"] for s in rep["sections"]]
    assert "Host" in titles
    assert "Subsystems" in titles
    assert isinstance(rep["warnings"], list)
    assert "host" in rep["data"]
