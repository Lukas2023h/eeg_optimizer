"""Tests for eeg_optimizer.devices (F4-01)."""

from __future__ import annotations

from datetime import time, timedelta
from pathlib import Path
from typing import Any

import pytest

from eeg_optimizer.config import ConfigError, load_config
from eeg_optimizer.devices import (
    ModulatingDevice,
    ProgramDevice,
    TimeWindow,
    devices_for,
    parse_device,
    parse_devices,
)
from eeg_optimizer.units import KW, KWh

COMMUNITY_YAML = """\
households:
  - id: hh01
    load: {source: synthetic}
    devices:
      - {id: ev, kind: modulating, power_kw: 11.0, energy_kwh_per_day: 8.0,
         window: {start: "18:00", end: "07:00"}, baseline_start: "18:00"}
      - {id: wm, kind: program, power_kw: 2.0, duration_min: 90,
         window: {start: "08:00", end: "20:00"}, baseline_start: "18:30"}
  - id: hh02
    load: {source: synthetic}
    devices:
      - id: wallbox
        kind: modulating
        power_kw: 11.0
        energy_kwh_per_day: 10.0
        window: {start: "17:00", end: "08:00"}
        baseline_start: "17:00"
        min_power_kw: 4.1
        min_run_min: 60
  - id: hh03
    load: {source: synthetic}
"""


def _ev(**changes: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "ev",
        "kind": "modulating",
        "power_kw": 11.0,
        "energy_kwh_per_day": 8.0,
        "window": {"start": "18:00", "end": "07:00"},
        "baseline_start": "18:00",
    }
    raw.update(changes)
    return raw


def _wm(**changes: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "wm",
        "kind": "program",
        "power_kw": 2.0,
        "duration_min": 90,
        "window": {"start": "08:00", "end": "20:00"},
        "baseline_start": "18:30",
    }
    raw.update(changes)
    return raw


# ---------------------------------------------------------------- loading from YAML


def test_devices_load_from_yaml_section(tmp_path: Path) -> None:
    path = tmp_path / "community.yaml"
    path.write_text(COMMUNITY_YAML, encoding="utf-8")
    devices = devices_for(load_config(path))

    assert list(devices) == ["hh01", "hh02", "hh03"]
    ev, wm = devices["hh01"]
    assert ev == ModulatingDevice(
        id="ev",
        power_kw=KW(11.0),
        energy_kwh_per_day=KWh(8.0),
        window=TimeWindow(time(18, 0), time(7, 0)),
        baseline_start=time(18, 0),
    )
    assert not ev.needs_binaries
    assert isinstance(wm, ProgramDevice)
    assert wm.duration == timedelta(minutes=90)
    assert wm.energy_kwh_per_day == pytest.approx(3.0)
    assert wm.baseline_start == time(18, 30)

    (wallbox,) = devices["hh02"]
    assert isinstance(wallbox, ModulatingDevice)
    assert wallbox.min_power_kw == 4.1
    assert wallbox.min_run_min == 60
    assert wallbox.needs_binaries
    assert devices["hh03"] == ()


def test_invalid_device_in_yaml_names_household_path(tmp_path: Path) -> None:
    path = tmp_path / "community.yaml"
    path.write_text(COMMUNITY_YAML.replace("power_kw: 2.0", "power_kw: -2.0"), encoding="utf-8")
    cfg = load_config(path)
    with pytest.raises(ConfigError, match=r"households\[0\]\.devices\[1\]\.power_kw"):
        devices_for(cfg)


def test_program_fixture_is_valid() -> None:
    wm = parse_device(_wm(), "d")
    assert isinstance(wm, ProgramDevice)
    assert wm.needs_binaries


def test_min_power_and_min_run_are_off_by_default() -> None:
    ev = parse_device(_ev(), "d")
    assert isinstance(ev, ModulatingDevice)
    assert ev.min_power_kw is None
    assert ev.min_run_min is None


# ---------------------------------------------------------------- validation


