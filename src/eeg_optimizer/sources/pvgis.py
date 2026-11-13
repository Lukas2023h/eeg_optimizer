"""PV generation from PVGIS (via ``pvlib.iotools.get_pvgis_hourly``).

PVGIS time-stamp convention (``outputs.hourly[].time``, e.g. ``20230620:1010``):

* The time zone is **UTC**, not local time.
* Each row describes one hour. ``P`` is the mean PV power in **W** over that hour.
* The minutes (``:10`` for SARAH3) are not the interval start: they mark the moment of
  the satellite image inside the hour, i.e. a point inside the interval (roughly its
  middle), not its beginning or end.

So the row stamped ``HH:10 UTC`` belongs to the interval ``[HH:00, HH+1:00) UTC``. We floor
the stamp to the full hour (interval start, as required by the internal format), convert
mean power over one hour into energy (W -> kW -> kWh via :mod:`eeg_optimizer.units`),
convert to Europe/Vienna and split each hour evenly onto four 15-minute intervals (spec
§7). Energy is conserved exactly.

Because PVGIS years are UTC years, the Vienna year starts 1 h (CET) before the first PVGIS
row. :func:`map_to_year` therefore moves the last UTC hour of the year (a night hour on
31 Dec) to the front, so a full PVGIS year fills exactly the local year and keeps its
annual total.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd
from pvlib.iotools import get_pvgis_hourly

from eeg_optimizer.sources.cache import Cache, default_cache
from eeg_optimizer.timeseries import HOUR, hourly_energy_to_15min, year_index
from eeg_optimizer.units import INTERVAL, TIMEZONE, kw_to_kwh, validate_frame, w_to_kw

SOURCE: Final = "pvgis"
COLUMN: Final = "pv_kwh"
DEFAULT_LOSS_PCT: Final = 14.0
"""System losses in percent (PVGIS recommends 14 % for crystalline Si)."""

PVGIS_POWER_COLUMN: Final = "P"
"""Mean PV power in W over the hour (pvlib keeps the PVGIS name)."""

HTTP_TIMEOUT_S: Final = 60.0
"""Timeout for the PVGIS request (a full year is ~1 MB of JSON)."""

# pvlib uses 180 deg = south, PVGIS ("aspect") and this project use 0 deg = south.
_PVLIB_SOUTH_DEG: Final = 180.0


class PvgisError(ValueError):
    """PVGIS data cannot be mapped onto the internal grid, or parameters are invalid."""


def hourly_to_15min(raw: pd.DataFrame) -> pd.DataFrame:
    """Convert PVGIS hourly rows (UTC, stamp inside the hour, ``P`` in W) to 15-min ``pv_kwh``.

    Works for any gapless range of whole hours (e.g. a few days from a fixture). The result
    is in Europe/Vienna with interval-start stamps; its sum equals ``sum(P) / 1000`` kWh.

    Raises:
        PvgisError: on a missing ``P`` column, a non-UTC index, inconsistent minute offsets,
            gaps, NaN or negative power.
    """
    if PVGIS_POWER_COLUMN not in raw.columns:
        raise PvgisError(f"PVGIS-Daten ohne Spalte {PVGIS_POWER_COLUMN!r} (PV-Leistung in W)")
    index = raw.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise PvgisError("PVGIS-Daten brauchen einen zeitzonenbehafteten DatetimeIndex (UTC)")
    if len(index) == 0:
        raise PvgisError("PVGIS-Daten sind leer")
    utc = index.tz_convert("UTC")
    # Stamp lies inside the hour (see module docstring) -> floor to the interval start.
    starts = utc.floor(pd.Timedelta(HOUR))
    offsets = utc - starts
    if (offsets != offsets[0]).any():
        raise PvgisError(
            "PVGIS-Zeitstempel haben unterschiedliche Minuten-Versätze "
            f"({sorted({str(o) for o in offsets})}); Stundenzuordnung wäre geraten"
        )
    power_w = pd.to_numeric(raw[PVGIS_POWER_COLUMN], errors="raise").to_numpy(dtype=float)
    bad = ~np.isfinite(power_w) | (power_w < 0)
    if bad.any():
        pos = int(np.flatnonzero(bad)[0])
        raise PvgisError(
            f"PVGIS-Leistung ungültig (NaN/inf/negativ) bei {index[pos].isoformat()}: "
            f"{power_w[pos]}"
        )

    # Mean power over one hour -> energy of that hour (1 W for 1 h = 0.001 kWh).
    kwh_per_w = kw_to_kwh(w_to_kw(1.0), HOUR)
    hourly = pd.DataFrame(
        {COLUMN: power_w * kwh_per_w},
        index=pd.DatetimeIndex(starts).tz_convert(TIMEZONE),
    )
    # validate_frame (inside hourly_energy_to_15min) rejects gaps and duplicates.
    return hourly_energy_to_15min(hourly)


def map_to_year(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    """Map a 15-min frame covering exactly the UTC year ``year`` onto the local year.

    The local (Europe/Vienna) year begins before the UTC year, so the trailing intervals
    that fall into ``year + 1`` locally (night of 31 Dec) are moved to the start. The
    annual total is unchanged.

    Raises:
        PvgisError: if ``frame`` does not cover exactly the UTC year ``year``.
    """
    target = year_index(year)
    utc_start = pd.Timestamp(year=year, month=1, day=1, tz="UTC")
    utc_end = pd.Timestamp(year=year + 1, month=1, day=1, tz="UTC")
    expected = pd.date_range(utc_start, utc_end, freq=pd.Timedelta(INTERVAL), inclusive="left")
    actual = pd.DatetimeIndex(frame.index).tz_convert("UTC")
    if len(actual) != len(expected) or not (actual == expected).all():
        raise PvgisError(
            f"PVGIS-Daten decken nicht genau das UTC-Jahr {year} ab "
            f"({len(actual)} statt {len(expected)} Viertelstunden, "
            f"{actual[0] if len(actual) else '-'} bis {actual[-1] if len(actual) else '-'})"
        )
    shift = int((utc_start - target[0].tz_convert("UTC")) / pd.Timedelta(INTERVAL))
    values = np.roll(frame.to_numpy(dtype=float), shift, axis=0)
    out = pd.DataFrame(values, index=target, columns=frame.columns)
    validate_frame(out)
    return out


def cache_key(
    *,
    kwp: float,
    tilt_deg: float,
    azimuth_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    year: int,
    loss_pct: float,
) -> str:
    """Cache key that changes whenever any request parameter changes.

    Values are written exactly (``repr``), not rounded, so two requests that differ only
    in a decimal place never share a cache entry.
    """

    def num(value: float) -> str:
        return repr(float(value))

    return (
        f"y{year}_lat{num(latitude_deg)}_lon{num(longitude_deg)}_kwp{num(kwp)}"
        f"_tilt{num(tilt_deg)}_az{num(azimuth_deg)}_loss{num(loss_pct)}"
    )


def _check_params(
    *,
    kwp: float,
    tilt_deg: float,
    azimuth_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    loss_pct: float,
) -> None:
    # Comparisons with NaN are False, so NaN fails every range check below.
    checks = (
        (np.isfinite(kwp) and kwp > 0, f"kWp muss > 0 sein, ist {kwp}"),
        (-180 <= longitude_deg <= 180, f"Längengrad ungültig: {longitude_deg}"),
        (0 <= tilt_deg <= 90, f"Neigung muss zwischen 0 und 90° liegen, ist {tilt_deg}"),
        (
            -180 <= azimuth_deg <= 180,
            f"Ausrichtung muss zwischen -180 und 180° liegen (0 = Süd), ist {azimuth_deg}",
        ),
        (-90 <= latitude_deg <= 90, f"Breitengrad ungültig: {latitude_deg}"),
        (0 <= loss_pct < 100, f"Systemverluste müssen in [0, 100) % liegen, sind {loss_pct}"),
    )
    for ok, message in checks:
        if not ok:
            raise PvgisError(message)


def fetch_pvgis_hourly(
    *,
    kwp: float,
    tilt_deg: float,
    azimuth_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    year: int,
    loss_pct: float,
) -> pd.DataFrame:
    """Call PVGIS (HTTP) for one UTC year and return pvlib's hourly frame (``P`` in W)."""
    result: Any = get_pvgis_hourly(
        latitude_deg,
        longitude_deg,
        start=year,
        end=year,
        surface_tilt=tilt_deg,
        # Our azimuth: 0 = south (PVGIS "aspect"). pvlib expects 180 = south and
        # subtracts 180 itself before calling PVGIS.
        surface_azimuth=azimuth_deg + _PVLIB_SOUTH_DEG,
        pvcalculation=True,
        peakpower=kwp,
        loss=loss_pct,
        components=False,
        outputformat="json",
        timeout=HTTP_TIMEOUT_S,
    )
    data = result[0]
    if not isinstance(data, pd.DataFrame):
        raise PvgisError(f"pvlib lieferte {type(data).__name__} statt DataFrame")
    return data


