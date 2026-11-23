"""Household load from Open Power System Data (OPSD) "Household Data" (supplementary source).

Dataset
-------
Open Power System Data. 2020. Data Package Household Data. Version 2020-04-15.
https://doi.org/10.25832/household_data/2020-04-15 (primary data: CoSSMic project,
households in Konstanz, Germany).

* **Licence:** CC BY 4.0 (attribution). The citation above is the required attribution.
* **File used:** ``household_data_15min_singleindex.csv`` (:data:`DOWNLOAD_URL`), one row
  per quarter hour, data roughly 2014-12 to 2019-05 (coverage differs per household).
* **Layout:** ``utc_timestamp`` (ISO 8601, ``Z``), ``cet_cest_timestamp``, then one column
  per meter named ``DE_KN_<household>_<quantity>`` (e.g. ``DE_KN_residential1_grid_import``,
  ``DE_KN_residential1_dishwasher``) and finally ``interpolated`` (text, ignored). Meter
  columns hold **cumulative** energy in **kWh**; a household's columns are empty before
  its metering starts and after it ends.
* **Household ID:** the prefix ``DE_KN_<household>``, e.g. ``DE_KN_residential1``
  (:func:`household_ids` lists all IDs of a file).

Conventions (assumptions, see progress.md)
------------------------------------------
* A reading at ``utc_timestamp`` ``t`` is the meter state at the instant ``t``. The energy
  of the interval starting at ``t`` is therefore ``reading(t + 15 min) - reading(t)``; the
  last reading starts no interval. Converted to Europe/Vienna, the stamp is the interval
  start as everywhere else in the project.
* Only the span in which **all** meters of the household have readings is used. Leading
  and trailing empty rows are cut; a gap inside that span or a falling meter reading is an
  :class:`OpsdError` (no silent interpolation).
* Household load (``load_kwh``) = ``grid_import + pv - grid_export``, i.e. grid import
  plus self-consumed PV. Households without ``pv`` use ``grid_import`` alone. The
  appliance meters (``<quantity>_kwh``) are sub-meters of that load and are only
  returned for plausibility checks (:func:`load_household_meters`).
* No mapping onto another year: :func:`load_household` with ``year`` returns that
  calendar year only if OPSD covers it completely (Europe/Vienna).

Signature
---------
Deviates from architecture.md §4 (``load_household(cfg: LoadConfig, year, cache)``):
``LoadConfig`` has no ``opsd`` source yet, so the function takes the household ID
directly. A config adapter is listed under "Offene Punkte" in progress.md.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Collection
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
import requests

from eeg_optimizer.sources.cache import Cache, data_dir, default_cache
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import ENERGY_SUFFIX, INTERVAL, TIMEZONE, validate_frame

SOURCE: Final = "opsd"
COLUMN: Final = "load_kwh"
VERSION: Final = "2020-04-15"
CSV_NAME: Final = "household_data_15min_singleindex.csv"
DOWNLOAD_URL: Final = f"https://data.open-power-system-data.org/household_data/{VERSION}/{CSV_NAME}"
HTTP_TIMEOUT_S: Final = (30.0, 300.0)
"""(connect, read) timeout for the download."""

TIMESTAMP_COLUMN: Final = "utc_timestamp"
NON_METER_COLUMNS: Final = frozenset({TIMESTAMP_COLUMN, "cet_cest_timestamp", "interpolated"})
ID_PARTS: Final = 3
"""``DE``, ``KN``, ``<household>``: the household ID is the first three ``_`` parts."""

GRID_IMPORT: Final = "grid_import"
GRID_EXPORT: Final = "grid_export"
PV: Final = "pv"
LOAD_QUANTITIES: Final = frozenset({GRID_IMPORT, GRID_EXPORT, PV})
"""Meters that make up the household load; appliance meters are sub-meters of it."""
ID_PATTERN: Final = re.compile(r"DE_KN_[A-Za-z0-9]+")

NEGATIVE_LOAD_TOLERANCE_KWH: Final = 1e-9
"""``grid_import + pv - grid_export`` down to ``-tol`` is summation noise of differenced
cumulative readings (~1e-12 at 1e4 kWh) and is set to 0; below that it is an error."""


class OpsdError(ValueError):
    """Unknown household ID or OPSD data that cannot be used without guessing."""


def default_csv_path() -> Path:
    """Where the raw download is kept: ``<data_dir>/raw/opsd/<CSV_NAME>``."""
    return data_dir() / "raw" / SOURCE / CSV_NAME


def download_csv(target: Path) -> Path:
    """Download the 15-min CSV to ``target`` (atomic). Returns ``target``.

    Raises:
        requests.HTTPError / requests.ConnectionError: if OPSD cannot be reached.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with (
            os.fdopen(fd, "wb") as out,
            requests.get(DOWNLOAD_URL, stream=True, timeout=HTTP_TIMEOUT_S) as response,
        ):
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1 << 20):
                out.write(chunk)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def _household_of(column: str) -> str | None:
    parts = column.split("_", ID_PARTS)
    if column in NON_METER_COLUMNS or len(parts) <= ID_PARTS or not parts[ID_PARTS]:
        return None
    return "_".join(parts[:ID_PARTS])


