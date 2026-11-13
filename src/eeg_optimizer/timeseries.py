"""Internal 15-minute grid and resampling into it (INV-6).

All functions return frames in the internal format (see :mod:`eeg_optimizer.units`): tz
Europe/Vienna, gapless 15-minute grid, timestamp = interval start. Arithmetic on the time
axis happens in UTC, so DST days automatically get 92 resp. 100 intervals.

Resampling follows the physics of the column:

* **Extensive** quantities (energy ``_kwh``, money ``_eur``, emissions ``_kg_co2``) are
  per-interval amounts. Upsampling splits them evenly, downsampling sums them, so the total
  is conserved.
* **Intensive** quantities (average power ``_kw``, prices ``_ct_per_kwh``, carbon intensity
  ``_g_per_kwh``) are rates. Upsampling repeats them unchanged.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

import numpy as np
import pandas as pd

from eeg_optimizer.units import (
    ALLOWED_SUFFIXES,
    INTERVAL,
    KW,
    TIMEZONE,
    kw_to_kwh,
    validate_frame,
    w_to_kw,
)

HOUR: Final = timedelta(hours=1)

EXTENSIVE_SUFFIXES: Final = ("_kwh", "_eur", "_kg_co2")
INTENSIVE_SUFFIXES: Final = ("_kw", "_ct_per_kwh", "_g_per_kwh")
"""Together exactly ``units.ALLOWED_SUFFIXES`` (checked by a unit test)."""

POWER_W_SUFFIX: Final = "_w"
"""Raw input suffix for power in watts, accepted only by :func:`aggregate_energy`."""


class ResampleError(ValueError):
    """Raised when input data cannot be mapped onto the internal grid without guessing."""


def year_index(year: int) -> pd.DatetimeIndex:
    """All 15-minute interval starts of ``year`` in Europe/Vienna (35040 in a common year)."""
    start = pd.Timestamp(year=year, month=1, day=1, tz=TIMEZONE).tz_convert("UTC")
    end = pd.Timestamp(year=year + 1, month=1, day=1, tz=TIMEZONE).tz_convert("UTC")
    utc = pd.date_range(start, end, freq=pd.Timedelta(INTERVAL), inclusive="left")
    return utc.tz_convert(TIMEZONE)


def _suffix_of(column: str, suffixes: tuple[str, ...]) -> str | None:
    # Longest match first: "_ct_per_kwh" also ends with "_kwh".
    for suffix in sorted(suffixes, key=len, reverse=True):
        if column.endswith(suffix) and len(column) > len(suffix):
            return suffix
    return None


def _require_columns(df: pd.DataFrame, allowed: tuple[str, ...], what: str) -> None:
    if len(df.columns) == 0:
        raise ResampleError("frame has no columns")
    for column in df.columns:
        name = str(column)
        if _suffix_of(name, ALLOWED_SUFFIXES) not in allowed:
            raise ResampleError(
                f"column {name!r} is not {what}; expected suffix {', '.join(allowed)}"
            )


def _steps_per(coarse: timedelta, fine: timedelta) -> int:
    if fine <= timedelta(0) or coarse % fine != timedelta(0):
        raise ResampleError(f"{fine} does not evenly divide {coarse}")
    return coarse // fine


def _upsample(df: pd.DataFrame, source_interval: timedelta, *, split: bool) -> pd.DataFrame:
    validate_frame(df, interval=source_interval)
    steps = _steps_per(source_interval, INTERVAL)
    offsets = pd.timedelta_range(start=0, periods=steps, freq=pd.Timedelta(INTERVAL))
    starts = pd.DatetimeIndex(df.index).tz_convert("UTC").repeat(steps)
    utc = pd.DatetimeIndex(starts + np.tile(offsets.to_numpy(), len(df.index)))
    values = df.to_numpy(dtype=float)
    if split:
        values = values / steps
    out = pd.DataFrame(
        np.repeat(values, steps, axis=0),
        index=utc.tz_convert(TIMEZONE),
        columns=df.columns,
    )
    validate_frame(out)
    return out


def hourly_energy_to_15min(df: pd.DataFrame) -> pd.DataFrame:
    """Split hourly extensive values (``_kwh``, ``_eur``, ``_kg_co2``) evenly onto 15 min.

    Each quarter receives a quarter of the hourly value, so totals are conserved. The input
    must already be in Europe/Vienna (UTC sources convert first via ``tz_convert``).

    Raises:
        FrameValidationError: if ``df`` is not a valid gapless hourly frame.
        ResampleError: if a column is not an extensive quantity.
    """
    _require_columns(df, EXTENSIVE_SUFFIXES, "an extensive quantity (energy, money, emissions)")
    return _upsample(df, HOUR, split=True)


def rate_to_15min(df: pd.DataFrame) -> pd.DataFrame:
    """Repeat hourly rates (``_kw``, ``_ct_per_kwh``, ``_g_per_kwh``) unchanged onto 15 min.

    The input must already be in Europe/Vienna (UTC sources convert first via ``tz_convert``).

    Raises:
        FrameValidationError: if ``df`` is not a valid gapless hourly frame.
        ResampleError: if a column is not a rate.
    """
    _require_columns(df, INTENSIVE_SUFFIXES, "a rate (power, price, intensity)")
    return _upsample(df, HOUR, split=False)


def aggregate_energy(df: pd.DataFrame, sample_interval: timedelta) -> pd.DataFrame:
    """Sum fine-grained samples (e.g. 1 s or 1 min) into 15-minute energy per interval.

    Accepted input columns and their meaning per sample:

    * ``<name>_kwh`` - energy in the sample, summed as is,
    * ``<name>_kw`` - average power in kW, converted via :func:`units.kw_to_kwh`,
    * ``<name>_w`` - average power in W, scaled to kW and converted likewise.

    The output column is always ``<name>_kwh``. The input index must be tz-aware, gapless
    with step ``sample_interval`` and cover only complete 15-minute intervals; partial
    intervals, missing and negative values raise instead of being filled or netted.

    Raises:
        ResampleError: on irregular, incomplete, negative or non-finite input, or on
            unknown or duplicate columns.
    """
    steps = _steps_per(INTERVAL, sample_interval)
    utc = _check_sample_index(df.index, sample_interval, steps)
    if len(df.columns) == 0:
        raise ResampleError("frame has no columns")
    if df.columns.has_duplicates:
        dups = sorted({str(c) for c in df.columns[df.columns.duplicated()]})
        raise ResampleError(f"duplicate column name(s): {', '.join(dups)}")

    kwh_per_kw = kw_to_kwh(KW(1.0), sample_interval)
    kw_per_w = w_to_kw(1.0)
    columns: dict[str, np.ndarray] = {}
    for column in df.columns:
        name = str(column)
        values = _non_negative_values(df[column], name)
        # Match against all known suffixes so that e.g. "_ct_per_kwh" is not taken for "_kwh".
        suffix = _suffix_of(name, (*ALLOWED_SUFFIXES, POWER_W_SUFFIX))
        if suffix == "_kwh":
            energy = values
        elif suffix == "_kw":
            energy = values * kwh_per_kw
        elif suffix == POWER_W_SUFFIX:
            energy = values * kw_per_w * kwh_per_kw
        else:
            raise ResampleError(
                f"column {name!r} has no energy or power suffix (_kwh, _kw, {POWER_W_SUFFIX})"
            )
        target = name.removesuffix(suffix) + "_kwh"
        if target in columns:
            raise ResampleError(f"several input columns map to {target!r}")
        columns[target] = energy.reshape(-1, steps).sum(axis=1)

    out = pd.DataFrame(columns, index=utc[::steps].tz_convert(TIMEZONE))
    validate_frame(out)
    return out


def _check_sample_index(
    index: pd.Index, sample_interval: timedelta, steps: int
) -> pd.DatetimeIndex:
    """Check that ``index`` is a gapless sample grid of complete 15-min intervals; return UTC."""
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ResampleError("index must be a tz-aware DatetimeIndex")
    if len(index) == 0:
        raise ResampleError("frame is empty")
    utc = index.tz_convert("UTC")
    step = pd.Timedelta(sample_interval)
    diffs = utc[1:] - utc[:-1]
    irregular = diffs != step
    if irregular.any():
        pos = int(np.flatnonzero(irregular)[0])
        raise ResampleError(
            f"samples are not on a gapless {step} grid between "
            f"{index[pos].isoformat()} and {index[pos + 1].isoformat()}"
        )
    epoch = pd.Timestamp(0, tz="UTC")
    if (utc[0] - epoch) % pd.Timedelta(INTERVAL) != pd.Timedelta(0):
        raise ResampleError(
            f"first sample {index[0].isoformat()} does not start a {INTERVAL} interval"
        )
    if len(index) % steps != 0:
        raise ResampleError(
            f"{len(index)} samples do not fill complete {INTERVAL} intervals "
            f"({steps} samples each); last interval is incomplete"
        )
    return utc


def _non_negative_values(series: pd.Series, name: str) -> np.ndarray:
    # Negative samples are rejected rather than netted out inside an interval: a mix of
    # consumption and feed-in in one column would hide energy flows.
    if not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        raise ResampleError(f"column {name!r} must be numeric, got {series.dtype}")
    values = series.to_numpy(dtype=float, na_value=np.nan)
    for bad, what in ((~np.isfinite(values), "NaN/inf"), (values < 0, "negative")):
        if bad.any():
            pos = int(np.flatnonzero(bad)[0])
            raise ResampleError(
                f"column {name!r} contains {int(bad.sum())} {what} value(s), "
                f"first at {series.index[pos].isoformat()}"
            )
    return values
