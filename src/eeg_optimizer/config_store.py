"""Configuration files for the UI: list, edit and save community configurations.

Streamlit-free helpers behind the page ``Konfiguration`` (F2-04):

- :func:`available_configs` lists the shipped examples in ``config/`` (without
  ``defaults.yaml``) and the user's saved files in ``data/configs/``.
- :func:`apply_edits` merges a partial mapping of UI edits over an existing
  configuration and validates the result through :func:`config.parse_config`, so the
  UI never has its own validation rules. Errors are :class:`config.ConfigError`
  with German messages and key paths.
- :func:`resize_households` grows or shrinks the household list for the
  "Anzahl Haushalte" input.
- :data:`DEVICE_PRESETS` / :func:`new_device` give typical devices for
  "Gerät hinzufügen" (F4-04); :func:`validate_devices` (called by ``apply_edits``)
  checks the devices, which ``config.parse_config`` keeps raw.
- :func:`save_user_config` writes to ``data/configs/<name>.yaml`` (gitignored).

The selected configuration is shared with the other pages via
``st.session_state[SESSION_CONFIG_KEY]`` (a :class:`config.CommunityConfig`) and
``st.session_state[SESSION_PATH_KEY]`` (the :class:`pathlib.Path` it came from).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from eeg_optimizer.config import (
    MAX_HOUSEHOLDS,
    CommunityConfig,
    ConfigError,
    config_to_dict,
    merge_dicts,
    parse_config,
    save_config,
)
from eeg_optimizer.devices import BaselineScheduleError, baseline_schedule, devices_for
from eeg_optimizer.sources.cache import PROJECT_ROOT
from eeg_optimizer.timeseries import year_index

BUILTIN_CONFIG_DIR: Final = PROJECT_ROOT / "config"
USER_CONFIG_DIR: Final = PROJECT_ROOT / "data" / "configs"
DEFAULT_CONFIG_PATH: Final = BUILTIN_CONFIG_DIR / "example_community.yaml"
"""Standard scenario (spec: Beispielgemeinschaft 2023)."""

SESSION_CONFIG_KEY: Final = "community_config"
SESSION_PATH_KEY: Final = "community_config_path"

_DEFAULTS_NAME: Final = "defaults.yaml"
_FILENAME_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


@dataclass(frozen=True)
class ConfigFile:
    """A selectable configuration file with a German display label."""

    label: str
    path: Path


def available_configs(
    builtin_dir: Path = BUILTIN_CONFIG_DIR, user_dir: Path = USER_CONFIG_DIR
) -> list[ConfigFile]:
    """Shipped examples first (sorted), then saved user files (sorted)."""
    files = [
        ConfigFile(f"Beispiel: {p.name}", p)
        for p in sorted(builtin_dir.glob("*.yaml"))
        if p.name != _DEFAULTS_NAME
    ]
    if user_dir.is_dir():
        files += [ConfigFile(f"Gespeichert: {p.name}", p) for p in sorted(user_dir.glob("*.yaml"))]
    return files


def apply_edits(base: CommunityConfig, edits: Mapping[str, Any]) -> CommunityConfig:
    """Merge ``edits`` (partial config mapping) over ``base`` and validate.

    Nested mappings merge key by key; lists (``households``) replace the old list.
    The devices are validated too (:func:`validate_devices`), so a saved configuration
    never breaks the baseline analysis. Raises :class:`ConfigError` for invalid values.
    """
    config = parse_config(merge_dicts(config_to_dict(base), edits))
    validate_devices(config)
    return config


def validate_devices(config: CommunityConfig) -> None:
    """Check all devices of ``config`` (F4-04).

    ``config.load_config`` keeps devices raw; this parses them
    (:func:`devices.devices_for`) and builds their baseline over the configured year,
    which catches windows that become too short on the spring DST day.

    Raises:
        ConfigError: German message with key path.
    """
    parsed = devices_for(config)
    index = year_index(config.period.year)
    for i, hh in enumerate(config.households):
        for j, device in enumerate(parsed[hh.id]):
            try:
                baseline_schedule(device, index)
            except BaselineScheduleError as exc:
                raise ConfigError(f"households[{i}].devices[{j}]: {exc}") from exc


@dataclass(frozen=True)
class DevicePreset:
    """A typical flexible load as starting point for a new device in the UI."""

    label: str
    device: Mapping[str, Any]


DEVICE_PRESETS: Final[dict[str, DevicePreset]] = {
    "washing_machine": DevicePreset(
        "Waschmaschine",
        {
            "kind": "program",
            "power_kw": 1.0,
            "duration_min": 60,
            "window": {"start": "08:00", "end": "22:00"},
            "baseline_start": "19:00",
        },
    ),
    "dishwasher": DevicePreset(
        "Geschirrspüler",
        {
            "kind": "program",
            "power_kw": 1.0,
            "duration_min": 90,
            "window": {"start": "08:00", "end": "23:00"},
            "baseline_start": "20:00",
        },
    ),
    "ev": DevicePreset(
        "E-Auto",
        {
            "kind": "modulating",
            "power_kw": 11.0,
            "energy_kwh_per_day": 8.0,
            "window": {"start": "18:00", "end": "07:00"},
            "baseline_start": "18:00",
        },
    ),
    "boiler": DevicePreset(
        "Boiler",
        {
            "kind": "modulating",
            "power_kw": 2.0,
            "energy_kwh_per_day": 5.0,
            "window": {"start": "08:00", "end": "06:00"},
            "baseline_start": "22:00",
        },
    ),
    "heat_pump": DevicePreset(
        "Wärmepumpe",
        {
            "kind": "modulating",
            "power_kw": 2.5,
            "energy_kwh_per_day": 8.0,
            "window": {"start": "10:00", "end": "16:00"},
            "baseline_start": "10:00",
        },
    ),
}
"""Presets for "Gerät hinzufügen"; values as in the example community (F4-03)."""


def new_device(preset: str, existing_ids: Iterable[str]) -> dict[str, Any]:
    """Raw device mapping from ``DEVICE_PRESETS[preset]`` with a free id.

    The id is the preset key, or ``<key>_2``, ``<key>_3`` … if it is taken.

    Raises:
        KeyError: unknown preset (German message).
    """
    if preset not in DEVICE_PRESETS:
        raise KeyError(
            f"unbekannte Gerätevorlage {preset!r} (bekannt: {', '.join(DEVICE_PRESETS)})"
        )
    taken = set(existing_ids)
    device_id = preset
    number = 2
    while device_id in taken:
        device_id = f"{preset}_{number}"
        number += 1
    return {"id": device_id, **copy.deepcopy(dict(DEVICE_PRESETS[preset].device))}


def resize_households(households: Sequence[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    """Return ``count`` households: truncate, or append copies of the last load.

    New households get the next free id ``hhNN`` and no devices; a ``seed`` in the
    copied load is set to one more than the highest seed in use, so synthetic
    households differ.
    """
    if not 1 <= count <= MAX_HOUSEHOLDS:
        raise ConfigError(f"households: Anzahl muss zwischen 1 und {MAX_HOUSEHOLDS} liegen")
    if not households:
        raise ConfigError("households: mindestens ein Haushalt ist nötig")
    result = [copy.deepcopy(dict(hh)) for hh in households[:count]]
    used_ids = {str(hh.get("id")) for hh in households}
    template = dict(households[-1].get("load", {}))
    next_seed = max(int(hh.get("load", {}).get("seed", 0)) for hh in households) + 1
    number = 1
    while len(result) < count:
        while f"hh{number:02d}" in used_ids:
            number += 1
        new_id = f"hh{number:02d}"
        used_ids.add(new_id)
        load = copy.deepcopy(template)
        load["seed"] = next_seed
        next_seed += 1
        result.append({"id": new_id, "load": load})
    return result


def user_config_path(filename: str, user_dir: Path = USER_CONFIG_DIR) -> Path:
    """Validated target path ``user_dir/<filename>.yaml`` (no directories allowed)."""
    stem = filename.strip()
    if stem.endswith(".yaml"):
        stem = stem[: -len(".yaml")]
    if not _FILENAME_RE.fullmatch(stem):
        raise ConfigError(
            "Dateiname: nur Buchstaben, Ziffern, '-' und '_' erlaubt "
            f"(1 bis 64 Zeichen, ohne Ordner), gefunden {filename!r}"
        )
    return user_dir / f"{stem}.yaml"


def save_user_config(
    config: CommunityConfig, filename: str, user_dir: Path = USER_CONFIG_DIR
) -> Path:
    """Write ``config`` to ``user_dir/<filename>.yaml`` and return the path."""
    path = user_config_path(filename, user_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    save_config(config, path)
    return path


__all__ = [
    "BUILTIN_CONFIG_DIR",
    "DEFAULT_CONFIG_PATH",
    "DEVICE_PRESETS",
    "SESSION_CONFIG_KEY",
    "SESSION_PATH_KEY",
    "USER_CONFIG_DIR",
    "ConfigFile",
    "DevicePreset",
    "apply_edits",
    "available_configs",
    "new_device",
    "resize_households",
    "save_user_config",
    "user_config_path",
    "validate_devices",
]
