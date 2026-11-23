"""Page 'Optimierung' (F5-10): choose objective and solver, run, see progress and result.

Uses the active configuration like the page 'Ist-Analyse'. The run itself is
``optimize.runner.optimize``; labels, notes and the score summary come from
``optimize.report``. The finished run is stored in ``st.session_state[SESSION_RUN_KEY]``
for the page 'Vergleich' (F6-02).
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from eeg_optimizer.community import CommunityInputs, build_inputs
from eeg_optimizer.config import (
    CommunityConfig,
    ConfigError,
    SolverName,
    config_to_dict,
    load_config,
    parse_config,
)
from eeg_optimizer.config_store import DEFAULT_CONFIG_PATH, SESSION_CONFIG_KEY, SESSION_PATH_KEY
from eeg_optimizer.optimize.objectives import OBJECTIVES, Objective, OptimizationInputs
from eeg_optimizer.optimize.report import (
    NOTES,
    OBJECTIVE_LABELS,
    SESSION_RUN_KEY,
    SOLVER_LABELS,
    OptimizationRun,
    score_summary,
)
from eeg_optimizer.optimize.runner import optimize
from eeg_optimizer.sources.cache import CacheError

SOLVERS: tuple[SolverName, ...] = ("milp", "greedy")


@st.cache_data(show_spinner="Zeitreihen werden geladen …", max_entries=4)
def _community_inputs(config_json: str) -> CommunityInputs:
    """Cached time series of a configuration; the key is the configuration as JSON."""
    return build_inputs(parse_config(json.loads(config_json)))


def _number(value: float, decimals: int = 0) -> str:
    """German number format: 12.345,6."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def _active_config() -> CommunityConfig:
    if SESSION_CONFIG_KEY not in st.session_state:
        st.session_state[SESSION_CONFIG_KEY] = load_config(DEFAULT_CONFIG_PATH)
        st.session_state[SESSION_PATH_KEY] = DEFAULT_CONFIG_PATH
    config: CommunityConfig = st.session_state[SESSION_CONFIG_KEY]
    return config


def _run(config: CommunityConfig, objective: Objective, solver: SolverName) -> OptimizationRun:
    inputs = _community_inputs(json.dumps(config_to_dict(config), sort_keys=True))
    bar = st.progress(0.0, text="Optimierung läuft …")

    def progress(done: int, total: int) -> None:
        bar.progress(done / total, text=f"Optimierung läuft: Tagesblock {done} von {total}")

    try:
        schedule = optimize(
            OptimizationInputs.from_community(inputs, config), objective, solver, progress=progress
        )
    finally:
        bar.empty()
    return OptimizationRun(config=config, schedule=schedule)


def _show_result(run: OptimizationRun) -> None:
    schedule = run.schedule
    summary = score_summary(schedule)
    st.subheader("Ergebnis")
    st.caption(
        f"Ziel: {OBJECTIVE_LABELS[schedule.objective]} · "
        f"Verfahren: {SOLVER_LABELS[schedule.solver]}"
    )
    decimals = 0 if summary.unit == "kWh" else 2
    cols = st.columns(3)
    cols[0].metric(
        f"{summary.label} (Ist-Zustand)", f"{_number(summary.baseline, decimals)} {summary.unit}"
    )
    cols[1].metric(
        f"{summary.label} (optimiert)",
        f"{_number(summary.optimized, decimals)} {summary.unit}",
        delta=f"{_number(summary.improvement, decimals)} {summary.unit} besser",
        delta_color="off",
    )
    cols[2].metric("Laufzeit", f"{_number(schedule.runtime_s, 1)} s")

    blocks = len(schedule.block_days)
    fallbacks = schedule.fallback_blocks
    st.markdown(f"Rückfall-Blöcke: {len(fallbacks)} von {blocks} Tagesblöcken")
    if fallbacks:
        st.warning(
            "In diesen Tagesblöcken wurde das Ergebnis des gewählten Verfahrens verworfen und "
            "ein Rückfall verwendet. Das Ergebnis ist dort nicht optimal."
        )
        st.dataframe(
            pd.DataFrame(
                {
                    "Tag": [f.day.isoformat() for f in fallbacks],
                    "verwendet": [f.used for f in fallbacks],
                    "Grund": [f.reason for f in fallbacks],
                }
            ),
            hide_index=True,
            key="optimization_fallbacks",
        )

    with st.container(border=True, key="optimization_notes"):
        st.markdown("**Einordnung des Ergebnisses**")
        st.markdown("\n".join(f"- {note}" for note in NOTES))


st.title("Optimierung")

try:
    config = _active_config()
except (ConfigError, OSError) as exc:
    st.error(f"Die Standardkonfiguration kann nicht geladen werden: {exc}")
    st.stop()

st.caption(
    f"Gemeinschaft **{config.name or 'ohne Namen'}**, {len(config.households)} Haushalte, "
    f"Jahr {config.period.year}. Verschiebbare Geräte werden Tag für Tag optimal eingeplant. "
    "Andere Konfiguration: Seite „Konfiguration“."
)

objective: Objective = st.selectbox(
    "Ziel",
    OBJECTIVES,
    index=OBJECTIVES.index(config.optimization.objective),
    format_func=lambda name: OBJECTIVE_LABELS[name],
    key="optimization_objective",
)
solver: SolverName = st.radio(
    "Verfahren",
    SOLVERS,
    index=SOLVERS.index(config.optimization.solver),
    format_func=lambda name: SOLVER_LABELS[name],
    horizontal=True,
    key="optimization_solver",
)

if st.button("Optimieren", type="primary", key="optimization_start"):
    try:
        st.session_state[SESSION_RUN_KEY] = _run(config, objective, solver)
    except (ValueError, OSError, CacheError) as exc:
        st.error(f"Die Optimierung ist fehlgeschlagen: {exc}")
        st.stop()

run: OptimizationRun | None = st.session_state.get(SESSION_RUN_KEY)
if run is None:
    st.info("Noch keine Optimierung gerechnet. Ziel und Verfahren wählen, dann „Optimieren“.")
elif run.config != config:
    st.info(
        "Die letzte Optimierung gehört zu einer anderen Konfiguration. "
        "Bitte mit „Optimieren“ neu rechnen."
    )
else:
    _show_result(run)
