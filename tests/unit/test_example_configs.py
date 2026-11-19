"""F2-02: defaults.yaml and the example communities (online + offline)."""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from eeg_optimizer.config import (
    DEFAULTS_PATH,
    GRID_FEE_REDUCTION_BY_EEG_TYPE,
    CommunityConfig,
    load_config,
)
from eeg_optimizer.sources.cache import PROJECT_ROOT
from eeg_optimizer.sources.synthetic import (
    synthetic_co2_intensity,
    synthetic_load,
    synthetic_pv,
    synthetic_spot_prices,
)
from eeg_optimizer.units import KWh

CONFIG_DIR = PROJECT_ROOT / "config"
EXAMPLE = CONFIG_DIR / "example_community.yaml"
EXAMPLE_OFFLINE = CONFIG_DIR / "example_community_offline.yaml"
SPEC = PROJECT_ROOT / "docs" / "spec.md"
YAML_FILES = [DEFAULTS_PATH, EXAMPLE, EXAMPLE_OFFLINE]

# Spec §7.1 as written down independently of the spec parser below.
SPEC_TARIFFS: dict[str, float] = {
    "energy_price": 13.0,
    "grid_fee_energy": 6.0,
    "levies_taxes": 6.0,
    "feed_in_tariff": 7.0,
    "eeg_internal_price": 12.0,
    "spot_markup": 1.5,
}


def _spec_tariff_rows() -> dict[str, str]:
    """``{parameter: default cell}`` from the table in spec §7.1."""
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("### 7.1", 1)[1].split("### 7.2", 1)[0]
    rows: dict[str, str] = {}
    for match in re.finditer(r"^\|\s*`(\w+)`\s*\|\s*([^|]+?)\s*\|", section, re.MULTILINE):
        rows[match.group(1)] = match.group(2)
    return rows


# ------------------------------------------------------------------ loading


@pytest.mark.parametrize("path", [EXAMPLE, EXAMPLE_OFFLINE], ids=lambda p: p.name)
def test_example_loads_as_standard_scenario(path: Path) -> None:
    cfg = load_config(path)
    assert len(cfg.households) == 5
    assert cfg.period.year == 2023
    assert cfg.pv.kwp == 30.0
    assert (cfg.location.lat, cfg.location.lon) == (48.31, 14.29)  # Linz
    assert cfg.tariffs.eeg_type == "local"
    assert cfg.name


def test_example_uses_real_data_sources() -> None:
    cfg = load_config(EXAMPLE)
    assert cfg.pv.source == "pvgis"
    assert cfg.prices.source == "awattar"
    assert cfg.co2.source == "energy_charts"
    assert {hh.load.source for hh in cfg.households} == {"htw"}
    profiles = [hh.load.profile for hh in cfg.households]
    assert len(set(profiles)) == len(profiles)


def test_offline_example_uses_only_synthetic_sources() -> None:
    cfg = load_config(EXAMPLE_OFFLINE)
    assert cfg.pv.source == "synthetic"
    assert cfg.prices.source == "synthetic"
    assert cfg.co2.source == "synthetic"
    assert {hh.load.source for hh in cfg.households} == {"synthetic"}
    assert all(hh.load.annual_kwh is not None for hh in cfg.households)


def test_offline_and_online_example_describe_the_same_community() -> None:
    online, offline = load_config(EXAMPLE), load_config(EXAMPLE_OFFLINE)
    assert [hh.id for hh in online.households] == [hh.id for hh in offline.households]
    assert online.location == offline.location
    assert online.period == offline.period
    assert online.tariffs == offline.tariffs
    assert (online.pv.kwp, online.pv.tilt_deg, online.pv.azimuth_deg) == (
        offline.pv.kwp,
        offline.pv.tilt_deg,
        offline.pv.azimuth_deg,
    )


def _synthetic_series(cfg: CommunityConfig) -> list[pd.DataFrame]:
    year = cfg.period.year
    frames = [
        synthetic_pv(
            kwp=cfg.pv.kwp,
            seed=cfg.pv.seed,
            year=year,
            latitude_deg=cfg.location.lat,
            longitude_deg=cfg.location.lon,
        ),
        synthetic_spot_prices(seed=cfg.prices.seed, year=year),
        synthetic_co2_intensity(seed=cfg.co2.seed, year=year),
    ]
    for hh in cfg.households:
        assert hh.load.annual_kwh is not None
        frames.append(
            synthetic_load(seed=hh.load.seed, annual_kwh=KWh(hh.load.annual_kwh), year=year)
        )
    return frames


def test_offline_example_builds_all_series_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Netzwerkzugriff in der Offline-Konfiguration")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)

    cfg = load_config(EXAMPLE_OFFLINE)
    frames = _synthetic_series(cfg)

    assert len(frames) == 3 + len(cfg.households)
    for frame in frames:
        assert len(frame) == 35040  # 365 days x 96 intervals, 2023
    for hh, frame in zip(cfg.households, frames[3:], strict=True):
        assert frame["load_kwh"].sum() == pytest.approx(hh.load.annual_kwh)