def test_energy_not_reachable_in_window_is_config_error() -> None:
    # 2 h window at 3 kW -> at most 6 kWh
    raw = _ev(
        power_kw=3.0,
        energy_kwh_per_day=6.5,
        window={"start": "10:00", "end": "12:00"},
        baseline_start="10:00",
    )
    with pytest.raises(ConfigError, match=r"d\.energy_kwh_per_day: .*nicht erreichbar"):
        parse_device(raw, "d")


def test_energy_exactly_reachable_is_accepted() -> None:
    raw = _ev(
        power_kw=3.0,
        energy_kwh_per_day=6.0,
        window={"start": "10:00", "end": "12:00"},
        baseline_start="10:00",
    )
    assert parse_device(raw, "d").energy_kwh_per_day == 6.0


def test_energy_not_reachable_from_baseline_start_is_config_error() -> None:
    # whole window allows 6 kWh, but starting at 11:00 only 3 kWh remain
    raw = _ev(
        power_kw=3.0,
        energy_kwh_per_day=5.0,
        window={"start": "10:00", "end": "12:00"},
        baseline_start="11:00",
    )
    with pytest.raises(ConfigError, match=r"d\.baseline_start: .*nur 3 kWh"):
        parse_device(raw, "d")


def test_min_power_above_power_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"d\.min_power_kw: Mindestleistung 12 kW"):
        parse_device(_ev(min_power_kw=12.0), "d")


def test_min_power_equal_to_power_is_accepted() -> None:
    device = parse_device(_ev(min_power_kw=11.0), "d")
    assert isinstance(device, ModulatingDevice)
    assert device.min_power_kw == 11.0


def test_baseline_start_outside_window_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"d\.baseline_start: 12:00 liegt nicht im Fenster"):
        parse_device(_ev(baseline_start="12:00"), "d")


def test_baseline_start_at_window_end_is_outside() -> None:
    with pytest.raises(ConfigError, match="liegt nicht im Fenster"):
        parse_device(_wm(baseline_start="20:00"), "d")


def test_program_baseline_start_outside_window_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"d\.baseline_start: 07:00 liegt nicht im Fenster"):
        parse_device(_wm(baseline_start="07:00"), "d")


def test_program_running_past_window_end_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"d\.baseline_start: .*endet nach dem Fensterende"):
        parse_device(_wm(baseline_start="19:00", duration_min=120), "d")


def test_program_longer_than_window_is_config_error() -> None:
    raw = _wm(window={"start": "08:00", "end": "09:00"}, baseline_start="08:00")
    with pytest.raises(ConfigError, match=r"d\.duration_min: 90 min passen nicht"):
        parse_device(raw, "d")


def test_min_run_longer_than_window_is_config_error() -> None:
    raw = _ev(
        window={"start": "10:00", "end": "16:00"},
        baseline_start="10:00",
        min_run_min=420,
    )
    with pytest.raises(ConfigError, match=r"d\.min_run_min: 420 min"):
        parse_device(raw, "d")


@pytest.mark.parametrize(
    ("changes", "pattern"),
    [
        ({"kind": "battery"}, r"d\.kind: unbekannter Wert"),
        ({"power_kw": 0.0}, r"d\.power_kw: muss größer als 0"),
        ({"energy_kwh_per_day": 0}, r"d\.energy_kwh_per_day: muss größer als 0"),
        ({"min_power_kw": 0.0}, r"d\.min_power_kw: muss größer als 0"),
        ({"min_run_min": 20}, r"d\.min_run_min: 20 min ist kein Vielfaches von 15"),
        ({"min_run_min": 1.5}, r"d\.min_run_min: ganze Zahl erwartet"),
        ({"duration_min": 90}, r"d: unbekannte Schlüssel duration_min"),
        ({"colour": "red"}, r"d: unbekannte Schlüssel colour"),
        ({"baseline_start": 1080}, r"d\.baseline_start: nicht-leerer Text erwartet"),
        ({"baseline_start": "18:10"}, r"d\.baseline_start: 18:10 liegt nicht auf dem 15-min"),
        ({"baseline_start": "6pm"}, r'd\.baseline_start: Uhrzeit als Text "HH:MM"'),
        ({"baseline_start": "24:00"}, r"d\.baseline_start: ungültige Uhrzeit"),
        ({"window": {"start": "18:00"}}, r"d\.window\.end: Pflichtwert fehlt"),
        ({"window": {"start": "18:00", "end": "18:00"}}, r"d\.window: Beginn und Ende sind"),
        (
            {"window": {"start": "18:00", "end": "07:00", "days": 1}},
            r"d\.window: unbekannte Schlüssel days",
        ),
        ({"id": ""}, r"d\.id: nicht-leerer Text"),
    ],
)
def test_invalid_modulating_fields(changes: dict[str, Any], pattern: str) -> None:
    with pytest.raises(ConfigError, match=pattern):
        parse_device(_ev(**changes), "d")


