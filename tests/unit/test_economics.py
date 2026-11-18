"""Unit tests for the cost model (F3-04, spec §7.1)."""

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from eeg_optimizer.allocation import DynamicProportional
from eeg_optimizer.balance import community_balance
from eeg_optimizer.config import DEFAULTS_PATH, TariffConfig
from eeg_optimizer.economics import (
    COMMUNITY_COST_COLUMN,
    COMMUNITY_COST_COLUMNS,
    HOUSEHOLD_COST_COLUMNS,
    CostInputError,
    costs,
    reduced_grid_fee_ct_per_kwh,
    value_shared_ct_per_kwh,
)
from eeg_optimizer.units import TIMEZONE, FrameValidationError, validate_frame


def _default_tariffs() -> TariffConfig:
    raw: dict[str, Any] = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))["tariffs"]
    return TariffConfig(**raw)


def _index(n: int, start: str = "2023-06-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="15min", tz=TIMEZONE)


def _scenario(
    pv: list[float], loads: dict[str, list[float]], start: str = "2023-06-01"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = _index(len(pv), start)
    load_frames = {hh: pd.DataFrame({"load_kwh": v}, index=index) for hh, v in loads.items()}
    balance = community_balance(pd.DataFrame({"pv_kwh": pv}, index=index), load_frames)
    return balance, DynamicProportional().allocate(balance, load_frames)


def _spot(values: list[float], index: pd.Index) -> pd.DataFrame:
    return pd.DataFrame({"spot_ct_per_kwh": values}, index=index)


# ---------------------------------------------------------------- value of a shared kWh


def test_value_shared_defaults_local() -> None:
    tariffs = replace(_default_tariffs(), eeg_type="local")
    assert value_shared_ct_per_kwh(tariffs) == pytest.approx(9.42, abs=1e-12)


def test_value_shared_defaults_regional() -> None:
    tariffs = replace(_default_tariffs(), eeg_type="regional")
    assert value_shared_ct_per_kwh(tariffs) == pytest.approx(7.68, abs=1e-12)


def test_value_shared_uses_explicit_grid_fee_reduction() -> None:
    tariffs = replace(_default_tariffs(), eeg_type="local", grid_fee_reduction=0.0)
    assert value_shared_ct_per_kwh(tariffs) == pytest.approx(13.0 - 7.0, abs=1e-12)
    assert reduced_grid_fee_ct_per_kwh(tariffs) == pytest.approx(6.0, abs=1e-12)


# ---------------------------------------------------------------- community view


# Mini example: pv [4, 0, 1], loads a [1, 2, 0.5], b [1, 0, 0.5]
# -> shared [2, 0, 1], export [2, 0, 0], grid_import [0, 2, 0].
# Defaults local: full price 13 + 6 + 6 = 25, shared (community) 6 * 0.43 + 6 = 8.58,
# feed-in 7, internal price 12 + 2.58 + 6 = 20.58 (all ct/kWh).
_MINI_PV = [4.0, 0.0, 1.0]
_MINI_LOADS = {"a": [1.0, 2.0, 0.5], "b": [1.0, 0.0, 0.5]}


def test_community_costs_fixed_tariff_mini_example() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)

    result = costs(balance, allocation, _default_tariffs())

    assert result.tariff == "fixed"
    assert tuple(result.community.columns) == COMMUNITY_COST_COLUMNS
    assert result.community.index.equals(balance.index)
    validate_frame(result.community)
    c = result.community
    np.testing.assert_allclose(c["grid_import_cost_eur"], [0.0, 0.50, 0.0], atol=1e-12)
    np.testing.assert_allclose(c["shared_cost_eur"], [0.1716, 0.0, 0.0858], atol=1e-12)
    np.testing.assert_allclose(c["export_revenue_eur"], [0.14, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(c[COMMUNITY_COST_COLUMN], [0.0316, 0.50, 0.0858], atol=1e-12)
    # 2 * 25 + 3 * 8.58 - 2 * 7 = 61.74 ct
    assert result.community_eur == pytest.approx(0.6174, abs=1e-12)


def test_shifting_one_kwh_from_export_to_shared_saves_value_shared() -> None:
    tariffs = _default_tariffs()
    # Same PV (2 kWh at noon) and same total load (2 kWh); B moves 1 kWh into the PV interval.
    before, alloc_before = _scenario([2.0, 0.0], {"a": [0.0, 2.0]})
    after, alloc_after = _scenario([2.0, 0.0], {"a": [1.0, 1.0]})
    assert after["shared_kwh"].sum() - before["shared_kwh"].sum() == 1.0
    assert after["export_kwh"].sum() - before["export_kwh"].sum() == -1.0

    saved = (
        costs(before, alloc_before, tariffs).community_eur
        - costs(after, alloc_after, tariffs).community_eur
    )

    assert saved == pytest.approx(value_shared_ct_per_kwh(tariffs) / 100, abs=1e-12)


@pytest.mark.parametrize("eeg_type", ["local", "regional"])
def test_community_cost_equals_no_sharing_minus_shared_times_value(eeg_type: str) -> None:
    tariffs = replace(_default_tariffs(), eeg_type=eeg_type)  # type: ignore[arg-type]
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)

    result = costs(balance, allocation, tariffs)

    full = tariffs.energy_price + tariffs.grid_fee_energy + tariffs.levies_taxes
    no_sharing_ct = balance["load_kwh"].sum() * full - balance["pv_kwh"].sum() * 7.0
    expected_ct = no_sharing_ct - balance["shared_kwh"].sum() * value_shared_ct_per_kwh(tariffs)
    assert result.community_eur == pytest.approx(expected_ct / 100, abs=1e-12)


# ---------------------------------------------------------------- household view


def test_household_costs_fixed_tariff_mini_example() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)

    result = costs(balance, allocation, _default_tariffs())

    h = result.households
    assert list(h.index) == ["a", "b"]
    assert h.index.name == "household_id"
    assert tuple(h.columns) == HOUSEHOLD_COST_COLUMNS
    # a: import 2 kWh * 25 ct, shared 1.5 kWh * 20.58 ct; b: shared 1.5 kWh * 20.58 ct.
    np.testing.assert_allclose(h["grid_import_cost_eur"], [0.50, 0.0], atol=1e-12)
    np.testing.assert_allclose(h["shared_cost_eur"], [0.3087, 0.3087], atol=1e-12)
    np.testing.assert_allclose(h["cost_eur"], [0.8087, 0.3087], atol=1e-12)
    assert result.household_eur("a") == pytest.approx(0.8087, abs=1e-12)
    with pytest.raises(KeyError, match="zz"):
        result.household_eur("zz")


def test_spot_tariff_mini_example() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    # Negative spot price in the import interval: energy -2 + 1.5 = -0.5, full price 11.5 ct.
    prices = _spot([10.0, -2.0, 5.0], balance.index)

    result = costs(balance, allocation, _default_tariffs(), prices)

    assert result.tariff == "spot"
    np.testing.assert_allclose(
        result.community["grid_import_cost_eur"], [0.0, 0.23, 0.0], atol=1e-12
    )
    # 2 * 11.5 + 3 * 8.58 - 2 * 7 = 34.74 ct
    assert result.community_eur == pytest.approx(0.3474, abs=1e-12)
    # Internal price stays fixed; only the grid import of household a changes.
    np.testing.assert_allclose(result.households["cost_eur"], [0.5387, 0.3087], atol=1e-12)


def test_spot_equal_to_fixed_energy_price_gives_fixed_costs() -> None:
    tariffs = _default_tariffs()
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    prices = _spot([tariffs.energy_price - tariffs.spot_markup] * 3, balance.index)

    spot = costs(balance, allocation, tariffs, prices)
    fixed = costs(balance, allocation, tariffs)

    assert spot.community_eur == pytest.approx(fixed.community_eur, abs=1e-12)
    pd.testing.assert_frame_equal(spot.households, fixed.households, rtol=0, atol=1e-12)


def test_full_year_with_dst() -> None:
    index = pd.date_range("2023-01-01", "2024-01-01", freq="15min", tz=TIMEZONE, inclusive="left")
    hour = index.hour.to_numpy()
    pv = np.where((hour >= 8) & (hour < 16), 0.5, 0.0)
    load = np.full(len(index), 0.2)
    loads = {"a": pd.DataFrame({"load_kwh": load}, index=index)}
    balance = community_balance(pd.DataFrame({"pv_kwh": pv}, index=index), loads)
    allocation = DynamicProportional().allocate(balance, loads)

    result = costs(balance, allocation, _default_tariffs())

    assert len(result.community) == 35040
    assert result.community_eur == pytest.approx(
        float(result.community[COMMUNITY_COST_COLUMN].sum()), abs=1e-9
    )


# ---------------------------------------------------------------- input errors


def test_missing_balance_column_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    with pytest.raises(CostInputError, match="export_kwh"):
        costs(balance.drop(columns=["export_kwh"]), allocation, _default_tariffs())


def test_wrong_price_column_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    prices = pd.DataFrame({"price_ct_per_kwh": [1.0, 2.0, 3.0]}, index=balance.index)
    with pytest.raises(CostInputError, match="spot_ct_per_kwh"):
        costs(balance, allocation, _default_tariffs(), prices)


def test_price_index_mismatch_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    prices = _spot([1.0, 2.0, 3.0], _index(3, "2023-07-01"))
    with pytest.raises(CostInputError, match="index"):
        costs(balance, allocation, _default_tariffs(), prices)


def test_invalid_price_frame_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    prices = _spot([1.0, float("nan"), 3.0], balance.index)
    with pytest.raises(FrameValidationError):
        costs(balance, allocation, _default_tariffs(), prices)


def test_allocation_of_other_scenario_raises() -> None:
    balance, _ = _scenario(_MINI_PV, _MINI_LOADS)
    _, other_allocation = _scenario(_MINI_PV, {"a": [3.0, 2.0, 0.5], "b": [1.0, 0.0, 0.5]})
    with pytest.raises(CostInputError, match="does not fit"):
        costs(balance, other_allocation, _default_tariffs())


def test_allocation_with_nan_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    broken = allocation.copy()
    broken.loc[broken.index[1], ("b", "grid_import_kwh")] = float("nan")
    with pytest.raises(FrameValidationError, match="NaN"):
        costs(balance, broken, _default_tariffs())


def test_allocation_without_column_levels_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    flat = allocation.copy()
    flat.columns = ["_".join(map(str, c)) for c in allocation.columns]
    with pytest.raises(CostInputError, match="column levels"):
        costs(balance, flat, _default_tariffs())


def test_allocation_missing_quantity_raises() -> None:
    balance, allocation = _scenario(_MINI_PV, _MINI_LOADS)
    with pytest.raises(CostInputError, match="load_kwh"):
        costs(balance, allocation.drop(columns=[("a", "load_kwh")]), _default_tariffs())


def test_allocation_index_mismatch_raises() -> None:
    balance, _ = _scenario(_MINI_PV, _MINI_LOADS)
    _, other_allocation = _scenario(_MINI_PV, _MINI_LOADS, start="2023-07-01")
    with pytest.raises(CostInputError, match="index"):
        costs(balance, other_allocation, _default_tariffs())
