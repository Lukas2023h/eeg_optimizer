"""MILP for one time block of the optimization (F5-02 modulating, F5-03 program devices).

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

Program devices (F5-03, washing machine, dishwasher) run ``n = duration_min / 15`` intervals
at ``power_kw`` in one piece. Per task there is one binary ``y[d, s]`` for every start ``s``
whose run ends inside the window, with ``Σ_s y[d, s] = 1`` (exactly one start), and the energy
is the expression ``x[d, t] = kw_to_kwh(power_kw) * Σ_{s ≤ t < s + n} y[d, s]`` instead of a
variable. A task whose window is shorter than the run (spring DST day) has no start and raises
:class:`BlockSolveError` while building. The schedule of a program is rebuilt from the chosen
start, so its profile is exact (no rescaling).

Modulating devices without ``min_run_min``/``min_power_kw`` only have continuous variables;
without programs the model is a pure LP. ``min_run_min``/``min_power_kw`` (F5-04) and the
objectives ``min_cost``/``min_co2`` (F5-05) are not part of this model yet and raise
:class:`UnsupportedModelError` instead of being ignored.

The baseline schedule is a feasible point of the model (same tasks, same energy, same power
limit), so the optimum is never worse than the baseline (INV-3). A solver failure raises
:class:`BlockSolveError`; the runner (F5-07) catches it and falls back.

CBC reports values with about 8 significant digits; :func:`solve_block` therefore rescales
each modulating task to its exact energy (deviations above :data:`ENERGY_REPAIR_LIMIT_KWH` are
errors).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd
import pulp

from eeg_optimizer.balance import LOAD_COLUMN, PV_COLUMN
from eeg_optimizer.devices import DEVICE_COLUMN, ModulatingDevice, ProgramDevice, tasks
from eeg_optimizer.optimize.objectives import Objective, OptimizationInputs
from eeg_optimizer.units import INTERVAL, KW, kw_to_kwh, validate_frame

SUPPORTED_OBJECTIVES: Final[tuple[Objective, ...]] = ("max_shared",)
ENERGY_REPAIR_LIMIT_KWH: Final = 1e-4
"""Largest task-energy deviation of a solver result that counts as rounding noise."""
BINARY_TOLERANCE: Final = 1e-6
"""Largest distance of a solved start binary from 0 or 1 that counts as rounding noise."""


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
    start_vars: tuple[Any, ...] = ()
    """Program only: binary ``LpVariable`` per possible start ``start + i`` (``i`` = position)."""
    run_intervals: int = 0
    """Program only: number of intervals of one run (``duration_min / 15``)."""


@dataclass(frozen=True)
class DeviceVars:
    """Model variables of one device."""

    row: list[Any]
    """Energy per interval of the index: ``LpVariable`` (modulating) or ``LpAffineExpression``
    of the start binaries (program); ``0.0`` outside every task window."""
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
            if isinstance(device, ProgramDevice):
                continue
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


def _add_modulating_task(
    problem: pulp.LpProblem, row: list[Any], span: TaskSpan, cap: float, name: str
) -> None:
    """One continuous variable per window interval, the task energy as equality."""
    for t in range(span.start, span.stop):
        row[t] = pulp.LpVariable(f"x_{name}_{t}", lowBound=0.0, upBound=cap)
    problem += pulp.lpSum(row[span.start : span.stop]) == span.energy_kwh, f"energy_{name}"


def _add_program_task(
    problem: pulp.LpProblem,
    row: list[Any],
    span: TaskSpan,
    device: ProgramDevice,
    *,
    cap: float,
    name: str,
) -> TaskSpan:
    """One start binary per feasible start, exactly one chosen; energy as expressions in ``row``."""
    run = device.duration // INTERVAL
    n_starts = span.stop - span.start - run + 1
    if n_starts <= 0:
        raise BlockSolveError(
            f"device {device.id}: program of {run} intervals does not fit into the window of "
            f"{span.stop - span.start} intervals at block position {span.start} "
            "(spring DST shortens the window by 1 h)"
        )
    starts = tuple(
        pulp.LpVariable(f"y_{name}_{span.start + i}", cat=pulp.LpBinary) for i in range(n_starts)
    )
    problem += pulp.lpSum(starts) == 1, f"one_start_{name}"
    for t in range(span.start, span.stop):
        covering = starts[max(0, t - span.start - run + 1) : t - span.start + 1]
        row[t] = cap * pulp.lpSum(covering)
    return TaskSpan(span.start, span.stop, span.energy_kwh, starts, run)


def build_block_model(inputs: OptimizationInputs, objective: Objective) -> BlockModel:
    """Build the (MI)LP of one block (see module docstring).

    Raises:
        UnsupportedModelError: objective, device kind or device options not supported yet,
            a device of an unknown household, or a load frame not on the index of the inputs.
        units.FrameValidationError: PV or load frames not in the internal format.
        BlockSolveError: a program task has no start whose run fits into its window.
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
                span = TaskSpan(
                    int(index.searchsorted(task.window_start)),
                    int(index.searchsorted(task.window_end)),
                    float(task.energy_kwh),
                )
                name = f"{h_pos}_{d_pos}_{k}"
                if isinstance(device, ProgramDevice):
                    span = _add_program_task(problem, row, span, device, cap=cap, name=name)
                else:
                    _add_modulating_task(problem, row, span, cap, name)
                spans.append(span)
            for t, item in enumerate(row):
                if not isinstance(item, float):
                    flexible[t].append(item)
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


