"""Unit tests for the objectives min_cost and min_co2 in the block MILP (F5-05)."""

from datetime import time

import numpy as np
import pandas as pd
import pulp
import pytest

from eeg_optimizer.config import TariffConfig
from eeg_optimizer.devices import Device, ModulatingDevice, ProgramDevice, TimeWindow
from eeg_optimizer.economics import CostInputError, EmissionInputError
from eeg_optimizer.optimize.milp import (
    UnsupportedModelError,
    build_block_model,
    objective_weights,
    solve_block,
)
from eeg_optimizer.optimize.objectives import (
    OBJECTIVES,
    Objective,
    OptimizationInputs,
    baseline_device_schedule,
    evaluate,
)
from eeg_optimizer.units import KW, TIMEZONE, KWh

# Import price = spot + 2 + 6 + 4 = spot + 12 ct; shared price = 3 + 4 = 7 ct; feed-in 7 ct;
# value of one shared kWh = spot + 12 - 7 - 7 = spot - 2 ct.
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
N = 96


def _index() -> pd.DatetimeIndex:
    return pd.date_range("2023-06-01", periods=N, freq="15min", tz=TIMEZONE)


def _inputs(
    devices: tuple[Device, ...],
    *,
    pv: np.ndarray | None = None,
    spot: np.ndarray | None = None,
    co2: np.ndarray | None = None,
) -> OptimizationInputs:
    index = _index()
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": np.zeros(N) if pv is None else pv}, index=index),
        loads={"a": pd.DataFrame({"load_kwh": np.full(N, 0.1)}, index=index)},
        prices=pd.DataFrame(
            {"spot_ct_per_kwh": np.full(N, 10.0) if spot is None else spot}, index=index
        ),
        co2=pd.DataFrame({"co2_g_per_kwh": np.full(N, 200.0) if co2 is None else co2}, index=index),
        tariffs=_TARIFFS,
        devices={"a": devices},
    )


def _boiler(energy_kwh: float) -> ModulatingDevice:
    # 2 kW (0.5 kWh per interval), window 06:00 to 20:00 (positions 24 to 80), baseline 06:00.
    return ModulatingDevice(
        id="boiler",
        power_kw=KW(2.0),
        energy_kwh_per_day=KWh(energy_kwh),
        window=TimeWindow(time(6, 0), time(20, 0)),
        baseline_start=time(6, 0),
    )


def _washer() -> ProgramDevice:
    # 2 kW for 30 min, window 06:00 to 20:00.
    return ProgramDevice(
        id="washer",
        power_kw=KW(2.0),
        duration_min=30,
        window=TimeWindow(time(6, 0), time(20, 0)),
        baseline_start=time(6, 0),
    )


def _only_used_at(series: pd.Series, positions: list[int], value: float) -> None:
    np.testing.assert_allclose(series.iloc[positions].to_numpy(), value, atol=1e-9)
    assert series.drop(series.index[positions]).abs().max() < 1e-9


def test_hand_example_min_cost_without_pv_load_lands_in_the_cheapest_interval() -> None:
    spot = np.full(N, 10.0)
    spot[57] = 3.0  # 14:15, cheapest inside the window
    spot[10] = -20.0  # 02:30, even cheaper but outside the window
    inputs = _inputs((_boiler(0.5),), spot=spot)  # 0.5 kWh = exactly one interval at 2 kW

    schedule = solve_block(inputs, "min_cost")

    _only_used_at(schedule["a"]["boiler"], [57], 0.5)
    # Baseline at 06:00 pays (10 + 12) ct, optimum (3 + 12) ct for the 0.5 kWh: 3.5 ct saved.
    saved = evaluate(schedule, inputs, "min_cost") - evaluate(
        baseline_device_schedule(inputs), inputs, "min_cost"
    )
    assert saved == pytest.approx(0.035, abs=1e-9)


def test_hand_example_min_cost_spreads_over_the_cheapest_intervals_at_full_power() -> None:
    spot = np.full(N, 10.0)
    spot[[30, 44, 70]] = [1.0, 2.0, 0.5]
    inputs = _inputs((_boiler(1.5),), spot=spot)

    schedule = solve_block(inputs, "min_cost")

    _only_used_at(schedule["a"]["boiler"], [30, 44, 70], 0.5)


def test_hand_example_min_co2_without_pv_load_lands_in_the_lowest_carbon_interval() -> None:
    co2 = np.full(N, 200.0)
    co2[41] = 40.0  # 10:15
    co2[0] = 10.0  # outside the window
    spot = np.full(N, 10.0)
    spot[60] = -30.0  # cheapest interval must not matter for min_co2
    inputs = _inputs((_boiler(0.5),), spot=spot, co2=co2)

    schedule = solve_block(inputs, "min_co2")

    _only_used_at(schedule["a"]["boiler"], [41], 0.5)
    # 0.5 kWh at 40 instead of 200 g/kWh: 80 g = 0.08 kg avoided.
    saved = evaluate(schedule, inputs, "min_co2") - evaluate(
        baseline_device_schedule(inputs), inputs, "min_co2"
    )
    assert saved == pytest.approx(0.08, abs=1e-9)


