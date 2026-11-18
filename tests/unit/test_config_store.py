"""Unit tests for config_store (F2-04): listing, editing and saving configurations."""

from pathlib import Path

import pytest

from eeg_optimizer.config import ConfigError, config_to_dict, load_config
from eeg_optimizer.config_store import (
    BUILTIN_CONFIG_DIR,
    DEFAULT_CONFIG_PATH,
    apply_edits,
    available_configs,
    resize_households,
    save_user_config,
    user_config_path,
)

OFFLINE = BUILTIN_CONFIG_DIR / "example_community_offline.yaml"


def test_available_configs_lists_examples_without_defaults(tmp_path: Path) -> None:
    files = available_configs(user_dir=tmp_path / "missing")
    names = [f.path.name for f in files]
    assert "defaults.yaml" not in names
    assert "example_community.yaml" in names
    assert "example_community_offline.yaml" in names
    assert all(f.label.startswith("Beispiel: ") for f in files)


def test_available_configs_appends_user_files_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "a.yaml").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    files = available_configs(user_dir=tmp_path)
    user = [f for f in files if f.label.startswith("Gespeichert: ")]
    assert [f.path.name for f in user] == ["a.yaml", "b.yaml"]
    assert files[-2:] == user


def test_default_config_path_is_standard_scenario() -> None:
    assert DEFAULT_CONFIG_PATH.name == "example_community.yaml"
    assert len(load_config(DEFAULT_CONFIG_PATH).households) == 5


def test_apply_edits_changes_only_given_values() -> None:
    base = load_config(OFFLINE)
    edited = apply_edits(base, {"pv": {"kwp": 20.0}, "tariffs": {"eeg_type": "regional"}})
    assert edited.pv.kwp == 20.0
    assert edited.pv.tilt_deg == base.pv.tilt_deg
    assert edited.tariffs.eeg_type == "regional"
    assert edited.tariffs.effective_grid_fee_reduction == 0.28
    assert edited.households == base.households


def test_apply_edits_rejects_negative_kwp_with_german_message() -> None:
    base = load_config(OFFLINE)
    with pytest.raises(ConfigError, match=r"pv\.kwp: muss größer als 0 sein"):
        apply_edits(base, {"pv": {"kwp": -5.0}})


def test_apply_edits_rejects_stale_load_keys() -> None:
    base = load_config(OFFLINE)
    households = config_to_dict(base)["households"]
    households[0]["load"]["source"] = "htw"  # annual_kwh left over -> unknown key
    with pytest.raises(ConfigError, match=r"households\[0\]\.load"):
        apply_edits(base, {"households": households})


def test_apply_edits_switches_load_source() -> None:
    base = load_config(OFFLINE)
    households = config_to_dict(base)["households"]
    households[0]["load"] = {"source": "htw", "profile": 12, "seed": 1}
    edited = apply_edits(base, {"households": households})
    assert edited.households[0].load.source == "htw"
    assert edited.households[0].load.profile == 12


def test_resize_households_grows_with_unique_ids_and_seeds() -> None:
    households = config_to_dict(load_config(OFFLINE))["households"]
    grown = resize_households(households, 7)
    assert [hh["id"] for hh in grown] == [f"hh{i:02d}" for i in range(1, 8)]
    seeds = [hh["load"]["seed"] for hh in grown]
    assert len(set(seeds)) == len(seeds)
    assert grown[5]["load"]["source"] == households[-1]["load"]["source"]
    assert "devices" not in grown[5]
    assert grown[:5] == households


def test_resize_households_does_not_mutate_input() -> None:
    households = config_to_dict(load_config(OFFLINE))["households"]
    before = [dict(hh, load=dict(hh["load"])) for hh in households]
    grown = resize_households(households, 6)
    grown[0]["load"]["seed"] = 99
    assert households == before


def test_resize_households_skips_used_ids() -> None:
    households = [{"id": "hh01", "load": {"source": "htw", "profile": 1, "seed": 0}}]
    households.append({"id": "hh02", "load": {"source": "htw", "profile": 2, "seed": 0}})
    grown = resize_households([households[1]], 3)
    assert [hh["id"] for hh in grown] == ["hh02", "hh01", "hh03"]


def test_resize_households_shrinks_and_parses() -> None:
    base = load_config(OFFLINE)
    shrunk = resize_households(config_to_dict(base)["households"], 2)
    edited = apply_edits(base, {"households": shrunk})
    assert [hh.id for hh in edited.households] == ["hh01", "hh02"]


@pytest.mark.parametrize("count", [0, 16])
def test_resize_households_rejects_out_of_range(count: int) -> None:
    households = config_to_dict(load_config(OFFLINE))["households"]
    with pytest.raises(ConfigError, match="zwischen 1 und 15"):
        resize_households(households, count)


def test_save_user_config_roundtrip(tmp_path: Path) -> None:
    edited = apply_edits(load_config(OFFLINE), {"pv": {"kwp": 20.0}})
    path = save_user_config(edited, "test_gemeinschaft", user_dir=tmp_path / "configs")
    assert path == tmp_path / "configs" / "test_gemeinschaft.yaml"
    assert "kwp: 20" in path.read_text(encoding="utf-8")
    assert load_config(path) == edited


def test_user_config_path_strips_yaml_suffix(tmp_path: Path) -> None:
    assert user_config_path("abc.yaml", tmp_path) == tmp_path / "abc.yaml"
    assert user_config_path("  a-b_1 ", tmp_path) == tmp_path / "a-b_1.yaml"


@pytest.mark.parametrize("name", ["", "   ", "../evil", "a/b", ".hidden", "ä", "x" * 65])
def test_user_config_path_rejects_unsafe_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ConfigError, match="Dateiname"):
        user_config_path(name, tmp_path)
