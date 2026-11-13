"""Property tests for the PVGIS conversion (F1-05, INV energy conservation)."""

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from eeg_optimizer.sources.pvgis import COLUMN, hourly_to_15min
from eeg_optimizer.units import validate_frame


@settings(max_examples=60, deadline=None)
@given(
    start_hour=st.integers(min_value=0, max_value=365 * 24 - 200),
    minute=st.integers(min_value=0, max_value=59),
    power_w=st.lists(
        st.floats(min_value=0.0, max_value=50_000.0, allow_nan=False), min_size=1, max_size=150
    ),
)
def test_energy_conserved_and_hour_kept(start_hour: int, minute: int, power_w: list[float]) -> None:
    # Ranges include both DST switches of 2023.
    first = pd.Timestamp("2023-01-01", tz="UTC") + pd.Timedelta(hours=start_hour, minutes=minute)
    index = pd.date_range(first, periods=len(power_w), freq="h")
    raw = pd.DataFrame({"P": power_w}, index=index)
    out = hourly_to_15min(raw)
    validate_frame(out)
    assert len(out) == 4 * len(power_w)
    assert out[COLUMN].sum() == pytest.approx(sum(power_w) / 1000.0, rel=1e-9, abs=1e-9)
    # Each hour's quarters sit in the UTC hour that contains the PVGIS stamp.
    utc_starts = pd.DatetimeIndex(out.index).tz_convert("UTC")[::4]
    assert (utc_starts == index.floor("h")).all()
    per_hour = out[COLUMN].to_numpy().reshape(-1, 4).sum(axis=1)
    assert np.allclose(per_hour, np.asarray(power_w) / 1000.0, rtol=1e-12, atol=1e-12)
