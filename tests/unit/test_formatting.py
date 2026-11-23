"""Unit tests for the German number format of the UI (F6-02)."""

from __future__ import annotations

import pytest

from eeg_optimizer.formatting import german_number


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (0.0, 0, "0"),
        (12.0, 0, "12"),
        (1234.0, 0, "1.234"),
        (1234567.891, 2, "1.234.567,89"),
        (12345.6, 1, "12.345,6"),
        (-1234.5, 1, "-1.234,5"),
        (0.125, 2, "0,12"),
        (999.5, 0, "1.000"),
    ],
)
def test_german_number(value: float, decimals: int, expected: str) -> None:
    assert german_number(value, decimals) == expected


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (1234.0, 0, "+1.234"),
        (0.06, 1, "+0,1"),
        (-12.5, 1, "-12,5"),
        (0.0, 0, "0"),
    ],
)
def test_signed_prefixes_positive_values(value: float, decimals: int, expected: str) -> None:
    assert german_number(value, decimals, signed=True) == expected


@pytest.mark.parametrize("value", [-0.0, -1e-9, 1e-9, -0.004, 0.004])
@pytest.mark.parametrize("signed", [False, True])
def test_values_rounding_to_zero_have_no_sign(value: float, signed: bool) -> None:
    """Solver noise like -1e-9 kWh must not show up as '-0' or '+0'."""
    assert german_number(value, 2, signed=signed) == "0,00"
    assert german_number(value, 0, signed=signed) == "0"
