"""Unit tests for the synthetic data generator (F1-04)."""

from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.sources.synthetic import (
    LOAD_KWH_RANGE,
    SyntheticDataError,
    synthetic_co2_intensity,
    synthetic_load,
    synthetic_pv,
    synthetic_spot_prices,
)
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import KWh, kwh_to_kw, validate_frame

GENERATORS: dict[str, Callable[[int], pd.DataFrame]] = {
    "pv_kwh": lambda seed: synthetic_pv(kwp=10.0, seed=seed),
    "load_kwh": lambda seed: synthetic_load(seed=seed),
    "spot_ct_per_kwh": lambda seed: synthetic_spot_prices(seed=seed),
    "co2_g_per_kwh": lambda seed: synthetic_co2_intensity(seed=seed),
}


def _idx(series: pd.Series) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(series.index)


@pytest.mark.parametrize("column", sorted(GENERATORS))
def test_frames_for_2023_pass_validation(column: str) -> None:
    df = GENERATORS[column](0)
    validate_frame(df)
    assert list(df.columns) == [column]
    assert df.index.equals(year_index(2023))
    assert len(df) == 35040


@pytest.mark.parametrize("column", sorted(GENERATORS))
def test_same_seed_gives_identical_data(column: str) -> None:
    pd.testing.assert_frame_equal(GENERATORS[column](7), GENERATORS[column](7))


@pytest.mark.parametrize("column", sorted(GENERATORS))
def test_different_seed_gives_different_data(column: str) -> None:
    assert not GENERATORS[column](1)[column].equals(GENERATORS[column](2)[column])


def test_pv_is_zero_at_night_and_positive_at_noon() -> None:
    pv = synthetic_pv(kwp=10.0, seed=3)["pv_kwh"]
    hours = _idx(pv).hour
    night = pv[(hours < 3) | (hours >= 22)]
    assert (night == 0).all()
    noon = pv[(hours == 12) & (_idx(pv).minute == 0)]
    assert len(noon) == 365
    assert (noon > 0).all()


def test_pv_power_never_exceeds_kwp_and_yield_is_plausible() -> None:
    kwp = 10.0
    pv = synthetic_pv(kwp=kwp, seed=4)["pv_kwh"]
    peak_kw = kwh_to_kw(KWh(float(pv.max())))
    assert 0 < peak_kw <= kwp
    # Austria: roughly 900-1300 kWh per kWp and year.
    assert 900 * kwp <= pv.sum() <= 1300 * kwp


def test_pv_scales_linearly_with_kwp() -> None:
    small = synthetic_pv(kwp=5.0, seed=9)["pv_kwh"]
    large = synthetic_pv(kwp=20.0, seed=9)["pv_kwh"]
    np.testing.assert_allclose(large.to_numpy(), 4 * small.to_numpy(), rtol=1e-12)


def test_pv_summer_exceeds_winter() -> None:
    pv = synthetic_pv(kwp=10.0, seed=5)["pv_kwh"]
    months = pv.groupby(_idx(pv).month).sum()
    assert months[6] > 2 * months[12]


@pytest.mark.parametrize("seed", range(10))
def test_household_annual_load_is_plausible(seed: int) -> None:
    total = synthetic_load(seed=seed)["load_kwh"].sum()
    assert 2000 <= total <= 6000
    assert LOAD_KWH_RANGE[0] - 1e-6 <= total <= LOAD_KWH_RANGE[1] + 1e-6


def test_load_respects_given_annual_consumption() -> None:
    load = synthetic_load(seed=1, annual_kwh=KWh(4200.0))["load_kwh"]
    assert load.sum() == pytest.approx(4200.0, rel=1e-12)
    assert (load > 0).all()


def test_load_has_evening_peak_and_winter_surplus() -> None:
    load = synthetic_load(seed=2, annual_kwh=KWh(3500.0))["load_kwh"]
    by_hour = load.groupby(_idx(load).hour).mean()
    assert by_hour[19] > 2 * by_hour[3]
    months = load.groupby(_idx(load).month).sum()
    assert months[1] > months[7]


def test_spot_prices_are_hourly_and_plausible() -> None:
    price = synthetic_spot_prices(seed=1)["spot_ct_per_kwh"]
    quarters = price.to_numpy().reshape(-1, 4)
    assert (quarters == quarters[:, :1]).all()
    assert 5 <= price.mean() <= 15
    assert (price < 0).any()  # sunny summer middays, as in 2023
    by_hour = price.groupby(_idx(price).hour).mean()
    assert by_hour[19] > by_hour[13]


def test_co2_intensity_positive_and_higher_in_winter() -> None:
    co2 = synthetic_co2_intensity(seed=1)["co2_g_per_kwh"]
    assert (co2 > 0).all()
    assert 100 <= co2.mean() <= 300
    months = co2.groupby(_idx(co2).month).mean()
    assert months[1] > months[7]


@pytest.mark.parametrize("year", [2020, 2024])
def test_other_years_including_leap_years(year: int) -> None:
    df = synthetic_load(seed=0, year=year)
    validate_frame(df)
    assert len(df) == 35136


def test_streams_are_independent_per_quantity() -> None:
    """PV for a seed does not depend on how many other series were generated before."""
    first = synthetic_pv(kwp=10.0, seed=11)
    synthetic_load(seed=11)
    synthetic_spot_prices(seed=11)
    pd.testing.assert_frame_equal(first, synthetic_pv(kwp=10.0, seed=11))


def test_numpy_integer_seed_equals_python_int() -> None:
    pd.testing.assert_frame_equal(
        synthetic_load(seed=np.int64(3)),  # type: ignore[arg-type]
        synthetic_load(seed=3),
    )


@pytest.mark.parametrize("seed", [-1, np.int64(-1), 1.5, True, "1"])
def test_invalid_seed_raises(seed: object) -> None:
    with pytest.raises(SyntheticDataError, match="Seed"):
        synthetic_load(seed=seed)  # type: ignore[arg-type]


@pytest.mark.parametrize("kwp", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_kwp_raises(kwp: float) -> None:
    with pytest.raises(SyntheticDataError, match="kWp"):
        synthetic_pv(kwp=kwp, seed=0)


@pytest.mark.parametrize("annual", [0.0, -100.0, float("nan")])
def test_invalid_annual_kwh_raises(annual: float) -> None:
    with pytest.raises(SyntheticDataError, match="Jahresverbrauch"):
        synthetic_load(seed=0, annual_kwh=KWh(annual))


@pytest.mark.parametrize("latitude", [80.0, -10.0])
def test_invalid_latitude_raises(latitude: float) -> None:
    with pytest.raises(SyntheticDataError, match="Breitengrad"):
        synthetic_pv(kwp=1.0, seed=0, latitude_deg=latitude)


def test_invalid_longitude_raises() -> None:
    with pytest.raises(SyntheticDataError, match="Längengrad"):
        synthetic_pv(kwp=1.0, seed=0, longitude_deg=200.0)


def test_load_shape_does_not_depend_on_drawn_annual_kwh() -> None:
    drawn = synthetic_load(seed=6)["load_kwh"]
    explicit = synthetic_load(seed=6, annual_kwh=KWh(1000.0))["load_kwh"]
    np.testing.assert_allclose(
        (drawn / drawn.sum()).to_numpy(), (explicit / explicit.sum()).to_numpy(), rtol=1e-9
    )
