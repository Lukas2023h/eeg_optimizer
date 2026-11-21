"""Bound check of the CBC result and the solve without preprocessing (F5-09).

CBC 2.10.3 (PuLP 2.9) reports ``Optimal`` on some blocks but returns a modulating device far
above its upper bound when its MIP preprocessing is on (example community 2023, min_cost,
22.01.-24.01.: EV with 8 kWh in one interval, cap 2.75 kWh). :func:`milp.solve_block` checks
the bounds and solves such a block once more with ``preprocess off``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time
from typing import Any

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.community import build_inputs
from eeg_optimizer.config import TariffConfig, load_config
from eeg_optimizer.config_store import BUILTIN_CONFIG_DIR
from eeg_optimizer.devices import ModulatingDevice, TimeWindow
from eeg_optimizer.optimize import milp, runner
from eeg_optimizer.optimize.milp import (
    BOUND_TOLERANCE_KWH,
    BlockModel,
    BlockSolveError,
    bound_violations,
    build_block_model,
    solve_block,
)
from eeg_optimizer.optimize.objectives import OptimizationInputs
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
_CAP_KWH = 0.5  # 2 kW for 15 min


def _inputs() -> OptimizationInputs:
    index = pd.date_range("2023-06-01", periods=96, freq="15min", tz=TIMEZONE)
    pv = np.zeros(96)
    pv[48:52] = 0.6
    boiler = ModulatingDevice(
        id="boiler",
        power_kw=KW(2.0),
        energy_kwh_per_day=KWh(2.0),
        window=TimeWindow(time(6, 0), time(20, 0)),
        baseline_start=time(6, 0),
    )
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads={"a": pd.DataFrame({"load_kwh": np.full(96, 0.1)}, index=index)},
        prices=pd.DataFrame({"spot_ct_per_kwh": np.full(96, 10.0)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": np.full(96, 100.0)}, index=index),
        tariffs=_TARIFFS,
        devices={"a": (boiler,)},
    )


def _boiler_vars(model: BlockModel) -> list[Any]:
    dvars = model.device_vars["a"]["boiler"]
    span = dvars.spans[0]
    return list(dvars.row[span.start : span.stop])


def _solved_model() -> BlockModel:
    model = build_block_model(_inputs(), "max_shared")
    milp.run_cbc(model, time_limit_s=None, mip_gap=None, preprocess=True)
    return model


def test_solved_model_inside_the_bounds_has_no_violation() -> None:
    assert bound_violations(_solved_model()) == []


@pytest.mark.parametrize(
    "value",
    [
        _CAP_KWH,
        0.0,
        _CAP_KWH + BOUND_TOLERANCE_KWH,  # exactly on the tolerance: still noise
        -BOUND_TOLERANCE_KWH,
        _CAP_KWH + 1e-9,
        -1e-9,
    ],
)
def test_values_on_the_bounds_and_within_the_tolerance_are_noise(value: float) -> None:
    model = _solved_model()
    _boiler_vars(model)[0].varValue = value

    assert bound_violations(model) == []


@pytest.mark.parametrize(
    "value", [_CAP_KWH + 2 * BOUND_TOLERANCE_KWH, -2 * BOUND_TOLERANCE_KWH, 2.0, -0.3]
)
def test_values_beyond_the_tolerance_are_violations(value: float) -> None:
    model = _solved_model()
    variable = _boiler_vars(model)[3]
    variable.varValue = value

    found = bound_violations(model)

    assert len(found) == 1
    assert variable.name in found[0]
    assert "cap 0.5 kWh" in found[0]


def _corrupting_run_cbc(monkeypatch: pytest.MonkeyPatch, corrupt: set[bool]) -> list[bool]:
    """Real CBC, but after a solve with ``preprocess in corrupt`` one boiler value is 4 kWh."""
    calls: list[bool] = []
    original = milp.run_cbc

    def fake(
        model: BlockModel, *, time_limit_s: float | None, mip_gap: float | None, preprocess: bool
    ) -> None:
        calls.append(preprocess)
        original(model, time_limit_s=time_limit_s, mip_gap=mip_gap, preprocess=preprocess)
        if preprocess in corrupt:
            _boiler_vars(model)[0].varValue = 4.0

    monkeypatch.setattr(milp, "run_cbc", fake)
    return calls


def test_bound_violation_is_solved_again_without_preprocessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _corrupting_run_cbc(monkeypatch, corrupt={True})

    boiler = solve_block(_inputs(), "max_shared")["a"]["boiler"]

    assert calls == [True, False]
    assert boiler.max() <= _CAP_KWH
    assert boiler.min() >= 0.0
    assert boiler.sum() == pytest.approx(2.0, abs=1e-9)
    np.testing.assert_allclose(boiler.iloc[48:52].to_numpy(), 0.5, atol=1e-6)


def test_result_inside_the_bounds_is_solved_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _corrupting_run_cbc(monkeypatch, corrupt=set())

    solve_block(_inputs(), "max_shared")

    assert calls == [True]


def test_bound_violation_also_without_preprocessing_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _corrupting_run_cbc(monkeypatch, corrupt={True, False})

    with pytest.raises(BlockSolveError, match="outside the device bounds") as raised:
        solve_block(_inputs(), "max_shared")

    assert calls == [True, False]
    assert "= 4 kWh, cap 0.5 kWh" in str(raised.value)


def test_regression_example_community_january_min_cost_has_no_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real data of the bug: before the fix 23.01.2023 fell back to greedy (EV missed 5.25 kWh)."""
    config = load_config(BUILTIN_CONFIG_DIR / "example_community_offline.yaml")
    full = OptimizationInputs.from_community(build_inputs(config), config)
    cut = slice(None, "2023-01-24")
    inputs = replace(
        full,
        pv=full.pv.loc[cut],
        loads={hh: frame.loc[cut] for hh, frame in full.loads.items()},
        prices=full.prices.loc[cut],
        co2=full.co2.loc[cut],
    )
    calls: list[bool] = []
    original = milp.run_cbc

    def spy(
        model: BlockModel, *, time_limit_s: float | None, mip_gap: float | None, preprocess: bool
    ) -> None:
        calls.append(preprocess)
        original(model, time_limit_s=time_limit_s, mip_gap=mip_gap, preprocess=preprocess)

    monkeypatch.setattr(milp, "run_cbc", spy)

    schedule = runner.optimize(inputs, "min_cost", "milp")

    assert schedule.fallback_blocks == ()
    # Intentionally tied to the pinned CBC 2.10.3 (PuLP 2.9): the bug occurred and was solved
    # without preprocessing. Fails once a CBC/PuLP upgrade fixes it: then re-check the workaround.
    assert False in calls
    for rows in schedule.devices.values():
        for series in rows.values():
            assert series.min() >= 0.0
    assert schedule.score >= schedule.baseline_score
