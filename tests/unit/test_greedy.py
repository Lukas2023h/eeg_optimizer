"""Unit tests for the greedy heuristic (F5-06): hand examples per device kind and objective."""

from datetime import time

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.config import TariffConfig
from eeg_optimizer.devices import Device, ModulatingDevice, ProgramDevice, TimeWindow
from eeg_optimizer.optimize.greedy import GreedyScheduleError, solve_greedy
from eeg_optimizer.optimize.milp import UnsupportedModelError, solve_block
from eeg_optimizer.optimize.objectives import ObjectiveInputError, OptimizationInputs, evaluate
from eeg_optimizer.units import KW, TIMEZONE, KWh, kw_to_kwh

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


def _inputs(
    pv: np.ndarray,
    devices: dict[str, tuple[Device, ...]],
    day: str = "2023-06-01",
    spot: np.ndarray | None = None,
    co2: np.ndarray | None = None,
) -> OptimizationInputs:
    start = pd.Timestamp(day).tz_localize(TIMEZONE)
    end = (pd.Timestamp(day) + pd.Timedelta(days=1)).tz_localize(TIMEZONE)
    index = pd.date_range(start, end, freq="15min", inclusive="left")
    n = len(index)
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv[:n]}, index=index),
        loads={hh: pd.DataFrame({"load_kwh": np.full(n, 0.1)}, index=index) for hh in devices},
        prices=pd.DataFrame(
            {"spot_ct_per_kwh": np.full(n, 10.0) if spot is None else spot[:n]}, index=index
        ),
        co2=pd.DataFrame(
            {"co2_g_per_kwh": np.full(n, 100.0) if co2 is None else co2[:n]}, index=index
        ),
        tariffs=_TARIFFS,
        devices=devices,
    )


def _boiler(
    energy: float = 2.0,
    window: tuple[time, time] = (time(8), time(20)),
    device_id: str = "boiler",
    **options: float | int,
) -> ModulatingDevice:
    # 2 kW -> at most 0.5 kWh per interval; baseline at the window start.
    return ModulatingDevice(
        id=device_id,
        power_kw=KW(2.0),
        energy_kwh_per_day=KWh(energy),
        window=TimeWindow(*window),
        baseline_start=window[0],
        **options,  # type: ignore[arg-type]
    )


def _washer(
    duration_min: int = 90, window: tuple[time, time] = (time(8), time(20))
) -> ProgramDevice:
    return ProgramDevice(
        id="wm",
        power_kw=KW(2.0),
        duration_min=duration_min,
        window=TimeWindow(*window),
        baseline_start=window[0],
    )


def _surplus(level: float, slots: slice = slice(44, 50)) -> np.ndarray:
    pv = np.zeros(96)
    pv[slots] = level  # load is 0.1 per interval, surplus = level - 0.1
    return pv


def test_modulating_device_fills_the_surplus_earliest_first() -> None:
    inputs = _inputs(_surplus(0.6), {"a": (_boiler(),)})

    boiler = solve_greedy(inputs, "max_shared")["a"]["boiler"]

    assert boiler.name == "device_kwh"
    assert boiler.index.equals(inputs.index)
    expected = np.zeros(96)
    expected[44:48] = 0.5  # 11:00-12:00 at full power, surplus 0.5 per interval
    np.testing.assert_allclose(boiler.to_numpy(), expected)
    schedule = {"a": {"boiler": boiler}}
    assert evaluate(schedule, inputs, "max_shared") == pytest.approx(0.6 + 2.0)


def test_modulating_device_puts_only_the_rest_outside_the_surplus() -> None:
    # Surplus 0.3 kWh in 6 intervals = 1.8 kWh; the remaining 0.2 kWh go to the earliest
    # window interval (value 0 everywhere outside the surplus, tie -> earliest).
    inputs = _inputs(_surplus(0.4), {"a": (_boiler(),)})

    boiler = solve_greedy(inputs, "max_shared")["a"]["boiler"].to_numpy()

    np.testing.assert_allclose(boiler[44:50], 0.3)
    assert boiler[32] == pytest.approx(0.2)  # 08:00
    assert boiler.sum() == pytest.approx(2.0)
    greedy = evaluate(
        {"a": {"boiler": pd.Series(boiler, index=inputs.index)}}, inputs, "max_shared"
    )
    optimum = evaluate(solve_block(inputs, "max_shared"), inputs, "max_shared")
    assert greedy == pytest.approx(optimum)


