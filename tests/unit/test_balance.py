"""Unit tests for the community energy balance (F3-01)."""

import pandas as pd
import pytest

from eeg_optimizer.balance import BALANCE_COLUMNS, BalanceInputError, community_balance
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import TIMEZONE, FrameValidationError, validate_frame


def _index(n: int = 4) -> pd.DatetimeIndex:
    return pd.date_range("2023-06-01 10:00", periods=n, freq="15min", tz=TIMEZONE)


def _pv(values: list[float], index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    return pd.DataFrame({"pv_kwh": values}, index=_index(len(values)) if index is None else index)


def _load(values: list[float], index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    return pd.DataFrame({"load_kwh": values}, index=_index(len(values)) if index is None else index)


def test_columns_and_values() -> None:
    out = community_balance(
        _pv([3.0, 1.0, 0.0, 2.0]),
        {"a": _load([1.0, 1.0, 0.5, 0.0]), "b": _load([0.5, 1.5, 0.5, 0.0])},
    )

    assert tuple(out.columns) == BALANCE_COLUMNS
    assert out["load_kwh"].tolist() == [1.5, 2.5, 1.0, 0.0]
    assert out["shared_kwh"].tolist() == [1.5, 1.0, 0.0, 0.0]
    assert out["export_kwh"].tolist() == [1.5, 0.0, 0.0, 2.0]
    assert out["grid_import_kwh"].tolist() == [0.0, 1.5, 1.0, 0.0]
    validate_frame(out)


def test_pv_zero_all_load_from_grid() -> None:
    out = community_balance(_pv([0.0, 0.0]), {"a": _load([0.7, 0.0])})

    assert out["shared_kwh"].tolist() == [0.0, 0.0]
    assert out["export_kwh"].tolist() == [0.0, 0.0]
    assert out["grid_import_kwh"].tolist() == [0.7, 0.0]


def test_load_zero_all_pv_exported() -> None:
    out = community_balance(_pv([2.5, 0.0]), {"a": _load([0.0, 0.0]), "b": _load([0.0, 0.0])})

    assert out["shared_kwh"].tolist() == [0.0, 0.0]
    assert out["export_kwh"].tolist() == [2.5, 0.0]
    assert out["grid_import_kwh"].tolist() == [0.0, 0.0]


def test_pv_equals_load_everything_shared() -> None:
    out = community_balance(_pv([1.2, 0.3]), {"a": _load([0.2, 0.1]), "b": _load([1.0, 0.2])})

    assert out["shared_kwh"].tolist() == pytest.approx([1.2, 0.3], abs=1e-12)
    assert out["export_kwh"].tolist() == pytest.approx([0.0, 0.0], abs=1e-12)
    assert out["grid_import_kwh"].tolist() == pytest.approx([0.0, 0.0], abs=1e-12)
    assert (out.to_numpy() >= 0).all()


def test_full_year_with_dst() -> None:
    index = year_index(2023)
    out = community_balance(_pv([1.0] * len(index), index), {"a": _load([0.4] * len(index), index)})

    assert out.index.equals(index)
    assert out["shared_kwh"].sum() == pytest.approx(0.4 * len(index))
    validate_frame(out)


def test_inputs_are_not_modified() -> None:
    pv = _pv([1.0, 2.0])
    load = _load([3.0, 0.5])
    community_balance(pv, {"a": load})

    assert pv["pv_kwh"].tolist() == [1.0, 2.0]
    assert list(pv.columns) == ["pv_kwh"]
    assert load["load_kwh"].tolist() == [3.0, 0.5]


def test_empty_loads_rejected() -> None:
    with pytest.raises(BalanceInputError, match="at least one household"):
        community_balance(_pv([1.0]), {})


def test_wrong_pv_column_rejected() -> None:
    pv = pd.DataFrame({"pv_kw": [1.0]}, index=_index(1))
    with pytest.raises(BalanceInputError, match="'pv_kwh'"):
        community_balance(pv, {"a": _load([1.0])})


def test_extra_load_column_rejected() -> None:
    load = _load([1.0]).assign(other_kwh=[0.0])
    with pytest.raises(BalanceInputError, match="household 'a'"):
        community_balance(_pv([1.0]), {"a": load})


def test_differing_index_rejected() -> None:
    shifted = _index(3) + pd.Timedelta(minutes=15)
    with pytest.raises(BalanceInputError, match="different index"):
        community_balance(_pv([1.0, 1.0, 1.0]), {"a": _load([1.0, 1.0, 1.0], shifted)})


def test_negative_load_rejected() -> None:
    with pytest.raises(FrameValidationError):
        community_balance(_pv([1.0]), {"a": _load([-0.1])})


def test_naive_index_rejected() -> None:
    naive = pd.date_range("2023-06-01", periods=2, freq="15min")
    with pytest.raises(FrameValidationError):
        community_balance(_pv([1.0, 1.0], naive), {"a": _load([1.0, 1.0], naive)})
