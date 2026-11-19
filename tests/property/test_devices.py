"""Property tests for eeg_optimizer.devices (F4-01): time windows and device validation."""

from __future__ import annotations

from datetime import time, timedelta

from hypothesis import given
from hypothesis import strategies as st

from eeg_optimizer.config import ConfigError
from eeg_optimizer.devices import TimeWindow, parse_device
from eeg_optimizer.units import KW, kw_to_kwh

SLOTS_PER_DAY = 96
grid_times = st.integers(0, SLOTS_PER_DAY - 1).map(lambda i: time(i // 4, (i % 4) * 15))


def _all_grid_times() -> list[time]:
    return [time(i // 4, (i % 4) * 15) for i in range(SLOTS_PER_DAY)]


@given(grid_times, grid_times)
def test_window_shape(start: time, end: time) -> None:
    if start == end:
        return
    window = TimeWindow(start, end)
    assert window.crosses_midnight == (end < start)
    assert timedelta(0) < window.duration < timedelta(days=1)
    assert window.contains(start)
    assert not window.contains(end)
    inside = [t for t in _all_grid_times() if window.contains(t)]
    assert len(inside) * timedelta(minutes=15) == window.duration


@given(
    grid_times,
    grid_times,
    st.integers(0, SLOTS_PER_DAY - 1),
    st.floats(0.5, 22.0),
    st.floats(0.1, 200.0),
)
def test_modulating_accepted_iff_energy_fits_from_baseline(
    start: time, end: time, offset_slots: int, power_kw: float, energy_kwh: float
) -> None:
    if start == end:
        return
    window = TimeWindow(start, end)
    slots = window.duration // timedelta(minutes=15)
    baseline_minutes = (start.hour * 60 + start.minute + (offset_slots % slots) * 15) % 1440
    baseline = time(baseline_minutes // 60, baseline_minutes % 60)
    raw = {
        "id": "x",
        "kind": "modulating",
        "power_kw": power_kw,
        "energy_kwh_per_day": energy_kwh,
        "window": {"start": f"{start:%H:%M}", "end": f"{end:%H:%M}"},
        "baseline_start": f"{baseline:%H:%M}",
    }
    reachable = kw_to_kwh(KW(power_kw), window.remaining_after(baseline))
    if energy_kwh <= reachable:
        device = parse_device(raw, "d")
        assert device.energy_kwh_per_day <= kw_to_kwh(device.power_kw, device.window.duration)
        assert device.window.contains(device.baseline_start)
    elif energy_kwh > reachable + 1e-6:
        try:
            parse_device(raw, "d")
        except ConfigError:
            return
        raise AssertionError("unreachable energy was accepted")
