"""Unit tests for the baseline schedule of flexible loads (F4-02)."""

from dataclasses import replace
from datetime import date, time

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer import analysis, devices
from eeg_optimizer.community import build_inputs
from eeg_optimizer.config import load_config
from eeg_optimizer.config_store import BUILTIN_CONFIG_DIR
from eeg_optimizer.devices import (
    BaselineScheduleError,
    ModulatingDevice,
    ProgramDevice,
    TimeWindow,
    baseline_loads,
    baseline_schedule,
    tasks,
)
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import KW, TIMEZONE, FrameValidationError, KWh

OFFLINE_CONFIG = BUILTIN_CONFIG_DIR / "example_community_offline.yaml"


def _days(start: str, n: int) -> pd.DatetimeIndex:
    """Local days ``start`` .. ``start + n - 1`` (92/100 intervals on DST days)."""
    first = pd.Timestamp(start)
    end = first + pd.Timedelta(days=n)
    return pd.date_range(
        first.tz_localize(TIMEZONE), end.tz_localize(TIMEZONE), freq="15min", inclusive="left"
    )


def _ev(
    energy: float = 8.0,
    start: str = "18:00",
    end: str = "07:00",
    baseline: str = "18:00",
    power: float = 11.0,
) -> ModulatingDevice:
    return ModulatingDevice(
        id="ev",
        power_kw=KW(power),
        energy_kwh_per_day=KWh(energy),
        window=TimeWindow(time.fromisoformat(start), time.fromisoformat(end)),
        baseline_start=time.fromisoformat(baseline),
    )


def _program(
    duration_min: int = 90,
    start: str = "08:00",
    end: str = "22:00",
    baseline: str = "19:00",
    power: float = 2.0,
) -> ProgramDevice:
    return ProgramDevice(
        id="wm",
        power_kw=KW(power),
        duration_min=duration_min,
        window=TimeWindow(time.fromisoformat(start), time.fromisoformat(end)),
        baseline_start=time.fromisoformat(baseline),
    )


def _contiguous(index: pd.Index) -> bool:
    """Consecutive real 15-min intervals (no gap, e.g. across a DST switch)."""
    steps = pd.DatetimeIndex(index[1:]) - pd.DatetimeIndex(index[:-1])
    return bool((steps == pd.Timedelta(minutes=15)).all())


def _at(series: pd.Series, stamp: str) -> float:
    return float(series.loc[pd.Timestamp(stamp, tz=TIMEZONE)])


def test_modulating_runs_full_power_then_rest() -> None:
    index = _days("2023-06-01", 3)
    schedule = baseline_schedule(_ev(), index)

    assert schedule.name == "device_kwh"
    assert schedule.index.equals(index)
    # 11 kW -> 2.75 kWh per interval; 8 kWh = 2 full intervals + 2.5 kWh
    assert _at(schedule, "2023-06-01 18:00") == pytest.approx(2.75)
    assert _at(schedule, "2023-06-01 18:15") == pytest.approx(2.75)
    assert _at(schedule, "2023-06-01 18:30") == pytest.approx(2.5)
    assert _at(schedule, "2023-06-01 18:45") == 0.0
    assert _at(schedule, "2023-06-02 18:00") == pytest.approx(2.75)
    # the task of 06-03 ends 06-04 07:00, outside the index -> not scheduled
    assert _at(schedule, "2023-06-03 18:00") == 0.0
    assert schedule.sum() == pytest.approx(16.0)
    assert (schedule >= 0).all()


def test_baseline_start_after_midnight_belongs_to_window_day() -> None:
    index = _days("2023-06-01", 2)
    schedule = baseline_schedule(_ev(energy=2.75, baseline="01:00"), index)

    # task of 06-01 (window 18:00 to 07:00) runs at 06-02 01:00; task of 05-31 is outside
    assert _at(schedule, "2023-06-02 01:00") == pytest.approx(2.75)
    assert _at(schedule, "2023-06-01 01:00") == 0.0
    assert schedule.sum() == pytest.approx(2.75)


