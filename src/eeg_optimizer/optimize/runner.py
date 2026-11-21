"""Runner of the optimization (F5-07): day blocks, comparison with the baseline, fallbacks.

:func:`optimize` plans the flexible devices of a whole period (usually one year) block by block
(docs/architecture.md §5 "Zerlegung", "Garantie INV-3"):

1. **Blocks.** Every task (:func:`devices.tasks` on the whole index) belongs to the day its
   window starts. Block ``d`` holds all tasks of day ``d``; its index runs from the earliest
   window start to the latest window end of these tasks (≤ 48 h, a window 18:00 to 07:00 ends on the
   next day). Days without a task have no block. The block inputs carry ``task_day = d``, so a
   short window of day ``d + 1`` that happens to lie inside the block is *not* planned twice.
2. **Fixed load.** Blocks are solved in day order. The schedule is kept per day; it starts as
   the baseline of every day. The block inputs of day ``d`` get the inflexible load **plus** the
   current device energy of all other days in the block (earlier days: already optimized, later
   days: still their baseline). Block ``d`` thus changes the year schedule only by its own tasks
   and its block score difference is exactly the difference of the year scores (the score is a
   sum over intervals). Accepting only blocks that are not worse than their baseline therefore
   makes the whole result not worse than the baseline (INV-3, up to ``blocks · SCORE_TOLERANCE``).
3. **Check and fallback.** The block result of the chosen solver (``milp``:
   :func:`milp.solve_block`, ``greedy``: :func:`greedy.solve_greedy`) is rated with
   :func:`objectives.evaluate` against the baseline of the block. A solver error
   (:class:`milp.BlockSolveError`, :class:`greedy.GreedyScheduleError`) or a score more than
   :data:`SCORE_TOLERANCE` below the baseline is a **fallback**: ``milp`` falls back to greedy,
   greedy to the baseline of the block. Every fallback block is listed in
   :attr:`Schedule.fallback_blocks` with day, reason and the schedule used; nothing is dropped
   silently. ``solver = "greedy"`` never calls the MILP.

Configuration errors (unknown objective, device of an unknown household, baseline that does not
fit its window) are not fallbacks and propagate.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Final, Literal

import numpy as np
import pandas as pd

from eeg_optimizer.balance import LOAD_COLUMN
from eeg_optimizer.config import SolverName
from eeg_optimizer.devices import DEVICE_COLUMN, tasks
from eeg_optimizer.optimize.greedy import GreedyScheduleError, solve_greedy
from eeg_optimizer.optimize.milp import BlockSolveError, solve_block
from eeg_optimizer.optimize.objectives import (
    DeviceSchedule,
    Objective,
    OptimizationInputs,
    baseline_device_schedule,
    evaluate,
)

SCORE_TOLERANCE: Final = 1e-6
"""Largest amount (kWh, € or kg CO₂ per block) a solver result may score below the baseline of
its block and still count as "not worse": CBC rounding (~8 digits) and the energy repair move the
score by ~1e-7. Anything below that is a fallback."""
DEFAULT_TIME_LIMIT_S: Final = 20.0
"""CBC time limit per block; reaching it without proven optimum is a fallback."""

UsedSchedule = Literal["greedy", "baseline"]
_Arrays = dict[str, dict[str, np.ndarray]]


class RunnerInputError(ValueError):
    """The inputs cannot be split into blocks (e.g. ``task_day`` already set)."""


@dataclass(frozen=True)
class FallbackBlock:
    """One block whose solver result was not used."""

    day: date
    """Day the windows of the block start (local date)."""
    reason: str
    """Why the solver result was rejected (German, for the UI), all steps of the chain."""
    used: UsedSchedule
    """Schedule that was used instead."""


@dataclass(frozen=True)
class Schedule:
    """Result of :func:`optimize` on the index of the inputs."""

    devices: dict[str, dict[str, pd.Series]]
    """household_id -> device_id -> energy per interval (Series ``device_kwh``); a
    :data:`~eeg_optimizer.optimize.objectives.DeviceSchedule` for :func:`evaluate`."""
    objective: Objective
    solver: SolverName
    block_days: tuple[date, ...]
    """Days with a block (in order)."""
    fallback_blocks: tuple[FallbackBlock, ...]
    score: float
    """Score of the result (:func:`evaluate`, higher is better)."""
    baseline_score: float
    """Score of the baseline on the same inputs."""
    runtime_s: float
    """Wall-clock time of :func:`optimize` in seconds."""

    @property
    def fallback_ratio(self) -> float:
        """Share of blocks with a fallback (0 without blocks)."""
        if not self.block_days:
            return 0.0
        return len(self.fallback_blocks) / len(self.block_days)


@dataclass(frozen=True)
class _Block:
    day: date
    start: int
    stop: int


def _blocks(inputs: OptimizationInputs) -> list[_Block]:
    index = inputs.index
    spans: dict[date, tuple[int, int]] = {}
    for devices in inputs.devices.values():
        for device in devices:
            for task in tasks(device, index):
                start = int(index.searchsorted(task.window_start))
                stop = int(index.searchsorted(task.window_end))
                old = spans.get(task.day, (start, stop))
                spans[task.day] = (min(old[0], start), max(old[1], stop))
    return [_Block(day, *spans[day]) for day in sorted(spans)]


def _block_inputs(
    inputs: OptimizationInputs,
    block: _Block,
    others: list[tuple[_Block, _Arrays]],
) -> OptimizationInputs:
    """Inputs of one block: frames cut to the block, other days' devices as fixed load."""
    window = slice(block.start, block.stop)
    loads: dict[str, pd.DataFrame] = {}
    for household_id, frame in inputs.loads.items():
        values = frame[LOAD_COLUMN].to_numpy(dtype=float)[window].copy()
        for other, arrays in others:
            lo, hi = max(block.start, other.start), min(block.stop, other.stop)
            for array in arrays.get(household_id, {}).values():
                values[lo - block.start : hi - block.start] += array[
                    lo - other.start : hi - other.start
                ]
        loads[household_id] = pd.DataFrame({LOAD_COLUMN: values}, index=frame.index[window])
    return replace(
        inputs,
        pv=inputs.pv.iloc[window],
        loads=loads,
        prices=inputs.prices.iloc[window],
        co2=inputs.co2.iloc[window],
        task_day=block.day,
    )