def load_pv(
    *,
    kwp: float,
    tilt_deg: float,
    azimuth_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    year: int = 2023,
    loss_pct: float = DEFAULT_LOSS_PCT,
    cache: Cache | None = None,
) -> pd.DataFrame:
    """PV generation of one plant for ``year`` as 15-min ``pv_kwh`` in Europe/Vienna.

    ``azimuth_deg`` follows PVGIS: 0 = south, -90 = east, 90 = west. The converted frame
    is cached, so a second call with the same parameters does no HTTP request.

    Raises:
        PvgisError: on invalid parameters or unusable PVGIS data.
        requests.HTTPError / requests.ConnectionError: if PVGIS cannot be reached.
    """
    params: dict[str, Any] = {
        "kwp": kwp,
        "tilt_deg": tilt_deg,
        "azimuth_deg": azimuth_deg,
        "latitude_deg": latitude_deg,
        "longitude_deg": longitude_deg,
        "year": year,
        "loss_pct": loss_pct,
    }
    _check_params(**{k: v for k, v in params.items() if k != "year"})

    def fetch() -> pd.DataFrame:
        raw = fetch_pvgis_hourly(**params)
        return map_to_year(hourly_to_15min(raw), year)

    store = cache if cache is not None else default_cache()
    key = cache_key(**params)
    frame = store.get_or_fetch(SOURCE, key, fetch)
    validate_frame(frame)
    if list(frame.columns) != [COLUMN] or not frame.index.equals(year_index(year)):
        raise PvgisError(
            f"Cache-Eintrag {SOURCE}/{key} passt nicht zum Jahr {year} bzw. zur Spalte "
            f"{COLUMN!r}. Datei {store.path(SOURCE, key)} löschen, um neu zu laden."
        )
    return frame


__all__ = [
    "COLUMN",
    "DEFAULT_LOSS_PCT",
    "PvgisError",
    "cache_key",
    "fetch_pvgis_hourly",
    "hourly_to_15min",
    "load_pv",
    "map_to_year",
]
