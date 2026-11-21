"""Unit tests for the objective functions (F5-01)."""

from dataclasses import replace
from datetime import time

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer import analysis
from eeg_optimizer.allocation import DynamicProportional
from eeg_optimizer.community import build_inputs
from eeg_optimizer.config import TariffConfig, load_config
from eeg_optimizer.config_store import BUILTIN_CONFIG_DIR
from eeg_optimizer.devices import ModulatingDevice, TimeWindow
from eeg_optimizer.economics import costs, emissions
from eeg_optimizer.optimize.objectives import (
    OBJECTIVES,
    ObjectiveInputError,
    OptimizationInputs,
    baseline_device_schedule,
    evaluate,
    schedule_loads,
)
from eeg_optimizer.units import KW, TIMEZONE, FrameValidationError, KWh

OFFLINE_CONFIG = BUILTIN_CONFIG_DIR / "example_community_offline.yaml"

# Mini example, 4 intervals from 2023-06-01 00:00 (values in kWh per interval):
#   pv            [2, 2, 0, 0]
#   inflexible a  [1, 1, 1, 1], b [0, 0, 1, 0]; household a has the device "ev" (2 kWh)
#   spot          [10, 10, -5, 20] ct/kWh, co2 [100, 100, 50, 400] g/kWh
# Tariffs: spot_markup 2, grid_fee_energy 6, levies_taxes 4, reduction 0.5, feed-in 7
#   -> full import price = spot + 12 = [22, 22, 7, 32], shared price 3 + 4 = 7 (ct/kWh).
#
# early  ev [1, 1, 0, 0]: load [2, 2, 2, 1], shared [2, 2, 0, 0] = 4, export 0, import [0, 0, 2, 1]
#        cost  = 2*7 + 1*32 + 4*7 - 0       = 74 ct;  co2 = 2*50 + 1*400 = 500 g
# late   ev [0, 0, 1, 1]: load [1, 1, 3, 2], shared 2, export [1, 1, 0, 0], import [0, 0, 3, 2]
#        cost  = 3*7 + 2*32 + 2*7 - 2*7     = 85 ct;  co2 = 3*50 + 2*400 = 950 g
# middle ev [0, 1, 1, 0]: load [1, 2, 3, 1], shared 3, export [1, 0, 0, 0], import [0, 0, 3, 1]
#        cost  = 3*7 + 1*32 + 3*7 - 1*7     = 67 ct;  co2 = 3*50 + 1*400 = 550 g
_EV_RUNS = {
    "early": [1.0, 1.0, 0.0, 0.0],
    "late": [0.0, 0.0, 1.0, 1.0],
    "middle": [0.0, 1.0, 1.0, 0.0],
}
_EXPECTED = {
    ("early", "max_shared"): 4.0,
    ("early", "min_cost"): -0.74,
    ("early", "min_co2"): -0.5,
    ("late", "max_shared"): 2.0,
    ("late", "min_cost"): -0.85,
    ("late", "min_co2"): -0.95,
    ("middle", "max_shared"): 3.0,
    ("middle", "min_cost"): -0.67,
    ("middle", "min_co2"): -0.55,
}

_TARIFFS = TariffConfig(
    eeg_type="local",
    energy_price=99.0,  # unused: min_cost uses the spot tariff
    grid_fee_energy=6.0,
    levies_taxes=4.0,
    feed_in_tariff=7.0,
    eeg_internal_price=50.0,  # household view only, does not enter the community cost
    spot_markup=2.0,
    grid_fee_reduction=0.5,
)

_EV = ModulatingDevice(
    id="ev",
    power_kw=KW(4.0),
    energy_kwh_per_day=KWh(2.0),
    window=TimeWindow(time(0, 0), time(1, 0)),
    baseline_start=time(0, 0),
)


def _index() -> pd.DatetimeIndex:
    return pd.date_range("2023-06-01", periods=4, freq="15min", tz=TIMEZONE)


def _mini_inputs() -> OptimizationInputs:
    index = _index()
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": [2.0, 2.0, 0.0, 0.0]}, index=index),
        loads={
            "a": pd.DataFrame({"load_kwh": [1.0, 1.0, 1.0, 1.0]}, index=index),
            "b": pd.DataFrame({"load_kwh": [0.0, 0.0, 1.0, 0.0]}, index=index),
        },
        prices=pd.DataFrame({"spot_ct_per_kwh": [10.0, 10.0, -5.0, 20.0]}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": [100.0, 100.0, 50.0, 400.0]}, index=index),
        tariffs=_TARIFFS,
        devices={"a": (_EV,)},
    )


def _schedule(run: list[float]) -> dict[str, dict[str, pd.Series]]:
    return {"a": {"ev": pd.Series(run, index=_index(), name="device_kwh")}}


# ---------------------------------------------------------------- hand-computed values


def test_objectives_are_the_config_names() -> None:
    assert OBJECTIVES == ("max_shared", "min_cost", "min_co2")


