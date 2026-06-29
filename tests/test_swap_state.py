"""Tests for spark.swap.state — swap state inspection (t1).

All tests inject fake /proc content via the ``read`` and ``statvfs``
parameters — no root required, no dependency on the host's real swap.
"""

from __future__ import annotations

from typing import Optional

from spark.swap.state import collect_swap_state

# ---------------------------------------------------------------------------
# Sample /proc content
# ---------------------------------------------------------------------------

MEMINFO = """\
MemTotal:       16384000 kB
MemFree:         2048000 kB
MemAvailable:   10240000 kB
Buffers:          512000 kB
Cached:          3000000 kB
Shmem:            100000 kB
SwapTotal:       8192000 kB
SwapFree:        6144000 kB
SwapCached:            0 kB
"""

SWAPS_FILE = (
    "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
    "/swap.img                              \tfile    \t8388604\t0      \t-2\n"
)

SWAPS_PARTITION = (
    "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
    "/dev/sda5                              \tpartition\t8388604\t0      \t-2\n"
)

SWAPS_MULTI = (
    "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
    "/swap.img                              \tfile    \t8388604\t1024   \t-2\n"
    "/dev/sda5                              \tpartition\t4194304\t0      \t-3\n"
)

SWAPS_EMPTY = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"

SWAPPINESS = "60\n"

MOUNTS = """\
sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
/dev/sda1 / ext4 rw,relatime 0 0
/dev/sda2 /home ext4 rw,relatime 0 0
tmpfs /tmp tmpfs rw,nosuid,nodev 0 0
"""

