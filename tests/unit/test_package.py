"""Package-level sanity checks."""

import re
from pathlib import Path

import eeg_optimizer

SRC = Path(eeg_optimizer.__file__).parent


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", eeg_optimizer.__version__)


def test_no_raw_interval_factor_outside_units() -> None:
    """INV-4 guard: kW<->kWh conversion only via units.py (no bare * 0.25 / / 0.25)."""
    pattern = re.compile(r"[*/]\s*0?\.25\b")
    offenders = [
        f"{p.relative_to(SRC)}:{i}"
        for p in SRC.rglob("*.py")
        if p.name != "units.py"
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, f"Use units.kw_to_kwh/kwh_to_kw instead: {offenders}"


UNIT_AGNOSTIC_MARKER = "# unit-agnostic"
"""Line marker for ``.asi8`` uses that only compare ticks with each other (no step size)."""


def test_no_raw_time_ticks_outside_units() -> None:
    """Lesson zeit-einheit: index ticks (``.asi8``) are in the index unit (s/ms/us/ns),
    ``Timedelta.value``/``as_unit(...).value`` in ns or another unit. Mixing them broke the
    grid check twice (F5-08, F1-09). Compare Timedeltas instead (``a - b != INTERVAL``);
    the only tick arithmetic lives in ``units._is_aligned_gapless_grid``.
    """
    pattern = re.compile(r"\.asi8\b|\.as_unit\(")
    offenders = [
        f"{p.relative_to(SRC)}:{i}"
        for p in SRC.rglob("*.py")
        if p.name != "units.py"
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if pattern.search(line) and UNIT_AGNOSTIC_MARKER not in line
    ]
    assert not offenders, f"Compare pd.Timedelta values instead of raw ticks: {offenders}"


def test_raw_time_ticks_guard_catches_the_f1_09_pattern() -> None:
    """The guard pattern matches the buggy F1-09 lines (red on the old error)."""
    pattern = re.compile(r"\.asi8\b|\.as_unit\(")
    assert pattern.search("steps = np.diff(span.index.asi8)")
    assert pattern.search("expected = interval.as_unit(span.index.unit).value")
    assert not pattern.search("off_grid = stamps[1:] - stamps[:-1] != pd.Timedelta(INTERVAL)")
