"""F5-09: the example community 2023 is optimized without a single fallback block.

A fallback block in the standard scenario is an error, not a detail (CLAUDE.md "Harte Regeln"):
it would hide solver problems behind the greedy or baseline schedule. The offline example runs
for every objective with the MILP; the online example (real PVGIS/HTW/aWATTar/Energy-Charts data)
runs as a network test for ``max_shared``.

The block days are checked as well, so a fallback ratio of 0 cannot come from an empty run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from eeg_optimizer.community import build_inputs
from eeg_optimizer.config import load_config
from eeg_optimizer.config_store import BUILTIN_CONFIG_DIR
from eeg_optimizer.optimize.objectives import Objective, OptimizationInputs
from eeg_optimizer.optimize.runner import SCORE_TOLERANCE, Schedule, optimize

OFFLINE_CONFIG: Final = BUILTIN_CONFIG_DIR / "example_community_offline.yaml"
ONLINE_CONFIG: Final = BUILTIN_CONFIG_DIR / "example_community.yaml"
DAYS_2023: Final = 365
"""Every day of 2023 starts at least one task (washing machine 08:00 to 22:00)."""


def _run(config_file: Path, objective: Objective) -> Schedule:
    config = load_config(config_file)
    assert config.period.year == 2023
    inputs = OptimizationInputs.from_community(build_inputs(config), config)
    return optimize(inputs, objective, config.optimization.solver)


def _assert_no_fallback(schedule: Schedule) -> None:
    details = "; ".join(f"{block.day}: {block.reason}" for block in schedule.fallback_blocks)
    assert schedule.fallback_ratio == 0, f"fallback blocks: {details}"
    assert schedule.fallback_blocks == ()
    assert len(schedule.block_days) == DAYS_2023
    assert schedule.score >= schedule.baseline_score - len(schedule.block_days) * SCORE_TOLERANCE


@pytest.mark.parametrize("objective", ["max_shared", "min_cost", "min_co2"])
def test_offline_example_community_2023_has_no_fallback(objective: Objective) -> None:
    schedule = _run(OFFLINE_CONFIG, objective)
    assert schedule.solver == "milp"
    _assert_no_fallback(schedule)


@pytest.mark.network
def test_online_example_community_2023_has_no_fallback_for_max_shared() -> None:
    schedule = _run(ONLINE_CONFIG, "max_shared")
    assert schedule.solver == "milp"
    _assert_no_fallback(schedule)
