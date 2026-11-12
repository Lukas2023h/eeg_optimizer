"""Streamlit entry point. Run with: streamlit run app/streamlit_app.py --server.port 8501."""

import streamlit as st

from eeg_optimizer import __version__

st.set_page_config(page_title="EEG-Optimizer", layout="wide")

st.title("EEG-Optimizer")
st.caption(f"Version {__version__}")
st.write(
    "Simulation und Optimierung des Eigenverbrauchs einer kleinen Energiegemeinschaft. "
    "Ergebnisse sind eine Obergrenze des Potenzials (perfekte Voraussicht)."
)
