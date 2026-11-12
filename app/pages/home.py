"""Start page."""

import streamlit as st

from eeg_optimizer import __version__

st.title("EEG-Optimizer")
st.caption(f"Version {__version__}")
st.write(
    "Simulation und Optimierung des Eigenverbrauchs einer kleinen Energiegemeinschaft. "
    "Ergebnisse sind eine Obergrenze des Potenzials (perfekte Voraussicht)."
)
