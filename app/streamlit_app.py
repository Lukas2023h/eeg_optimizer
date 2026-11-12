"""Streamlit entry point. Run with: streamlit run app/streamlit_app.py --server.port 8501."""

import streamlit as st

st.set_page_config(page_title="EEG-Optimizer", layout="wide")

navigation = st.navigation(
    [
        st.Page("pages/home.py", title="Start", default=True),
        st.Page("pages/status.py", title="Systemstatus", url_path="systemstatus"),
    ]
)
navigation.run()
