"""Page 'Vergleich' (F6-02): effect of the last optimization for laypeople.

Shows the headline numbers (+kWh shared, € saved, kg CO₂ avoided), a table with one row per
household plus the total, and the community power of one selectable day before and after.
The run comes from the page 'Optimierung' (``st.session_state[report.SESSION_RUN_KEY]``);
all numbers come from :mod:`eeg_optimizer.compare`.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from eeg_optimizer import compare
from eeg_optimizer.allocation import strategy_for
from eeg_optimizer.analysis import ASSUMPTION_NOTES
from eeg_optimizer.community import CommunityInputs, build_inputs
from eeg_optimizer.config import (
    CommunityConfig,
    ConfigError,
    config_to_dict,
    load_config,
    parse_config,
)
from eeg_optimizer.config_store import DEFAULT_CONFIG_PATH, SESSION_CONFIG_KEY, SESSION_PATH_KEY
from eeg_optimizer.economics import TariffKind
from eeg_optimizer.formatting import german_number, time_labels
from eeg_optimizer.optimize.objectives import OptimizationInputs
from eeg_optimizer.optimize.report import (
    BLOCK_NOTE,
    OBJECTIVE_LABELS,
    SESSION_RUN_KEY,
    SOLVER_LABELS,
    UPPER_BOUND_NOTE,
    OptimizationRun,
)
from eeg_optimizer.sources.cache import CacheError

SESSION_COMPARISON_KEY = "comparison_result"
"""Session-state key of ``(run, comparison)``: the comparison is computed once per run."""
DAY_KEY = "comparison_day"
"""Widget key of the day selection; reset when a new run is compared."""
TOTAL_LABEL = "Summe"
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
PV_COLOR = "#e8a317"
BASELINE_COLOR = "#8a8a8a"
OPTIMIZED_COLOR = "#3b6ea8"

TABLE_COLUMNS = {
    compare.SHARED_BASELINE_COLUMN: "Gemeinsam genutzt vorher (kWh)",
    compare.SHARED_OPTIMIZED_COLUMN: "Gemeinsam genutzt nachher (kWh)",
    compare.ADDITIONAL_SHARED_COLUMN: "Mehr gemeinsam genutzt (kWh)",
    compare.COST_BASELINE_COLUMN: "Stromkosten vorher (€)",
    compare.COST_OPTIMIZED_COLUMN: "Stromkosten nachher (€)",
    compare.SAVED_COLUMN: "Gespart (€)",
    compare.EMISSIONS_BASELINE_COLUMN: "CO₂ vorher (kg)",
    compare.EMISSIONS_OPTIMIZED_COLUMN: "CO₂ nachher (kg)",
    compare.AVOIDED_COLUMN: "CO₂ vermieden (kg)",
}
"""German column headers of the household table (compare column -> label)."""
TABLE_DECIMALS = {
    compare.COST_BASELINE_COLUMN: 2,
    compare.COST_OPTIMIZED_COLUMN: 2,
    compare.SAVED_COLUMN: 2,
}


@st.cache_data(show_spinner="Zeitreihen werden geladen …", max_entries=4)
def _community_inputs(config_json: str) -> CommunityInputs:
    """Cached time series of a configuration; the key is the configuration as JSON."""
    return build_inputs(parse_config(json.loads(config_json)))


def _active_config() -> CommunityConfig:
    if SESSION_CONFIG_KEY not in st.session_state:
        st.session_state[SESSION_CONFIG_KEY] = load_config(DEFAULT_CONFIG_PATH)
        st.session_state[SESSION_PATH_KEY] = DEFAULT_CONFIG_PATH
    config: CommunityConfig = st.session_state[SESSION_CONFIG_KEY]
    return config


def _tariff(run: OptimizationRun) -> TariffKind:
    """Spot tariff for the objective 'min_cost' (what it optimized), else the fixed tariff."""
    return "spot" if run.schedule.objective == "min_cost" else "fixed"


def _comparison(run: OptimizationRun) -> compare.Comparison:
    cached = st.session_state.get(SESSION_COMPARISON_KEY)
    if cached is not None and cached[0] is run:
        result: compare.Comparison = cached[1]
        return result
    inputs = _community_inputs(json.dumps(config_to_dict(run.config), sort_keys=True))
    with st.spinner("Vergleich wird berechnet …"):
        result = compare.compare_schedule(
            OptimizationInputs.from_community(inputs, run.config),
            run.schedule.devices,
            strategy_for(run.config.allocation),
            tariff=_tariff(run),
        )
    st.session_state[SESSION_COMPARISON_KEY] = (run, result)
    # a new run preselects its own best day instead of keeping the old selection
    st.session_state.pop(DAY_KEY, None)
    return result


def _headline(result: compare.Comparison, run: OptimizationRun) -> None:
    shared = f"{german_number(result.additional_shared_kwh, signed=True)} kWh"
    saved = f"{german_number(result.saved_community_eur, 2)} €"
    avoided = f"{german_number(result.avoided_kg_co2)} kg CO₂"
    st.markdown(
        f"### {shared} gemeinsam genutzt · {saved} gespart · {avoided} vermieden",
    )
    tariff_text = "Spot-Tarif" if _tariff(run) == "spot" else "fixer Tarif"
    cols = st.columns(3)
    cols[0].metric(
        "Zusätzlich gemeinsam genutzt",
        shared,
        help="So viel mehr PV-Strom verbraucht die Gemeinschaft im Jahr selbst.",
    )
    cols[1].metric(
        "Gespart (Gemeinschaft)",
        saved,
        help=(
            f"Stromkosten der ganzen Gemeinschaft vorher minus nachher ({tariff_text}). "
            "Die Tabelle zeigt die Sicht der einzelnen Haushalte."
        ),
    )
    cols[2].metric(
        "CO₂ vermieden",
        avoided,
        help="Weniger Netzbezug mal CO₂-Intensität im jeweiligen Intervall.",
    )
    st.caption(
        f"Jahr {run.config.period.year} · Ziel: {OBJECTIVE_LABELS[run.schedule.objective]} · "
        f"Verfahren: {SOLVER_LABELS[run.schedule.solver]} · Kosten mit "
        f"{'Spot-Tarif' if _tariff(run) == 'spot' else 'fixem Tarif'}."
    )


def _household_table(result: compare.Comparison) -> pd.DataFrame:
    """Table for the UI: German headers, formatted numbers, total row 'Summe'."""
    table = result.table.rename(index={compare.TOTAL_ROW: TOTAL_LABEL})
    shown = pd.DataFrame(index=pd.Index(table.index, name="Haushalt"))
    for column, label in TABLE_COLUMNS.items():
        decimals = TABLE_DECIMALS.get(column, 0)
        shown[label] = [german_number(float(v), decimals) for v in table[column]]
    return shown


def _day_figure(profile: pd.DataFrame) -> go.Figure:
    # unique labels also on the DST fall-back day (02:00 twice), else the lines fold back
    x = time_labels(pd.DatetimeIndex(profile.index))
    fig = go.Figure()
    fig.add_scatter(
        x=x,
        y=profile[compare.PV_POWER_COLUMN],
        name="PV-Erzeugung",
        line={"color": PV_COLOR},
        fill="tozeroy",
    )
    fig.add_scatter(
        x=x,
        y=profile[compare.LOAD_BASELINE_POWER_COLUMN],
        name="Verbrauch vorher",
        line={"color": BASELINE_COLOR, "dash": "dash"},
    )
    fig.add_scatter(
        x=x,
        y=profile[compare.LOAD_OPTIMIZED_POWER_COLUMN],
        name="Verbrauch nachher",
        line={"color": OPTIMIZED_COLOR},
    )
    fig.update_layout(
        xaxis_title="Uhrzeit",
        yaxis_title="Leistung der Gemeinschaft (kW)",
        xaxis={"nticks": 13},
        hovermode="x unified",
        legend={"orientation": "h"},
        margin={"t": 30},
    )
    return fig


def _day_section(result: compare.Comparison) -> None:
    all_days = compare.days(result)
    default = compare.best_day(result)
    # selectbox with type-to-search ("15.06.2023"); the list holds exactly the computed days
    day: date = st.selectbox(
        "Tag",
        all_days,
        index=all_days.index(default),
        format_func=lambda d: f"{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m.%Y')}",
        key=DAY_KEY,
    )
    gain = compare.additional_shared_by_day(result).get(day, 0.0)
    st.caption(
        f"Vorgewählt ist der Tag mit dem größten Gewinn ({default.strftime('%d.%m.%Y')}). "
        f"Am {day.strftime('%d.%m.%Y')}: {german_number(float(gain), 1, signed=True)} kWh "
        "gemeinsam genutzt. Wo die blaue Linie unter der PV-Fläche liegt, wird PV-Strom "
        "in der Gemeinschaft verbraucht."
    )
    st.plotly_chart(
        _day_figure(compare.day_profile(result, day)),
        use_container_width=True,
        key="comparison_day_chart",
    )


st.title("Vergleich")

try:
    config = _active_config()
except (ConfigError, OSError) as exc:
    st.error(f"Die Standardkonfiguration kann nicht geladen werden: {exc}")
    st.stop()

run: OptimizationRun | None = st.session_state.get(SESSION_RUN_KEY)
if run is None:
    st.info("Noch keine Optimierung gerechnet. Bitte zuerst auf der Seite „Optimierung“ rechnen.")
    st.page_link("pages/optimization.py", label="Zur Optimierung")
    st.stop()
if run.config != config:
    st.info(
        "Die letzte Optimierung gehört zu einer anderen Konfiguration. "
        "Bitte auf der Seite „Optimierung“ neu rechnen."
    )
    st.page_link("pages/optimization.py", label="Zur Optimierung")
    st.stop()

try:
    comparison = _comparison(run)
except (ValueError, OSError, CacheError) as exc:
    st.error(f"Der Vergleich kann nicht berechnet werden: {exc}")
    st.stop()

st.caption(
    f"Gemeinschaft **{config.name or 'ohne Namen'}**, {len(config.households)} Haushalte. "
    "Vorher = Ist-Zustand, Geräte laufen wie gewohnt. Nachher = Geräte optimal verschoben."
)
_headline(comparison, run)

with st.container(border=True, key="comparison_notes"):
    st.markdown("**Einordnung**")
    # ASSUMPTION_NOTES[0] is the upper-bound note in other words; UPPER_BOUND_NOTE replaces it
    st.markdown(
        "\n".join(f"- {note}" for note in (UPPER_BOUND_NOTE, BLOCK_NOTE, *ASSUMPTION_NOTES[1:]))
    )

st.subheader("Je Haushalt")
st.caption(
    "Kosten aus Sicht der Haushalte (inkl. interner Preis für gemeinsam genutzten Strom). "
    "Die Summe der Haushalte weicht daher von der Ersparnis der Gemeinschaft oben ab."
)
with st.container(key="comparison_table"):
    # static table: at most 15 households + total, readable without scrolling
    st.table(_household_table(comparison))

st.subheader("Ein Tag vorher und nachher")
_day_section(comparison)
