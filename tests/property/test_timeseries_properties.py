"""Property tests for energy-conserving resampling (F1-02, INV-6)."""

import math
from datetime import timedelta

import numpy as np
import pandas as pd
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from eeg_optimizer.timeseries import aggregate_energy, hourly_energy_to_15min, rate_to_15min
from eeg_optimizer.units import TIMEZONE, validate_frame

# Any full hour of 2023, so both DST transitions in Europe/Vienna are covered.
_hour_starts = st.integers(min_value=0, max_value=365 * 24 - 1).map(
    lambda h: pd.Timestamp("2023-01-01", tz=TIMEZONE).tz_convert("UTC") + pd.Timedelta(hours=h)
)
_quarter_starts = st.integers(min_value=0, max_value=365 * 96 - 1).map(
    lambda q: (
        pd.Timestamp("2023-01-01", tz=TIMEZONE).tz_convert("UTC") + pd.Timedelta(minutes=15 * q)
    )
)
_energy = st.floats(min_value=0, max_value=1e4, allow_nan=False, allow_infinity=False)
_watts = st.floats(min_value=0, max_value=2e4, allow_nan=False, allow_infinity=False)
_SECONDS_PER_HOUR = 3600


@given(start=_hour_starts, data=st.data())
def test_hourly_energy_to_15min_conserves_energy(start: pd.Timestamp, data: st.DataObject) -> None:
    values = data.draw(st.lists(_energy, min_size=1, max_size=200))
    index = pd.date_range(start, periods=len(values), freq="h").tz_convert(TIMEZONE)
    out = hourly_energy_to_15min(pd.DataFrame({"load_kwh": values}, index=index))

    validate_frame(out)
    assert len(out) == 4 * len(values)
    assert math.isclose(out["load_kwh"].sum(), math.fsum(values), rel_tol=1e-9, abs_tol=1e-9)
    quarters = out["load_kwh"].to_numpy().reshape(-1, 4)
    for hour_value, row in zip(values, quarters, strict=True):
        assert all(q == hour_value / 4 for q in row)


@given(start=_hour_starts, data=st.data())
def test_rate_to_15min_repeats_unchanged(start: pd.Timestamp, data: st.DataObject) -> None:
    prices = data.draw(st.lists(st.floats(min_value=-500, max_value=500), min_size=1, max_size=100))
    index = pd.date_range(start, periods=len(prices), freq="h").tz_convert(TIMEZONE)
    out = rate_to_15min(pd.DataFrame({"spot_ct_per_kwh": prices}, index=index))

    validate_frame(out)
    assert out["spot_ct_per_kwh"].to_numpy().tolist() == np.repeat(prices, 4).tolist()


@given(
    start=_quarter_starts,
    intervals=st.integers(min_value=1, max_value=4),
    sample_seconds=st.sampled_from([1, 5, 60, 300]),
    data=st.data(),
)
def test_aggregate_energy_conserves_energy(
    start: pd.Timestamp, intervals: int, sample_seconds: int, data: st.DataObject
) -> None:
    samples = intervals * 900 // sample_seconds
    # Up to 3600 samples: a background fill value plus sparsely drawn individual samples
    # keeps the minimal example small enough for hypothesis.
    watts = data.draw(
        arrays(np.float64, samples, elements=_watts, fill=_watts),
    )
    index = pd.date_range(start, periods=samples, freq=f"{sample_seconds}s").tz_convert(TIMEZONE)
    out = aggregate_energy(
        pd.DataFrame({"load_w": watts}, index=index), timedelta(seconds=sample_seconds)
    )

    validate_frame(out)
    assert len(out) == intervals
    expected_kwh = math.fsum(watts) / 1000 * sample_seconds / _SECONDS_PER_HOUR
    assert math.isclose(out["load_kwh"].sum(), expected_kwh, rel_tol=1e-9, abs_tol=1e-9)
