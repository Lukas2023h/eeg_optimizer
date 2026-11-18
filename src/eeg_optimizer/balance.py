"""Energy balance of the community per 15-min interval (INV-1).

``community_balance(pv, loads)`` sums the household loads and splits PV generation and load
into the shared part (PV consumed inside the community), the export (PV surplus fed into
the grid) and the grid import (load not covered by PV):

* ``shared_kwh      = min(pv_kwh, load_kwh)``
* ``export_kwh      = pv_kwh - shared_kwh``
* ``grid_import_kwh = load_kwh - shared_kwh``

so ``pv = shared + export`` and ``load = shared + grid_import`` hold per interval and all
values are >= 0. The result is the basis of every KPI, cost and CO₂ figure (F3-02..F3-05)
and of the household allocation (F3-03, which takes the same ``loads`` mapping).

Inputs are checked at the module boundary: ``pv`` has exactly the column ``pv_kwh``, every
load exactly ``load_kwh``, all frames pass :func:`units.validate_frame` and share one index.

``kpis(balance)`` and ``monthly_kpis(balance)`` aggregate the balance to the baseline KPIs
(F3-02): self-consumption ratio ``shared / pv``, autarky ratio ``shared / load``, export and
grid import, for the whole input and per calendar month. A ratio with denominator 0 is 0.0.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt
import pandas as pd

from eeg_optimizer.units import KWh, validate_frame

PV_COLUMN: Final = "pv_kwh"
LOAD_COLUMN: Final = "load_kwh"
SHARED_COLUMN: Final = "shared_kwh"
EXPORT_COLUMN: Final = "export_kwh"
GRID_IMPORT_COLUMN: Final = "grid_import_kwh"

BALANCE_COLUMNS: Final = (PV_COLUMN, LOAD_COLUMN, SHARED_COLUMN, EXPORT_COLUMN, GRID_IMPORT_COLUMN)


class BalanceInputError(ValueError):
    """Input frames of the balance are missing, have wrong columns or differing indices."""


def _check_input(df: pd.DataFrame, column: str, what: str) -> None:
    if list(df.columns) != [column]:
        raise BalanceInputError(
            f"{what} must have exactly the column {column!r}, got {[str(c) for c in df.columns]}"
        )
    validate_frame(df)


def community_balance(pv: pd.DataFrame, loads: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Community energy balance per interval.

    Args:
        pv: Shared PV generation, column ``pv_kwh``.
        loads: Household id -> load, column ``load_kwh``; at least one household, every
            frame on the same index as ``pv``.

    Returns:
        Frame on the index of ``pv`` with the columns of :data:`BALANCE_COLUMNS`.

    Raises:
        BalanceInputError: no households, wrong columns or differing indices.
        units.FrameValidationError: an input violates the internal time series format.
    """
    _check_input(pv, PV_COLUMN, "pv")
    if not loads:
        raise BalanceInputError("loads is empty; the community needs at least one household")
    index = pv.index
    total_load = np.zeros(len(index), dtype=float)
    for household_id, load in loads.items():
        what = f"load of household {household_id!r}"
        _check_input(load, LOAD_COLUMN, what)
        if not load.index.equals(index):
            raise BalanceInputError(f"{what} has a different index than pv")
        total_load += load[LOAD_COLUMN].to_numpy(dtype=float)

    pv_values = pv[PV_COLUMN].to_numpy(dtype=float)
    shared = np.minimum(pv_values, total_load)
    # Subtracting the smaller of two non-negative floats never yields a negative result.
    result = pd.DataFrame(
        {
            PV_COLUMN: pv_values,
            LOAD_COLUMN: total_load,
            SHARED_COLUMN: shared,
            EXPORT_COLUMN: pv_values - shared,
            GRID_IMPORT_COLUMN: total_load - shared,
        },
        index=index,
    )
    validate_frame(result)
    return result


