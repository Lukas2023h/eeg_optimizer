"""Deterministic synthetic time series in the internal format (F1-04).

Offline replacement for the real sources (PVGIS, HTW load profiles, spot prices, CO2
intensity). Used by tests, the performance test and a demo mode without internet. The
series are plausible, not realistic: they reproduce day/night, season, weekday and noise,
nothing more.

Every function takes a ``seed``; the same arguments always give identical frames. Each
quantity draws from its own random stream (``[seed, stream id]``), so e.g. the PV series
for seed 1 does not change when the load generator changes. Frames cover a whole calendar
year on the internal 15-minute grid and pass :func:`units.validate_frame`.
"""

from __future__ import annotations

from typing import Final

import numpy as np
import pandas as pd

from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import INTERVAL, KW, KWh, kw_to_kwh, validate_frame

DEFAULT_YEAR: Final = 2023
DEFAULT_LATITUDE_DEG: Final = 48.31
"""Linz (spec example community)."""
DEFAULT_LONGITUDE_DEG: Final = 14.29

PERFORMANCE_RATIO: Final = 0.85
"""Share of the clear-sky module output that reaches the meter (inverter, cabling, heat)."""

LOAD_KWH_RANGE: Final = (2500.0, 4500.0)
"""Annual consumption drawn per household when ``annual_kwh`` is not given."""

_STREAM_PV: Final = 1
_STREAM_LOAD: Final = 2
_STREAM_SPOT: Final = 3
_STREAM_CO2: Final = 4
_STREAM_LOAD_SIZE: Final = 5

_DAYS_PER_YEAR: Final = 365.25
_MIN_CO2_G_PER_KWH: Final = 30.0
_LOAD_SEASONAL_AMPLITUDE: Final = 0.3
"""Relative winter surplus / summer deficit of household consumption."""


class SyntheticDataError(ValueError):
    """Raised for parameters that cannot produce a meaningful series."""


def _rng(seed: int, stream: int) -> np.random.Generator:
    # numpy integers (e.g. seeds taken from a pandas column) are accepted; bool is not.
    if isinstance(seed, bool) or not isinstance(seed, int | np.integer) or seed < 0:
        raise SyntheticDataError(f"Seed muss eine nicht-negative ganze Zahl sein, war {seed!r}")
    return np.random.default_rng([int(seed), stream])


