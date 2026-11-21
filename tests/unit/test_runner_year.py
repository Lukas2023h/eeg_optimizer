"""Runner F5-07 over a synthetic year with 3 households: INV-2 and INV-3 for the whole result."""

from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.config import TariffConfig
from eeg_optimizer.devices import Device, ModulatingDevice, ProgramDevice, TimeWindow, tasks
from eeg_optimizer.optimize.objectives import OptimizationInputs, baseline_device_schedule, evaluate
from eeg_optimizer.optimize.runner import SCORE_TOLERANCE, Schedule, optimize
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import INTERVAL, KW, KWh, kw_to_kwh

_TARIFFS = TariffConfig(
    eeg_type="local",
    energy_price=30.0,
    grid_fee_energy=6.0,
    levies_taxes=4.0,
    feed_in_tariff=7.0,
    eeg_internal_price=12.0,
    spot_markup=2.0,
    grid_fee_reduction=0.5,
)


def _modulating(
    device_id: str, power: float, energy: float, window: tuple[int, int], baseline: int
) -> Device:
    return ModulatingDevice(
        id=device_id,
        power_kw=KW(power),
        energy_kwh_per_day=KWh(energy),
        window=TimeWindow(time(window[0]), time(window[1])),
        baseline_start=time(baseline),
    )


def _program(device_id: str, power: float, window: tuple[int, int], baseline: int) -> Device:
    return ProgramDevice(
        id=device_id,
        power_kw=KW(power),
        duration_min=90,
        window=TimeWindow(time(window[0]), time(window[1])),
        baseline_start=time(baseline),
    )


DEVICES: dict[str, tuple[Device, ...]] = {
    "hh1": (_modulating("ev", 11.0, 8.0, (18, 7), 18), _program("washer", 1.0, (8, 20), 18)),
    "hh2": (
        _modulating("boiler", 2.0, 5.0, (22, 16), 22),
        _program("dishwasher", 1.2, (20, 16), 20),
    ),
    "hh3": (_modulating("heat_pump", 2.5, 8.0, (10, 18), 10),),
}


def _year_inputs() -> OptimizationInputs:
    index = year_index(2023)
    n = len(index)
    hour = np.asarray(index.hour + index.minute / 60.0)
    season = 0.6 + 0.4 * np.cos((np.asarray(index.day_of_year) - 172) / 365.0 * 2 * np.pi)
    pv = np.clip(np.sin((hour - 6.0) / 14.0 * np.pi), 0.0, None) * 2.0 * season
    rng = np.random.default_rng(7)
    loads = {
        hh: pd.DataFrame({"load_kwh": 0.05 + 0.1 * rng.random(n)}, index=index) for hh in DEVICES
    }
    spot = 8.0 + 6.0 * np.cos((hour - 13.0) / 24.0 * 2 * np.pi)
    co2 = 120.0 + 60.0 * np.cos((hour - 2.0) / 24.0 * 2 * np.pi)
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads=loads,
        prices=pd.DataFrame({"spot_ct_per_kwh": spot}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": co2}, index=index),
        tariffs=_TARIFFS,
        devices=DEVICES,
    )


@pytest.fixture(scope="module")
def year() -> tuple[OptimizationInputs, Schedule]:
    inputs = _year_inputs()
    return inputs, optimize(inputs, "max_shared", "milp")


def test_year_has_a_block_per_day_and_no_fallback(
    year: tuple[OptimizationInputs, Schedule],
) -> None:
    _, schedule = year
    # EV and dishwasher windows of 31 Dec end in 2024, but the other devices still start a block.
    assert schedule.block_days[0] == date(2023, 1, 1)
    assert len(schedule.block_days) == 365
    assert schedule.fallback_blocks == ()
    assert schedule.fallback_ratio == 0.0
    assert schedule.runtime_s > 0.0


def test_year_result_keeps_inv2_for_every_task(year: tuple[OptimizationInputs, Schedule]) -> None:
    inputs, schedule = year
    index = inputs.index
    for household_id, devices in DEVICES.items():
        for device in devices:
            values = schedule.devices[household_id][device.id].to_numpy()
            cap = float(kw_to_kwh(KW(device.power_kw), INTERVAL))
            assert values.min() >= 0.0
            assert values.max() <= cap + 1e-9
            inside = np.zeros(len(index), dtype=bool)
            device_tasks = tasks(device, index)
            assert len(device_tasks) >= 364
            for task in device_tasks:
                window = (index >= task.window_start) & (index < task.window_end)
                inside |= window
                part = values[window]
                assert part.sum() == pytest.approx(task.energy_kwh, abs=1e-6)
                if isinstance(device, ProgramDevice):
                    running = np.flatnonzero(part > 0.0)
                    assert len(running) == device.duration_min // 15
                    assert running[-1] - running[0] == len(running) - 1
            assert np.abs(values[~inside]).max(initial=0.0) == 0.0


def test_year_result_keeps_inv3(year: tuple[OptimizationInputs, Schedule]) -> None:
    inputs, schedule = year
    baseline = evaluate(baseline_device_schedule(inputs), inputs, "max_shared")
    optimized = evaluate(schedule.devices, inputs, "max_shared")
    assert schedule.baseline_score == pytest.approx(baseline, rel=1e-12)
    assert schedule.score == pytest.approx(optimized, rel=1e-12)
    assert optimized >= baseline - len(schedule.block_days) * SCORE_TOLERANCE
    # Sanity: shifting into the PV hours shares clearly more than the baseline.
    assert optimized > baseline + 1000.0
