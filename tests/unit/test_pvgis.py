"""Tests for PV generation from PVGIS (F1-05). Offline: pvlib/HTTP are mocked."""

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pvlib.iotools.pvgis as pvlib_pvgis
import pytest
from pvlib.iotools import read_pvgis_hourly

from eeg_optimizer.sources import pvgis
from eeg_optimizer.sources.cache import Cache, CacheMissError
from eeg_optimizer.sources.pvgis import (
    COLUMN,
    PvgisError,
    fetch_pvgis_hourly,
    hourly_to_15min,
    load_pv,
    map_to_year,
)
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import validate_frame

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "pvgis_linz_10kwp_30deg_south_2023-06-20_3d.json"
)

LINZ: dict[str, Any] = {
    "kwp": 10.0,
    "tilt_deg": 30.0,
    "azimuth_deg": 0.0,
    "latitude_deg": 48.31,
    "longitude_deg": 14.29,
}


def _fixture_raw() -> pd.DataFrame:
    data, _meta = read_pvgis_hourly(str(FIXTURE))
    assert isinstance(data, pd.DataFrame)
    return data


def _full_year_raw(year: int = 2023) -> pd.DataFrame:
    """PVGIS-like hourly frame for a whole UTC year, stamps at HH:10 UTC."""
    index = pd.date_range(
        pd.Timestamp(year=year, month=1, day=1, minute=10, tz="UTC"),
        pd.Timestamp(year=year + 1, month=1, day=1, tz="UTC"),
        freq="h",
        inclusive="left",
    )
    hour = index.hour.to_numpy()
    power_w = np.clip(np.sin((hour - 4) / 16 * np.pi), 0, None) * 8000.0
    # Mark the last UTC hour so the wrap-around is observable.
    power_w[-1] = 123.0
    return pd.DataFrame({"P": power_w, "temp_air": 5.0}, index=index)


# --- conversion of the fixture -------------------------------------------------------------


def test_fixture_conversion_kwh_tz_and_energy_conservation() -> None:
    raw = _fixture_raw()
    assert len(raw) == 72
    out = hourly_to_15min(raw)
    validate_frame(out)
    assert list(out.columns) == [COLUMN]
    assert str(pd.DatetimeIndex(out.index).tz) == "Europe/Vienna"
    assert len(out) == 72 * 4
    # Energy conservation: sum of hourly mean power [W] * 1 h == sum of 15-min kWh.
    assert out[COLUMN].sum() == pytest.approx(raw["P"].sum() / 1000.0, rel=1e-12)


def test_fixture_utc_stamp_inside_hour_maps_to_interval_start_in_vienna() -> None:
    raw = _fixture_raw()
    out = hourly_to_15min(raw)
    # First fixture row: 2023-06-20 00:10 UTC -> hour [00:00, 01:00) UTC = 02:00 CEST.
    assert out.index[0] == pd.Timestamp("2023-06-20 02:00", tz="Europe/Vienna")
    # The hour stamped 10:10 UTC is the interval 12:00-13:00 CEST, split into four quarters.
    raw_index = pd.DatetimeIndex(raw.index)
    p_w = float(raw["P"].to_numpy()[raw_index == pd.Timestamp("2023-06-20 10:10", tz="UTC")][0])
    assert p_w > 0
    out_index = pd.DatetimeIndex(out.index)
    noon = (out_index >= pd.Timestamp("2023-06-20 12:00", tz="Europe/Vienna")) & (
        out_index < pd.Timestamp("2023-06-20 13:00", tz="Europe/Vienna")
    )
    quarters = out[COLUMN].to_numpy()[noon]
    assert len(quarters) == 4
    assert np.allclose(quarters, p_w / 1000.0 / 4)


def test_fixture_daily_peak_near_solar_noon_local_time() -> None:
    out = hourly_to_15min(_fixture_raw())
    index = pd.DatetimeIndex(out.index)
    on_day = index.normalize() == pd.Timestamp("2023-06-21", tz="Europe/Vienna")
    values = out[COLUMN].to_numpy()[on_day]
    hours = index[on_day].hour
    peak_hour = int(hours[int(np.argmax(values))])
    # Solar noon in Linz in summer is ~13:05 CEST; a 1-hour shift would be visible here.
    assert 12 <= peak_hour <= 13
    assert values[hours < 4].sum() == 0.0


