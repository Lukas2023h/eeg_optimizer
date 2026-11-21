"""Property tests for the runner F5-07: INV-2 and INV-3 over several day blocks.

Random devices (modulating and program, windows across midnight included), 2 to 4 days around
the DST days and random signals, all objectives and both solvers. Blocks overlap (windows up to
24 h after a start on day d), so the fixed load of neighbouring blocks is exercised.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from eeg_optimizer.devices import Device, ProgramDevice, tasks
from eeg_optimizer.optimize.objectives import (
    OBJECTIVES,
    Objective,
    OptimizationInputs,
    baseline_device_schedule,
    evaluate,
)
from eeg_optimizer.optimize.runner import SCORE_TOLERANCE, optimize
from eeg_optimizer.units import INTERVAL, KW, TIMEZONE, kw_to_kwh
from tests.property.test_milp import _TARIFFS, DST_DAYS, mixed_devices

first_days = st.one_of(
    st.sampled_from([day - timedelta(days=1) for day in DST_DAYS]),
    st.integers(0, 360).map(lambda n: date(2023, 1, 1) + timedelta(days=n)),
)


def _index(first: date, n_days: int) -> pd.DatetimeIndex:
    start = pd.Timestamp(first).tz_localize(TIMEZONE)
    end = pd.Timestamp(first + timedelta(days=n_days)).tz_localize(TIMEZONE)
    return pd.date_range(start, end, freq="15min", inclusive="left")


@st.composite
def period_inputs(draw: st.DrawFn) -> OptimizationInputs:
    index = _index(draw(first_days), draw(st.integers(2, 4)))
    n = len(index)
    hours = (n + 3) // 4
    pv = np.repeat(draw(st.lists(st.floats(0.0, 3.0), min_size=hours, max_size=hours)), 4)[:n]
    spot = np.repeat(draw(st.lists(st.floats(-60.0, 60.0), min_size=hours, max_size=hours)), 4)
    co2 = np.repeat(draw(st.lists(st.floats(0.0, 300.0), min_size=hours, max_size=hours)), 4)
    loads: dict[str, pd.DataFrame] = {}
    devices: dict[str, tuple[Device, ...]] = {}
    for h in range(draw(st.integers(1, 3))):
        household_id = f"hh{h}"
        level = draw(st.floats(0.0, 0.5))
        loads[household_id] = pd.DataFrame(
            {"load_kwh": level + 0.2 * np.sin(np.arange(n) / 7.0) ** 2}, index=index
        )
        n_devices = draw(st.integers(0, 2))
        devices[household_id] = tuple(draw(mixed_devices(f"d{k}")) for k in range(n_devices))
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads=loads,
        prices=pd.DataFrame({"spot_ct_per_kwh": spot[:n]}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": co2[:n]}, index=index),
        tariffs=_TARIFFS,
        devices=devices,
    )


_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@pytest.mark.parametrize("solver", ["milp", "greedy"])
@pytest.mark.parametrize("objective", OBJECTIVES)
@_SETTINGS
@given(inputs=period_inputs())
def test_runner_keeps_inv2_and_inv3(
    objective: Objective, solver: str, inputs: OptimizationInputs
) -> None:
    schedule = optimize(inputs, objective, solver)  # type: ignore[arg-type]

    index = inputs.index
    for household_id, devices in inputs.devices.items():
        assert set(schedule.devices[household_id]) == {device.id for device in devices}
        for device in devices:
            values = schedule.devices[household_id][device.id].to_numpy()
            cap = float(kw_to_kwh(KW(device.power_kw), INTERVAL))
            assert values.min() >= 0.0
            assert values.max() <= cap + 1e-9
            inside = np.zeros(len(index), dtype=bool)
            for task in tasks(device, index):
                window = (index >= task.window_start) & (index < task.window_end)
                inside |= window
                part = values[window]
                assert abs(part.sum() - task.energy_kwh) <= 1e-6
                if isinstance(device, ProgramDevice):
                    running = np.flatnonzero(part > 0.0)
                    assert len(running) == device.duration_min // 15
                    assert running[-1] - running[0] == len(running) - 1
            assert np.abs(values[~inside]).max(initial=0.0) == 0.0

    baseline = evaluate(baseline_device_schedule(inputs), inputs, objective)
    optimized = evaluate(schedule.devices, inputs, objective)
    if schedule.fallback_blocks:
        event("fallback")
    if optimized > baseline + 1e-6:
        event("strictly better than the baseline")
    assert optimized >= baseline - len(schedule.block_days) * SCORE_TOLERANCE
