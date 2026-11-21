"""Page 'Konfiguration' (F2-04): choose, edit and save a community configuration.

The active configuration is stored in ``st.session_state[SESSION_CONFIG_KEY]`` for the
other pages. All validation happens in ``eeg_optimizer.config`` (via ``apply_edits``);
this page only collects the values and shows the German error messages.
"""

from __future__ import annotations

from datetime import timedelta
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
    DEVICE_PRESETS,
    SESSION_CONFIG_KEY,
    SESSION_PATH_KEY,
    USER_CONFIG_DIR,
    apply_edits,
    available_configs,
    new_device,
    resize_households,
    save_user_config,
)
from eeg_optimizer.devices import DEVICE_KINDS
from eeg_optimizer.sources.htw import PROFILE_COUNT
from eeg_optimizer.units import KW, kw_to_kwh

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
DEVICE_KIND_LABELS = {
    "modulating": "regelbar (E-Auto, Boiler, Wärmepumpe)",
    "program": "Programm (Wasch-, Spülmaschine)",
}
TIME_OPTIONS = tuple(f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45))
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
    edited["devices"] = _device_inputs(raw.get("devices", []), index)
    return edited


# ------------------------------------------------------------------ devices (F4-04)


def _next_uid() -> int:
    key = _key("dev_uid")
    uid: int = st.session_state.get(key, 0)
    st.session_state[key] = uid + 1
    return uid


def _draft(raw_devices: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    """Device list of household ``index`` under edit: ``[{"uid": int, "raw": dict}]``.

    Kept in session state so adding/removing a device does not reset other widgets;
    the uid keeps widget keys stable when a device above is removed.
    """
    key = _key(f"hh_devices_{index}")
    if key not in st.session_state:
        st.session_state[key] = [{"uid": _next_uid(), "raw": dev} for dev in raw_devices]
    draft: list[dict[str, Any]] = st.session_state[key]
    return draft


def _add_device(index: int) -> None:
    draft = st.session_state[_key(f"hh_devices_{index}")]
    preset = st.session_state[_key(f"dev_preset_{index}")]
    ids = [
        str(st.session_state.get(_key(f"dev_id_{d['uid']}"), d["raw"].get("id", ""))) for d in draft
    ]
    draft.append({"uid": _next_uid(), "raw": new_device(preset, ids)})


def _remove_device(index: int, uid: int) -> None:
    key = _key(f"hh_devices_{index}")
    st.session_state[key] = [d for d in st.session_state[key] if d["uid"] != uid]


def _time_select(container: Any, label: str, value: str, key: str) -> str:
    options = list(TIME_OPTIONS)
    if value not in options:  # invalid file value: show it, validation reports it
        options.insert(0, value)
    chosen: str = container.selectbox(label, options, index=options.index(value), key=key)
    return chosen


def _single_device(raw: dict[str, Any], index: int, position: int, uid: int) -> dict[str, Any]:
    """Widgets for one device; returns the edited raw device mapping."""
    suffix = f"Gerät {position} Haushalt {index + 1}"
    col_id, col_kind, col_power, col_amount = st.columns(4)
    device_id = col_id.text_input(
        f"ID {suffix}", value=str(raw.get("id", "")), key=_key(f"dev_id_{uid}")
    )
    kind = col_kind.selectbox(
        f"Art {suffix}",
        DEVICE_KINDS,
        index=DEVICE_KINDS.index(raw["kind"]) if raw.get("kind") in DEVICE_KINDS else 0,
        format_func=DEVICE_KIND_LABELS.__getitem__,
        key=_key(f"dev_kind_{uid}"),
    )
    power = float(raw.get("power_kw", 1.0))
    edited: dict[str, Any] = {
        "id": device_id,
        "kind": kind,
        "power_kw": col_power.number_input(
            f"Leistung {suffix} (kW)", value=power, step=0.5, key=_key(f"dev_power_{uid}")
        ),
    }
    if kind == "modulating":
        default_energy = raw.get("energy_kwh_per_day")
        if default_energy is None:  # switched from program: same energy as one run
            default_energy = kw_to_kwh(KW(power), timedelta(minutes=raw.get("duration_min", 60)))
        edited["energy_kwh_per_day"] = col_amount.number_input(
            f"Energie pro Tag {suffix} (kWh)",
            value=float(default_energy),
            step=0.5,
            key=_key(f"dev_energy_{uid}"),
        )
        for optional in ("min_run_min", "min_power_kw"):  # no widgets yet, keep values
            if raw.get(optional) is not None:
                edited[optional] = raw[optional]
    else:
        edited["duration_min"] = col_amount.number_input(
            f"Dauer {suffix} (Minuten)",
            value=int(raw.get("duration_min", 60)),
            min_value=15,
            step=15,
            key=_key(f"dev_duration_{uid}"),
        )
    window = raw.get("window", {})
    col_start, col_end, col_base, col_remove = st.columns(4)
    edited["window"] = {
        "start": _time_select(
            col_start,
            f"Fenster Beginn {suffix}",
            window.get("start", "08:00"),
            _key(f"dev_ws_{uid}"),
        ),
        "end": _time_select(
            col_end, f"Fenster Ende {suffix}", window.get("end", "20:00"), _key(f"dev_we_{uid}")
        ),
    }
    edited["baseline_start"] = _time_select(
        col_base,
        f"Baseline-Start {suffix}",
        raw.get("baseline_start", "08:00"),
        _key(f"dev_bs_{uid}"),
    )
    col_remove.button(
        f"Gerät {position} entfernen (Haushalt {index + 1})",
        key=_key(f"dev_remove_{uid}"),
        on_click=_remove_device,
        args=(index, uid),
    )
    return edited


def _device_inputs(raw_devices: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    """Expander with the devices of household ``index``; returns the edited device list."""
    number = index + 1
    draft = _draft(raw_devices, index)
    edited: list[dict[str, Any]] = []
    with st.expander(f"Geräte Haushalt {number}"):
        st.caption(
            "Verschiebbare Geräte: Leistung, Tagesenergie (Programm: Dauer), erlaubtes "
            "Zeitfenster und Start im Ist-Betrieb (Baseline). Uhrzeiten im 15-min-Raster."
        )
        if not draft:
            st.caption("Keine Geräte.")
        for position, entry in enumerate(draft, start=1):
            edited.append(_single_device(entry["raw"], index, position, entry["uid"]))
            st.divider()
        col_preset, col_add = st.columns(2)
        col_preset.selectbox(
            f"Neues Gerät Haushalt {number}",
            tuple(DEVICE_PRESETS),
            format_func=lambda key: DEVICE_PRESETS[key].label,
            key=_key(f"dev_preset_{index}"),
        )
        col_add.button(
            f"Gerät hinzufügen (Haushalt {number})",
            key=_key(f"dev_add_{index}"),
            on_click=_add_device,
            args=(index,),
        )
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
    # drafts of removed households must not reappear when the count grows again
    for index in range(int(count), MAX_HOUSEHOLDS):
        st.session_state.pop(_key(f"hh_devices_{index}"), None)
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
