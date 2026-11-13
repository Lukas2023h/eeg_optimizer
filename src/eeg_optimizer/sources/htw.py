"""Household load from the HTW Berlin representative load profiles (74 households).

Dataset
-------
Tjaden, T.; Bergner, J.; Weniger, J.; Quaschning, V.: "Repräsentative elektrische
Lastprofile für Einfamilienhäuser in Deutschland auf 1-sekündiger Datenbasis", Datensatz,
Hochschule für Technik und Wirtschaft (HTW) Berlin, DOI 10.13140/RG.2.1.5112.0080/1.

* **Licence:** CC BY-NC 4.0 (attribution, non-commercial). The citation above is the
  required attribution.
* **Landing page:** https://solar.htw-berlin.de/elektrische-lastprofile-fuer-wohngebaeude/
* **Download:** Nextcloud share https://cloud.htw-berlin.de/s/wLEKCqpAniw4TpQ (password
  ``profile``). We fetch the file directly via the share's public WebDAV endpoint
  (:data:`DOWNLOAD_URL`, HTTP basic auth: user = share token, password = ``profile``).
* **File used:** ``CSV_74_Loadprofiles_1min_W_var.zip`` (0.21 GB, :data:`ZIP_SIZE_BYTES`).
  The share also offers the 1-second version (5.4 GB zipped, 45 GB unzipped). The 1-min
  values are the mean power of the 60 one-second values, so summing to 15 min gives the
  same energy at a fraction of the download; we therefore aggregate 1 min -> 15 min
  instead of 1 s -> 15 min as spec §7.2 says (deliberate deviation, see progress.md).
* **Layout:** ``PL1.csv``, ``PL2.csv``, ``PL3.csv`` hold active power per phase in **W**,
  525600 rows (one per minute) x 74 columns (column ``n`` = profile ``n``), no header.
  ``time_datevec_MEZ.csv`` holds ``year, month, day, hour, minute, second`` per row.
  Reactive power (``QL*.csv``) is not used. Household load = PL1 + PL2 + PL3.
* **Original time zone:** local **winter time all year (MEZ = UTC+1, no DST)**, data year
  **2010**, 2010-01-01 00:00 to 2010-12-31 23:59. Each stamp is the start of its minute.
  We read it as fixed offset UTC+1 (``Etc/GMT-1``), so the data covers exactly the
  Europe/Vienna year 2010 (both years begin and end in winter time).
* **Annual consumption:** across the 74 profiles between :data:`ANNUAL_KWH_MIN` and
  :data:`ANNUAL_KWH_MAX` kWh; profile 1 has :data:`PROFILE_1_ANNUAL_KWH` kWh (computed
  from the download, checked by a network test).

Mapping onto another year (weekday-faithful)
--------------------------------------------
Days are matched in MEZ (fixed UTC+1), where every day has exactly 96 quarter hours, both
in the source and in the target year. Target day ``t`` takes source day ``t + k``, where
the constant shift ``k`` in ``-3..3`` makes the weekdays agree (2023 -> 2010: ``k = +2``,
e.g. Monday 2023-01-02 <- Monday 2010-01-04). Source days that run past either end of
the source year are replaced by the same weekday one week closer (``-7`` / ``+7``).
Converting the result back to Europe/Vienna gives the DST days their 92 resp. 100
intervals automatically, without gaps or duplicates. Because the households lived on
wall-clock time, their summer behaviour is stored one hour earlier in MEZ; matching days
of the same season keeps it at the right wall-clock time in the target year. For 2023 the
clock changes line up exactly (2023-03-26 <- 2010-03-28, 2023-10-29 <- 2010-10-31). In
other years the days between the two clock changes (at most 6) can be off by 1 h, e.g.
2024 (``k = +3``): 24-30 March 2024 (winter time) take 28 March-3 April 2010, i.e. the
2010 switch day and the summer-time days after it. Public holidays are not taken into account.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
import requests

from eeg_optimizer.sources.cache import Cache, data_dir, default_cache
from eeg_optimizer.timeseries import aggregate_energy, year_index
from eeg_optimizer.units import INTERVAL, validate_frame

SOURCE: Final = "htw"
COLUMN: Final = "load_kwh"
PROFILE_COUNT: Final = 74
SOURCE_YEAR: Final = 2010
SOURCE_TZ: Final = "Etc/GMT-1"
"""Fixed UTC+1 (MEZ without DST). Note the inverted sign of the ``Etc/`` zones."""

SAMPLE_INTERVAL: Final = timedelta(minutes=1)

SHARE_TOKEN: Final = "wLEKCqpAniw4TpQ"  # noqa: S105 - public share id from the HTW page
SHARE_PASSWORD: Final = "profile"  # noqa: S105 - public password printed on the HTW page
ZIP_NAME: Final = "CSV_74_Loadprofiles_1min_W_var.zip"
DOWNLOAD_URL: Final = f"https://cloud.htw-berlin.de/public.php/webdav/{ZIP_NAME}"
ZIP_SIZE_BYTES: Final = 222_837_241
HTTP_TIMEOUT_S: Final = (30.0, 120.0)
"""(connect, read) timeout for the download."""

PHASE_MEMBERS: Final = ("PL1.csv", "PL2.csv", "PL3.csv")
TIME_MEMBER: Final = "time_datevec_MEZ.csv"

ANNUAL_KWH_MIN: Final = 1398.5
"""Smallest annual consumption in 2010 (profile 6), rounded down to 0.1 kWh."""
ANNUAL_KWH_MAX: Final = 8635.2
"""Largest annual consumption in 2010 (profile 46), rounded up to 0.1 kWh."""
PROFILE_1_ANNUAL_KWH: Final = 3238.7
"""Annual consumption of profile 1 in 2010 (rounded to 0.1 kWh)."""

_SLOTS_PER_DAY: Final = timedelta(days=1) // INTERVAL
_DAYS_PER_WEEK: Final = 7


class HtwError(ValueError):
    """Invalid profile number or HTW data that cannot be mapped without guessing."""


def profile_column(profile: int) -> str:
    """Column name of a profile in the all-profiles frame, e.g. ``profile_01_kwh``."""
    return f"profile_{profile:02d}_kwh"


def check_profile(profile: object) -> int:
    """Return ``profile`` if it is an integer 1..74, else raise :class:`HtwError`."""
    if (
        isinstance(profile, bool)
        or not isinstance(profile, int | np.integer)
        or not 1 <= int(profile) <= PROFILE_COUNT
    ):
        raise HtwError(
            f"Ungültige HTW-Lastprofil-Nummer {profile!r}: erlaubt sind ganze Zahlen "
            f"von 1 bis {PROFILE_COUNT}"
        )
    return int(profile)


def default_zip_path() -> Path:
    """Where the raw download is kept: ``<data_dir>/raw/htw/<ZIP_NAME>``."""
    return data_dir() / "raw" / SOURCE / ZIP_NAME


def download_zip(target: Path) -> Path:
    """Download the 1-min CSV zip to ``target`` (atomic; size checked). Returns ``target``.

    Raises:
        requests.HTTPError / requests.ConnectionError: if the share cannot be reached.
        HtwError: if the downloaded size differs from :data:`ZIP_SIZE_BYTES`.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with (
            os.fdopen(fd, "wb") as out,
            requests.get(
                DOWNLOAD_URL,
                auth=(SHARE_TOKEN, SHARE_PASSWORD),
                stream=True,
                timeout=HTTP_TIMEOUT_S,
            ) as response,
        ):
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1 << 20):
                out.write(chunk)
        size = tmp.stat().st_size
        if size != ZIP_SIZE_BYTES:
            raise HtwError(
                f"HTW-Download unvollständig oder verändert: {size} statt {ZIP_SIZE_BYTES} Bytes"
            )
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def _read_time_index(zf: zipfile.ZipFile) -> pd.DatetimeIndex:
    with zf.open(TIME_MEMBER) as fh:
        parts = pd.read_csv(
            fh, header=None, names=["year", "month", "day", "hour", "minute", "second"]
        )
    naive = pd.to_datetime(parts, errors="raise")
    return pd.DatetimeIndex(naive).tz_localize(SOURCE_TZ)


