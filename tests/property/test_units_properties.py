"""Property tests for INV-4 (units) and the grid part of INV-6 (F1-01)."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from eeg_optimizer.units import (
    KW,
    TIMEZONE,
    FrameValidationError,
    KWh,
    kw_to_kwh,
    kwh_to_kw,
    validate_frame,
)

_finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
_minutes = st.integers(min_value=1, max_value=24 * 60)
# 2023 covers both DST transitions in Europe/Vienna.
_starts = st.integers(min_value=0, max_value=365 * 96 - 1).map(
    lambda q: (
        pd.Timestamp("2023-01-01", tz=TIMEZONE).tz_convert("UTC") + pd.Timedelta(minutes=15 * q)
    )
)


@given(power=_finite, minutes=_minutes)
def test_kw_kwh_roundtrip(power: float, minutes: int) -> None:
    interval = timedelta(minutes=minutes)
    assert kwh_to_kw(kw_to_kwh(KW(power), interval), interval) == pytest.approx(power)


@given(energy=_finite, minutes=_minutes)
def test_kwh_kw_roundtrip(energy: float, minutes: int) -> None:
    interval = timedelta(minutes=minutes)
    assert kw_to_kwh(kwh_to_kw(KWh(energy), interval), interval) == pytest.approx(energy)


@given(power=_finite, minutes=_minutes)
def test_energy_scales_linearly_with_interval(power: float, minutes: int) -> None:
    single = kw_to_kwh(KW(power), timedelta(minutes=minutes))
    double = kw_to_kwh(KW(power), timedelta(minutes=2 * minutes))
    assert double == pytest.approx(2 * single)


@given(start=_starts, periods=st.integers(min_value=1, max_value=300), data=st.data())
def test_valid_grid_frames_pass_across_dst(
    start: pd.Timestamp, periods: int, data: st.DataObject
) -> None:
    index = pd.date_range(start, periods=periods, freq="15min").tz_convert(TIMEZONE)
    energy = data.draw(
        st.lists(st.floats(min_value=0, max_value=1e3), min_size=periods, max_size=periods)
    )
    validate_frame(pd.DataFrame({"pv_kwh": energy}, index=index))


@given(
    start=_starts,
    periods=st.integers(min_value=3, max_value=300),
    data=st.data(),
)
def test_any_removed_inner_interval_is_detected(
    start: pd.Timestamp, periods: int, data: st.DataObject
) -> None:
    index = pd.date_range(start, periods=periods, freq="15min").tz_convert(TIMEZONE)
    drop = data.draw(st.integers(min_value=1, max_value=periods - 2))
    df = pd.DataFrame({"pv_kwh": np.ones(periods)}, index=index).drop(index=index[drop])
    with pytest.raises(FrameValidationError, match="missing intervals"):
        validate_frame(df)


@given(
    periods=st.integers(min_value=1, max_value=50),
    data=st.data(),
)
def test_any_negative_energy_is_detected(periods: int, data: st.DataObject) -> None:
    index = pd.date_range("2023-03-26", periods=periods, freq="15min", tz=TIMEZONE)
    values = np.ones(periods)
    pos = data.draw(st.integers(min_value=0, max_value=periods - 1))
    values[pos] = -data.draw(st.floats(min_value=1e-9, max_value=1e6))
    with pytest.raises(FrameValidationError, match="negative"):
        validate_frame(pd.DataFrame({"load_kwh": values}, index=index))