# ------------------------------------------------------------------ spec §7.1


def test_spec_table_is_parsed() -> None:
    rows = _spec_tariff_rows()
    assert set(SPEC_TARIFFS) | {"eeg_type", "grid_fee_reduction"} <= set(rows)


def _spec_reduction() -> dict[str, float]:
    cell = _spec_tariff_rows()["grid_fee_reduction"]
    return {k: float(v) for k, v in re.findall(r"(local|regional):\s*([0-9.]+)", cell)}


def test_spec_table_matches_expected_numbers() -> None:
    """Guards the independently written SPEC_TARIFFS against the spec text."""
    rows = _spec_tariff_rows()
    for name, expected in SPEC_TARIFFS.items():
        assert float(rows[name]) == expected, f"spec §7.1 {name}"
    assert rows["eeg_type"].strip("`") == "local"
    assert _spec_reduction() == {"local": 0.57, "regional": 0.28}
    assert dict(GRID_FEE_REDUCTION_BY_EEG_TYPE) == _spec_reduction()


def test_defaults_file_tariffs_match_spec() -> None:
    raw = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))["tariffs"]
    rows = _spec_tariff_rows()
    for name in SPEC_TARIFFS:
        assert raw[name] == float(rows[name]), name
    assert raw["eeg_type"] == "local"
    assert raw["grid_fee_reduction"] is None  # derived from eeg_type


@pytest.mark.parametrize("path", [EXAMPLE, EXAMPLE_OFFLINE], ids=lambda p: p.name)
def test_example_tariffs_match_spec(path: Path) -> None:
    tariffs = load_config(path).tariffs
    for name, expected in SPEC_TARIFFS.items():
        assert getattr(tariffs, name) == expected, name
    assert tariffs.eeg_type == "local"
    assert tariffs.grid_fee_reduction is None
    assert tariffs.effective_grid_fee_reduction == _spec_reduction()["local"]


def test_grid_purchase_sum_matches_spec() -> None:
    t = load_config(EXAMPLE).tariffs
    assert t.energy_price + t.grid_fee_energy + t.levies_taxes == pytest.approx(25.0)


def test_regional_reduction_matches_spec(tmp_path: Path) -> None:
    text = EXAMPLE.read_text(encoding="utf-8").replace("eeg_type: local", "eeg_type: regional")
    path = tmp_path / "regional.yaml"
    path.write_text(text, encoding="utf-8")
    tariffs = load_config(path).tariffs
    assert tariffs.eeg_type == "regional"
    assert tariffs.effective_grid_fee_reduction == _spec_reduction()["regional"]


def test_defaults_location_and_year_match_spec() -> None:
    """spec §7.2: reference year 2023, standard location Linz (48.31 N, 14.29 E)."""
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    assert defaults["period"]["year"] == 2023
    assert defaults["location"] == {"lat": 48.31, "lon": 14.29}


# ------------------------------------------------------------------ comments

_KEY_LINE = re.compile(r"^\s*(?:- )?([A-Za-z_][A-Za-z0-9_]*):(.*)$")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_UNIT_HINTS = (
    "kWh",
    "kWp",
    "kW",
    "Minuten",
    "ct/kWh",
    "g CO₂",
    "Grad",
    "Prozent",
    "Anteil",
    "ganze Zahl",
    "ohne Einheit",
)


def _key_lines(path: Path) -> list[tuple[int, str, str, str]]:
    """``(line number, key, value, comment)`` for every ``key:`` line."""
    out = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        match = _KEY_LINE.match(line)
        if match is None:
            continue
        value, _, comment = match.group(2).partition("#")
        out.append((no, match.group(1), value.strip(), comment.strip()))
    return out


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_every_parameter_has_a_comment(path: Path) -> None:
    lines = _key_lines(path)
    assert lines
    missing = [f"{path.name}:{no} {key}" for no, key, _, comment in lines if len(comment) < 5]
    assert not missing, f"Parameter ohne Kommentar: {missing}"


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_every_number_comment_names_its_unit(path: Path) -> None:
    missing = [
        f"{path.name}:{no} {key}"
        for no, key, value, comment in _key_lines(path)
        if _NUMBER.match(value) and not any(hint in comment for hint in _UNIT_HINTS)
    ]
    assert not missing, f"Zahl ohne Einheit im Kommentar: {missing}"


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_comments_are_german(path: Path) -> None:
    """Crude check: German words or umlauts appear in the comments."""
    comments = " ".join(comment for *_, comment in _key_lines(path))
    assert re.search(r"[äöüß]|\b(der|die|das|und|oder|ohne|nur)\b", comments)