@pytest.mark.parametrize(("run", "objective"), sorted(_EXPECTED))
def test_mini_example_hand_computed(run: str, objective: str) -> None:
    value = evaluate(_schedule(_EV_RUNS[run]), _mini_inputs(), objective)  # type: ignore[arg-type]

    assert isinstance(value, float)
    assert value == pytest.approx(_EXPECTED[(run, objective)], abs=1e-12)


def test_higher_is_better_ranks_runs_per_objective() -> None:
    inputs = _mini_inputs()

    def best(objective: str) -> str:
        return max(
            _EV_RUNS,
            key=lambda run: evaluate(_schedule(_EV_RUNS[run]), inputs, objective),  # type: ignore[arg-type]
        )

    # Most PV is used when the car charges early; the negative spot price favours "middle".
    assert best("max_shared") == "early"
    assert best("min_cost") == "middle"
    assert best("min_co2") == "early"


def test_min_cost_ignores_fixed_energy_price_and_internal_price() -> None:
    inputs = _mini_inputs()
    other = replace(inputs, tariffs=replace(_TARIFFS, energy_price=1.0, eeg_internal_price=1.0))
    schedule = _schedule(_EV_RUNS["early"])

    assert evaluate(schedule, other, "min_cost") == evaluate(schedule, inputs, "min_cost")


def test_schedule_loads_adds_devices() -> None:
    loads = schedule_loads(_schedule(_EV_RUNS["early"]), _mini_inputs())

    assert list(loads) == ["a", "b"]
    np.testing.assert_allclose(loads["a"]["load_kwh"], [2.0, 2.0, 1.0, 1.0])
    np.testing.assert_allclose(loads["b"]["load_kwh"], [0.0, 0.0, 1.0, 0.0])


# ---------------------------------------------------------------- consistency with economics


@pytest.fixture(scope="module")
def offline_example() -> tuple[analysis.BaselineAnalysis, OptimizationInputs]:
    config = load_config(OFFLINE_CONFIG)
    community = build_inputs(config)
    return analysis.analyze(config, community), OptimizationInputs.from_community(community, config)


def test_baseline_score_matches_reported_figures(
    offline_example: tuple[analysis.BaselineAnalysis, OptimizationInputs],
) -> None:
    result, inputs = offline_example
    schedule = baseline_device_schedule(inputs)
    allocation = DynamicProportional().allocate(result.balance, schedule_loads(schedule, inputs))
    spot_cost = costs(result.balance, allocation, inputs.tariffs, inputs.prices)
    emitted = emissions(result.balance, allocation, inputs.co2)

    assert evaluate(schedule, inputs, "max_shared") == pytest.approx(
        result.kpis.shared_kwh, rel=1e-12
    )
    assert evaluate(schedule, inputs, "min_cost") == pytest.approx(
        -spot_cost.community_eur, rel=1e-12
    )
    assert evaluate(schedule, inputs, "min_co2") == pytest.approx(
        -emitted.community_kg_co2, rel=1e-12
    )


# ---------------------------------------------------------------- errors


def test_unknown_objective_raises() -> None:
    with pytest.raises(ObjectiveInputError, match="unknown objective"):
        evaluate(_schedule(_EV_RUNS["early"]), _mini_inputs(), "max_profit")  # type: ignore[arg-type]


def test_missing_device_raises() -> None:
    with pytest.raises(ObjectiveInputError, match="exactly the devices"):
        evaluate({}, _mini_inputs(), "max_shared")


def test_unknown_device_raises() -> None:
    schedule = _schedule(_EV_RUNS["early"])
    schedule["a"]["boiler"] = pd.Series(0.0, index=_index(), name="device_kwh")

    with pytest.raises(ObjectiveInputError, match="exactly the devices"):
        evaluate(schedule, _mini_inputs(), "max_shared")


def test_unknown_household_raises() -> None:
    schedule = _schedule(_EV_RUNS["early"])
    schedule["zz"] = {}

    with pytest.raises(ObjectiveInputError, match="unknown household"):
        evaluate(schedule, _mini_inputs(), "max_shared")


def test_device_series_on_other_index_raises() -> None:
    other = pd.date_range("2023-06-02", periods=4, freq="15min", tz=TIMEZONE)
    schedule = {"a": {"ev": pd.Series(_EV_RUNS["early"], index=other)}}

    with pytest.raises(ObjectiveInputError, match="index differs"):
        evaluate(schedule, _mini_inputs(), "max_shared")


@pytest.mark.parametrize("bad", [-0.5, float("nan")])
def test_invalid_device_energy_raises(bad: float) -> None:
    with pytest.raises(FrameValidationError):
        evaluate(_schedule([1.0, bad, 0.0, 0.0]), _mini_inputs(), "max_shared")


def test_device_schedule_must_be_series() -> None:
    schedule = {"a": {"ev": [1.0, 1.0, 0.0, 0.0]}}

    with pytest.raises(ObjectiveInputError, match="Series expected"):
        evaluate(schedule, _mini_inputs(), "max_shared")  # type: ignore[arg-type]
