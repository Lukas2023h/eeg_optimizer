"""E2E: configuration page: edit, save, validation errors, session state (F2-04)."""

import uuid
from collections.abc import Iterator

import pytest
import yaml
from playwright.sync_api import Page, expect

from eeg_optimizer.config_store import USER_CONFIG_DIR

pytestmark = pytest.mark.e2e

TIMEOUT_MS = 20_000


@pytest.fixture
def config_name() -> Iterator[str]:
    """Unique file name under data/configs/, removed after the test."""
    name = f"e2e_{uuid.uuid4().hex[:8]}"
    yield name
    (USER_CONFIG_DIR / f"{name}.yaml").unlink(missing_ok=True)


def _open(page: Page, app_url: str) -> None:
    page.goto(f"{app_url}/konfiguration")
    expect(page.get_by_role("heading", name="Konfiguration")).to_be_visible(timeout=TIMEOUT_MS)
    expect(page.get_by_label("PV-Leistung (kWp)")).to_be_visible(timeout=TIMEOUT_MS)


def _set_input(page: Page, label: str, value: str) -> None:
    field = page.get_by_label(label, exact=True)
    field.fill(value)
    field.press("Enter")


def test_change_kwp_and_save(page: Page, app_url: str, config_name: str) -> None:
    _open(page, app_url)
    _set_input(page, "PV-Leistung (kWp)", "20")
    _set_input(page, "Dateiname (ohne .yaml, wird unter data/configs/ gespeichert)", config_name)
    page.get_by_role("button", name="Speichern").click()
    expect(page.get_by_text(f"Gespeichert unter data/configs/{config_name}.yaml")).to_be_visible(
        timeout=TIMEOUT_MS
    )
    expect(page.locator("[data-testid='stException']")).to_have_count(0)

    saved = USER_CONFIG_DIR / f"{config_name}.yaml"
    text = saved.read_text(encoding="utf-8")
    assert "kwp: 20" in text
    assert yaml.safe_load(text)["pv"]["kwp"] == 20
    # the saved file is now the selected one
    expect(page.get_by_label("Konfigurationsdatei")).to_have_value(
        f"Gespeichert: {config_name}.yaml"
    )


def test_negative_kwp_shows_german_error(page: Page, app_url: str) -> None:
    _open(page, app_url)
    files_before = set(USER_CONFIG_DIR.glob("*.yaml")) if USER_CONFIG_DIR.is_dir() else set()
    _set_input(page, "PV-Leistung (kWp)", "-5")
    page.get_by_role("button", name="Speichern").click()
    expect(page.get_by_text("pv.kwp: muss größer als 0 sein")).to_be_visible(timeout=TIMEOUT_MS)
    expect(page.get_by_text("Die Konfiguration ist ungültig")).to_be_visible()
    expect(page.locator("[data-testid='stException']")).to_have_count(0)
    files_after = set(USER_CONFIG_DIR.glob("*.yaml")) if USER_CONFIG_DIR.is_dir() else set()
    assert files_after == files_before


def test_applied_config_survives_page_change(page: Page, app_url: str) -> None:
    _open(page, app_url)
    _set_input(page, "PV-Leistung (kWp)", "12.5")
    page.get_by_role("button", name="Übernehmen").click()
    expect(page.get_by_text("Übernommen.")).to_be_visible(timeout=TIMEOUT_MS)

    page.get_by_role("link", name="Systemstatus").click()
    expect(page.get_by_text("CBC verfügbar: ja")).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_role("link", name="Konfiguration").click()
    expect(page.get_by_text("12.5 kWp")).to_be_visible(timeout=TIMEOUT_MS)
    expect(page.get_by_label("PV-Leistung (kWp)")).to_have_value("12.50")


def test_household_count_adds_household(page: Page, app_url: str) -> None:
    _open(page, app_url)
    _set_input(page, "Anzahl Haushalte", "6")
    expect(page.get_by_label("ID Haushalt 6")).to_have_value("hh06", timeout=TIMEOUT_MS)
    page.get_by_role("button", name="Übernehmen").click()
    expect(page.get_by_text("6 Haushalte")).to_be_visible(timeout=TIMEOUT_MS)
    expect(page.locator("[data-testid='stException']")).to_have_count(0)
