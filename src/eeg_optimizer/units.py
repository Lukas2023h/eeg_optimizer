"""Unit types, column suffixes and validation of the internal time series format (INV-4).

Power (kW) and energy (kWh) must never be mixed. This is enforced in two ways:

* Scalars use the ``NewType``s below. ``mypy --strict`` rejects passing a ``KW``
  where a ``KWh`` is expected.
* Every numeric DataFrame column carries its unit as a suffix (see ``ALLOWED_SUFFIXES``),
  checked by :func:`validate_frame` at every module boundary.

kW <-> kWh conversion happens **only** via :func:`kw_to_kwh` / :func:`kwh_to_kw`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final, NewType

import numpy as np
import pandas as pd

KWh = NewType("KWh", float)
"""Energy in kilowatt hours (per interval when used for time series values)."""
KW = NewType("KW", float)
"""Average power in kilowatts."""
Eur = NewType("Eur", float)
"""Money in euros."""
KgCO2 = NewType("KgCO2", float)
"""Emissions in kilograms of CO2 equivalent."""

TIMEZONE: Final = "Europe/Vienna"
INTERVAL: Final = timedelta(minutes=15)

ENERGY_SUFFIX: Final = "_kwh"
ALLOWED_SUFFIXES: Final = ("_kwh", "_kw", "_eur", "_ct_per_kwh", "_g_per_kwh", "_kg_co2")

_SECONDS_PER_HOUR: Final = 3600.0


class FrameValidationError(ValueError):
    """Raised when a DataFrame violates the internal time series format."""


def _hours(interval: timedelta) -> float:
    if interval <= timedelta(0):
        raise ValueError(f"interval must be positive, got {interval}")
    return interval.total_seconds() / _SECONDS_PER_HOUR


def kw_to_kwh(power: KW, interval: timedelta = INTERVAL) -> KWh:
    """Energy delivered by constant ``power`` over ``interval``."""
    return KWh(power * _hours(interval))


def kwh_to_kw(energy: KWh, interval: timedelta = INTERVAL) -> KW:
    """Average power that delivers ``energy`` within ``interval``."""
    return KW(energy / _hours(interval))


def _unit_suffix(column: str) -> str | None:
    # Longest match first: "_ct_per_kwh" also ends with "_kwh".
    for suffix in sorted(ALLOWED_SUFFIXES, key=len, reverse=True):
        if column.endswith(suffix) and len(column) > len(suffix):
            return suffix
    return None


def validate_frame(df: pd.DataFrame, interval: timedelta = INTERVAL) -> None:
    """Check that ``df`` follows the internal time series format.

    Requirements: tz-aware ``DatetimeIndex`` in Europe/Vienna, strictly increasing, gapless
    grid of ``interval`` aligned to interval boundaries (timestamp = interval start). Every
    column holding numbers ends with an allowed unit suffix, and every column with a unit
    suffix has a numeric (non-bool) dtype, no NaN/inf values and, for ``_kwh``, no negative
    values. Non-numeric columns without suffix (e.g. ``household_id``) are not checked.

    Raises:
        FrameValidationError: describing the first violation found.
    """
    if interval <= timedelta(0) or timedelta(hours=1) % interval != timedelta(0):
        # Alignment is checked against the UTC epoch, valid only for sub-hour grids.
        raise ValueError(f"interval must be positive and divide one hour, got {interval}")
    _validate_index(df.index, interval)
    _validate_columns(df)


def _validate_index(index: pd.Index, interval: timedelta) -> None:
    if not isinstance(index, pd.DatetimeIndex):
        raise FrameValidationError(f"index must be a DatetimeIndex, got {type(index).__name__}")
    if index.tz is None:
        raise FrameValidationError(
            f"index has a naive timezone; expected tz-aware timestamps in {TIMEZONE}"
        )
    if str(index.tz) != TIMEZONE:
        raise FrameValidationError(f"index timezone is {index.tz}; expected {TIMEZONE}")
    if len(index) == 0:
        raise FrameValidationError("frame is empty")
    if index.has_duplicates:
        dup = index[index.duplicated()][0]
        raise FrameValidationError(f"duplicate timestamp {dup.isoformat()}")
    if not index.is_monotonic_increasing:
        raise FrameValidationError("index is not sorted in increasing order")

    step = pd.Timedelta(interval)
    epoch = pd.Timestamp(0, tz="UTC")
    misaligned = (index.tz_convert("UTC") - epoch) % step != pd.Timedelta(0)
    if misaligned.any():
        first = index[misaligned][0]
        raise FrameValidationError(
            f"timestamp {first.isoformat()} is not aligned to the {step} grid "
            "(timestamps must mark the interval start)"
        )

    diffs = index[1:] - index[:-1]
    gaps = diffs != step
    if gaps.any():
        pos = int(np.flatnonzero(gaps)[0])
        missing = int(diffs[pos] / step) - 1
        raise FrameValidationError(
            f"missing intervals: {missing} interval(s) of {step} missing between "
            f"{index[pos].isoformat()} and {index[pos + 1].isoformat()}"
        )


_NUMERIC_INFERRED: Final = frozenset({"integer", "floating", "mixed-integer-float", "decimal"})


def _holds_numbers(series: pd.Series) -> bool:
    """True for numeric dtypes and for object/string columns whose values are all numbers."""
    if pd.api.types.is_numeric_dtype(series):
        return True
    return pd.api.types.infer_dtype(series, skipna=True) in _NUMERIC_INFERRED


def _validate_columns(df: pd.DataFrame) -> None:
    if df.columns.has_duplicates:
        dups = sorted({str(c) for c in df.columns[df.columns.duplicated()]})
        raise FrameValidationError(f"duplicate column name(s): {', '.join(dups)}")
    for column in df.columns:
        series = df[column]
        name = str(column)
        suffix = _unit_suffix(name)
        if suffix is None:
            if _holds_numbers(series):
                raise FrameValidationError(
                    f"numeric column {name!r} has no unit suffix; "
                    f"allowed suffixes: {', '.join(ALLOWED_SUFFIXES)}"
                )
            continue
        if (
            not pd.api.types.is_numeric_dtype(series)
            or pd.api.types.is_bool_dtype(series)
            or pd.api.types.is_complex_dtype(series)
        ):
            raise FrameValidationError(
                f"unit column {name!r} must have a numeric dtype, got {series.dtype}"
            )
        values = series.to_numpy(dtype=float, na_value=np.nan)
        bad = ~np.isfinite(values)
        if bad.any():
            pos = int(np.flatnonzero(bad)[0])
            raise FrameValidationError(
                f"column {name!r} contains {int(bad.sum())} NaN/inf value(s), "
                f"first at {df.index[pos].isoformat()}"
            )
        if suffix == ENERGY_SUFFIX:
            negative = values < 0
            if negative.any():
                pos = int(np.flatnonzero(negative)[0])
                raise FrameValidationError(
                    f"energy column {name!r} contains {int(negative.sum())} negative "
                    f"value(s), first {values[pos]} at {df.index[pos].isoformat()}"
                )
