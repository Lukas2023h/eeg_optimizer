"""Unit tests for the comparison baseline vs. optimized (F6-01)."""

from __future__ import annotations

from dataclasses import replace
from datetime import time

import numpy as np
import pandas as pd
import pytest

from eeg_optimizer.allocation import DynamicProportional
from eeg_optimizer.compare import (
    ADDITIONAL_SHARED_COLUMN,
    AVOIDED_COLUMN,
    COMPARISON_COLUMNS,
    SAVED_COLUMN,
    TOTAL_ROW,
    ComparisonInputError,
    compare,
    compare_schedule,
    scenario,
)
from eeg_optimizer.config import EegType, TariffConfig
from eeg_optimizer.devices import Device, ModulatingDevice, ProgramDevice, TimeWindow
from eeg_optimizer.economics import TariffKind, value_shared_ct_per_kwh
from eeg_optimizer.optimize.objectives import (
    ObjectiveInputError,
    OptimizationInputs,
    baseline_device_schedule,
)
from eeg_optimizer.optimize.runner import optimize
from eeg_optimizer.units import KW, TIMEZONE, KWh, ct_to_eur

_TARIFFS = TariffConfig(
    eeg_type="local",
    energy_price=13.0,
    grid_fee_energy=6.0,
    levies_taxes=6.0,
    feed_in_tariff=7.0,
    eeg_internal_price=12.0,
    spot_markup=2.0,
    grid_fee_reduction=0.57,
)

_DEVICES: dict[str, tuple[Device, ...]] = {
    "hh1": (
        ModulatingDevice(
            id="ev",
            power_kw=KW(11.0),
            energy_kwh_per_day=KWh(8.0),
            window=TimeWindow(time(18), time(7)),
            baseline_start=time(18),
        ),
    ),
    "hh2": (
        ProgramDevice(
            id="washer",
            power_kw=KW(2.0),
            duration_min=90,
            window=TimeWindow(time(6), time(20)),
            baseline_start=time(17),
        ),
    ),
    "hh3": (),
}


def _inputs(days: int = 3) -> OptimizationInputs:
    index = pd.date_range("2023-06-01", periods=96 * days, freq="15min", tz=TIMEZONE)
    hour = np.asarray(index.hour + index.minute / 60.0)
    pv = np.clip(np.sin((hour - 6.0) / 14.0 * np.pi), 0.0, None) * 2.0
    rng = np.random.default_rng(3)
    loads = {
        hh: pd.DataFrame({"load_kwh": 0.05 + 0.1 * rng.random(len(index))}, index=index)
        for hh in _DEVICES
    }
    spot = 8.0 + 6.0 * np.cos((hour - 13.0) / 24.0 * 2 * np.pi)
    co2 = 120.0 + 60.0 * np.cos((hour - 2.0) / 24.0 * 2 * np.pi)
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads=loads,
        prices=pd.DataFrame({"spot_ct_per_kwh": spot}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": co2}, index=index),
        tariffs=_TARIFFS,
        devices=_DEVICES,
    )


@pytest.fixture(scope="module")
def inputs() -> OptimizationInputs:
    return _inputs()


@pytest.fixture(scope="module")
def optimized_devices(inputs: OptimizationInputs) -> dict[str, dict[str, pd.Series]]:
    schedule = optimize(inputs, "max_shared", "milp")
    assert schedule.fallback_ratio == 0.0
    return {hh: dict(devices) for hh, devices in schedule.devices.items()}


# ---------------------------------------------------------------- table layout


def test_table_has_a_row_per_household_plus_total(
    inputs: OptimizationInputs, optimized_devices: dict[str, dict[str, pd.Series]]
) -> None:
    result = compare_schedule(inputs, optimized_devices, DynamicProportional())

    assert list(result.table.index) == ["hh1", "hh2", "hh3", TOTAL_ROW]
    assert list(result.table.columns) == list(COMPARISON_COLUMNS)
    assert result.table.index.name == "household_id"
    assert np.isfinite(result.table.to_numpy(dtype=float)).all()
    assert result.total.to_dict() == result.table.iloc[3].to_dict()
    assert result.total.name == TOTAL_ROW
    assert result.household("hh2").to_dict() == result.table.iloc[1].to_dict()
    assert result.household("hh2").name == "hh2"