def _arrays(schedule: DeviceSchedule) -> _Arrays:
    return {
        household_id: {
            device_id: series.to_numpy(dtype=float) for device_id, series in rows.items()
        }
        for household_id, rows in schedule.items()
    }


def _solve(
    solver: SolverName,
    inputs: OptimizationInputs,
    objective: Objective,
    time_limit_s: float | None,
    mip_gap: float | None,
) -> dict[str, dict[str, pd.Series]]:
    if solver == "milp":
        return solve_block(inputs, objective, time_limit_s=time_limit_s, mip_gap=mip_gap)
    return solve_greedy(inputs, objective)


_LABEL: Final[Mapping[SolverName, str]] = {"milp": "MILP", "greedy": "Greedy"}


def _plan_block(
    day: date,
    block_inputs: OptimizationInputs,
    baseline: DeviceSchedule,
    objective: Objective,
    solver: SolverName,
    *,
    time_limit_s: float | None,
    mip_gap: float | None,
) -> tuple[DeviceSchedule, FallbackBlock | None]:
    """Schedule of one block and the fallback record (``None`` = solver result used)."""
    baseline_score = evaluate(baseline, block_inputs, objective)
    chain: tuple[SolverName, ...] = ("milp", "greedy") if solver == "milp" else ("greedy",)
    reasons: list[str] = []
    for step in chain:
        try:
            result = _solve(step, block_inputs, objective, time_limit_s, mip_gap)
        except (BlockSolveError, GreedyScheduleError) as error:
            reasons.append(f"{_LABEL[step]}: {error}")
            continue
        score = evaluate(result, block_inputs, objective)
        if score >= baseline_score - SCORE_TOLERANCE:
            if not reasons:
                return result, None
            return result, FallbackBlock(day, "; ".join(reasons), "greedy")
        reasons.append(
            f"{_LABEL[step]}: Zielwert {score:.6g} schlechter als Baseline {baseline_score:.6g}"
        )
    return baseline, FallbackBlock(day, "; ".join(reasons), "baseline")


