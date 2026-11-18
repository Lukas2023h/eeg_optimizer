"""Property tests for the CO2 intensity from the generation mix (F1-08, INV-4)."""

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from eeg_optimizer.sources.energy_charts import (
    EMISSION_FACTORS_G_PER_KWH,
    intensity_from_generation,
)

TYPES = sorted(EMISSION_FACTORS_G_PER_KWH)
F_MIN = min(EMISSION_FACTORS_G_PER_KWH.values())
F_MAX = max(EMISSION_FACTORS_G_PER_KWH.values())

_power = st.floats(min_value=0.0, max_value=20_000.0, allow_nan=False, allow_infinity=False)


@st.composite
def _mix(draw: st.DrawFn) -> dict[str, np.ndarray]:
    n = draw(st.integers(min_value=1, max_value=8))
    names = draw(st.lists(st.sampled_from(TYPES), min_size=1, max_size=len(TYPES), unique=True))
    mix = {name: np.array(draw(st.lists(_power, min_size=n, max_size=n))) for name in names}
    # Guarantee positive total generation in every interval.
    mix[names[0]] = mix[names[0]] + 1.0
    return mix


@given(_mix())
def test_intensity_lies_between_smallest_and_largest_factor(mix: dict[str, np.ndarray]) -> None:
    out = intensity_from_generation(mix)
    used = [EMISSION_FACTORS_G_PER_KWH[name] for name in mix]
    assert np.all(out >= min(used) - 1e-9)
    assert np.all(out <= max(used) + 1e-9)
    assert np.all((out >= F_MIN - 1e-9) & (out <= F_MAX + 1e-9))


@given(_mix(), st.floats(min_value=0.01, max_value=100.0))
def test_intensity_is_scale_invariant(mix: dict[str, np.ndarray], factor: float) -> None:
    scaled = {name: values * factor for name, values in mix.items()}
    assert np.allclose(intensity_from_generation(mix), intensity_from_generation(scaled))


@given(st.sampled_from(TYPES), st.lists(_power.map(lambda p: p + 1.0), min_size=1, max_size=8))
def test_single_type_yields_its_factor(name: str, power: list[float]) -> None:
    out = intensity_from_generation({name: np.array(power)})
    assert np.allclose(out, EMISSION_FACTORS_G_PER_KWH[name])
