"""Unit tests for the internal 15-minute grid and resampling (F1-02)."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.timeseries import (
    EXTENSIVE_SUFFIXES,
    INTENSIVE_SUFFIXES,
    ResampleError,
    aggregate_energy,
    hourly_energy_to_15min,
    rate_to_15min,
    year_index,
)
from eeg_optimizer.units import (
    ALLOWED_SUFFIXES,
    TIMEZONE,
    FrameValidationError,
    validate_frame,
    w_to_kw,
)


def _hourly(columns: dict[str, list[float]], start: str = "2023-06-01") -> pd.DataFrame:
    periods = len(next(iter(columns.values())))
    index = pd.date_range(start, periods=periods, freq="h", tz=TIMEZONE)
    return pd.DataFrame(columns, index=index)


# --- year_index -------------------------------------------------------------------------


def test_year_index_2023_has_35040_intervals() -> None:
    index = year_index(2023)
    assert len(index) == 35040
    assert str(index.tz) == TIMEZONE
    assert index[0] == pd.Timestamp("2023-01-01 00:00", tz=TIMEZONE)
    assert index[-1] == pd.Timestamp("2023-12-31 23:45", tz=TIMEZONE)


def test_year_index_dst_days_2023() -> None:
    per_day = pd.Series(1, index=year_index(2023)).groupby(year_index(2023).date).size()
    assert per_day[pd.Timestamp("2023-03-26").date()] == 92
    assert per_day[pd.Timestamp("2023-10-29").date()] == 100
    assert per_day[pd.Timestamp("2023-06-01").date()] == 96
    assert len(per_day) == 365


def test_year_index_leap_year() -> None:
    assert len(year_index(2024)) == 35136


def test_year_index_passes_validate_frame() -> None:
    index = year_index(2023)
    validate_frame(pd.DataFrame({"pv_kwh": np.zeros(len(index))}, index=index))


# --- hourly_energy_to_15min -------------------------------------------------------------


def test_hourly_energy_quarters_each_hour() -> None:
    out = hourly_energy_to_15min(_hourly({"load_kwh": [4.0, 2.0], "cost_eur": [1.0, 0.0]}))
    assert list(out["load_kwh"]) == [1.0] * 4 + [0.5] * 4
    assert list(out["cost_eur"]) == [0.25] * 4 + [0.0] * 4
    assert out.index[0] == pd.Timestamp("2023-06-01 00:00", tz=TIMEZONE)
    assert out.index[-1] == pd.Timestamp("2023-06-01 01:45", tz=TIMEZONE)
    validate_frame(out)


def test_hourly_energy_across_spring_dst() -> None:
    # 2023-03-26: 02:00 local does not exist -> 23 hours -> 92 quarters.
    index = pd.date_range("2023-03-26", "2023-03-27", freq="h", tz=TIMEZONE, inclusive="left")
    assert len(index) == 23
    out = hourly_energy_to_15min(pd.DataFrame({"pv_kwh": np.ones(23)}, index=index))
    assert len(out) == 92
    assert out["pv_kwh"].sum() == pytest.approx(23.0)
    validate_frame(out)


def test_hourly_energy_across_autumn_dst() -> None:
    index = pd.date_range("2023-10-29", "2023-10-30", freq="h", tz=TIMEZONE, inclusive="left")
    assert len(index) == 25
    out = hourly_energy_to_15min(pd.DataFrame({"pv_kwh": np.arange(25.0)}, index=index))
    assert len(out) == 100
    assert out["pv_kwh"].sum() == pytest.approx(np.arange(25.0).sum())
    # Both 02:00 hours (CEST and CET) are kept apart.
    assert pd.DatetimeIndex(out.index).tz_convert("UTC").is_unique


def test_hourly_energy_rejects_rate_column() -> None:
    with pytest.raises(ResampleError, match="spot_ct_per_kwh"):
        hourly_energy_to_15min(_hourly({"spot_ct_per_kwh": [10.0]}))


def test_hourly_energy_rejects_power_column() -> None:
    with pytest.raises(ResampleError, match="pv_kw"):
        hourly_energy_to_15min(_hourly({"pv_kw": [10.0]}))


def test_hourly_energy_rejects_gaps() -> None:
    df = _hourly({"load_kwh": [1.0, 1.0, 1.0]}).drop(
        index=pd.Timestamp("2023-06-01 01:00", tz=TIMEZONE)
    )
    with pytest.raises(FrameValidationError, match="missing intervals"):
        hourly_energy_to_15min(df)


def test_hourly_energy_rejects_non_hourly_input() -> None:
    index = pd.date_range("2023-06-01", periods=4, freq="15min", tz=TIMEZONE)
    with pytest.raises(FrameValidationError):
        hourly_energy_to_15min(pd.DataFrame({"load_kwh": np.ones(4)}, index=index))


def test_hourly_energy_rejects_nan() -> None:
    with pytest.raises(FrameValidationError, match="NaN"):
        hourly_energy_to_15min(_hourly({"load_kwh": [1.0, float("nan")]}))


def test_hourly_energy_rejects_empty_columns() -> None:
    index = pd.date_range("2023-06-01", periods=2, freq="h", tz=TIMEZONE)
    with pytest.raises(ResampleError, match="no columns"):
        hourly_energy_to_15min(pd.DataFrame(index=index))


# --- rate_to_15min ----------------------------------------------------------------------


def test_rate_repeats_prices_unchanged() -> None:
    df = _hourly({"spot_ct_per_kwh": [10.0, -3.5], "co2_g_per_kwh": [120.0, 80.0]})
    out = rate_to_15min(df)
    assert list(out["spot_ct_per_kwh"]) == [10.0] * 4 + [-3.5] * 4
    assert list(out["co2_g_per_kwh"]) == [120.0] * 4 + [80.0] * 4
    validate_frame(out)


def test_rate_repeats_power_unchanged() -> None:
    out = rate_to_15min(_hourly({"pv_kw": [7.0]}))
    assert list(out["pv_kw"]) == [7.0] * 4


def test_rate_rejects_energy_column() -> None:
    with pytest.raises(ResampleError, match="load_kwh"):
        rate_to_15min(_hourly({"load_kwh": [1.0]}))


def test_rate_across_dst_year() -> None:
    hours = pd.date_range("2023-01-01", "2024-01-01", freq="h", tz=TIMEZONE, inclusive="left")
    out = rate_to_15min(pd.DataFrame({"spot_ct_per_kwh": np.ones(len(hours))}, index=hours))
    assert out.index.equals(year_index(2023))


# --- aggregate_energy -------------------------------------------------------------------


def test_aggregate_one_second_watts_to_kwh() -> None:
    # 1000 W constant for 15 min = 0.25 kWh per interval.
    index = pd.date_range("2023-06-01", periods=2 * 900, freq="s", tz=TIMEZONE)
    df = pd.DataFrame({"load_w": np.full(len(index), 1000.0)}, index=index)
    out = aggregate_energy(df, timedelta(seconds=1))
    assert list(out.columns) == ["load_kwh"]
    assert len(out) == 2
    assert out["load_kwh"].to_numpy() == pytest.approx([0.25, 0.25])
    assert out.index[1] == pd.Timestamp("2023-06-01 00:15", tz=TIMEZONE)
    validate_frame(out)


def test_aggregate_one_minute_kw_and_kwh() -> None:
    index = pd.date_range("2023-06-01", periods=15, freq="min", tz=TIMEZONE)
    df = pd.DataFrame(
        {"pv_kw": np.full(15, 6.0), "load_kwh": np.full(15, 0.01)},
        index=index,
    )
    out = aggregate_energy(df, timedelta(minutes=1))
    assert out["pv_kwh"].iloc[0] == pytest.approx(1.5)
    assert out["load_kwh"].iloc[0] == pytest.approx(0.15)


def test_aggregate_accepts_other_timezone() -> None:
    index = pd.date_range("2023-05-31 22:00", periods=15, freq="min", tz="UTC")
    out = aggregate_energy(
        pd.DataFrame({"load_kwh": np.ones(15)}, index=index), timedelta(minutes=1)
    )
    assert out.index[0] == pd.Timestamp("2023-06-01 00:00", tz=TIMEZONE)
    validate_frame(out)


def test_aggregate_rejects_incomplete_interval() -> None:
    index = pd.date_range("2023-06-01", periods=20, freq="min", tz=TIMEZONE)
    with pytest.raises(ResampleError, match="incomplete"):
        aggregate_energy(pd.DataFrame({"load_kwh": np.ones(20)}, index=index), timedelta(minutes=1))


def test_aggregate_rejects_misaligned_start() -> None:
    index = pd.date_range("2023-06-01 00:01", periods=15, freq="min", tz=TIMEZONE)
    with pytest.raises(ResampleError, match="does not start"):
        aggregate_energy(pd.DataFrame({"load_kwh": np.ones(15)}, index=index), timedelta(minutes=1))


def test_aggregate_rejects_gap() -> None:
    index = pd.date_range("2023-06-01", periods=31, freq="min", tz=TIMEZONE).delete(7)
    with pytest.raises(ResampleError, match="gapless"):
        aggregate_energy(pd.DataFrame({"load_kwh": np.ones(30)}, index=index), timedelta(minutes=1))


def test_aggregate_rejects_nan() -> None:
    index = pd.date_range("2023-06-01", periods=15, freq="min", tz=TIMEZONE)
    values = np.ones(15)
    values[3] = np.nan
    with pytest.raises(ResampleError, match="NaN"):
        aggregate_energy(pd.DataFrame({"load_w": values}, index=index), timedelta(minutes=1))


def test_aggregate_rejects_naive_index() -> None:
    index = pd.date_range("2023-06-01", periods=15, freq="min")
    with pytest.raises(ResampleError, match="tz-aware"):
        aggregate_energy(pd.DataFrame({"load_kwh": np.ones(15)}, index=index), timedelta(minutes=1))


def test_aggregate_rejects_sample_interval_not_dividing_15min() -> None:
    index = pd.date_range("2023-06-01", periods=2, freq="7min", tz=TIMEZONE)
    with pytest.raises(ResampleError, match="divide"):
        aggregate_energy(pd.DataFrame({"load_kwh": np.ones(2)}, index=index), timedelta(minutes=7))


def test_aggregate_rejects_unknown_suffix() -> None:
    index = pd.date_range("2023-06-01", periods=15, freq="min", tz=TIMEZONE)
    with pytest.raises(ResampleError, match="price_ct_per_kwh"):
        aggregate_energy(
            pd.DataFrame({"price_ct_per_kwh": np.ones(15)}, index=index), timedelta(minutes=1)
        )


def test_aggregate_rejects_empty_columns() -> None:
    index = pd.date_range("2023-06-01", periods=15, freq="min", tz=TIMEZONE)
    with pytest.raises(ResampleError, match="no columns"):
        aggregate_energy(pd.DataFrame(index=index), timedelta(minutes=1))


def test_aggregate_rejects_colliding_targets() -> None:
    index = pd.date_range("2023-06-01", periods=15, freq="min", tz=TIMEZONE)
    df = pd.DataFrame({"load_w": np.ones(15), "load_kwh": np.ones(15)}, index=index)
    with pytest.raises(ResampleError, match="load_kwh"):
        aggregate_energy(df, timedelta(minutes=1))


def test_aggregate_rejects_negative_energy() -> None:
    index = pd.date_range("2023-06-01", periods=15, freq="min", tz=TIMEZONE)
    with pytest.raises(ResampleError, match="negative"):
        aggregate_energy(pd.DataFrame({"load_w": -np.ones(15)}, index=index), timedelta(minutes=1))


def test_aggregate_rejects_negative_sample_even_if_interval_sum_positive() -> None:
    index = pd.date_range("2023-06-01", periods=15, freq="min", tz=TIMEZONE)
    values = np.ones(15)
    values[4] = -0.5
    with pytest.raises(ResampleError, match="1 negative value"):
        aggregate_energy(pd.DataFrame({"load_kwh": values}, index=index), timedelta(minutes=1))


def test_aggregate_rejects_duplicate_columns() -> None:
    index = pd.date_range("2023-06-01", periods=15, freq="min", tz=TIMEZONE)
    df = pd.DataFrame(np.ones((15, 2)), index=index, columns=["load_w", "load_w"])
    with pytest.raises(ResampleError, match="duplicate column"):
        aggregate_energy(df, timedelta(minutes=1))


def test_w_to_kw() -> None:
    assert w_to_kw(2500.0) == 2.5
    assert w_to_kw(0.0) == 0.0


def test_every_unit_suffix_is_classified_once() -> None:
    assert sorted(EXTENSIVE_SUFFIXES + INTENSIVE_SUFFIXES) == sorted(ALLOWED_SUFFIXES)
    assert not set(EXTENSIVE_SUFFIXES) & set(INTENSIVE_SUFFIXES)


def test_aggregate_across_autumn_dst() -> None:
    index = pd.date_range("2023-10-29", "2023-10-30", freq="min", tz=TIMEZONE, inclusive="left")
    df = pd.DataFrame({"load_kwh": np.ones(len(index))}, index=index)
    out = aggregate_energy(df, timedelta(minutes=1))
    assert len(out) == 100
    assert out["load_kwh"].sum() == pytest.approx(len(index))
