"""Property tests for the comparison F6-01 on random communities and schedules.

* The household rows add up to the total row (1e-6) for both tariffs.
* Fixed tariff: community saving == additional shared kWh * value of a shared kWh (spec §7.1).
* The baseline compared with itself gives zero deltas.
"""

from __future__ import annotations

import pytest
from hypothesis import given

from eeg_optimizer.allocation import DynamicProportional
from eeg_optimizer.compare import (
    ADDITIONAL_SHARED_COLUMN,
    AVOIDED_COLUMN,
    COMPARISON_COLUMNS,
    SAVED_COLUMN,
    TOTAL_ROW,
    compare_schedule,
)
from eeg_optimizer.economics import value_shared_ct_per_kwh
from eeg_optimizer.optimize.objectives import OptimizationInputs, baseline_device_schedule
from eeg_optimizer.optimize.runner import optimize
from eeg_optimizer.units import ct_to_eur
from tests.property.test_runner import _SETTINGS, period_inputs


@_SETTINGS
@given(inputs=period_inputs())
def test_households_add_up_and_fixed_saving_equals_value_shared(
    inputs: OptimizationInputs,
) -> None:
    schedule = optimize(inputs, "max_shared", "greedy").devices

    for tariff in ("fixed", "spot"):
        result = compare_schedule(inputs, schedule, DynamicProportional(), tariff)
        households = result.table.drop(index=TOTAL_ROW)
        assert len(households) == len(inputs.loads)
        for column in COMPARISON_COLUMNS:
            assert float(households[column].sum()) == pytest.approx(
                float(result.total[column]), abs=1e-6
            ), column
        if tariff == "fixed":
            expected = ct_to_eur(
                result.additional_shared_kwh * value_shared_ct_per_kwh(inputs.tariffs)
            )
            assert result.saved_community_eur == pytest.approx(expected, abs=1e-6)


@_SETTINGS
@given(inputs=period_inputs())
def test_baseline_against_itself_has_zero_deltas(inputs: OptimizationInputs) -> None:
    result = compare_schedule(inputs, baseline_device_schedule(inputs), DynamicProportional())
    for column in (ADDITIONAL_SHARED_COLUMN, SAVED_COLUMN, AVOIDED_COLUMN):
        assert (result.table[column] == 0.0).all(), column
    assert result.saved_community_eur == 0.0