MEMINFO_NO_SWAP = """\
MemTotal:       16384000 kB
MemFree:         2048000 kB
MemAvailable:   10240000 kB
SwapTotal:              0 kB
SwapFree:               0 kB
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_reader(files: dict) -> callable:
    """Return a fake ``read`` function backed by a path->content dict."""

    def _read(path: str) -> Optional[str]:
        return files.get(str(path))

    return _read


class _FakeStatvfs:
    """Minimal statvfs-like object."""

    def __init__(self, f_bavail: int, f_frsize: int) -> None:
        self.f_bavail = f_bavail
        self.f_frsize = f_frsize


def make_statvfs(results: dict) -> callable:
    """Return a fake statvfs backed by a mount->``(f_bavail, f_frsize)`` dict."""

    def _statvfs(path: str):
        if path in results:
            bavail, frsize = results[path]
            return _FakeStatvfs(bavail, frsize)
        raise OSError(f"no fake statvfs for {path!r}")

    return _statvfs


def _full_files(**overrides) -> dict:
    """Default set of /proc files, with optional per-key overrides."""
    base = {
        "/proc/meminfo": MEMINFO,
        "/proc/swaps": SWAPS_FILE,
        "/proc/sys/vm/swappiness": SWAPPINESS,
        "/proc/mounts": MOUNTS,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# AC-1: Parses all /proc sources into the correct schema
# ---------------------------------------------------------------------------


class TestSchema:
    """AC-1 — full parse, correct shape and values."""

    def _result(self):
        files = _full_files()
        sv = make_statvfs({"/": (1_000_000, 4096)})
        return collect_swap_state(read=make_reader(files), statvfs=sv)

    def test_top_level_keys_present(self):
        r = self._result()
        assert set(r.keys()) >= {"available", "swappiness", "mem", "devices", "backing"}

    def test_available_true(self):
        assert self._result()["available"] is True

    def test_swappiness_parsed_as_int(self):
        assert self._result()["swappiness"] == 60

    def test_mem_keys_present(self):
        mem = self._result()["mem"]
        required = {
            "total_bytes",
            "available_bytes",
            "free_bytes",
            "used_bytes",
            "used_pct",
            "swap_total_bytes",
            "swap_free_bytes",
            "swap_used_bytes",
            "swap_used_pct",
        }
        assert set(mem.keys()) >= required

    def test_mem_total_bytes(self):
        assert self._result()["mem"]["total_bytes"] == 16_384_000 * 1024

    def test_mem_available_bytes(self):
        assert self._result()["mem"]["available_bytes"] == 10_240_000 * 1024

    def test_mem_free_bytes(self):
        assert self._result()["mem"]["free_bytes"] == 2_048_000 * 1024

    def test_mem_used_bytes_is_total_minus_available(self):
        mem = self._result()["mem"]
        assert mem["used_bytes"] == mem["total_bytes"] - mem["available_bytes"]

    def test_mem_used_pct_rounded(self):
        mem = self._result()["mem"]
        expected = round(mem["used_bytes"] / mem["total_bytes"] * 100, 2)
        assert mem["used_pct"] == expected

    def test_swap_total_bytes(self):
        assert self._result()["mem"]["swap_total_bytes"] == 8_192_000 * 1024

    def test_swap_free_bytes(self):
        assert self._result()["mem"]["swap_free_bytes"] == 6_144_000 * 1024

    def test_swap_used_bytes_is_total_minus_free(self):
        mem = self._result()["mem"]
        assert mem["swap_used_bytes"] == mem["swap_total_bytes"] - mem["swap_free_bytes"]

    def test_swap_used_pct_rounded(self):
        mem = self._result()["mem"]
        expected = round(mem["swap_used_bytes"] / mem["swap_total_bytes"] * 100, 2)
        assert mem["swap_used_pct"] == expected

    def test_devices_list_length(self):
        assert len(self._result()["devices"]) == 1

    def test_device_name(self):
        assert self._result()["devices"][0]["name"] == "/swap.img"

    def test_device_type_file(self):
        assert self._result()["devices"][0]["type"] == "file"

    def test_device_size_bytes_kib_to_bytes(self):
        # 8388604 KiB → bytes
        assert self._result()["devices"][0]["size_bytes"] == 8_388_604 * 1024

    def test_device_used_bytes(self):
        assert self._result()["devices"][0]["used_bytes"] == 0

    def test_device_priority(self):
        assert self._result()["devices"][0]["priority"] == -2

    def test_backing_keys_present(self):
        backing = self._result()["backing"]
        assert set(backing.keys()) >= {"swapfile", "fs_type", "mount", "free_bytes"}

    def test_backing_swapfile(self):
        assert self._result()["backing"]["swapfile"] == "/swap.img"

    def test_backing_fs_type(self):
        assert self._result()["backing"]["fs_type"] == "ext4"

    def test_backing_mount(self):
        assert self._result()["backing"]["mount"] == "/"

    def test_backing_free_bytes(self):
        # f_bavail=1_000_000, f_frsize=4096
        assert self._result()["backing"]["free_bytes"] == 1_000_000 * 4096


# ---------------------------------------------------------------------------
# AC-2: Missing probe sources => available: False, no exception
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """AC-2 — missing sources set available=False and never raise."""

    def test_missing_meminfo_does_not_raise(self):
        files = _full_files(**{"/proc/meminfo": None})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["available"] is False

    def test_missing_meminfo_returns_full_schema(self):
        files = _full_files(**{"/proc/meminfo": None})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert "mem" in result
        assert "devices" in result
        assert "backing" in result

    def test_missing_meminfo_zeros_mem(self):
        files = _full_files(**{"/proc/meminfo": None})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["mem"]["total_bytes"] == 0
        assert result["mem"]["used_bytes"] == 0

    def test_missing_swaps_does_not_raise(self):
        files = _full_files(**{"/proc/swaps": None})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["available"] is False

    def test_missing_swaps_returns_full_schema(self):
        files = _full_files(**{"/proc/swaps": None})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert "mem" in result
        assert "devices" in result
        assert "backing" in result

    def test_missing_swaps_swap_total_zero(self):
        files = _full_files(**{"/proc/swaps": None})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["mem"]["swap_total_bytes"] == 0

    def test_both_missing_does_not_raise(self):
        files = _full_files(**{"/proc/meminfo": None, "/proc/swaps": None})
        sv = make_statvfs({})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["available"] is False

    def test_missing_swappiness_yields_none(self):
        files = _full_files(**{"/proc/sys/vm/swappiness": None})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["available"] is True  # doesn't affect availability
        assert result["swappiness"] is None

    def test_missing_mounts_backing_none(self):
        files = _full_files(**{"/proc/mounts": None})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["available"] is True
        backing = result["backing"]
        assert backing["swapfile"] == "/swap.img"
        assert backing["fs_type"] is None
        assert backing["mount"] is None
        assert backing["free_bytes"] is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional coverage for edge-case branches."""

    def test_no_swap_configured_pct_is_zero(self):
        """SwapTotal=0 must not produce division-by-zero; swap_used_pct = 0.0."""
        files = _full_files(
            **{
                "/proc/meminfo": MEMINFO_NO_SWAP,
                "/proc/swaps": SWAPS_EMPTY,
            }
        )
        sv = make_statvfs({})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["available"] is True
        assert result["mem"]["swap_used_pct"] == 0.0
        assert result["devices"] == []

    def test_partition_swap_backing_all_none(self):
        """Partition-only swap has no file-backed device; backing fields are None."""
        files = _full_files(**{"/proc/swaps": SWAPS_PARTITION})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        backing = result["backing"]
        assert backing["swapfile"] is None
        assert backing["fs_type"] is None
        assert backing["mount"] is None
        assert backing["free_bytes"] is None

    def test_multiple_devices_all_parsed(self):
        """All rows in /proc/swaps are returned in devices list."""
        files = _full_files(**{"/proc/swaps": SWAPS_MULTI})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert len(result["devices"]) == 2
        assert result["devices"][0]["name"] == "/swap.img"
        assert result["devices"][0]["type"] == "file"
        assert result["devices"][1]["name"] == "/dev/sda5"
        assert result["devices"][1]["type"] == "partition"

    def test_multiple_devices_backing_uses_first_file(self):
        """When multiple devices exist, backing uses the first file-type one."""
        files = _full_files(**{"/proc/swaps": SWAPS_MULTI})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["backing"]["swapfile"] == "/swap.img"

    def test_swap_used_bytes_for_file_device(self):
        """Non-zero used KiB is converted to bytes correctly."""
        files = _full_files(**{"/proc/swaps": SWAPS_MULTI})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        # First device has 1024 KiB used
        assert result["devices"][0]["used_bytes"] == 1024 * 1024

    def test_statvfs_error_backing_free_bytes_none(self):
        """If statvfs raises OSError, backing.free_bytes is None (not raised)."""
        files = _full_files()
        sv = make_statvfs({})  # no entry → raises OSError
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert result["available"] is True
        assert result["backing"]["swapfile"] == "/swap.img"
        assert result["backing"]["free_bytes"] is None

    def test_return_type_is_dict(self):
        files = _full_files()
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        assert isinstance(result, dict)

    def test_devices_type_values(self):
        """Type field is exactly 'file' or 'partition'."""
        files = _full_files(**{"/proc/swaps": SWAPS_MULTI})
        sv = make_statvfs({"/": (1000, 4096)})
        result = collect_swap_state(read=make_reader(files), statvfs=sv)
        for dev in result["devices"]:
            assert dev["type"] in ("file", "partition")
