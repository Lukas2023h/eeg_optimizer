"""Property tests for the household allocation (F3-03, INV-7)."""

import numpy as np
import pandas as pd
from hypothesis import given
from hypothesis import strategies as st

from eeg_optimizer.allocation import (
    HOUSEHOLD_COLUMNS,
    DynamicProportional,
    community_totals,
    household_frame,
    household_ids,
)
from eeg_optimizer.balance import community_balance
from eeg_optimizer.units import TIMEZONE, validate_frame

TOLERANCE_KWH = 1e-6

_quarter_starts = st.integers(min_value=0, max_value=365 * 96 - 1).map(
    lambda q: (
        pd.Timestamp("2023-01-01", tz=TIMEZONE).tz_convert("UTC") + pd.Timedelta(minutes=15 * q)
    )
)
# Zeros are frequent so that intervals without load (and households without load) occur.
_energy = st.one_of(
    st.just(0.0),
    st.integers(min_value=0, max_value=5).map(float),
    st.floats(min_value=0, max_value=1e3, allow_nan=False, allow_infinity=False),
)


@st.composite
def _community(draw: st.DrawFn) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    start = draw(_quarter_starts)
    n = draw(st.integers(min_value=1, max_value=48))
    households = draw(st.integers(min_value=1, max_value=15))
    index = pd.date_range(start, periods=n, freq="15min").tz_convert(TIMEZONE)
    pv = pd.DataFrame({"pv_kwh": draw(st.lists(_energy, min_size=n, max_size=n))}, index=index)
    loads = {
        f"hh{i}": pd.DataFrame(
            {"load_kwh": draw(st.lists(_energy, min_size=n, max_size=n))}, index=index
        )
        for i in range(households)
    }
    return pv, loads


@given(community=_community())
def test_allocation_is_complete_per_interval(
    community: tuple[pd.DataFrame, dict[str, pd.DataFrame]],
) -> None:
    """INV-7: household shares add up to the community values in every interval."""
    pv, loads = community
    balance = community_balance(pv, loads)

    out = DynamicProportional().allocate(balance, loads)

    assert out.index.equals(balance.index)
    totals = community_totals(out)
    for column in ("shared_kwh", "grid_import_kwh"):
        np.testing.assert_allclose(totals[column], balance[column], rtol=0, atol=TOLERANCE_KWH)


@given(community=_community())
def test_no_household_gets_more_shared_than_its_load(
    community: tuple[pd.DataFrame, dict[str, pd.DataFrame]],
) -> None:
    pv, loads = community
    balance = community_balance(pv, loads)

    out = DynamicProportional().allocate(balance, loads)

    assert household_ids(out) == list(loads)
    for household_id, load in loads.items():
        block = household_frame(out, household_id)
        validate_frame(block)
        assert tuple(block.columns) == HOUSEHOLD_COLUMNS
        load_values = load["load_kwh"].to_numpy()
        assert (block["shared_kwh"].to_numpy() <= load_values).all()
        assert (block.to_numpy() >= 0).all()
        np.testing.assert_allclose(
            block["shared_kwh"] + block["grid_import_kwh"], load_values, rtol=0, atol=1e-9
        )
