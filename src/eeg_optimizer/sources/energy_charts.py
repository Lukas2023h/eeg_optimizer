"""CO2 intensity of Austrian electricity generation from the Energy-Charts API.

Energy-Charts (Fraunhofer ISE) has **no** CO2 endpoint for Austria (checked against
``https://api.energy-charts.info/openapi.json``, 2026-11-18). The intensity is therefore
computed from the generation mix:

``co2_g_per_kwh[t] = sum_i P_i[t] * f_i / sum_i P_i[t]``

with ``P_i`` the net public generation of type ``i`` in MW and ``f_i`` the emission factor
in g CO2-eq/kWh from :data:`EMISSION_FACTORS_G_PER_KWH` (MW cancel out, no unit conversion).

API: ``GET https://api.energy-charts.info/public_power?country=at&start=<UTC>&end=<UTC>``
(no key). ``start``/``end`` are ISO strings (``2023-01-01T00:00Z``), both **inclusive**.
The response is ``{"unix_seconds": [...], "production_types": [{"name", "data"}, ...],
"deprecated": bool}``. For AT, 2023 is delivered in 15-minute resolution (timestamp =
interval start, values = mean power in MW), so the intensity is computed directly per
15-minute interval. This is finer than the "hourly" intensity in spec §7.2 and contains it.

"Without imports" (spec §7.2): only domestic generation counts. Excluded are
``Cross border electricity trading``, ``Hydro pumped storage consumption`` and the
non-generation series ``Load``, ``Residual load`` and the renewable shares. Pumped-storage
**generation** counts with the hydro factor, although part of it was pumped with imported
power (simplification). Unknown production types, missing values (``null``), negative
generation, gaps or duplicate timestamps raise :class:`EnergyChartsError`; nothing is filled.

A local calendar year is loaded with **one** request and cached as
``energy_charts/co2-<FACTORS_VERSION>-<year>``. Monthly chunks (as for aWATTar) ran into
the API rate limit (HTTP 429 after ~8 requests); one year (~35 000 rows) is fine. Changing a
factor requires a new :data:`FACTORS_VERSION` so stale cache entries are not reused.

Signature note: architecture.md §4 lists ``load_co2_intensity(year, cache)``. Here ``year``
defaults to 2023, ``cache`` is optional (default: :func:`default_cache`) and a keyword-only
``fetch`` hook allows offline tests. Positional use stays compatible, no adapter needed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

import numpy as np
import pandas as pd
import requests

from eeg_optimizer.sources.cache import Cache, default_cache
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import INTERVAL, TIMEZONE, validate_frame

SOURCE: Final = "energy_charts"
COLUMN: Final = "co2_g_per_kwh"
API_URL: Final = "https://api.energy-charts.info/public_power"
COUNTRY: Final = "at"
HTTP_TIMEOUT_S: Final = 120.0
"""Timeout for one year request (35 040 rows x 16 series, a few MB of JSON)."""

FACTORS_VERSION: Final = "ipcc-ar5-v1"

IPCC_AR5_G_PER_KWH: Final[Mapping[str, float]] = {
    "Hydro Run-of-River": 24.0,
    "Hydro water reservoir": 24.0,
    "Hydro pumped storage": 24.0,
    "Wind onshore": 11.0,
    "Wind offshore": 12.0,
    "Solar": 41.0,
    "Biomass": 230.0,
    "Geothermal": 38.0,
    "Nuclear": 12.0,
    "Fossil gas": 490.0,
    "Fossil hard coal": 820.0,
    "Fossil brown coal / lignite": 820.0,
}
"""Life-cycle medians, IPCC AR5 WG III (2014), Annex III, Table A.III.2 (g CO2-eq/kWh).

Hydro = "Hydropower" for all three hydro types, Solar = "PV rooftop" (Austrian PV is mostly
rooftop), Biomass = "Biomass - dedicated", Fossil gas = "Gas - Combined Cycle",
coal = "Coal - PC".
"""

ASSUMED_G_PER_KWH: Final[Mapping[str, float]] = {
    "Fossil oil": 650.0,
    "Fossil coal-derived gas": 820.0,
    "Waste": 700.0,
    "Others": 700.0,
}
"""Types without an IPCC AR5 category (own assumptions, see spec §7.2).

