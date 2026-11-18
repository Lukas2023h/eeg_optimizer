"""Property tests for the community energy balance (F3-01, INV-1) and its KPIs (F3-02)."""

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from eeg_optimizer.balance import BALANCE_COLUMNS, community_balance, kpis, monthly_kpis
from eeg_optimizer.units import TIMEZONE, validate_frame

TOLERANCE_KWH = 1e-6

# Any quarter hour of 2023, so DST transitions in Europe/Vienna are covered.
_quarter_starts = st.integers(min_value=0, max_value=365 * 96 - 1).map(
    lambda q: (
        pd.Timestamp("2023-01-01", tz=TIMEZONE).tz_convert("UTC") + pd.Timedelta(minutes=15 * q)
    )
)
# Zeros and small integers are mixed in so that PV == load and empty intervals occur often.
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
def test_balance_holds_per_interval(
    community: tuple[pd.DataFrame, dict[str, pd.DataFrame]],
) -> None:
    pv, loads = community
    out = community_balance(pv, loads)

    validate_frame(out)
    assert tuple(out.columns) == BALANCE_COLUMNS
    assert out.index.equals(pv.index)
    np.testing.assert_allclose(
        out["pv_kwh"], out["shared_kwh"] + out["export_kwh"], rtol=0, atol=TOLERANCE_KWH
    )
    np.testing.assert_allclose(
        out["load_kwh"], out["shared_kwh"] + out["grid_import_kwh"], rtol=0, atol=TOLERANCE_KWH
    )
    assert (out.to_numpy() >= 0).all()


@given(community=_community())
def test_inputs_pass_through_unchanged(
    community: tuple[pd.DataFrame, dict[str, pd.DataFrame]],
) -> None:
    pv, loads = community
    out = community_balance(pv, loads)

    assert out["pv_kwh"].tolist() == pv["pv_kwh"].tolist()
    expected_load = sum(load["load_kwh"].to_numpy() for load in loads.values())
    np.testing.assert_allclose(out["load_kwh"], expected_load, rtol=0, atol=TOLERANCE_KWH)
    # Either nothing is exported or nothing is imported in one interval.
    assert ((out["export_kwh"] == 0) | (out["grid_import_kwh"] == 0)).all()


@given(community=_community())
def test_kpis_ratios_defined_and_bounded(
    community: tuple[pd.DataFrame, dict[str, pd.DataFrame]],
) -> None:
    pv, loads = community
    balance = community_balance(pv, loads)
    result = kpis(balance)

    for ratio in (result.self_consumption_ratio, result.autarky_ratio):
        assert np.isfinite(ratio)
        assert 0.0 <= ratio <= 1.0
    assert result.pv_kwh == pytest.approx(result.shared_kwh + result.export_kwh, abs=1e-6)
    assert result.load_kwh == pytest.approx(result.shared_kwh + result.grid_import_kwh, abs=1e-6)
    if result.pv_kwh > 0:
        assert result.self_consumption_ratio == pytest.approx(result.shared_kwh / result.pv_kwh)
    if result.load_kwh > 0:
        assert result.autarky_ratio == pytest.approx(result.shared_kwh / result.load_kwh)


@given(community=_community())
def test_monthly_kpis_consistent_with_total(
    community: tuple[pd.DataFrame, dict[str, pd.DataFrame]],
) -> None:
    pv, loads = community
    balance = community_balance(pv, loads)
    monthly = monthly_kpis(balance)
    total = kpis(balance)

    assert 1 <= len(monthly) <= 2  # at most 48 intervals -> one month boundary
    assert np.isfinite(monthly.to_numpy()).all()
    ratios = monthly[["self_consumption_ratio", "autarky_ratio"]].to_numpy()
    assert ((ratios >= 0) & (ratios <= 1)).all()
    for column in BALANCE_COLUMNS:
        assert monthly[column].sum() == pytest.approx(getattr(total, column), abs=1e-6)