def _frame(index: pd.DatetimeIndex, column: str, values: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame({column: values.astype(float)}, index=index)
    validate_frame(df)
    return df


def _local_hour(index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(index.hour + index.minute / 60.0, dtype=float)


def _day_codes(index: pd.DatetimeIndex) -> tuple[np.ndarray, int]:
    """Running day number (0-based, local calendar days) per interval and number of days."""
    days = np.asarray(index.normalize().tz_localize(None).asi8, dtype=np.int64)
    _, codes = np.unique(days, return_inverse=True)
    return codes, int(codes.max()) + 1


def _winter_weight(index: pd.DatetimeIndex) -> np.ndarray:
    """1 in mid-January, -1 in mid-July (cosine over the year)."""
    doy = np.asarray(index.day_of_year, dtype=float)
    return np.asarray(np.cos(2 * np.pi * (doy - 15) / _DAYS_PER_YEAR), dtype=float)


def _sin_solar_elevation(
    index: pd.DatetimeIndex, latitude_deg: float, longitude_deg: float
) -> np.ndarray:
    """Sine of the solar elevation at the middle of each interval (simple astronomy)."""
    mid = index.tz_convert("UTC") + pd.Timedelta(INTERVAL) / 2
    doy = np.asarray(mid.day_of_year, dtype=float)
    utc_hour = np.asarray(mid.hour + mid.minute / 60.0 + mid.second / 3600.0, dtype=float)
    b = 2 * np.pi * (doy - 81) / 364
    equation_of_time_h = (9.87 * np.sin(2 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)) / 60
    solar_time_h = utc_hour + longitude_deg / 15 + equation_of_time_h
    hour_angle = np.radians(15 * (solar_time_h - 12))
    declination = np.radians(23.44) * np.sin(2 * np.pi * (284 + doy) / 365)
    lat = np.radians(latitude_deg)
    return np.asarray(
        np.sin(lat) * np.sin(declination) + np.cos(lat) * np.cos(declination) * np.cos(hour_angle),
        dtype=float,
    )


def synthetic_pv(
    *,
    kwp: float,
    seed: int,
    year: int = DEFAULT_YEAR,
    latitude_deg: float = DEFAULT_LATITUDE_DEG,
    longitude_deg: float = DEFAULT_LONGITUDE_DEG,
) -> pd.DataFrame:
    """PV generation ``pv_kwh`` of a ``kwp`` plant, 0 while the sun is below the horizon.

    Output power is ``kwp * PERFORMANCE_RATIO * clearness * sin(elevation)``, so it never
    exceeds ``kwp``. The clearness index is drawn per day (cloudier in winter, never zero).

    Raises:
        SyntheticDataError: if ``kwp`` is not positive, the latitude is outside 0-66° N
            (northern hemisphere only), the longitude is outside ±180°, or the seed is
            invalid.
    """
    if not np.isfinite(kwp) or kwp <= 0:
        raise SyntheticDataError(f"kWp muss positiv sein, war {kwp!r}")
    # Northern hemisphere only: the seasonal weights assume winter in January.
    if not 0.0 <= latitude_deg <= 66.0:
        raise SyntheticDataError(f"Breitengrad muss zwischen 0 und 66 liegen, war {latitude_deg!r}")
    if not -180.0 <= longitude_deg <= 180.0:
        raise SyntheticDataError(
            f"Längengrad muss zwischen -180 und 180 liegen, war {longitude_deg!r}"
        )
    rng = _rng(seed, _STREAM_PV)
    index = year_index(year)
    codes, n_days = _day_codes(index)
    # Daily clearness index in [0.1, 1]; mean ~0.5 in winter and ~0.7 in summer.
    daily_winter = np.cos(2 * np.pi * (np.arange(n_days) + 1 - 15) / _DAYS_PER_YEAR)
    daily = rng.beta(4.0, 2.0, size=n_days) * (1 - 0.2 * daily_winter)
    clearness = np.clip(daily, 0.1, 1.0)[codes]
    relative = np.clip(_sin_solar_elevation(index, latitude_deg, longitude_deg), 0.0, None)
    power_kw = kwp * PERFORMANCE_RATIO * clearness * relative
    return _frame(index, "pv_kwh", power_kw * kw_to_kwh(KW(1.0)))


def synthetic_load(
    *,
    seed: int,
    annual_kwh: KWh | None = None,
    year: int = DEFAULT_YEAR,
) -> pd.DataFrame:
    """Household consumption ``load_kwh`` with morning/evening peaks and winter surplus.

    The series is scaled so that the year sums exactly to ``annual_kwh``. Without it, the
    annual consumption is drawn from ``LOAD_KWH_RANGE`` (seeded), so different seeds give
    households of different size.

    Raises:
        SyntheticDataError: if ``annual_kwh`` is not positive or the seed is invalid.
    """
    rng = _rng(seed, _STREAM_LOAD)
    if annual_kwh is None:
        # Own stream, so the load shape is the same with and without an explicit value.
        size_rng = _rng(seed, _STREAM_LOAD_SIZE)
        annual_kwh = KWh(float(size_rng.uniform(*LOAD_KWH_RANGE)))
    elif not np.isfinite(annual_kwh) or annual_kwh <= 0:
        raise SyntheticDataError(f"Jahresverbrauch muss positiv sein, war {annual_kwh!r}")
    index = year_index(year)
    hour = _local_hour(index)
    weekend = np.asarray(index.day_of_week >= 5)
    morning_peak = np.where(weekend, 9.0, 7.0)

    def bump(center: np.ndarray | float, width: float, height: float) -> np.ndarray:
        return np.asarray(height * np.exp(-0.5 * ((hour - center) / width) ** 2), dtype=float)

    shape = 0.3 + bump(morning_peak, 1.0, 0.6) + bump(12.5, 1.5, 0.4) + bump(19.0, 2.0, 1.0)
    shape = shape * (1 + _LOAD_SEASONAL_AMPLITUDE * _winter_weight(index))
    shape = shape * rng.lognormal(mean=0.0, sigma=0.35, size=len(index))
    energy = shape / shape.sum() * annual_kwh
    return _frame(index, "load_kwh", energy)


def synthetic_spot_prices(*, seed: int, year: int = DEFAULT_YEAR) -> pd.DataFrame:
    """Day-ahead price ``spot_ct_per_kwh``, constant within each hour, ~10 ct on average.

    Higher in winter and at the morning/evening peaks, lower on weekends and at sunny
    summer middays, where it can turn negative (as in 2023).

    Raises:
        SyntheticDataError: if the seed is invalid.
    """
    rng = _rng(seed, _STREAM_SPOT)
    index = year_index(year)
    codes, n_days = _day_codes(index)
    hour = np.asarray(index.hour, dtype=float)  # hourly product: same price for all quarters
    winter = _winter_weight(index)
    weekend = np.asarray(index.day_of_week >= 5)
    daily_level = (10.0 + rng.normal(0.0, 2.5, size=n_days))[codes]
    peaks = 3.0 * np.exp(-0.5 * ((hour - 8) / 1.5) ** 2) + 4.0 * np.exp(
        -0.5 * ((hour - 19) / 1.5) ** 2
    )
    solar_dip = (4.0 - 3.0 * winter) * np.exp(-0.5 * ((hour - 13) / 2.0) ** 2)
    # The grid starts at a full hour and is gapless in UTC, so 4 quarters = 1 real hour,
    # also on DST days.
    quarters_per_hour = int(pd.Timedelta(hours=1) / pd.Timedelta(INTERVAL))
    hour_codes = np.arange(len(index)) // quarters_per_hour
    hourly_noise = rng.normal(0.0, 1.0, size=int(hour_codes[-1]) + 1)
    price = (
        daily_level + 2.0 * winter + peaks - solar_dip - 2.0 * weekend + hourly_noise[hour_codes]
    )
    return _frame(index, "spot_ct_per_kwh", price)


def synthetic_co2_intensity(*, seed: int, year: int = DEFAULT_YEAR) -> pd.DataFrame:
    """Grid carbon intensity ``co2_g_per_kwh`` around 180 g/kWh, always positive.

    Higher in winter and in the evening, lower at summer middays (solar, hydro).

    Raises:
        SyntheticDataError: if the seed is invalid.
    """
    rng = _rng(seed, _STREAM_CO2)
    index = year_index(year)
    hour = _local_hour(index)
    winter = _winter_weight(index)
    evening = 30.0 * np.exp(-0.5 * ((hour - 19) / 2.0) ** 2)
    solar_dip = (50.0 - 30.0 * winter) * np.exp(-0.5 * ((hour - 13) / 2.5) ** 2)
    intensity = 180.0 + 60.0 * winter + evening - solar_dip + rng.normal(0.0, 10.0, len(index))
    return _frame(index, "co2_g_per_kwh", np.clip(intensity, _MIN_CO2_G_PER_KWH, None))
