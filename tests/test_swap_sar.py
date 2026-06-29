"""Tests for spark.swap.sar — system-trend reader (sadf/sysstat).

All tests inject a fake runner — NO real sar/sadf is invoked and sysstat
need NOT be installed on the test machine.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence

from spark.swap.sar import read_swap_trend

# ---------------------------------------------------------------------------
# Realistic sadf -j fixture (2 timestamps)
# ---------------------------------------------------------------------------

_SADF_JSON_FIXTURE = json.dumps(
    {
        "sysstat": {
            "hosts": [
                {
                    "nodename": "testhost",
                    "sysname": "Linux",
                    "statistics": [
                        {
                            "timestamp": {
                                "date": "2024-01-01",
                                "time": "10:00:00",
                                "utc": 1,
                                "interval": 3600,
                            },
                            "swap-pages": {
                                "pswpin/s": 0.0,
                                "pswpout/s": 0.0,
                                "%swpused": 15.5,
                            },
                            "memory": {
                                "frmpg/s": 0.0,
                                "bufpg/s": 0.0,
                                "campg/s": 0.0,
                                "%memused": 72.3,
                            },
                        },
                        {
                            "timestamp": {
                                "date": "2024-01-01",
                                "time": "11:00:00",
                                "utc": 1,
                                "interval": 3600,
                            },
                            "swap-pages": {
                                "pswpin/s": 0.0,
                                "pswpout/s": 0.02,
                                "%swpused": 16.2,
                            },
                            "memory": {
                                "frmpg/s": 0.0,
                                "bufpg/s": 0.0,
                                "campg/s": 0.0,
                                "%memused": 75.1,
                            },
                        },
                    ],
                }
            ]
        }
    }
)


# ---------------------------------------------------------------------------
# Fake runner factory
# ---------------------------------------------------------------------------


def _make_runner(sadf_output: Optional[str], sar_output: Optional[str] = None):
    """Return a fake runner that serves canned output per tool name."""

    def _runner(name: str, args: Sequence[str]) -> Optional[str]:
        if name == "sadf":
            return sadf_output
        if name == "sar":
            return sar_output
        return None

    return _runner


# ---------------------------------------------------------------------------
# Acceptance criterion 1+2a: sadf present — parse and normalize series
# ---------------------------------------------------------------------------


class TestReadSwapTrendAvailable:
    """sadf present: assert parsed swap_used_pct / mem_used_pct values."""

    def test_available_true(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert result["available"] is True

    def test_source_is_sar(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert result["source"] == "sar"

    def test_series_has_two_entries(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert len(result["series"]) == 2

    def test_first_entry_swap_used_pct(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert result["series"][0]["swap_used_pct"] == 15.5

    def test_first_entry_mem_used_pct(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert result["series"][0]["mem_used_pct"] == 72.3

    def test_second_entry_swap_used_pct(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert result["series"][1]["swap_used_pct"] == 16.2

    def test_second_entry_mem_used_pct(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert result["series"][1]["mem_used_pct"] == 75.1

    def test_ts_string_first(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert result["series"][0]["ts"] == "2024-01-01 10:00:00"

    def test_ts_string_second(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert result["series"][1]["ts"] == "2024-01-01 11:00:00"

    def test_series_entries_have_required_keys(self):
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        for entry in result["series"]:
            assert set(entry.keys()) == {"ts", "swap_used_pct", "mem_used_pct"}


# ---------------------------------------------------------------------------
# Acceptance criterion 2b: runner returns None — graceful degradation
# ---------------------------------------------------------------------------


class TestReadSwapTrendUnavailable:
    """sysstat absent (runner returns None): no exception, available=False."""

    def test_available_false(self):
        result = read_swap_trend(hours=24, runner=_make_runner(None, None))
        assert result["available"] is False

    def test_source_is_none(self):
        result = read_swap_trend(hours=24, runner=_make_runner(None, None))
        assert result["source"] is None

    def test_series_is_empty(self):
        result = read_swap_trend(hours=24, runner=_make_runner(None, None))
        assert result["series"] == []

    def test_no_exception_raised(self):
        """Must NOT raise — this is the core of graceful degradation."""
        result = read_swap_trend(hours=24, runner=_make_runner(None, None))
        assert isinstance(result, dict)

    def test_return_shape_complete(self):
        result = read_swap_trend(hours=24, runner=_make_runner(None, None))
        assert set(result.keys()) == {"available", "source", "series"}


# ---------------------------------------------------------------------------
# Defensive parsing: malformed / partial sadf JSON
# ---------------------------------------------------------------------------


class TestReadSwapTrendEdgeCases:
    """Defensive parsing: malformed or partial sadf JSON must never raise."""

    def test_empty_json_object_returns_empty_series(self):
        result = read_swap_trend(hours=24, runner=_make_runner("{}"))
        assert result["available"] is True
        assert result["series"] == []

    def test_invalid_json_returns_empty_series_no_raise(self):
        result = read_swap_trend(hours=24, runner=_make_runner("not json at all"))
        assert result["available"] is True
        assert result["series"] == []

    def test_entry_missing_swap_pages_is_skipped(self):
        data = {
            "sysstat": {
                "hosts": [
                    {
                        "statistics": [
                            {
                                "timestamp": {"date": "2024-01-01", "time": "10:00:00"},
                                # "swap-pages" intentionally absent
                                "memory": {"%memused": 70.0},
                            }
                        ]
                    }
                ]
            }
        }
        result = read_swap_trend(hours=24, runner=_make_runner(json.dumps(data)))
        assert result["series"] == []

    def test_entry_missing_memory_is_skipped(self):
        data = {
            "sysstat": {
                "hosts": [
                    {
                        "statistics": [
                            {
                                "timestamp": {"date": "2024-01-01", "time": "10:00:00"},
                                "swap-pages": {"%swpused": 10.0},
                                # "memory" intentionally absent
                            }
                        ]
                    }
                ]
            }
        }
        result = read_swap_trend(hours=24, runner=_make_runner(json.dumps(data)))
        assert result["series"] == []

    def test_entry_missing_swpused_field_is_skipped(self):
        data = {
            "sysstat": {
                "hosts": [
                    {
                        "statistics": [
                            {
                                "timestamp": {"date": "2024-01-01", "time": "10:00:00"},
                                "swap-pages": {"pswpin/s": 0.0},  # missing %swpused
                                "memory": {"%memused": 70.0},
                            }
                        ]
                    }
                ]
            }
        }
        result = read_swap_trend(hours=24, runner=_make_runner(json.dumps(data)))
        assert result["series"] == []

    def test_values_rounded_to_two_decimals(self):
        data = {
            "sysstat": {
                "hosts": [
                    {
                        "statistics": [
                            {
                                "timestamp": {"date": "2024-01-01", "time": "10:00:00"},
                                "swap-pages": {"%swpused": 15.555},
                                "memory": {"%memused": 72.334},
                            }
                        ]
                    }
                ]
            }
        }
        result = read_swap_trend(hours=24, runner=_make_runner(json.dumps(data)))
        assert result["series"][0]["swap_used_pct"] == round(15.555, 2)
        assert result["series"][0]["mem_used_pct"] == round(72.334, 2)

    def test_ts_date_only_when_time_missing(self):
        """Timestamp with only date (no time key) must not crash."""
        data = {
            "sysstat": {
                "hosts": [
                    {
                        "statistics": [
                            {
                                "timestamp": {"date": "2024-01-01"},
                                "swap-pages": {"%swpused": 5.0},
                                "memory": {"%memused": 60.0},
                            }
                        ]
                    }
                ]
            }
        }
        result = read_swap_trend(hours=24, runner=_make_runner(json.dumps(data)))
        assert len(result["series"]) == 1
        assert result["series"][0]["ts"] == "2024-01-01"

    def test_multiple_hosts_all_parsed(self):
        """When multiple hosts present, all statistics are combined."""
        data = {
            "sysstat": {
                "hosts": [
                    {
                        "statistics": [
                            {
                                "timestamp": {"date": "2024-01-01", "time": "10:00:00"},
                                "swap-pages": {"%swpused": 10.0},
                                "memory": {"%memused": 50.0},
                            }
                        ]
                    },
                    {
                        "statistics": [
                            {
                                "timestamp": {"date": "2024-01-01", "time": "11:00:00"},
                                "swap-pages": {"%swpused": 11.0},
                                "memory": {"%memused": 55.0},
                            }
                        ]
                    },
                ]
            }
        }
        result = read_swap_trend(hours=24, runner=_make_runner(json.dumps(data)))
        assert len(result["series"]) == 2

    def test_hours_parameter_accepted(self):
        """hours parameter must be accepted without error (value is advisory)."""
        result = read_swap_trend(hours=48, runner=_make_runner(None))
        assert isinstance(result, dict)

    def test_return_shape_when_available(self):
        """Return dict must have exactly the required keys when available."""
        result = read_swap_trend(hours=24, runner=_make_runner(_SADF_JSON_FIXTURE))
        assert set(result.keys()) == {"available", "source", "series"}
