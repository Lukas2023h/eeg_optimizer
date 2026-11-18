"""Day-ahead spot prices for Austria from the aWATTar API.

API: ``GET https://api.awattar.at/v1/marketdata?start=<ms>&end=<ms>`` (no key needed).
The response is ``{"object": "list", "data": [...], "url": "/at/v1/marketdata"}``; each row
of ``data`` holds

* ``start_timestamp`` / ``end_timestamp``: UTC epoch milliseconds of the delivery hour
  (start = interval start, end exclusive; DST days have 23 resp. 25 rows),
* ``marketprice``: EPEX day-ahead price for the AT bidding zone in ``unit``,
* ``unit``: always ``"Eur/MWh"``.

Prices are rates, so the hourly value is repeated unchanged onto the four 15-minute
intervals (spec §7). EUR/MWh -> ct/kWh via :func:`units.eur_per_mwh_to_ct_per_kwh`.
Negative prices are real and kept. A year is loaded in calendar-month chunks (local time),
each chunk is cached separately as ``awattar/<year>-<month>``. Missing, duplicate or
unexpected hours raise :class:`AwattarError` instead of being filled.

Signature note: architecture.md §4 lists ``load_spot_prices(year, cache)``. Here ``year``
defaults to 2023, ``cache`` is optional (default: :func:`default_cache`) and a keyword-only
``fetch`` hook allows offline tests. Positional use ``load_spot_prices(year, cache=...)``
stays compatible, so no adapter is needed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

import numpy as np
import pandas as pd
import requests

from eeg_optimizer.sources.cache import Cache, default_cache
from eeg_optimizer.timeseries import HOUR, rate_to_15min, year_index
from eeg_optimizer.units import INTERVAL, TIMEZONE, eur_per_mwh_to_ct_per_kwh, validate_frame

SOURCE: Final = "awattar"
COLUMN: Final = "spot_ct_per_kwh"
API_URL: Final = "https://api.awattar.at/v1/marketdata"
HTTP_TIMEOUT_S: Final = 30.0
"""Timeout for one month request (~750 rows, ~70 kB of JSON)."""
API_UNIT: Final = "Eur/MWh"

_MS_PER_HOUR: Final = 3_600_000

Fetcher = Callable[[int, int], Any]
"""``(start_ms, end_ms) -> decoded JSON payload``."""


class AwattarError(ValueError):
    """aWATTar data is incomplete or malformed, or the request parameters are invalid."""


def month_range(year: int, month: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Start (inclusive) and end (exclusive) of a calendar month in Europe/Vienna."""
    if not 1 <= month <= 12:
        raise AwattarError(f"Monat muss zwischen 1 und 12 liegen, ist {month}")
    start = pd.Timestamp(year=year, month=month, day=1, tz=TIMEZONE)
    end = start + pd.DateOffset(months=1)
    return start, pd.Timestamp(end)


