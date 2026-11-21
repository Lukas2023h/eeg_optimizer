"""Property tests for the block (MI)LP: INV-2 (device constraints) and INV-3 (never worse).

F5-02: modulating devices only; F5-03: mixed with program devices (start binaries).
"""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from eeg_optimizer.config import TariffConfig
from eeg_optimizer.devices import Device, ProgramDevice, parse_device, tasks
from eeg_optimizer.optimize.milp import solve_block
from eeg_optimizer.optimize.objectives import (
    OptimizationInputs,
    baseline_device_schedule,
    evaluate,
)
from eeg_optimizer.units import INTERVAL, KW, TIMEZONE, kw_to_kwh

TOL = 1e-6
SLOTS_PER_DAY = 96
DST_DAYS = (date(2023, 3, 25), date(2023, 3, 26), date(2023, 10, 28), date(2023, 10, 29))

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

grid_times = st.integers(0, SLOTS_PER_DAY - 1).map(lambda i: time(i // 4, (i % 4) * 15))
first_days = st.one_of(
    st.sampled_from(DST_DAYS),
    st.integers(0, 360).map(lambda n: date(2023, 1, 1) + timedelta(days=n)),
)


def _index(first: date, n_days: int) -> pd.DatetimeIndex:
    start = pd.Timestamp(first).tz_localize(TIMEZONE)
    end = pd.Timestamp(first + timedelta(days=n_days)).tz_localize(TIMEZONE)
    return pd.date_range(start, end, freq="15min", inclusive="left")


@st.composite
def modulating_devices(draw: st.DrawFn, device_id: str) -> Device:
    """Random modulating devices without min options that pass the F4-01 validation."""
    start = draw(grid_times)
    window_slots = draw(st.integers(5, SLOTS_PER_DAY - 1))
    end_minutes = (start.hour * 60 + start.minute + window_slots * 15) % 1440
    end = time(end_minutes // 60, end_minutes % 60)
    offset = draw(st.integers(0, window_slots - 5))
    minutes = (start.hour * 60 + start.minute + offset * 15) % 1440
    baseline = time(minutes // 60, minutes % 60)
    power = draw(st.floats(0.5, 22.0))
    # At most (remaining - 4) slots of energy: the baseline still fits when the spring DST
    # day removes one hour from the window.
    usable_slots = window_slots - offset - 4
    reachable = usable_slots * kw_to_kwh(KW(power))
    raw: dict[str, Any] = {
        "id": device_id,
        "kind": "modulating",
        "power_kw": power,
        "energy_kwh_per_day": draw(st.floats(0.01, 1.0)) * reachable,
        "window": {"start": f"{start:%H:%M}", "end": f"{end:%H:%M}"},
        "baseline_start": f"{baseline:%H:%M}",
    }
    return parse_device(raw, device_id)


@st.composite
def program_devices(draw: st.DrawFn, device_id: str) -> Device:
    """Random program devices that pass the F4-01 validation and fit on the spring DST day."""
    start = draw(grid_times)
    window_slots = draw(st.integers(5, SLOTS_PER_DAY - 1))
    end_minutes = (start.hour * 60 + start.minute + window_slots * 15) % 1440
    end = time(end_minutes // 60, end_minutes % 60)
    offset = draw(st.integers(0, window_slots - 5))
    minutes = (start.hour * 60 + start.minute + offset * 15) % 1440
    baseline = time(minutes // 60, minutes % 60)
    run_slots = draw(st.integers(1, window_slots - offset - 4))
    raw: dict[str, Any] = {
        "id": device_id,
        "kind": "program",
        "power_kw": draw(st.floats(0.5, 5.0)),
        "duration_min": run_slots * 15,
        "window": {"start": f"{start:%H:%M}", "end": f"{end:%H:%M}"},
        "baseline_start": f"{baseline:%H:%M}",
    }
    return parse_device(raw, device_id)


@st.composite
def mixed_devices(draw: st.DrawFn, device_id: str) -> Device:
    """Program or modulating device with equal chance."""
    if draw(st.booleans()):
        return draw(program_devices(device_id))
    return draw(modulating_devices(device_id))


@st.composite
def block_inputs(draw: st.DrawFn, *, with_programs: bool = False) -> OptimizationInputs:
    first = draw(first_days)
    n_days = draw(st.integers(1, 2))
    index = _index(first, n_days)
    n = len(index)
    energy = st.floats(0.0, 3.0, allow_nan=False)
    pv = draw(st.lists(energy, min_size=n, max_size=n))
    n_households = draw(st.integers(1, 3))
    loads: dict[str, pd.DataFrame] = {}
    devices: dict[str, tuple[Device, ...]] = {}
    for h in range(n_households):
        household_id = f"hh{h}"
        level = draw(st.floats(0.0, 0.5))
        loads[household_id] = pd.DataFrame(
            {"load_kwh": level + 0.2 * np.sin(np.arange(n) / 7.0) ** 2}, index=index
        )
        n_devices = draw(st.integers(0, 2))
        kind = mixed_devices if with_programs else modulating_devices
        devices[household_id] = tuple(draw(kind(f"d{k}")) for k in range(n_devices))
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads=loads,
        prices=pd.DataFrame({"spot_ct_per_kwh": np.full(n, 10.0)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": np.full(n, 100.0)}, index=index),
        tariffs=_TARIFFS,
        devices=devices,
    )


_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@_SETTINGS
@given(block_inputs())
def test_inv2_energy_window_and_power_are_kept(inputs: OptimizationInputs) -> None:
    schedule = solve_block(inputs, "max_shared")

    index = inputs.index
    assert set(schedule) == set(inputs.devices)
    for household_id, devices in inputs.devices.items():
        assert set(schedule[household_id]) == {device.id for device in devices}
        for device in devices:
            series = schedule[household_id][device.id]
            assert series.index.equals(index)
            values = series.to_numpy()
            cap = kw_to_kwh(KW(device.power_kw), INTERVAL)
            assert values.min() >= 0.0
            assert values.max() <= cap + 1e-9
            inside = np.zeros(len(index), dtype=bool)
            device_tasks = tasks(device, index)
            event(
                "tasks per device: " + ("2+" if len(device_tasks) > 1 else str(len(device_tasks)))
            )
            for task in device_tasks:
                in_window = (index >= task.window_start) & (index < task.window_end)
                inside |= in_window
                assert abs(values[in_window].sum() - task.energy_kwh) <= TOL
            assert np.abs(values[~inside]).max(initial=0.0) <= TOL


@_SETTINGS
@given(block_inputs())
def test_inv3_never_worse_than_the_baseline(inputs: OptimizationInputs) -> None:
    schedule = solve_block(inputs, "max_shared")

    optimized = evaluate(schedule, inputs, "max_shared")
    baseline = evaluate(baseline_device_schedule(inputs), inputs, "max_shared")
    assert optimized >= baseline - TOL


@_SETTINGS
@given(block_inputs(with_programs=True))
def test_inv2_program_runs_once_in_one_piece_inside_the_window(
    inputs: OptimizationInputs,
) -> None:
    schedule = solve_block(inputs, "max_shared")

    index = inputs.index
    for household_id, devices in inputs.devices.items():
        for device in devices:
            values = schedule[household_id][device.id].to_numpy()
            cap = kw_to_kwh(KW(device.power_kw), INTERVAL)
            inside = np.zeros(len(index), dtype=bool)
            for task in tasks(device, index):
                in_window = (index >= task.window_start) & (index < task.window_end)
                inside |= in_window
                part = values[in_window]
                if isinstance(device, ProgramDevice):
                    event("program task")
                    # Exactly one start: one contiguous run of duration/15 intervals at cap.
                    running = np.flatnonzero(part > 0.0)
                    n_run = device.duration_min // 15
                    assert len(running) == n_run
                    assert running[-1] - running[0] == n_run - 1
                    assert np.all(part[running] == cap)
                assert abs(part.sum() - task.energy_kwh) <= TOL
            assert values.min() >= 0.0
            assert values.max() <= cap + 1e-9
            assert np.abs(values[~inside]).max(initial=0.0) <= TOL


@_SETTINGS
@given(block_inputs(with_programs=True))
def test_inv3_mixed_devices_never_worse_than_the_baseline(inputs: OptimizationInputs) -> None:
    schedule = solve_block(inputs, "max_shared")

    optimized = evaluate(schedule, inputs, "max_shared")
    baseline = evaluate(baseline_device_schedule(inputs), inputs, "max_shared")
    assert optimized >= baseline - TOL
