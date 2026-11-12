"""Tests for the system status collector (H-01)."""

import platform
from pathlib import Path

from eeg_optimizer import __version__
from eeg_optimizer.health import collect_status


def test_cbc_solver_is_available() -> None:
    status = collect_status()
    assert "PULP_CBC_CMD" in status.solvers
    assert status.cbc_available


def test_status_reports_version_and_python() -> None:
    status = collect_status()
    assert status.version == __version__
    assert status.python_version == platform.python_version()


def test_folder_status_reflects_filesystem(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    status = collect_status(root=tmp_path)
    assert status.folders == {"data": True, "config": False}
