"""E2E: page 'Vergleich' after an optimization with the offline example (F6-02)."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_optimization_page import (
    OBJECTIVES,
    SOLVERS,
    TIMEOUT_MS,
    _choose,
    _metric_value,
    _open_optimization_offline,
    _run_and_wait,
)

pytestmark = pytest.mark.e2e

HOUSEHOLDS = ("hh01", "hh02", "hh03", "hh04", "hh05")
NUMBER = r"-?[\d.]+(,\d+)?"
"""German number: thousands '.', decimal ','."""


def _open_comparison_after_run(page: Page, app_url: str, objective: str = OBJECTIVES[0]) -> None:
    _open_optimization_offline(page, app_url)
    _choose(page, objective, SOLVERS[0])
    _run_and_wait(page)
    # in-app navigation keeps the session (and with it the run)
    page.get_by_role("link", name="Zum Vergleich vorher/nachher").click()
    expect(page.get_by_role("heading", name="Vergleich", exact=True)).to_be_visible(
        timeout=TIMEOUT_MS
    )
    expect(page.get_by_text(re.compile("kWh gemeinsam genutzt ·"))).to_be_visible(
        timeout=TIMEOUT_MS
    )


def _no_exception(page: Page) -> None:
    expect(page.locator("[data-testid='stException']")).to_have_count(0)
    expect(
        page.locator("[data-testid='stAlert']").filter(has_text="nicht berechnet")
    ).to_have_count(0)


def test_headline_shows_kwh_euro_and_co2_with_numbers(page: Page, app_url: str) -> None:
    _open_comparison_after_run(page, app_url)

    headline = page.get_by_text(re.compile("gemeinsam genutzt · .* gespart · .* vermieden"))
    text = headline.inner_text()
    match = re.fullmatch(
        rf"\+({NUMBER}) kWh gemeinsam genutzt · ({NUMBER}) € gespart · ({NUMBER}) kg CO₂ vermieden",
        text,
    )
    assert match, text
    # the MILP shifts devices into the PV surplus: clearly more shared energy than before
    assert float(match.group(1).replace(".", "").replace(",", ".")) > 100.0

    assert re.fullmatch(rf"\+{NUMBER} kWh", _metric_value(page, "Zusätzlich gemeinsam genutzt"))
    assert re.fullmatch(rf"{NUMBER} €", _metric_value(page, "Gespart (Gemeinschaft)"))
    assert re.fullmatch(rf"{NUMBER} kg CO₂", _metric_value(page, "CO₂ vermieden"))
    expect(page.get_by_text(re.compile(r"Kosten mit fixem Tarif"))).to_be_visible()
    _no_exception(page)


def test_table_has_one_row_per_household_plus_total(page: Page, app_url: str) -> None:
    _open_comparison_after_run(page, app_url)

    table = page.locator(".st-key-comparison_table table")
    expect(table).to_be_visible(timeout=TIMEOUT_MS)
    rows = table.locator("tbody tr")
    expect(rows).to_have_count(len(HOUSEHOLDS) + 1)
    names = [rows.nth(i).locator("th").inner_text().strip() for i in range(rows.count())]
    assert names == [*HOUSEHOLDS, "Summe"]
    headers = table.locator("thead th").all_inner_texts()
    for header in ("Mehr gemeinsam genutzt (kWh)", "Gespart (€)", "CO₂ vermieden (kg)"):
        assert header in headers, headers
    total_cells = rows.nth(len(HOUSEHOLDS)).locator("td").all_inner_texts()
    assert all(re.fullmatch(NUMBER, cell.strip()) for cell in total_cells), total_cells
    _no_exception(page)


def test_day_chart_before_and_after_for_a_chosen_day_and_upper_bound_note(
    page: Page, app_url: str
) -> None:
    _open_comparison_after_run(page, app_url)

    chart = page.locator(".st-key-comparison_day_chart")
    expect(chart).to_be_visible(timeout=TIMEOUT_MS)
    expect(chart.get_by_text("Verbrauch vorher")).to_be_visible()
    expect(chart.get_by_text("Verbrauch nachher")).to_be_visible()
    expect(
        page.get_by_text(re.compile(r"Vorgewählt ist der Tag mit dem größten Gewinn"))
    ).to_be_visible()

    page.get_by_label("Tag", exact=True).click()
    page.keyboard.type("15.06.2023")
    page.get_by_role("option", name="Do 15.06.2023").click()
    expect(page.get_by_text(re.compile(r"Am 15\.06\.2023: [+-]?[\d.,]+ kWh"))).to_be_visible(
        timeout=TIMEOUT_MS
    )
    expect(chart.get_by_text("Verbrauch nachher")).to_be_visible()

    notes = page.locator(".st-key-comparison_notes")
    expect(notes.get_by_text("Obergrenze bei perfekter Voraussicht", exact=False)).to_be_visible()
    expect(notes.get_by_text("Pro Tagesblock optimal, nicht global", exact=False)).to_be_visible()
    _no_exception(page)


def test_min_cost_compares_with_the_spot_tariff(page: Page, app_url: str) -> None:
    _open_comparison_after_run(page, app_url, OBJECTIVES[1])

    expect(page.get_by_text(re.compile(r"Kosten mit Spot-Tarif"))).to_be_visible()
    assert re.fullmatch(rf"{NUMBER} €", _metric_value(page, "Gespart (Gemeinschaft)"))
    _no_exception(page)


def test_without_a_run_the_page_points_to_the_optimization(page: Page, app_url: str) -> None:
    page.goto(f"{app_url}/vergleich")

    expect(page.get_by_text("Noch keine Optimierung gerechnet", exact=False)).to_be_visible(
        timeout=TIMEOUT_MS
    )
    expect(page.get_by_role("link", name="Zur Optimierung")).to_be_visible()
    _no_exception(page)
