"""E2E: page 'Export' delivers the CSV files of the last optimization (F7-01)."""

import re

import pandas as pd
import pytest
from playwright.sync_api import Page, expect

from eeg_optimizer.export import KPI_FILE, TIMESERIES_COLUMNS, TIMESERIES_FILE, TIMESTAMP_COLUMN
from tests.e2e.test_comparison_page import HOUSEHOLDS
from tests.e2e.test_optimization_page import (
    OBJECTIVES,
    SOLVERS,
    TIMEOUT_MS,
    _choose,
    _open_optimization_offline,
    _run_and_wait,
)

pytestmark = pytest.mark.e2e

INTERVALS_2023 = 365 * 96
ISO_WITH_OFFSET = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")


def _open_export_after_run(page: Page, app_url: str) -> None:
    _open_optimization_offline(page, app_url)
    _choose(page, OBJECTIVES[0], SOLVERS[1])
    _run_and_wait(page)
    # in-app navigation keeps the session (and with it the run): Optimierung -> Vergleich -> Export
    page.get_by_role("link", name="Zum Vergleich vorher/nachher").click()
    page.get_by_role("link", name="Zum CSV-Export").click(timeout=TIMEOUT_MS)
    expect(page.get_by_role("heading", name="Export", exact=True)).to_be_visible(timeout=TIMEOUT_MS)


def _no_exception(page: Page) -> None:
    expect(page.locator("[data-testid='stException']")).to_have_count(0)
    expect(page.locator("[data-testid='stAlert']").filter(has_text="nicht erstellt")).to_have_count(
        0
    )


def test_download_buttons_deliver_readable_csv_files(page: Page, app_url: str) -> None:
    _open_export_after_run(page, app_url)

    button = page.get_by_role("button", name="Zeitreihen herunterladen (CSV)")
    expect(button).to_be_visible(timeout=TIMEOUT_MS)
    with page.expect_download(timeout=TIMEOUT_MS) as info:
        button.click()
    download = info.value
    assert download.suggested_filename == TIMESERIES_FILE
    series = pd.read_csv(download.path())
    assert list(series.columns) == [TIMESTAMP_COLUMN, *TIMESERIES_COLUMNS]
    assert len(series) == INTERVALS_2023
    assert all(ISO_WITH_OFFSET.fullmatch(stamp) for stamp in series[TIMESTAMP_COLUMN])
    assert series[TIMESTAMP_COLUMN].iloc[0] == "2023-01-01T00:00:00+01:00"
    assert series["shared_optimized_kwh"].sum() > series["shared_baseline_kwh"].sum()

    with page.expect_download(timeout=TIMEOUT_MS) as info:
        page.get_by_role("button", name="Kennzahlen herunterladen (CSV)").click()
    download = info.value
    assert download.suggested_filename == KPI_FILE
    kpis = pd.read_csv(download.path(), index_col="household_id")
    assert list(kpis.index) == [*HOUSEHOLDS, "total"]
    assert "additional_shared_kwh" in kpis.columns
    _no_exception(page)


def test_without_a_run_the_page_points_to_the_optimization(page: Page, app_url: str) -> None:
    page.goto(f"{app_url}/export")

    expect(page.get_by_text("Noch keine Optimierung gerechnet", exact=False)).to_be_visible(
        timeout=TIMEOUT_MS
    )
    expect(page.get_by_role("link", name="Zur Optimierung")).to_be_visible()
    expect(page.get_by_role("button", name="Zeitreihen herunterladen (CSV)")).to_have_count(0)
    _no_exception(page)