@pytest.mark.parametrize(
    ("changes", "pattern"),
    [
        ({"duration_min": 100}, r"d\.duration_min: 100 min ist kein Vielfaches"),
        ({"duration_min": 0}, r"d\.duration_min: darf nicht kleiner als 15"),
        ({"energy_kwh_per_day": 3.0}, r"d: unbekannte Schlüssel energy_kwh_per_day"),
        ({"min_power_kw": 1.0}, r"d: unbekannte Schlüssel min_power_kw"),
    ],
)
def test_invalid_program_fields(changes: dict[str, Any], pattern: str) -> None:
    with pytest.raises(ConfigError, match=pattern):
        parse_device(_wm(**changes), "d")


def test_duplicate_device_ids_in_household_are_rejected() -> None:
    with pytest.raises(ConfigError, match=r"devices\[1\]\.id: Geräte-ID 'ev' ist doppelt"):
        parse_devices([_ev(), _ev()])


def test_same_device_id_in_different_households_is_fine(tmp_path: Path) -> None:
    path = tmp_path / "community.yaml"
    path.write_text(COMMUNITY_YAML.replace("id: wallbox", "id: ev"), encoding="utf-8")
    devices = devices_for(load_config(path))
    assert devices["hh01"][0].id == devices["hh02"][0].id == "ev"


# ---------------------------------------------------------------- time windows


def test_window_18_to_07_crosses_midnight() -> None:
    window = TimeWindow(time(18, 0), time(7, 0))
    assert window.crosses_midnight
    assert window.duration == timedelta(hours=13)
    assert window.contains(time(18, 0))
    assert window.contains(time(23, 45))
    assert window.contains(time(0, 0))
    assert window.contains(time(6, 45))
    assert not window.contains(time(7, 0))
    assert not window.contains(time(12, 0))
    assert not window.contains(time(17, 45))
    assert window.remaining_after(time(23, 0)) == timedelta(hours=8)


def test_window_10_to_16_does_not_cross_midnight() -> None:
    window = TimeWindow(time(10, 0), time(16, 0))
    assert not window.crosses_midnight
    assert window.duration == timedelta(hours=6)
    assert window.contains(time(10, 0))
    assert not window.contains(time(16, 0))
    assert not window.contains(time(9, 45))


def test_window_ending_at_midnight() -> None:
    window = TimeWindow(time(20, 0), time(0, 0))
    assert window.crosses_midnight
    assert window.duration == timedelta(hours=4)
    assert window.contains(time(23, 45))
    assert not window.contains(time(0, 0))


def test_parsed_overnight_window_allows_energy_over_midnight() -> None:
    # 13 h at 11 kW = 143 kWh; the 18:00-start must not be read as a 0-h or negative window
    device = parse_device(_ev(energy_kwh_per_day=143.0), "d")
    assert device.window.crosses_midnight
    assert device.window.duration == timedelta(hours=13)


def test_empty_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="nicht gleich"):
        TimeWindow(time(8, 0), time(8, 0))


def test_remaining_after_outside_window_raises() -> None:
    with pytest.raises(ValueError, match="nicht im Fenster"):
        TimeWindow(time(10, 0), time(16, 0)).remaining_after(time(17, 0))
