"""Property test: arbitrary valid frames survive the Parquet cache roundtrip (F1-03)."""

import tempfile
from pathlib import Path

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from eeg_optimizer.sources.cache import Cache
from eeg_optimizer.units import TIMEZONE, validate_frame

_SUFFIXES = ("_kwh", "_kw", "_eur", "_ct_per_kwh", "_g_per_kwh", "_kg_co2")
# Start anywhere in 2023 (covers both DST transitions in Europe/Vienna).
_starts = st.integers(min_value=0, max_value=365 * 96 - 1).map(
    lambda q: (
        pd.Timestamp("2023-01-01", tz=TIMEZONE).tz_convert("UTC") + pd.Timedelta(minutes=15 * q)
    )
)
_columns = st.lists(
    st.tuples(st.from_regex(r"[a-z][a-z0-9]{0,8}", fullmatch=True), st.sampled_from(_SUFFIXES)),
    min_size=1,
    max_size=4,
    unique_by=lambda t: t[0] + t[1],
)


@settings(max_examples=40, deadline=None)
@given(
    start=_starts, periods=st.integers(min_value=1, max_value=400), columns=_columns, data=st.data()
)
def test_cache_roundtrip_preserves_frame(
    start: pd.Timestamp,
    periods: int,
    columns: list[tuple[str, str]],
    data: st.DataObject,
) -> None:
    index = pd.date_range(start, periods=periods, freq="15min").tz_convert(TIMEZONE)
    values = st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False)
    frame = pd.DataFrame(
        {
            stem + suffix: data.draw(st.lists(values, min_size=periods, max_size=periods))
            for stem, suffix in columns
        },
        index=index,
    )
    validate_frame(frame)

    with tempfile.TemporaryDirectory() as tmp:
        Cache(Path(tmp)).get_or_fetch("src", "key", lambda: frame)
        loaded = Cache(Path(tmp), allow_fetch=False).get_or_fetch("src", "key", _never)

    assert isinstance(loaded.index, pd.DatetimeIndex)
    assert str(loaded.index.tz) == TIMEZONE
    assert list(loaded.columns) == list(frame.columns)
    pd.testing.assert_frame_equal(loaded, frame, check_freq=False)
    validate_frame(loaded)


def _never() -> pd.DataFrame:
    raise AssertionError("fetch_fn must not be called on a cache hit")
