"""F4-03: flexible devices in both example communities."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pandas as pd
import pytest

from eeg_optimizer.config import load_config
from eeg_optimizer.devices import (
    Device,
    ModulatingDevice,
    ProgramDevice,
    baseline_schedule,
    devices_for,
    tasks,
)
from eeg_optimizer.sources.cache import PROJECT_ROOT
from eeg_optimizer.units import TIMEZONE

CONFIG_DIR = PROJECT_ROOT / "config"
EXAMPLE = CONFIG_DIR / "example_community.yaml"
EXAMPLE_OFFLINE = CONFIG_DIR / "example_community_offline.yaml"
EXAMPLES = [EXAMPLE, EXAMPLE_OFFLINE]

# Device id -> expected kind; spec F4-03 lists exactly these five device types.
EXPECTED_KINDS: dict[str, type] = {
    "ev": ModulatingDevice,
    "boiler": ModulatingDevice,
    "heat_pump": ModulatingDevice,
    "washing_machine": ProgramDevice,
    "dishwasher": ProgramDevice,
}

YEAR_2023 = pd.date_range("2023-01-01", "2024-01-01", freq="15min", tz=TIMEZONE, inclusive="left")


def _all_devices(path: Path) -> list[Device]:
    return [d for devs in devices_for(load_config(path)).values() for d in devs]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_devices_load_without_errors(path: Path) -> None:
    by_household = devices_for(load_config(path))
    assert set(by_household) == {f"hh0{i}" for i in range(1, 6)}
    assert all(by_household.values()), "jeder Haushalt hat mindestens ein Gerät"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_device_type_is_present_with_the_right_kind(path: Path) -> None:
    found = _all_devices(path)
    assert {d.id for d in found} == set(EXPECTED_KINDS)
    for device in found:
        assert isinstance(device, EXPECTED_KINDS[device.id]), device.id


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_heat_pump_is_modulating_in_narrow_window(path: Path) -> None:
    heat_pumps = [d for d in _all_devices(path) if d.id == "heat_pump"]
    assert heat_pumps
    for hp in heat_pumps:
        assert isinstance(hp, ModulatingDevice)
        assert hp.kind == "modulating"
        assert (hp.window.start, hp.window.end) == (time(10, 0), time(16, 0))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_heat_pump_comment_points_to_simplification(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if "- id: heat_pump" in line]
    assert starts
    for i in starts:
        context = "\n".join(lines[max(i - 2, 0) : i + 1])
        assert "Vereinfachung" in context
        assert "spec §7.2" in context
        assert "10:00 bis 16:00" in context


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_min_power_is_off_by_default(path: Path) -> None:
    cfg = load_config(path)
    for hh in cfg.households:
        for raw in hh.devices:
            assert "min_power_kw" not in raw, f"{hh.id}/{raw['id']}"
    for device in _all_devices(path):
        if isinstance(device, ModulatingDevice):
            assert device.min_power_kw is None
            assert device.min_run_min is None


def test_online_and_offline_examples_have_the_same_devices() -> None:
    online, offline = load_config(EXAMPLE), load_config(EXAMPLE_OFFLINE)
    assert [hh.devices for hh in online.households] == [hh.devices for hh in offline.households]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_devices_have_a_baseline_for_all_of_2023(path: Path) -> None:
    """Includes both DST days (F4-02: windows over 02:00 need reserve)."""
    for device in _all_devices(path):
        schedule = baseline_schedule(device, YEAR_2023)
        n_tasks = len(tasks(device, YEAR_2023))
        assert n_tasks >= 364, device.id
        assert schedule.sum() == pytest.approx(n_tasks * device.energy_kwh_per_day), device.id
