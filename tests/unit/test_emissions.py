"""Unit tests for the CO2 balance (F3-05, spec §7.2)."""

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.allocation import DynamicProportional
from eeg_optimizer.balance import community_balance
from eeg_optimizer.economics import EMISSIONS_COLUMN, EmissionInputError, emissions
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import TIMEZONE, FrameValidationError, validate_frame


def _scenario(
    pv: list[float], loads: dict[str, list[float]], index: pd.DatetimeIndex | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if index is None:
        index = pd.date_range("2023-06-01", periods=len(pv), freq="15min", tz=TIMEZONE)
    load_frames = {hh: pd.DataFrame({"load_kwh": v}, index=index) for hh, v in loads.items()}
    balance = community_balance(pd.DataFrame({"pv_kwh": pv}, index=index), load_frames)
    return balance, DynamicProportional().allocate(balance, load_frames)


def _co2(values: list[float] | float, index: pd.Index) -> pd.DataFrame:
    return pd.DataFrame({"co2_g_per_kwh": values}, index=index)


# Mini example: pv [4, 0, 1, 0], loads a [1, 2, 0.5, 3], b [1, 0, 1.5, 1]
# -> shared [2, 0, 1, 0], grid_import [0, 2, 1, 4];
# household import: a [0, 2, 0.25, 3], b [0, 0, 0.75, 1].
# co2 [100, 200, 400, 50] g/kWh
# -> community per interval [0, 0.4, 0.4, 0.2] kg, total 1.0 kg;
#    a = (400 + 100 + 150) / 1000 = 0.65 kg, b = (300 + 50) / 1000 = 0.35 kg.
_MINI_PV = [4.0, 0.0, 1.0, 0.0]
_MINI_LOADS = {"a": [1.0, 2.0, 0.5, 3.0], "b": [1.0, 0.0, 1.5, 1.0]}
_MINI_CO2 = [100.0, 200.0, 400.0, 50.0]


def test_emissions_mini_example() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)

    result = emissions(balance, allocation, _co2(_MINI_CO2, balance.index))

    assert list(result.community.columns) == [EMISSIONS_COLUMN]
    assert EMISSIONS_COLUMN.endswith("_kg_co2")
    assert result.community.index.equals(balance.index)
    validate_frame(result.community)
    np.testing.assert_allclose(result.community[EMISSIONS_COLUMN], [0.0, 0.4, 0.4, 0.2], atol=1e-12)
    assert result.community_kg_co2 == pytest.approx(1.0, abs=1e-12)

    assert list(result.households.index) == ["a", "b"]
    assert result.households.index.name == "household_id"
    assert list(result.households.columns) == [EMISSIONS_COLUMN]
    assert result.household_kg_co2("a") == pytest.approx(0.65, abs=1e-12)
    assert result.household_kg_co2("b") == pytest.approx(0.35, abs=1e-12)


def test_pv_counts_with_zero_emissions() -> None:
    """Fully covered by PV -> no grid import -> 0 kg, also with surplus export."""
    balance, allocation = _scenario([5.0, 3.0], {"a": [1.0, 2.0], "b": [2.0, 1.0]})

    result = emissions(balance, allocation, _co2(500.0, balance.index))

    assert result.community_kg_co2 == 0.0
    assert (result.community[EMISSIONS_COLUMN] == 0.0).all()
    assert (result.households[EMISSIONS_COLUMN] == 0.0).all()


def test_households_add_up_to_community_over_a_year() -> None:
    index = year_index(2023)
    n = len(index)
    rng = np.random.default_rng(7)
    pv = rng.uniform(0, 3, n).tolist()
    loads = {f"hh{i}": rng.uniform(0, 1, n).tolist() for i in range(5)}
    balance, allocation = _scenario(pv, loads, index)
    co2 = _co2(rng.uniform(20, 300, n).tolist(), index)

    result = emissions(balance, allocation, co2)

    expected = float((balance["grid_import_kwh"] * co2["co2_g_per_kwh"]).sum() / 1000)
    assert result.community_kg_co2 == pytest.approx(expected, rel=1e-12)
    assert result.households[EMISSIONS_COLUMN].sum() == pytest.approx(expected, rel=1e-9)


def test_unknown_household_raises_key_error() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    result = emissions(balance, allocation, _co2(_MINI_CO2, balance.index))

    with pytest.raises(KeyError, match="zz"):
        result.household_kg_co2("zz")


# ---------------------------------------------------------------- input errors


def test_wrong_co2_column_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    bad = pd.DataFrame({"intensity_g_per_kwh": _MINI_CO2}, index=balance.index)

    with pytest.raises(EmissionInputError, match="co2_g_per_kwh"):
        emissions(balance, allocation, bad)


def test_co2_with_other_index_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    shifted = pd.date_range("2023-06-01 00:15", periods=len(balance), freq="15min", tz=TIMEZONE)

    with pytest.raises(EmissionInputError, match="index"):
        emissions(balance, allocation, _co2(_MINI_CO2, shifted))


def test_negative_co2_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)

    with pytest.raises(EmissionInputError, match="negative"):
        emissions(balance, allocation, _co2([100.0, -1.0, 0.0, 0.0], balance.index))


def test_nan_co2_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)

    with pytest.raises(FrameValidationError, match="NaN"):
        emissions(balance, allocation, _co2([100.0, np.nan, 0.0, 0.0], balance.index))


def test_balance_without_grid_import_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)

    with pytest.raises(EmissionInputError, match="grid_import_kwh"):
        emissions(
            balance.drop(columns="grid_import_kwh"), allocation, _co2(_MINI_CO2, balance.index)
        )


def test_allocation_of_other_balance_raises() -> None:
    balance, _ = _scenario(_MINI_PV, _MINI_LOADS)
    _, other_allocation = _scenario(_MINI_PV, {"a": [2.0, 2.0, 2.0, 2.0]})

    with pytest.raises(EmissionInputError, match="does not fit"):
        emissions(balance, other_allocation, _co2(_MINI_CO2, balance.index))