@pytest.mark.parametrize("objective", ["min_cost", "min_co2"])
def test_program_starts_in_the_best_pair_of_intervals(objective: Objective) -> None:
    values = np.full(N, 50.0)
    values[[50, 51]] = [5.0, 6.0]  # best 30-min run starts at 12:30
    values[60] = 1.0  # single best interval, its neighbours are expensive
    inputs = _inputs((_washer(),), spot=values, co2=values * 4.0)

    schedule = solve_block(inputs, objective)

    _only_used_at(schedule["a"]["washer"], [50, 51], 0.5)


def test_min_cost_with_pv_prefers_the_surplus_over_a_cheap_grid_interval() -> None:
    pv = np.zeros(N)
    pv[48] = 0.6  # surplus 0.5 kWh at 12:00
    spot = np.full(N, 10.0)
    spot[30] = 4.0  # grid at 4 + 12 = 16 ct vs. shared at 7 ct + 7 ct lost feed-in = 14 ct
    inputs = _inputs((_boiler(0.5),), pv=pv, spot=spot)

    schedule = solve_block(inputs, "min_cost")

    _only_used_at(schedule["a"]["boiler"], [48], 0.5)


def test_negative_shared_value_keeps_the_balance_rule_shared_is_min_of_pv_and_load() -> None:
    # At 12:00 the spot price is so negative that sharing costs money (value = -50 - 2 ct). The
    # balance still shares min(pv, load), so adding 0.5 kWh there costs 0.5 * 14 ct. At 07:30
    # (no PV) it costs 0.5 * (-10 + 12) ct = 1 ct, which wins. A model that let the solver choose
    # shared = 0 at 12:00 would see 0.5 * (-50 + 12) ct = -19 ct and pick 12:00.
    pv = np.zeros(N)
    pv[48] = 1.0
    spot = np.full(N, 10.0)
    spot[48] = -50.0
    spot[30] = -10.0
    inputs = _inputs((_boiler(0.5),), pv=pv, spot=spot)

    model = build_block_model(inputs, "min_cost")
    # Load can reach at most 0.6 kWh <= PV 1.0 kWh: shared >= load, no binary needed (open
    # cases with binary: test_zz_repro_f505.py).
    assert "shared_is_load_48" in model.problem.constraints
    schedule = solve_block(inputs, "min_cost")

    _only_used_at(schedule["a"]["boiler"], [30], 0.5)


def test_without_negative_shared_value_min_cost_and_min_co2_stay_pure_lps() -> None:
    pv = np.zeros(N)
    pv[40:56] = 1.0
    for objective in ("min_cost", "min_co2"):
        model = build_block_model(_inputs((_boiler(2.0),), pv=pv), objective)
        assert all(v.cat == pulp.LpContinuous for v in model.problem.variables())


@pytest.mark.parametrize("objective", OBJECTIVES)
def test_optimal_lp_value_is_the_score_of_evaluate(objective: Objective) -> None:
    rng = np.random.default_rng(5)
    pv = np.clip(np.sin(np.linspace(0, np.pi, N)) * 1.2 - 0.2, 0.0, None)
    spot = rng.uniform(-60.0, 40.0, N)
    co2 = rng.uniform(20.0, 300.0, N)
    inputs = _inputs((_boiler(3.0), _washer()), pv=pv, spot=spot, co2=co2)

    model = build_block_model(inputs, objective)
    model.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    schedule = solve_block(inputs, objective)

    score = evaluate(schedule, inputs, objective)
    assert pulp.value(model.problem.objective) == pytest.approx(score, abs=1e-6)
    assert score >= evaluate(baseline_device_schedule(inputs), inputs, objective) - 1e-9


def test_weights_use_the_economics_coefficients() -> None:
    spot = np.full(N, 10.0)
    co2 = np.full(N, 250.0)
    inputs = _inputs((), spot=spot, co2=co2)

    cost = objective_weights(inputs, "min_cost")
    emitted = objective_weights(inputs, "min_co2")
    shared = objective_weights(inputs, "max_shared")

    np.testing.assert_allclose(cost.load_weight, 0.22)  # (10 + 2 + 6 + 4) ct in €
    np.testing.assert_allclose(cost.pv_weight, 0.07)
    np.testing.assert_allclose(cost.shared_weight, 0.08)  # 22 - 7 - 7 ct
    np.testing.assert_allclose(emitted.shared_weight, 0.25)
    np.testing.assert_allclose(emitted.load_weight, 0.25)
    np.testing.assert_allclose(emitted.pv_weight, 0.0)
    np.testing.assert_allclose(shared.shared_weight, 1.0)


def test_unknown_objective_is_rejected() -> None:
    with pytest.raises(UnsupportedModelError, match="median_cost"):
        build_block_model(_inputs((_boiler(0.5),)), "median_cost")  # type: ignore[arg-type]


def test_wrong_price_and_co2_frames_raise_the_economics_errors() -> None:
    inputs = _inputs((_boiler(0.5),))
    bad_prices = inputs.prices.rename(columns={"spot_ct_per_kwh": "price_ct_per_kwh"})
    bad_co2 = inputs.co2.iloc[:-1]

    with pytest.raises(CostInputError, match="spot_ct_per_kwh"):
        build_block_model(
            OptimizationInputs(
                inputs.pv, inputs.loads, bad_prices, inputs.co2, inputs.tariffs, inputs.devices
            ),
            "min_cost",
        )
    with pytest.raises(EmissionInputError, match="different index"):
        build_block_model(
            OptimizationInputs(
                inputs.pv, inputs.loads, inputs.prices, bad_co2, inputs.tariffs, inputs.devices
            ),
            "min_co2",
        )
