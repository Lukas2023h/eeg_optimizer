"""German number formatting for the UI pages (one place instead of a copy per page)."""

from __future__ import annotations

from collections import Counter

import pandas as pd


def german_number(value: float, decimals: int = 0, *, signed: bool = False) -> str:
    """Format ``value`` German style: thousands '.', decimal ','; e.g. ``12.345,6``.

    Args:
        decimals: digits after the decimal comma.
        signed: prefix positive values with '+' (for deltas like '+1.234 kWh'). Values that
            round to zero get no sign, so '-0' or '+0' never appear.
    """
    text = f"{value:,.{decimals}f}"
    if float(text.replace(",", "")) == 0.0:
        text = f"{0.0:,.{decimals}f}"
    elif signed and value > 0:
        text = "+" + text
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def time_labels(index: pd.DatetimeIndex) -> list[str]:
    """Local clock times ``HH:MM`` of a day, unique also on the DST fall-back day.

    On the last Sunday of October 02:00 to 02:45 occurs twice; those labels get the
    German suffix " (Sommerzeit)" or " (Winterzeit)" so a category axis does not fold
    the second hour onto the first.
    """
    plain = [t.strftime("%H:%M") for t in index]
    counts = Counter(plain)
    return [
        label if counts[label] == 1 else f"{label} ({_dst_name(t)})"
        for label, t in zip(plain, index, strict=True)
    ]


def _dst_name(timestamp: pd.Timestamp) -> str:
    dst = timestamp.dst()
    return "Sommerzeit" if dst is not None and dst > pd.Timedelta(0) else "Winterzeit"


__all__ = ["german_number", "time_labels"]
