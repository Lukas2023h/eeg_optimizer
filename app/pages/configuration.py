"""Page 'Konfiguration' (F2-04): choose, edit and save a community configuration.

The active configuration is stored in ``st.session_state[SESSION_CONFIG_KEY]`` for the
other pages. All validation happens in ``eeg_optimizer.config`` (via ``apply_edits``);
this page only collects the values and shows the German error messages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from eeg_optimizer.config import (
    MAX_HOUSEHOLDS,
    CommunityConfig,
    ConfigError,
    config_to_dict,
    load_config,
)
from eeg_optimizer.config_store import (
    DEFAULT_CONFIG_PATH,
    SESSION_CONFIG_KEY,
    SESSION_PATH_KEY,
    USER_CONFIG_DIR,
    apply_edits,
    available_configs,
    resize_households,
    save_user_config,
)
from eeg_optimizer.sources.htw import PROFILE_COUNT

SELECT_KEY = "config_file_select"
VERSION_KEY = "config_form_version"
LOAD_ERROR_KEY = "config_load_error"
FLASH_KEY = "config_flash"
LOAD_SOURCES = ("htw", "synthetic", "smartmeter")
LOAD_SOURCE_LABELS = {
    "htw": "HTW-Standardprofil",
    "synthetic": "synthetisch (offline)",
    "smartmeter": "Smart-Meter-Datei",
}
EEG_TYPE_LABELS = {
    "local": "lokal (Netzebene 6/7, Netzentgelt -57 %)",
    "regional": "regional (Netzebene 4/5, Netzentgelt -28 %)",
}
TARIFF_FIELDS = (
    ("energy_price", "Energiepreis Lieferant (ct/kWh)"),
    ("grid_fee_energy", "Netzentgelt Arbeit (ct/kWh)"),
    ("levies_taxes", "Abgaben und Steuern (ct/kWh)"),
    ("feed_in_tariff", "Einspeisetarif (ct/kWh)"),
    ("eeg_internal_price", "EEG-interner Preis (ct/kWh)"),
    ("spot_markup", "Aufschlag Spottarif (ct/kWh)"),
)


def _load(path: Path) -> None:
    st.session_state[SESSION_CONFIG_KEY] = load_config(path)
    st.session_state[SESSION_PATH_KEY] = path
    st.session_state[VERSION_KEY] = st.session_state.get(VERSION_KEY, 0) + 1
    st.session_state.pop(LOAD_ERROR_KEY, None)


def _on_select() -> None:
    label = st.session_state[SELECT_KEY]
    path = next((f.path for f in available_configs() if f.label == label), None)
    if path is None:
        st.session_state[LOAD_ERROR_KEY] = f"{label} ist nicht mehr vorhanden"
        return
    try:
        _load(path)
    except (ConfigError, OSError) as exc:
        st.session_state[LOAD_ERROR_KEY] = f"{path.name} kann nicht geladen werden: {exc}"


def _key(name: str) -> str:
    return f"{name}_v{st.session_state[VERSION_KEY]}"


def _household_inputs(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Widgets for one household; returns the edited household mapping."""
    load = raw["load"]
    number = index + 1
    col_id, col_source, col_value = st.columns(3)
    hh_id = col_id.text_input(f"ID Haushalt {number}", value=raw["id"], key=_key(f"hh_id_{index}"))
    source = col_source.selectbox(
        f"Lastprofil Haushalt {number}",
        LOAD_SOURCES,
        index=LOAD_SOURCES.index(load["source"]),
        format_func=LOAD_SOURCE_LABELS.__getitem__,
        key=_key(f"hh_source_{index}"),
    )
    new_load: dict[str, Any] = {"source": source, "seed": load.get("seed", 0)}
    if source == "htw":
        new_load["profile"] = col_value.number_input(
            f"HTW-Profil Haushalt {number} (1 bis {PROFILE_COUNT})",
            min_value=1,
            max_value=PROFILE_COUNT,
            value=int(load.get("profile") or 1),
            step=1,
            key=_key(f"hh_profile_{index}"),
        )
    elif source == "synthetic":
        annual = load.get("annual_kwh")
        new_load["annual_kwh"] = col_value.number_input(
            f"Jahresverbrauch Haushalt {number} (kWh)",
            value=None if annual is None else float(annual),
            step=100.0,
            placeholder="leer = aus Seed",
            key=_key(f"hh_annual_{index}"),
        )
    else:
        new_load["path"] = col_value.text_input(
            f"Smart-Meter-Datei Haushalt {number}",
            value=load.get("path") or "",
            key=_key(f"hh_path_{index}"),
        )
    edited = dict(raw)
    edited["id"] = hh_id
    edited["load"] = new_load
    return edited


