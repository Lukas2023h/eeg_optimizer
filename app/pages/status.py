"""Diagnostics page: versions, solver availability, folders."""

import streamlit as st

from eeg_optimizer.health import collect_status


def _yes_no(flag: bool) -> str:
    return "ja" if flag else "nein"


status = collect_status()

st.title("Systemstatus")
col_version, col_python, col_cbc = st.columns(3)
col_version.metric("EEG-Optimizer", status.version)
col_python.metric("Python", status.python_version)
col_cbc.metric("Solver", "CBC" if status.cbc_available else "fehlt")

st.write(f"CBC verfügbar: {_yes_no(status.cbc_available)}")
if not status.cbc_available:
    st.error("Der Optimierungs-Solver CBC fehlt. Bitte ./init.sh ausführen.")

st.subheader("Ordner")
for name, exists in status.folders.items():
    st.write(f"`{name}/` vorhanden: {_yes_no(exists)}")

st.subheader("Verfügbare Solver")
st.write(", ".join(status.solvers) or "keine")
