"""Regression (F5-05): tiny PV next to tiny loads at negative shared value must stay solvable.

Found by the property test ``test_inv3_cost_and_co2_never_worse_than_the_baseline``: PV of
1e-12 kWh with an inflexible load of 1e-9 kWh and spot -60 ct made CBC report the big-M binary
``shared = min(pv, load)`` as infeasible. Now the binary is only used where the minimum is open
and PV is at least ``SHARED_BINARY_MIN_KWH``.
"""

from datetime import time

import numpy as np
import pandas as pd
import pulp
import pytest

from eeg_optimizer.config import TariffConfig
from eeg_optimizer.devices import Device, ModulatingDevice, TimeWindow
from eeg_optimizer.optimize.milp import SHARED_BINARY_MIN_KWH, build_block_model, solve_block
from eeg_optimizer.optimize.objectives import OptimizationInputs, evaluate
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
N = 8
_HEATER = ModulatingDevice(
    id="heater",
    power_kw=KW(2.0),
    energy_kwh_per_day=KWh(0.5),
    window=TimeWindow(time(0, 0), time(2, 0)),
    baseline_start=time(0, 0),
)


def _inputs(pv_kwh: float, load_kwh: float, devices: tuple[Device, ...]) -> OptimizationInputs:
    index = pd.date_range("2023-06-01", periods=N, freq="15min", tz=TIMEZONE)
    pv = np.zeros(N)
    pv[0] = pv_kwh
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads={"a": pd.DataFrame({"load_kwh": np.full(N, load_kwh)}, index=index)},
        prices=pd.DataFrame({"spot_ct_per_kwh": np.full(N, -60.0)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": np.zeros(N)}, index=index),
        tariffs=_TARIFFS,
        devices={"a": devices},
    )


@pytest.mark.parametrize("load_kwh", [0.0, 1e-9, 0.1])
@pytest.mark.parametrize("pv_kwh", [5e-324, 1e-300, 1e-12, SHARED_BINARY_MIN_KWH, 0.1, 1.0])
@pytest.mark.parametrize("with_device", [False, True])
def test_negative_shared_value_with_tiny_values_is_solved_and_scored_like_evaluate(
    pv_kwh: float, load_kwh: float, *, with_device: bool
) -> None:
    devices: tuple[Device, ...] = (_HEATER,) if with_device else ()
    inputs = _inputs(pv_kwh, load_kwh, devices)

    schedule = solve_block(inputs, "min_cost")
    model = build_block_model(inputs, "min_cost")
    model.problem.solve(pulp.PULP_CBC_CMD(msg=False))

    score = evaluate(schedule, inputs, "min_cost")
    # Below SHARED_BINARY_MIN_KWH the model may share less than the balance: < 1e-6 €.
    value = pulp.value(model.problem.objective) / model.objective_scale
    assert value == pytest.approx(score, abs=1e-6)


def test_binary_only_where_the_minimum_of_pv_and_load_is_open() -> None:
    # pv 1.0 > inflexible 0.1, but the heater (0.5 kWh per interval) can lift the load to 0.6
    # only: load_max <= pv, no binary needed. With pv 0.3 the minimum is open: binary.
    closed = build_block_model(_inputs(1.0, 0.1, (_HEATER,)), "min_cost")
    open_ = build_block_model(_inputs(0.3, 0.1, (_HEATER,)), "min_cost")
    pv_below_load = build_block_model(_inputs(0.05, 0.1, (_HEATER,)), "min_cost")

    assert not closed.problem.isMIP()
    assert open_.problem.isMIP()
    assert not pv_below_load.problem.isMIP()
