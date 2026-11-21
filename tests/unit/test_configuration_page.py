"""Headless Streamlit AppTest of the configuration page (F2-04): session state."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Button, NumberInput

from eeg_optimizer.config import CommunityConfig, config_to_dict
from eeg_optimizer.config_store import (
    DEFAULT_CONFIG_PATH,
    SESSION_CONFIG_KEY,
    SESSION_PATH_KEY,
    apply_edits,
)

VERSION_KEY = "config_form_version"  # app/pages/configuration.py (not importable: page script)
PAGE = str(Path(__file__).resolve().parents[2] / "app" / "pages" / "configuration.py")


def _run() -> AppTest:
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _number(at: AppTest, label: str) -> NumberInput:
    return next(w for w in at.number_input if w.label == label)


def _button(at: AppTest, label: str) -> Button:
    return next(w for w in at.button if w.label == label)


def test_default_config_in_session_state() -> None:
    at = _run()
    cfg = at.session_state[SESSION_CONFIG_KEY]
    assert isinstance(cfg, CommunityConfig)
    assert at.session_state[SESSION_PATH_KEY] == DEFAULT_CONFIG_PATH
    assert len(cfg.households) == 5


def test_apply_updates_session_state() -> None:
    at = _run()
    _number(at, "PV-Leistung (kWp)").set_value(20.0)
    _button(at, "Übernehmen").click()
    at.run()
    assert not at.exception
    assert at.session_state[SESSION_CONFIG_KEY].pv.kwp == 20.0
    assert not at.error


def test_invalid_value_keeps_last_valid_config() -> None:
    at = _run()
    _number(at, "PV-Leistung (kWp)").set_value(-5.0)
    _button(at, "Übernehmen").click()
    at.run()
    assert not at.exception
    assert any("pv.kwp: muss größer als 0 sein" in e.value for e in at.error)
    assert at.session_state[SESSION_CONFIG_KEY].pv.kwp == 30.0


def test_selecting_offline_example_loads_it() -> None:
    at = _run()
    select = next(w for w in at.selectbox if w.label == "Konfigurationsdatei")
    select.select("Beispiel: example_community_offline.yaml")
    at.run()
    assert not at.exception
    cfg = at.session_state[SESSION_CONFIG_KEY]
    assert cfg.pv.source == "synthetic"
    assert at.session_state[SESSION_PATH_KEY].name == "example_community_offline.yaml"
    # form widgets show the newly loaded values
    assert _number(at, "Jahresverbrauch Haushalt 1 (kWh)").value == 3200.0


def _device_ids(at: AppTest, household: int) -> list[str]:
    return [
        str(w.value)
        for w in at.text_input
        if w.label.startswith("ID Gerät") and w.label.endswith(f"Haushalt {household}")
    ]


def test_add_device_keeps_other_edits_and_applies() -> None:
    at = _run()
    assert _device_ids(at, 1) == ["ev", "washing_machine"]
    _number(at, "PV-Leistung (kWp)").set_value(25.0)
    next(w for w in at.selectbox if w.label == "Neues Gerät Haushalt 1").select("dishwasher")
    _button(at, "Gerät hinzufügen (Haushalt 1)").click()
    at.run()
    assert not at.exception
    assert _device_ids(at, 1) == ["ev", "washing_machine", "dishwasher"]
    assert _number(at, "PV-Leistung (kWp)").value == 25.0
    _button(at, "Übernehmen").click()
    at.run()
    assert not at.exception
    cfg = at.session_state[SESSION_CONFIG_KEY]
    assert [d["id"] for d in cfg.households[0].devices] == ["ev", "washing_machine", "dishwasher"]
    assert cfg.pv.kwp == 25.0


def test_remove_device_keeps_the_others() -> None:
    at = _run()
    _button(at, "Gerät 1 entfernen (Haushalt 1)").click()
    at.run()
    assert not at.exception
    assert _device_ids(at, 1) == ["washing_machine"]
    _button(at, "Übernehmen").click()
    at.run()
    cfg = at.session_state[SESSION_CONFIG_KEY]
    assert [d["id"] for d in cfg.households[0].devices] == ["washing_machine"]


def test_edit_device_values() -> None:
    at = _run()
    _number(at, "Energie pro Tag Gerät 1 Haushalt 1 (kWh)").set_value(12.0)
    next(w for w in at.selectbox if w.label == "Baseline-Start Gerät 1 Haushalt 1").select("19:00")
    _button(at, "Übernehmen").click()
    at.run()
    assert not at.exception
    ev = at.session_state[SESSION_CONFIG_KEY].households[0].devices[0]
    assert ev["energy_kwh_per_day"] == 12.0
    assert ev["baseline_start"] == "19:00"


def test_switch_kind_to_program_shows_duration() -> None:
    at = _run()
    next(w for w in at.selectbox if w.label == "Art Gerät 1 Haushalt 1").select("program")
    at.run()
    assert not at.exception
    assert _number(at, "Dauer Gerät 1 Haushalt 1 (Minuten)").value == 60
    _button(at, "Übernehmen").click()
    at.run()
    ev = at.session_state[SESSION_CONFIG_KEY].households[0].devices[0]
    assert ev["kind"] == "program"
    assert "energy_kwh_per_day" not in ev


def test_shrinking_households_drops_device_drafts() -> None:
    at = _run()
    _button(at, "Gerät 1 entfernen (Haushalt 5)").click()
    at.run()
    _number(at, "Anzahl Haushalte").set_value(4)
    at.run()
    _number(at, "Anzahl Haushalte").set_value(5)
    at.run()
    assert not at.exception
    # household 5 comes back from the active configuration, not from the old draft
    assert _device_ids(at, 5)[0] == "ev"


def test_hidden_device_options_survive_editing() -> None:
    at = _run()
    cfg = at.session_state[SESSION_CONFIG_KEY]
    households = config_to_dict(cfg)["households"]
    households[0]["devices"][0].update(min_run_min=60, min_power_kw=4.0)
    at.session_state[SESSION_CONFIG_KEY] = apply_edits(cfg, {"households": households})
    # new widget generation, as after loading a file
    at.session_state[VERSION_KEY] = at.session_state[VERSION_KEY] + 1
    at.run()
    _number(at, "Energie pro Tag Gerät 1 Haushalt 1 (kWh)").set_value(10.0)
    _button(at, "Übernehmen").click()
    at.run()
    assert not at.exception
    saved = at.session_state[SESSION_CONFIG_KEY].households[0].devices[0]
    assert saved["energy_kwh_per_day"] == 10.0
    assert saved["min_run_min"] == 60
    assert saved["min_power_kw"] == 4.0


def test_unreachable_energy_shows_german_error() -> None:
    at = _run()
    _number(at, "Energie pro Tag Gerät 1 Haushalt 1 (kWh)").set_value(500.0)
    _button(at, "Übernehmen").click()
    at.run()
    assert not at.exception
    assert any(
        "households[0].devices[0].energy_kwh_per_day" in e.value and "nicht erreichbar" in e.value
        for e in at.error
    )
    ev = at.session_state[SESSION_CONFIG_KEY].households[0].devices[0]
    assert ev["energy_kwh_per_day"] == 8.0


@pytest.mark.parametrize("count", [1, 7])
def test_household_count_changes_households(count: int) -> None:
    at = _run()
    _number(at, "Anzahl Haushalte").set_value(count)
    at.run()
    _button(at, "Übernehmen").click()
    at.run()
    assert not at.exception
    assert len(at.session_state[SESSION_CONFIG_KEY].households) == count
