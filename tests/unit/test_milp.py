"""Unit tests for the block LP with modulating devices (F5-02)."""

from datetime import time

import numpy as np
import pandas as pd
import pulp
import pytest

from eeg_optimizer.config import TariffConfig
from eeg_optimizer.devices import Device, ModulatingDevice, ProgramDevice, TimeWindow
from eeg_optimizer.optimize.milp import (
    BlockSolveError,
    TaskSpan,
    UnsupportedModelError,
    _exact_energy,
    build_block_model,
    solve_block,
)
from eeg_optimizer.optimize.objectives import (
    OptimizationInputs,
    baseline_device_schedule,
    evaluate,
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


def _day_index() -> pd.DatetimeIndex:
    return pd.date_range("2023-06-01", periods=96, freq="15min", tz=TIMEZONE)


def _inputs(pv: np.ndarray, devices: tuple[Device, ...]) -> OptimizationInputs:
    index = _day_index()
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads={"a": pd.DataFrame({"load_kwh": np.full(96, 0.1)}, index=index)},
        prices=pd.DataFrame({"spot_ct_per_kwh": np.full(96, 10.0)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": np.full(96, 100.0)}, index=index),
        tariffs=_TARIFFS,
        devices={"a": devices},
    )


def _boiler(**options: float | int) -> ModulatingDevice:
    # 2 kW, 2 kWh per day, window 06:00 to 20:00, baseline at 06:00 (no PV there).
    return ModulatingDevice(
        id="boiler",
        power_kw=KW(2.0),
        energy_kwh_per_day=KWh(2.0),
        window=TimeWindow(time(6, 0), time(20, 0)),
        baseline_start=time(6, 0),
        min_run_min=options.get("min_run_min"),  # type: ignore[arg-type]
        min_power_kw=options.get("min_power_kw"),  # type: ignore[arg-type]
    )


def _surplus_12_to_13() -> np.ndarray:
    pv = np.zeros(96)
    pv[48:52] = 0.6  # 12:00 to 13:00: 0.6 kWh per interval, inflexible load 0.1
    return pv


def test_hand_example_load_moves_into_the_only_surplus_hour() -> None:
    inputs = _inputs(_surplus_12_to_13(), (_boiler(),))

    schedule = solve_block(inputs, "max_shared")

    boiler = schedule["a"]["boiler"]
    assert boiler.name == "device_kwh"
    assert boiler.index.equals(inputs.index)
    # Surplus 0.5 kWh per interval * 4 = 2 kWh = exactly the daily energy.
    np.testing.assert_allclose(boiler.iloc[48:52].to_numpy(), 0.5, atol=1e-6)
    assert boiler.drop(boiler.index[48:52]).abs().max() < 1e-6
    assert boiler.sum() == pytest.approx(2.0, abs=1e-6)
    # All PV is shared now: 0.6 * 4 = 2.4 kWh; the baseline (06:00) shares only 0.4 kWh.
    assert evaluate(schedule, inputs, "max_shared") == pytest.approx(2.4, abs=1e-6)
    assert evaluate(baseline_device_schedule(inputs), inputs, "max_shared") == pytest.approx(0.4)


def test_power_limit_spreads_the_load_when_the_surplus_is_large() -> None:
    pv = np.zeros(96)
    pv[48:52] = 5.0  # surplus far above the 0.5 kWh per interval of 2 kW
    inputs = _inputs(pv, (_boiler(),))

    boiler = solve_block(inputs, "max_shared")["a"]["boiler"]

    np.testing.assert_allclose(boiler.iloc[48:52].to_numpy(), 0.5, atol=1e-6)
    assert boiler.max() <= 0.5 + 1e-9


def test_model_without_min_options_has_no_binary_variables() -> None:
    model = build_block_model(_inputs(_surplus_12_to_13(), (_boiler(),)), "max_shared")

    variables = model.problem.variables()
    assert variables
    assert all(v.cat == pulp.LpContinuous for v in variables)
    assert not model.problem.isMIP()


def test_device_variables_only_exist_inside_the_window() -> None:
    model = build_block_model(_inputs(_surplus_12_to_13(), (_boiler(),)), "max_shared")

    dvars = model.device_vars["a"]["boiler"]
    is_var = [not isinstance(item, float) for item in dvars.row]
    assert is_var == [24 <= t < 80 for t in range(96)]  # 06:00 to 20:00
    assert dvars.spans == (TaskSpan(24, 80, 2.0),)
    assert dvars.cap_kwh == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("solver_values", "energy"),
    [
        ([0.4999999, 0.4999999, 0.5, 0.5], 2.0),  # 2e-7 kWh missing (CBC prints ~8 digits)
        ([0.4, 0.4, 0.4, 0.4000003], 1.6),  # 3e-7 kWh too much
        ([0.5, 0.5, 0.5, 0.5000003], 2.0),  # above the cap, clipping restores the energy
        ([-1e-9, 0.3, 0.4999999, 0.2], 1.0),  # negative noise and 1e-7 missing
    ],
)
def test_solver_rounding_is_repaired_to_the_exact_task_energy(
    solver_values: list[float], energy: float
) -> None:
    values = np.asarray([0.0, *solver_values, 0.0])

    _exact_energy(values, TaskSpan(1, 5, energy), 0.5, "test")

    assert values.sum() == pytest.approx(energy, abs=1e-12)
    assert values.min() >= 0.0
    assert values.max() <= 0.5
    assert values[0] == 0.0
    assert values[5] == 0.0


def test_large_energy_deviation_is_an_error_not_repaired() -> None:
    values = np.asarray([0.5, 0.5, 0.5, 0.4])

    with pytest.raises(BlockSolveError, match="misses the task energy"):
        _exact_energy(values, TaskSpan(0, 4, 2.0), 0.5, "device a/boiler")


def test_unsupported_objective_is_rejected() -> None:
    inputs = _inputs(_surplus_12_to_13(), (_boiler(),))
    with pytest.raises(UnsupportedModelError, match="min_cost"):
        solve_block(inputs, "min_cost")


def test_program_devices_are_planned_not_ignored() -> None:
    # Since F5-03 programs are part of the model (F5-02 rejected them); see test_milp_program.py.
    program = ProgramDevice(
        id="wm",
        power_kw=KW(2.0),
        duration_min=60,
        window=TimeWindow(time(8, 0), time(20, 0)),
        baseline_start=time(8, 0),
    )
    wm = solve_block(_inputs(_surplus_12_to_13(), (program,)), "max_shared")["a"]["wm"]
    np.testing.assert_allclose(wm.iloc[48:52].to_numpy(), 0.5)
    assert wm.sum() == pytest.approx(2.0)


@pytest.mark.parametrize("options", [{"min_run_min": 60}, {"min_power_kw": 1.0}])
def test_min_options_are_planned_not_ignored(options: dict[str, float | int]) -> None:
    # Since F5-04 min options are part of the model (F5-02 rejected them); see
    # test_milp_min_options.py. Both options force binaries, the surplus hour is still used.
    inputs = _inputs(_surplus_12_to_13(), (_boiler(**options),))
    assert build_block_model(inputs, "max_shared").problem.isMIP()

    boiler = solve_block(inputs, "max_shared")["a"]["boiler"]

    np.testing.assert_allclose(boiler.iloc[48:52].to_numpy(), 0.5, atol=1e-6)
    assert boiler.sum() == pytest.approx(2.0, abs=1e-9)


def test_household_without_devices_and_no_devices_at_all() -> None:
    inputs = _inputs(_surplus_12_to_13(), ())

    assert solve_block(inputs, "max_shared") == {"a": {}}
