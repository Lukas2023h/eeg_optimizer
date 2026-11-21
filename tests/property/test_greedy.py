"""Property tests for the greedy heuristic (F5-06).

INV-2 (energy, window, power, program in one piece, min power and min run) for all device kinds,
and the plausibility check "greedy is never better than the MILP optimum" on small instances.
The random blocks are the strategies of the MILP property tests.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from hypothesis import HealthCheck, event, given, reject, settings
from hypothesis import strategies as st

from eeg_optimizer.devices import ModulatingDevice, ProgramDevice, tasks
from eeg_optimizer.optimize.greedy import solve_greedy
from eeg_optimizer.optimize.milp import BlockSolveError, solve_block
from eeg_optimizer.optimize.objectives import (
    OBJECTIVES,
    DeviceSchedule,
    Objective,
    OptimizationInputs,
    evaluate,
)
from eeg_optimizer.units import INTERVAL, KW, kw_to_kwh
from tests.property.test_milp import _run_lengths, block_inputs

TOL = 1e-6
MILP_TIME_LIMIT_S = 20.0

_SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


def _assert_inv2(inputs: OptimizationInputs, schedule: DeviceSchedule) -> None:
    index = inputs.index
    assert set(schedule) == set(inputs.devices)
    for household_id, devices in inputs.devices.items():
        assert set(schedule[household_id]) == {device.id for device in devices}
        for device in devices:
            series = schedule[household_id][device.id]
            assert series.index.equals(index)
            values = series.to_numpy()
            cap = kw_to_kwh(KW(device.power_kw), INTERVAL)
            assert values.min() >= 0.0
            assert values.max() <= cap + 1e-9
            inside = np.zeros(len(index), dtype=bool)
            for task in tasks(device, index):
                in_window = (index >= task.window_start) & (index < task.window_end)
                inside |= in_window
                part = values[in_window]
                assert abs(part.sum() - task.energy_kwh) <= TOL
                running = part > 0.0
                if isinstance(device, ProgramDevice):
                    event("program")
                    # One contiguous run of duration/15 intervals at full power.
                    idx = np.flatnonzero(running)
                    assert len(idx) == device.duration_min // 15
                    assert idx[-1] - idx[0] == len(idx) - 1
                    assert np.all(part[idx] == cap)
                    continue
                if device.min_power_kw is not None:
                    event("min_power_kw")
                    assert part[running].min() >= kw_to_kwh(device.min_power_kw) - 1e-12
                if device.min_run_min is not None:
                    event("min_run_min")
                    assert min(_run_lengths(running)) >= device.min_run_min // 15
            assert np.abs(values[~inside]).max(initial=0.0) <= TOL


@pytest.mark.parametrize("objective", OBJECTIVES)
@_SETTINGS
@given(inputs=block_inputs(with_programs=True, random_signals=True))
def test_inv2_modulating_and_program_devices(
    objective: Objective, inputs: OptimizationInputs
) -> None:
    _assert_inv2(inputs, solve_greedy(inputs, objective))


@pytest.mark.parametrize("objective", OBJECTIVES)
@_SETTINGS
@given(inputs=block_inputs(with_min_options=True, random_signals=True))
def test_inv2_min_power_and_min_run_devices(
    objective: Objective, inputs: OptimizationInputs
) -> None:
    schedule = solve_greedy(inputs, objective)

    _assert_inv2(inputs, schedule)
    for devices in inputs.devices.values():
        assert all(isinstance(device, ModulatingDevice) for device in devices)


_SMALL = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@pytest.mark.parametrize("objective", OBJECTIVES)
@_SMALL
@given(inputs=block_inputs(with_programs=True, random_signals=True))
def test_greedy_is_never_better_than_the_milp(
    objective: Objective, inputs: OptimizationInputs
) -> None:
    greedy = evaluate(solve_greedy(inputs, objective), inputs, objective)
    optimum = evaluate(solve_block(inputs, objective), inputs, objective)

    if greedy < optimum - TOL:
        event("MILP strictly better")
    # Greedy schedules are feasible points of the MILP; TOL in kWh / € / kg CO₂ covers the
    # ~1e-7 kWh repair of the solver result (CLAUDE.md solver-precision).
    assert greedy <= optimum + TOL


@_SMALL
@given(
    inputs=block_inputs(with_min_options=True),
    objective=st.sampled_from(OBJECTIVES),
)
def test_greedy_with_min_options_is_never_better_than_the_milp(
    inputs: OptimizationInputs, objective: Objective
) -> None:
    # Without mip_gap: the comparison needs the proven optimum, not a schedule within a gap.
    # Proving it can take CBC minutes for rare large semi-continuous devices (progress.md F5-04);
    # only an example that really hit the time limit is discarded, any other failure counts.
    greedy = evaluate(solve_greedy(inputs, objective), inputs, objective)
    started = time.perf_counter()
    try:
        schedule = solve_block(inputs, objective, time_limit_s=MILP_TIME_LIMIT_S)
    except BlockSolveError:
        if time.perf_counter() - started < MILP_TIME_LIMIT_S:
            raise
        event("MILP time limit")
        reject()
    optimum = evaluate(schedule, inputs, objective)

    assert greedy <= optimum + TOL