def read_samples(zip_path: Path, profiles: Sequence[int]) -> pd.DataFrame:
    """Read 1-min total active power (``profile_NN_w`` = PL1+PL2+PL3, in W) from the zip.

    Works on the full download and on small excerpts with the same member names, where
    column ``n`` of each phase file is profile ``n``.

    Raises:
        HtwError: on invalid profile numbers, missing members/columns or row mismatches.
    """
    numbers = [check_profile(p) for p in profiles]
    if not numbers:
        raise HtwError("Keine HTW-Lastprofil-Nummer angegeben")
    with zipfile.ZipFile(zip_path) as zf:
        missing = [m for m in (*PHASE_MEMBERS, TIME_MEMBER) if m not in zf.namelist()]
        if missing:
            raise HtwError(f"HTW-Archiv {zip_path} enthält {', '.join(missing)} nicht")
        index = _read_time_index(zf)
        total = np.zeros((len(index), len(numbers)))
        for member in PHASE_MEMBERS:
            with zf.open(member) as fh:
                phase = pd.read_csv(fh, header=None, dtype=float)
            if phase.shape[1] < max(numbers):
                raise HtwError(
                    f"{member} hat nur {phase.shape[1]} Spalten, Profil {max(numbers)} fehlt"
                )
            if len(phase) != len(index):
                raise HtwError(f"{member} hat {len(phase)} Zeilen, {TIME_MEMBER} aber {len(index)}")
            total += phase.iloc[:, [n - 1 for n in numbers]].to_numpy()
    columns = [f"profile_{n:02d}_w" for n in numbers]
    return pd.DataFrame(total, index=index, columns=columns)


def aggregate_samples(samples: pd.DataFrame) -> pd.DataFrame:
    """1-min ``profile_NN_w`` samples -> 15-min ``profile_NN_kwh`` in Europe/Vienna."""
    return aggregate_energy(samples, SAMPLE_INTERVAL)


