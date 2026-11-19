"""Unit tests for the baseline analysis behind the page 'Ist-Analyse' (F3-06)."""

import time

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer import analysis
from eeg_optimizer.balance import BalanceInputError, community_balance, kpis
from eeg_optimizer.community import build_inputs
from eeg_optimizer.config import load_config
from eeg_optimizer.config_store import BUILTIN_CONFIG_DIR
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import TIMEZONE, FrameValidationError, KWh, kwh_to_kw

OFFLINE_CONFIG = BUILTIN_CONFIG_DIR / "example_community_offline.yaml"


def _balance(index: pd.DatetimeIndex, pv: list[float], load: list[float]) -> pd.DataFrame:
    return community_balance(
        pd.DataFrame({"pv_kwh": pv}, index=index),
        {"a": pd.DataFrame({"load_kwh": load}, index=index)},
    )


def _two_days() -> pd.DatetimeIndex:
    return pd.date_range("2023-06-01 00:00", periods=2 * 96, freq="15min", tz=TIMEZONE)


def test_daily_profile_mean_power_per_slot() -> None:
    index = _two_days()
    pv = [0.0] * len(index)
    load = [0.25] * len(index)
    # 12:00 slot: 1 kWh on day 1, 0.5 kWh on day 2 -> mean 0.75 kWh = 3 kW
    pv[48] = 1.0
    pv[96 + 48] = 0.5
    profile = analysis.daily_profile(_balance(index, pv, load))

    assert list(profile.columns) == list(analysis.PROFILE_COLUMNS)
    assert profile.index.name == analysis.TIME_OF_DAY_NAME
    assert len(profile) == 96
    assert profile.index[0] == "00:00"
    assert profile.index[-1] == "23:45"
    assert profile.loc["12:00", "pv_kw"] == pytest.approx(3.0)
    assert profile.loc["12:15", "pv_kw"] == pytest.approx(0.0)
    assert np.allclose(profile["load_kw"], 1.0)


def test_daily_profile_full_year_matches_annual_mean() -> None:
    index = year_index(2023)
    rng = np.random.default_rng(0)
    pv = rng.uniform(0, 2, len(index)).tolist()
    load = rng.uniform(0, 1, len(index)).tolist()
    profile = analysis.daily_profile(_balance(index, pv, load))

    assert len(profile) == 96  # DST days do not add or drop slots
    # every slot occurs on ~365 days, so the slot means average to the overall mean power
    assert profile["pv_kw"].mean() == pytest.approx(kwh_to_kw(np.mean(pv)), rel=1e-3)


def test_surplus_by_day_hour_sums_export() -> None:
    index = _two_days()
    pv = [0.0] * len(index)
    load = [0.0] * len(index)
    # day 1, 12:00-12:45: export 0.5 each -> 2 kWh in hour 12; load reduces one interval
    for i in range(48, 52):
        pv[i] = 0.5
    load[48] = 0.2
    surplus = analysis.surplus_by_day_hour(_balance(index, pv, load))

    assert surplus.name == "export_kwh"
    assert surplus.index.names == [analysis.DAY_NAME, analysis.HOUR_NAME]
    assert len(surplus) == 48
    day1 = pd.Timestamp("2023-06-01").date()
    assert surplus.loc[(day1, 12)] == pytest.approx(1.8)
    assert surplus.sum() == pytest.approx(1.8)


def test_surplus_dst_days_keep_total() -> None:
    index = year_index(2023)
    pv = [1.0] * len(index)
    load = [0.25] * len(index)
    bal = _balance(index, pv, load)
    surplus = analysis.surplus_by_day_hour(bal)

    assert surplus.sum() == pytest.approx(kpis(bal).export_kwh)
    spring = pd.Timestamp("2023-03-26").date()
    autumn = pd.Timestamp("2023-10-29").date()
    assert (spring, 2) not in surplus.index  # hour 02:00 does not exist
    assert surplus.loc[(autumn, 2)] == pytest.approx(8 * 0.75)  # 02:00 occurs twice


def test_inputs_are_checked() -> None:
    bal = _balance(_two_days(), [1.0] * 192, [0.5] * 192)
    with pytest.raises(BalanceInputError):
        analysis.daily_profile(bal.drop(columns=["export_kwh"]))
    with pytest.raises(BalanceInputError):
        analysis.surplus_by_day_hour(bal.drop(columns=["pv_kwh"]))
    broken = bal.copy()
    broken.iloc[3, 0] = np.nan
    with pytest.raises(FrameValidationError):
        analysis.daily_profile(broken)


def test_analyze_offline_example() -> None:
    config = load_config(OFFLINE_CONFIG)
    start = time.perf_counter()
    result = analysis.analyze(config, build_inputs(config))
    elapsed = time.perf_counter() - start

    assert len(config.households) == 5
    assert elapsed < 10.0  # page build budget (F3-06), uncached
    assert 0.0 < result.kpis.self_consumption_ratio <= 1.0
    assert 0.0 < result.kpis.autarky_ratio <= 1.0
    assert len(result.monthly) == 12
    assert len(result.profile) == 96
    assert result.surplus.sum() == pytest.approx(result.kpis.export_kwh)
    assert result.balance["shared_kwh"].sum() == pytest.approx(result.kpis.shared_kwh)
    assert np.isfinite(result.costs.community_eur)
    assert result.emissions.community_kg_co2 > 0.0


def test_assumption_notes_mention_htw_and_co2_imports() -> None:
    text = " ".join(analysis.ASSUMPTION_NOTES)
    assert "HTW" in text
    assert "ohne Importe" in text
    assert "perfekter Voraussicht" in text


def test_kwh_to_kw_array() -> None:
    out = kwh_to_kw(np.array([0.25, 1.0]))
    assert np.allclose(out, [1.0, 4.0])
    assert kwh_to_kw(KWh(0.5)) == pytest.approx(2.0)
