"""E2E: page 'Ist-Analyse' with the offline example configuration (F3-06)."""

import re
import time

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

TIMEOUT_MS = 30_000
OFFLINE_LABEL = re.compile("example_community_offline")


def _select_offline_config(page: Page, app_url: str) -> None:
    page.goto(f"{app_url}/konfiguration")
    select = page.get_by_label("Konfigurationsdatei")
    expect(select).to_be_visible(timeout=TIMEOUT_MS)
    select.click()
    page.get_by_role("option", name=OFFLINE_LABEL).click()
    expect(page.get_by_text("Beispielgemeinschaft Linz (offline)")).to_be_visible(
        timeout=TIMEOUT_MS
    )


def _open_analysis(page: Page) -> None:
    page.get_by_role("link", name="Ist-Analyse").click()
    expect(page.get_by_role("heading", name="Ist-Analyse")).to_be_visible(timeout=TIMEOUT_MS)


def _metric_value(page: Page, label: str) -> str:
    metric = page.locator("[data-testid='stMetric']").filter(has_text=label)
    expect(metric).to_have_count(1, timeout=TIMEOUT_MS)
    return metric.locator("[data-testid='stMetricValue']").inner_text()


def test_kpis_charts_and_notes(page: Page, app_url: str) -> None:
    _select_offline_config(page, app_url)
    _open_analysis(page)

    expect(page.get_by_text("Beispielgemeinschaft Linz (offline)")).to_be_visible(
        timeout=TIMEOUT_MS
    )
    assert re.fullmatch(r"\d+,\d %", _metric_value(page, "Eigenverbrauchsquote"))
    assert re.fullmatch(r"\d+,\d %", _metric_value(page, "Autarkiegrad"))
    assert re.fullmatch(r"[\d.]+ kWh", _metric_value(page, "Einspeisung"))
    assert re.fullmatch(r"[\d.]+ kWh", _metric_value(page, "Netzbezug"))

    charts = page.locator("[data-testid='stPlotlyChart']")
    expect(charts).to_have_count(3, timeout=TIMEOUT_MS)
    for i in range(3):
        expect(charts.nth(i).locator(".main-svg").first).to_be_visible(timeout=TIMEOUT_MS)

    expect(page.get_by_text("Annahmen und Vereinfachungen")).to_be_visible()
    expect(page.get_by_text("HTW-Berlin-Standardprofile", exact=False)).to_be_visible()
    expect(page.get_by_text("ohne Importe", exact=False)).to_be_visible()
    expect(page.get_by_text("perfekter Voraussicht", exact=False)).to_be_visible()
    expect(page.locator("[data-testid='stException']")).to_have_count(0)


def test_cached_page_build_under_10_s(page: Page, app_url: str) -> None:
    _select_offline_config(page, app_url)
    _open_analysis(page)
    expect(page.locator("[data-testid='stPlotlyChart']")).to_have_count(3, timeout=TIMEOUT_MS)

    # second visit hits st.cache_data: page with all KPIs and charts within 10 s
    page.get_by_role("link", name="Konfiguration").click()
    expect(page.get_by_role("heading", name="Konfiguration")).to_be_visible(timeout=TIMEOUT_MS)
    start = time.perf_counter()
    _open_analysis(page)
    expect(page.locator("[data-testid='stPlotlyChart']")).to_have_count(3, timeout=10_000)
    _metric_value(page, "Netzbezug")
    assert time.perf_counter() - start < 10.0
    expect(page.locator("[data-testid='stException']")).to_have_count(0)
