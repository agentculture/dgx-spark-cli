"""``spark thermal`` — SoC thermal zones and hwmon sensors from /sys.

Reads ``/sys/class/thermal/thermal_zone*`` (ACPI/SoC zones) and
``/sys/class/hwmon/hwmon*`` (nvme, wifi PHY, …). Temperatures are reported in
Celsius. This is the kernel's thermal view — GPU die temperature comes from
``nvidia-smi`` and is reported by ``spark gpu`` (and folded into ``spark
status``). No ``lm-sensors`` dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from spark.probe import _run
from spark.probe._report import report, unavailable

_HOT_C = 85.0  # warn at/above this temperature


def _read_temp_c(path: Path) -> Optional[float]:
    """Read a ``*_input``/``temp`` millidegree node and return Celsius."""
    raw = _run.read_first_line(path)
    if raw is None or raw == "":
        return None
    try:
        return int(raw) / 1000.0
    except ValueError:
        return None


def _record(tag: str, temp: float, kind: str, items: list, sensors: list, warnings: list) -> None:
    """Append one sensor reading to the running items/sensors/warnings lists."""
    items.append(f"{tag}: {temp:.1f} C")
    sensors.append({"kind": kind, "name": tag, "temp_c": temp})
    if temp >= _HOT_C:
        warnings.append(f"{tag} at {temp:.1f} C")


def _hotter(current: Optional[float], temp: float) -> float:
    return temp if current is None else max(current, temp)


def _zones(root: Path, sensors: list, warnings: list) -> tuple[list[str], Optional[float]]:
    hottest: Optional[float] = None
    items: list[str] = []
    if not root.is_dir():
        return items, hottest
    for zdir in sorted(root.glob("thermal_zone*")):
        ztype = _run.read_first_line(zdir / "type") or zdir.name
        temp = _read_temp_c(zdir / "temp")
        if temp is None:
            continue
        _record(ztype, temp, "zone", items, sensors, warnings)
        hottest = _hotter(hottest, temp)
    return items, hottest


def _hwmon_chip(
    hdir: Path, name: str, sensors: list, warnings: list
) -> tuple[list[str], Optional[float]]:
    hottest: Optional[float] = None
    items: list[str] = []
    for inp in sorted(hdir.glob("temp*_input")):
        temp = _read_temp_c(inp)
        if temp is None:
            continue
        label = _run.read_first_line(inp.parent / inp.name.replace("_input", "_label"))
        tag = f"{name}/{label}" if label else name
        _record(tag, temp, "hwmon", items, sensors, warnings)
        hottest = _hotter(hottest, temp)
    return items, hottest


def _hwmon(root: Path, sensors: list, warnings: list) -> tuple[list[str], Optional[float]]:
    hottest: Optional[float] = None
    items: list[str] = []
    if not root.is_dir():
        return items, hottest
    for hdir in sorted(root.glob("hwmon*")):
        name = _run.read_first_line(hdir / "name") or hdir.name
        if name == "acpitz":
            continue  # mirrors thermal_zone* readings — avoid double-reporting
        chip_items, chip_hot = _hwmon_chip(hdir, name, sensors, warnings)
        items.extend(chip_items)
        if chip_hot is not None:
            hottest = _hotter(hottest, chip_hot)
    return items, hottest


def collect(
    thermal_root: str = "/sys/class/thermal",
    hwmon_root: str = "/sys/class/hwmon",
) -> dict:
    """Return a thermal report from the two /sys roots (injectable for tests)."""
    sensors: list[dict[str, object]] = []
    warnings: list[str] = []
    sections: list[dict[str, object]] = []

    zone_items, zone_hottest = _zones(Path(thermal_root), sensors, warnings)
    if zone_items:
        sections.append({"title": "Thermal zones", "items": zone_items})

    hwmon_items, hwmon_hottest = _hwmon(Path(hwmon_root), sensors, warnings)
    if hwmon_items:
        sections.append({"title": "hwmon sensors", "items": hwmon_items})

    if not sections:
        return unavailable(
            "thermal", f"{thermal_root}, {hwmon_root}", "read /sys/class/thermal on a Linux host"
        )

    hottest = max((h for h in (zone_hottest, hwmon_hottest) if h is not None), default=None)
    sections.append(
        {
            "title": "Note",
            "items": ["GPU die temperature is reported by 'spark gpu' (nvidia-smi)."],
        }
    )
    data = {"hottest_c": hottest, "sensors": sensors}
    return report(
        "thermal",
        source=f"{thermal_root}, {hwmon_root}",
        sections=sections,
        warnings=warnings,
        data=data,
    )