def _collect_edits(config: CommunityConfig) -> dict[str, Any]:
    raw = config_to_dict(config)
    edits: dict[str, Any] = {
        "name": st.text_input("Name der Gemeinschaft", value=raw["name"], key=_key("name"))
    }

    st.subheader("PV-Anlage")
    col_kwp, col_tilt, col_azimuth = st.columns(3)
    edits["pv"] = {
        "kwp": col_kwp.number_input(
            "PV-Leistung (kWp)", value=float(raw["pv"]["kwp"]), step=1.0, key=_key("pv_kwp")
        ),
        "tilt_deg": col_tilt.number_input(
            "Neigung (Grad)", value=float(raw["pv"]["tilt_deg"]), step=5.0, key=_key("pv_tilt")
        ),
        "azimuth_deg": col_azimuth.number_input(
            "Ausrichtung (Grad, 0 = Süd, -90 = Ost, 90 = West)",
            value=float(raw["pv"]["azimuth_deg"]),
            step=5.0,
            key=_key("pv_azimuth"),
        ),
    }

    st.subheader("Energiegemeinschaft und Tarife")
    tariffs = raw["tariffs"]
    eeg_types = tuple(EEG_TYPE_LABELS)
    edits["tariffs"] = {
        "eeg_type": st.selectbox(
            "EEG-Typ",
            eeg_types,
            index=eeg_types.index(tariffs["eeg_type"]),
            format_func=EEG_TYPE_LABELS.__getitem__,
            key=_key("eeg_type"),
        )
    }
    columns = st.columns(3)
    for i, (field, label) in enumerate(TARIFF_FIELDS):
        edits["tariffs"][field] = columns[i % 3].number_input(
            label, value=float(tariffs[field]), step=0.5, key=_key(f"tariff_{field}")
        )

    st.subheader("Haushalte")
    count = st.number_input(
        "Anzahl Haushalte",
        min_value=1,
        max_value=MAX_HOUSEHOLDS,
        value=len(raw["households"]),
        step=1,
        key=_key("hh_count"),
    )
    households = resize_households(raw["households"], int(count))
    edits["households"] = [_household_inputs(hh, i) for i, hh in enumerate(households)]
    return edits


def _default_filename(path: Path | None) -> str:
    if path is not None and path.parent == USER_CONFIG_DIR:
        return path.stem
    return "meine_gemeinschaft"


st.title("Konfiguration")

if SESSION_CONFIG_KEY not in st.session_state:
    try:
        _load(DEFAULT_CONFIG_PATH)
    except ConfigError as exc:
        st.error(f"Die Standardkonfiguration kann nicht geladen werden: {exc}")
        st.stop()

config: CommunityConfig = st.session_state[SESSION_CONFIG_KEY]
current_path: Path | None = st.session_state.get(SESSION_PATH_KEY)
files = available_configs()
labels = [f.label for f in files]
current_label = next((f.label for f in files if f.path == current_path), None)
if current_label is not None:
    st.session_state[SELECT_KEY] = current_label
st.selectbox("Konfigurationsdatei", labels, key=SELECT_KEY, on_change=_on_select)
if LOAD_ERROR_KEY in st.session_state:
    st.error(st.session_state[LOAD_ERROR_KEY])
st.caption(
    f"Aktiv: **{config.name or 'ohne Namen'}**, {len(config.households)} Haushalte, "
    f"{config.pv.kwp:g} kWp, Jahr {config.period.year}. "
    "Nicht angezeigte Werte kommen aus der Datei bzw. config/defaults.yaml."
)

edits = _collect_edits(config)

st.subheader("Speichern")
filename = st.text_input(
    "Dateiname (ohne .yaml, wird unter data/configs/ gespeichert)",
    value=_default_filename(current_path),
    key=_key("filename"),
)
col_apply, col_save = st.columns(2)
apply_clicked = col_apply.button("Übernehmen", key="config_apply")
save_clicked = col_save.button("Speichern", type="primary", key="config_save")

if apply_clicked or save_clicked:
    try:
        new_config = apply_edits(config, edits)
        saved_path = save_user_config(new_config, filename) if save_clicked else current_path
    except ConfigError as exc:
        st.error(f"Die Konfiguration ist ungültig: {exc}")
    except OSError as exc:
        st.error(f"Speichern fehlgeschlagen: {exc}")
    else:
        st.session_state[SESSION_CONFIG_KEY] = new_config
        st.session_state[SESSION_PATH_KEY] = saved_path
        st.session_state[FLASH_KEY] = (
            f"Gespeichert unter data/configs/{saved_path.name}"
            if save_clicked and saved_path is not None
            else "Übernommen. Die anderen Seiten rechnen mit dieser Konfiguration."
        )
        # rerun so selection and summary at the top show the new configuration
        st.rerun()

if FLASH_KEY in st.session_state:
    st.success(st.session_state.pop(FLASH_KEY))