def _header(csv_path: Path) -> list[str]:
    columns = [str(c) for c in pd.read_csv(csv_path, nrows=0).columns]
    if TIMESTAMP_COLUMN not in columns:
        raise OpsdError(f"OPSD-Datei {csv_path} hat keine Spalte {TIMESTAMP_COLUMN!r}")
    return columns


def household_ids(csv_path: Path) -> list[str]:
    """All household IDs (``DE_KN_<household>``) in the file, sorted."""
    ids = {h for c in _header(csv_path) if (h := _household_of(c)) is not None}
    return sorted(ids)


def check_household(csv_path: Path, household_id: str) -> list[str]:
    """Meter columns of ``household_id``; :class:`OpsdError` with all IDs if it is unknown."""
    columns = [c for c in _header(csv_path) if _household_of(c) == household_id]
    if not columns:
        known = ", ".join(household_ids(csv_path)) or "keine"
        raise OpsdError(f"Unbekannter OPSD-Haushalt {household_id!r}. Vorhanden: {known}")
    return columns


def read_readings(
    csv_path: Path, household_id: str, quantities: Collection[str] | None = None
) -> pd.DataFrame:
    """Cumulative meter readings (kWh) of one household, columns named by quantity.

    The index is the reading instant in Europe/Vienna. Columns are e.g. ``grid_import``,
    ``pv``, ``dishwasher`` (no unit suffix: these are meter states, not interval values;
    only :func:`readings_to_intervals` should consume them). With ``quantities`` only
    those of the household's meters are read (missing ones are simply absent).

    Raises:
        OpsdError: on an unknown household ID, no matching meter or an unparsable file.
    """
    prefix = f"{household_id}_"
    columns = check_household(csv_path, household_id)
    if quantities is not None:
        columns = [c for c in columns if c.removeprefix(prefix) in quantities]
        if not columns:
            raise OpsdError(
                f"OPSD-Haushalt {household_id} hat keinen der Zähler {sorted(quantities)}"
            )
    raw = pd.read_csv(csv_path, usecols=[TIMESTAMP_COLUMN, *columns])
    try:
        stamps = pd.to_datetime(raw[TIMESTAMP_COLUMN], utc=True, format="ISO8601")
        values = raw[columns].astype(float)
    except (ValueError, TypeError) as exc:
        raise OpsdError(f"OPSD-Datei {csv_path} ist nicht lesbar: {exc}") from exc
    values.columns = [c.removeprefix(prefix) for c in columns]
    values.index = pd.DatetimeIndex(stamps).tz_convert(TIMEZONE)
    return values


def readings_to_intervals(readings: pd.DataFrame) -> pd.DataFrame:
    """Cumulative readings -> energy per 15-min interval, columns ``<quantity>_kwh``.

    See the module docstring for the convention. The result passes ``validate_frame``.

    Raises:
        OpsdError: if no complete reading pair exists, the readings are not on a gapless
            15-min grid, a reading is missing inside the span or a meter runs backwards.
    """
    complete = readings.notna().all(axis=1).to_numpy()
    if complete.sum() < 2:
        raise OpsdError("OPSD-Haushalt hat keine zwei vollständigen Zählerstände")
    first = int(np.argmax(complete))
    last = len(complete) - 1 - int(np.argmax(complete[::-1]))
    span = readings.iloc[first : last + 1]
    gaps = span.columns[span.isna().any()].tolist()
    if gaps:
        stamp = span.index[span.isna().any(axis=1).to_numpy()][0]
        raise OpsdError(
            f"OPSD-Zählerstand fehlt ab {stamp} ({', '.join(gaps)}); Lücken werden nicht "
            "interpoliert"
        )
    stamps = pd.DatetimeIndex(span.index)
    off_grid = np.asarray(stamps[1:] - stamps[:-1] != pd.Timedelta(INTERVAL))
    if off_grid.any():
        bad = stamps[1:][off_grid][0]
        raise OpsdError(f"OPSD-Zählerstände nicht im 15-min-Raster (bei {bad})")
    energy = span.diff().iloc[1:]
    falling = energy.columns[(energy < 0).any()].tolist()
    if falling:
        raise OpsdError(f"OPSD-Zählerstand sinkt ({', '.join(falling)}); Zählerwechsel?")
    out = pd.DataFrame(
        energy.to_numpy(),
        index=pd.DatetimeIndex(span.index[:-1]),
        columns=[f"{c}{ENERGY_SUFFIX}" for c in span.columns],
    )
    validate_frame(out)
    return out


