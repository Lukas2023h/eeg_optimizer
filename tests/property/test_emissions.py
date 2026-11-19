"""Property tests for the CO2 balance (F3-05, spec §7.2)."""

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from eeg_optimizer.allocation import DynamicProportional, household_frame, household_ids
from eeg_optimizer.balance import community_balance
from eeg_optimizer.economics import EMISSIONS_COLUMN, emissions
from eeg_optimizer.units import TIMEZONE

_energy = st.one_of(
    st.just(0.0),
    st.integers(min_value=0, max_value=5).map(float),
    st.floats(min_value=0, max_value=1e3, allow_nan=False, allow_infinity=False),
)
_intensity = st.floats(min_value=0, max_value=1500, allow_nan=False, allow_infinity=False)
_quarter_starts = st.integers(min_value=0, max_value=365 * 96 - 1).map(
    lambda q: (
        pd.Timestamp("2023-01-01", tz=TIMEZONE).tz_convert("UTC") + pd.Timedelta(minutes=15 * q)
    )
)


@st.composite
def _scenario(draw: st.DrawFn) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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
    co2 = pd.DataFrame(
        {"co2_g_per_kwh": draw(st.lists(_intensity, min_size=n, max_size=n))}, index=index
    )
    balance = community_balance(pv, loads)
    return balance, DynamicProportional().allocate(balance, loads), co2


@given(scenario=_scenario())
def test_emissions_match_formula_and_households_add_up(
    scenario: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    balance, allocation, co2 = scenario

    result = emissions(balance, allocation, co2)

    expected = balance["grid_import_kwh"].to_numpy() * co2["co2_g_per_kwh"].to_numpy() / 1000
    np.testing.assert_allclose(result.community[EMISSIONS_COLUMN], expected, rtol=1e-12, atol=0)
    assert result.community_kg_co2 == pytest.approx(float(expected.sum()), rel=1e-12, abs=1e-9)
    assert list(result.households.index) == [f"hh{i}" for i in range(len(result.households))]
    assert (result.households[EMISSIONS_COLUMN] >= 0).all()
    # INV-7 carries over: households share exactly the community grid import per interval.
    assert result.households[EMISSIONS_COLUMN].sum() == pytest.approx(
        result.community_kg_co2, rel=1e-9, abs=1e-6
    )


@given(scenario=_scenario())
def test_more_pv_never_increases_emissions(
    scenario: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> None:
    """PV counts with 0 g/kWh: extra PV can only replace grid import."""
    balance, allocation, co2 = scenario
    loads = {hh: household_frame(allocation, hh)[["load_kwh"]] for hh in household_ids(allocation)}
    more_pv = pd.DataFrame({"pv_kwh": balance["pv_kwh"] + 1.0}, index=balance.index)
    more_balance = community_balance(more_pv, loads)
    more_allocation = DynamicProportional().allocate(more_balance, loads)

    before = emissions(balance, allocation, co2)
    after = emissions(more_balance, more_allocation, co2)

    assert after.community_kg_co2 <= before.community_kg_co2 + 1e-9
