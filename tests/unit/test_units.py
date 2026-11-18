"""Tests for unit conversion and frame validation (F1-01, INV-4)."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.units import (
    INTERVAL,
    KW,
    TIMEZONE,
    FrameValidationError,
    KWh,
    ct_to_eur,
    kw_to_kwh,
    kwh_to_kw,
    validate_frame,
)


def test_ct_to_eur_scalar_and_array() -> None:
    assert ct_to_eur(942.0) == pytest.approx(9.42)
    assert ct_to_eur(-50.0) == pytest.approx(-0.5)
    np.testing.assert_allclose(ct_to_eur(np.array([100.0, -2.5])), [1.0, -0.025])


def _frame(periods: int = 8, start: str = "2023-06-01 00:00") -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="15min", tz=TIMEZONE)
    return pd.DataFrame(
        {
            "pv_kwh": np.linspace(0.0, 1.0, periods),
            "load_kw": np.full(periods, -0.5),  # power may be negative (feed-in sign)
            "spot_ct_per_kwh": np.full(periods, -2.0),  # negative prices are real
            "cost_eur": np.zeros(periods),
            "co2_g_per_kwh": np.full(periods, 150.0),
            "saved_kg_co2": np.zeros(periods),
        },
        index=index,
    )


# --- conversion ---------------------------------------------------------------


def test_kw_to_kwh_quarter_hour() -> None:
    assert kw_to_kwh(KW(4.0), interval=timedelta(minutes=15)) == 1.0


def test_kw_to_kwh_default_interval_is_15_min() -> None:
    assert timedelta(minutes=15) == INTERVAL
    assert kw_to_kwh(KW(4.0)) == 1.0


def test_kwh_to_kw_is_inverse() -> None:
    assert kwh_to_kw(KWh(1.0), interval=timedelta(minutes=15)) == 4.0
    assert kwh_to_kw(kw_to_kwh(KW(3.7))) == pytest.approx(3.7)


def test_conversion_other_interval() -> None:
    assert kw_to_kwh(KW(2.0), interval=timedelta(hours=1)) == 2.0
    assert kwh_to_kw(KWh(2.0), interval=timedelta(minutes=30)) == 4.0


@pytest.mark.parametrize("interval", [timedelta(0), timedelta(minutes=-15)])
def test_conversion_rejects_non_positive_interval(interval: timedelta) -> None:
    with pytest.raises(ValueError, match="interval must be positive"):
        kw_to_kwh(KW(1.0), interval=interval)
    with pytest.raises(ValueError, match="interval must be positive"):
        kwh_to_kw(KWh(1.0), interval=interval)


# --- validate_frame: accept ---------------------------------------------------


def test_accepts_valid_vienna_frame() -> None:
    validate_frame(_frame())


@pytest.mark.parametrize(
    ("day", "expected_intervals"),
    [("2023-03-26", 92), ("2023-10-29", 100), ("2023-06-01", 96)],
)
def test_accepts_dst_days(day: str, expected_intervals: int) -> None:
    start = pd.Timestamp(day, tz=TIMEZONE)
    # Build in UTC between local midnights to get the real interval count of the day.
    index = pd.date_range(
        start.tz_convert("UTC"),
        (start + pd.DateOffset(days=1)).tz_convert("UTC"),
        freq="15min",
        inclusive="left",
    ).tz_convert(TIMEZONE)
    assert len(index) == expected_intervals
    validate_frame(pd.DataFrame({"pv_kwh": np.zeros(len(index))}, index=index))


def test_ignores_non_numeric_columns() -> None:
    df = _frame()
    df["household_id"] = "h1"
    validate_frame(df)


# --- validate_frame: reject ---------------------------------------------------


def test_rejects_column_without_unit_suffix() -> None:
    df = _frame()
    df["pv"] = 1.0
    with pytest.raises(FrameValidationError, match=r"'pv' has no unit suffix.*_kwh"):
        validate_frame(df)


@pytest.mark.parametrize("name", ["pv_kWh", "load_wh", "_kwh", "price_ct"])
def test_rejects_invalid_suffix_variants(name: str) -> None:
    df = _frame()
    df[name] = 1.0
    with pytest.raises(FrameValidationError, match="has no unit suffix"):
        validate_frame(df)


def test_rejects_naive_timezone() -> None:
    df = _frame()
    df.index = pd.DatetimeIndex(df.index).tz_localize(None)
    with pytest.raises(FrameValidationError, match=r"naive timezone.*Europe/Vienna"):
        validate_frame(df)


def test_rejects_other_timezone() -> None:
    df = _frame()
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
    with pytest.raises(FrameValidationError, match="timezone is UTC; expected Europe/Vienna"):
        validate_frame(df)


def test_rejects_non_datetime_index() -> None:
    df = _frame().reset_index(drop=True)
    with pytest.raises(FrameValidationError, match="DatetimeIndex"):
        validate_frame(df)


def test_rejects_missing_intervals() -> None:
    df = _frame(periods=8).drop(index=_frame(periods=8).index[3:5])
    with pytest.raises(
        FrameValidationError,
        match=r"missing intervals: 2 interval\(s\).*2023-06-01T00:30:00\+02:00.*01:15",
    ):
        validate_frame(df)


def test_rejects_misaligned_timestamps() -> None:
    df = _frame(start="2023-06-01 00:05")
    with pytest.raises(FrameValidationError, match="not aligned"):
        validate_frame(df)


def test_rejects_duplicate_timestamps() -> None:
    df = _frame()
    df = pd.concat([df, df.iloc[[2]]]).sort_index()
    with pytest.raises(FrameValidationError, match="duplicate timestamp"):
        validate_frame(df)


def test_rejects_unsorted_index() -> None:
    df = _frame().iloc[::-1]
    with pytest.raises(FrameValidationError, match="not sorted"):
        validate_frame(df)


def test_rejects_empty_frame() -> None:
    df = _frame().iloc[:0]
    with pytest.raises(FrameValidationError, match="empty"):
        validate_frame(df)


def test_rejects_nan() -> None:
    df = _frame()
    df.loc[df.index[5], "co2_g_per_kwh"] = np.nan
    with pytest.raises(
        FrameValidationError,
        match=r"'co2_g_per_kwh' contains 1 NaN/inf.*2023-06-01T01:15:00\+02:00",
    ):
        validate_frame(df)


def test_rejects_inf() -> None:
    df = _frame()
    df.loc[df.index[0], "load_kw"] = np.inf
    with pytest.raises(FrameValidationError, match="'load_kw' contains 1 NaN/inf"):
        validate_frame(df)


def test_rejects_negative_energy() -> None:
    df = _frame()
    df.loc[df.index[2], "pv_kwh"] = -0.1
    with pytest.raises(
        FrameValidationError,
        match=r"energy column 'pv_kwh' contains 1 negative value\(s\), first -0.1 at "
        r"2023-06-01T00:30:00\+02:00",
    ):
        validate_frame(df)


@pytest.mark.parametrize(
    "values",
    [
        pd.Series([0.5, "n/a", -3.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype="string"),
        pd.Series([None, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=object),
        pd.Series([-1.0] * 8, dtype=object),
        pd.Series([True] * 8),
    ],
    ids=["string", "object-none", "object-negative", "bool"],
)
def test_rejects_unit_column_with_non_numeric_dtype(values: pd.Series) -> None:
    df = _frame()
    df["pv_kwh"] = values.to_numpy()
    df["pv_kwh"] = df["pv_kwh"].astype(values.dtype)
    with pytest.raises(FrameValidationError, match="'pv_kwh' must have a numeric dtype"):
        validate_frame(df)


@pytest.mark.parametrize(
    "interval", [timedelta(0), timedelta(minutes=-15), timedelta(minutes=7), timedelta(days=1)]
)
def test_validate_frame_rejects_unsupported_interval(interval: timedelta) -> None:
    with pytest.raises(ValueError, match="divide one hour"):
        validate_frame(_frame(), interval=interval)


def test_validate_frame_accepts_hourly_grid() -> None:
    index = pd.date_range("2023-03-26", periods=24, freq="1h", tz=TIMEZONE)
    validate_frame(pd.DataFrame({"pv_kwh": np.zeros(24)}, index=index), interval=timedelta(hours=1))


def test_rejects_complex_unit_column() -> None:
    df = _frame()
    df["pv_kwh"] = np.full(8, 1 + 1j)
    with pytest.raises(FrameValidationError, match="'pv_kwh' must have a numeric dtype"):
        validate_frame(df)


def test_rejects_duplicate_column_names() -> None:
    df = pd.concat([_frame()[["pv_kwh"]], _frame()[["pv_kwh"]]], axis=1)
    with pytest.raises(FrameValidationError, match=r"duplicate column name.*pv_kwh"):
        validate_frame(df)


def test_rejects_object_column_with_numbers_without_suffix() -> None:
    df = _frame()
    df["pv"] = pd.Series([1.0] * 8, dtype=object).to_numpy()
    assert df["pv"].dtype == object
    with pytest.raises(FrameValidationError, match="'pv' has no unit suffix"):
        validate_frame(df)


def test_rejects_bool_column_without_suffix() -> None:
    df = _frame()
    df["flag"] = True
    with pytest.raises(FrameValidationError, match="'flag' has no unit suffix"):
        validate_frame(df)


def test_rejects_missing_value_in_nullable_float() -> None:
    df = _frame()
    df["pv_kwh"] = pd.array([None, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype="Float64")
    with pytest.raises(FrameValidationError, match="'pv_kwh' contains 1 NaN/inf"):
        validate_frame(df)


def test_negative_ct_per_kwh_is_not_energy() -> None:
    # "_ct_per_kwh" also ends in "_kwh" but is a price; negative spot prices are valid.
    df = _frame()
    assert (df["spot_ct_per_kwh"] < 0).all()
    validate_frame(df)