def optimize(
    inputs: OptimizationInputs,
    objective: Objective,
    solver: SolverName,
    *,
    time_limit_s: float | None = DEFAULT_TIME_LIMIT_S,
    mip_gap: float | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Schedule:
    """Optimize all devices of ``inputs`` block by block (see module docstring).

    ``time_limit_s``/``mip_gap`` go to :func:`milp.solve_block` (ignored by greedy).
    ``progress(done, total)`` is called after every block (UI progress bar).

    Raises:
        RunnerInputError: ``inputs.task_day`` is set (the runner makes the blocks itself).
        objectives.ObjectiveInputError, milp.UnsupportedModelError, devices.BaselineScheduleError,
            units.FrameValidationError, economics.CostInputError, economics.EmissionInputError:
            inputs or configuration that no block can be planned with (not a fallback).
    """
    began = time.perf_counter()
    if inputs.task_day is not None:
        raise RunnerInputError("optimize() expects inputs of the whole period without task_day")
    blocks = _blocks(inputs)
    baselines = {
        block.day: baseline_device_schedule(_block_inputs(inputs, block, [])) for block in blocks
    }
    current: dict[date, _Arrays] = {day: _arrays(baseline) for day, baseline in baselines.items()}
    fallbacks: list[FallbackBlock] = []
    for position, block in enumerate(blocks):
        # A block lies in [day 00:00, day + 2 00:00): only the neighbouring blocks overlap.
        neighbours = [
            (other, current[other.day])
            for other in blocks[max(0, position - 1) : position + 2]
            if other is not block and other.start < block.stop and block.start < other.stop
        ]
        block_inputs = _block_inputs(inputs, block, neighbours)
        schedule, fallback = _plan_block(
            block.day,
            block_inputs,
            baselines[block.day],
            objective,
            solver,
            time_limit_s=time_limit_s,
            mip_gap=mip_gap,
        )
        current[block.day] = _arrays(schedule)
        if fallback is not None:
            fallbacks.append(fallback)
        if progress is not None:
            progress(position + 1, len(blocks))

    devices = _assemble(inputs, blocks, current)
    return Schedule(
        devices=devices,
        objective=objective,
        solver=solver,
        block_days=tuple(block.day for block in blocks),
        fallback_blocks=tuple(fallbacks),
        score=evaluate(devices, inputs, objective),
        baseline_score=evaluate(baseline_device_schedule(inputs), inputs, objective),
        runtime_s=time.perf_counter() - began,
    )


def _assemble(
    inputs: OptimizationInputs, blocks: list[_Block], current: Mapping[date, _Arrays]
) -> dict[str, dict[str, pd.Series]]:
    index = inputs.index
    values = {
        household_id: {device.id: np.zeros(len(index)) for device in devices}
        for household_id, devices in inputs.devices.items()
    }
    for block in blocks:
        for household_id, rows in current[block.day].items():
            for device_id, array in rows.items():
                values[household_id][device_id][block.start : block.stop] += array
    return {
        household_id: {
            device_id: pd.Series(array, index=index, name=DEVICE_COLUMN)
            for device_id, array in rows.items()
        }
        for household_id, rows in values.items()
    }


__all__ = [
    "DEFAULT_TIME_LIMIT_S",
    "SCORE_TOLERANCE",
    "FallbackBlock",
    "RunnerInputError",
    "Schedule",
    "optimize",
]
