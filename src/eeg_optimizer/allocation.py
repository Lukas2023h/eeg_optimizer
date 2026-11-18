"""Allocation of the community balance to the households (Verteilschlüssel, F3-03, INV-7).

An :class:`AllocationStrategy` splits the community values ``shared_kwh`` and
``grid_import_kwh`` of :func:`balance.community_balance` onto the households. The result is
complete per interval: the household shares add up to the community values (INV-7).

:class:`DynamicProportional` (spec §7.2) splits the shared energy of every interval
proportionally to the household loads of that interval::

    shared_h      = load_h * shared / sum(load)      (0 if sum(load) == 0)
    grid_import_h = load_h - shared_h

Because ``shared <= sum(load)``, no household gets more shared energy than its own load and
all values are >= 0. The strategy is chosen by ``allocation.strategy`` in the configuration
via :func:`strategy_for` and the registry :data:`STRATEGIES`; a further strategy (e.g. a
static key) only needs a class with ``allocate`` and an entry there.

Output format (``allocate``): a frame on the index of the balance with two column levels
``household_id`` and ``quantity``; households keep the order of ``loads``.
:func:`household_frame` returns the flat frame of one household with the columns
:data:`HOUSEHOLD_COLUMNS` (``load_kwh``, ``shared_kwh``, ``grid_import_kwh``) that passes
:func:`units.validate_frame`, :func:`community_totals` the sum over all households.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final, Protocol

import numpy as np
import numpy.typing as npt
import pandas as pd

from eeg_optimizer.balance import (
    BALANCE_COLUMNS,
    GRID_IMPORT_COLUMN,
    LOAD_COLUMN,
    SHARED_COLUMN,
)
from eeg_optimizer.config import AllocationConfig
from eeg_optimizer.units import validate_frame

HOUSEHOLD_COLUMNS: Final = (LOAD_COLUMN, SHARED_COLUMN, GRID_IMPORT_COLUMN)
"""Columns of every household block of an allocation result."""
COLUMN_LEVELS: Final = ("household_id", "quantity")
"""Names of the two column levels of an allocation result."""

CONSISTENCY_TOLERANCE_KWH: Final = 1e-6
"""Allowed deviation between the balance and the household loads per interval."""


class AllocationInputError(ValueError):
    """Balance and household loads do not fit together or have the wrong format."""


class AllocationStrategy(Protocol):
    """Splits the community balance onto the households (INV-7)."""

    def allocate(self, balance: pd.DataFrame, loads: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
        """Household shares of ``shared_kwh`` and ``grid_import_kwh`` per interval.

        Args:
            balance: Output of :func:`balance.community_balance` for ``loads``.
            loads: Household id -> load (column ``load_kwh``) on the index of ``balance``.

        Returns:
            Frame with the column levels :data:`COLUMN_LEVELS`, see module docstring.
        """
        ...


def _check_inputs(
    balance: pd.DataFrame, loads: Mapping[str, pd.DataFrame]
) -> npt.NDArray[np.float64]:
    """Validate the inputs and return the summed household load per interval."""
    missing = [c for c in BALANCE_COLUMNS if c not in balance.columns]
    if missing:
        raise AllocationInputError(
            f"balance is missing column(s) {missing}; use balance.community_balance"
        )
    validate_frame(balance)
    if not loads:
        raise AllocationInputError("loads is empty; the community needs at least one household")

    total_load = np.zeros(len(balance.index), dtype=float)
    for household_id, load in loads.items():
        what = f"load of household {household_id!r}"
        if list(load.columns) != [LOAD_COLUMN]:
            raise AllocationInputError(
                f"{what} must have exactly the column {LOAD_COLUMN!r}, "
                f"got {[str(c) for c in load.columns]}"
            )
        validate_frame(load)
        if not load.index.equals(balance.index):
            raise AllocationInputError(f"{what} has a different index than the balance")
        total_load += load[LOAD_COLUMN].to_numpy(dtype=float)

    balance_load = balance[LOAD_COLUMN].to_numpy(dtype=float)
    shared = balance[SHARED_COLUMN].to_numpy(dtype=float)
    grid_import = balance[GRID_IMPORT_COLUMN].to_numpy(dtype=float)
    checks = (
        (np.abs(balance_load - total_load), "load_kwh differs from the sum of the household loads"),
        (shared - balance_load, "shared_kwh exceeds load_kwh"),
        (np.abs(shared + grid_import - balance_load), "shared_kwh + grid_import_kwh != load_kwh"),
    )
    for deviation, message in checks:
        bad = deviation > CONSISTENCY_TOLERANCE_KWH
        if bad.any():
            first = balance.index[int(np.flatnonzero(bad)[0])]
            raise AllocationInputError(
                f"balance does not fit the loads: {message} in {int(bad.sum())} interval(s), "
                f"first at {first.isoformat()}"
            )
    return total_load


class DynamicProportional:
    """Shared energy per interval proportional to the household load in that interval."""

    def allocate(self, balance: pd.DataFrame, loads: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
        """See :meth:`AllocationStrategy.allocate`.

        Raises:
            AllocationInputError: missing balance columns, no households, wrong load columns,
                differing indices or a balance that does not belong to ``loads``.
            units.FrameValidationError: an input violates the internal time series format.
        """
        total_load = _check_inputs(balance, loads)
        shared = balance[SHARED_COLUMN].to_numpy(dtype=float)
        # Fraction of every household load covered by shared PV; 0 for intervals without load.
        covered = np.zeros_like(total_load)
        np.divide(shared, total_load, out=covered, where=total_load > 0)
        # shared <= total load is checked with CONSISTENCY_TOLERANCE_KWH; the clip only removes
        # that tolerated rounding overshoot, so the fraction is at most 1.
        np.clip(covered, 0.0, 1.0, out=covered)

        blocks: dict[str, pd.DataFrame] = {}
        for household_id, load in loads.items():
            load_values = load[LOAD_COLUMN].to_numpy(dtype=float)
            shared_h = load_values * covered
            block = pd.DataFrame(
                {
                    LOAD_COLUMN: load_values,
                    SHARED_COLUMN: shared_h,
                    # covered <= 1 gives shared_h <= load_values, so this is never negative.
                    GRID_IMPORT_COLUMN: load_values - shared_h,
                },
                index=balance.index,
            )
            validate_frame(block)
            blocks[household_id] = block
        return pd.concat(blocks, axis=1, names=list(COLUMN_LEVELS))


def household_ids(allocation: pd.DataFrame) -> list[str]:
    """Household ids of an allocation result in their original order."""
    return [str(h) for h in allocation.columns.get_level_values(COLUMN_LEVELS[0]).unique()]


def household_frame(allocation: pd.DataFrame, household_id: str) -> pd.DataFrame:
    """Flat frame (columns :data:`HOUSEHOLD_COLUMNS`) of one household of an allocation.

    Raises:
        KeyError: ``household_id`` is not part of the allocation.
    """
    if household_id not in household_ids(allocation):
        raise KeyError(f"household {household_id!r} is not part of the allocation")
    return pd.DataFrame(
        {c: allocation[(household_id, c)].to_numpy(dtype=float) for c in HOUSEHOLD_COLUMNS},
        index=allocation.index,
    )


def community_totals(allocation: pd.DataFrame) -> pd.DataFrame:
    """Sum over all households per interval, columns :data:`HOUSEHOLD_COLUMNS`."""
    totals = {c: np.zeros(len(allocation.index), dtype=float) for c in HOUSEHOLD_COLUMNS}
    for household_id in household_ids(allocation):
        for column in HOUSEHOLD_COLUMNS:
            totals[column] += allocation[(household_id, column)].to_numpy(dtype=float)
    return pd.DataFrame(totals, index=allocation.index)


STRATEGIES: Final[Mapping[str, Callable[[], AllocationStrategy]]] = {
    "dynamic_proportional": DynamicProportional,
}
"""Strategy name from ``allocation.strategy`` -> factory of the strategy."""


def strategy_for(config: AllocationConfig) -> AllocationStrategy:
    """The allocation strategy selected by ``allocation.strategy`` in the configuration.

    Raises:
        KeyError: the strategy name has no entry in :data:`STRATEGIES`.
    """
    try:
        factory = STRATEGIES[config.strategy]
    except KeyError:
        available = ", ".join(sorted(STRATEGIES))
        raise KeyError(
            f"allocation.strategy: Verteilschlüssel {config.strategy!r} ist nicht verfügbar, "
            f"verfügbar: {available}"
        ) from None
    return factory()


__all__ = [
    "COLUMN_LEVELS",
    "HOUSEHOLD_COLUMNS",
    "STRATEGIES",
    "AllocationInputError",
    "AllocationStrategy",
    "DynamicProportional",
    "community_totals",
    "household_frame",
    "household_ids",
    "strategy_for",
]
