"""Headless Streamlit AppTest of the configuration page (F2-04): session state."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Button, NumberInput

from eeg_optimizer.config import CommunityConfig
from eeg_optimizer.config_store import (
    DEFAULT_CONFIG_PATH,
    SESSION_CONFIG_KEY,
    SESSION_PATH_KEY,
)

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


@pytest.mark.parametrize("count", [1, 7])
def test_household_count_changes_households(count: int) -> None:
    at = _run()
    _number(at, "Anzahl Haushalte").set_value(count)
    at.run()
    _button(at, "Übernehmen").click()
    at.run()
    assert not at.exception
    assert len(at.session_state[SESSION_CONFIG_KEY].households) == count