def test_program_runs_fixed_duration() -> None:
    index = _days("2023-06-01", 1)
    schedule = baseline_schedule(_program(), index)

    running = schedule[schedule > 0]
    assert len(running) == 6
    assert np.allclose(running.to_numpy(), 0.5)
    assert running.index[0] == pd.Timestamp("2023-06-01 19:00", tz=TIMEZONE)
    assert running.index[-1] == pd.Timestamp("2023-06-01 20:15", tz=TIMEZONE)
    assert schedule.sum() == pytest.approx(3.0)


def test_tasks_lists_windows_inside_index() -> None:
    index = _days("2023-06-01", 3)
    found = tasks(_ev(), index)

    assert [t.day for t in found] == [date(2023, 6, 1), date(2023, 6, 2)]
    first = found[0]
    assert first.window_start == pd.Timestamp("2023-06-01 18:00", tz=TIMEZONE)
    assert first.window_end == pd.Timestamp("2023-06-02 07:00", tz=TIMEZONE)
    assert first.baseline_start == pd.Timestamp("2023-06-01 18:00", tz=TIMEZONE)
    assert first.energy_kwh == pytest.approx(8.0)
    # window ending at midnight belongs to the start day and fits the index exactly
    late = tasks(_program(duration_min=60, start="20:00", end="00:00", baseline="22:00"), index)
    assert [t.day for t in late] == [date(2023, 6, 1), date(2023, 6, 2), date(2023, 6, 3)]


def test_spring_dst_day_counts_real_intervals() -> None:
    index = _days("2023-03-26", 1)
    assert len(index) == 92
    # 00:00 to 05:00 wall clock = 4 real hours; 4 kW for 3.5 h = 14 kWh fits
    device = _ev(energy=14.0, start="00:00", end="05:00", baseline="00:00", power=4.0)
    schedule = baseline_schedule(device, index)

    running = schedule[schedule > 0]
    assert len(running) == 14
    assert schedule.sum() == pytest.approx(14.0)
    assert running.index[0] == pd.Timestamp("2023-03-26 00:00", tz=TIMEZONE)
    # 01:45 is followed directly by 03:00 (CEST)
    assert running.index[-1] == pd.Timestamp("2023-03-26 04:15", tz=TIMEZONE)
    assert _contiguous(running.index)


def test_spring_dst_nonexistent_baseline_start_moves_to_three() -> None:
    index = _days("2023-03-26", 1)
    device = _program(duration_min=60, start="01:00", end="06:00", baseline="02:30")
    schedule = baseline_schedule(device, index)

    running = schedule[schedule > 0]
    assert running.index[0] == pd.Timestamp("2023-03-26 03:00", tz=TIMEZONE)
    assert len(running) == 4
    assert schedule.sum() == pytest.approx(2.0)


def test_spring_dst_too_short_window_raises() -> None:
    index = _days("2023-03-26", 1)
    # 4 wall-clock hours, only 3 real hours on this day; 4 kW x 3.5 h does not fit
    device = _ev(energy=14.0, start="00:00", end="04:00", baseline="00:00", power=4.0)
    with pytest.raises(BaselineScheduleError, match="2023-03-26"):
        baseline_schedule(device, index)


def test_autumn_dst_day_has_extra_hour() -> None:
    index = _days("2023-10-29", 1)
    assert len(index) == 100
    # 3 h from 01:30 run through both 02:xx hours and end 03:30 local, not 04:30
    device = _program(duration_min=180, start="00:00", end="06:00", baseline="01:30")
    schedule = baseline_schedule(device, index)

    running = schedule[schedule > 0]
    assert len(running) == 12
    assert schedule.sum() == pytest.approx(6.0)
    assert _contiguous(running.index)
    assert running.index[0] == pd.Timestamp("2023-10-28 23:30", tz="UTC")
    assert running.index[-1] == pd.Timestamp("2023-10-29 03:15", tz=TIMEZONE)


def test_autumn_dst_ambiguous_baseline_start_takes_first_occurrence() -> None:
    index = _days("2023-10-29", 1)
    device = _program(duration_min=30, start="01:00", end="06:00", baseline="02:30")
    running = baseline_schedule(device, index)
    running = running[running > 0]
    # 02:30 summer time = 00:30 UTC
    assert running.index[0] == pd.Timestamp("2023-10-29 00:30", tz="UTC")
    assert len(running) == 2


