"""E2E fixtures: talk to the Streamlit app that init.sh / scripts/serve.sh started.

If no app is reachable, one is started for the test session and stopped afterwards.
"""

import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("EEG_PORT", "8501"))
BASE_URL = f"http://localhost:{PORT}"


def _healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/_stcore/health", timeout=2) as r:
            return bool(r.status == 200)
    except OSError:
        return False


@pytest.fixture(scope="session")
def app_url() -> Iterator[str]:
    """Base URL of a running app; starts one if needed."""
    if _healthy():
        yield BASE_URL
        return
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app/streamlit_app.py",
            "--server.port",
            str(PORT),
            "--server.headless",
            "true",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            if _healthy():
                break
            time.sleep(0.5)
        else:
            pytest.fail(f"Streamlit did not become healthy on {BASE_URL}")
        yield BASE_URL
    finally:
        proc.terminate()
        proc.wait(timeout=10)
