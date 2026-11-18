"""F2-03: build_inputs builds the community input bundle from a configuration."""

from __future__ import annotations

import socket
from dataclasses import replace
from typing import Any

import pandas as pd
import pytest

from eeg_optimizer.community import (
    CO2_SOURCES,
    LOAD_SOURCES,
    PRICE_SOURCES,
    PV_SOURCES,
    CommunitySourceError,
    build_inputs,
)
from eeg_optimizer.config import (
    CommunityConfig,
    HouseholdConfig,
    LoadConfig,
    Location,
    PvConfig,
    SeriesSourceConfig,
    load_config,
)
from eeg_optimizer.sources import awattar, energy_charts, htw, pvgis
from eeg_optimizer.sources.cache import PROJECT_ROOT, Cache
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import FrameValidationError, validate_frame

EXAMPLE = PROJECT_ROOT / "config" / "example_community.yaml"
EXAMPLE_OFFLINE = PROJECT_ROOT / "config" / "example_community_offline.yaml"


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Netzwerkzugriff beim Bauen der Offline-Zeitreihen")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture(scope="module")
def offline_cfg() -> CommunityConfig:
    return load_config(EXAMPLE_OFFLINE)


def _year_frame(column: str, value: float, year: int = 2023) -> pd.DataFrame:
    index = year_index(year)
    return pd.DataFrame({column: [value] * len(index)}, index=index)


# ------------------------------------------------------------------ offline build


def test_offline_config_builds_all_frames_on_one_index(
    no_network: None, offline_cfg: CommunityConfig
) -> None:
    inputs = build_inputs(offline_cfg)

    expected = year_index(2023)
    assert list(inputs.pv.columns) == ["pv_kwh"]
    assert list(inputs.prices.columns) == ["spot_ct_per_kwh"]
    assert list(inputs.co2.columns) == ["co2_g_per_kwh"]
    assert list(inputs.loads) == [hh.id for hh in offline_cfg.households]
    frames = [inputs.pv, inputs.prices, inputs.co2, *inputs.loads.values()]
    for frame in frames:
        assert frame.index.equals(expected)
        validate_frame(frame)
    for hh in offline_cfg.households:
        assert list(inputs.loads[hh.id].columns) == ["load_kwh"]
        assert inputs.loads[hh.id]["load_kwh"].sum() == pytest.approx(hh.load.annual_kwh)
    assert inputs.index.equals(expected)
    assert len(inputs.index) == 35040


def test_build_is_reproducible(no_network: None, offline_cfg: CommunityConfig) -> None:
    a, b = build_inputs(offline_cfg), build_inputs(offline_cfg)
    pd.testing.assert_frame_equal(a.pv, b.pv)
    pd.testing.assert_frame_equal(a.co2, b.co2)
    for hh_id in a.loads:
        pd.testing.assert_frame_equal(a.loads[hh_id], b.loads[hh_id])


def test_synthetic_config_values_reach_the_generators(
    no_network: None, offline_cfg: CommunityConfig
) -> None:
    """kWp and seeds come from the config, not from generator defaults."""
    base = build_inputs(offline_cfg)
    bigger = replace(offline_cfg, pv=replace(offline_cfg.pv, kwp=60.0))
    other_seed = replace(offline_cfg, prices=SeriesSourceConfig(source="synthetic", seed=99))

    doubled = build_inputs(bigger).pv["pv_kwh"]
    assert doubled.to_numpy() == pytest.approx(2 * base.pv["pv_kwh"].to_numpy())
    assert not build_inputs(other_seed).prices.equals(base.prices)


def test_synthetic_load_without_annual_kwh(no_network: None, offline_cfg: CommunityConfig) -> None:
    hh = HouseholdConfig(id="x", load=LoadConfig(source="synthetic", seed=3))
    inputs = build_inputs(replace(offline_cfg, households=(hh,)))
    assert inputs.loads["x"]["load_kwh"].sum() > 0


def test_other_year_uses_that_years_index(no_network: None, offline_cfg: CommunityConfig) -> None:
    cfg = replace(offline_cfg, period=replace(offline_cfg.period, year=2024))
    inputs = build_inputs(cfg)
    assert inputs.index.equals(year_index(2024))
    assert len(inputs.co2) == 366 * 96


# ------------------------------------------------------------------ dispatch


def test_unknown_load_source_has_clear_german_message(offline_cfg: CommunityConfig) -> None:
    """smartmeter is a valid config value but has no loader before F8-01."""
    hh = HouseholdConfig(id="sm", load=LoadConfig(source="smartmeter", path="data/x.csv"))
    cfg = replace(offline_cfg, households=(*offline_cfg.households, hh))
    with pytest.raises(CommunitySourceError) as exc:
        build_inputs(cfg)
    message = str(exc.value)
    assert f"households[{len(offline_cfg.households)}].load.source" in message
    assert "'smartmeter'" in message
    assert "nicht verfügbar" in message
    assert "htw, synthetic" in message


@pytest.mark.parametrize(
    ("section", "registry"),
    [("pv", PV_SOURCES), ("prices", PRICE_SOURCES), ("co2", CO2_SOURCES)],
)
def test_unknown_series_source_names_key_path(
    monkeypatch: pytest.MonkeyPatch,
    offline_cfg: CommunityConfig,
    section: str,
    registry: dict[str, Any],
) -> None:
    monkeypatch.delitem(registry, "synthetic")
    with pytest.raises(CommunitySourceError, match=rf"^{section}\.source: .*'synthetic'"):
        build_inputs(offline_cfg)