def test_household_accessor_rejects_unknown_and_total(
    inputs: OptimizationInputs, optimized_devices: dict[str, dict[str, pd.Series]]
) -> None:
    result = compare_schedule(inputs, optimized_devices, DynamicProportional())
    with pytest.raises(KeyError, match="hh9"):
        result.household("hh9")
    with pytest.raises(KeyError, match=TOTAL_ROW):
        result.household(TOTAL_ROW)


# ---------------------------------------------------------------- households add up to the total


@pytest.mark.parametrize("tariff", ["fixed", "spot"])
def test_households_sum_to_total(
    inputs: OptimizationInputs,
    optimized_devices: dict[str, dict[str, pd.Series]],
    tariff: TariffKind,
) -> None:
    result = compare_schedule(inputs, optimized_devices, DynamicProportional(), tariff)

    households = result.table.drop(index=TOTAL_ROW)
    for column in COMPARISON_COLUMNS:
        assert float(households[column].sum()) == pytest.approx(
            float(result.total[column]), abs=1e-6
        ), column


def test_totals_match_the_community_values(
    inputs: OptimizationInputs, optimized_devices: dict[str, dict[str, pd.Series]]
) -> None:
    result = compare_schedule(inputs, optimized_devices, DynamicProportional())
    base, opt = result.baseline, result.optimized

    shared_delta = float(opt.balance["shared_kwh"].sum() - base.balance["shared_kwh"].sum())
    assert result.additional_shared_kwh == pytest.approx(shared_delta, abs=1e-12)
    assert result.total[ADDITIONAL_SHARED_COLUMN] == pytest.approx(shared_delta, abs=1e-12)
    co2_delta = float(base.emissions.community_kg_co2 - opt.emissions.community_kg_co2)
    assert result.avoided_kg_co2 == pytest.approx(co2_delta, abs=1e-12)
    assert result.total[AVOIDED_COLUMN] == pytest.approx(co2_delta, abs=1e-12)
    assert result.saved_community_eur == pytest.approx(
        float(base.costs.community_eur - opt.costs.community_eur), abs=1e-12
    )
    household_saving = float(
        base.costs.households["cost_eur"].sum() - opt.costs.households["cost_eur"].sum()
    )
    assert result.saved_households_eur == pytest.approx(household_saving, abs=1e-12)
    assert result.total[SAVED_COLUMN] == pytest.approx(household_saving, abs=1e-12)


def test_optimization_for_max_shared_shares_more(
    inputs: OptimizationInputs, optimized_devices: dict[str, dict[str, pd.Series]]
) -> None:
    result = compare_schedule(inputs, optimized_devices, DynamicProportional())
    assert result.additional_shared_kwh > 1.0
    assert result.saved_community_eur > 0.0


# ---------------------------------------------------------------- fixed tariff: € = Δ kWh * value


@pytest.mark.parametrize("eeg_type", ["local", "regional"])
def test_fixed_tariff_saving_is_additional_shared_times_value_shared(
    optimized_devices: dict[str, dict[str, pd.Series]], eeg_type: EegType
) -> None:
    tariffs = replace(_TARIFFS, eeg_type=eeg_type, grid_fee_reduction=None)
    inputs = replace(_inputs(), tariffs=tariffs)

    result = compare_schedule(inputs, optimized_devices, DynamicProportional(), "fixed")

    expected = ct_to_eur(result.additional_shared_kwh * value_shared_ct_per_kwh(tariffs))
    assert result.additional_shared_kwh > 1.0
    assert result.saved_community_eur == pytest.approx(expected, abs=1e-9)


