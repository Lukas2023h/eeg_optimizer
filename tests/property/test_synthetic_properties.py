"""Property tests for the synthetic generator (F1-04): any seed gives valid, sane frames."""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from eeg_optimizer.sources.synthetic import (
    synthetic_co2_intensity,
    synthetic_load,
    synthetic_pv,
    synthetic_spot_prices,
)
from eeg_optimizer.units import KWh, kwh_to_kw, validate_frame

_seeds = st.integers(min_value=0, max_value=2**32 - 1)


@settings(max_examples=15, deadline=None)
@given(
    seed=_seeds,
    kwp=st.floats(min_value=0.1, max_value=500.0, allow_nan=False, allow_infinity=False),
)
def test_pv_valid_and_bounded_for_any_seed(seed: int, kwp: float) -> None:
    df = synthetic_pv(kwp=kwp, seed=seed)
    validate_frame(df)
    assert kwh_to_kw(KWh(float(df["pv_kwh"].max()))) <= kwp * (1 + 1e-12)
    assert 900 * kwp <= df["pv_kwh"].sum() <= 1300 * kwp


@settings(max_examples=15, deadline=None)
@given(
    seed=_seeds,
    annual=st.floats(min_value=100.0, max_value=50_000.0, allow_nan=False, allow_infinity=False),
)
def test_load_valid_and_sums_to_annual_for_any_seed(seed: int, annual: float) -> None:
    df = synthetic_load(seed=seed, annual_kwh=KWh(annual))
    validate_frame(df)
    assert math.isclose(df["load_kwh"].sum(), annual, rel_tol=1e-9)
    assert (df["load_kwh"] > 0).all()


@settings(max_examples=15, deadline=None)
@given(seed=_seeds)
def test_spot_and_co2_valid_for_any_seed(seed: int) -> None:
    spot = synthetic_spot_prices(seed=seed)
    co2 = synthetic_co2_intensity(seed=seed)
    validate_frame(spot)
    validate_frame(co2)
    assert spot.index.equals(co2.index)
    assert (co2["co2_g_per_kwh"] > 0).all()
    quarters = spot["spot_ct_per_kwh"].to_numpy().reshape(-1, 4)
    assert (quarters == quarters[:, :1]).all()
    assert 5 <= spot["spot_ct_per_kwh"].mean() <= 15