def _day_shift(year: int, source_year: int) -> int:
    """Shift ``k`` in -3..3 so that target day ``t`` and source day ``t + k`` share a weekday."""
    k = (date(year, 1, 1).weekday() - date(source_year, 1, 1).weekday()) % _DAYS_PER_WEEK
    return k - _DAYS_PER_WEEK if k > _DAYS_PER_WEEK // 2 else k


def source_days(year: int, source_year: int = SOURCE_YEAR) -> np.ndarray:
    """For each day of ``year`` the index of the source day (same weekday, nearest date)."""
    n_target = (date(year + 1, 1, 1) - date(year, 1, 1)).days
    n_source = (date(source_year + 1, 1, 1) - date(source_year, 1, 1)).days
    days = np.arange(n_target) + _day_shift(year, source_year)
    days[days >= n_source] -= _DAYS_PER_WEEK
    days[days < 0] += _DAYS_PER_WEEK
    return days


def map_to_year(frame: pd.DataFrame, year: int, source_year: int = SOURCE_YEAR) -> pd.DataFrame:
    """Map a full 15-min year ``source_year`` weekday-faithfully onto ``year``.

    See the module docstring for the rule. The result has the index ``year_index(year)``.

    Raises:
        HtwError: if ``frame`` does not cover exactly the Europe/Vienna year ``source_year``.
    """
    validate_frame(frame)
    if not pd.DatetimeIndex(frame.index).equals(year_index(source_year)):
        raise HtwError(
            f"HTW-Daten decken nicht genau das Jahr {source_year} ab "
            f"({len(frame)} Viertelstunden, {frame.index[0]} bis {frame.index[-1]})"
        )
    target = year_index(year)
    # Both indices start at local midnight in winter time (= MEZ) and run in UTC order, so
    # row d * 96 + s is quarter hour s of MEZ day d in source and target alike.
    values = frame.to_numpy(dtype=float).reshape(-1, _SLOTS_PER_DAY, frame.shape[1])
    mapped = values[source_days(year, source_year)].reshape(len(target), frame.shape[1])
    out = pd.DataFrame(mapped, index=target, columns=frame.columns)
    validate_frame(out)
    return out


def _all_profiles_key() -> str:
    return f"profiles_{SOURCE_YEAR}_15min"


def load_all_profiles(*, cache: Cache | None = None, zip_path: Path | None = None) -> pd.DataFrame:
    """All 74 profiles of 2010 as 15-min ``profile_NN_kwh`` (Europe/Vienna), cached.

    On a cache miss the zip is downloaded to ``zip_path`` (default
    :func:`default_zip_path`) unless it already exists there, then parsed once.
    """
    source_zip = zip_path if zip_path is not None else default_zip_path()

    def fetch() -> pd.DataFrame:
        if not source_zip.exists():
            download_zip(source_zip)
        elif zip_path is None and source_zip.stat().st_size != ZIP_SIZE_BYTES:
            # Only the default location must hold the official download; an explicit
            # ``zip_path`` may point at an excerpt with the same layout.
            raise HtwError(
                f"HTW-Archiv {source_zip} hat {source_zip.stat().st_size} statt "
                f"{ZIP_SIZE_BYTES} Bytes (unvollständig?). Datei löschen, um neu zu laden."
            )
        samples = read_samples(source_zip, range(1, PROFILE_COUNT + 1))
        return aggregate_samples(samples)

    store = cache if cache is not None else default_cache()
    key = _all_profiles_key()
    frame = store.get_or_fetch(SOURCE, key, fetch)
    expected = [profile_column(n) for n in range(1, PROFILE_COUNT + 1)]
    if list(frame.columns) != expected or not frame.index.equals(year_index(SOURCE_YEAR)):
        raise HtwError(
            f"Cache-Eintrag {SOURCE}/{key} hat nicht die erwarteten {PROFILE_COUNT} Profile "
            f"des Jahres {SOURCE_YEAR}. Datei {store.path(SOURCE, key)} löschen, um neu zu laden."
        )
    validate_frame(frame)
    return frame


def load_household(
    profile: int,
    *,
    year: int = 2023,
    cache: Cache | None = None,
    zip_path: Path | None = None,
) -> pd.DataFrame:
    """Household load of HTW profile ``profile`` (1..74) mapped onto ``year`` as ``load_kwh``.

    Raises:
        HtwError: on an invalid profile number or unusable data.
        requests.HTTPError / requests.ConnectionError: if the download fails.
    """
    number = check_profile(profile)
    frame = load_all_profiles(cache=cache, zip_path=zip_path)
    single = frame[[profile_column(number)]].rename(columns={profile_column(number): COLUMN})
    return map_to_year(single, year)


__all__ = [
    "ANNUAL_KWH_MAX",
    "ANNUAL_KWH_MIN",
    "COLUMN",
    "DOWNLOAD_URL",
    "PROFILE_1_ANNUAL_KWH",
    "PROFILE_COUNT",
    "SOURCE_YEAR",
    "HtwError",
    "aggregate_samples",
    "check_profile",
    "default_zip_path",
    "download_zip",
    "load_all_profiles",
    "load_household",
    "map_to_year",
    "profile_column",
    "read_samples",
    "source_days",
]
