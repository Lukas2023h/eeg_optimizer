"""E2E: edit flexible devices per household on the configuration page (F4-04)."""

import uuid
from collections.abc import Iterator

import pytest
import yaml
from playwright.sync_api import Page, expect

from eeg_optimizer.config_store import USER_CONFIG_DIR

pytestmark = pytest.mark.e2e

TIMEOUT_MS = 20_000
FILENAME_LABEL = "Dateiname (ohne .yaml, wird unter data/configs/ gespeichert)"


@pytest.fixture
def config_name() -> Iterator[str]:
    """Unique file name under data/configs/, removed after the test."""
    name = f"e2e_dev_{uuid.uuid4().hex[:8]}"
    yield name
    (USER_CONFIG_DIR / f"{name}.yaml").unlink(missing_ok=True)


def _open(page: Page, app_url: str) -> None:
    page.goto(f"{app_url}/konfiguration")
    expect(page.get_by_role("heading", name="Konfiguration")).to_be_visible(timeout=TIMEOUT_MS)
    expect(page.get_by_label("PV-Leistung (kWp)")).to_be_visible(timeout=TIMEOUT_MS)


def _expand_devices(page: Page, household: int) -> None:
    page.locator("summary", has_text=f"Geräte Haushalt {household}").click()
    expect(page.get_by_label(f"ID Gerät 1 Haushalt {household}")).to_be_visible(timeout=TIMEOUT_MS)


def _no_exception(page: Page) -> None:
    expect(page.locator("[data-testid='stException']")).to_have_count(0)


def test_add_washing_machine_save_and_reload(page: Page, app_url: str, config_name: str) -> None:
    _open(page, app_url)
    _expand_devices(page, 1)
    # household 1 already has ev + washing_machine; the preset default is the washing machine
    expect(page.get_by_label("Neues Gerät Haushalt 1")).to_have_value("Waschmaschine")
    page.get_by_role("button", name="Gerät hinzufügen (Haushalt 1)").click()
    new_id = page.get_by_label("ID Gerät 3 Haushalt 1")
    expect(new_id).to_have_value("washing_machine_2", timeout=TIMEOUT_MS)
    expect(page.get_by_label("Dauer Gerät 3 Haushalt 1 (Minuten)")).to_have_value("60")

    filename = page.get_by_label(FILENAME_LABEL, exact=True)
    filename.fill(config_name)
    filename.press("Enter")
    page.get_by_role("button", name="Speichern").click()
    expect(page.get_by_text(f"Gespeichert unter data/configs/{config_name}.yaml")).to_be_visible(
        timeout=TIMEOUT_MS
    )
    _no_exception(page)

    saved = yaml.safe_load((USER_CONFIG_DIR / f"{config_name}.yaml").read_text(encoding="utf-8"))
    devices = saved["households"][0]["devices"]
    assert [d["id"] for d in devices] == ["ev", "washing_machine", "washing_machine_2"]
    assert devices[2]["kind"] == "program"
    assert devices[2]["window"] == {"start": "08:00", "end": "22:00"}

    # reload: a fresh session starts with the standard scenario, then load the saved file
    page.reload()
    _open(page, app_url)
    select = page.get_by_label("Konfigurationsdatei")
    select.click()
    page.get_by_role("option", name=f"Gespeichert: {config_name}.yaml").click()
    expect(select).to_have_value(f"Gespeichert: {config_name}.yaml", timeout=TIMEOUT_MS)
    _expand_devices(page, 1)
    expect(page.get_by_label("ID Gerät 3 Haushalt 1")).to_have_value(
        "washing_machine_2", timeout=TIMEOUT_MS
    )
    expect(page.get_by_label("Baseline-Start Gerät 3 Haushalt 1")).to_have_value("19:00")
    _no_exception(page)


def test_unreachable_energy_shows_german_error(page: Page, app_url: str) -> None:
    _open(page, app_url)
    files_before = set(USER_CONFIG_DIR.glob("*.yaml")) if USER_CONFIG_DIR.is_dir() else set()
    _expand_devices(page, 1)
    energy = page.get_by_label("Energie pro Tag Gerät 1 Haushalt 1 (kWh)")
    energy.fill("500")
    energy.press("Enter")
    page.get_by_role("button", name="Speichern").click()
    expect(page.get_by_text("Die Konfiguration ist ungültig")).to_be_visible(timeout=TIMEOUT_MS)
    expect(
        page.get_by_text(
            "households[0].devices[0].energy_kwh_per_day: 500 kWh sind im Fenster "
            "18:00 bis 07:00 mit 11 kW nicht erreichbar"
        )
    ).to_be_visible()
    _no_exception(page)
    files_after = set(USER_CONFIG_DIR.glob("*.yaml")) if USER_CONFIG_DIR.is_dir() else set()
    assert files_after == files_before


def test_remove_device(page: Page, app_url: str) -> None:
    _open(page, app_url)
    _expand_devices(page, 1)
    page.get_by_role("button", name="Gerät 1 entfernen (Haushalt 1)").click()
    # during the rerun Streamlit briefly shows the old elements as well; wait until
    # the run has finished (count assertions retry, strict value lookups do not)
    expect(page.get_by_label("ID Gerät 2 Haushalt 1")).to_have_count(0, timeout=TIMEOUT_MS)
    first_id = page.get_by_label("ID Gerät 1 Haushalt 1")
    expect(first_id).to_have_count(1, timeout=TIMEOUT_MS)
    expect(first_id).to_have_value("washing_machine")
    page.get_by_role("button", name="Übernehmen").click()
    expect(page.get_by_text("Übernommen.")).to_be_visible(timeout=TIMEOUT_MS)
    _no_exception(page)
