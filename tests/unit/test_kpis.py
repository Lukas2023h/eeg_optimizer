"""Unit tests for the baseline KPIs (F3-02)."""

import dataclasses
import math

import pandas as pd
import pytest

from eeg_optimizer.balance import (
    BALANCE_COLUMNS,
    MONTHLY_KPI_COLUMNS,
    BalanceInputError,
    Kpis,
    community_balance,
    kpis,
    monthly_kpis,
)
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import TIMEZONE, FrameValidationError


def _index(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-06-01 10:00", periods=n, freq="15min", tz=TIMEZONE)


def _balance(pv: list[float], loads: dict[str, list[float]]) -> pd.DataFrame:
    index = _index(len(pv))
    return community_balance(
        pd.DataFrame({"pv_kwh": pv}, index=index),
        {hh: pd.DataFrame({"load_kwh": v}, index=index) for hh, v in loads.items()},
    )


def test_hand_calculated_example() -> None:
    # Per interval: pv 4/2/0/2, load (a+b) 1/3/2/0
    #   shared 1/2/0/0 = 3, export 3/0/0/2 = 5, import 0/1/2/0 = 3
    #   self-consumption 3/8 = 0.375, autarky 3/6 = 0.5
    result = kpis(
        _balance([4.0, 2.0, 0.0, 2.0], {"a": [0.5, 1.0, 1.0, 0.0], "b": [0.5, 2.0, 1.0, 0.0]})
    )

    assert isinstance(result, Kpis)
    assert result.pv_kwh == pytest.approx(8.0)
    assert result.load_kwh == pytest.approx(6.0)
    assert result.shared_kwh == pytest.approx(3.0)
    assert result.export_kwh == pytest.approx(5.0)
    assert result.grid_import_kwh == pytest.approx(3.0)
    assert result.self_consumption_ratio == pytest.approx(0.375)
    assert result.autarky_ratio == pytest.approx(0.5)


def test_kpis_fields_are_plain_floats() -> None:
    result = kpis(_balance([1.0], {"a": [0.5]}))

    for field in dataclasses.fields(result):
        assert type(getattr(result, field.name)) is float, field.name


def test_pv_zero_gives_defined_ratios() -> None:
    result = kpis(_balance([0.0, 0.0], {"a": [1.0, 0.5]}))

    assert result.self_consumption_ratio == 0.0
    assert result.autarky_ratio == 0.0
    assert result.grid_import_kwh == pytest.approx(1.5)


def test_load_zero_gives_defined_ratios() -> None:
    result = kpis(_balance([2.0, 1.0], {"a": [0.0, 0.0]}))

    assert result.self_consumption_ratio == 0.0
    assert result.autarky_ratio == 0.0
    assert result.export_kwh == pytest.approx(3.0)


def test_pv_and_load_zero_gives_defined_ratios() -> None:
    result = kpis(_balance([0.0], {"a": [0.0]}))

    assert not math.isnan(result.self_consumption_ratio)
    assert result.self_consumption_ratio == 0.0
    assert result.autarky_ratio == 0.0


def test_pv_equals_load_full_ratios() -> None:
    result = kpis(_balance([1.0, 0.5], {"a": [1.0, 0.5]}))

    assert result.self_consumption_ratio == pytest.approx(1.0)
    assert result.autarky_ratio == pytest.approx(1.0)


def test_missing_balance_column_rejected() -> None:
    balance = _balance([1.0], {"a": [1.0]}).drop(columns=["shared_kwh"])
    with pytest.raises(BalanceInputError, match="shared_kwh"):
        kpis(balance)
    with pytest.raises(BalanceInputError, match="shared_kwh"):
        monthly_kpis(balance)


def test_invalid_frame_rejected() -> None:
    balance = _balance([1.0, 1.0], {"a": [1.0, 1.0]})
    balance.index = pd.DatetimeIndex(balance.index).tz_localize(None)
    with pytest.raises(FrameValidationError):
        kpis(balance)


def _year_balance() -> pd.DataFrame:
    index = year_index(2023)
    hours = index.hour.to_numpy()
    # PV only 10-14 h in summer months (Apr-Sep), constant load: winter months have no PV.
    summer = (index.month >= 4) & (index.month <= 9)
    pv = ((hours >= 10) & (hours < 14) & summer).astype(float) * 2.0
    load = [0.5] * len(index)
    return community_balance(
        pd.DataFrame({"pv_kwh": pv}, index=index),
        {"a": pd.DataFrame({"load_kwh": load}, index=index)},
    )


def test_monthly_kpis_twelve_rows_for_2023() -> None:
    monthly = monthly_kpis(_year_balance())

    assert len(monthly) == 12
    assert tuple(monthly.columns) == MONTHLY_KPI_COLUMNS
    assert isinstance(monthly.index, pd.DatetimeIndex)
    assert monthly.index.name == "month"
    assert str(monthly.index.tz) == TIMEZONE
    assert list(monthly.index.month) == list(range(1, 13))
    assert (monthly.index.year == 2023).all()
    assert not monthly.isna().any().any()


def test_monthly_kpis_sum_up_to_total() -> None:
    balance = _year_balance()
    monthly = monthly_kpis(balance)
    total = kpis(balance)

    for column in BALANCE_COLUMNS:
        assert monthly[column].sum() == pytest.approx(getattr(total, column))


def test_monthly_values_per_month() -> None:
    monthly = monthly_kpis(_year_balance())
    january = monthly.iloc[0]
    june = monthly.iloc[5]

    # January: 31 days * 96 intervals * 0.5 kWh load, no PV -> ratios defined as 0.0
    assert january["load_kwh"] == pytest.approx(31 * 96 * 0.5)
    assert january["pv_kwh"] == 0.0
    assert january["self_consumption_ratio"] == 0.0
    assert january["autarky_ratio"] == 0.0
    # June: 30 days * 16 PV intervals; per PV interval pv 2.0, shared 0.5
    assert june["pv_kwh"] == pytest.approx(30 * 16 * 2.0)
    assert june["shared_kwh"] == pytest.approx(30 * 16 * 0.5)
    assert june["self_consumption_ratio"] == pytest.approx(0.25)
    assert june["autarky_ratio"] == pytest.approx(16 / 96)


def test_monthly_kpis_march_with_dst_counts_all_intervals() -> None:
    monthly = monthly_kpis(_year_balance())

    # March 2023 has 31 days with one 23-hour day (DST start) -> 31 * 96 - 4 intervals.
    assert monthly.iloc[2]["load_kwh"] == pytest.approx((31 * 96 - 4) * 0.5)
    assert monthly.iloc[9]["load_kwh"] == pytest.approx((31 * 96 + 4) * 0.5)


def test_monthly_kpis_partial_period() -> None:
    monthly = monthly_kpis(_balance([1.0, 0.0], {"a": [0.5, 0.5]}))

    assert len(monthly) == 1
    assert monthly.index[0] == pd.Timestamp("2023-06-01", tz=TIMEZONE)
    assert monthly.iloc[0]["self_consumption_ratio"] == pytest.approx(0.5)
    assert monthly.iloc[0]["autarky_ratio"] == pytest.approx(0.5)
