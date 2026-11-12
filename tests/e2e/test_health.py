"""E2E: the system status page is reachable via navigation and reports CBC (H-01)."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_status_page_via_navigation(page: Page, app_url: str) -> None:
    page.goto(app_url)
    page.get_by_role("link", name="Systemstatus").click()
    expect(page.get_by_role("heading", name="Systemstatus")).to_be_visible(timeout=20_000)
    expect(page.get_by_text("CBC verfügbar: ja")).to_be_visible()
    expect(page.locator("[data-testid='stException']")).to_have_count(0)


def test_status_page_direct_url(page: Page, app_url: str) -> None:
    page.goto(f"{app_url}/systemstatus")
    expect(page.get_by_text("CBC verfügbar: ja")).to_be_visible(timeout=20_000)
    expect(page.locator("[data-testid='stException']")).to_have_count(0)
