"""Page 'Export' (F7-01): time series and key figures of the last optimization as CSV.

The run comes from the page 'Optimierung' (``st.session_state[report.SESSION_RUN_KEY]``); the
comparison is shared with the page 'Vergleich' (``report.SESSION_COMPARISON_KEY``). File format:
:mod:`eeg_optimizer.export`.
"""

from __future__ import annotations

import json

import streamlit as st

from eeg_optimizer import compare, export
from eeg_optimizer.community import CommunityInputs, build_inputs
from eeg_optimizer.config import (
    CommunityConfig,
    ConfigError,
    config_to_dict,
    load_config,
    parse_config,
)
from eeg_optimizer.config_store import DEFAULT_CONFIG_PATH, SESSION_CONFIG_KEY, SESSION_PATH_KEY
from eeg_optimizer.optimize.report import (
    SESSION_COMPARISON_KEY,
    SESSION_RUN_KEY,
    OptimizationRun,
    compare_run,
    comparison_tariff,
)
from eeg_optimizer.sources.cache import CacheError

SESSION_EXPORT_KEY = "export_csv"
"""Session-state key of ``(comparison, timeseries_csv, kpi_csv)``: built once per comparison."""
CSV_MIME = "text/csv"


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


def _comparison(run: OptimizationRun) -> compare.Comparison:
    cached = st.session_state.get(SESSION_COMPARISON_KEY)
    if cached is not None and cached[0] is run:
        result: compare.Comparison = cached[1]
        return result
    inputs = _community_inputs(json.dumps(config_to_dict(run.config), sort_keys=True))
    with st.spinner("Vergleich wird berechnet …"):
        result = compare_run(run, inputs)
    st.session_state[SESSION_COMPARISON_KEY] = (run, result)
    return result


def _csv_texts(result: compare.Comparison) -> tuple[str, str]:
    cached = st.session_state.get(SESSION_EXPORT_KEY)
    if cached is not None and cached[0] is result:
        texts: tuple[str, str] = cached[1]
        return texts
    with st.spinner("CSV-Dateien werden erstellt …"):
        texts = (
            export.timeseries_csv(export.timeseries_frame(result)),
            export.kpi_csv(export.kpi_frame(result)),
        )
    st.session_state[SESSION_EXPORT_KEY] = (result, texts)
    return texts


st.title("Export")

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
    timeseries_text, kpi_text = _csv_texts(comparison)
except (ValueError, OSError, CacheError) as exc:
    st.error(f"Der Export kann nicht erstellt werden: {exc}")
    st.stop()

tariff_text = "Spot-Tarif" if comparison_tariff(run.schedule.objective) == "spot" else "fixem Tarif"
st.caption(
    f"Gemeinschaft **{config.name or 'ohne Namen'}**, Jahr {config.period.year}. "
    f"Kosten mit {tariff_text}. Trennzeichen Komma, Dezimalpunkt; die Einheit steht im "
    "Spaltennamen (z. B. `_kwh`, `_eur`, `_kg_co2`)."
)

st.subheader("Zeitreihen")
st.markdown(
    "Eine Zeile je 15 Minuten (Spalte `timestamp` = Beginn des Intervalls mit UTC-Versatz). "
    "Für die ganze Gemeinschaft: PV-Erzeugung und je Szenario (`baseline` = vorher, "
    "`optimized` = nachher) Verbrauch, gemeinsam genutzt, Einspeisung, Netzbezug, Kosten "
    "und CO₂."
)
st.download_button(
    "Zeitreihen herunterladen (CSV)",
    data=timeseries_text,
    file_name=export.TIMESERIES_FILE,
    mime=CSV_MIME,
    key="export_timeseries",
)

st.subheader("Kennzahlen")
st.markdown(
    f"Eine Zeile je Haushalt und die Summe (`{compare.TOTAL_ROW}`): gemeinsam genutzt, "
    "Stromkosten aus Sicht "
    "der Haushalte und CO₂, jeweils vorher, nachher und die Verbesserung."
)
st.download_button(
    "Kennzahlen herunterladen (CSV)",
    data=kpi_text,
    file_name=export.KPI_FILE,
    mime=CSV_MIME,
    key="export_kpis",
)
