"""E2E: page 'Optimierung' with the offline example configuration (F5-10)."""

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

TIMEOUT_MS = 30_000
RUN_TIMEOUT_MS = 180_000
"""A whole year with the MILP takes ~15-25 s here; generous for slower machines."""
OFFLINE_LABEL = re.compile("example_community_offline")

OBJECTIVES = (
    "Gemeinsam genutzte Energie maximieren",
    "Stromkosten minimieren",
    "CO₂-Emissionen minimieren",
)
SOLVERS = ("MILP (exakt je Tagesblock)", "Greedy (schnelle Heuristik)")


def _open_optimization_offline(page: Page, app_url: str) -> None:
    page.goto(f"{app_url}/konfiguration")
    select = page.get_by_label("Konfigurationsdatei")
    expect(select).to_be_visible(timeout=TIMEOUT_MS)
    select.click()
    page.get_by_role("option", name=OFFLINE_LABEL).click()
    expect(page.get_by_text("Beispielgemeinschaft Linz (offline)")).to_be_visible(
        timeout=TIMEOUT_MS
    )
    page.get_by_role("link", name="Optimierung").click()
    expect(page.get_by_role("heading", name="Optimierung")).to_be_visible(timeout=TIMEOUT_MS)


def _choose(page: Page, objective: str, solver: str) -> None:
    page.get_by_label("Ziel").click()
    page.get_by_role("option", name=objective).click()
    page.get_by_text(solver, exact=True).click()


def _run_and_wait(page: Page) -> None:
    page.get_by_role("button", name="Optimieren").click()
    expect(page.get_by_text(re.compile(r"^Rückfall-Blöcke: \d+ von \d+"))).to_be_visible(
        timeout=RUN_TIMEOUT_MS
    )


def _metric_value(page: Page, label: str) -> str:
    metric = page.locator("[data-testid='stMetric']").filter(has_text=label)
    expect(metric).to_have_count(1, timeout=TIMEOUT_MS)
    return metric.locator("[data-testid='stMetricValue']").inner_text()


def test_max_shared_shows_runtime_no_fallback_and_notes(page: Page, app_url: str) -> None:
    _open_optimization_offline(page, app_url)
    _choose(page, OBJECTIVES[0], SOLVERS[0])
    page.get_by_role("button", name="Optimieren").click()
    # the MILP year takes several seconds: the progress bar counts the day blocks meanwhile
    expect(page.get_by_text(re.compile(r"Tagesblock \d+ von 365"))).to_be_visible(
        timeout=TIMEOUT_MS
    )
    expect(page.get_by_text(re.compile(r"^Rückfall-Blöcke: \d+ von \d+"))).to_be_visible(
        timeout=RUN_TIMEOUT_MS
    )

    expect(page.get_by_text(re.compile(r"^Rückfall-Blöcke: 0 von 365"))).to_be_visible()
    assert re.fullmatch(r"[\d.]+,\d s", _metric_value(page, "Laufzeit"))
    assert re.fullmatch(r"[\d.]+ kWh", _metric_value(page, "Gemeinsam genutzt (optimiert)"))
    assert re.fullmatch(r"[\d.]+ kWh", _metric_value(page, "Gemeinsam genutzt (Ist-Zustand)"))

    notes = page.locator(".st-key-optimization_notes")
    expect(notes.get_by_text("Obergrenze bei perfekter Voraussicht", exact=False)).to_be_visible()
    expect(notes.get_by_text("Pro Tagesblock optimal, nicht global", exact=False)).to_be_visible()
    expect(page.locator("[data-testid='stException']")).to_have_count(0)


@pytest.mark.parametrize("solver", SOLVERS)
@pytest.mark.parametrize("objective", OBJECTIVES)
def test_every_objective_and_solver_runs_without_exception(
    page: Page, app_url: str, objective: str, solver: str
) -> None:
    _open_optimization_offline(page, app_url)
    _choose(page, objective, solver)
    _run_and_wait(page)

    expect(page.get_by_text(f"Ziel: {objective} · Verfahren: {solver}")).to_be_visible()
    expect(page.get_by_text(re.compile(r"^Rückfall-Blöcke: 0 von 365"))).to_be_visible()
    expect(page.locator(".st-key-optimization_notes")).to_be_visible()
    expect(page.locator("[data-testid='stException']")).to_have_count(0)
    expect(page.locator("[data-testid='stAlert']").filter(has_text="fehlgeschlagen")).to_have_count(
        0
    )
