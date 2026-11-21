"""Objective functions: one score for any device schedule (F5-01, INV-3).

:func:`evaluate` rates a :data:`DeviceSchedule` (energy per interval of every flexible device)
for one of the objectives of ``optimization.objective``. Baseline, greedy and MILP results are
rated by the same function, so "never worse than the baseline" (INV-3) is checkable.

**Sign convention: higher is better** for every objective.

* ``max_shared``: ``+ Σ shared_kwh`` in kWh.
* ``min_cost``:   ``- Σ community_cost_eur`` in € (community view, dynamic tariff with
  ``OptimizationInputs.prices``; :func:`economics.community_costs`).
* ``min_co2``:    ``- Σ emissions_kg_co2`` in kg CO₂ (grid import * intensity;
  :func:`economics.community_emissions`).

The load of a household is its inflexible load plus the energy of its devices; the balance is
:func:`balance.community_balance`. The schedule must contain exactly the devices of
``OptimizationInputs.devices`` on the index of the inputs, so a forgotten device cannot
improve the score. Inputs may cover any part of the year (e.g. one block of the runner).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final, get_args

import numpy as np
import numpy.typing as npt
import pandas as pd

from eeg_optimizer.balance import LOAD_COLUMN, SHARED_COLUMN, community_balance
from eeg_optimizer.community import CommunityInputs
from eeg_optimizer.config import CommunityConfig, ObjectiveName, TariffConfig
from eeg_optimizer.devices import (
    DEVICE_COLUMN,
    Device,
    Task,
    baseline_schedule,
    devices_for,
    tasks,
)
from eeg_optimizer.economics import (
    COMMUNITY_COST_COLUMN,
    EMISSIONS_COLUMN,
    community_costs,
    community_emissions,
)
from eeg_optimizer.units import validate_frame

Objective = ObjectiveName
"""``max_shared`` | ``min_cost`` | ``min_co2`` (same names as the configuration)."""

OBJECTIVES: Final[tuple[Objective, ...]] = get_args(ObjectiveName)

DeviceSchedule = Mapping[str, Mapping[str, pd.Series]]
"""household_id -> device_id -> energy per interval (Series ``device_kwh`` on the input index)."""


class ObjectiveInputError(ValueError):
    """Schedule and inputs do not fit together, or the objective is unknown."""


@dataclass(frozen=True)
class OptimizationInputs:
    """Everything the optimization and its objectives need, on one common 15-min index."""

    pv: pd.DataFrame
    """Shared PV generation, column ``pv_kwh``."""
    loads: Mapping[str, pd.DataFrame]
    """Household id -> **inflexible** load, column ``load_kwh`` (without the devices)."""
    prices: pd.DataFrame
    """Spot prices, column ``spot_ct_per_kwh`` (objective ``min_cost``)."""
    co2: pd.DataFrame
    """Grid carbon intensity, column ``co2_g_per_kwh`` (objective ``min_co2``)."""
    tariffs: TariffConfig
    devices: Mapping[str, tuple[Device, ...]]
    """Household id -> flexible devices (households without devices may be missing)."""
    task_day: date | None = None
    """Only plan the tasks whose window starts on this day (one block of the runner F5-07);
    ``None`` = every task whose window lies in the index. Used by baseline, MILP and greedy."""

    @property
    def index(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.pv.index)

    def tasks(self, device: Device) -> list[Task]:
        """Tasks of ``device`` that this input plans (:func:`devices.tasks` with ``task_day``)."""
        return tasks(device, self.index, day=self.task_day)

    @classmethod
    def from_community(cls, inputs: CommunityInputs, config: CommunityConfig) -> OptimizationInputs:
        """Bundle the series of :func:`community.build_inputs` with tariffs and devices."""
        return cls(
            pv=inputs.pv,
            loads=inputs.loads,
            prices=inputs.prices,
            co2=inputs.co2,
            tariffs=config.tariffs,
            devices=devices_for(config),
        )


def baseline_device_schedule(inputs: OptimizationInputs) -> dict[str, dict[str, pd.Series]]:
    """Baseline schedule of every device (:func:`devices.baseline_schedule`) as DeviceSchedule."""
    return {
        household_id: {
            device.id: baseline_schedule(device, inputs.index, day=inputs.task_day)
            for device in devices
        }
        for household_id, devices in inputs.devices.items()
    }


def _device_values(
    series: pd.Series, where: str, index: pd.DatetimeIndex
) -> npt.NDArray[np.float64]:
    if not isinstance(series, pd.Series):
        raise ObjectiveInputError(f"{where}: pandas Series expected, got {type(series).__name__}")
    if not series.index.equals(index):
        raise ObjectiveInputError(f"{where}: index differs from the index of the inputs")
    validate_frame(series.to_frame(DEVICE_COLUMN))
    return series.to_numpy(dtype=float)


def schedule_loads(schedule: DeviceSchedule, inputs: OptimizationInputs) -> dict[str, pd.DataFrame]:
    """Household loads (``load_kwh``) = inflexible load + energy of all scheduled devices.

    Raises:
        ObjectiveInputError: unknown household, missing or unknown device, wrong index.
        units.FrameValidationError: a device series has NaN, negative or non-grid values.
    """
    unknown = sorted(set(schedule) - set(inputs.loads))
    if unknown:
        raise ObjectiveInputError(f"schedule has unknown household(s) {unknown}")
    index = inputs.index
    result: dict[str, pd.DataFrame] = {}
    for household_id, frame in inputs.loads.items():
        expected = {device.id for device in inputs.devices.get(household_id, ())}
        planned = schedule.get(household_id, {})
        if set(planned) != expected:
            raise ObjectiveInputError(
                f"schedule of household {household_id!r} must contain exactly the devices "
                f"{sorted(expected)}, got {sorted(planned)}"
            )
        if list(frame.columns) != [LOAD_COLUMN]:
            raise ObjectiveInputError(
                f"load of household {household_id!r} must have exactly the column "
                f"{LOAD_COLUMN!r}, got {[str(c) for c in frame.columns]}"
            )
        if not frame.index.equals(index):
            raise ObjectiveInputError(f"load of household {household_id!r} has a different index")
        total = frame[LOAD_COLUMN].to_numpy(dtype=float).copy()
        for device_id, series in planned.items():
            total += _device_values(series, f"device {household_id}/{device_id}", index)
        result[household_id] = pd.DataFrame({LOAD_COLUMN: total}, index=index)
    return result


def score_balance(balance: pd.DataFrame, inputs: OptimizationInputs, objective: Objective) -> float:
    """Score of a community balance (higher is better, see module docstring)."""
    if objective == "max_shared":
        return float(balance[SHARED_COLUMN].sum())
    if objective == "min_cost":
        cost = community_costs(balance, inputs.tariffs, inputs.prices)
        return -float(cost[COMMUNITY_COST_COLUMN].sum())
    if objective == "min_co2":
        emitted = community_emissions(balance, inputs.co2)
        return -float(emitted[EMISSIONS_COLUMN].sum())
    raise ObjectiveInputError(f"unknown objective {objective!r}, known: {', '.join(OBJECTIVES)}")


def evaluate(schedule: DeviceSchedule, inputs: OptimizationInputs, objective: Objective) -> float:
    """Score of ``schedule`` for ``objective``; **higher is better**.

    Units: kWh shared (``max_shared``), negative € community cost (``min_cost``), negative
    kg CO₂ (``min_co2``). Only differences between schedules on the same inputs are meaningful.

    Raises:
        ObjectiveInputError: unknown objective or a schedule that does not fit the inputs.
        balance.BalanceInputError, economics.CostInputError, economics.EmissionInputError,
        units.FrameValidationError: input frames with wrong columns, index or values.
    """
    balance = community_balance(inputs.pv, schedule_loads(schedule, inputs))
    return score_balance(balance, inputs, objective)


__all__ = [
    "OBJECTIVES",
    "DeviceSchedule",
    "Objective",
    "ObjectiveInputError",
    "OptimizationInputs",
    "baseline_device_schedule",
    "evaluate",
    "schedule_loads",
    "score_balance",
]
