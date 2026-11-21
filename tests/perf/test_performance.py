"""INV-5 (F5-08): 15 households x 1 year x 15 min are optimized in < 60 s (MILP, max_shared).

Synthetic data, offline: PV and loads are generated, the devices are the example community's
(EV, boiler, heat pump, washing machine, dishwasher), 3 to 5 per household. The wall-clock time
of :func:`runner.optimize` is measured around the call and asserted.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.config import load_config
from eeg_optimizer.config_store import BUILTIN_CONFIG_DIR
from eeg_optimizer.devices import devices_for
from eeg_optimizer.optimize.objectives import OptimizationInputs
from eeg_optimizer.optimize.runner import optimize
from eeg_optimizer.units import TIMEZONE

LIMIT_S = 60.0
HOUSEHOLDS = 15
YEAR = 2023


def _year_inputs() -> OptimizationInputs:
    config = load_config(BUILTIN_CONFIG_DIR / "example_community_offline.yaml")
    mix = max(devices_for(config).values(), key=len)
    assert len(mix) == 5
    start = pd.Timestamp(f"{YEAR}-01-01").tz_localize(TIMEZONE)
    end = pd.Timestamp(f"{YEAR + 1}-01-01").tz_localize(TIMEZONE)
    index = pd.date_range(start, end, freq="15min", inclusive="left")
    n = len(index)
    rng = np.random.default_rng(5)
    hours = (index.hour + index.minute / 60.0).to_numpy(dtype=float)
    season = 0.6 + 0.4 * np.sin((index.day_of_year.to_numpy() - 80) / 365.0 * 2.0 * np.pi)
    clouds = rng.uniform(0.3, 1.0, n // 96 + 1)[np.arange(n) // 96]
    pv = np.clip(np.sin((hours - 6.0) / 14.0 * np.pi), 0.0, None) * 12.0 * season * clouds
    loads = {
        f"hh{h:02d}": pd.DataFrame({"load_kwh": rng.uniform(0.02, 0.3, n)}, index=index)
        for h in range(HOUSEHOLDS)
    }
    # 3, 4 or 5 devices per household (the first k of the example mix), 60 devices in total.
    devices = {household_id: mix[: 3 + h % 3] for h, household_id in enumerate(loads)}
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads=loads,
        prices=pd.DataFrame({"spot_ct_per_kwh": rng.uniform(-5.0, 30.0, n)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": rng.uniform(50.0, 200.0, n)}, index=index),
        tariffs=config.tariffs,
        devices=devices,
    )


@pytest.mark.slow
def test_inv5_15_households_one_year_milp_below_60_seconds() -> None:
    inputs = _year_inputs()
    assert len(inputs.loads) == HOUSEHOLDS
    assert all(3 <= len(devices) <= 5 for devices in inputs.devices.values())
    assert len(inputs.index) == 365 * 96

    started = time.perf_counter()
    schedule = optimize(inputs, "max_shared", "milp")
    elapsed = time.perf_counter() - started

    print(
        f"INV-5: {elapsed:.1f} s wall clock, {len(schedule.block_days)} blocks, "
        f"fallback_ratio {schedule.fallback_ratio}, "
        f"shared {schedule.baseline_score:.1f} -> {schedule.score:.1f} kWh"
    )
    assert elapsed < LIMIT_S
    assert schedule.fallback_ratio == 0
    assert schedule.score >= schedule.baseline_score
