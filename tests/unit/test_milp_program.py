"""Unit tests for program devices in the block MILP (F5-03): one start binary per start."""

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
    _chosen_start,
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


def _inputs(
    pv: np.ndarray, devices: tuple[Device, ...], day: str = "2023-06-01"
) -> OptimizationInputs:
    # Local calendar day: 92 intervals in spring, 100 in autumn DST.
    start = pd.Timestamp(day).tz_localize(TIMEZONE)
    end = (pd.Timestamp(day) + pd.Timedelta(days=1)).tz_localize(TIMEZONE)
    index = pd.date_range(start, end, freq="15min", inclusive="left")
    n = len(index)
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv[:n]}, index=index),
        loads={"a": pd.DataFrame({"load_kwh": np.full(n, 0.1)}, index=index)},
        prices=pd.DataFrame({"spot_ct_per_kwh": np.full(n, 10.0)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": np.full(n, 100.0)}, index=index),
        tariffs=_TARIFFS,
        devices={"a": devices},
    )


def _washer(
    duration_min: int = 90, window: tuple[time, time] = (time(8), time(20))
) -> ProgramDevice:
    # 2 kW -> 0.5 kWh per interval; baseline at the window start (no PV there).
    return ProgramDevice(
        id="wm",
        power_kw=KW(2.0),
        duration_min=duration_min,
        window=TimeWindow(*window),
        baseline_start=window[0],
    )


def _surplus_11_to_1230() -> np.ndarray:
    pv = np.zeros(96)
    pv[44:50] = 0.6  # 11:00 to 12:30: surplus 0.5 kWh per interval over the load of 0.1
    return pv


def test_hand_example_program_of_90_minutes_runs_in_the_surplus() -> None:
    inputs = _inputs(_surplus_11_to_1230(), (_washer(),))

    wm = solve_block(inputs, "max_shared")["a"]["wm"]

    assert wm.name == "device_kwh"
    expected = np.zeros(96)
    expected[44:50] = 0.5  # 6 intervals at 2 kW = 90 min starting 11:00
    np.testing.assert_array_equal(wm.to_numpy(), expected)
    assert wm.index[44] == pd.Timestamp("2023-06-01 11:00", tz=TIMEZONE)
    # All 3.6 kWh PV shared; the baseline (08:00) shares nothing but the load 0.1 * 6.
    assert evaluate({"a": {"wm": wm}}, inputs, "max_shared") == pytest.approx(3.6)
    assert evaluate(baseline_device_schedule(inputs), inputs, "max_shared") == pytest.approx(0.6)


def test_program_profile_is_exact_without_rescaling() -> None:
    # The run is rebuilt from the chosen start: every value is exactly 0 or the cap.
    wm = solve_block(_inputs(_surplus_11_to_1230(), (_washer(duration_min=45),)), "max_shared")
    values = wm["a"]["wm"].to_numpy()

    assert set(np.unique(values)) == {0.0, 0.5}
    assert values.sum() == 1.5


def test_one_binary_per_feasible_start_and_exactly_one_chosen() -> None:
    model = build_block_model(_inputs(_surplus_11_to_1230(), (_washer(),)), "max_shared")

    span = model.device_vars["a"]["wm"].spans[0]
    assert (span.start, span.stop, span.run_intervals) == (32, 80, 6)  # 08:00 to 20:00
    assert len(span.start_vars) == 48 - 6 + 1  # last start 18:30, run ends 20:00
    # PuLP stores LpBinary as an integer variable with bounds 0 and 1.
    assert all((v.cat, v.lowBound, v.upBound) == (pulp.LpInteger, 0, 1) for v in span.start_vars)
    assert model.problem.isMIP()
    one_start = model.problem.constraints["one_start_0_0_0"]
    assert one_start.constant == -1
    assert {v.name for v in one_start} == {v.name for v in span.start_vars}


def test_program_energy_expression_is_zero_outside_the_window() -> None:
    model = build_block_model(_inputs(_surplus_11_to_1230(), (_washer(),)), "max_shared")

    row = model.device_vars["a"]["wm"].row
    assert all(isinstance(item, float) and item == 0.0 for item in row[:32] + row[80:])
    assert all(not isinstance(item, float) for item in row[32:80])


def test_program_ending_exactly_at_the_window_end() -> None:
    pv = np.zeros(96)
    pv[74:80] = 0.6  # 18:30 to 20:00 = the last six intervals of the window
    wm = solve_block(_inputs(pv, (_washer(),)), "max_shared")["a"]["wm"]

    assert wm.iloc[74:80].tolist() == [0.5] * 6
    assert wm.sum() == pytest.approx(3.0)


def test_program_window_over_midnight() -> None:
    # Window 22:00 to 06:00 (next day); artificial PV at night, 02.06. 02:00 to 03:00.
    pv = np.zeros(2 * 96)
    pv[96 + 8 : 96 + 12] = 0.6  # day 2, 02:00 to 03:00
    start = pd.Timestamp("2023-06-01").tz_localize(TIMEZONE)
    index = pd.date_range(start, periods=2 * 96, freq="15min")
    washer = _washer(duration_min=60, window=(time(22), time(6)))
    inputs = OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv}, index=index),
        loads={"a": pd.DataFrame({"load_kwh": np.full(2 * 96, 0.1)}, index=index)},
        prices=pd.DataFrame({"spot_ct_per_kwh": np.full(2 * 96, 10.0)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": np.full(2 * 96, 100.0)}, index=index),
        tariffs=_TARIFFS,
        devices={"a": (washer,)},
    )

    wm = solve_block(inputs, "max_shared")["a"]["wm"]

    assert wm.iloc[96 + 8 : 96 + 12].tolist() == [0.5] * 4
    assert wm.sum() == pytest.approx(2.0)  # only the task 01.06. 22:00 fits in the index


def test_program_that_no_longer_fits_on_the_spring_dst_day_is_an_error() -> None:
    # 01:00 to 04:00 lasts only 2 h on 2023-03-26; a 150-min program has no start there.
    washer = _washer(duration_min=150, window=(time(1), time(4)))
    inputs = _inputs(np.zeros(96), (washer,), day="2023-03-26")

    with pytest.raises(BlockSolveError, match="does not fit"):
        build_block_model(inputs, "max_shared")


def test_program_on_the_autumn_dst_day_uses_the_longer_window() -> None:
    # 01:00 to 04:00 lasts 4 h on 2023-10-29: a 4-h program fits exactly.
    washer = _washer(duration_min=240, window=(time(1), time(4)))
    inputs = _inputs(np.zeros(100), (washer,), day="2023-10-29")

    wm = solve_block(inputs, "max_shared")["a"]["wm"]

    assert len(wm) == 100
    assert wm.iloc[4:20].tolist() == [0.5] * 16
    assert wm.sum() == pytest.approx(8.0)


def test_mixed_devices_share_the_surplus() -> None:
    # Surplus 1.0 kWh per interval for 6 intervals: program (0.5) and boiler (0.5) fit both.
    pv = np.zeros(96)
    pv[44:50] = 1.1
    boiler = ModulatingDevice(
        id="boiler",
        power_kw=KW(2.0),
        energy_kwh_per_day=KWh(3.0),
        window=TimeWindow(time(6), time(20)),
        baseline_start=time(6),
    )
    inputs = _inputs(pv, (_washer(), boiler))

    schedule = solve_block(inputs, "max_shared")

    assert schedule["a"]["wm"].iloc[44:50].tolist() == [0.5] * 6
    np.testing.assert_allclose(schedule["a"]["boiler"].iloc[44:50].to_numpy(), 0.5, atol=1e-6)
    assert evaluate(schedule, inputs, "max_shared") == pytest.approx(6.6, abs=1e-6)


def _span_with_values(values: list[float]) -> TaskSpan:
    starts = []
    for i, value in enumerate(values):
        var = pulp.LpVariable(f"y_{i}", cat=pulp.LpBinary)
        var.varValue = value
        starts.append(var)
    return TaskSpan(10, 20, 1.0, tuple(starts), 2)


def test_chosen_start_is_the_position_of_the_one_binary() -> None:
    assert _chosen_start(_span_with_values([0.0, 0.0, 1.0, 0.0]), "test") == 12
    assert _chosen_start(_span_with_values([1e-9, 0.9999999, 0.0]), "test") == 11


@pytest.mark.parametrize(
    "values",
    [[1.0, 1.0, 0.0], [0.0, 0.0, 0.0], [0.5, 0.5, 0.0], [0.0, 0.9, 0.0]],
)
def test_chosen_start_rejects_zero_two_or_fractional_starts(values: list[float]) -> None:
    with pytest.raises(BlockSolveError, match="exactly one start"):
        _chosen_start(_span_with_values(values), "device a/wm")
