"""Unit tests for min_power_kw / min_run_min of modulating devices in the block MILP (F5-04)."""

from datetime import time

import numpy as np
import pandas as pd
import pulp
import pytest

from eeg_optimizer.config import TariffConfig
from eeg_optimizer.devices import Device, ModulatingDevice, TimeWindow
from eeg_optimizer.optimize.milp import (
    RUN_FLOOR_SHARE,
    BlockSolveError,
    TaskSpan,
    UnsupportedModelError,
    _exact_energy,
    _running_count_bounds,
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
_DAY_WINDOW = TimeWindow(time(6, 0), time(20, 0))


def _inputs(
    pv: np.ndarray, devices: tuple[Device, ...], day: str = "2023-06-01"
) -> OptimizationInputs:
    index = pd.date_range(
        day,
        f"{pd.Timestamp(day) + pd.Timedelta(days=1):%Y-%m-%d}",
        freq="15min",
        tz=TIMEZONE,
        inclusive="left",
    )
    n = len(index)
    return OptimizationInputs(
        pv=pd.DataFrame({"pv_kwh": pv[:n]}, index=index),
        loads={"a": pd.DataFrame({"load_kwh": np.full(n, 0.1)}, index=index)},
        prices=pd.DataFrame({"spot_ct_per_kwh": np.full(n, 10.0)}, index=index),
        co2=pd.DataFrame({"co2_g_per_kwh": np.full(n, 100.0)}, index=index),
        tariffs=_TARIFFS,
        devices={"a": devices},
    )


def _boiler(
    energy: float = 2.0,
    *,
    min_run_min: int | None = None,
    min_power_kw: float | None = None,
    window: TimeWindow = _DAY_WINDOW,
) -> ModulatingDevice:
    # 2 kW = 0.5 kWh per interval; baseline at the window start.
    return ModulatingDevice(
        id="boiler",
        power_kw=KW(2.0),
        energy_kwh_per_day=KWh(energy),
        window=window,
        baseline_start=window.start,
        min_run_min=min_run_min,
        min_power_kw=None if min_power_kw is None else KW(min_power_kw),
    )


def _runs(values: np.ndarray) -> list[tuple[int, int]]:
    """``(first, length)`` of every run of ``values > 0``."""
    running = np.concatenate([[False], values > 0.0, [False]]).astype(int)
    edges = np.diff(running)
    starts = np.flatnonzero(edges == 1)
    stops = np.flatnonzero(edges == -1)
    return [(int(a), int(b - a)) for a, b in zip(starts, stops, strict=True)]


def test_min_power_lifts_every_running_interval() -> None:
    # Surplus only 0.2 kWh per interval 12:00 to 13:00 (PV 0.3, load 0.1), 0.8 kWh per day.
    # Without min_power the only optimum is 0.2 in each of the 4 intervals; with 1.2 kW
    # (0.3 kWh) every running interval needs 0.3, so at most 2 intervals run.
    pv = np.zeros(96)
    pv[48:52] = 0.3
    plain = solve_block(_inputs(pv, (_boiler(0.8),)), "max_shared")["a"]["boiler"].to_numpy()
    np.testing.assert_allclose(plain[48:52], 0.2, atol=1e-6)

    inputs = _inputs(pv, (_boiler(0.8, min_power_kw=1.2),))
    values = solve_block(inputs, "max_shared")["a"]["boiler"].to_numpy()

    assert values.sum() == pytest.approx(0.8, abs=1e-9)
    assert values[values > 0.0].min() >= 0.3
    assert values.max() <= 0.5
    assert np.count_nonzero(values) == 2
    # Both runs lie in the surplus hour (shared 0.2 + 0.2 there is the optimum).
    assert set(np.flatnonzero(values)) <= set(range(48, 52))


def test_min_run_merges_short_runs_into_long_ones() -> None:
    # Two single surplus intervals 10:00 and 14:00 (0.5 kWh each). Without min_run the LP runs
    # exactly there (two runs of 1 interval); with 60 min every run lasts 4 intervals.
    pv = np.zeros(96)
    pv[[40, 56]] = 0.6
    plain = solve_block(_inputs(pv, (_boiler(1.0),)), "max_shared")["a"]["boiler"].to_numpy()
    assert [length for _, length in _runs(plain)] == [1, 1]

    inputs = _inputs(pv, (_boiler(1.0, min_run_min=60),))
    values = solve_block(inputs, "max_shared")["a"]["boiler"].to_numpy()

    assert values.sum() == pytest.approx(1.0, abs=1e-9)
    assert all(length >= 4 for _, length in _runs(values))
    # Floor for min_run only: 1 % of cap, tiny compared to the surplus that is still used.
    assert values[values > 0.0].min() >= RUN_FLOOR_SHARE * 0.5 - 1e-12
    assert values[40] > 0.45
    assert values[56] > 0.45


def test_min_run_does_not_need_time_after_the_window() -> None:
    # Surplus only in the last window interval (19:45): a run must still end by 20:00.
    pv = np.zeros(96)
    pv[79] = 0.6
    values = solve_block(_inputs(pv, (_boiler(1.0, min_run_min=60),)), "max_shared")["a"][
        "boiler"
    ].to_numpy()

    assert np.abs(values[80:]).max() == 0.0
    runs = _runs(values)
    assert all(length >= 4 for _, length in runs)
    assert runs[-1][0] + runs[-1][1] == 80


def test_both_options_together() -> None:
    pv = np.zeros(96)
    pv[[40, 56]] = 0.6
    inputs = _inputs(pv, (_boiler(2.0, min_run_min=45, min_power_kw=1.0),))

    values = solve_block(inputs, "max_shared")["a"]["boiler"].to_numpy()

    assert values.sum() == pytest.approx(2.0, abs=1e-9)
    assert values[values > 0.0].min() >= 0.25
    assert all(length >= 3 for _, length in _runs(values))


def test_min_options_add_binaries_only_for_that_device() -> None:
    pv = np.zeros(96)
    with_options = ModulatingDevice(
        id="ev",
        power_kw=KW(11.0),
        energy_kwh_per_day=KWh(8.0),
        window=TimeWindow(time(6, 0), time(20, 0)),
        baseline_start=time(6, 0),
        min_power_kw=KW(4.1),
    )
    model = build_block_model(_inputs(pv, (_boiler(), with_options)), "max_shared")

    assert model.problem.isMIP()
    binaries = [v.name for v in model.problem.variables() if v.cat == pulp.LpInteger]
    assert len(binaries) == 56  # one "on" per interval 06:00 to 20:00, only for the EV (d_pos 1)
    assert all(name.startswith("on_0_1_") for name in binaries)
    span = model.device_vars["a"]["ev"].spans[0]
    assert len(span.on_vars) == 56
    assert span.floor_kwh == pytest.approx(1.025)
    assert model.device_vars["a"]["boiler"].spans[0].on_vars == ()


def test_energy_that_min_power_cannot_split_is_infeasible() -> None:
    # 1 kWh, but one interval at >= 4.1 kW is already 1.025 kWh: no feasible schedule.
    ev = ModulatingDevice(
        id="ev",
        power_kw=KW(11.0),
        energy_kwh_per_day=KWh(1.0),
        window=TimeWindow(time(6, 0), time(20, 0)),
        baseline_start=time(6, 0),
        min_power_kw=KW(4.1),
    )
    with pytest.raises(BlockSolveError, match="Infeasible"):
        solve_block(_inputs(np.zeros(96), (ev,)), "max_shared")


def test_min_run_longer_than_the_spring_dst_window_raises_while_building() -> None:
    # Window 01:00 to 04:00 is only 2 h on 2023-03-26; min_run 3 h cannot fit.
    device = _boiler(1.0, min_run_min=180, window=TimeWindow(time(1, 0), time(4, 0)))
    inputs = _inputs(np.zeros(96), (device,), day="2023-03-26")

    with pytest.raises(BlockSolveError, match="min_run of 12 intervals"):
        build_block_model(inputs, "max_shared")


def test_min_run_on_the_autumn_dst_day() -> None:
    # Window 01:00 to 04:00 is 4 h on 2023-10-29 (100 intervals); runs still >= 3 h.
    pv = np.zeros(100)
    pv[4:20] = 0.6
    device = _boiler(1.5, min_run_min=180, window=TimeWindow(time(1, 0), time(4, 0)))
    inputs = _inputs(pv, (device,), day="2023-10-29")

    values = solve_block(inputs, "max_shared")["a"]["boiler"].to_numpy()

    assert len(values) == 100
    assert values.sum() == pytest.approx(1.5, abs=1e-9)
    assert all(length >= 12 for _, length in _runs(values))


@pytest.mark.parametrize(
    ("energy", "floor", "cap", "length", "expected"),
    [
        (14.4570709, 3.0004132, 5.2638829, 42, (3, 4)),  # 2.75 -> 3, 4.82 -> 4
        (1.5, 0.5, 0.5, 10, (3, 3)),  # exact multiples are kept despite float noise
        (1.5000000001, 0.5, 0.5, 10, (3, 3)),
        (1.0, 0.0, 0.5, 10, (2, 10)),  # no floor: at most the window
        (1.0, 0.005, 0.5, 10, (2, 10)),  # tiny floor: capped by the window
    ],
)
def test_running_count_bounds(
    energy: float, floor: float, cap: float, length: int, expected: tuple[int, int]
) -> None:
    assert _running_count_bounds(energy, floor, cap, length) == expected


@pytest.mark.parametrize(
    ("level", "energy"),
    [
        (1.025, 3 * 1.025 - 4.4e-16),  # all at floor, 1-ulp surplus from summation order
        (0.5, 1.5 + 2.2e-16),  # all at cap, 1-ulp shortfall
    ],
)
def test_repair_accepts_summation_noise_when_all_values_sit_at_a_bound(
    level: float, energy: float
) -> None:
    values = np.asarray([0.0, level, level, level, 0.0])
    floor = 1.025 if level == 1.025 else 0.1
    span = TaskSpan(1, 4, energy, on_vars=(None,) * 3, floor_kwh=floor)

    _exact_energy(values, span, max(level, 0.5), "test", np.asarray([True, True, True]))

    np.testing.assert_array_equal(values, [0.0, level, level, level, 0.0])


def test_repair_without_room_and_real_deviation_still_raises() -> None:
    values = np.asarray([0.5, 0.5, 0.5])
    with pytest.raises(BlockSolveError, match="no room left"):
        _exact_energy(values, TaskSpan(0, 3, 1.5 + 1e-6), 0.5, "test")


def test_min_run_can_make_the_optimum_worse_than_a_baseline_that_violates_it() -> None:
    # Pins the documented limit of INV-3: the baseline (06:00, one interval of 0.5 kWh) shares
    # all 0.6 kWh of PV at 06:00 but violates min_run 60. Every feasible schedule runs 4
    # intervals with at least the floor in the 3 PV-less ones, so it shares less. The runner
    # (F5-07) must compare against the baseline itself.
    pv = np.zeros(96)
    pv[24] = 0.6
    inputs = _inputs(pv, (_boiler(0.5, min_run_min=60),))

    optimized = evaluate(solve_block(inputs, "max_shared"), inputs, "max_shared")
    baseline = evaluate(baseline_device_schedule(inputs), inputs, "max_shared")

    assert baseline == pytest.approx(0.6)
    assert optimized < baseline - 1e-3


def test_min_run_off_the_grid_is_rejected_not_floored() -> None:
    # parse_device rejects such values; a device built directly must not be floored silently.
    with pytest.raises(UnsupportedModelError, match="15-min grid"):
        build_block_model(_inputs(np.zeros(96), (_boiler(min_run_min=50),)), "max_shared")


@pytest.mark.parametrize("gap", [-0.1, 1.0])
def test_invalid_mip_gap_is_rejected(gap: float) -> None:
    with pytest.raises(ValueError, match="mip_gap"):
        solve_block(_inputs(np.zeros(96), (_boiler(),)), "max_shared", mip_gap=gap)


def test_mip_gap_still_keeps_min_options() -> None:
    pv = np.zeros(96)
    pv[[40, 56]] = 0.6
    inputs = _inputs(pv, (_boiler(1.0, min_run_min=60, min_power_kw=0.4),))

    values = solve_block(inputs, "max_shared", mip_gap=1e-2)["a"]["boiler"].to_numpy()

    assert values.sum() == pytest.approx(1.0, abs=1e-9)
    assert values[values > 0.0].min() >= 0.1
    assert all(length >= 4 for _, length in _runs(values))


@pytest.mark.parametrize(
    "solver_values",
    [
        [0.0, 0.2999999, 0.5, 0.4999999],  # missing, one interval just below the floor
        [1e-9, 0.3000002, 0.5, 0.5000001],  # surplus, noise where not running
    ],
)
def test_repair_keeps_floor_and_zero_where_not_running(solver_values: list[float]) -> None:
    values = np.asarray([0.0, *solver_values, 0.0])
    span = TaskSpan(1, 5, 1.3, on_vars=(None,) * 4, floor_kwh=0.3)
    running = np.asarray([False, True, True, True])

    _exact_energy(values, span, 0.5, "test", running)

    assert values.sum() == pytest.approx(1.3, abs=1e-12)
    assert values[1] == 0.0
    assert values[2:5].min() >= 0.3
    assert values.max() <= 0.5
