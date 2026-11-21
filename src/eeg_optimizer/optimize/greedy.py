"""Greedy heuristic for one block of the optimization (F5-06): comparison and fallback.

:func:`solve_greedy` has the same interface as :func:`~eeg_optimizer.optimize.milp.solve_block`
(inputs already cut to one block, returns a
:data:`~eeg_optimizer.optimize.objectives.DeviceSchedule`), but needs no solver and runs in
milliseconds. Its result is a feasible point of the block MILP, so it is never better than the
MILP optimum; it is **not** guaranteed to beat the baseline (the runner F5-07 compares).

Algorithm (docs/architecture.md §5 "Greedy"):

1. Every task of every device (:func:`devices.tasks`) becomes one job. Jobs are placed in order
   of flexibility: smallest slack first (window intervals minus the intervals the task needs at
   full power), then earlier window, then household and device order of the inputs.
2. The value of adding ``a`` kWh to interval ``t`` follows the block objective
   (:func:`milp.objective_weights`): ``shared_weight · min(a, surplus) - load_weight · a`` with
   ``surplus = max(pv - load, 0)`` of the load placed so far (inflexible load + earlier jobs).
3. Placement per kind (power limit and window always kept, INV-2):

   * modulating without min options: water filling. The interval with the best value per kWh
     (in surplus: ``shared_weight - load_weight``, else ``-load_weight``) gets energy up to its
     remaining power, its surplus or the remaining task energy; repeat until the task energy is
     met. Ties go to the earliest interval.
   * program: one run of ``duration_min / 15`` intervals at ``power_kw``; the start with the best
     total value wins (ties: earliest).
   * modulating with ``min_power_kw``/``min_run_min``: one contiguous run of ``k`` intervals at
     ``energy / k`` each, for every ``k`` with ``kw_to_kwh(min_power_kw) ≤ energy / k ≤ cap``
     and ``k ≥ min_run_min / 15``; best (``k``, start) wins (ties: fewest intervals, earliest).
     A single run is complete: if any split into runs is feasible, one run with the same number
     of running intervals is as well.

A task that cannot be placed (program or ``min_run_min`` longer than a window shortened by the
spring DST change, energy beyond the window) raises :class:`GreedyScheduleError`; the runner
counts that as a fallback, nothing is cut silently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

import numpy as np
import numpy.typing as npt
import pandas as pd

from eeg_optimizer.balance import PV_COLUMN
from eeg_optimizer.devices import DEVICE_COLUMN, Device, ProgramDevice
from eeg_optimizer.optimize.milp import (
    ObjectiveWeights,
    UnsupportedModelError,
    inflexible_total,
    objective_weights,
)
from eeg_optimizer.optimize.objectives import Objective, ObjectiveInputError, OptimizationInputs
from eeg_optimizer.units import INTERVAL, KW, kw_to_kwh, validate_frame

FloatArray = npt.NDArray[np.float64]

ENERGY_NOISE_KWH: Final = 1e-9
"""Task energy that may stay unplaced when every window interval is at full power (float noise
of the F4-01 check ``energy ≤ window · power`` with the same tolerance)."""
BOUND_NOISE_KWH: Final = 1e-12
"""Amount by which ``energy / k`` may lie below the minimum power or above the power limit of one
interval (float division of an energy that is exactly ``k`` times the bound); clamped onto it."""


class GreedyScheduleError(RuntimeError):
    """A task cannot be placed in its window (e.g. spring DST day); message names the task."""


@dataclass(frozen=True)
class _Job:
    household_id: str
    device: Device
    start: int
    stop: int
    energy_kwh: float
    cap_kwh: float
    order: tuple[int, int, int, int]
    """Sort key: slack, window start, household position, device position."""

    @property
    def where(self) -> str:
        return f"device {self.household_id}/{self.device.id}"


def _jobs(inputs: OptimizationInputs, index: pd.DatetimeIndex) -> list[_Job]:
    jobs: list[_Job] = []
    for h_pos, (household_id, devices) in enumerate(inputs.devices.items()):
        for d_pos, device in enumerate(devices):
            cap = float(kw_to_kwh(KW(device.power_kw)))
            for task in inputs.tasks(device):
                start = int(index.searchsorted(task.window_start))
                stop = int(index.searchsorted(task.window_end))
                energy = float(task.energy_kwh)
                if isinstance(device, ProgramDevice):
                    needed = device.duration // INTERVAL
                else:  # cap > 0: parse_device requires power_kw > 0
                    needed = math.ceil(energy / cap * (1.0 - 1e-9))
                slack = (stop - start) - needed
                jobs.append(
                    _Job(
                        household_id, device, start, stop, energy, cap, (slack, start, h_pos, d_pos)
                    )
                )
    jobs.sort(key=lambda job: job.order)
    return jobs


def _gain(
    amount: FloatArray | float, surplus: FloatArray, w_shared: FloatArray, w_load: FloatArray
) -> FloatArray:
    """Objective change per interval when ``amount`` kWh is added on top of the placed load."""
    return np.asarray(w_shared * np.minimum(amount, surplus) - w_load * amount, dtype=float)


def _water_fill(
    job: _Job, surplus: FloatArray, w_shared: FloatArray, w_load: FloatArray
) -> FloatArray:
    length = job.stop - job.start
    placed = np.zeros(length)
    cap_left = np.full(length, job.cap_kwh)
    segment = surplus.copy()
    remaining = job.energy_kwh
    # Every step empties the power of one interval, its surplus or the task: <= 2L + 1 steps.
    for _ in range(2 * length + 1):
        if remaining <= 0.0:
            break
        open_ = cap_left > 0.0
        if not open_.any():
            break
        in_surplus = segment > 0.0
        value = np.where(in_surplus, w_shared - w_load, -w_load)
        value[~open_] = -np.inf
        t = int(np.argmax(value))
        limit = segment[t] if in_surplus[t] else np.inf
        amount = min(cap_left[t], limit, remaining)
        placed[t] += amount
        cap_left[t] = 0.0 if amount == cap_left[t] else cap_left[t] - amount
        if in_surplus[t]:
            segment[t] = 0.0 if amount == limit else segment[t] - amount
        remaining = 0.0 if amount == remaining else remaining - amount
    if remaining > ENERGY_NOISE_KWH:
        raise GreedyScheduleError(
            f"{job.where}: {remaining:.6g} kWh do not fit into the window of {length} intervals"
        )
    return placed


def _best_run(
    job: _Job,
    candidates: list[tuple[int, float]],
    surplus: FloatArray,
    w_shared: FloatArray,
    w_load: FloatArray,
) -> FloatArray:
    """Best contiguous run over ``candidates`` = (intervals, energy per interval)."""
    length = job.stop - job.start
    best: tuple[float, int, int, float] | None = None
    for k, amount in candidates:
        totals = np.concatenate([[0.0], np.cumsum(_gain(amount, surplus, w_shared, w_load))])
        sums = totals[k:] - totals[: length - k + 1]
        s = int(np.argmax(sums))
        if best is None or sums[s] > best[0]:
            best = (float(sums[s]), s, k, amount)
    if best is None:
        raise GreedyScheduleError(f"{job.where}: no run fits into the window of {length} intervals")
    _, s, k, amount = best
    placed = np.zeros(length)
    placed[s : s + k] = amount
    return placed


def _run_candidates(job: _Job) -> list[tuple[int, float]]:
    length = job.stop - job.start
    device = job.device
    if isinstance(device, ProgramDevice):
        run = device.duration // INTERVAL
        return [(run, job.cap_kwh)] if run <= length else []
    floor = 0.0 if device.min_power_kw is None else float(kw_to_kwh(device.min_power_kw))
    shortest = 1
    if device.min_run_min is not None:
        shortest, rest = divmod(timedelta(minutes=device.min_run_min), INTERVAL)
        if rest:
            raise UnsupportedModelError(
                f"{job.where}: min_run_min {device.min_run_min} is not on the 15-min grid"
            )
    # Energy exactly k · floor or k · cap may divide to 1 ulp outside the bound; such a k is
    # valid and its amount is clamped onto the bound (energy changes by <= k · BOUND_NOISE_KWH).
    candidates: list[tuple[int, float]] = []
    for k in range(max(shortest, 1), length + 1):
        amount = job.energy_kwh / k
        if floor - BOUND_NOISE_KWH <= amount <= job.cap_kwh + BOUND_NOISE_KWH:
            candidates.append((k, min(max(amount, floor), job.cap_kwh)))
    return candidates


def _place(job: _Job, surplus: FloatArray, weights: ObjectiveWeights) -> FloatArray:
    window = slice(job.start, job.stop)
    w_shared, w_load = weights.shared_weight[window], weights.load_weight[window]
    device = job.device
    if isinstance(device, ProgramDevice) or device.needs_binaries:
        return _best_run(job, _run_candidates(job), surplus, w_shared, w_load)
    return _water_fill(job, surplus, w_shared, w_load)


def solve_greedy(
    inputs: OptimizationInputs, objective: Objective
) -> dict[str, dict[str, pd.Series]]:
    """Greedy schedule of all devices in ``inputs`` for ``objective`` (Series ``device_kwh``).

    Same result format as :func:`milp.solve_block`; every task keeps its energy, window and
    power limit (INV-2, see module docstring for programs and min options).

    Raises:
        ObjectiveInputError: devices of a household that has no load.
        milp.UnsupportedModelError: unknown objective, load frame not on the input index,
            ``min_run_min`` off the 15-min grid (devices built without ``parse_device``).
        units.FrameValidationError, economics.CostInputError, economics.EmissionInputError:
            input frames not in the internal format (see :func:`milp.objective_weights`).
        GreedyScheduleError: a task cannot be placed in its window.
    """
    unknown = sorted(set(inputs.devices) - set(inputs.loads))
    if unknown:
        raise ObjectiveInputError(f"devices for unknown household(s) {unknown}")
    validate_frame(inputs.pv)
    index = inputs.index
    weights = objective_weights(inputs, objective)
    pv = inputs.pv[PV_COLUMN].to_numpy(dtype=float)
    load = inflexible_total(inputs)
    values = {
        household_id: {device.id: np.zeros(len(index)) for device in devices}
        for household_id, devices in inputs.devices.items()
    }
    for job in _jobs(inputs, index):
        window = slice(job.start, job.stop)
        surplus = np.maximum(pv[window] - load[window], 0.0)
        placed = _place(job, surplus, weights)
        values[job.household_id][job.device.id][window] += placed
        load[window] += placed
    return {
        household_id: {
            device_id: pd.Series(array, index=index, name=DEVICE_COLUMN)
            for device_id, array in rows.items()
        }
        for household_id, rows in values.items()
    }


__all__ = ["BOUND_NOISE_KWH", "ENERGY_NOISE_KWH", "GreedyScheduleError", "solve_greedy"]
