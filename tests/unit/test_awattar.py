"""Tests for Austrian spot prices from aWATTar (F1-07). Offline: HTTP is mocked."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import requests

from eeg_optimizer.sources import awattar
from eeg_optimizer.sources.awattar import (
    COLUMN,
    AwattarError,
    fetch_marketdata,
    load_month,
    load_spot_prices,
    month_range,
    parse_marketdata,
    to_15min,
)
from eeg_optimizer.sources.cache import Cache, CacheMissError
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import eur_per_mwh_to_ct_per_kwh, validate_frame

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_JULY = FIXTURES / "awattar_at_2023-07-02_1d.json"
FIXTURE_DST = FIXTURES / "awattar_at_2023-03-26_dst_1d.json"
HOUR_MS = 3_600_000


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def _day(date: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(date, tz="Europe/Vienna")
    return start, pd.Timestamp(start + pd.DateOffset(days=1))


def _ms(ts: pd.Timestamp) -> int:
    return int(ts.tz_convert("UTC").value // 1_000_000)


def _payload(start: pd.Timestamp, end: pd.Timestamp, price_fn: Any = None) -> dict[str, Any]:
    """aWATTar-like payload with one row per UTC hour in ``[start, end)``."""
    rows = []
    for i, ms in enumerate(range(_ms(start), _ms(end), HOUR_MS)):
        price = float(price_fn(i)) if price_fn is not None else 100.0
        rows.append(
            {
                "start_timestamp": ms,
                "end_timestamp": ms + HOUR_MS,
                "marketprice": price,
                "unit": "Eur/MWh",
            }
        )
    return {"object": "list", "data": rows, "url": "/at/v1/marketdata"}


class _FakeFetch:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def __call__(self, start_ms: int, end_ms: int) -> dict[str, Any]:
        self.calls.append((start_ms, end_ms))
        start = pd.Timestamp(start_ms, unit="ms", tz="UTC").tz_convert("Europe/Vienna")
        end = pd.Timestamp(end_ms, unit="ms", tz="UTC").tz_convert("Europe/Vienna")
        return _payload(start, end, lambda i: (i % 24) * 10.0 - 50.0)


# --- conversion ----------------------------------------------------------------------------


def test_eur_per_mwh_to_ct_per_kwh() -> None:
    assert eur_per_mwh_to_ct_per_kwh(100.0) == pytest.approx(10.0)
    assert eur_per_mwh_to_ct_per_kwh(-500.0) == pytest.approx(-50.0)
    assert np.allclose(eur_per_mwh_to_ct_per_kwh(np.array([0.0, 1.0])), [0.0, 0.1])


def test_100_eur_per_mwh_is_10_ct_per_kwh_on_15min_grid() -> None:
    start, end = _day("2023-01-10")
    out = to_15min(parse_marketdata(_payload(start, end), start, end))
    validate_frame(out)
    assert list(out.columns) == [COLUMN] == ["spot_ct_per_kwh"]
    assert len(out) == 96
    assert np.allclose(out[COLUMN].to_numpy(), 10.0)


def test_fixture_july_timezone_and_values() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    hourly = parse_marketdata(payload, start, end)
    assert str(pd.DatetimeIndex(hourly.index).tz) == "Europe/Vienna"
    # First API row: start_timestamp = 2023-07-01 22:00 UTC = 2023-07-02 00:00 CEST.
    assert payload["data"][0]["start_timestamp"] == 1688248800000
    assert hourly.index[0] == pd.Timestamp("2023-07-02 00:00", tz="Europe/Vienna")
    assert hourly[COLUMN].iloc[0] == pytest.approx(payload["data"][0]["marketprice"] / 10.0)

    out = to_15min(hourly)
    validate_frame(out)
    assert len(out) == 96
    # Each hourly rate is repeated unchanged onto its four quarters.
    quarters = out[COLUMN].to_numpy().reshape(-1, 4)
    assert (quarters == quarters[:, :1]).all()
    expected = np.array([row["marketprice"] for row in payload["data"]]) / 10.0
    assert np.allclose(quarters[:, 0], expected)


def test_negative_prices_are_kept() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    out = to_15min(parse_marketdata(payload, start, end))
    # 2 July 2023 (Sunday) had -500 EUR/MWh at noon in AT.
    assert out[COLUMN].min() == pytest.approx(-50.0)
    worst = pd.DatetimeIndex(out.index)[int(np.argmin(out[COLUMN].to_numpy()))]
    assert 12 <= worst.hour <= 15
    assert (out[COLUMN] < 0).sum() >= 4 * 4
    # Synthetic check as well: sign survives unchanged.
    syn_start, syn_end = _day("2023-01-10")
    syn = parse_marketdata(_payload(syn_start, syn_end, lambda i: -12.3), syn_start, syn_end)
    assert np.allclose(syn[COLUMN].to_numpy(), -1.23)


def test_fixture_dst_day_has_23_hours_and_92_quarters() -> None:
    payload = _load(FIXTURE_DST)
    start, end = _day("2023-03-26")
    assert len(payload["data"]) == 23
    out = to_15min(parse_marketdata(payload, start, end))
    validate_frame(out)
    assert len(out) == 92
    # 01:45 CET is followed directly by 03:00 CEST (02:00 local does not exist).
    assert out.index[7] == pd.Timestamp("2023-03-26 01:45", tz="Europe/Vienna")
    assert out.index[8] == pd.Timestamp("2023-03-26 03:00", tz="Europe/Vienna")
    expected = np.array([row["marketprice"] for row in payload["data"]]) / 10.0
    assert np.allclose(out[COLUMN].to_numpy()[::4], expected)


def test_autumn_dst_day_has_100_quarters() -> None:
    start, end = _day("2023-10-29")
    payload = _payload(start, end, lambda i: i)
    assert len(payload["data"]) == 25
    out = to_15min(parse_marketdata(payload, start, end))
    assert len(out) == 100
    # Both 02:00 hours (CEST then CET) are kept with their own prices.
    two_am = out[pd.DatetimeIndex(out.index).hour == 2][COLUMN].to_numpy()
    assert list(two_am) == pytest.approx([0.2] * 4 + [0.3] * 4)


# --- gaps and malformed data ---------------------------------------------------------------


def test_gap_raises_clear_error_instead_of_filling() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    del payload["data"][5]
    with pytest.raises(AwattarError, match=r"lückenhaft: 1 von 24 Stunden fehlen.*05:00"):
        parse_marketdata(payload, start, end)


def test_missing_tail_raises() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    payload["data"] = payload["data"][:20]
    with pytest.raises(AwattarError, match="4 von 24 Stunden fehlen"):
        parse_marketdata(payload, start, end)


def test_empty_data_raises() -> None:
    start, end = _day("2023-07-02")
    with pytest.raises(AwattarError, match="24 von 24"):
        parse_marketdata({"object": "list", "data": []}, start, end)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda p: p.update(data=None), "data"),
        (lambda p: p["data"].append("x"), "kein Objekt"),
        (lambda p: p["data"][0].pop("marketprice"), "marketprice"),
        (lambda p: p["data"][0].update(unit="Eur/kWh"), "Einheit"),
        (lambda p: p["data"][0].update(marketprice=None), "keine Zahl"),
        (lambda p: p["data"][0].update(marketprice=True), "keine Zahl"),
        (lambda p: p["data"][0].update(marketprice=float("nan")), "ungültig"),
        (
            lambda p: p["data"][0].update(end_timestamp=p["data"][0]["start_timestamp"] + 900_000),
            "volle Stunde",
        ),
        (lambda p: p["data"][0].update(start_timestamp=1.5), "Millisekunden"),
        (lambda p: p["data"].append(dict(p["data"][3])), "doppelt"),
        (
            lambda p: p["data"].append(
                {
                    **p["data"][-1],
                    "start_timestamp": p["data"][-1]["end_timestamp"],
                    "end_timestamp": p["data"][-1]["end_timestamp"] + HOUR_MS,
                }
            ),
            "außerhalb",
        ),
    ],
)
def test_malformed_payload_raises(mutate: Any, match: str) -> None:
    payload = _load(FIXTURE_JULY)
    mutate(payload)
    start, end = _day("2023-07-02")
    with pytest.raises(AwattarError, match=match):
        parse_marketdata(payload, start, end)


def test_non_dict_payload_raises() -> None:
    start, end = _day("2023-07-02")
    with pytest.raises(AwattarError, match="data"):
        parse_marketdata([1, 2], start, end)


def test_month_range() -> None:
    start, end = month_range(2023, 12)
    assert start == pd.Timestamp("2023-12-01", tz="Europe/Vienna")
    assert end == pd.Timestamp("2024-01-01", tz="Europe/Vienna")
    with pytest.raises(AwattarError, match="Monat"):
        month_range(2023, 13)


# --- HTTP ----------------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status} error")

    def json(self) -> Any:
        return self.payload


def test_fetch_uses_timeout_and_ms_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured.update(url=url, **kwargs)
        return _FakeResponse(_load(FIXTURE_JULY))

    monkeypatch.setattr(requests, "get", fake_get)
    payload = fetch_marketdata(1688248800000, 1688335200000)
    assert captured["url"] == "https://api.awattar.at/v1/marketdata"
    assert captured["params"] == {"start": 1688248800000, "end": 1688335200000}
    assert captured["timeout"] == awattar.HTTP_TIMEOUT_S > 0
    assert len(payload["data"]) == 24


def test_fetch_http_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "get", lambda url, **kw: _FakeResponse({}, 503))
    with pytest.raises(requests.HTTPError):
        fetch_marketdata(0, HOUR_MS)


def test_load_month_requests_local_month_bounds(tmp_path: Path) -> None:
    fake = _FakeFetch()
    out = load_month(2023, 3, cache=Cache(tmp_path), fetch=fake)
    assert fake.calls == [
        (
            _ms(pd.Timestamp("2023-03-01", tz="Europe/Vienna")),
            _ms(pd.Timestamp("2023-04-01", tz="Europe/Vienna")),
        )
    ]
    # March 2023: 31 days, one of them 23 h long.
    assert len(out) == (31 * 24 - 1) * 4


# --- whole year, chunks and cache ----------------------------------------------------------


def test_load_spot_prices_full_year_in_monthly_chunks(tmp_path: Path) -> None:
    fake = _FakeFetch()
    out = load_spot_prices(2023, cache=Cache(tmp_path), fetch=fake)
    validate_frame(out)
    assert out.index.equals(year_index(2023))
    assert list(out.columns) == [COLUMN]
    assert len(fake.calls) == 12
    starts = [
        pd.Timestamp(s, unit="ms", tz="UTC").tz_convert("Europe/Vienna") for s, _ in fake.calls
    ]
    assert [(t.month, t.day, t.hour) for t in starts] == [(m, 1, 0) for m in range(1, 13)]
    # Chunks are contiguous.
    assert all(fake.calls[i][1] == fake.calls[i + 1][0] for i in range(11))
    assert (out[COLUMN] < 0).any()


def test_second_call_uses_cache(tmp_path: Path) -> None:
    fake = _FakeFetch()
    first = load_spot_prices(2023, cache=Cache(tmp_path), fetch=fake)

    def no_http(start_ms: int, end_ms: int) -> Any:
        raise AssertionError("HTTP request on cache hit")

    second = load_spot_prices(2023, cache=Cache(tmp_path, allow_fetch=False), fetch=no_http)
    pd.testing.assert_frame_equal(first, second, check_freq=False)
    assert Cache(tmp_path).path("awattar", "2023-01").exists()


def test_cache_miss_without_fetch_raises(tmp_path: Path) -> None:
    with pytest.raises(CacheMissError):
        load_month(2023, 1, cache=Cache(tmp_path, allow_fetch=False))


def test_gap_in_one_month_fails_whole_load_and_is_not_cached(tmp_path: Path) -> None:
    def gappy(start_ms: int, end_ms: int) -> dict[str, Any]:
        payload = _FakeFetch()(start_ms, end_ms)
        if start_ms == _ms(pd.Timestamp("2023-02-01", tz="Europe/Vienna")):
            del payload["data"][100]
        return payload

    with pytest.raises(AwattarError, match="lückenhaft"):
        load_spot_prices(2023, cache=Cache(tmp_path), fetch=gappy)
    assert not Cache(tmp_path).path("awattar", "2023-02").exists()


def test_corrupt_cache_entry_raises(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    start, _ = month_range(2023, 1)
    wrong = pd.DataFrame({"load_kwh": 1.0}, index=pd.date_range(start, periods=4, freq="15min"))
    cache.get_or_fetch("awattar", "2023-01", lambda: wrong)
    with pytest.raises(AwattarError, match="löschen"):
        load_month(2023, 1, cache=cache, fetch=_FakeFetch())


# --- real API ------------------------------------------------------------------------------


@pytest.mark.network
def test_network_january_2023(tmp_path: Path) -> None:
    out = load_month(2023, 1, cache=Cache(tmp_path))
    validate_frame(out)
    assert len(out) == 31 * 24 * 4
    assert out.index[0] == pd.Timestamp("2023-01-01 00:00", tz="Europe/Vienna")
    mean = float(out[COLUMN].mean())
    # January 2023 AT day-ahead averaged roughly 140 EUR/MWh.
    assert 5.0 <= mean <= 30.0
