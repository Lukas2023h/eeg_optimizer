"""Properties of the aWATTar conversion (F1-07): unit conversion, rate repetition, gaps."""

from typing import Any

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from eeg_optimizer.sources.awattar import COLUMN, AwattarError, parse_marketdata, to_15min
from eeg_optimizer.units import validate_frame

HOUR_MS = 3_600_000
# aWATTar publishes EUR/MWh with two decimals; EPEX limits are -500 and 4000 EUR/MWh.
prices = st.integers(min_value=-50_000, max_value=400_000).map(lambda cents: cents / 100)
days = st.dates(
    min_value=pd.Timestamp("2023-01-01").date(), max_value=pd.Timestamp("2023-12-31").date()
)


def _bounds(day: Any) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(day, tz="Europe/Vienna")
    return start, pd.Timestamp(start + pd.DateOffset(days=1))


def _payload(start: pd.Timestamp, end: pd.Timestamp, values: list[float]) -> dict[str, Any]:
    first = int(start.tz_convert("UTC").value // 1_000_000)
    last = int(end.tz_convert("UTC").value // 1_000_000)
    rows = [
        {
            "start_timestamp": ms,
            "end_timestamp": ms + HOUR_MS,
            "marketprice": values[i],
            "unit": "Eur/MWh",
        }
        for i, ms in enumerate(range(first, last, HOUR_MS))
    ]
    return {"object": "list", "data": rows}


@settings(max_examples=60, deadline=None)
@given(day=days, data=st.data())
def test_prices_are_divided_by_ten_and_repeated(day: Any, data: st.DataObject) -> None:
    start, end = _bounds(day)
    hours = int((end - start) / pd.Timedelta(hours=1))
    values = data.draw(st.lists(prices, min_size=hours, max_size=hours))
    out = to_15min(parse_marketdata(_payload(start, end, values), start, end))
    validate_frame(out)
    assert len(out) == hours * 4
    quarters = out[COLUMN].to_numpy().reshape(-1, 4)
    assert np.allclose(quarters, np.array(values)[:, None] / 10.0)
    # Sign is never changed (negative prices are real).
    assert (np.sign(quarters[:, 0]) == np.sign(values)).all()


@settings(max_examples=60, deadline=None)
@given(day=days, data=st.data())
def test_any_missing_hour_raises(day: Any, data: st.DataObject) -> None:
    start, end = _bounds(day)
    hours = int((end - start) / pd.Timedelta(hours=1))
    payload = _payload(start, end, [50.0] * hours)
    drop = data.draw(st.integers(min_value=0, max_value=hours - 1))
    del payload["data"][drop]
    with pytest.raises(AwattarError, match="fehlen"):
        parse_marketdata(payload, start, end)
