"""Labels, notes and the score summary of an optimization run for the UI (F5-10).

The page 'Optimierung' (``app/pages/optimization.py``) only formats what this module returns.
The last run is kept in ``st.session_state[SESSION_RUN_KEY]`` as an :class:`OptimizationRun`
so later pages (F6-02 'Vergleich') can show the comparison without solving again.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from eeg_optimizer.config import CommunityConfig, SolverName
from eeg_optimizer.optimize.objectives import Objective
from eeg_optimizer.optimize.runner import Schedule

SESSION_RUN_KEY: Final = "optimization_run"
"""Session-state key of the last :class:`OptimizationRun`."""

OBJECTIVE_LABELS: Final[Mapping[Objective, str]] = {
    "max_shared": "Gemeinsam genutzte Energie maximieren",
    "min_cost": "Stromkosten minimieren",
    "min_co2": "CO₂-Emissionen minimieren",
}
SOLVER_LABELS: Final[Mapping[SolverName, str]] = {
    "milp": "MILP (exakt je Tagesblock)",
    "greedy": "Greedy (schnelle Heuristik)",
}

UPPER_BOUND_NOTE: Final = (
    "Obergrenze bei perfekter Voraussicht: Die Optimierung kennt PV-Erzeugung, Verbrauch, "
    "Preise und CO₂-Intensität im Voraus. Im Alltag ist weniger erreichbar."
)
BLOCK_NOTE: Final = (
    "Pro Tagesblock optimal, nicht global: Jeder Tag wird für sich geplant (Fenster bis in den "
    "Folgetag eingeschlossen). Ein Plan über das ganze Jahr auf einmal könnte etwas mehr "
    "erreichen."
)
NOTES: Final[tuple[str, ...]] = (UPPER_BOUND_NOTE, BLOCK_NOTE)

ScoreUnit = Literal["kWh", "€", "kg CO₂"]


@dataclass(frozen=True)
class OptimizationRun:
    """One finished optimization with the configuration it was computed for."""

    config: CommunityConfig
    schedule: Schedule


@dataclass(frozen=True)
class ScoreSummary:
    """Result of a run in the quantity of its objective (positive numbers, ``unit``)."""

    label: str
    """What the numbers measure, e.g. 'Gemeinsam genutzt' (German, for the UI)."""
    unit: ScoreUnit
    baseline: float
    """Value of the baseline (Ist-Zustand) in ``unit``."""
    optimized: float
    """Value of the optimized schedule in ``unit``."""
    improvement: float
    """How much better the optimized schedule is, in ``unit`` (≥ 0 by INV-3)."""


_QUANTITY: Final[Mapping[Objective, tuple[str, ScoreUnit, float]]] = {
    # objective -> (label, unit, sign that turns the score into the shown quantity)
    "max_shared": ("Gemeinsam genutzt", "kWh", 1.0),
    "min_cost": ("Stromkosten Gemeinschaft (Spot-Tarif)", "€", -1.0),
    "min_co2": ("CO₂-Emissionen", "kg CO₂", -1.0),
}


def score_summary(schedule: Schedule) -> ScoreSummary:
    """Translate the scores of ``schedule`` (higher = better) into the quantity of its objective.

    ``max_shared`` shows kWh shared, ``min_cost`` € and ``min_co2`` kg CO₂ (scores are negative
    costs/emissions there, see :mod:`optimize.objectives`).
    """
    label, unit, sign = _QUANTITY[schedule.objective]
    return ScoreSummary(
        label=label,
        unit=unit,
        baseline=sign * schedule.baseline_score,
        optimized=sign * schedule.score,
        improvement=schedule.score - schedule.baseline_score,
    )


__all__ = [
    "BLOCK_NOTE",
    "NOTES",
    "OBJECTIVE_LABELS",
    "SESSION_RUN_KEY",
    "SOLVER_LABELS",
    "UPPER_BOUND_NOTE",
    "OptimizationRun",
    "ScoreSummary",
    "score_summary",
]
