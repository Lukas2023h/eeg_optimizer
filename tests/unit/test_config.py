"""Tests for eeg_optimizer.config (F2-01)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from eeg_optimizer.config import (
    DEFAULTS_PATH,
    MAX_HOUSEHOLDS,
    CommunityConfig,
    ConfigError,
    config_to_dict,
    load_config,
    merge_dicts,
    parse_config,
    save_config,
)


def _households(n: int) -> list[dict[str, Any]]:
    return [{"id": f"hh{i:02d}", "load": {"source": "htw", "profile": i}} for i in range(1, n + 1)]


def _write(path: Path, data: object) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _load(tmp_path: Path, community: dict[str, Any]) -> CommunityConfig:
    return load_config(_write(tmp_path / "community.yaml", community))


def _with(**sections: Any) -> dict[str, Any]:
    community: dict[str, Any] = {"households": _households(3)}
    community.update(sections)
    return community


# ---------------------------------------------------------------- defaults / merging


def test_defaults_are_used_when_not_overridden(tmp_path: Path) -> None:
    cfg = _load(tmp_path, _with(pv={"kwp": 12.5}))
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    # overridden
    assert cfg.pv.kwp == 12.5
    # taken from defaults.yaml
    assert cfg.pv.tilt_deg == defaults["pv"]["tilt_deg"]
    assert cfg.pv.source == defaults["pv"]["source"]
    assert cfg.period.year == defaults["period"]["year"]
    assert cfg.location.lat == defaults["location"]["lat"]
    assert cfg.tariffs.energy_price == defaults["tariffs"]["energy_price"]
    assert cfg.tariffs.feed_in_tariff == defaults["tariffs"]["feed_in_tariff"]
    assert cfg.optimization.objective == defaults["optimization"]["objective"]
    assert cfg.allocation.strategy == "dynamic_proportional"
    assert [hh.id for hh in cfg.households] == ["hh01", "hh02", "hh03"]
    assert cfg.households[0].load.profile == 1


def test_custom_defaults_file_is_merged_key_by_key(tmp_path: Path) -> None:
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    defaults["tariffs"]["energy_price"] = 20.0
    defaults_path = _write(tmp_path / "defaults.yaml", defaults)
    community = _write(tmp_path / "c.yaml", _with(tariffs={"levies_taxes": 1.0}))
    cfg = load_config(community, defaults_path=defaults_path)
    assert cfg.tariffs.energy_price == 20.0
    assert cfg.tariffs.levies_taxes == 1.0
    assert cfg.tariffs.grid_fee_energy == defaults["tariffs"]["grid_fee_energy"]


def test_merge_dicts_replaces_lists_and_merges_mappings() -> None:
    base = {"a": {"x": 1, "y": 2}, "l": [1, 2]}
    merged = merge_dicts(base, {"a": {"y": 3}, "l": [9]})
    assert merged == {"a": {"x": 1, "y": 3}, "l": [9]}
    assert base == {"a": {"x": 1, "y": 2}, "l": [1, 2]}


def test_missing_defaults_file_raises(tmp_path: Path) -> None:
    community = _write(tmp_path / "c.yaml", _with())
    with pytest.raises(ConfigError, match=r"Defaults-Datei .* nicht gefunden"):
        load_config(community, defaults_path=tmp_path / "missing.yaml")


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"Konfigurationsdatei .* nicht gefunden"):
        load_config(tmp_path / "missing.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("pv: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="kein gültiges YAML"):
        load_config(path)


def test_non_mapping_top_level_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="oberste Ebene"):
        load_config(_write(tmp_path / "c.yaml", [1, 2]))


# ---------------------------------------------------------------- acceptance: invalid values


def test_negative_kwp_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"pv\.kwp: muss größer als 0 sein"):
        _load(tmp_path, _with(pv={"kwp": -1.0}))


def test_unknown_load_source_raises(tmp_path: Path) -> None:
    households = _households(3)
    households[1]["load"] = {"source": "opsd_unknown"}
    with pytest.raises(
        ConfigError, match=r"households\[1\]\.load\.source: unbekannter Wert 'opsd_unknown'"
    ):
        _load(tmp_path, _with(households=households))


def test_unknown_pv_source_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"pv\.source: unbekannter Wert 'sonne'"):
        _load(tmp_path, _with(pv={"source": "sonne"}))


def test_duplicate_household_id_raises(tmp_path: Path) -> None:
    households = _households(3)
    households[2]["id"] = "hh01"
    with pytest.raises(
        ConfigError, match=r"households\[2\]\.id: Haushalts-ID 'hh01' ist doppelt vergeben"
    ):
        _load(tmp_path, _with(households=households))


def test_invalid_eeg_type_raises(tmp_path: Path) -> None:
    with pytest.raises(
        ConfigError,
        match=r"tariffs\.eeg_type: unbekannter Wert 'national', erlaubt: local, regional",
    ):
        _load(tmp_path, _with(tariffs={"eeg_type": "national"}))


def test_too_many_households_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"höchstens 15 Haushalte erlaubt, gefunden 16"):
        _load(tmp_path, _with(households=_households(MAX_HOUSEHOLDS + 1)))


def test_max_households_is_accepted(tmp_path: Path) -> None:
    cfg = _load(tmp_path, _with(households=_households(MAX_HOUSEHOLDS)))
    assert len(cfg.households) == MAX_HOUSEHOLDS


def test_no_households_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="mindestens ein Haushalt"):
        _load(tmp_path, _with(households=[]))


# ---------------------------------------------------------------- further validation


@pytest.mark.parametrize(
    ("sections", "message"),
    [
        ({"pv": {"kwp": 0}}, r"pv\.kwp: muss größer als 0 sein"),
        ({"pv": {"kwp": "viel"}}, r"pv\.kwp: Zahl erwartet"),
        ({"pv": {"kwp": True}}, r"pv\.kwp: Zahl erwartet"),
        ({"pv": {"kwp": float("nan")}}, r"pv\.kwp: endliche Zahl erwartet"),
        ({"pv": {"tilt_deg": 95}}, r"pv\.tilt_deg: darf nicht größer als 90 sein"),
        ({"pv": {"kwpp": 3}}, r"pv: unbekannte Schlüssel kwpp"),
        ({"period": {"year": 2023.5}}, r"period\.year: ganze Zahl erwartet"),
        ({"location": {"lat": 91}}, r"location\.lat: darf nicht größer als 90 sein"),
        ({"tariffs": {"energy_price": -1}}, r"tariffs\.energy_price: darf nicht kleiner als 0"),
        ({"tariffs": {"grid_fee_reduction": 1.5}}, r"tariffs\.grid_fee_reduction: darf nicht"),
        ({"prices": {"source": "epex"}}, r"prices\.source: unbekannter Wert 'epex'"),
        ({"co2": {"source": "electricitymaps"}}, r"co2\.source: unbekannter Wert"),
        ({"optimization": {"objective": "max_fun"}}, r"optimization\.objective: unbekannter"),
        ({"optimization": {"solver": "gurobi"}}, r"optimization\.solver: unbekannter"),
        ({"allocation": {"strategy": "static"}}, r"allocation\.strategy: unbekannter"),
        ({"pv": 5}, r"pv: Zuordnung"),
        ({"pv": None}, r"pv: Pflichtwert fehlt"),
        ({"extra": 1}, r"Konfiguration: unbekannte Schlüssel extra"),
    ],
)
def test_invalid_values_raise_with_key_path(
    tmp_path: Path, sections: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        _load(tmp_path, _with(**sections))


@pytest.mark.parametrize(
    ("load", "message"),
    [
        ({"source": "htw"}, r"load\.profile: Pflichtwert fehlt"),
        ({"source": "htw", "profile": 75}, r"load\.profile: darf nicht größer als 74"),
        ({"source": "htw", "profile": 3, "path": "x.csv"}, r"unbekannte Schlüssel path"),
        ({"source": "smartmeter"}, r"load\.path: Pflichtwert fehlt"),
        ({"source": "synthetic", "annual_kwh": 0}, r"annual_kwh: muss größer als 0"),
        ({"source": "synthetic", "seed": -1}, r"seed: darf nicht kleiner als 0"),
    ],
)
def test_invalid_load_raises(tmp_path: Path, load: dict[str, Any], message: str) -> None:
    households = _households(1)
    households[0]["load"] = load
    with pytest.raises(ConfigError, match=message):
        _load(tmp_path, _with(households=households))


def test_empty_household_id_raises(tmp_path: Path) -> None:
    households = _households(1)
    households[0]["id"] = " "
    with pytest.raises(ConfigError, match=r"households\[0\]\.id: nicht-leerer Text"):
        _load(tmp_path, _with(households=households))


def test_all_load_sources_parse(tmp_path: Path) -> None:
    households: list[dict[str, Any]] = [
        {"id": "a", "load": {"source": "htw", "profile": 74}},
        {"id": "b", "load": {"source": "synthetic", "annual_kwh": 3500, "seed": 7}},
        {"id": "c", "load": {"source": "synthetic"}},
        {"id": "d", "load": {"source": "smartmeter", "path": "data/hh_d.csv"}},
    ]
    cfg = _load(tmp_path, _with(households=households))
    loads = [hh.load for hh in cfg.households]
    assert loads[0].profile == 74
    assert loads[1].annual_kwh == 3500.0
    assert loads[1].seed == 7
    assert loads[2].annual_kwh is None
    assert loads[2].seed == 0
    assert loads[3].path == "data/hh_d.csv"


def test_devices_are_kept_as_raw_mappings(tmp_path: Path) -> None:
    device = {
        "id": "ev",
        "kind": "modulating",
        "power_kw": 11.0,
        "energy_kwh_per_day": 8.0,
        "window": {"start": "18:00", "end": "07:00"},
        "baseline_start": "18:00",
    }
    households = _households(2)
    households[0]["devices"] = [device]
    cfg = _load(tmp_path, _with(households=households))
    assert cfg.households[0].devices == (device,)
    assert cfg.households[1].devices == ()


def test_devices_must_be_list_of_mappings(tmp_path: Path) -> None:
    households = _households(1)
    households[0]["devices"] = ["ev"]
    with pytest.raises(ConfigError, match=r"households\[0\]\.devices\[0\]: Zuordnung"):
        _load(tmp_path, _with(households=households))


# ---------------------------------------------------------------- grid_fee_reduction


@pytest.mark.parametrize(("eeg_type", "expected"), [("local", 0.57), ("regional", 0.28)])
def test_grid_fee_reduction_follows_eeg_type(
    tmp_path: Path, eeg_type: str, expected: float
) -> None:
    cfg = _load(tmp_path, _with(tariffs={"eeg_type": eeg_type}))
    assert cfg.tariffs.grid_fee_reduction is None
    assert cfg.tariffs.effective_grid_fee_reduction == expected


def test_explicit_grid_fee_reduction_wins(tmp_path: Path) -> None:
    cfg = _load(tmp_path, _with(tariffs={"eeg_type": "regional", "grid_fee_reduction": 0.4}))
    assert cfg.tariffs.effective_grid_fee_reduction == 0.4


def test_explicit_zero_grid_fee_reduction_is_kept(tmp_path: Path) -> None:
    cfg = _load(tmp_path, _with(tariffs={"grid_fee_reduction": 0}))
    assert cfg.tariffs.effective_grid_fee_reduction == 0.0


# ---------------------------------------------------------------- roundtrip


def test_roundtrip_save_load_is_identical(tmp_path: Path) -> None:
    households = _households(3)
    households[1]["load"] = {"source": "synthetic", "annual_kwh": 4200.0, "seed": 3}
    households[2]["load"] = {"source": "smartmeter", "path": "data/ü.csv"}
    households[0]["devices"] = [
        {"id": "wm", "kind": "program", "power_kw": 2.0, "duration_min": 90}
    ]
    original = _load(
        tmp_path,
        _with(
            name="Gemeinschaft Mühlviertel",
            households=households,
            tariffs={"eeg_type": "regional"},
            pv={"kwp": 17.3, "azimuth_deg": -45},
        ),
    )
    out = tmp_path / "saved.yaml"
    save_config(original, out)
    reloaded = load_config(out)
    assert reloaded == original
    # the saved file is complete: loading it over an empty defaults file gives the same
    empty_defaults = tmp_path / "empty_defaults.yaml"
    empty_defaults.write_text("", encoding="utf-8")
    assert load_config(out, defaults_path=empty_defaults) == original
    # and saving again is byte-identical
    out2 = tmp_path / "saved2.yaml"
    save_config(reloaded, out2)
    assert out2.read_text(encoding="utf-8") == out.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "name", ["a\x85b", "Zeile\nZwei", "tab\there", "x" + chr(0x2028) + "y", "Öko ☀"]
)
def test_roundtrip_keeps_line_breaks_and_controls_in_text(tmp_path: Path, name: str) -> None:
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    original = parse_config(merge_dicts(defaults, _with(name=name)))
    assert original.name == name
    out = tmp_path / "saved.yaml"
    save_config(original, out)
    assert load_config(out) == original


def test_roundtrip_keeps_derived_grid_fee_reduction_derived(tmp_path: Path) -> None:
    original = _load(tmp_path, _with())
    out = tmp_path / "saved.yaml"
    save_config(original, out)
    data = config_to_dict(load_config(out))
    data["tariffs"]["eeg_type"] = "regional"
    assert parse_config(data).tariffs.effective_grid_fee_reduction == 0.28
