"""Cost model in euros (F3-04, spec §7.1) and CO2 balance (F3-05, :func:`emissions`).

All tariffs are gross prices in ct/kWh (:class:`config.TariffConfig`). With
``reduced_grid_fee = grid_fee_energy * (1 - grid_fee_reduction)``:

* **Community view** (the shared PV belongs to the community, internal payments cancel out),
  per interval::

      grid_import_cost = grid_import * (energy + grid_fee_energy + levies_taxes)
      shared_cost      = shared * (reduced_grid_fee + levies_taxes)
      export_revenue   = export * feed_in_tariff
      community_cost   = grid_import_cost + shared_cost - export_revenue

* **Value of one shared kWh** (community view, fixed tariff), :func:`value_shared_ct_per_kwh`::

      value_shared = energy_price + grid_fee_energy * grid_fee_reduction - feed_in_tariff

  Every additional shared kWh replaces one kWh grid import and one kWh export at the same PV
  and load, so the community cost drops by exactly ``value_shared``. Equivalently
  ``community_cost = load * full_price - pv * feed_in_tariff - shared * value_shared``.

* **Household view** (consumer), per household of an allocation (:mod:`allocation`)::

      grid_import_cost_h = grid_import_h * (energy + grid_fee_energy + levies_taxes)
      shared_cost_h      = shared_h * (eeg_internal_price + reduced_grid_fee + levies_taxes)

``energy`` is the supplier energy price of the grid import: ``energy_price`` for the fixed
tariff (``prices=None``) or ``spot_ct_per_kwh + spot_markup`` per interval for the dynamic
tariff of the objective "Kosten minimieren" (``prices`` given). The internal price
``eeg_internal_price`` stays fixed in both variants.

Results are euros: per-interval frames with ``_eur`` columns and totals as :data:`units.Eur`.
Further exemptions (e.g. Elektrizitätsabgabe) are not modelled (spec §7.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

from eeg_optimizer.allocation import (
    COLUMN_LEVELS,
    HOUSEHOLD_COLUMNS,
    household_frame,
    household_ids,
)
from eeg_optimizer.balance import (
    BALANCE_COLUMNS,
    EXPORT_COLUMN,
    GRID_IMPORT_COLUMN,
    SHARED_COLUMN,
)
from eeg_optimizer.community import CO2_COLUMN
from eeg_optimizer.config import TariffConfig
from eeg_optimizer.units import Eur, KgCO2, ct_to_eur, g_to_kg_co2, validate_frame

SPOT_COLUMN: Final = "spot_ct_per_kwh"
"""Column of the spot price frame (``CommunityInputs.prices``)."""

GRID_IMPORT_COST_COLUMN: Final = "grid_import_cost_eur"
SHARED_COST_COLUMN: Final = "shared_cost_eur"
EXPORT_REVENUE_COLUMN: Final = "export_revenue_eur"
COMMUNITY_COST_COLUMN: Final = "community_cost_eur"
COMMUNITY_COST_COLUMNS: Final = (
    GRID_IMPORT_COST_COLUMN,
    SHARED_COST_COLUMN,
    EXPORT_REVENUE_COLUMN,
    COMMUNITY_COST_COLUMN,
)
"""Per-interval columns of :attr:`CostResult.community`."""

HOUSEHOLD_COST_COLUMN: Final = "cost_eur"
HOUSEHOLD_COST_COLUMNS: Final = (GRID_IMPORT_COST_COLUMN, SHARED_COST_COLUMN, HOUSEHOLD_COST_COLUMN)
"""Columns of :attr:`CostResult.households` (one row per household, index ``household_id``)."""

CONSISTENCY_TOLERANCE_KWH: Final = 1e-6
"""Allowed deviation between the allocation totals and the balance per interval."""

TariffKind = Literal["fixed", "spot"]


class CostInputError(ValueError):
    """Balance, allocation or prices are missing columns or do not fit together."""


@dataclass(frozen=True)
class CostResult:
    """Costs of one scenario in both views."""

    tariff: TariffKind
    """``fixed`` (energy_price) or ``spot`` (spot + spot_markup) for the grid import."""
    community: pd.DataFrame
    """Community view per interval, columns :data:`COMMUNITY_COST_COLUMNS` (``_eur``)."""
    community_eur: Eur
    """Total community cost over the whole period (sum of ``community_cost_eur``)."""
    households: pd.DataFrame
    """Household view, totals over the period: index ``household_id`` in allocation order,
    columns :data:`HOUSEHOLD_COST_COLUMNS` (``_eur``). Aggregate table, no time series."""

    def household_eur(self, household_id: str) -> Eur:
        """Total consumer cost of one household.

        Raises:
            KeyError: ``household_id`` is not part of the result.
        """
        per_household = self.households[HOUSEHOLD_COST_COLUMN].to_dict()
        if household_id not in per_household:
            raise KeyError(f"household {household_id!r} is not part of the cost result")
        return Eur(float(per_household[household_id]))


def reduced_grid_fee_ct_per_kwh(tariffs: TariffConfig) -> float:
    """Grid fee (energy part) of a kWh shared inside the community, after the reduction."""
    return tariffs.grid_fee_energy * (1.0 - tariffs.effective_grid_fee_reduction)


def value_shared_ct_per_kwh(tariffs: TariffConfig) -> float:
    """Value of one shared kWh in the community view with the fixed tariff (spec §7.1).

    ``energy_price + grid_fee_energy * grid_fee_reduction - feed_in_tariff``; defaults give
    9.42 ct/kWh for ``local`` and 7.68 ct/kWh for ``regional``.
    """
    return (
        tariffs.energy_price
        + tariffs.grid_fee_energy * tariffs.effective_grid_fee_reduction
        - tariffs.feed_in_tariff
    )


def _energy_price_ct_per_kwh(
    index: pd.DatetimeIndex, tariffs: TariffConfig, prices: pd.DataFrame | None
) -> npt.NDArray[np.float64]:
    """Supplier energy price of the grid import per interval (fixed or spot + markup)."""
    if prices is None:
        return np.full(len(index), tariffs.energy_price, dtype=float)
    if list(prices.columns) != [SPOT_COLUMN]:
        raise CostInputError(
            f"prices must have exactly the column {SPOT_COLUMN!r}, "
            f"got {[str(c) for c in prices.columns]}"
        )
    validate_frame(prices)
    if not prices.index.equals(index):
        raise CostInputError("prices have a different index than the balance")
    return prices[SPOT_COLUMN].to_numpy(dtype=float) + tariffs.spot_markup


def _check_balance(balance: pd.DataFrame, error: type[ValueError] = CostInputError) -> None:
    missing = [c for c in BALANCE_COLUMNS if c not in balance.columns]
    if missing:
        raise error(f"balance is missing column(s) {missing}; use balance.community_balance")
    validate_frame(balance)


def _household_frames(
    allocation: pd.DataFrame,
    balance: pd.DataFrame,
    error: type[ValueError] = CostInputError,
) -> dict[str, pd.DataFrame]:
    """Validated flat frame per household; the shares add up to the balance (INV-7)."""
    if not allocation.index.equals(balance.index):
        raise error("allocation has a different index than the balance")
    if list(allocation.columns.names) != list(COLUMN_LEVELS):
        raise error(
            f"allocation must have the column levels {list(COLUMN_LEVELS)}, "
            f"got {list(allocation.columns.names)}; use an allocation strategy"
        )
    frames: dict[str, pd.DataFrame] = {}
    for household_id in household_ids(allocation):
        quantities = set(allocation[household_id].columns)
        missing = [c for c in HOUSEHOLD_COLUMNS if c not in quantities]
        if missing:
            raise error(f"allocation of household {household_id!r} is missing column(s) {missing}")
        frame = household_frame(allocation, household_id)
        # Rejects NaN/inf and negative energies, which would otherwise yield NaN costs.
        validate_frame(frame)
        frames[household_id] = frame
    if not frames:
        raise error("allocation has no households")

    for column in (SHARED_COLUMN, GRID_IMPORT_COLUMN):
        total = np.zeros(len(balance.index), dtype=float)
        for frame in frames.values():
            total += frame[column].to_numpy(dtype=float)
        deviation = np.abs(total - balance[column].to_numpy(dtype=float))
        bad = deviation > CONSISTENCY_TOLERANCE_KWH
        if bad.any():
            first = balance.index[int(np.flatnonzero(bad)[0])]
            raise error(
                f"allocation does not fit the balance: sum of household {column} differs in "
                f"{int(bad.sum())} interval(s), first at {first.isoformat()}"
            )
    return frames


def costs(
    balance: pd.DataFrame,
    allocation: pd.DataFrame,
    tariffs: TariffConfig,
    prices: pd.DataFrame | None = None,
) -> CostResult:
    """Costs of a scenario in community and household view (see module docstring).

    Args:
        balance: Output of :func:`balance.community_balance`.
        allocation: Output of an :class:`allocation.AllocationStrategy` for this balance.
        tariffs: Tariff parameters in ct/kWh.
        prices: ``None`` for the fixed tariff; a frame with exactly ``spot_ct_per_kwh`` on the
            index of ``balance`` for the dynamic tariff (spot + ``spot_markup``).

    Raises:
        CostInputError: missing balance columns, wrong price columns, differing indices, an
            allocation without the column levels/quantities of :mod:`allocation` or one that
            does not add up to the balance (tolerance 1e-6 kWh).
        units.FrameValidationError: an input (including every household block of the
            allocation, e.g. NaN or negative energy) violates the internal time series format.
    """
    _check_balance(balance)
    index = pd.DatetimeIndex(balance.index)
    energy = _energy_price_ct_per_kwh(index, tariffs, prices)
    household_frames = _household_frames(allocation, balance)

    reduced_grid_fee = reduced_grid_fee_ct_per_kwh(tariffs)
    full_price = energy + tariffs.grid_fee_energy + tariffs.levies_taxes
    community_shared_price = reduced_grid_fee + tariffs.levies_taxes
    internal_price = tariffs.eeg_internal_price + reduced_grid_fee + tariffs.levies_taxes

    import_cost = ct_to_eur(balance[GRID_IMPORT_COLUMN].to_numpy(dtype=float) * full_price)
    shared_cost = ct_to_eur(balance[SHARED_COLUMN].to_numpy(dtype=float) * community_shared_price)
    export_revenue = ct_to_eur(
        balance[EXPORT_COLUMN].to_numpy(dtype=float) * tariffs.feed_in_tariff
    )
    community = pd.DataFrame(
        {
            GRID_IMPORT_COST_COLUMN: import_cost,
            SHARED_COST_COLUMN: shared_cost,
            EXPORT_REVENUE_COLUMN: export_revenue,
            COMMUNITY_COST_COLUMN: import_cost + shared_cost - export_revenue,
        },
        index=index,
    )
    validate_frame(community)

    rows: dict[str, Mapping[str, float]] = {}
    for household_id, frame in household_frames.items():
        hh_import = float(
            ct_to_eur(frame[GRID_IMPORT_COLUMN].to_numpy(dtype=float) * full_price).sum()
        )
        hh_shared = float(
            ct_to_eur(frame[SHARED_COLUMN].to_numpy(dtype=float) * internal_price).sum()
        )
        rows[household_id] = {
            GRID_IMPORT_COST_COLUMN: hh_import,
            SHARED_COST_COLUMN: hh_shared,
            HOUSEHOLD_COST_COLUMN: hh_import + hh_shared,
        }
    households = pd.DataFrame.from_dict(rows, orient="index", columns=list(HOUSEHOLD_COST_COLUMNS))
    households.index.name = "household_id"

    return CostResult(
        tariff="fixed" if prices is None else "spot",
        community=community,
        community_eur=Eur(float(community[COMMUNITY_COST_COLUMN].sum())),
        households=households,
    )


# --- CO2 balance (F3-05) -------------------------------------------------------------------

CO2_INTENSITY_COLUMN: Final = CO2_COLUMN
"""Column of the grid carbon intensity frame (``CommunityInputs.co2``)."""

EMISSIONS_COLUMN: Final = "emissions_kg_co2"
"""Emissions of the grid import, per interval in :attr:`EmissionResult.community` and as total
per household in :attr:`EmissionResult.households`."""


class EmissionInputError(ValueError):
    """Balance, allocation or carbon intensity are missing columns or do not fit together."""


@dataclass(frozen=True)
class EmissionResult:
    """Emissions of the grid import of one scenario, community and per household."""

    community: pd.DataFrame
    """Per interval, column :data:`EMISSIONS_COLUMN` (``grid_import_kwh * co2 / 1000``)."""
    community_kg_co2: KgCO2
    """Total over the whole period (sum of :attr:`community`)."""
    households: pd.DataFrame
    """Totals over the period: index ``household_id`` in allocation order, column
    :data:`EMISSIONS_COLUMN`. Sums up to :attr:`community_kg_co2` (INV-7 of the allocation)."""

    def household_kg_co2(self, household_id: str) -> KgCO2:
        """Total emissions of the grid import of one household.

        Raises:
            KeyError: ``household_id`` is not part of the result.
        """
        per_household = self.households[EMISSIONS_COLUMN].to_dict()
        if household_id not in per_household:
            raise KeyError(f"household {household_id!r} is not part of the emission result")
        return KgCO2(float(per_household[household_id]))


def _co2_intensity_g_per_kwh(co2: pd.DataFrame, index: pd.DatetimeIndex) -> npt.NDArray[np.float64]:
    if list(co2.columns) != [CO2_INTENSITY_COLUMN]:
        raise EmissionInputError(
            f"co2 must have exactly the column {CO2_INTENSITY_COLUMN!r}, "
            f"got {[str(c) for c in co2.columns]}"
        )
    validate_frame(co2)
    if not co2.index.equals(index):
        raise EmissionInputError("co2 has a different index than the balance")
    intensity = co2[CO2_INTENSITY_COLUMN].to_numpy(dtype=float)
    negative = intensity < 0
    if negative.any():
        first = index[int(np.flatnonzero(negative)[0])]
        raise EmissionInputError(
            f"co2 intensity is negative in {int(negative.sum())} interval(s), "
            f"first at {first.isoformat()}"
        )
    return intensity


def emissions(balance: pd.DataFrame, allocation: pd.DataFrame, co2: pd.DataFrame) -> EmissionResult:
    """CO2 emissions of the grid import (spec §7.2), community and per household.

    ``emissions = grid_import_kwh * co2_g_per_kwh / 1000`` per interval in kg CO2e. PV (shared
    and exported) counts with 0 g/kWh; exports earn no credit. A household's share follows
    its ``grid_import_kwh`` from the allocation, weighted with the intensity of the interval.
    The avoided emissions of a scenario are the difference of two results (F6-01).

    Args:
        balance: Output of :func:`balance.community_balance`.
        allocation: Output of an :class:`allocation.AllocationStrategy` for this balance.
        co2: Frame with exactly ``co2_g_per_kwh`` (≥ 0) on the index of ``balance``.

    Raises:
        EmissionInputError: missing balance columns, wrong co2 column, differing indices,
            negative intensity, or an allocation that does not fit the balance (1e-6 kWh).
        units.FrameValidationError: an input violates the internal time series format.
    """
    _check_balance(balance, EmissionInputError)
    index = pd.DatetimeIndex(balance.index)
    intensity = _co2_intensity_g_per_kwh(co2, index)
    household_frames = _household_frames(allocation, balance, EmissionInputError)

    per_interval = g_to_kg_co2(balance[GRID_IMPORT_COLUMN].to_numpy(dtype=float) * intensity)
    community = pd.DataFrame({EMISSIONS_COLUMN: per_interval}, index=index)
    validate_frame(community)

    rows = {
        household_id: float(
            g_to_kg_co2(frame[GRID_IMPORT_COLUMN].to_numpy(dtype=float) * intensity).sum()
        )
        for household_id, frame in household_frames.items()
    }
    households = pd.DataFrame({EMISSIONS_COLUMN: pd.Series(rows, dtype=float)})
    households.index.name = "household_id"

    return EmissionResult(
        community=community,
        community_kg_co2=KgCO2(float(per_interval.sum())),
        households=households,
    )


__all__ = [
    "CO2_INTENSITY_COLUMN",
    "COMMUNITY_COST_COLUMN",
    "COMMUNITY_COST_COLUMNS",
    "EMISSIONS_COLUMN",
    "EXPORT_REVENUE_COLUMN",
    "GRID_IMPORT_COST_COLUMN",
    "HOUSEHOLD_COST_COLUMN",
    "HOUSEHOLD_COST_COLUMNS",
    "SHARED_COST_COLUMN",
    "SPOT_COLUMN",
    "CostInputError",
    "CostResult",
    "EmissionInputError",
    "EmissionResult",
    "TariffKind",
    "costs",
    "emissions",
    "reduced_grid_fee_ct_per_kwh",
    "value_shared_ct_per_kwh",
]
