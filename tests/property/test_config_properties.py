"""Property tests for the config roundtrip (F2-01)."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from eeg_optimizer.config import MAX_HOUSEHOLDS, load_config, merge_dicts, parse_config, save_config
from eeg_optimizer.sources.htw import PROFILE_COUNT

_finite = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)

_load = st.one_of(
    st.builds(lambda p: {"source": "htw", "profile": p}, st.integers(1, PROFILE_COUNT)),
    st.builds(
        lambda kwh, seed: {"source": "synthetic", "annual_kwh": kwh, "seed": seed},
        st.floats(min_value=1.0, max_value=20000.0, allow_nan=False),
        st.integers(0, 2**31),
    ),
    st.builds(
        lambda p: {"source": "smartmeter", "path": p},
        st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != ""),
    ),
)


@st.composite
def _configs(draw: st.DrawFn) -> dict[str, Any]:
    n = draw(st.integers(1, MAX_HOUSEHOLDS))
    households = [{"id": f"hh{i:02d}", "load": draw(_load)} for i in range(n)]
    return {
        "name": draw(st.text(max_size=30)),
        "period": {"year": draw(st.integers(2000, 2100))},
        "location": {
            "lat": draw(st.floats(-90, 90, allow_nan=False)),
            "lon": draw(st.floats(-180, 180, allow_nan=False)),
        },
        "pv": {
            "source": draw(st.sampled_from(["pvgis", "synthetic"])),
            "kwp": draw(st.floats(min_value=0.01, max_value=1000.0, allow_nan=False)),
            "tilt_deg": draw(st.floats(0, 90, allow_nan=False)),
            "azimuth_deg": draw(st.floats(-180, 180, allow_nan=False)),
            "loss_pct": draw(st.floats(0, 100, allow_nan=False)),
            "seed": draw(st.integers(0, 2**31)),
        },
        "prices": {"source": draw(st.sampled_from(["awattar", "synthetic"])), "seed": 0},
        "co2": {"source": draw(st.sampled_from(["energy_charts", "synthetic"])), "seed": 1},
        "tariffs": {
            "eeg_type": draw(st.sampled_from(["local", "regional"])),
            "energy_price": draw(_finite),
            "grid_fee_energy": draw(_finite),
            "levies_taxes": draw(_finite),
            "feed_in_tariff": draw(_finite),
            "eeg_internal_price": draw(_finite),
            "spot_markup": draw(st.floats(-10, 10, allow_nan=False)),
            "grid_fee_reduction": draw(st.none() | st.floats(0, 1, allow_nan=False)),
        },
        "allocation": {"strategy": "dynamic_proportional"},
        "optimization": {
            "objective": draw(st.sampled_from(["max_shared", "min_cost", "min_co2"])),
            "solver": draw(st.sampled_from(["milp", "greedy"])),
        },
        "households": households,
    }


@settings(max_examples=50, deadline=None)
@given(_configs())
def test_save_load_roundtrip(raw: dict[str, Any]) -> None:
    original = parse_config(raw)
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "c.yaml"
        save_config(original, out)
        assert load_config(out) == original


@given(
    st.dictionaries(
        st.text(max_size=3), st.integers() | st.dictionaries(st.text(max_size=3), st.integers())
    ),
    st.dictionaries(
        st.text(max_size=3), st.integers() | st.dictionaries(st.text(max_size=3), st.integers())
    ),
)
def test_merge_override_wins_for_scalars(base: dict[str, Any], override: dict[str, Any]) -> None:
    merged = merge_dicts(base, override)
    assert set(merged) == set(base) | set(override)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            assert merged[key] == {**base[key], **value}
        else:
            assert merged[key] == value