def test_hourly_to_15min_rejects_bad_input() -> None:
    raw = _fixture_raw()
    with pytest.raises(PvgisError, match="Spalte"):
        hourly_to_15min(raw.drop(columns=["P"]))
    with pytest.raises(PvgisError, match="zeitzonenbehafteten"):
        hourly_to_15min(raw.tz_localize(None))
    for bad_value, match in ((-1.0, "negativ"), (np.nan, "NaN")):
        power = raw["P"].to_numpy(dtype=float, copy=True)
        power[5] = bad_value
        with pytest.raises(PvgisError, match=match):
            hourly_to_15min(raw.assign(P=power))
    inf = raw.copy()
    inf["P"] = np.where(np.arange(len(raw)) == 5, np.inf, raw["P"].to_numpy())
    with pytest.raises(PvgisError, match="inf"):
        hourly_to_15min(inf)
    shifted = raw.copy()
    idx = shifted.index.to_list()
    idx[3] = idx[3] + pd.Timedelta(minutes=20)
    shifted.index = pd.DatetimeIndex(idx)
    with pytest.raises(PvgisError, match="Minuten"):
        hourly_to_15min(shifted)


def test_hourly_to_15min_rejects_gaps() -> None:
    raw = _fixture_raw().drop(index=_fixture_raw().index[10])
    with pytest.raises(ValueError, match=r"gap|Lücke|missing|contiguous|grid"):
        hourly_to_15min(raw)


# --- mapping onto the local year -----------------------------------------------------------


def test_map_to_year_fills_local_year_and_conserves_energy() -> None:
    raw = _full_year_raw()
    out = map_to_year(hourly_to_15min(raw), 2023)
    validate_frame(out)
    assert out.index.equals(year_index(2023))
    assert out[COLUMN].sum() == pytest.approx(raw["P"].sum() / 1000.0, rel=1e-12)
    # The last UTC hour (31 Dec 23:00 UTC = 1 Jan 00:00 local next year) moved to the front.
    assert np.allclose(out[COLUMN].iloc[:4].to_numpy(), 123.0 / 1000.0 / 4)


def test_map_to_year_leap_year() -> None:
    out = map_to_year(hourly_to_15min(_full_year_raw(2024)), 2024)
    assert out.index.equals(year_index(2024))
    assert len(out) == 35136


def test_map_to_year_rejects_partial_year() -> None:
    with pytest.raises(PvgisError, match="UTC-Jahr 2023"):
        map_to_year(hourly_to_15min(_fixture_raw()), 2023)


# --- parameters passed to PVGIS ------------------------------------------------------------


class _FakePvlib:
    def __init__(self, raw: pd.DataFrame) -> None:
        self.raw = raw
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
        self.calls.append((args, kwargs))
        return self.raw, {}


@pytest.mark.parametrize(
    ("azimuth_deg", "pvlib_azimuth"), [(0.0, 180.0), (-90.0, 90.0), (90.0, 270.0)]
)
def test_parameters_passed_to_pvlib(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, azimuth_deg: float, pvlib_azimuth: float
) -> None:
    fake = _FakePvlib(_full_year_raw())
    monkeypatch.setattr(pvgis, "get_pvgis_hourly", fake)
    load_pv(
        kwp=7.5,
        tilt_deg=25.0,
        azimuth_deg=azimuth_deg,
        latitude_deg=48.31,
        longitude_deg=14.29,
        year=2023,
        cache=Cache(tmp_path),
    )
    assert len(fake.calls) == 1
    args, kwargs = fake.calls[0]
    assert args == (48.31, 14.29)
    assert kwargs["peakpower"] == 7.5
    assert kwargs["surface_tilt"] == 25.0
    assert kwargs["surface_azimuth"] == pvlib_azimuth
    assert kwargs["pvcalculation"] is True
    assert kwargs["start"] == 2023
    assert kwargs["end"] == 2023
    assert kwargs["loss"] == pvgis.DEFAULT_LOSS_PCT


class _FakeResponse:
    ok = True

    def __init__(self, text: str) -> None:
        self.text = text


@pytest.mark.parametrize(("azimuth_deg", "aspect"), [(0.0, 0.0), (-90.0, -90.0), (45.0, 45.0)])
def test_pvgis_http_request_uses_pvgis_convention(
    monkeypatch: pytest.MonkeyPatch, azimuth_deg: float, aspect: float
) -> None:
    """Through the real pvlib code: 0 = south ends up as PVGIS ``aspect`` unchanged."""
    captured: dict[str, Any] = {}

    def fake_get(url: str, params: dict[str, Any], **kwargs: Any) -> _FakeResponse:
        captured.update(url=url, params=params, timeout=kwargs.get("timeout"))
        return _FakeResponse(FIXTURE.read_text())

    monkeypatch.setattr(pvlib_pvgis.requests, "get", fake_get)
    raw = fetch_pvgis_hourly(**{**LINZ, "azimuth_deg": azimuth_deg}, year=2023, loss_pct=14.0)
    params = captured["params"]
    assert captured["url"].endswith("seriescalc")
    assert params["aspect"] == aspect
    assert params["angle"] == 30.0
    assert params["peakpower"] == 10.0
    assert params["pvcalculation"] == 1
    assert params["loss"] == 14.0
    assert params["startyear"] == params["endyear"] == 2023
    assert params["lat"] == 48.31
    assert params["lon"] == 14.29
    assert captured["timeout"] == pvgis.HTTP_TIMEOUT_S
    assert len(raw) == 72


