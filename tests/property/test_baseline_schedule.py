"""Property tests for the baseline schedule of flexible loads (F4-02, INV-2 for the baseline)."""

from __future__ import annotations

import itertools
import math
from datetime import date, time, timedelta
from typing import Any

import numpy as np
import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from eeg_optimizer.devices import (
    BaselineScheduleError,
    Device,
    ProgramDevice,
    TimeWindow,
    baseline_schedule,
    parse_device,
    tasks,
)
from eeg_optimizer.units import INTERVAL, KW, TIMEZONE, kw_to_kwh

SLOTS_PER_DAY = 96
TOL = 1e-6
DST_DAYS = (date(2023, 3, 25), date(2023, 3, 26), date(2023, 10, 28), date(2023, 10, 29))

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
def devices(draw: st.DrawFn) -> Device:
    """Random devices that pass the F4-01 validation (via ``parse_device``)."""
    start = draw(grid_times)
    end = draw(grid_times.filter(lambda t: t != start))
    window = TimeWindow(start, end)
    slots = window.duration // INTERVAL
    offset = draw(st.integers(0, slots - 1))
    minutes = (start.hour * 60 + start.minute + offset * 15) % 1440
    baseline = time(minutes // 60, minutes % 60)
    remaining_slots = window.remaining_after(baseline) // INTERVAL
    power = draw(st.floats(0.5, 22.0))
    raw: dict[str, Any] = {
        "id": "d",
        "power_kw": power,
        "window": {"start": f"{start:%H:%M}", "end": f"{end:%H:%M}"},
        "baseline_start": f"{baseline:%H:%M}",
    }
    if draw(st.booleans()):
        raw["kind"] = "program"
        raw["duration_min"] = 15 * draw(st.integers(1, remaining_slots))
    else:
        raw["kind"] = "modulating"
        reachable = remaining_slots * kw_to_kwh(KW(power))
        raw["energy_kwh_per_day"] = draw(st.floats(0.01, 1.0)) * reachable
    return parse_device(raw, "d")


def _needed_intervals(device: Device) -> int:
    if isinstance(device, ProgramDevice):
        return device.duration_min // 15
    return math.ceil(device.energy_kwh_per_day / kw_to_kwh(device.power_kw) - 1e-9)


@settings(max_examples=150, deadline=None)
@given(devices(), first_days, st.integers(1, 4))
def test_baseline_meets_energy_power_and_window(device: Device, first: date, n_days: int) -> None:
    index = _index(first, n_days)
    found = tasks(device, index)
    try:
        schedule = baseline_schedule(device, index)
    except BaselineScheduleError:
        # only legitimate if a window really is too short in real time (spring DST)
        needed = _needed_intervals(device) * INTERVAL
        assert any(t.window_end - t.baseline_start < needed for t in found)
        assert date(2023, 3, 26) in {t.day for t in found} | {
            t.day + timedelta(days=1) for t in found
        }
        return

    values = schedule.to_numpy(dtype=float)
    assert schedule.name == "device_kwh"
    assert schedule.index.equals(index)
    assert (values >= 0).all()
    # power limit per interval
    assert (values <= kw_to_kwh(device.power_kw) + 1e-9).all()

    inside = np.zeros(len(index), dtype=bool)
    for task in found:
        in_window = (index >= task.window_start) & (index < task.window_end)
        inside |= in_window
        # daily energy met exactly within the task's window
        assert abs(values[in_window].sum() - device.energy_kwh_per_day) < TOL
        # the run starts at the baseline start
        assert values[index.get_loc(task.baseline_start)] > 0
    # load only inside windows
    assert (values[~inside] == 0).all()
    assert abs(values.sum() - len(found) * device.energy_kwh_per_day) < TOL * max(1, len(found))
    if isinstance(device, ProgramDevice):
        running = values[values > 0]
        assert len(running) == len(found) * (device.duration_min // 15)


@settings(max_examples=60, deadline=None)
@given(devices(), first_days, st.integers(1, 4))
def test_tasks_are_ordered_disjoint_and_inside_index(
    device: Device, first: date, n_days: int
) -> None:
    index = _index(first, n_days)
    found = tasks(device, index)
    end = index[-1] + INTERVAL
    for task in found:
        # on 2023-03-26 times in 02:00..02:59 move to 03:00, so a window can shrink to 0
        assert index[0] <= task.window_start <= task.window_end <= end
        assert task.window_start <= task.baseline_start <= task.window_end
        if task.day != date(2023, 3, 26) and task.day + timedelta(days=1) != date(2023, 3, 26):
            assert task.window_start < task.window_end
            assert task.baseline_start < task.window_end
    for earlier, later in itertools.pairwise(found):
        assert earlier.day < later.day
        assert earlier.window_end <= later.window_start
    # every day whose window lies fully in a plain (non-DST) index yields a task
    assert len(found) >= n_days - 1
