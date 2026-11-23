"""Unit tests for the per-day views of the comparison (F6-02): days, best day, day profile."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.allocation import DynamicProportional
from eeg_optimizer.balance import LOAD_COLUMN, PV_COLUMN, SHARED_COLUMN
from eeg_optimizer.compare import (
    ADDITIONAL_SHARED_COLUMN,
    DAY_PROFILE_COLUMNS,
    LOAD_BASELINE_POWER_COLUMN,
    LOAD_OPTIMIZED_POWER_COLUMN,
    PV_POWER_COLUMN,
    Comparison,
    ComparisonInputError,
    additional_shared_by_day,
    best_day,
    compare_schedule,
    day_profile,
    days,
)
from eeg_optimizer.optimize.objectives import OptimizationInputs, baseline_device_schedule
from eeg_optimizer.optimize.runner import optimize
from eeg_optimizer.units import TIMEZONE, kw_to_kwh
from tests.unit.test_compare import _inputs


def _shifted(inputs: OptimizationInputs, start: str, n_days: int) -> OptimizationInputs:
    """Same synthetic inputs on another (local) calendar range, e.g. across a DST switch."""
    index = pd.date_range(
        start,
        f"{pd.Timestamp(start) + pd.Timedelta(days=n_days):%Y-%m-%d}",
        freq="15min",
        tz=TIMEZONE,
        inclusive="left",
    )
    n = len(index)

    def take(frame: pd.DataFrame) -> pd.DataFrame:
        values = np.resize(frame.to_numpy(dtype=float), (n, frame.shape[1]))
        return pd.DataFrame(values, index=index, columns=frame.columns)

    return replace(
        inputs,
        pv=take(inputs.pv),
        loads={hh: take(frame) for hh, frame in inputs.loads.items()},
        prices=take(inputs.prices),
        co2=take(inputs.co2),
    )


@pytest.fixture(scope="module")
def comparison() -> Comparison:
    inputs = _inputs(days=3)
    schedule = optimize(inputs, "max_shared", "milp")
    assert schedule.fallback_ratio == 0.0
    return compare_schedule(inputs, schedule.devices, DynamicProportional())


def test_days_are_the_local_calendar_days(comparison: Comparison) -> None:
    assert days(comparison) == [date(2023, 6, 1), date(2023, 6, 2), date(2023, 6, 3)]


def test_additional_shared_by_day_sums_to_the_total(comparison: Comparison) -> None:
    by_day = additional_shared_by_day(comparison)

    assert list(by_day.index) == days(comparison)
    assert by_day.name == ADDITIONAL_SHARED_COLUMN
    assert by_day.sum() == pytest.approx(float(comparison.additional_shared_kwh), abs=1e-9)
    on_day = pd.DatetimeIndex(comparison.baseline.balance.index).date == date(2023, 6, 2)
    optimized = comparison.optimized.balance[SHARED_COLUMN].to_numpy(dtype=float)[on_day]
    baseline = comparison.baseline.balance[SHARED_COLUMN].to_numpy(dtype=float)[on_day]
    expected = float(optimized.sum() - baseline.sum())
    assert by_day[date(2023, 6, 2)] == pytest.approx(expected, abs=1e-9)


def test_best_day_has_the_largest_gain(comparison: Comparison) -> None:
    by_day = additional_shared_by_day(comparison)

    assert by_day[best_day(comparison)] == by_day.max()
    assert by_day.max() > 0.0


def test_best_day_takes_the_earliest_day_on_a_tie() -> None:
    inputs = _inputs(days=3)
    same = compare_schedule(inputs, baseline_device_schedule(inputs), DynamicProportional())

    assert (additional_shared_by_day(same) == 0.0).all()
    assert best_day(same) == date(2023, 6, 1)


def test_day_profile_converts_the_day_of_both_balances_to_kw(comparison: Comparison) -> None:
    profile = day_profile(comparison, date(2023, 6, 2))

    assert list(profile.columns) == list(DAY_PROFILE_COLUMNS)
    assert len(profile) == 96
    assert set(pd.DatetimeIndex(profile.index).date) == {date(2023, 6, 2)}
    assert str(pd.DatetimeIndex(profile.index).tz) == TIMEZONE
    base = comparison.baseline.balance.loc["2023-06-02"]
    opt = comparison.optimized.balance.loc["2023-06-02"]
    to_kwh = np.vectorize(kw_to_kwh)
    np.testing.assert_allclose(to_kwh(profile[PV_POWER_COLUMN]), base[PV_COLUMN])
    np.testing.assert_allclose(to_kwh(profile[LOAD_BASELINE_POWER_COLUMN]), base[LOAD_COLUMN])
    np.testing.assert_allclose(to_kwh(profile[LOAD_OPTIMIZED_POWER_COLUMN]), opt[LOAD_COLUMN])
    # the optimization moved device energy within the day block, so the loads differ
    assert not np.allclose(
        profile[LOAD_BASELINE_POWER_COLUMN], profile[LOAD_OPTIMIZED_POWER_COLUMN]
    )


def test_day_profile_rejects_a_day_outside_the_comparison(comparison: Comparison) -> None:
    with pytest.raises(ComparisonInputError, match="2023-06-04"):
        day_profile(comparison, date(2023, 6, 4))


@pytest.mark.parametrize(("day", "rows"), [(date(2023, 3, 26), 92), (date(2023, 10, 29), 100)])
def test_day_profile_on_dst_days_has_all_local_intervals(day: date, rows: int) -> None:
    inputs = _shifted(_inputs(days=3), f"{day - timedelta(days=1):%Y-%m-%d}", 3)
    result = compare_schedule(inputs, baseline_device_schedule(inputs), DynamicProportional())

    profile = day_profile(result, day)

    assert len(profile) == rows
    assert days(result)[1] == day
    assert additional_shared_by_day(result)[day] == 0.0