SELF_CONSUMPTION_COLUMN: Final = "self_consumption_ratio"
AUTARKY_COLUMN: Final = "autarky_ratio"
MONTHLY_KPI_COLUMNS: Final = (*BALANCE_COLUMNS, SELF_CONSUMPTION_COLUMN, AUTARKY_COLUMN)


@dataclass(frozen=True)
class Kpis:
    """Baseline KPIs of a period (whole input or one month).

    Ratios are dimensionless fractions in [0, 1]; a ratio whose denominator is 0
    (no PV resp. no load) is defined as 0.0, never NaN.
    """

    pv_kwh: KWh
    load_kwh: KWh
    shared_kwh: KWh
    export_kwh: KWh
    grid_import_kwh: KWh
    self_consumption_ratio: float
    """shared / pv: share of the PV generation consumed inside the community."""
    autarky_ratio: float
    """shared / load: share of the community load covered by the shared PV."""


def _ratio(
    numerator: npt.NDArray[np.float64], denominator: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """Element-wise numerator / denominator, 0.0 where the denominator is 0."""
    out: npt.NDArray[np.float64] = np.zeros_like(numerator, dtype=np.float64)
    np.divide(numerator, denominator, out=out, where=denominator > 0)
    # shared <= pv and shared <= load per interval, so sums keep the ratio <= 1 up to rounding.
    np.clip(out, 0.0, 1.0, out=out)
    return out


def _check_balance(balance: pd.DataFrame) -> None:
    missing = [c for c in BALANCE_COLUMNS if c not in balance.columns]
    if missing:
        raise BalanceInputError(f"balance is missing column(s) {missing}; use community_balance")
    validate_frame(balance)


def kpis(balance: pd.DataFrame) -> Kpis:
    """Baseline KPIs summed over the whole ``balance`` (output of :func:`community_balance`).

    Raises:
        BalanceInputError: a column of :data:`BALANCE_COLUMNS` is missing.
        units.FrameValidationError: ``balance`` violates the internal time series format.
    """
    _check_balance(balance)
    totals = {c: float(balance[c].sum()) for c in BALANCE_COLUMNS}
    shared = np.array([totals[SHARED_COLUMN]])
    return Kpis(
        pv_kwh=KWh(totals[PV_COLUMN]),
        load_kwh=KWh(totals[LOAD_COLUMN]),
        shared_kwh=KWh(totals[SHARED_COLUMN]),
        export_kwh=KWh(totals[EXPORT_COLUMN]),
        grid_import_kwh=KWh(totals[GRID_IMPORT_COLUMN]),
        self_consumption_ratio=float(_ratio(shared, np.array([totals[PV_COLUMN]]))[0]),
        autarky_ratio=float(_ratio(shared, np.array([totals[LOAD_COLUMN]]))[0]),
    )


def monthly_kpis(balance: pd.DataFrame) -> pd.DataFrame:
    """Baseline KPIs per calendar month (local time Europe/Vienna).

    Returns:
        One row per month contained in ``balance`` (12 for a full year), index = month start
        (tz Europe/Vienna, name ``month``), columns :data:`MONTHLY_KPI_COLUMNS`: the monthly
        energy sums ``*_kwh`` and the dimensionless ratios ``self_consumption_ratio`` and
        ``autarky_ratio`` (0.0 for a month without PV resp. load). This is an aggregate
        table, not a 15-min time series.

    Raises:
        BalanceInputError: a column of :data:`BALANCE_COLUMNS` is missing.
        units.FrameValidationError: ``balance`` violates the internal time series format.
    """
    _check_balance(balance)
    sums = balance[list(BALANCE_COLUMNS)].astype(float).resample("MS").sum()
    shared = sums[SHARED_COLUMN].to_numpy(dtype=float)
    sums[SELF_CONSUMPTION_COLUMN] = _ratio(shared, sums[PV_COLUMN].to_numpy(dtype=float))
    sums[AUTARKY_COLUMN] = _ratio(shared, sums[LOAD_COLUMN].to_numpy(dtype=float))
    sums.index.name = "month"
    return sums
