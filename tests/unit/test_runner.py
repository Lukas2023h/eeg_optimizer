"""Runner F5-07: day blocks, fixed load of other blocks, fallbacks and their report."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, time
from typing import Any

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.config import TariffConfig
from eeg_optimizer.devices import Device, ModulatingDevice, ProgramDevice, TimeWindow, tasks
from eeg_optimizer.optimize import runner
from eeg_optimizer.optimize.greedy import GreedyScheduleError, solve_greedy
from eeg_optimizer.optimize.milp import BlockSolveError, solve_block
from eeg_optimizer.optimize.objectives import (
    OptimizationInputs,
    baseline_device_schedule,
    evaluate,
)
from eeg_optimizer.optimize.runner import (
    FallbackBlock,
    RunnerInputError,
    Schedule,
    optimize,
)
from eeg_optimizer.units import KW, TIMEZONE, KWh

_TARIFFS = TariffConfig(
    eeg_type="local",
    energy_price=30.0,
    grid_fee_energy=6.0,
    levies_taxes=4.0,
    feed_in_tariff=7.0,
    eeg_internal_price=12.0,
    spot_markup=2.0,
    grid_fee_reduction=0.5,
)
FIRST = date(2023, 6, 1)
TOL = 1e-9


def _index(n_days: int, first: date = FIRST) -> pd.DatetimeIndex:
    start = pd.Timestamp(first).tz_localize(TIMEZONE)
    end = (pd.Timestamp(first) + pd.Timedelta(days=n_days)).tz_localize(TIMEZONE)
    return pd.date_range(start, end, freq="15min", inclusive="left")


def _inputs(
    devices: Mapping[str, tuple[Device, ...]],
    pv_hours: tuple[int, int],
    n_days: int = 3,
    pv_kwh: float = 2.0,
) -> OptimizationInputs:
    """PV ``pv_kwh`` per interval in the local hours ``[from, to)`` of every day, load 0."""
    index = _index(n_days)
    n = len(index)
    hours = np.asarray(index.hour)
    pv = np.where((hours >= pv_hours[0]) & (hours < pv_hours[1]), pv_kwh, 0.0)
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads={hh: pd.DataFrame({"load_kwh": np.zeros(n)}, index=index) for hh in devices},
        prices=pd.DataFrame({"spot_ct_per_kwh": np.full(n, 10.0)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": np.full(n, 100.0)}, index=index),
        tariffs=_TARIFFS,
        devices=devices,
    )


def _ev(energy: float = 4.0) -> ModulatingDevice:
    return ModulatingDevice(
        id="ev",
        power_kw=KW(11.0),
        energy_kwh_per_day=KWh(energy),
        window=TimeWindow(time(18), time(7)),
        baseline_start=time(18),
    )


def _boiler(
    window: tuple[time, time] = (time(1), time(5)), energy: float = 1.0
) -> ModulatingDevice:
    return ModulatingDevice(
        id="boiler",
        power_kw=KW(2.0),
        energy_kwh_per_day=KWh(energy),
        window=TimeWindow(*window),
        baseline_start=window[0],
    )


def _washer() -> ProgramDevice:
    return ProgramDevice(
        id="washer",
        power_kw=KW(1.0),
        duration_min=60,
        window=TimeWindow(time(8), time(20)),
        baseline_start=time(19),
    )


def _task_energy(
    schedule: Schedule, household_id: str, device: Device, index: pd.DatetimeIndex
) -> list[float]:
    values = schedule.devices[household_id][device.id]
    return [
        float(values[(index >= task.window_start) & (index < task.window_end)].sum())
        for task in tasks(device, index)
    ]


# ------------------------------------------------------------------ blocks


def test_one_block_per_day_on_which_a_window_starts() -> None:
    # EV 18:00 to 07:00 on 3 days: the window of the last day ends after the index.
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(5, 7))

    schedule = optimize(inputs, "max_shared", "milp")

    assert schedule.block_days == (date(2023, 6, 1), date(2023, 6, 2))
    assert schedule.fallback_blocks == ()
    assert schedule.fallback_ratio == 0.0


def test_window_across_midnight_is_planned_over_the_block_boundary() -> None:
    # PV only 05:00 to 07:00: the EV (window 18:00 to 07:00, baseline 18:00) has to charge in
    # the morning of the next day, i.e. in the part of its block after midnight.
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(5, 7))

    schedule = optimize(inputs, "max_shared", "milp")

    index = inputs.index
    ev = schedule.devices["a"]["ev"]
    morning = (index.hour >= 5) & (index.hour < 7) & (index.date > FIRST)
    assert float(ev[morning].sum()) == pytest.approx(8.0, abs=TOL)
    assert float(ev[~morning].sum()) == pytest.approx(0.0, abs=TOL)
    assert _task_energy(schedule, "a", _ev(), index) == pytest.approx([4.0, 4.0], abs=TOL)
    assert schedule.score == pytest.approx(8.0, abs=1e-6)
    assert schedule.baseline_score == pytest.approx(0.0, abs=TOL)


def test_short_window_of_the_next_day_inside_a_block_is_planned_once() -> None:
    # Block of 1 June runs 18:00 to 07:00 on 2 June and contains the boiler window of 2 June
    # (01:00 to 05:00); that task belongs to the block of 2 June only.
    devices = {"a": (_ev(), _boiler())}
    inputs = _inputs(devices, pv_hours=(1, 7))

    schedule = optimize(inputs, "max_shared", "milp")

    index = inputs.index
    boiler_tasks = tasks(_boiler(), index)
    assert len(boiler_tasks) == 3
    assert _task_energy(schedule, "a", _boiler(), index) == pytest.approx([1.0] * 3, abs=TOL)
    assert float(schedule.devices["a"]["boiler"].sum()) == pytest.approx(3.0, abs=TOL)
    assert float(schedule.devices["a"]["ev"].sum()) == pytest.approx(8.0, abs=TOL)


def test_devices_of_other_blocks_are_fixed_load_of_a_block(monkeypatch: pytest.MonkeyPatch) -> None:
    # The EV of 1 June charges 05:00 to 07:00 on 2 June; the block of 2 June (starts with the
    # boiler window 00:00 to 08:00) must see that energy as load.
    devices = {"a": (_ev(),), "b": (_boiler((time(0), time(8)), energy=1.0),)}
    inputs = _inputs(devices, pv_hours=(5, 7))
    seen: dict[date, OptimizationInputs] = {}

    def recording(block: OptimizationInputs, *args: Any, **kwargs: Any) -> Any:
        assert block.task_day is not None
        seen[block.task_day] = block
        return solve_block(block, *args, **kwargs)

    monkeypatch.setattr(runner, "solve_block", recording)
    schedule = optimize(inputs, "max_shared", "milp")

    ev = schedule.devices["a"]["ev"]
    second = seen[date(2023, 6, 2)]
    load_a = second.loads["a"]["load_kwh"]
    # The block of 2 June also holds the EV task of 2 June (planned, not fixed); its fixed load
    # is exactly the EV of 1 June in the morning of 2 June.
    of_first_task = ev.loc[second.index][
        second.index < pd.Timestamp("2023-06-02 07:00", tz=TIMEZONE)
    ]
    np.testing.assert_allclose(load_a.loc[of_first_task.index], of_first_task, atol=TOL)
    assert float(load_a.sum()) == pytest.approx(4.0, abs=TOL)
    assert float(of_first_task.sum()) == pytest.approx(4.0, abs=TOL)
    # PV 05:00 to 07:00 of 2 June is 16 kWh, EV takes 4, the boiler of 2 June fits beside it.
    assert schedule.fallback_blocks == ()


def test_baseline_of_later_blocks_is_fixed_load_so_the_year_is_not_worse() -> None:
    # Justifies the refinement of architecture §5: a block also sees the (still) baseline load
    # of later days. Objective min_co2, PV per day: 1 kWh at 18:00, 2 kWh at 05:00; intensity
    # 300 g/kWh at 05:00, 100 g/kWh elsewhere. EV of day d (2 kWh, window 18:00 to 07:00,
    # baseline 18:00) imports 1 kWh at 100 g; the boiler of day d + 1 (2 kWh at 8 kW, window
    # 05:00 to 05:15, cannot move) uses the 2 kWh PV at 05:00. With only earlier blocks as
    # fixed load the EV block sees 2 kWh free PV at 05:00 and moves energy there (block view:
    # no import at all), but that PV is the boiler's: the EV then imports at 300 g instead of
    # 100 g and the year scores -1.2 kg against -0.2 kg of the baseline (verified against that
    # variant). With the later baseline as fixed load the EV never charges at 05:00.
    ev = _ev(energy=2.0)
    boiler = ModulatingDevice(
        id="boiler",
        power_kw=KW(8.0),
        energy_kwh_per_day=KWh(2.0),
        window=TimeWindow(time(5), time(5, 15)),
        baseline_start=time(5),
    )
    inputs = _inputs({"a": (ev,), "b": (boiler,)}, pv_hours=(0, 0))
    index = inputs.index
    pv = np.zeros(len(index))
    pv[(index.hour == 18) & (index.minute == 0)] = 1.0
    five = (index.hour == 5) & (index.minute == 0)
    pv[five] = 2.0
    co2 = np.where(five, 300.0, 100.0)
    inputs = OptimizationInputs(
        pd.DataFrame({"pv_kwh": pv}, index=index),
        inputs.loads,
        inputs.prices,
        pd.DataFrame({"co2_g_per_kwh": co2}, index=index),
        inputs.tariffs,
        inputs.devices,
    )

    schedule = optimize(inputs, "min_co2", "milp")

    assert schedule.fallback_blocks == ()
    # 2 EV days import 1 kWh at 100 g each, the boilers import nothing.
    assert schedule.baseline_score == pytest.approx(-0.2, abs=1e-12)
    assert schedule.score >= schedule.baseline_score - 1e-9
    assert float(schedule.devices["a"]["ev"][five].sum()) == pytest.approx(0.0, abs=1e-9)


def test_all_three_objectives_run_without_fallback() -> None:
    inputs = _inputs({"a": (_ev(), _washer()), "b": (_boiler(),)}, pv_hours=(9, 16))

    for objective in ("max_shared", "min_cost", "min_co2"):
        schedule = optimize(inputs, objective, "milp")
        assert schedule.fallback_blocks == (), objective
        assert schedule.score >= schedule.baseline_score - 1e-6
        assert schedule.objective == objective


def test_no_devices_gives_no_blocks() -> None:
    inputs = _inputs({"a": ()}, pv_hours=(9, 16))

    schedule = optimize(inputs, "max_shared", "milp")

    assert schedule.block_days == ()
    assert schedule.fallback_ratio == 0.0
    assert schedule.devices == {"a": {}}


def test_inputs_with_task_day_are_rejected() -> None:
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(9, 16))
    block = OptimizationInputs(
        inputs.pv, inputs.loads, inputs.prices, inputs.co2, inputs.tariffs, inputs.devices, FIRST
    )

    with pytest.raises(RunnerInputError, match="task_day"):
        optimize(block, "max_shared", "milp")


def test_progress_is_reported_after_every_block() -> None:
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(5, 7))
    calls: list[tuple[int, int]] = []

    optimize(
        inputs, "max_shared", "greedy", progress=lambda done, total: calls.append((done, total))
    )

    assert calls == [(1, 2), (2, 2)]


# ------------------------------------------------------------------ fallbacks


def test_solver_error_is_a_counted_fallback_to_greedy(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(5, 7))
    failing_day = date(2023, 6, 2)

    def failing(block: OptimizationInputs, *args: Any, **kwargs: Any) -> Any:
        if block.task_day == failing_day:
            raise BlockSolveError("CBC status 'Not Solved'")
        return solve_block(block, *args, **kwargs)

    monkeypatch.setattr(runner, "solve_block", failing)
    schedule = optimize(inputs, "max_shared", "milp")

    assert len(schedule.fallback_blocks) == 1
    fallback = schedule.fallback_blocks[0]
    assert fallback.day == failing_day
    assert fallback.used == "greedy"
    assert "MILP" in fallback.reason
    assert "Not Solved" in fallback.reason
    assert schedule.fallback_ratio == pytest.approx(0.5)
    # Greedy is optimal here as well: the day still charges in the morning PV.
    assert schedule.score == pytest.approx(8.0, abs=1e-6)


def test_greedy_failure_falls_back_to_the_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(5, 7))

    def milp_fails(*args: Any, **kwargs: Any) -> Any:
        raise BlockSolveError("CBC status 'Infeasible'")

    def greedy_fails(*args: Any, **kwargs: Any) -> Any:
        raise GreedyScheduleError("device a/ev: no run fits")

    monkeypatch.setattr(runner, "solve_block", milp_fails)
    monkeypatch.setattr(runner, "solve_greedy", greedy_fails)
    schedule = optimize(inputs, "max_shared", "milp")

    assert [f.day for f in schedule.fallback_blocks] == list(schedule.block_days)
    assert all(f.used == "baseline" for f in schedule.fallback_blocks)
    assert "Infeasible" in schedule.fallback_blocks[0].reason
    assert "no run fits" in schedule.fallback_blocks[0].reason
    assert schedule.fallback_ratio == 1.0
    baseline = baseline_device_schedule(inputs)
    np.testing.assert_allclose(schedule.devices["a"]["ev"], baseline["a"]["ev"], atol=TOL)
    assert schedule.score == pytest.approx(schedule.baseline_score, abs=TOL)


def _latest_possible(block: OptimizationInputs) -> dict[str, dict[str, pd.Series]]:
    """Valid but bad schedule: every task at full power right before its window end."""
    index = block.index
    result: dict[str, dict[str, pd.Series]] = {}
    for household_id, devices in block.devices.items():
        result[household_id] = {}
        for device in devices:
            values = np.zeros(len(index))
            cap = device.power_kw / 4
            for task in block.tasks(device):
                remaining = float(task.energy_kwh)
                t = int(index.searchsorted(task.window_end)) - 1
                while remaining > 0:
                    values[t] = min(cap, remaining)
                    remaining -= values[t]
                    t -= 1
            result[household_id][device.id] = pd.Series(values, index=index, name="device_kwh")
    return result


def test_result_worse_than_the_baseline_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # PV 18:00 to 20:00: the baseline (start 18:00) is optimal, the end of the window is dark.
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(18, 20))

    def bad(block: OptimizationInputs, *args: Any, **kwargs: Any) -> Any:
        return _latest_possible(block)

    monkeypatch.setattr(runner, "solve_block", bad)
    schedule = optimize(inputs, "max_shared", "milp")

    assert len(schedule.fallback_blocks) == 2
    assert all(f.used == "greedy" for f in schedule.fallback_blocks)
    assert "schlechter als Baseline" in schedule.fallback_blocks[0].reason
    assert schedule.score >= schedule.baseline_score - 1e-6


def test_greedy_worse_than_the_baseline_falls_back_to_the_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(18, 20))

    def bad(block: OptimizationInputs, *args: Any, **kwargs: Any) -> Any:
        return _latest_possible(block)

    monkeypatch.setattr(runner, "solve_greedy", bad)
    schedule = optimize(inputs, "max_shared", "greedy")

    assert all(f.used == "baseline" for f in schedule.fallback_blocks)
    assert "Greedy" in schedule.fallback_blocks[0].reason
    assert schedule.score == pytest.approx(schedule.baseline_score, abs=TOL)


def test_result_within_the_score_tolerance_is_not_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Solver noise: a result that scores up to SCORE_TOLERANCE below the baseline is accepted.
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(18, 20))
    real_evaluate = evaluate
    calls = {"n": 0}

    def noisy(schedule: Any, block: OptimizationInputs, objective: Any) -> float:
        value = real_evaluate(schedule, block, objective)
        calls["n"] += 1
        # Every second call per block rates the solver result (first: baseline).
        return value - runner.SCORE_TOLERANCE if calls["n"] % 2 == 0 else value

    monkeypatch.setattr(runner, "evaluate", noisy)
    schedule = optimize(inputs, "max_shared", "milp")

    assert schedule.fallback_blocks == ()


def test_greedy_solver_never_calls_the_milp(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = _inputs({"a": (_ev(), _washer()), "b": (_boiler(),)}, pv_hours=(5, 16))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("solver: greedy must not call the MILP")

    monkeypatch.setattr(runner, "solve_block", forbidden)
    schedule = optimize(inputs, "min_cost", "greedy")

    assert schedule.solver == "greedy"
    assert schedule.fallback_blocks == ()
    assert schedule.score >= schedule.baseline_score - 1e-6


def test_greedy_solver_result_is_the_greedy_schedule_of_each_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs({"a": (_ev(),)}, pv_hours=(5, 7))
    blocks: list[OptimizationInputs] = []

    def recording(block: OptimizationInputs, objective: Any) -> Any:
        blocks.append(block)
        return solve_greedy(block, objective)

    monkeypatch.setattr(runner, "solve_greedy", recording)
    schedule = optimize(inputs, "max_shared", "greedy")

    assert [block.task_day for block in blocks] == list(schedule.block_days)
    for block in blocks:
        expected = solve_greedy(block, "max_shared")["a"]["ev"]
        np.testing.assert_allclose(schedule.devices["a"]["ev"].loc[block.index], expected, atol=TOL)


def test_fallback_block_is_a_plain_record() -> None:
    fallback = FallbackBlock(FIRST, "MILP: CBC status 'Infeasible'", "greedy")
    assert (fallback.day, fallback.used) == (FIRST, "greedy")


# ------------------------------------------------------------------ task_day filter


def test_task_day_limits_tasks_baseline_milp_and_greedy() -> None:
    index = _index(2)
    boiler = _boiler()
    assert [task.day for task in tasks(boiler, index)] == [date(2023, 6, 1), date(2023, 6, 2)]
    assert [task.day for task in tasks(boiler, index, day=date(2023, 6, 2))] == [date(2023, 6, 2)]

    full = _inputs({"a": (boiler,)}, pv_hours=(1, 7), n_days=2)
    second = OptimizationInputs(
        full.pv, full.loads, full.prices, full.co2, full.tariffs, full.devices, date(2023, 6, 2)
    )
    first_day = index.date == FIRST
    for schedule in (
        baseline_device_schedule(second),
        solve_block(second, "max_shared"),
        solve_greedy(second, "max_shared"),
    ):
        values = schedule["a"]["boiler"]
        assert float(values[first_day].sum()) == 0.0
        assert float(values[~first_day].sum()) == pytest.approx(1.0, abs=TOL)
    assert evaluate(baseline_device_schedule(second), second, "max_shared") == pytest.approx(1.0)
