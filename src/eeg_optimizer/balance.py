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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import numpy as np
import pandas as pd

from eeg_optimizer.units import validate_frame

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