def test_program_runs_in_one_piece_at_the_best_start() -> None:
    inputs = _inputs(_surplus(0.6), {"a": (_washer(),)})

    wm = solve_greedy(inputs, "max_shared")["a"]["wm"].to_numpy()

    expected = np.zeros(96)
    expected[44:50] = 0.5  # 11:00-12:30, the only start with the run fully in the surplus
    np.testing.assert_array_equal(wm, expected)


def test_program_that_does_not_fit_the_spring_dst_window_raises() -> None:
    # 2023-03-26: 00:00-04:00 is only 3 h long, a 4 h program has no start.
    washer = _washer(duration_min=240, window=(time(0), time(4)))
    inputs = _inputs(np.zeros(96), {"a": (washer,)}, day="2023-03-26")

    with pytest.raises(GreedyScheduleError, match="a/wm"):
        solve_greedy(inputs, "max_shared")


def test_min_cost_moves_the_energy_into_the_cheapest_hour() -> None:
    spot = np.full(96, 20.0)
    spot[60:64] = -5.0  # 15:00-16:00
    inputs = _inputs(np.zeros(96), {"a": (_boiler(),)}, spot=spot)

    boiler = solve_greedy(inputs, "min_cost")["a"]["boiler"].to_numpy()

    expected = np.zeros(96)
    expected[60:64] = 0.5
    np.testing.assert_allclose(boiler, expected)


def test_min_co2_moves_the_energy_into_the_cleanest_hour() -> None:
    co2 = np.full(96, 300.0)
    co2[36:40] = 50.0  # 09:00-10:00
    inputs = _inputs(np.zeros(96), {"a": (_boiler(),)}, co2=co2)

    boiler = solve_greedy(inputs, "min_co2")["a"]["boiler"].to_numpy()

    expected = np.zeros(96)
    expected[36:40] = 0.5
    np.testing.assert_allclose(boiler, expected)


def test_least_flexible_task_is_placed_first() -> None:
    # Surplus 0.5 kWh at 12:00 and 13:00. The tight device (window 12:00-12:15, slack 0) must
    # run at 12:00; placed first, the flexible one takes 13:00 and both are fully shared.
    # Placed in input order, the flexible one would take 12:00 (earliest) and lose 0.5 kWh.
    pv = np.zeros(96)
    pv[[48, 52]] = 0.6
    flexible = _boiler(0.5, (time(10), time(14)), "flexible")
    tight = _boiler(0.5, (time(12), time(12, 15)), "tight")
    inputs = _inputs(pv, {"a": (flexible, tight)})

    schedule = solve_greedy(inputs, "max_shared")

    assert schedule["a"]["tight"].to_numpy()[48] == pytest.approx(0.5)
    assert schedule["a"]["flexible"].to_numpy()[52] == pytest.approx(0.5)
    assert evaluate(schedule, inputs, "max_shared") == pytest.approx(1.2)


def test_min_options_give_one_run_with_min_power_and_min_run() -> None:
    # Surplus only at 11:00 and 11:15. 1 kWh with >= 1 kW (0.25 kWh) and >= 60 min: runs of
    # 2..4 intervals are possible by power, min_run leaves 4 intervals at 0.25 kWh; every
    # start covering both surplus intervals is equal, the earliest (10:30) wins.
    pv = np.zeros(96)
    pv[44:46] = 0.6
    boiler = _boiler(1.0, min_power_kw=KW(1.0), min_run_min=60)
    inputs = _inputs(pv, {"a": (boiler,)})

    values = solve_greedy(inputs, "max_shared")["a"]["boiler"].to_numpy()

    expected = np.zeros(96)
    expected[42:46] = 0.25
    np.testing.assert_allclose(values, expected)


