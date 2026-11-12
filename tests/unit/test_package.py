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
