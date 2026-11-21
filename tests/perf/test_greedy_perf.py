"""Runtime of the greedy heuristic (F5-06): 15 households x 1 day in < 0.5 s."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.config import load_config
from eeg_optimizer.config_store import BUILTIN_CONFIG_DIR
from eeg_optimizer.devices import devices_for
from eeg_optimizer.optimize.greedy import solve_greedy
from eeg_optimizer.optimize.objectives import OBJECTIVES, Objective, OptimizationInputs, evaluate
from eeg_optimizer.units import TIMEZONE

LIMIT_S = 0.5
HOUSEHOLDS = 15


def _block_inputs() -> OptimizationInputs:
    # A runner block: one day plus the following night (48 h index), so the tasks of the day
    # including the windows across midnight (EV 18:00-07:00) are complete. The device mix of
    # the example community (all five devices) for every one of 15 households: 75 devices.
    config = load_config(BUILTIN_CONFIG_DIR / "example_community_offline.yaml")
    mix = max(devices_for(config).values(), key=len)
    start = pd.Timestamp("2023-06-01").tz_localize(TIMEZONE)
    index = pd.date_range(start, start + pd.Timedelta(days=2), freq="15min", inclusive="left")
    n = len(index)
    hours = np.arange(n) % 96 / 4.0
    pv = np.clip(np.sin((hours - 6.0) / 14.0 * np.pi), 0.0, None) * 12.0  # ~45 kWp, 15 min
    rng = np.random.default_rng(0)
    loads = {
        f"hh{h:02d}": pd.DataFrame({"load_kwh": rng.uniform(0.02, 0.3, n)}, index=index)
        for h in range(HOUSEHOLDS)
    }
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads=loads,
        prices=pd.DataFrame({"spot_ct_per_kwh": rng.uniform(-5.0, 30.0, n)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": rng.uniform(50.0, 200.0, n)}, index=index),
        tariffs=config.tariffs,
        devices={household_id: mix for household_id in loads},
    )


@pytest.mark.slow
@pytest.mark.parametrize("objective", OBJECTIVES)
def test_greedy_15_households_one_day_below_half_a_second(objective: Objective) -> None:
    inputs = _block_inputs()
    assert sum(len(devices) for devices in inputs.devices.values()) == 5 * HOUSEHOLDS
    solve_greedy(inputs, objective)  # warm-up (imports, first numpy calls)

    started = time.perf_counter()
    schedule = solve_greedy(inputs, objective)
    elapsed = time.perf_counter() - started

    print(f"greedy {objective}: {elapsed:.3f} s for {HOUSEHOLDS} households")
    assert elapsed < LIMIT_S
    assert np.isfinite(evaluate(schedule, inputs, objective))
