"""Comparison of baseline and optimized schedule (F6-01): kWh, € and kg CO₂.

A :class:`Scenario` is one device schedule evaluated completely: household loads, community
balance, allocation (:mod:`allocation`), costs and emissions (:mod:`economics`).
:func:`compare` puts two scenarios on the same inputs side by side.

:attr:`Comparison.table` has one row per household (allocation order) plus the row
:data:`TOTAL_ROW`:

* ``shared_*_kwh`` / ``additional_shared_kwh``: shared PV of the household (allocation); the total
  row is the community value of the balance.
* ``cost_*_eur`` / ``saved_eur``: **household view** (consumer cost incl. internal price); the
  total row is the sum of the households. The household view deliberately does not add up to
  the community view (internal payments), see :attr:`Comparison.saved_community_eur`.
* ``emissions_*_kg_co2`` / ``avoided_kg_co2``: grid import emissions; the total row is the
  community value.

Positive deltas are improvements (more shared, less cost, less CO₂). The household rows add up
to the total row (allocation INV-7; ``cost`` by definition). With the fixed tariff and the same
device energy, ``saved_community_eur == additional_shared_kwh * value_shared`` (spec §7.1),
because the community cost is ``load * full_price - pv * feed_in - shared * value_shared``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np
import pandas as pd

from eeg_optimizer.allocation import AllocationStrategy, household_frame, household_ids
from eeg_optimizer.balance import PV_COLUMN, SHARED_COLUMN, community_balance
from eeg_optimizer.economics import (
    EMISSIONS_COLUMN,
    HOUSEHOLD_COST_COLUMN,
    CostResult,
    EmissionResult,
    TariffKind,
    costs,
    emissions,
)
from eeg_optimizer.optimize.objectives import (
    DeviceSchedule,
    OptimizationInputs,
    baseline_device_schedule,
    schedule_loads,
)
from eeg_optimizer.units import Eur, KgCO2, KWh

TOTAL_ROW: Final = "total"
"""Index label of the total row of :attr:`Comparison.table`."""

SHARED_BASELINE_COLUMN: Final = "shared_baseline_kwh"
SHARED_OPTIMIZED_COLUMN: Final = "shared_optimized_kwh"
ADDITIONAL_SHARED_COLUMN: Final = "additional_shared_kwh"
COST_BASELINE_COLUMN: Final = "cost_baseline_eur"
COST_OPTIMIZED_COLUMN: Final = "cost_optimized_eur"
SAVED_COLUMN: Final = "saved_eur"
EMISSIONS_BASELINE_COLUMN: Final = "emissions_baseline_kg_co2"
EMISSIONS_OPTIMIZED_COLUMN: Final = "emissions_optimized_kg_co2"
AVOIDED_COLUMN: Final = "avoided_kg_co2"
COMPARISON_COLUMNS: Final = (
    SHARED_BASELINE_COLUMN,
    SHARED_OPTIMIZED_COLUMN,
    ADDITIONAL_SHARED_COLUMN,
    COST_BASELINE_COLUMN,
    COST_OPTIMIZED_COLUMN,
    SAVED_COLUMN,
    EMISSIONS_BASELINE_COLUMN,
    EMISSIONS_OPTIMIZED_COLUMN,
    AVOIDED_COLUMN,
)
"""Columns of :attr:`Comparison.table` (aggregate table, no time series)."""

PV_TOLERANCE_KWH: Final = 1e-9
"""Both scenarios must have the same PV per interval (they differ only in the schedule)."""


class ComparisonInputError(ValueError):
    """The two scenarios do not belong to the same community, period or tariff."""


@dataclass(frozen=True)
class Scenario:
    """One device schedule, evaluated completely."""

    loads: Mapping[str, pd.DataFrame]
    """Household id -> load incl. devices (``load_kwh``)."""
    balance: pd.DataFrame
    """Output of :func:`balance.community_balance`."""
    allocation: pd.DataFrame
    """Output of the allocation strategy for :attr:`balance`."""
    costs: CostResult
    emissions: EmissionResult


@dataclass(frozen=True)
class Comparison:
    """Baseline vs. optimized, per household and in total (see module docstring)."""

    baseline: Scenario
    optimized: Scenario
    table: pd.DataFrame
    """Index ``household_id`` (households in allocation order, then :data:`TOTAL_ROW`),
    columns :data:`COMPARISON_COLUMNS`."""
    additional_shared_kwh: KWh
    """Community: additional shared PV (optimized - baseline)."""
    saved_community_eur: Eur
    """Community view: baseline - optimized community cost."""
    saved_households_eur: Eur
    """Household view: sum over the households of baseline - optimized consumer cost."""
    avoided_kg_co2: KgCO2
    """Community: baseline - optimized emissions of the grid import."""

    def household(self, household_id: str) -> pd.Series:
        """Row of one household of :attr:`table`.

        Raises:
            KeyError: ``household_id`` is not part of the comparison.
        """
        if household_id == TOTAL_ROW or household_id not in self.table.index:
            raise KeyError(f"household {household_id!r} is not part of the comparison")
        return self.table.loc[[household_id]].iloc[0]

    @property
    def total(self) -> pd.Series:
        """Total row of :attr:`table`."""
        return self.table.loc[[TOTAL_ROW]].iloc[0]


def scenario(
    inputs: OptimizationInputs,
    schedule: DeviceSchedule,
    strategy: AllocationStrategy,
    tariff: TariffKind = "fixed",
) -> Scenario:
    """Evaluate ``schedule`` on ``inputs``: loads, balance, allocation, costs, emissions.

    Args:
        tariff: ``fixed`` (``energy_price``) or ``spot`` (``inputs.prices`` + ``spot_markup``)
            for the grid import of the costs.

    Raises:
        objectives.ObjectiveInputError: the schedule does not fit the inputs.
        ComparisonInputError: a household id equals :data:`TOTAL_ROW`.
        Every input error of balance, allocation and economics (all ``ValueError``).
    """
    if TOTAL_ROW in inputs.loads:
        raise ComparisonInputError(
            f"household id {TOTAL_ROW!r} is reserved for the total row of the comparison"
        )
    loads = schedule_loads(schedule, inputs)
    bal = community_balance(inputs.pv, loads)
    alloc = strategy.allocate(bal, loads)
    prices = inputs.prices if tariff == "spot" else None
    return Scenario(
        loads=loads,
        balance=bal,
        allocation=alloc,
        costs=costs(bal, alloc, inputs.tariffs, prices),
        emissions=emissions(bal, alloc, inputs.co2),
    )


def _check_pair(baseline: Scenario, optimized: Scenario) -> list[str]:
    ids = household_ids(baseline.allocation)
    if household_ids(optimized.allocation) != ids:
        raise ComparisonInputError(
            f"scenarios have different households: {ids} vs. {household_ids(optimized.allocation)}"
        )
    if TOTAL_ROW in ids:
        raise ComparisonInputError(
            f"household id {TOTAL_ROW!r} is reserved for the total row of the comparison"
        )
    if not baseline.balance.index.equals(optimized.balance.index):
        raise ComparisonInputError("scenarios have different time indices")
    pv_deviation = np.abs(
        baseline.balance[PV_COLUMN].to_numpy(dtype=float)
        - optimized.balance[PV_COLUMN].to_numpy(dtype=float)
    )
    if (pv_deviation > PV_TOLERANCE_KWH).any():
        raise ComparisonInputError("scenarios have different PV generation")
    if baseline.costs.tariff != optimized.costs.tariff:
        raise ComparisonInputError(
            f"scenarios use different tariffs: {baseline.costs.tariff} vs. {optimized.costs.tariff}"
        )
    return ids


def _shared_kwh(sc: Scenario, household_id: str) -> float:
    return float(household_frame(sc.allocation, household_id)[SHARED_COLUMN].sum())


def compare(baseline: Scenario, optimized: Scenario) -> Comparison:
    """Compare two scenarios on the same inputs (see module docstring).

    Raises:
        ComparisonInputError: different households, index, PV or tariff kind.
    """
    ids = _check_pair(baseline, optimized)
    rows: dict[str, dict[str, float]] = {}
    for household_id in ids:
        rows[household_id] = _row(
            (_shared_kwh(baseline, household_id), _shared_kwh(optimized, household_id)),
            (
                float(baseline.costs.household_eur(household_id)),
                float(optimized.costs.household_eur(household_id)),
            ),
            (
                float(baseline.emissions.household_kg_co2(household_id)),
                float(optimized.emissions.household_kg_co2(household_id)),
            ),
        )
    total = _row(
        (
            float(baseline.balance[SHARED_COLUMN].sum()),
            float(optimized.balance[SHARED_COLUMN].sum()),
        ),
        (
            float(baseline.costs.households[HOUSEHOLD_COST_COLUMN].sum()),
            float(optimized.costs.households[HOUSEHOLD_COST_COLUMN].sum()),
        ),
        (
            float(baseline.emissions.community[EMISSIONS_COLUMN].sum()),
            float(optimized.emissions.community[EMISSIONS_COLUMN].sum()),
        ),
    )
    rows[TOTAL_ROW] = total
    table = pd.DataFrame.from_dict(rows, orient="index", columns=list(COMPARISON_COLUMNS))
    table.index.name = "household_id"

    return Comparison(
        baseline=baseline,
        optimized=optimized,
        table=table,
        additional_shared_kwh=KWh(total[ADDITIONAL_SHARED_COLUMN]),
        saved_community_eur=Eur(
            float(baseline.costs.community_eur) - float(optimized.costs.community_eur)
        ),
        saved_households_eur=Eur(total[SAVED_COLUMN]),
        avoided_kg_co2=KgCO2(total[AVOIDED_COLUMN]),
    )


def _row(
    shared: tuple[float, float], cost: tuple[float, float], co2: tuple[float, float]
) -> dict[str, float]:
    """One table row from (baseline, optimized) pairs; deltas positive = improvement."""
    return {
        SHARED_BASELINE_COLUMN: shared[0],
        SHARED_OPTIMIZED_COLUMN: shared[1],
        ADDITIONAL_SHARED_COLUMN: shared[1] - shared[0],
        COST_BASELINE_COLUMN: cost[0],
        COST_OPTIMIZED_COLUMN: cost[1],
        SAVED_COLUMN: cost[0] - cost[1],
        EMISSIONS_BASELINE_COLUMN: co2[0],
        EMISSIONS_OPTIMIZED_COLUMN: co2[1],
        AVOIDED_COLUMN: co2[0] - co2[1],
    }


def compare_schedule(
    inputs: OptimizationInputs,
    schedule: DeviceSchedule,
    strategy: AllocationStrategy,
    tariff: TariffKind = "fixed",
) -> Comparison:
    """Compare ``schedule`` (e.g. ``runner.Schedule.devices``) with the baseline schedule.

    The baseline is :func:`objectives.baseline_device_schedule` on the same ``inputs``.
    """
    baseline = scenario(inputs, baseline_device_schedule(inputs), strategy, tariff)
    return compare(baseline, scenario(inputs, schedule, strategy, tariff))


__all__ = [
    "ADDITIONAL_SHARED_COLUMN",
    "AVOIDED_COLUMN",
    "COMPARISON_COLUMNS",
    "COST_BASELINE_COLUMN",
    "COST_OPTIMIZED_COLUMN",
    "EMISSIONS_BASELINE_COLUMN",
    "EMISSIONS_OPTIMIZED_COLUMN",
    "PV_TOLERANCE_KWH",
    "SAVED_COLUMN",
    "SHARED_BASELINE_COLUMN",
    "SHARED_OPTIMIZED_COLUMN",
    "TOTAL_ROW",
    "Comparison",
    "ComparisonInputError",
    "Scenario",
    "compare",
    "compare_schedule",
    "scenario",
]
