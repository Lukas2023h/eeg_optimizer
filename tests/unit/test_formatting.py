"""Unit tests for the German number format of the UI (F6-02)."""

from __future__ import annotations

import pandas as pd
import pytest

from eeg_optimizer.formatting import german_number, time_labels


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


def _day(day: str) -> pd.DatetimeIndex:
    """15-min intervals of one local calendar day (92/96/100 intervals)."""
    start = pd.Timestamp(day, tz="Europe/Vienna")
    end = (start.tz_localize(None) + pd.Timedelta(days=1)).tz_localize("Europe/Vienna")
    return pd.date_range(start, end, freq="15min", inclusive="left")


def test_time_labels_of_a_normal_day_are_plain_clock_times() -> None:
    labels = time_labels(_day("2023-06-15"))
    assert len(labels) == 96
    assert labels[:2] == ["00:00", "00:15"]
    assert labels[-1] == "23:45"


def test_time_labels_are_unique_on_the_dst_fall_back_day() -> None:
    labels = time_labels(_day("2023-10-29"))
    assert len(labels) == 100
    assert len(set(labels)) == 100
    assert labels[8:13] == [
        "02:00 (Sommerzeit)",
        "02:15 (Sommerzeit)",
        "02:30 (Sommerzeit)",
        "02:45 (Sommerzeit)",
        "02:00 (Winterzeit)",
    ]
    assert labels[7] == "01:45"
    assert labels[16] == "03:00"


def test_time_labels_of_the_dst_spring_day_skip_the_missing_hour() -> None:
    labels = time_labels(_day("2023-03-26"))
    assert len(labels) == 92
    assert "02:00" not in labels
    assert labels[8] == "03:00"
