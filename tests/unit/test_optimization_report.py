"""Labels, notes and score summary of the optimization page (F5-10)."""

from __future__ import annotations

from typing import get_args

import pytest

from eeg_optimizer.config import SolverName
from eeg_optimizer.optimize.objectives import OBJECTIVES, Objective
from eeg_optimizer.optimize.report import (
    BLOCK_NOTE,
    NOTES,
    OBJECTIVE_LABELS,
    SOLVER_LABELS,
    UPPER_BOUND_NOTE,
    score_summary,
)
from eeg_optimizer.optimize.runner import Schedule


def _schedule(objective: Objective, score: float, baseline_score: float) -> Schedule:
    return Schedule(
        devices={},
        objective=objective,
        solver="milp",
        block_days=(),
        fallback_blocks=(),
        score=score,
        baseline_score=baseline_score,
        runtime_s=1.0,
    )


def test_every_objective_and_solver_has_a_label() -> None:
    assert set(OBJECTIVE_LABELS) == set(OBJECTIVES)
    assert set(SOLVER_LABELS) == set(get_args(SolverName))
    assert OBJECTIVE_LABELS["max_shared"] == "Gemeinsam genutzte Energie maximieren"


def test_notes_name_upper_bound_and_day_blocks() -> None:
    assert NOTES == (UPPER_BOUND_NOTE, BLOCK_NOTE)
    assert "Obergrenze bei perfekter Voraussicht" in UPPER_BOUND_NOTE
    assert "Pro Tagesblock optimal, nicht global" in BLOCK_NOTE


def test_max_shared_summary_is_in_kwh_and_keeps_the_sign() -> None:
    summary = score_summary(_schedule("max_shared", score=120.0, baseline_score=100.0))
    assert summary.unit == "kWh"
    assert (summary.baseline, summary.optimized, summary.improvement) == (100.0, 120.0, 20.0)


@pytest.mark.parametrize(("objective", "unit"), [("min_cost", "€"), ("min_co2", "kg CO₂")])
def test_minimizing_objectives_show_positive_amounts(objective: Objective, unit: str) -> None:
    # scores are negative costs/emissions: -80 is better than -100
    summary = score_summary(_schedule(objective, score=-80.0, baseline_score=-100.0))
    assert summary.unit == unit
    assert summary.baseline == pytest.approx(100.0)
    assert summary.optimized == pytest.approx(80.0)
    assert summary.improvement == pytest.approx(20.0)


def test_unchanged_result_has_zero_improvement() -> None:
    summary = score_summary(_schedule("min_cost", score=-50.0, baseline_score=-50.0))
    assert summary.improvement == 0.0
    assert summary.optimized == summary.baseline
