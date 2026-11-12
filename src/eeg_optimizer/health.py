"""System status for the diagnostics page (version, Python, solvers, folders)."""

import platform
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pulp

from eeg_optimizer import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CBC_SOLVER = "PULP_CBC_CMD"


@dataclass(frozen=True)
class SystemStatus:
    version: str
    python_version: str
    solvers: tuple[str, ...]
    folders: Mapping[str, bool]

    @property
    def cbc_available(self) -> bool:
        return CBC_SOLVER in self.solvers


def collect_status(root: Path = PROJECT_ROOT) -> SystemStatus:
    """Collect versions, available PuLP solvers and presence of the data/config folders."""
    solvers = tuple(str(s) for s in pulp.listSolvers(onlyAvailable=True))
    return SystemStatus(
        version=__version__,
        python_version=platform.python_version(),
        solvers=solvers,
        folders={name: (root / name).is_dir() for name in ("data", "config")},
    )
