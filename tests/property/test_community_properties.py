"""F2-03 properties: any valid synthetic community yields one consistent input bundle.

INV-4 (unit suffixes) and INV-6 (gapless 15-min grid, DST days 92/100 intervals) hold for
every frame, and all frames share one index.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from eeg_optimizer.community import build_inputs
from eeg_optimizer.config import (
    CommunityConfig,
    HouseholdConfig,
    LoadConfig,
    PeriodConfig,
    SeriesSourceConfig,
    load_config,
)
from eeg_optimizer.sources.cache import PROJECT_ROOT
from eeg_optimizer.units import validate_frame

_BASE: CommunityConfig = load_config(PROJECT_ROOT / "config" / "example_community_offline.yaml")

_seed = st.integers(min_value=0, max_value=2**31 - 1)
_household_loads = st.lists(
    st.builds(
        LoadConfig,
        source=st.just("synthetic"),
        annual_kwh=st.none() | st.floats(min_value=500.0, max_value=20_000.0),
        seed=_seed,
    ),
    min_size=1,
    max_size=4,
)


@settings(max_examples=8, deadline=None)
@given(
    year=st.integers(min_value=2015, max_value=2030),
    kwp=st.floats(min_value=0.5, max_value=200.0),
    pv_seed=_seed,
    price_seed=_seed,
    co2_seed=_seed,
    loads=_household_loads,
)
def test_bundle_is_consistent(
    *,
    year: int,
    kwp: float,
    pv_seed: int,
    price_seed: int,
    co2_seed: int,
    loads: list[LoadConfig],
) -> None:
    cfg = replace(
        _BASE,
        period=PeriodConfig(year=year),
        pv=replace(_BASE.pv, kwp=kwp, seed=pv_seed),
        prices=SeriesSourceConfig(source="synthetic", seed=price_seed),
        co2=SeriesSourceConfig(source="synthetic", seed=co2_seed),
        households=tuple(HouseholdConfig(id=f"hh{i}", load=ld) for i, ld in enumerate(loads)),
    )
    inputs = build_inputs(cfg)

    frames = [inputs.pv, inputs.prices, inputs.co2, *inputs.loads.values()]
    for frame in frames:
        validate_frame(frame)
        assert frame.index.equals(inputs.index)
    assert len(inputs.loads) == len(loads)

    per_day = pd.Series(1, index=inputs.index).groupby(inputs.index.date).size()
    assert set(per_day) == {92, 96, 100}
    assert (per_day == 92).sum() == 1
    assert (per_day == 100).sum() == 1
    for ld, frame in zip(loads, inputs.loads.values(), strict=True):
        if ld.annual_kwh is not None:
            assert abs(frame["load_kwh"].sum() - ld.annual_kwh) < 1e-6 * ld.annual_kwh
        else:
            assert frame["load_kwh"].sum() > 0
