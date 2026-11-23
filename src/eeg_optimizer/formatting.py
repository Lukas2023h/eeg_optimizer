"""German number formatting for the UI pages (one place instead of a copy per page)."""

from __future__ import annotations


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


__all__ = ["german_number"]
