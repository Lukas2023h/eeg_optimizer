"""Tests for the Austrian CO2 intensity from Energy-Charts (F1-08). Offline: HTTP is mocked."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import requests

from eeg_optimizer.sources import energy_charts
from eeg_optimizer.sources.cache import Cache, CacheMissError
from eeg_optimizer.sources.energy_charts import (
    COLUMN,
    EMISSION_FACTORS_G_PER_KWH,
    NON_GENERATION,
    EnergyChartsError,
    cache_key,
    fetch_public_power,
    intensity_from_generation,
    load_co2_intensity,
    parse_public_power,
)
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import validate_frame

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_JULY = FIXTURES / "energy_charts_public_power_at_2023-07-02_1d.json"
FIXTURE_DST = FIXTURES / "energy_charts_public_power_at_2023-03-26_dst_1d.json"
QUARTER_S = 900


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def _day(date: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(date, tz="Europe/Vienna")
    return start, pd.Timestamp(start + pd.DateOffset(days=1))


def _s(ts: pd.Timestamp) -> int:
    return int(ts.tz_convert("UTC").value // 1_000_000_000)


def _payload(first_s: int, last_s: int) -> dict[str, Any]:
    """Energy-Charts-like payload for inclusive ``[first_s, last_s]`` (gas 1/4, hydro 3/4)."""
    stamps = list(range(first_s, last_s + 1, QUARTER_S))
    n = len(stamps)
    return {
        "unix_seconds": stamps,
        "production_types": [
            {"name": "Cross border electricity trading", "data": [-5000.0] * n},
            {"name": "Fossil gas", "data": [1000.0] * n},
            {"name": "Hydro Run-of-River", "data": [3000.0] * n},
            {"name": "Load", "data": [7000.0] * n},
            {"name": "Renewable share of generation", "data": [None] * n},
        ],
        "deprecated": False,
    }


class _FakeFetch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, start: str, end: str) -> dict[str, Any]:
        self.calls.append((start, end))
        return _payload(_s(pd.Timestamp(start)), _s(pd.Timestamp(end)))


# --- method --------------------------------------------------------------------------------


def test_intensity_is_generation_weighted_mean_of_factors() -> None:
    out = intensity_from_generation(
        {"Fossil gas": np.array([1000.0, 0.0]), "Hydro Run-of-River": np.array([3000.0, 10.0])}
    )
    assert out == pytest.approx([(1000 * 490 + 3000 * 24) / 4000, 24.0])


def test_factors_are_documented_ipcc_values() -> None:
    assert EMISSION_FACTORS_G_PER_KWH["Fossil gas"] == 490.0
    assert EMISSION_FACTORS_G_PER_KWH["Hydro Run-of-River"] == 24.0
    assert EMISSION_FACTORS_G_PER_KWH["Wind onshore"] == 11.0
    assert EMISSION_FACTORS_G_PER_KWH["Solar"] == 41.0
    assert all(0 <= f <= 1000 for f in EMISSION_FACTORS_G_PER_KWH.values())
    # Imports and storage pumping never count as generation.
    assert "Cross border electricity trading" in NON_GENERATION
    assert "Hydro pumped storage consumption" in NON_GENERATION
    assert not NON_GENERATION & set(EMISSION_FACTORS_G_PER_KWH)


def test_zero_total_generation_raises() -> None:
    with pytest.raises(EnergyChartsError, match="0 MW"):
        intensity_from_generation({"Solar": np.array([1.0, 0.0])})


def test_unknown_factor_raises() -> None:
    with pytest.raises(EnergyChartsError, match="Emissionsfaktor"):
        intensity_from_generation({"Fusion": np.array([1.0])})


# --- fixtures ------------------------------------------------------------------------------


def test_fixture_july_column_range_and_timezone() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    out = parse_public_power(payload, start, end)
    validate_frame(out)
    assert list(out.columns) == [COLUMN] == ["co2_g_per_kwh"]
    assert str(pd.DatetimeIndex(out.index).tz) == "Europe/Vienna"
    assert len(out) == 96
    # First API row: 2023-07-01 22:00 UTC = 2023-07-02 00:00 CEST (interval start).
    assert payload["unix_seconds"][0] == 1688248800
    assert out.index[0] == pd.Timestamp("2023-07-02 00:00", tz="Europe/Vienna")
    values = out[COLUMN].to_numpy()
    assert ((values >= 0) & (values <= 600)).all()
    # Hydro-dominated summer Sunday: far below the gas factor.
    assert 20.0 <= values.mean() <= 100.0


def test_fixture_first_interval_matches_manual_calculation() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    out = parse_public_power(payload, start, end)
    num = den = 0.0
    for entry in payload["production_types"]:
        if entry["name"] in EMISSION_FACTORS_G_PER_KWH:
            num += entry["data"][0] * EMISSION_FACTORS_G_PER_KWH[entry["name"]]
            den += entry["data"][0]
    assert out[COLUMN].iloc[0] == pytest.approx(num / den)


def test_imports_do_not_change_intensity() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    before = parse_public_power(payload, start, end)
    for entry in payload["production_types"]:
        if entry["name"] in NON_GENERATION and entry["name"] != "Renewable share of generation":
            entry["data"] = [x * 3.0 + 100.0 for x in entry["data"]]
    after = parse_public_power(payload, start, end)
    pd.testing.assert_frame_equal(before, after)


def test_fixture_dst_day_has_92_quarters() -> None:
    payload = _load(FIXTURE_DST)
    start, end = _day("2023-03-26")
    out = parse_public_power(payload, start, end)
    validate_frame(out)
    assert len(out) == 92
    assert out.index[7] == pd.Timestamp("2023-03-26 01:45", tz="Europe/Vienna")
    assert out.index[8] == pd.Timestamp("2023-03-26 03:00", tz="Europe/Vienna")
    assert ((out[COLUMN] >= 0) & (out[COLUMN] <= 600)).all()


def test_autumn_dst_day_has_100_quarters() -> None:
    start, end = _day("2023-10-29")
    payload = _payload(_s(start), _s(end) - QUARTER_S)
    out = parse_public_power(payload, start, end)
    assert len(out) == 100
    assert np.allclose(out[COLUMN].to_numpy(), (1000 * 490 + 3000 * 24) / 4000)


def test_unsorted_timestamps_keep_values_aligned() -> None:
    start, end = _day("2023-01-10")
    payload = _payload(_s(start), _s(end) - QUARTER_S)
    gas = payload["production_types"][1]["data"]
    gas[0] = 0.0
    payload["unix_seconds"].reverse()
    for entry in payload["production_types"]:
        entry["data"].reverse()
    out = parse_public_power(payload, start, end)
    assert out[COLUMN].iloc[0] == pytest.approx(24.0)
    assert out[COLUMN].iloc[1] == pytest.approx((1000 * 490 + 3000 * 24) / 4000)


# --- gaps and malformed data ---------------------------------------------------------------


def test_gap_raises_clear_error_instead_of_filling() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    del payload["unix_seconds"][5]
    for entry in payload["production_types"]:
        del entry["data"][5]
    with pytest.raises(EnergyChartsError, match=r"lückenhaft: 1 von 96 Intervallen.*01:15"):
        parse_public_power(payload, start, end)


def test_null_value_raises_instead_of_filling() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    gas = next(e for e in payload["production_types"] if e["name"] == "Fossil gas")
    gas["data"][10] = None
    with pytest.raises(EnergyChartsError, match=r"fehlt: 'Fossil gas'.*02:30"):
        parse_public_power(payload, start, end)


def test_unknown_production_type_raises() -> None:
    payload = _load(FIXTURE_JULY)
    start, end = _day("2023-07-02")
    payload["production_types"].append({"name": "Fusion", "data": [1.0] * 96})
    with pytest.raises(EnergyChartsError, match=r"Unbekannte.*Fusion"):
        parse_public_power(payload, start, end)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda p: p.update(unix_seconds=None), "unix_seconds"),
        (lambda p: p["unix_seconds"].__setitem__(0, 1.5), "ganze Sekunde"),
        (lambda p: p["unix_seconds"].__setitem__(0, p["unix_seconds"][0] + 60), "Raster"),
        (lambda p: p["unix_seconds"].__setitem__(1, p["unix_seconds"][0]), "doppelt"),
        (lambda p: p["unix_seconds"].__setitem__(-1, p["unix_seconds"][-1] + 900), "außerhalb"),
        (lambda p: p.update(production_types=None), "production_types"),
        (lambda p: p["production_types"].append({"data": []}), "ohne Namen"),
        (lambda p: p["production_types"].append(dict(p["production_types"][2])), "doppelt"),
        (lambda p: p["production_types"][2]["data"].pop(), "nicht 96 Werte"),
        (lambda p: p["production_types"][2]["data"].__setitem__(0, "x"), "keine Zahl"),
        (lambda p: p["production_types"][2]["data"].__setitem__(0, True), "keine Zahl"),
        (lambda p: p["production_types"][2]["data"].__setitem__(0, -1.0), "negativ"),
        (lambda p: p["production_types"][2]["data"].__setitem__(0, float("inf")), "endlich"),
        (
            lambda p: p.update(
                production_types=[e for e in p["production_types"] if e["name"] == "Load"]
            ),
            "keine Erzeugungsart",
        ),
    ],
)
def test_malformed_payload_raises(mutate: Any, match: str) -> None:
    payload = _load(FIXTURE_JULY)
    assert payload["production_types"][2]["name"] == "Hydro Run-of-River"
    mutate(payload)
    start, end = _day("2023-07-02")
    with pytest.raises(EnergyChartsError, match=match):
        parse_public_power(payload, start, end)


def test_non_dict_payload_raises() -> None:
    start, end = _day("2023-07-02")
    with pytest.raises(EnergyChartsError, match="kein Objekt"):
        parse_public_power([1, 2], start, end)


def test_empty_range_raises() -> None:
    start, _ = _day("2023-07-02")
    with pytest.raises(EnergyChartsError, match="Leerer Zeitraum"):
        parse_public_power(_load(FIXTURE_JULY), start, start)


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


def test_fetch_uses_timeout_country_and_iso_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured.update(url=url, **kwargs)
        return _FakeResponse(_load(FIXTURE_JULY))

    monkeypatch.setattr(requests, "get", fake_get)
    payload = fetch_public_power("2023-07-01T22:00Z", "2023-07-02T21:45Z")
    assert captured["url"] == "https://api.energy-charts.info/public_power"
    assert captured["params"] == {
        "country": "at",
        "start": "2023-07-01T22:00Z",
        "end": "2023-07-02T21:45Z",
    }
    assert captured["timeout"] == energy_charts.HTTP_TIMEOUT_S > 0
    assert len(payload["unix_seconds"]) == 96


def test_fetch_http_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "get", lambda url, **kw: _FakeResponse({}, 503))
    with pytest.raises(requests.HTTPError):
        fetch_public_power("2023-01-01T00:00Z", "2023-01-01T00:45Z")


# --- whole year and cache ------------------------------------------------------------------


def test_load_co2_intensity_full_year_in_one_request(tmp_path: Path) -> None:
    fake = _FakeFetch()
    out = load_co2_intensity(2023, cache=Cache(tmp_path), fetch=fake)
    validate_frame(out)
    assert out.index.equals(year_index(2023))
    assert list(out.columns) == [COLUMN]
    assert len(out) == 35040
    # 1 Jan 00:00 CET = 31 Dec 23:00 UTC; last quarter 31 Dec 23:45 CET = 22:45 UTC (inclusive).
    assert fake.calls == [("2022-12-31T23:00Z", "2023-12-31T22:45Z")]
    assert np.allclose(out[COLUMN].to_numpy(), (1000 * 490 + 3000 * 24) / 4000)


def test_second_call_uses_cache(tmp_path: Path) -> None:
    fake = _FakeFetch()
    first = load_co2_intensity(2023, cache=Cache(tmp_path), fetch=fake)

    def no_http(start: str, end: str) -> Any:
        raise AssertionError("HTTP request on cache hit")

    second = load_co2_intensity(2023, cache=Cache(tmp_path, allow_fetch=False), fetch=no_http)
    pd.testing.assert_frame_equal(first, second, check_freq=False)
    assert len(fake.calls) == 1
    assert cache_key(2023) == f"co2-{energy_charts.FACTORS_VERSION}-2023"
    assert Cache(tmp_path).path("energy_charts", cache_key(2023)).exists()


def test_cache_miss_without_fetch_raises(tmp_path: Path) -> None:
    with pytest.raises(CacheMissError):
        load_co2_intensity(2023, cache=Cache(tmp_path, allow_fetch=False))


def test_gap_fails_whole_load_and_is_not_cached(tmp_path: Path) -> None:
    def gappy(start: str, end: str) -> dict[str, Any]:
        payload = _FakeFetch()(start, end)
        del payload["unix_seconds"][3000]
        for entry in payload["production_types"]:
            del entry["data"][3000]
        return payload

    with pytest.raises(EnergyChartsError, match="lückenhaft"):
        load_co2_intensity(2023, cache=Cache(tmp_path), fetch=gappy)
    assert not Cache(tmp_path).path("energy_charts", cache_key(2023)).exists()


def test_corrupt_cache_entry_raises(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    start = pd.Timestamp("2023-01-01", tz="Europe/Vienna")
    wrong = pd.DataFrame({"load_kwh": 1.0}, index=pd.date_range(start, periods=4, freq="15min"))
    cache.get_or_fetch("energy_charts", cache_key(2023), lambda: wrong)
    with pytest.raises(EnergyChartsError, match="löschen"):
        load_co2_intensity(2023, cache=cache, fetch=_FakeFetch())


# --- real API ------------------------------------------------------------------------------


@pytest.mark.network
def test_network_year_2023_mean(tmp_path: Path) -> None:
    out = load_co2_intensity(2023, cache=Cache(tmp_path))
    validate_frame(out)
    assert out.index.equals(year_index(2023))
    values = out[COLUMN].to_numpy()
    assert ((values >= 0) & (values <= 600)).all()
    assert 50.0 <= float(values.mean()) <= 250.0
