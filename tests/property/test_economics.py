"""Property tests for the cost model (F3-04, spec §7.1)."""

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from eeg_optimizer.allocation import DynamicProportional
from eeg_optimizer.balance import community_balance
from eeg_optimizer.config import DEFAULTS_PATH, TariffConfig
from eeg_optimizer.economics import costs, value_shared_ct_per_kwh
from eeg_optimizer.units import TIMEZONE

_DEFAULT_TARIFFS = TariffConfig(
    **yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))["tariffs"]
)

_energy = st.one_of(
    st.just(0.0),
    st.integers(min_value=0, max_value=5).map(float),
    st.floats(min_value=0, max_value=1e3, allow_nan=False, allow_infinity=False),
)
_ct = st.floats(min_value=0, max_value=50, allow_nan=False, allow_infinity=False)
_quarter_starts = st.integers(min_value=0, max_value=365 * 96 - 1).map(
    lambda q: (
        pd.Timestamp("2023-01-01", tz=TIMEZONE).tz_convert("UTC") + pd.Timedelta(minutes=15 * q)
    )
)


@st.composite
def _tariffs(draw: st.DrawFn) -> TariffConfig:
    values: dict[str, Any] = {
        name: draw(_ct)
        for name in (
            "energy_price",
            "grid_fee_energy",
            "levies_taxes",
            "feed_in_tariff",
            "eeg_internal_price",
            "spot_markup",
        )
    }
    values["eeg_type"] = draw(st.sampled_from(["local", "regional"]))
    values["grid_fee_reduction"] = draw(st.one_of(st.none(), st.floats(min_value=0, max_value=1)))
    return replace(_DEFAULT_TARIFFS, **values)


@st.composite
def _scenario(draw: st.DrawFn) -> tuple[pd.DataFrame, pd.DataFrame]:
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
    balance = community_balance(pv, loads)
    return balance, DynamicProportional().allocate(balance, loads)


@given(scenario=_scenario(), tariffs=_tariffs())
def test_community_cost_is_no_sharing_cost_minus_shared_value(
    scenario: tuple[pd.DataFrame, pd.DataFrame], tariffs: TariffConfig
) -> None:
    """Fixed tariff: every shared kWh lowers the community cost by exactly value_shared."""
    balance, allocation = scenario

    result = costs(balance, allocation, tariffs)

    full = tariffs.energy_price + tariffs.grid_fee_energy + tariffs.levies_taxes
    no_sharing_ct = (
        balance["load_kwh"].sum() * full - balance["pv_kwh"].sum() * tariffs.feed_in_tariff
    )
    expected_ct = no_sharing_ct - balance["shared_kwh"].sum() * value_shared_ct_per_kwh(tariffs)
    assert result.community_eur == pytest.approx(expected_ct / 100, rel=1e-9, abs=1e-6)


@given(scenario=_scenario(), tariffs=_tariffs())
def test_household_import_costs_add_up_to_community_import_costs(
    scenario: tuple[pd.DataFrame, pd.DataFrame], tariffs: TariffConfig
) -> None:
    balance, allocation = scenario

    result = costs(balance, allocation, tariffs)

    assert list(result.households.index) == [f"hh{i}" for i in range(len(result.households))]
    assert (result.households.to_numpy() >= 0).all()
    assert result.households["grid_import_cost_eur"].sum() == pytest.approx(
        result.community["grid_import_cost_eur"].sum(), rel=1e-9, abs=1e-6
    )
    np.testing.assert_allclose(
        result.households["cost_eur"],
        result.households["grid_import_cost_eur"] + result.households["shared_cost_eur"],
        rtol=0,
        atol=1e-9,
    )


@given(scenario=_scenario(), tariffs=_tariffs(), data=st.data())
def test_spot_costs_match_formula_with_negative_prices(
    scenario: tuple[pd.DataFrame, pd.DataFrame], tariffs: TariffConfig, data: st.DataObject
) -> None:
    """Dynamic tariff incl. negative spot prices: import cost = Σ import·(spot+markup+fees)."""
    balance, allocation = scenario
    n = len(balance.index)
    spot = data.draw(
        st.lists(st.floats(min_value=-50, max_value=50, allow_nan=False), min_size=n, max_size=n)
    )
    prices = pd.DataFrame({"spot_ct_per_kwh": spot}, index=balance.index)

    result = costs(balance, allocation, tariffs, prices)

    full = np.array(spot) + tariffs.spot_markup + tariffs.grid_fee_energy + tariffs.levies_taxes
    expected_import_ct = float((balance["grid_import_kwh"].to_numpy() * full).sum())
    assert result.community["grid_import_cost_eur"].sum() == pytest.approx(
        expected_import_ct / 100, rel=1e-9, abs=1e-6
    )
    assert result.households["grid_import_cost_eur"].sum() == pytest.approx(
        expected_import_ct / 100, rel=1e-9, abs=1e-6
    )


@given(scenario=_scenario(), tariffs=_tariffs())
def test_spot_at_energy_price_minus_markup_equals_fixed_tariff(
    scenario: tuple[pd.DataFrame, pd.DataFrame], tariffs: TariffConfig
) -> None:
    balance, allocation = scenario
    spot_value = tariffs.energy_price - tariffs.spot_markup
    prices = pd.DataFrame({"spot_ct_per_kwh": spot_value}, index=balance.index)

    spot = costs(balance, allocation, tariffs, prices)
    fixed = costs(balance, allocation, tariffs)

    assert spot.community_eur == pytest.approx(fixed.community_eur, rel=1e-9, abs=1e-6)
    np.testing.assert_allclose(
        spot.households["cost_eur"], fixed.households["cost_eur"], rtol=1e-9, atol=1e-6
    )
