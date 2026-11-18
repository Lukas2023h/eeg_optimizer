"""Unit tests for the household allocation (F3-03)."""

from typing import get_args

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.allocation import (
    COLUMN_LEVELS,
    HOUSEHOLD_COLUMNS,
    STRATEGIES,
    AllocationInputError,
    AllocationStrategy,
    DynamicProportional,
    community_totals,
    household_frame,
    household_ids,
    strategy_for,
)
from eeg_optimizer.balance import community_balance
from eeg_optimizer.config import AllocationConfig, AllocationStrategyName, load_config
from eeg_optimizer.units import TIMEZONE, FrameValidationError, validate_frame


def _index(n: int, start: str = "2023-06-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="15min", tz=TIMEZONE)


def _pv(values: list[float], index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({"pv_kwh": values}, index=index)


def _load(values: list[float], index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({"load_kwh": values}, index=index)


def test_proportional_values() -> None:
    index = _index(4)
    pv = _pv([2.0, 0.0, 10.0, 1.0], index)
    loads = {
        "a": _load([1.0, 1.0, 1.0, 0.0], index),
        "b": _load([3.0, 1.0, 3.0, 0.0], index),
    }
    balance = community_balance(pv, loads)

    out = DynamicProportional().allocate(balance, loads)

    assert list(out.columns.names) == list(COLUMN_LEVELS)
    assert household_ids(out) == ["a", "b"]
    a, b = household_frame(out, "a"), household_frame(out, "b")
    assert tuple(a.columns) == HOUSEHOLD_COLUMNS
    # Interval 0: shared 2 of load 4 -> each household covers half its load.
    assert a["shared_kwh"].tolist() == [0.5, 0.0, 1.0, 0.0]
    assert b["shared_kwh"].tolist() == [1.5, 0.0, 3.0, 0.0]
    assert a["grid_import_kwh"].tolist() == [0.5, 1.0, 0.0, 0.0]
    assert b["grid_import_kwh"].tolist() == [1.5, 1.0, 0.0, 0.0]
    assert b["load_kwh"].tolist() == [3.0, 1.0, 3.0, 0.0]
    validate_frame(a)
    validate_frame(b)
    with pytest.raises(KeyError, match="'c'"):
        household_frame(out, "c")


def test_zero_total_load_gives_zero_shares_without_nan() -> None:
    index = _index(3)
    pv = _pv([5.0, 0.0, 0.0], index)
    loads = {"a": _load([0.0, 0.0, 0.0], index), "b": _load([0.0, 0.0, 0.0], index)}
    balance = community_balance(pv, loads)

    with np.errstate(all="raise"):
        out = DynamicProportional().allocate(balance, loads)

    values = out.to_numpy()
    assert np.isfinite(values).all()
    assert (values == 0.0).all()


def test_community_sums_match_balance() -> None:
    index = _index(96 * 2, start="2023-03-25")  # includes the DST switch on 2023-03-26
    rng = np.random.default_rng(7)
    pv = _pv(list(rng.uniform(0, 3, len(index))), index)
    loads = {f"hh{i}": _load(list(rng.uniform(0, 1, len(index))), index) for i in range(5)}
    balance = community_balance(pv, loads)

    out = DynamicProportional().allocate(balance, loads)

    totals = community_totals(out)
    assert tuple(totals.columns) == HOUSEHOLD_COLUMNS
    for column in HOUSEHOLD_COLUMNS:
        np.testing.assert_allclose(totals[column], balance[column], rtol=0, atol=1e-6)
    # Cross-check the helper against plain pandas aggregation.
    shared_sum = out.T.groupby(level="quantity").sum().T["shared_kwh"]
    np.testing.assert_allclose(totals["shared_kwh"], shared_sum, rtol=0, atol=1e-9)
    assert out.index.equals(balance.index)


def test_single_household_gets_everything() -> None:
    index = _index(2)
    pv = _pv([1.0, 4.0], index)
    loads = {"solo": _load([2.0, 3.0], index)}
    balance = community_balance(pv, loads)

    out = DynamicProportional().allocate(balance, loads)

    solo = household_frame(out, "solo")
    assert solo["shared_kwh"].tolist() == balance["shared_kwh"].tolist()
    assert solo["grid_import_kwh"].tolist() == balance["grid_import_kwh"].tolist()


def test_rejects_balance_of_other_loads() -> None:
    index = _index(2)
    loads = {"a": _load([1.0, 1.0], index)}
    other = {"a": _load([2.0, 1.0], index)}
    balance = community_balance(_pv([1.0, 1.0], index), other)

    with pytest.raises(AllocationInputError, match="sum of the household loads"):
        DynamicProportional().allocate(balance, loads)


def test_rejects_inconsistent_balance() -> None:
    index = _index(2)
    loads = {"a": _load([1.0, 1.0], index)}
    balance = community_balance(_pv([1.0, 1.0], index), loads)
    broken = balance.copy()
    broken["shared_kwh"] = [2.0, 1.0]
    with pytest.raises(AllocationInputError, match="shared_kwh exceeds load_kwh"):
        DynamicProportional().allocate(broken, loads)

    broken = balance.copy()
    broken["grid_import_kwh"] = [0.5, 0.0]
    with pytest.raises(AllocationInputError, match="grid_import_kwh"):
        DynamicProportional().allocate(broken, loads)


def test_rejects_missing_balance_column() -> None:
    index = _index(2)
    loads = {"a": _load([1.0, 1.0], index)}
    balance = community_balance(_pv([1.0, 1.0], index), loads).drop(columns="shared_kwh")
    with pytest.raises(AllocationInputError, match="shared_kwh"):
        DynamicProportional().allocate(balance, loads)


def test_rejects_empty_loads_wrong_columns_and_index() -> None:
    index = _index(2)
    loads = {"a": _load([1.0, 1.0], index)}
    balance = community_balance(_pv([1.0, 1.0], index), loads)
    strategy = DynamicProportional()

    with pytest.raises(AllocationInputError, match="at least one household"):
        strategy.allocate(balance, {})
    with pytest.raises(AllocationInputError, match="exactly the column"):
        strategy.allocate(balance, {"a": pd.DataFrame({"pv_kwh": [1.0, 1.0]}, index=index)})
    with pytest.raises(AllocationInputError, match="different index"):
        strategy.allocate(balance, {"a": _load([1.0, 1.0], _index(2, start="2023-06-02"))})
    with pytest.raises(FrameValidationError):
        strategy.allocate(balance, {"a": _load([1.0, -1.0], index)})


def test_strategy_selected_from_configuration() -> None:
    cfg = load_config("config/example_community_offline.yaml")
    assert cfg.allocation.strategy == "dynamic_proportional"

    strategy: AllocationStrategy = strategy_for(cfg.allocation)

    assert isinstance(strategy, DynamicProportional)


def test_every_configurable_strategy_is_registered() -> None:
    assert set(get_args(AllocationStrategyName)) == set(STRATEGIES)


def test_unknown_strategy_name_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("eeg_optimizer.allocation.STRATEGIES", {})
    with pytest.raises(KeyError, match=r"dynamic_proportional.*nicht verfügbar"):
        strategy_for(AllocationConfig(strategy="dynamic_proportional"))