def test_cache_key_is_exact() -> None:
    base = {**LINZ, "year": 2023, "loss_pct": 14.0}
    assert pvgis.cache_key(**base) != pvgis.cache_key(**{**base, "tilt_deg": 30.04})
    assert pvgis.cache_key(**base) != pvgis.cache_key(**{**base, "kwp": 10.0001})


def test_corrupt_cache_entry_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakePvlib(_full_year_raw())
    monkeypatch.setattr(pvgis, "get_pvgis_hourly", fake)
    cache = Cache(tmp_path)
    key = pvgis.cache_key(**LINZ, year=2023, loss_pct=pvgis.DEFAULT_LOSS_PCT)
    wrong = pd.DataFrame({"load_kwh": 1.0}, index=year_index(2023))
    cache.get_or_fetch(pvgis.SOURCE, key, lambda: wrong)
    with pytest.raises(PvgisError, match="löschen"):
        load_pv(**LINZ, cache=cache)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"kwp": 0.0}, "kWp"),
        ({"kwp": float("nan")}, "kWp"),
        ({"tilt_deg": 95.0}, "Neigung"),
        ({"azimuth_deg": 200.0}, "Ausrichtung"),
        ({"latitude_deg": 91.0}, "Breitengrad"),
        ({"longitude_deg": 181.0}, "Längengrad"),
        ({"longitude_deg": float("nan")}, "Längengrad"),
        ({"loss_pct": 100.0}, "Systemverluste"),
    ],
)
def test_invalid_parameters_raise_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, override: dict[str, float], match: str
) -> None:
    fake = _FakePvlib(_full_year_raw())
    monkeypatch.setattr(pvgis, "get_pvgis_hourly", fake)
    with pytest.raises(PvgisError, match=match):
        load_pv(**{**LINZ, **override}, cache=Cache(tmp_path))
    assert fake.calls == []


# --- caching -------------------------------------------------------------------------------


def test_second_call_uses_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakePvlib(_full_year_raw())
    monkeypatch.setattr(pvgis, "get_pvgis_hourly", fake)
    first = load_pv(**LINZ, cache=Cache(tmp_path))
    assert len(fake.calls) == 1

    def no_http(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("HTTP request on cache hit")

    monkeypatch.setattr(pvgis, "get_pvgis_hourly", no_http)
    monkeypatch.setattr(pvlib_pvgis.requests, "get", no_http)
    second = load_pv(**LINZ, cache=Cache(tmp_path, allow_fetch=False))
    pd.testing.assert_frame_equal(first, second)
    validate_frame(second)
    assert second.index.equals(year_index(2023))


def test_changed_parameters_miss_the_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakePvlib(_full_year_raw())
    monkeypatch.setattr(pvgis, "get_pvgis_hourly", fake)
    load_pv(**LINZ, cache=Cache(tmp_path))
    with pytest.raises(CacheMissError):
        load_pv(**{**LINZ, "tilt_deg": 35.0}, cache=Cache(tmp_path, allow_fetch=False))
    for change in ({"azimuth_deg": -90.0}, {"kwp": 5.0}, {"latitude_deg": 47.0}):
        load_pv(**{**LINZ, **change}, cache=Cache(tmp_path))
    assert len(fake.calls) == 4


def test_pvlib_returning_non_frame_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pvgis, "get_pvgis_hourly", lambda *a, **k: ("oops", {}))
    with pytest.raises(PvgisError, match="DataFrame"):
        load_pv(**LINZ, cache=Cache(tmp_path))


# --- real API ------------------------------------------------------------------------------


@pytest.mark.network
def test_network_linz_10kwp_annual_yield_2023(tmp_path: Path) -> None:
    out = load_pv(**LINZ, year=2023, cache=Cache(tmp_path))
    validate_frame(out)
    assert out.index.equals(year_index(2023))
    total = float(out[COLUMN].sum())
    assert 9000.0 <= total <= 13000.0
