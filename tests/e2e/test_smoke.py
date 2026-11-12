"""Smoke test: the app renders its title without a Python exception."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_app_renders_title(page: Page, app_url: str) -> None:
    page.goto(app_url)
    expect(page.get_by_role("heading", name="EEG-Optimizer")).to_be_visible(timeout=20_000)
    expect(page.locator("[data-testid='stException']")).to_have_count(0)