Oil 650 g/kWh is a common value for oil-fired plants; coal-derived gas (steel works) is
counted like coal; waste and unspecified "Others" get a conservative fossil-mix value of
700 g/kWh. In AT 2023 waste and others are ~1.8 % of generation.
"""

EMISSION_FACTORS_G_PER_KWH: Final[Mapping[str, float]] = {
    **IPCC_AR5_G_PER_KWH,
    **ASSUMED_G_PER_KWH,
}

NON_GENERATION: Final = frozenset(
    {
        "Cross border electricity trading",
        "Hydro pumped storage consumption",
        "Load",
        "Residual load",
        "Renewable share of load",
        "Renewable share of generation",
    }
)
"""Series in the response that are not domestic generation and are ignored."""

_INTERVAL_S: Final = int(INTERVAL.total_seconds())

Fetcher = Callable[[str, str], Any]
"""``(start, end)`` as inclusive UTC ISO strings -> decoded JSON payload."""


class EnergyChartsError(ValueError):
    """Energy-Charts data is incomplete or malformed, or the request is invalid."""


def _grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """15-minute grid ``[start, end)`` in Europe/Vienna (built in UTC, DST-safe)."""
    return pd.date_range(
        start.tz_convert("UTC"),
        end.tz_convert("UTC"),
        freq=pd.Timedelta(INTERVAL),
        inclusive="left",
    ).tz_convert(TIMEZONE)


def _api_time(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%MZ")


def fetch_public_power(start: str, end: str) -> Any:
    """HTTP request for AT public net generation in ``[start, end]``; returns the JSON."""
    response = requests.get(
        API_URL,
        params={"country": COUNTRY, "start": start, "end": end},
        timeout=HTTP_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()


def _stamp(seconds: int) -> str:
    return pd.Timestamp(seconds, unit="s", tz="UTC").tz_convert(TIMEZONE).isoformat()


def _timestamps(payload: Any) -> list[int]:
    if not isinstance(payload, dict):
        raise EnergyChartsError("Energy-Charts-Antwort ist kein Objekt")
    stamps = payload.get("unix_seconds")
    if not isinstance(stamps, list):
        raise EnergyChartsError("Energy-Charts-Antwort hat kein Feld 'unix_seconds' (Liste)")
    for s in stamps:
        if isinstance(s, bool) or not isinstance(s, int):
            raise EnergyChartsError(f"Energy-Charts-Zeitstempel ist keine ganze Sekunde: {s!r}")
    return stamps


def _generation(payload: dict[str, Any], stamps: list[int]) -> dict[str, np.ndarray]:
    """Generation series per production type in MW (only types with an emission factor)."""
    types = payload.get("production_types")
    if not isinstance(types, list):
        raise EnergyChartsError("Energy-Charts-Antwort hat kein Feld 'production_types' (Liste)")
    n = len(stamps)
    series: dict[str, np.ndarray] = {}
    for entry in types:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise EnergyChartsError(f"Energy-Charts-Erzeugungsart ohne Namen: {entry!r}")
        name: str = entry["name"]
        if name in NON_GENERATION:
            continue
        if name not in EMISSION_FACTORS_G_PER_KWH:
            raise EnergyChartsError(
                f"Unbekannte Energy-Charts-Erzeugungsart {name!r}: kein Emissionsfaktor "
                "hinterlegt. Faktor in EMISSION_FACTORS_G_PER_KWH ergänzen (mit Quelle)."
            )
        if name in series:
            raise EnergyChartsError(f"Energy-Charts-Erzeugungsart doppelt: {name!r}")
        data = entry.get("data")
        if not isinstance(data, list) or len(data) != n:
            raise EnergyChartsError(
                f"Energy-Charts-Reihe {name!r} hat nicht {n} Werte wie 'unix_seconds'"
            )
        for i, value in enumerate(data):
            if value is None:
                raise EnergyChartsError(
                    f"Energy-Charts-Wert fehlt: {name!r} bei {_stamp(stamps[i])}. "
                    "Es wird nicht aufgefüllt."
                )
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise EnergyChartsError(
                    f"Energy-Charts-Wert ist keine Zahl: {name!r} bei {_stamp(stamps[i])}: "
                    f"{value!r}"
                )
        values = np.asarray(data, dtype=float)
        bad = ~np.isfinite(values) | (values < 0)
        if bad.any():
            i = int(np.argmax(bad))
            raise EnergyChartsError(
                f"Energy-Charts-Erzeugung ungültig (nicht endlich oder negativ): {name!r} bei "
                f"{_stamp(stamps[i])}: {values[i]}"
            )
        series[name] = values
    if not series:
        raise EnergyChartsError("Energy-Charts-Antwort enthält keine Erzeugungsart")
    return series


def intensity_from_generation(generation_mw: Mapping[str, np.ndarray]) -> np.ndarray:
    """Generation-weighted mean emission factor per interval in g CO2-eq/kWh.

    Raises:
        EnergyChartsError: for a type without factor or an interval with zero generation.
    """
    unknown = sorted(set(generation_mw) - set(EMISSION_FACTORS_G_PER_KWH))
    if unknown:
        raise EnergyChartsError(f"Kein Emissionsfaktor für {unknown}")
    total = np.sum(list(generation_mw.values()), axis=0)
    weighted = np.sum(
        [values * EMISSION_FACTORS_G_PER_KWH[name] for name, values in generation_mw.items()],
        axis=0,
    )
    if (total <= 0).any():
        raise EnergyChartsError(
            f"Gesamterzeugung 0 MW in Intervall {int(np.argmax(total <= 0))}: "
            "CO2-Intensität nicht definiert"
        )
    return np.asarray(weighted / total, dtype=float)


def parse_public_power(payload: Any, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """15-min ``co2_g_per_kwh`` in Europe/Vienna covering exactly ``[start, end)``.

    Raises:
        EnergyChartsError: on a malformed payload, unknown production types, missing or
            negative values, timestamps off the 15-min grid, duplicates, rows outside the
            range or missing intervals (gaps are never filled).
    """
    stamps = _timestamps(payload)
    expected = _grid(start, end)
    if len(expected) == 0:
        raise EnergyChartsError(f"Leerer Zeitraum {start.isoformat()} bis {end.isoformat()}")
    off_grid = [s for s in stamps if s % _INTERVAL_S != 0]
    if off_grid:
        raise EnergyChartsError(
            f"Energy-Charts-Zeitstempel nicht im 15-min-Raster, erster {_stamp(off_grid[0])}. "
            "Unterstützt ist nur 15-min-Auflösung."
        )
    present: set[int] = set()
    for s in stamps:
        if s in present:
            raise EnergyChartsError(f"Energy-Charts-Zeitstempel doppelt: {_stamp(s)}")
        present.add(s)
    expected_s = [int(ts.value // 1_000_000_000) for ts in expected]
    outside = sorted(present - set(expected_s))
    if outside:
        raise EnergyChartsError(
            f"Energy-Charts lieferte {len(outside)} Intervall(e) außerhalb von "
            f"{start.isoformat()} bis {end.isoformat()}, erstes {_stamp(outside[0])}"
        )
    missing = [s for s in expected_s if s not in present]
    if missing:
        raise EnergyChartsError(
            f"Energy-Charts-Daten lückenhaft: {len(missing)} von {len(expected_s)} Intervallen "
            f"fehlen zwischen {start.isoformat()} und {end.isoformat()}, erstes fehlendes "
            f"{_stamp(missing[0])}. Es wird nicht aufgefüllt."
        )

    generation = _generation(payload, stamps)
    order = np.argsort(np.asarray(stamps))
    intensity = intensity_from_generation({k: v[order] for k, v in generation.items()})
    return pd.DataFrame({COLUMN: intensity}, index=expected)


def cache_key(year: int) -> str:
    """Cache key of one year; contains :data:`FACTORS_VERSION`."""
    return f"co2-{FACTORS_VERSION}-{year}"


def load_co2_intensity(
    year: int = 2023, *, cache: Cache | None = None, fetch: Fetcher = fetch_public_power
) -> pd.DataFrame:
    """CO2 intensity of Austrian generation (no imports) as 15-min ``co2_g_per_kwh``.

    Loads the whole local year with **one** request (cached). A second call does no HTTP
    request.

    Raises:
        EnergyChartsError: if Energy-Charts data is incomplete or malformed.
        requests.HTTPError / requests.ConnectionError / requests.Timeout: if Energy-Charts
            cannot be reached (HTTP 429 = rate limit, later retry).
    """
    index = year_index(year)
    start = pd.Timestamp(index[0])
    end = pd.Timestamp(index[-1]) + INTERVAL

    def fetch_fn() -> pd.DataFrame:
        payload = fetch(_api_time(start), _api_time(pd.Timestamp(index[-1])))
        return parse_public_power(payload, start, end)

    store = cache if cache is not None else default_cache()
    key = cache_key(year)
    frame = store.get_or_fetch(SOURCE, key, fetch_fn)
    validate_frame(frame)
    if list(frame.columns) != [COLUMN] or not frame.index.equals(index):
        raise EnergyChartsError(
            f"Cache-Eintrag {SOURCE}/{key} passt nicht zum Jahr {year} bzw. zur Spalte "
            f"{COLUMN!r}. Datei {store.path(SOURCE, key)} löschen, um neu zu laden."
        )
    return frame


__all__ = [
    "API_URL",
    "ASSUMED_G_PER_KWH",
    "COLUMN",
    "EMISSION_FACTORS_G_PER_KWH",
    "FACTORS_VERSION",
    "HTTP_TIMEOUT_S",
    "IPCC_AR5_G_PER_KWH",
    "NON_GENERATION",
    "EnergyChartsError",
    "cache_key",
    "fetch_public_power",
    "intensity_from_generation",
    "load_co2_intensity",
    "parse_public_power",
]