def test_registry_is_extensible(
    monkeypatch: pytest.MonkeyPatch, offline_cfg: CommunityConfig
) -> None:
    calls: list[tuple[LoadConfig, int, Cache | None]] = []

    def fake_smartmeter(cfg: LoadConfig, year: int, cache: Cache | None) -> pd.DataFrame:
        calls.append((cfg, year, cache))
        return _year_frame("load_kwh", 0.1, year)

    monkeypatch.setitem(LOAD_SOURCES, "smartmeter", fake_smartmeter)
    load = LoadConfig(source="smartmeter", path="data/hh.csv")
    cfg = replace(offline_cfg, households=(HouseholdConfig(id="sm", load=load),))
    inputs = build_inputs(cfg)

    assert calls == [(load, 2023, None)]
    assert inputs.loads["sm"]["load_kwh"].sum() == pytest.approx(0.1 * 35040)


def test_network_adapters_pass_config_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """pvgis/htw/awattar/energy_charts adapters map the config onto the source kwargs."""
    cfg = load_config(EXAMPLE)
    cache = Cache(tmp_path)
    seen: dict[str, Any] = {}

    def fake_pv(**kwargs: Any) -> pd.DataFrame:
        seen["pv"] = kwargs
        return _year_frame("pv_kwh", 0.0)

    def fake_household(profile: int, **kwargs: Any) -> pd.DataFrame:
        seen.setdefault("htw", []).append((profile, kwargs))
        return _year_frame("load_kwh", 0.1)

    def fake_prices(year: int, **kwargs: Any) -> pd.DataFrame:
        seen["prices"] = (year, kwargs)
        return _year_frame("spot_ct_per_kwh", -1.0)

    def fake_co2(year: int, **kwargs: Any) -> pd.DataFrame:
        seen["co2"] = (year, kwargs)
        return _year_frame("co2_g_per_kwh", 90.0)

    monkeypatch.setattr(pvgis, "load_pv", fake_pv)
    monkeypatch.setattr(htw, "load_household", fake_household)
    monkeypatch.setattr(awattar, "load_spot_prices", fake_prices)
    monkeypatch.setattr(energy_charts, "load_co2_intensity", fake_co2)

    inputs = build_inputs(cfg, cache=cache)

    assert seen["pv"] == {
        "kwp": cfg.pv.kwp,
        "tilt_deg": cfg.pv.tilt_deg,
        "azimuth_deg": cfg.pv.azimuth_deg,
        "latitude_deg": cfg.location.lat,
        "longitude_deg": cfg.location.lon,
        "year": 2023,
        "loss_pct": cfg.pv.loss_pct,
        "cache": cache,
    }
    assert seen["htw"] == [
        (hh.load.profile, {"year": 2023, "cache": cache}) for hh in cfg.households
    ]
    assert seen["prices"] == (2023, {"cache": cache})
    assert seen["co2"] == (2023, {"cache": cache})
    assert list(inputs.loads) == [hh.id for hh in cfg.households]


def test_htw_without_profile_is_rejected() -> None:
    with pytest.raises(CommunitySourceError, match="profile"):
        LOAD_SOURCES["htw"](LoadConfig(source="htw"), 2023, None)


# ------------------------------------------------------------------ boundary checks


def _with_pv_loader(monkeypatch: pytest.MonkeyPatch, frame: pd.DataFrame) -> None:
    def loader(cfg: PvConfig, loc: Location, year: int, cache: Cache | None) -> pd.DataFrame:
        return frame

    monkeypatch.setitem(PV_SOURCES, "synthetic", loader)


def test_wrong_column_is_rejected(
    monkeypatch: pytest.MonkeyPatch, offline_cfg: CommunityConfig
) -> None:
    _with_pv_loader(monkeypatch, _year_frame("load_kwh", 0.0))
    with pytest.raises(CommunitySourceError, match=r"^pv: .*'pv_kwh'"):
        build_inputs(offline_cfg)


def test_wrong_year_is_rejected(
    monkeypatch: pytest.MonkeyPatch, offline_cfg: CommunityConfig
) -> None:
    _with_pv_loader(monkeypatch, _year_frame("pv_kwh", 0.0, year=2022))
    with pytest.raises(CommunitySourceError, match=r"^pv: Zeitachse passt nicht zum Jahr 2023"):
        build_inputs(offline_cfg)


def test_truncated_frame_is_rejected(
    monkeypatch: pytest.MonkeyPatch, offline_cfg: CommunityConfig
) -> None:
    _with_pv_loader(monkeypatch, _year_frame("pv_kwh", 0.0).iloc[:-96])
    with pytest.raises(CommunitySourceError, match="34944 statt 35040"):
        build_inputs(offline_cfg)


def test_invalid_frame_fails_validate_frame(
    monkeypatch: pytest.MonkeyPatch, offline_cfg: CommunityConfig
) -> None:
    _with_pv_loader(monkeypatch, _year_frame("pv_kwh", -1.0))
    with pytest.raises(FrameValidationError):
        build_inputs(offline_cfg)