def test_spot_tariff_saving_differs_from_fixed(
    inputs: OptimizationInputs, optimized_devices: dict[str, dict[str, pd.Series]]
) -> None:
    fixed = compare_schedule(inputs, optimized_devices, DynamicProportional(), "fixed")
    spot = compare_schedule(inputs, optimized_devices, DynamicProportional(), "spot")
    assert spot.baseline.costs.tariff == "spot"
    assert fixed.additional_shared_kwh == pytest.approx(spot.additional_shared_kwh, abs=1e-12)
    assert spot.saved_community_eur != pytest.approx(fixed.saved_community_eur, abs=1e-6)


# ---------------------------------------------------------------- identical schedules


@pytest.mark.parametrize("tariff", ["fixed", "spot"])
def test_identical_schedules_give_zero_deltas(
    inputs: OptimizationInputs, tariff: TariffKind
) -> None:
    result = compare_schedule(
        inputs,
        baseline_device_schedule(inputs),
        DynamicProportional(),
        tariff,
    )

    for column in (ADDITIONAL_SHARED_COLUMN, SAVED_COLUMN, AVOIDED_COLUMN):
        assert (result.table[column] == 0.0).all(), column
    assert result.additional_shared_kwh == 0.0
    assert result.saved_community_eur == 0.0
    assert result.saved_households_eur == 0.0
    assert result.avoided_kg_co2 == 0.0


# ---------------------------------------------------------------- input errors


def test_schedule_with_missing_device_is_rejected(inputs: OptimizationInputs) -> None:
    schedule = baseline_device_schedule(inputs)
    del schedule["hh1"]["ev"]
    with pytest.raises(ObjectiveInputError, match="hh1"):
        scenario(inputs, schedule, DynamicProportional())


def test_different_households_are_rejected(inputs: OptimizationInputs) -> None:
    other_inputs = replace(
        inputs,
        loads={k: v for k, v in inputs.loads.items() if k != "hh3"},
        devices={k: v for k, v in inputs.devices.items() if k != "hh3"},
    )
    base = scenario(inputs, baseline_device_schedule(inputs), DynamicProportional())
    other = scenario(other_inputs, baseline_device_schedule(other_inputs), DynamicProportional())
    with pytest.raises(ComparisonInputError, match="different households"):
        compare(base, other)


def test_different_pv_is_rejected(inputs: OptimizationInputs) -> None:
    other_inputs = replace(inputs, pv=inputs.pv * 1.1)
    base = scenario(inputs, baseline_device_schedule(inputs), DynamicProportional())
    other = scenario(other_inputs, baseline_device_schedule(other_inputs), DynamicProportional())
    with pytest.raises(ComparisonInputError, match="PV"):
        compare(base, other)


def test_different_index_is_rejected() -> None:
    short, long = _inputs(2), _inputs(3)
    base = scenario(short, baseline_device_schedule(short), DynamicProportional())
    other = scenario(long, baseline_device_schedule(long), DynamicProportional())
    with pytest.raises(ComparisonInputError, match="time indices"):
        compare(base, other)


def test_different_tariffs_are_rejected(inputs: OptimizationInputs) -> None:
    schedule = baseline_device_schedule(inputs)
    fixed = scenario(inputs, schedule, DynamicProportional(), "fixed")
    spot = scenario(inputs, schedule, DynamicProportional(), "spot")
    with pytest.raises(ComparisonInputError, match="tariffs"):
        compare(fixed, spot)


def test_household_named_like_the_total_row_is_rejected(inputs: OptimizationInputs) -> None:
    renamed = replace(
        inputs,
        loads={TOTAL_ROW if k == "hh3" else k: v for k, v in inputs.loads.items()},
        devices={TOTAL_ROW if k == "hh3" else k: v for k, v in inputs.devices.items()},
    )
    with pytest.raises(ComparisonInputError, match="reserved"):
        scenario(renamed, baseline_device_schedule(renamed), DynamicProportional())
