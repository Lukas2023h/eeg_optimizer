"""Property tests for the HTW load profiles (F1-06): energy conservation, weekday mapping."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from eeg_optimizer.sources.htw import (
    SOURCE_TZ,
    SOURCE_YEAR,
    aggregate_samples,
    map_to_year,
    source_days,
)
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import validate_frame

_SOURCE = year_index(SOURCE_YEAR)
_N_SOURCE_DAYS = len(_SOURCE) // 96


@settings(max_examples=60, deadline=None)
@given(
    start_quarter=st.integers(min_value=0, max_value=_N_SOURCE_DAYS * 96 - 20),
    power_w=st.integers(min_value=1, max_value=12).flatmap(
        lambda quarters: st.lists(
            st.floats(min_value=0.0, max_value=30_000.0, allow_nan=False),
            min_size=15 * quarters,
            max_size=15 * quarters,
        )
    ),
)
def test_minute_aggregation_conserves_energy(start_quarter: int, power_w: list[float]) -> None:
    # Start anywhere in the MEZ year 2010, including both DST switches.
    first = pd.Timestamp("2010-01-01", tz=SOURCE_TZ) + pd.Timedelta(minutes=15 * start_quarter)
    index = pd.date_range(first, periods=len(power_w), freq="min")
    out = aggregate_samples(pd.DataFrame({"profile_01_w": power_w}, index=index))
    validate_frame(out)
    assert len(out) == len(power_w) // 15
    assert out["profile_01_kwh"].sum() == pytest.approx(sum(power_w) / 60_000, rel=1e-9, abs=1e-12)


@settings(max_examples=40, deadline=None)
@given(year=st.integers(min_value=1990, max_value=2100), seed=st.integers(0, 2**32 - 1))
def test_mapping_copies_whole_days_with_same_weekday(year: int, seed: int) -> None:
    values = np.random.default_rng(seed).uniform(0.0, 2.0, len(_SOURCE))
    out = map_to_year(pd.DataFrame({"load_kwh": values}, index=_SOURCE), year)
    validate_frame(out)
    assert out.index.equals(year_index(year))

    days = source_days(year)
    first = date(year, 1, 1)
    for target_day, source_day in enumerate(days):
        target = first + timedelta(days=int(target_day))
        source = date(SOURCE_YEAR, 1, 1) + timedelta(days=int(source_day))
        assert target.weekday() == source.weekday()
        assert source.year == SOURCE_YEAR
        assert abs(source.timetuple().tm_yday - target.timetuple().tm_yday) <= 7
    # Every target MEZ day is a verbatim copy of its source day.
    mapped = out["load_kwh"].to_numpy().reshape(-1, 96)
    np.testing.assert_array_equal(mapped, values.reshape(-1, 96)[days])