def test_min_options_prefer_few_strong_intervals_in_a_short_surplus() -> None:
    # Only min_power: 1 kWh at 0.5 kWh in the two surplus intervals is best.
    pv = np.zeros(96)
    pv[44:46] = 0.6
    inputs = _inputs(pv, {"a": (_boiler(1.0, min_power_kw=KW(1.0)),)})

    values = solve_greedy(inputs, "max_shared")["a"]["boiler"].to_numpy()

    expected = np.zeros(96)
    expected[44:46] = 0.5
    np.testing.assert_allclose(values, expected)


def test_energy_exactly_k_times_the_min_power_is_placed_on_the_floor() -> None:
    # 0.3 kWh with >= 0.4 kW (0.1 kWh) and >= 45 min: only k = 3 fits, and 0.3 / 3 is
    # 0.09999999999999999 < 0.1 in floats (CLAUDE.md solver-precision: values on the bound).
    assert float(kw_to_kwh(KW(0.4))) > 0.3 / 3
    boiler = _boiler(0.3, min_power_kw=KW(0.4), min_run_min=45)
    inputs = _inputs(np.zeros(96), {"a": (boiler,)})

    values = solve_greedy(inputs, "max_shared")["a"]["boiler"].to_numpy()

    running = values[values > 0.0]
    assert len(running) == 3
    assert running.min() >= float(kw_to_kwh(KW(0.4)))
    assert values.sum() == pytest.approx(0.3, abs=1e-12)


def test_energy_exactly_k_times_the_power_limit_stays_at_the_cap() -> None:
    # 3.7 kW: (3 · 0.925) / 3 is 1 ulp above 0.925; min power = power leaves only k = 3.
    cap = float(kw_to_kwh(KW(3.7)))
    energy = cap * 3
    assert energy / 3 > cap
    device = ModulatingDevice(
        id="boiler",
        power_kw=KW(3.7),
        energy_kwh_per_day=KWh(energy),
        window=TimeWindow(time(8), time(20)),
        baseline_start=time(8),
        min_power_kw=KW(3.7),
    )
    inputs = _inputs(np.zeros(96), {"a": (device,)})

    values = solve_greedy(inputs, "max_shared")["a"]["boiler"].to_numpy()

    np.testing.assert_array_equal(values[values > 0.0], [cap, cap, cap])


def test_min_run_off_the_grid_is_rejected_not_rounded() -> None:
    inputs = _inputs(np.zeros(96), {"a": (_boiler(1.0, min_run_min=50),)})

    with pytest.raises(UnsupportedModelError, match="min_run_min 50"):
        solve_greedy(inputs, "max_shared")


def test_min_power_that_cannot_split_the_energy_raises() -> None:
    # 0.6 kWh at exactly 2 kW (0.5 kWh): 1 interval is too much, 2 intervals too little.
    inputs = _inputs(np.zeros(96), {"a": (_boiler(0.6, min_power_kw=KW(2.0)),)})

    with pytest.raises(GreedyScheduleError, match="a/boiler"):
        solve_greedy(inputs, "max_shared")


def test_result_has_every_device_of_every_household() -> None:
    inputs = _inputs(_surplus(0.6), {"a": (_boiler(), _washer()), "b": ()})

    schedule = solve_greedy(inputs, "max_shared")

    assert set(schedule) == {"a", "b"}
    assert set(schedule["a"]) == {"boiler", "wm"}
    assert schedule["b"] == {}
    # The washer has slack 48 - 6, the boiler 48 - 4: the washer gets the surplus first.
    np.testing.assert_array_equal(schedule["a"]["wm"].to_numpy()[44:50], 0.5)


def test_devices_of_an_unknown_household_are_rejected() -> None:
    inputs = _inputs(np.zeros(96), {"a": (_boiler(),)})
    broken = OptimizationInputs(
        pv=inputs.pv,
        loads=inputs.loads,
        prices=inputs.prices,
        co2=inputs.co2,
        tariffs=inputs.tariffs,
        devices={"a": (), "x": (_boiler(),)},
    )

    with pytest.raises(ObjectiveInputError, match="x"):
        solve_greedy(broken, "max_shared")


def test_unknown_objective_is_rejected() -> None:
    inputs = _inputs(np.zeros(96), {"a": (_boiler(),)})

    with pytest.raises(UnsupportedModelError, match="max_fun"):
        solve_greedy(inputs, "max_fun")  # type: ignore[arg-type]
