"""Property: the CSV export (F7-01) returns every value and timestamp exactly."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from eeg_optimizer.export import TIMESERIES_FILE, read_timeseries_csv, timeseries_csv
from eeg_optimizer.units import TIMEZONE

STARTS = ("2023-01-01", "2023-03-25 22:00", "2023-10-28 23:00", "2023-12-31 20:00")
"""Includes both DST switches (local 02:00 missing / twice)."""


@st.composite
def frames(draw: st.DrawFn) -> pd.DataFrame:
    n = draw(st.integers(min_value=1, max_value=24))
    start = draw(st.sampled_from(STARTS))
    index = pd.date_range(start, periods=n, freq="15min", tz=TIMEZONE)
    kwh = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_subnormal=True),
            min_size=n,
            max_size=n,
        )
    )
    eur = draw(
        st.lists(
            st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
            min_size=n,
            max_size=n,
        )
    )
    return pd.DataFrame(
        {"pv_kwh": np.array(kwh, dtype=float), "community_cost_baseline_eur": eur},
        index=index,
    )


@settings(max_examples=60, deadline=None)
@given(frames())
def test_timeseries_csv_roundtrip_is_exact(frame: pd.DataFrame) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / TIMESERIES_FILE
        path.write_text(timeseries_csv(frame), encoding="utf-8")

        restored = read_timeseries_csv(path)

    pd.testing.assert_frame_equal(restored, frame, check_exact=True, check_freq=False)