def _chosen_start(span: TaskSpan, where: str) -> int:
    """Block position of the one start binary that is 1 (all others must be 0)."""
    values = np.asarray([_value(var) for var in span.start_vars], dtype=float)
    noisy = np.minimum(np.abs(values), np.abs(values - 1.0)) > BINARY_TOLERANCE
    chosen = np.flatnonzero(values > 0.5)
    if noisy.any() or len(chosen) != 1:
        raise BlockSolveError(
            f"{where}: program needs exactly one start, solver chose {len(chosen)} "
            f"(non-binary values: {int(noisy.sum())})"
        )
    return span.start + int(chosen[0])


def solve_block(
    inputs: OptimizationInputs, objective: Objective, time_limit_s: float | None = None
) -> dict[str, dict[str, pd.Series]]:
    """Optimal schedule of all devices in ``inputs`` for ``objective`` (Series ``device_kwh``).

    Modulating tasks: solver rounding is repaired per task (:func:`_exact_energy`), values stay
    in ``[0, kw_to_kwh(power_kw)]`` and every task gets its energy up to float precision.
    Program tasks: the run is rebuilt from the chosen start, ``kw_to_kwh(power_kw)`` in each of
    its ``duration_min / 15`` intervals, 0 elsewhere (INV-2).

    Raises:
        UnsupportedModelError, units.FrameValidationError: see :func:`build_block_model`.
        BlockSolveError: a program does not fit into its window (see :func:`build_block_model`),
            CBC status is not ``Optimal`` (infeasible, time limit, ...), a task energy is
            missed by more than :data:`ENERGY_REPAIR_LIMIT_KWH`, or a program has not exactly
            one start binary at 1.
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
            values = np.zeros(len(model.index), dtype=float)
            for span in dvars.spans:
                where = (
                    f"device {household_id}/{device_id}, {model.index[span.start]:%Y-%m-%d %H:%M}"
                )
                if span.start_vars:
                    start = _chosen_start(span, where)
                    values[start : start + span.run_intervals] = dvars.cap_kwh
                else:
                    values[span.start : span.stop] = [
                        _value(item) for item in dvars.row[span.start : span.stop]
                    ]
                    _exact_energy(values, span, dvars.cap_kwh, where)
            schedule[household_id][device_id] = pd.Series(
                values, index=model.index, name=DEVICE_COLUMN
            )
    return schedule


__all__ = [
    "BINARY_TOLERANCE",
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