def _to_ms(ts: pd.Timestamp) -> int:
    return int(ts.tz_convert("UTC").value // 1_000_000)


def fetch_marketdata(start_ms: int, end_ms: int) -> Any:
    """HTTP request to aWATTar for ``[start_ms, end_ms)``; returns the decoded JSON."""
    response = requests.get(
        API_URL, params={"start": start_ms, "end": end_ms}, timeout=HTTP_TIMEOUT_S
    )
    response.raise_for_status()
    return response.json()


def _rows(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise AwattarError("aWATTar-Antwort hat kein Feld 'data' mit einer Liste von Preisen")
    for row in data:
        if not isinstance(row, dict):
            raise AwattarError(f"aWATTar-Zeile ist kein Objekt: {row!r}")
        missing = {"start_timestamp", "end_timestamp", "marketprice", "unit"} - row.keys()
        if missing:
            raise AwattarError(f"aWATTar-Zeile ohne Feld(er) {sorted(missing)}: {row!r}")
    return data


def _stamp(ms: int) -> str:
    return pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert(TIMEZONE).isoformat()


def parse_marketdata(payload: Any, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Hourly ``spot_ct_per_kwh`` in Europe/Vienna covering exactly ``[start, end)``.

    Raises:
        AwattarError: on a malformed payload, a unit other than EUR/MWh, rows that are not
            whole hours, non-finite prices, duplicates, rows outside the range or missing
            hours (gaps are never filled).
    """
    rows = _rows(payload)
    expected = pd.date_range(
        start.tz_convert("UTC"), end.tz_convert("UTC"), freq=pd.Timedelta(HOUR), inclusive="left"
    )
    if len(expected) == 0:
        raise AwattarError(f"Leerer Zeitraum {start.isoformat()} bis {end.isoformat()}")

    prices: dict[int, float] = {}
    for row in rows:
        begin, finish, price, unit = (
            row["start_timestamp"],
            row["end_timestamp"],
            row["marketprice"],
            row["unit"],
        )
        if unit != API_UNIT:
            raise AwattarError(f"aWATTar-Einheit {unit!r} statt {API_UNIT!r} bei {row!r}")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (begin, finish)):
            raise AwattarError(f"aWATTar-Zeitstempel sind keine ganzen Millisekunden: {row!r}")
        if finish - begin != _MS_PER_HOUR or begin % _MS_PER_HOUR != 0:
            raise AwattarError(
                f"aWATTar-Zeile ist keine volle Stunde ({_stamp(begin)} bis {_stamp(finish)}). "
                "Unterstützt sind nur Stundenprodukte (Day-Ahead bis 30.09.2025)."
            )
        if isinstance(price, bool) or not isinstance(price, int | float):
            raise AwattarError(f"aWATTar-Preis ist keine Zahl bei {_stamp(begin)}: {price!r}")
        if not np.isfinite(price):
            raise AwattarError(f"aWATTar-Preis ungültig bei {_stamp(begin)}: {price}")
        if begin in prices:
            raise AwattarError(f"aWATTar-Stunde doppelt: {_stamp(begin)}")
        prices[begin] = float(price)

    expected_ms = [_to_ms(ts) for ts in expected]
    outside = sorted(set(prices) - set(expected_ms))
    if outside:
        raise AwattarError(
            f"aWATTar lieferte {len(outside)} Stunde(n) außerhalb von {start.isoformat()} bis "
            f"{end.isoformat()}, erste {_stamp(outside[0])}"
        )
    missing = [ms for ms in expected_ms if ms not in prices]
    if missing:
        raise AwattarError(
            f"aWATTar-Daten lückenhaft: {len(missing)} von {len(expected_ms)} Stunden fehlen "
            f"zwischen {start.isoformat()} und {end.isoformat()}, erste fehlende "
            f"{_stamp(missing[0])}. Es wird nicht aufgefüllt."
        )

    values = np.array([prices[ms] for ms in expected_ms], dtype=float)
    return pd.DataFrame(
        {COLUMN: eur_per_mwh_to_ct_per_kwh(values)}, index=expected.tz_convert(TIMEZONE)
    )


def to_15min(hourly: pd.DataFrame) -> pd.DataFrame:
    """Repeat hourly ``spot_ct_per_kwh`` unchanged onto the 15-minute grid."""
    return rate_to_15min(hourly)


def load_month(
    year: int, month: int, *, cache: Cache | None = None, fetch: Fetcher = fetch_marketdata
) -> pd.DataFrame:
    """Spot prices of one local calendar month as 15-min ``spot_ct_per_kwh`` (cached)."""
    start, end = month_range(year, month)

    def fetch_fn() -> pd.DataFrame:
        payload = fetch(_to_ms(start), _to_ms(end))
        return to_15min(parse_marketdata(payload, start, end))

    store = cache if cache is not None else default_cache()
    key = f"{year}-{month:02d}"
    frame = store.get_or_fetch(SOURCE, key, fetch_fn)
    validate_frame(frame)
    expected = pd.date_range(
        start.tz_convert("UTC"),
        end.tz_convert("UTC"),
        freq=pd.Timedelta(INTERVAL),
        inclusive="left",
    ).tz_convert(TIMEZONE)
    if list(frame.columns) != [COLUMN] or not frame.index.equals(expected):
        raise AwattarError(
            f"Cache-Eintrag {SOURCE}/{key} passt nicht zum Monat {key} bzw. zur Spalte "
            f"{COLUMN!r}. Datei {store.path(SOURCE, key)} löschen, um neu zu laden."
        )
    return frame


def load_spot_prices(
    year: int = 2023, *, cache: Cache | None = None, fetch: Fetcher = fetch_marketdata
) -> pd.DataFrame:
    """Austrian day-ahead prices for ``year`` as 15-min ``spot_ct_per_kwh`` in Europe/Vienna.

    Loads twelve monthly chunks (each cached). A second call does no HTTP request.

    Raises:
        AwattarError: if aWATTar data is incomplete or malformed.
        requests.HTTPError / requests.ConnectionError / requests.Timeout: if aWATTar
            cannot be reached.
    """
    months = [load_month(year, m, cache=cache, fetch=fetch) for m in range(1, 13)]
    frame = pd.concat(months)
    if not frame.index.equals(year_index(year)):
        raise AwattarError(f"Monats-Chunks ergeben nicht genau das Jahr {year}")
    validate_frame(frame)
    return frame


__all__ = [
    "API_URL",
    "COLUMN",
    "HTTP_TIMEOUT_S",
    "AwattarError",
    "fetch_marketdata",
    "load_month",
    "load_spot_prices",
    "month_range",
    "parse_marketdata",
    "to_15min",
]