def household_load(meters: pd.DataFrame) -> pd.DataFrame:
    """``load_kwh`` = grid import + PV - grid export (or grid import alone without PV).

    Raises:
        OpsdError: if ``grid_import`` is missing, PV comes without ``grid_export`` or the
            balance gets negative (inconsistent meters).
    """
    imp, exp, pv = (f"{q}{ENERGY_SUFFIX}" for q in (GRID_IMPORT, GRID_EXPORT, PV))
    if imp not in meters.columns:
        raise OpsdError(f"OPSD-Haushalt hat keinen Netzbezugszähler ({GRID_IMPORT})")
    load = meters[imp].copy()
    if pv in meters.columns:
        if exp not in meters.columns:
            raise OpsdError(
                f"OPSD-Haushalt hat PV, aber keinen Einspeisezähler ({GRID_EXPORT}); "
                "Eigenverbrauch nicht bestimmbar"
            )
        load = load + meters[pv] - meters[exp]
    negative = (load < -NEGATIVE_LOAD_TOLERANCE_KWH).to_numpy()
    if negative.any():
        stamp = load.index[negative][0]
        raise OpsdError(f"OPSD-Haushaltslast negativ ab {stamp} (Zähler inkonsistent)")
    # Summation noise of differenced readings (e.g. -1e-13 for export == PV) -> exactly 0.
    out = load.clip(lower=0.0).to_frame(COLUMN)
    validate_frame(out)
    return out


def _restrict_to_year(frame: pd.DataFrame, year: int, household_id: str) -> pd.DataFrame:
    target = year_index(year)
    if not target.isin(frame.index).all():
        raise OpsdError(
            f"OPSD-Haushalt {household_id} deckt das Jahr {year} nicht vollständig ab "
            f"(Daten {frame.index[0]} bis {frame.index[-1]})"
        )
    out = frame.loc[target]
    validate_frame(out)
    return out


def _check_id_format(household_id: str) -> None:
    """Reject IDs that cannot be OPSD IDs before they reach a cache file name."""
    if not ID_PATTERN.fullmatch(household_id):
        raise OpsdError(
            f"Ungültige OPSD-Haushalts-ID {household_id!r}: erwartet DE_KN_<haushalt>, "
            "z. B. DE_KN_residential1"
        )


def _cached_intervals(
    household_id: str,
    variant: str,
    quantities: Collection[str] | None,
    cache: Cache | None,
    csv_path: Path | None,
) -> pd.DataFrame:
    _check_id_format(household_id)
    source_csv = csv_path if csv_path is not None else default_csv_path()

    def fetch() -> pd.DataFrame:
        if not source_csv.exists():
            download_csv(source_csv)
        return readings_to_intervals(read_readings(source_csv, household_id, quantities))

    store = cache if cache is not None else default_cache()
    frame = store.get_or_fetch(SOURCE, f"{VERSION}-{household_id}-{variant}-15min", fetch)
    validate_frame(frame)
    return frame


def load_household_meters(
    household_id: str,
    *,
    year: int | None = None,
    cache: Cache | None = None,
    csv_path: Path | None = None,
) -> pd.DataFrame:
    """All meters of a household as 15-min ``<quantity>_kwh`` (Europe/Vienna), cached.

    For plausibility checks. The span is the one in which **every** meter (including the
    appliances) has readings. On a cache miss the CSV is downloaded to ``csv_path``
    (default :func:`default_csv_path`) unless it already exists there. With ``year`` only
    that calendar year is returned (it must be covered completely).

    Raises:
        OpsdError: on an invalid or unknown household ID or unusable data.
        requests.HTTPError / requests.ConnectionError: if the download fails.
    """
    frame = _cached_intervals(household_id, "meters", None, cache, csv_path)
    return frame if year is None else _restrict_to_year(frame, year, household_id)


def load_household(
    household_id: str,
    *,
    year: int | None = None,
    cache: Cache | None = None,
    csv_path: Path | None = None,
) -> pd.DataFrame:
    """Household load ``load_kwh`` of an OPSD household (see :func:`household_load`).

    Only the meters ``grid_import``, ``pv`` and ``grid_export`` are read, so gaps in
    appliance meters do not shorten the load series.

    Raises:
        OpsdError: on an invalid or unknown household ID or unusable data.
        requests.HTTPError / requests.ConnectionError: if the download fails.
    """
    meters = _cached_intervals(household_id, "load", LOAD_QUANTITIES, cache, csv_path)
    load = household_load(meters)
    return load if year is None else _restrict_to_year(load, year, household_id)


__all__ = [
    "COLUMN",
    "CSV_NAME",
    "DOWNLOAD_URL",
    "HTTP_TIMEOUT_S",
    "LOAD_QUANTITIES",
    "NEGATIVE_LOAD_TOLERANCE_KWH",
    "SOURCE",
    "VERSION",
    "OpsdError",
    "check_household",
    "default_csv_path",
    "download_csv",
    "household_ids",
    "household_load",
    "load_household",
    "load_household_meters",
    "read_readings",
    "readings_to_intervals",
]