def test_autumn_dst_modulating_daily_sum() -> None:
    index = _days("2023-10-28", 3)
    # 6 h at 4 kW from 23:00; the run crosses the doubled hour in the night 28. to 29.
    device = _ev(energy=24.0, start="22:00", end="06:00", baseline="23:00", power=4.0)
    schedule = baseline_schedule(device, index)
    found = tasks(device, index)
    assert [t.day for t in found] == [date(2023, 10, 28), date(2023, 10, 29)]
    for task in found:
        in_window = schedule[
            (schedule.index >= task.window_start) & (schedule.index < task.window_end)
        ]
        assert in_window.sum() == pytest.approx(24.0)
        assert in_window.max() <= 1.0 + 1e-12


def test_full_year_total() -> None:
    index = year_index(2023)
    schedule = baseline_schedule(_ev(), index)
    # 365 window starts, the one on 31.12. ends in 2024 -> 364 tasks
    assert len(tasks(_ev(), index)) == 364
    assert schedule.sum() == pytest.approx(364 * 8.0)
    assert baseline_schedule(_program(), index).sum() == pytest.approx(365 * 3.0)


def test_rejects_index_not_in_internal_format() -> None:
    naive = pd.date_range("2023-06-01", periods=96, freq="15min")
    with pytest.raises(FrameValidationError):
        baseline_schedule(_ev(), naive)


def test_baseline_loads_adds_devices() -> None:
    index = _days("2023-06-01", 2)
    loads = {
        "a": pd.DataFrame({"load_kwh": 0.1}, index=index),
        "b": pd.DataFrame({"load_kwh": 0.2}, index=index),
    }
    result = baseline_loads(loads, {"a": (_ev(), _program())})

    assert list(result) == ["a", "b"]
    expected_a = 0.1 + baseline_schedule(_ev(), index) + baseline_schedule(_program(), index)
    assert np.allclose(result["a"]["load_kwh"].to_numpy(), expected_a.to_numpy())
    assert np.allclose(result["b"]["load_kwh"].to_numpy(), 0.2)
    assert list(result["a"].columns) == ["load_kwh"]
    # the input is not changed
    assert np.allclose(loads["a"]["load_kwh"].to_numpy(), 0.1)


def test_baseline_loads_rejects_unknown_household() -> None:
    index = _days("2023-06-01", 1)
    loads = {"a": pd.DataFrame({"load_kwh": 0.1}, index=index)}
    with pytest.raises(BaselineScheduleError, match="unbekannte Haushalte: x"):
        baseline_loads(loads, {"x": (_ev(),)})


def test_baseline_loads_rejects_wrong_column() -> None:
    index = _days("2023-06-01", 1)
    loads = {"a": pd.DataFrame({"pv_kwh": 0.1}, index=index)}
    with pytest.raises(BaselineScheduleError, match="load_kwh"):
        baseline_loads(loads, {})


def test_devices_flow_into_baseline_balance() -> None:
    example = load_config(OFFLINE_CONFIG)
    # start from the example without its devices (F4-03), then add exactly one EV
    config = replace(
        example, households=tuple(replace(hh, devices=()) for hh in example.households)
    )
    inputs = build_inputs(config)
    first = config.households[0]
    ev_raw = {
        "id": "ev",
        "kind": "modulating",
        "power_kw": 11.0,
        "energy_kwh_per_day": 8.0,
        "window": {"start": "18:00", "end": "07:00"},
        "baseline_start": "18:00",
    }
    with_ev = replace(
        config, households=(replace(first, devices=(ev_raw,)), *config.households[1:])
    )

    plain = analysis.analyze(config, inputs)
    shifted = analysis.analyze(with_ev, inputs)

    ev_kwh = baseline_schedule(devices.devices_for(with_ev)[first.id][0], inputs.index)
    load_diff = shifted.balance["load_kwh"] - plain.balance["load_kwh"]
    assert np.allclose(load_diff.to_numpy(), ev_kwh.to_numpy())
    assert shifted.kpis.load_kwh == pytest.approx(plain.kpis.load_kwh + 364 * 8.0)
    # the EV charges in the evening, so most of it comes from the grid
    assert shifted.kpis.grid_import_kwh > plain.kpis.grid_import_kwh
