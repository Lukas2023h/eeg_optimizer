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
- :func:`save_user_config` writes to ``data/configs/<name>.yaml`` (gitignored).

The selected configuration is shared with the other pages via
``st.session_state[SESSION_CONFIG_KEY]`` (a :class:`config.CommunityConfig`) and
``st.session_state[SESSION_PATH_KEY]`` (the :class:`pathlib.Path` it came from).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
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
from eeg_optimizer.sources.cache import PROJECT_ROOT

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
    Raises :class:`ConfigError` for invalid values.
    """
    return parse_config(merge_dicts(config_to_dict(base), edits))


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
    "SESSION_CONFIG_KEY",
    "SESSION_PATH_KEY",
    "USER_CONFIG_DIR",
    "ConfigFile",
    "apply_edits",
    "available_configs",
    "resize_households",
    "save_user_config",
    "user_config_path",
]
