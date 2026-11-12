"""Architecture guard (docs/architecture.md §3.1): kW<->kWh factors only live in units.py."""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_QUARTER_FACTOR = re.compile(r"[*/]\s*0?\.25(?![\d.])|(?<![\d.])0?\.25\s*\*")
_ALLOWED = {_ROOT / "src" / "eeg_optimizer" / "units.py"}


def _offending_lines() -> list[str]:
    hits = []
    for folder in ("src", "app"):
        for path in sorted((_ROOT / folder).rglob("*.py")):
            if path in _ALLOWED:
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                code = line.split("#", 1)[0]
                if _QUARTER_FACTOR.search(code):
                    hits.append(f"{path.relative_to(_ROOT)}:{lineno}: {line.strip()}")
    return hits


@pytest.mark.parametrize("code", ["e = p * 0.25", "p = e / 0.25", "e = .25*p", "x=0.25 * p"])
def test_pattern_detects_quarter_factor(code: str) -> None:
    assert _QUARTER_FACTOR.search(code)


@pytest.mark.parametrize(
    "code", ["share = 0.255", "x = 1.25 + y", "q = quantile(0.25)", "y = 1.25 * x", "y = x * 0.255"]
)
def test_pattern_ignores_unrelated_numbers(code: str) -> None:
    assert not _QUARTER_FACTOR.search(code)


def test_no_quarter_hour_factor_outside_units() -> None:
    assert _offending_lines() == [], "use units.kw_to_kwh / kwh_to_kw instead of * 0.25 or / 0.25"
