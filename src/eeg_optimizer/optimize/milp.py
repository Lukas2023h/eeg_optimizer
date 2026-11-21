"""MILP for one time block of the optimization (F5-02: modulating devices, objective max_shared).

:func:`build_block_model` turns :class:`~eeg_optimizer.optimize.objectives.OptimizationInputs`
(already cut to one block, e.g. one day plus the night) into a PuLP model;
:func:`solve_block` solves it with CBC and returns a
:data:`~eeg_optimizer.optimize.objectives.DeviceSchedule` that
:func:`~eeg_optimizer.optimize.objectives.evaluate` can rate.

Variables and constraints (docs/architecture.md §5), per interval ``t`` of the block:

* ``x[d, t] ≥ 0`` energy of device ``d`` in kWh, ``x[d, t] ≤ kw_to_kwh(power_kw)``;
  ``x[d, t] = 0`` outside the windows of the tasks of ``d`` (:func:`devices.tasks`);
  ``Σ_{t in window} x[d, t] = energy_kwh`` for every task (INV-2).
* ``shared[t] ≤ pv[t]``, ``shared[t] ≤ inflexible_load[t] + Σ_d x[d, t]``.
* ``max_shared``: maximize ``Σ shared[t]``. At the optimum ``shared[t]`` equals
  ``min(pv, load)`` of :func:`balance.community_balance` wherever that matters for the sum, so
  the LP value is the score of :func:`evaluate`.

Without ``min_run_min``/``min_power_kw`` every variable is continuous: the model is a pure LP.
Program devices (F5-03), ``min_run_min``/``min_power_kw`` (F5-04) and the objectives
``min_cost``/``min_co2`` (F5-05) are not part of this model yet and raise
:class:`UnsupportedModelError` instead of being ignored.

The baseline schedule is a feasible point of the model (same tasks, same energy, same power
limit), so the optimum is never worse than the baseline (INV-3). A solver failure raises
:class:`BlockSolveError`; the runner (F5-07) catches it and falls back.

CBC reports values with about 8 significant digits; :func:`solve_block` therefore rescales
each task to its exact energy (deviations above :data:`ENERGY_REPAIR_LIMIT_KWH` are errors).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd
import pulp

from eeg_optimizer.balance import LOAD_COLUMN, PV_COLUMN
from eeg_optimizer.devices import DEVICE_COLUMN, ModulatingDevice, tasks
from eeg_optimizer.optimize.objectives import Objective, OptimizationInputs
from eeg_optimizer.units import KW, kw_to_kwh, validate_frame

SUPPORTED_OBJECTIVES: Final[tuple[Objective, ...]] = ("max_shared",)
ENERGY_REPAIR_LIMIT_KWH: Final = 1e-4
"""Largest task-energy deviation of a solver result that counts as rounding noise."""


class UnsupportedModelError(ValueError):
    """The block contains something this model cannot represent yet (device kind, objective)."""


class BlockSolveError(RuntimeError):
    """CBC did not find an optimal solution (status in the message)."""


@dataclass(frozen=True)
class TaskSpan:
    """Positions ``[start, stop)`` of one task window in the block index and its energy."""

    start: int
    stop: int
    energy_kwh: float


@dataclass(frozen=True)
class DeviceVars:
    """Model variables of one device."""

    row: list[Any]
    """One ``LpVariable`` per interval of the index, ``0.0`` outside every task window."""
    cap_kwh: float
    """Maximum energy per interval (``kw_to_kwh(power_kw)``)."""
    spans: tuple[TaskSpan, ...]


@dataclass(frozen=True)
class BlockModel:
    """PuLP problem of one block plus the device variables."""

    problem: pulp.LpProblem
    index: pd.DatetimeIndex
    device_vars: Mapping[str, Mapping[str, DeviceVars]]
    """household_id -> device_id -> variables."""


def _check_supported(inputs: OptimizationInputs, objective: Objective) -> None:
    if objective not in SUPPORTED_OBJECTIVES:
        raise UnsupportedModelError(
            f"objective {objective!r} is not supported by the block model yet "
            f"(supported: {', '.join(SUPPORTED_OBJECTIVES)})"
        )
    for household_id, devices in inputs.devices.items():
        for device in devices:
            if not isinstance(device, ModulatingDevice):
                raise UnsupportedModelError(
                    f"device {household_id}/{device.id}: kind {device.kind!r} is not "
                    "supported by the block model yet"
                )
            if device.needs_binaries:
                raise UnsupportedModelError(
                    f"device {household_id}/{device.id}: min_run_min/min_power_kw are not "
                    "supported by the block model yet"
                )


def _inflexible_total(inputs: OptimizationInputs) -> np.ndarray:
    index = inputs.index
    total = np.zeros(len(index), dtype=float)
    for household_id, frame in inputs.loads.items():
        if list(frame.columns) != [LOAD_COLUMN] or not frame.index.equals(index):
            raise UnsupportedModelError(
                f"load of household {household_id!r} must have exactly {LOAD_COLUMN!r} "
                "on the index of the inputs"
            )
        validate_frame(frame)
        total += frame[LOAD_COLUMN].to_numpy(dtype=float)
    return total


def build_block_model(inputs: OptimizationInputs, objective: Objective) -> BlockModel:
    """Build the LP of one block (see module docstring).

    Raises:
        UnsupportedModelError: objective, device kind or device options not supported yet,
            a device of an unknown household, or a load frame not on the index of the inputs.
        units.FrameValidationError: PV or load frames not in the internal format.
    """
    _check_supported(inputs, objective)
    unknown = sorted(set(inputs.devices) - set(inputs.loads))
    if unknown:
        raise UnsupportedModelError(f"devices for unknown household(s) {unknown}")
    validate_frame(inputs.pv)
    index = inputs.index
    pv = inputs.pv[PV_COLUMN].to_numpy(dtype=float)
    load = _inflexible_total(inputs)
    n = len(index)

    problem = pulp.LpProblem("block", pulp.LpMaximize)
    device_vars: dict[str, dict[str, DeviceVars]] = {}
    flexible: list[list[Any]] = [[] for _ in range(n)]
    for h_pos, (household_id, devices) in enumerate(inputs.devices.items()):
        device_vars[household_id] = {}
        for d_pos, device in enumerate(devices):
            cap = float(kw_to_kwh(KW(device.power_kw)))
            row: list[Any] = [0.0] * n
            spans: list[TaskSpan] = []
            for k, task in enumerate(tasks(device, index)):
                start = int(index.searchsorted(task.window_start))
                stop = int(index.searchsorted(task.window_end))
                for t in range(start, stop):
                    var = pulp.LpVariable(f"x_{h_pos}_{d_pos}_{t}", lowBound=0.0, upBound=cap)
                    row[t] = var
                    flexible[t].append(var)
                problem += (
                    pulp.lpSum(row[start:stop]) == float(task.energy_kwh),
                    f"energy_{h_pos}_{d_pos}_{k}",
                )
                spans.append(TaskSpan(start, stop, float(task.energy_kwh)))
            device_vars[household_id][device.id] = DeviceVars(row, cap, tuple(spans))

    shared = [pulp.LpVariable(f"shared_{t}", lowBound=0.0, upBound=float(pv[t])) for t in range(n)]
    for t in range(n):
        problem += shared[t] <= float(load[t]) + pulp.lpSum(flexible[t]), f"shared_load_{t}"
    problem += pulp.lpSum(shared)
    return BlockModel(problem=problem, index=index, device_vars=device_vars)


def _value(item: Any) -> float:
    if isinstance(item, float):
        return item
    value = item.varValue
    if value is None:
        raise BlockSolveError(f"variable {item.name} has no value after solving")
    return float(value)


def _exact_energy(values: np.ndarray, span: TaskSpan, cap: float, where: str) -> None:
    """Make ``values[span]`` sum to the task energy exactly, staying within ``[0, cap]``.

    CBC reports values with about 8 significant digits, so a task may miss its energy by
    ~1e-6 kWh. A missing amount goes into the headroom below ``cap`` (proportionally), a
    surplus is removed proportionally to the values. Anything beyond solver noise raises.
    """
    part = np.clip(values[span.start : span.stop], 0.0, cap)
    diff = span.energy_kwh - float(part.sum())
    if abs(diff) > ENERGY_REPAIR_LIMIT_KWH:
        raise BlockSolveError(
            f"{where}: solver result misses the task energy by {diff:.3g} kWh "
            f"(limit {ENERGY_REPAIR_LIMIT_KWH:g} kWh)"
        )
    if diff > 0.0:
        headroom = cap - part
        if float(headroom.sum()) <= 0.0:
            raise BlockSolveError(f"{where}: no headroom left to reach the task energy")
        part = part + diff * headroom / float(headroom.sum())
    elif diff < 0.0:
        part = part * (span.energy_kwh / float(part.sum()))
    values[span.start : span.stop] = np.clip(part, 0.0, cap)


def solve_block(
    inputs: OptimizationInputs, objective: Objective, time_limit_s: float | None = None
) -> dict[str, dict[str, pd.Series]]:
    """Optimal schedule of all devices in ``inputs`` for ``objective`` (Series ``device_kwh``).

    Solver rounding is repaired per task (:func:`_exact_energy`): values stay in
    ``[0, kw_to_kwh(power_kw)]`` and every task gets its energy up to float precision (INV-2).

    Raises:
        UnsupportedModelError, units.FrameValidationError: see :func:`build_block_model`.
        BlockSolveError: CBC status is not ``Optimal`` (infeasible, time limit, ...) or a
            task energy is missed by more than :data:`ENERGY_REPAIR_LIMIT_KWH`.
    """
    model = build_block_model(inputs, objective)
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s)
    model.problem.solve(solver)
    status = pulp.LpStatus[model.problem.status]
    # With a time limit CBC may report status Optimal for a merely feasible solution.
    if status == "Optimal" and model.problem.sol_status != pulp.LpSolutionOptimal:
        status = pulp.LpSolution[model.problem.sol_status]
    if status != "Optimal":
        raise BlockSolveError(
            f"block {model.index[0].isoformat()} to {model.index[-1].isoformat()}: "
            f"CBC status {status!r}"
        )
    schedule: dict[str, dict[str, pd.Series]] = {}
    for household_id, rows in model.device_vars.items():
        schedule[household_id] = {}
        for device_id, dvars in rows.items():
            values = np.asarray([_value(item) for item in dvars.row], dtype=float)
            for span in dvars.spans:
                where = (
                    f"device {household_id}/{device_id}, {model.index[span.start]:%Y-%m-%d %H:%M}"
                )
                _exact_energy(values, span, dvars.cap_kwh, where)
            schedule[household_id][device_id] = pd.Series(
                values, index=model.index, name=DEVICE_COLUMN
            )
    return schedule


__all__ = [
    "ENERGY_REPAIR_LIMIT_KWH",
    "SUPPORTED_OBJECTIVES",
    "BlockModel",
    "BlockSolveError",
    "DeviceVars",
    "TaskSpan",
    "UnsupportedModelError",
    "build_block_model",
    "solve_block",
]
