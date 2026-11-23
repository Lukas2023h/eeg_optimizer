"""Page 'Ist-Analyse' (F3-06): baseline KPIs, daily profile, months and surplus heatmap.

Uses the active configuration from ``st.session_state[SESSION_CONFIG_KEY]`` (page
'Konfiguration'); without one, ``DEFAULT_CONFIG_PATH`` is loaded. All numbers come from
``eeg_optimizer.analysis``; this page only formats and plots them.
"""

from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from eeg_optimizer import analysis, balance
from eeg_optimizer.community import build_inputs
from eeg_optimizer.config import (
    CommunityConfig,
    ConfigError,
    config_to_dict,
    load_config,
    parse_config,
)
from eeg_optimizer.config_store import DEFAULT_CONFIG_PATH, SESSION_CONFIG_KEY, SESSION_PATH_KEY
from eeg_optimizer.formatting import german_number
from eeg_optimizer.sources.cache import CacheError

MONTH_LABELS = ("Jän", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")
PV_COLOR = "#e8a317"
LOAD_COLOR = "#3b6ea8"
SHARED_COLOR = "#4c9a2a"


@st.cache_data(show_spinner="Zeitreihen werden geladen und ausgewertet …", max_entries=8)
def _analyze(config_json: str) -> analysis.BaselineAnalysis:
    """Cached baseline analysis; the key is the configuration serialized as JSON."""
    config = parse_config(json.loads(config_json))
    return analysis.analyze(config, build_inputs(config))


_number = german_number


def _percent(ratio: float) -> str:
    return f"{_number(ratio * 100, 1)} %"


def _active_config() -> CommunityConfig:
    if SESSION_CONFIG_KEY not in st.session_state:
        st.session_state[SESSION_CONFIG_KEY] = load_config(DEFAULT_CONFIG_PATH)
        st.session_state[SESSION_PATH_KEY] = DEFAULT_CONFIG_PATH
    config: CommunityConfig = st.session_state[SESSION_CONFIG_KEY]
    return config


def _kpi_tiles(result: analysis.BaselineAnalysis) -> None:
    k = result.kpis
    cols = st.columns(4)
    cols[0].metric(
        "Eigenverbrauchsquote",
        _percent(k.self_consumption_ratio),
        help="Anteil der PV-Erzeugung, der innerhalb der Gemeinschaft verbraucht wird.",
    )
    cols[1].metric(
        "Autarkiegrad",
        _percent(k.autarky_ratio),
        help="Anteil des Verbrauchs der Gemeinschaft, der aus der gemeinsamen PV gedeckt wird.",
    )
    cols[2].metric(
        "Einspeisung",
        f"{_number(k.export_kwh)} kWh",
        help="PV-Überschuss, der ins öffentliche Netz geht.",
    )
    cols[3].metric(
        "Netzbezug",
        f"{_number(k.grid_import_kwh)} kWh",
        help="Strom, den die Haushalte zusätzlich aus dem Netz beziehen.",
    )
    cols = st.columns(4)
    cols[0].metric("PV-Erzeugung", f"{_number(k.pv_kwh)} kWh")
    cols[1].metric("Gemeinsam genutzt", f"{_number(k.shared_kwh)} kWh")
    cols[2].metric(
        "Stromkosten Gemeinschaft",
        f"{_number(result.costs.community_eur)} €",
        help="Fixer Tarif, Gemeinschaftssicht: Netzbezug plus Abgaben minus Einspeisevergütung.",
    )
    cols[3].metric(
        "CO₂-Emissionen",
        f"{_number(result.emissions.community_kg_co2)} kg",
        help="Netzbezug mal CO₂-Intensität im jeweiligen Intervall.",
    )


def _profile_figure(profile: pd.DataFrame) -> go.Figure:
    x = list(profile.index)
    fig = go.Figure()
    fig.add_scatter(
        x=x,
        y=profile[analysis.PV_POWER_COLUMN],
        name="PV-Erzeugung",
        line={"color": PV_COLOR},
        fill="tozeroy",
    )
    fig.add_scatter(
        x=x, y=profile[analysis.LOAD_POWER_COLUMN], name="Verbrauch", line={"color": LOAD_COLOR}
    )
    fig.update_layout(
        xaxis_title="Uhrzeit",
        yaxis_title="mittlere Leistung (kW)",
        xaxis={"nticks": 13},
        hovermode="x unified",
        legend={"orientation": "h"},
        margin={"t": 30},
    )
    return fig


def _monthly_figure(monthly: pd.DataFrame) -> go.Figure:
    months = [MONTH_LABELS[m.month - 1] for m in pd.DatetimeIndex(monthly.index)]
    fig = go.Figure()
    fig.add_bar(x=months, y=monthly[balance.PV_COLUMN], name="PV-Erzeugung", marker_color=PV_COLOR)
    fig.add_bar(x=months, y=monthly[balance.LOAD_COLUMN], name="Verbrauch", marker_color=LOAD_COLOR)
    fig.add_bar(
        x=months,
        y=monthly[balance.SHARED_COLUMN],
        name="Gemeinsam genutzt",
        marker_color=SHARED_COLOR,
    )
    fig.update_layout(
        barmode="group",
        yaxis_title="Energie (kWh)",
        legend={"orientation": "h"},
        margin={"t": 30},
    )
    return fig


def _heatmap_figure(surplus: pd.Series) -> go.Figure:
    grid = surplus.reset_index().pivot_table(
        index=analysis.HOUR_NAME,
        columns=analysis.DAY_NAME,
        values=balance.EXPORT_COLUMN,
        aggfunc="sum",
        dropna=False,
    )
    fig = go.Figure(
        go.Heatmap(
            z=grid.to_numpy(dtype=float),
            x=[str(day) for day in grid.columns],
            y=[f"{hour:02d}:00" for hour in grid.index],
            colorscale="YlOrRd",
            colorbar={"title": "kWh"},
            hovertemplate="%{x}, %{y}: %{z:.2f} kWh<extra></extra>",
        )
    )
    fig.update_layout(xaxis_title="Tag", yaxis_title="Stunde", margin={"t": 30})
    return fig


st.title("Ist-Analyse")

try:
    config = _active_config()
except (ConfigError, OSError) as exc:
    st.error(f"Die Standardkonfiguration kann nicht geladen werden: {exc}")
    st.stop()

st.caption(
    f"Gemeinschaft **{config.name or 'ohne Namen'}**, {len(config.households)} Haushalte, "
    f"{config.pv.kwp:g} kWp, Jahr {config.period.year}. "
    "Ist-Zustand ohne verschobene Geräte. Andere Konfiguration: Seite „Konfiguration“."
)

try:
    result = _analyze(json.dumps(config_to_dict(config), sort_keys=True))
except (ValueError, OSError, CacheError) as exc:
    st.error(
        "Die Zeitreihen für diese Konfiguration können nicht geladen oder ausgewertet werden: "
        f"{exc}. Ohne Internet die Datei „example_community_offline“ wählen."
    )
    st.stop()

st.subheader("Kennzahlen im Jahr")
_kpi_tiles(result)

st.subheader("Mittlerer Tagesverlauf")
st.caption("Durchschnitt über alle Tage des Jahres: Wann erzeugt die PV, wann wird verbraucht?")
st.plotly_chart(_profile_figure(result.profile), use_container_width=True, key="analysis_profile")

st.subheader("Monate")
st.caption("Im Sommer übersteigt die PV-Erzeugung den Verbrauch, im Winter reicht sie nicht.")
st.plotly_chart(_monthly_figure(result.monthly), use_container_width=True, key="analysis_monthly")

st.subheader("PV-Überschuss nach Stunde und Tag")
st.caption(
    "Dunkle Felder: Hier geht PV-Strom ins Netz, den die Gemeinschaft nicht selbst nutzt. "
    "Genau in diese Zeiten lassen sich Geräte verschieben."
)
st.plotly_chart(_heatmap_figure(result.surplus), use_container_width=True, key="analysis_heatmap")

with st.container(border=True, key="analysis_assumptions"):
    st.markdown("**Annahmen und Vereinfachungen**")
    st.markdown("\n".join(f"- {note}" for note in analysis.ASSUMPTION_NOTES))
