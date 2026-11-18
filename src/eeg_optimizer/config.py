"""Community configuration: YAML -> typed dataclasses, validated in one place.

``load_config(path)`` reads a community YAML, deep-merges it over
``config/defaults.yaml`` (mappings are merged key by key, lists and scalars of the
community file replace the default) and validates the result. Every problem raises
:class:`ConfigError` with a German message naming the offending key path, e.g.
``pv.kwp: muss größer als 0 sein``. Unknown keys are rejected so typos never
silently fall back to a default.

YAML layout (see docs/architecture.md §3.3)::

    name: Beispielgemeinschaft
    period: {year: 2023}
    location: {lat: 48.31, lon: 14.29}
    pv: {source: pvgis, kwp: 30.0, tilt_deg: 30, azimuth_deg: 0, loss_pct: 14, seed: 0}
    prices: {source: awattar, seed: 0}
    co2: {source: energy_charts, seed: 0}
    tariffs: {eeg_type: local, energy_price: 13.0, ...}   # ct/kWh, spec §7.1
    allocation: {strategy: dynamic_proportional}
    optimization: {objective: max_shared, solver: milp}
    households:
      - id: hh01
        load: {source: htw, profile: 12}
        devices: [...]          # kept as raw mappings, parsed by devices.py (F4-01)

Tariff values are ct/kWh (gross incl. 20 % VAT); ``grid_fee_reduction`` is a
fraction. If ``grid_fee_reduction`` is not set it follows ``eeg_type``
(:data:`GRID_FEE_REDUCTION_BY_EEG_TYPE`); :attr:`TariffConfig.effective_grid_fee_reduction`
returns the value to use. ``save_config`` writes the fully merged configuration, so
``load_config(save_config(cfg))`` reproduces ``cfg`` regardless of the defaults file.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, cast, get_args

import yaml

from eeg_optimizer.sources.cache import PROJECT_ROOT
from eeg_optimizer.sources.htw import PROFILE_COUNT

DEFAULTS_PATH: Final = PROJECT_ROOT / "config" / "defaults.yaml"
MAX_HOUSEHOLDS: Final = 15

EegType = Literal["local", "regional"]
PvSource = Literal["pvgis", "synthetic"]
LoadSource = Literal["htw", "synthetic", "smartmeter"]
PriceSource = Literal["awattar", "synthetic"]
Co2Source = Literal["energy_charts", "synthetic"]
AllocationStrategyName = Literal["dynamic_proportional"]
ObjectiveName = Literal["max_shared", "min_cost", "min_co2"]
SolverName = Literal["milp", "greedy"]

GRID_FEE_REDUCTION_BY_EEG_TYPE: Final[Mapping[str, float]] = {"local": 0.57, "regional": 0.28}
"""Reduction of ``grid_fee_energy`` for community-internal kWh (spec §7.1)."""


class ConfigError(ValueError):
    """Invalid community configuration (German message with the key path)."""


@dataclass(frozen=True)
class PeriodConfig:
    year: int


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float


@dataclass(frozen=True)
class PvConfig:
    source: PvSource
    kwp: float
    tilt_deg: float
    azimuth_deg: float
    """0 = south (PVGIS convention), -90 = east, 90 = west."""
    loss_pct: float
    seed: int


@dataclass(frozen=True)
class SeriesSourceConfig:
    """Source of a community-wide series (spot prices, CO₂ intensity)."""

    source: str
    seed: int


@dataclass(frozen=True)
class TariffConfig:
    """Tariffs in ct/kWh (gross), spec §7.1."""

    eeg_type: EegType
    energy_price: float
    grid_fee_energy: float
    levies_taxes: float
    feed_in_tariff: float
    eeg_internal_price: float
    spot_markup: float
    grid_fee_reduction: float | None = None
    """Explicit override; ``None`` means: derived from ``eeg_type``."""

    @property
    def effective_grid_fee_reduction(self) -> float:
        if self.grid_fee_reduction is not None:
            return self.grid_fee_reduction
        return GRID_FEE_REDUCTION_BY_EEG_TYPE[self.eeg_type]


@dataclass(frozen=True)
class AllocationConfig:
    strategy: AllocationStrategyName


@dataclass(frozen=True)
class OptimizationConfig:
    objective: ObjectiveName
    solver: SolverName


@dataclass(frozen=True)
class LoadConfig:
    source: LoadSource
    profile: int | None = None
    """HTW profile number 1..74 (``source: htw``)."""
    path: str | None = None
    """Smart-meter CSV path (``source: smartmeter``)."""
    annual_kwh: float | None = None
    """Annual consumption for ``source: synthetic`` (``None`` = drawn from the seed)."""
    seed: int = 0


@dataclass(frozen=True)
class HouseholdConfig:
    id: str
    load: LoadConfig
    devices: tuple[dict[str, Any], ...] = field(default=())
    """Raw device mappings; validated by devices.py (F4-01)."""


@dataclass(frozen=True)
class CommunityConfig:
    name: str
    period: PeriodConfig
    location: Location
    pv: PvConfig
    prices: SeriesSourceConfig
    co2: SeriesSourceConfig
    tariffs: TariffConfig
    allocation: AllocationConfig
    optimization: OptimizationConfig
    households: tuple[HouseholdConfig, ...]


# --------------------------------------------------------------------------- loading


def load_config(path: Path | str, defaults_path: Path | str = DEFAULTS_PATH) -> CommunityConfig:
    """Load ``path``, merge it over ``defaults_path`` and validate."""
    defaults = _read_yaml(Path(defaults_path), "Defaults-Datei")
    community = _read_yaml(Path(path), "Konfigurationsdatei")
    return parse_config(merge_dicts(defaults, community))


def parse_config(raw: Mapping[str, Any]) -> CommunityConfig:
    """Validate an already merged mapping and build :class:`CommunityConfig`."""
    root = _Section(raw, "")
    config = CommunityConfig(
        name=root.optional_str("name") or "",
        period=_parse_period(root.section("period")),
        location=_parse_location(root.section("location")),
        pv=_parse_pv(root.section("pv")),
        prices=_parse_series_source(root.section("prices"), get_args(PriceSource)),
        co2=_parse_series_source(root.section("co2"), get_args(Co2Source)),
        tariffs=_parse_tariffs(root.section("tariffs")),
        allocation=_parse_allocation(root.section("allocation")),
        optimization=_parse_optimization(root.section("optimization")),
        households=_parse_households(root.list_of_sections("households")),
    )
    root.finish()
    return config


def merge_dicts(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Deep merge: nested mappings merge key by key, everything else is replaced."""
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        old = merged.get(key)
        if isinstance(old, Mapping) and isinstance(value, Mapping):
            merged[key] = merge_dicts(old, value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"{what} {path} nicht gefunden")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{what} {path} ist kein gültiges YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"{what} {path}: oberste Ebene muss eine Zuordnung (Schlüssel: Wert) sein"
        )
    return data


def _parse_period(s: _Section) -> PeriodConfig:
    cfg = PeriodConfig(year=s.integer("year", minimum=2000, maximum=2100))
    s.finish()
    return cfg


def _parse_location(s: _Section) -> Location:
    cfg = Location(
        lat=s.number("lat", minimum=-90.0, maximum=90.0),
        lon=s.number("lon", minimum=-180.0, maximum=180.0),
    )
    s.finish()
    return cfg


def _parse_pv(s: _Section) -> PvConfig:
    cfg = PvConfig(
        source=cast(PvSource, s.choice("source", get_args(PvSource))),
        kwp=s.number("kwp", minimum=0.0, minimum_exclusive=True),
        tilt_deg=s.number("tilt_deg", minimum=0.0, maximum=90.0),
        azimuth_deg=s.number("azimuth_deg", minimum=-180.0, maximum=180.0),
        loss_pct=s.number("loss_pct", minimum=0.0, maximum=100.0),
        seed=s.integer("seed", minimum=0),
    )
    s.finish()
    return cfg


def _parse_series_source(s: _Section, allowed: tuple[str, ...]) -> SeriesSourceConfig:
    cfg = SeriesSourceConfig(source=s.choice("source", allowed), seed=s.integer("seed", minimum=0))
    s.finish()
    return cfg


def _parse_tariffs(s: _Section) -> TariffConfig:
    reduction = s.optional_number("grid_fee_reduction", minimum=0.0, maximum=1.0)
    cfg = TariffConfig(
        eeg_type=cast(EegType, s.choice("eeg_type", get_args(EegType))),
        energy_price=s.number("energy_price", minimum=0.0),
        grid_fee_energy=s.number("grid_fee_energy", minimum=0.0),
        levies_taxes=s.number("levies_taxes", minimum=0.0),
        feed_in_tariff=s.number("feed_in_tariff", minimum=0.0),
        eeg_internal_price=s.number("eeg_internal_price", minimum=0.0),
        spot_markup=s.number("spot_markup"),
        grid_fee_reduction=reduction,
    )
    s.finish()
    return cfg


def _parse_allocation(s: _Section) -> AllocationConfig:
    strategy = s.choice("strategy", get_args(AllocationStrategyName))
    s.finish()
    return AllocationConfig(strategy=cast(AllocationStrategyName, strategy))


def _parse_optimization(s: _Section) -> OptimizationConfig:
    cfg = OptimizationConfig(
        objective=cast(ObjectiveName, s.choice("objective", get_args(ObjectiveName))),
        solver=cast(SolverName, s.choice("solver", get_args(SolverName))),
    )
    s.finish()
    return cfg


def _parse_households(sections: list[_Section]) -> tuple[HouseholdConfig, ...]:
    if not sections:
        raise ConfigError("households: mindestens ein Haushalt ist nötig")
    if len(sections) > MAX_HOUSEHOLDS:
        raise ConfigError(
            f"households: höchstens {MAX_HOUSEHOLDS} Haushalte erlaubt, gefunden {len(sections)}"
        )
    households: list[HouseholdConfig] = []
    seen: set[str] = set()
    for s in sections:
        hh_id = s.string("id")
        if hh_id in seen:
            raise ConfigError(f"{s.path}.id: Haushalts-ID {hh_id!r} ist doppelt vergeben")
        seen.add(hh_id)
        households.append(
            HouseholdConfig(
                id=hh_id,
                load=_parse_load(s.section("load")),
                devices=tuple(d.take_all() for d in s.list_of_sections("devices", required=False)),
            )
        )
        s.finish()
    return tuple(households)


def _parse_load(s: _Section) -> LoadConfig:
    source = cast(LoadSource, s.choice("source", get_args(LoadSource)))
    profile: int | None = None
    path: str | None = None
    annual_kwh: float | None = None
    if source == "htw":
        profile = s.integer("profile", minimum=1, maximum=PROFILE_COUNT)
    elif source == "smartmeter":
        path = s.string("path")
    else:
        annual_kwh = s.optional_number("annual_kwh", minimum=0.0, minimum_exclusive=True)
    seed = s.optional_seed()
    cfg = LoadConfig(source=source, profile=profile, path=path, annual_kwh=annual_kwh, seed=seed)
    s.finish()
    return cfg


class _Section:
    """Typed, path-aware reader for one mapping; :meth:`finish` rejects unknown keys."""

    def __init__(self, raw: object, path: str) -> None:
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{path or 'Konfiguration'}: Zuordnung (Schlüssel: Wert) erwartet")
        self._raw: dict[str, Any] = {str(k): v for k, v in raw.items()}
        self._used: set[str] = set()
        self.path = path

    def _key(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def _get(self, key: str) -> Any:
        self._used.add(key)
        if key not in self._raw or self._raw[key] is None:
            raise ConfigError(f"{self._key(key)}: Pflichtwert fehlt")
        return self._raw[key]

    def has(self, key: str) -> bool:
        return self._raw.get(key) is not None

    def section(self, key: str) -> _Section:
        return _Section(self._get(key), self._key(key))

    def list_of_sections(self, key: str, *, required: bool = True) -> list[_Section]:
        if not required and not self.has(key):
            self._used.add(key)
            return []
        value = self._get(key)
        if not isinstance(value, list):
            raise ConfigError(f"{self._key(key)}: Liste erwartet")
        return [_Section(item, f"{self._key(key)}[{i}]") for i, item in enumerate(value)]

    def string(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{self._key(key)}: nicht-leerer Text erwartet, gefunden {value!r}")
        return value

    def optional_str(self, key: str) -> str | None:
        if not self.has(key):
            self._used.add(key)
            return None
        value = self._get(key)
        if not isinstance(value, str):
            raise ConfigError(f"{self._key(key)}: Text erwartet, gefunden {value!r}")
        return value

    def choice(self, key: str, allowed: tuple[str, ...]) -> str:
        value = self._get(key)
        if value not in allowed:
            raise ConfigError(
                f"{self._key(key)}: unbekannter Wert {value!r}, erlaubt: {', '.join(allowed)}"
            )
        return cast(str, value)

    def number(
        self,
        key: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        minimum_exclusive: bool = False,
    ) -> float:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"{self._key(key)}: Zahl erwartet, gefunden {value!r}")
        number = float(value)
        if not math.isfinite(number):
            raise ConfigError(f"{self._key(key)}: endliche Zahl erwartet, gefunden {value!r}")
        if minimum is not None:
            if minimum_exclusive and number <= minimum:
                raise ConfigError(
                    f"{self._key(key)}: muss größer als {minimum:g} sein, ist {value!r}"
                )
            if not minimum_exclusive and number < minimum:
                raise ConfigError(
                    f"{self._key(key)}: darf nicht kleiner als {minimum:g} sein, ist {value!r}"
                )
        if maximum is not None and number > maximum:
            raise ConfigError(
                f"{self._key(key)}: darf nicht größer als {maximum:g} sein, ist {value!r}"
            )
        return number

    def optional_number(
        self,
        key: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        minimum_exclusive: bool = False,
    ) -> float | None:
        if not self.has(key):
            self._used.add(key)
            return None
        return self.number(
            key, minimum=minimum, maximum=maximum, minimum_exclusive=minimum_exclusive
        )

    def integer(self, key: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{self._key(key)}: ganze Zahl erwartet, gefunden {value!r}")
        if minimum is not None and value < minimum:
            raise ConfigError(
                f"{self._key(key)}: darf nicht kleiner als {minimum} sein, ist {value}"
            )
        if maximum is not None and value > maximum:
            raise ConfigError(
                f"{self._key(key)}: darf nicht größer als {maximum} sein, ist {value}"
            )
        return value

    def optional_seed(self) -> int:
        """Optional ``seed`` (integer ≥ 0, default 0; only used by synthetic sources)."""
        if not self.has("seed"):
            self._used.add("seed")
            return 0
        return self.integer("seed", minimum=0)

    def take_all(self) -> dict[str, Any]:
        """Return the raw mapping unvalidated (validated by its owning module)."""
        self._used.update(self._raw)
        return dict(self._raw)

    def finish(self) -> None:
        unknown = sorted(set(self._raw) - self._used)
        if unknown:
            raise ConfigError(
                f"{self.path or 'Konfiguration'}: unbekannte Schlüssel {', '.join(unknown)}"
            )


# --------------------------------------------------------------------------- saving


def config_to_dict(config: CommunityConfig) -> dict[str, Any]:
    """Plain mapping (YAML-ready) of the fully merged configuration."""
    pv = config.pv
    t = config.tariffs
    return {
        "name": config.name,
        "period": {"year": config.period.year},
        "location": {"lat": config.location.lat, "lon": config.location.lon},
        "pv": {
            "source": pv.source,
            "kwp": pv.kwp,
            "tilt_deg": pv.tilt_deg,
            "azimuth_deg": pv.azimuth_deg,
            "loss_pct": pv.loss_pct,
            "seed": pv.seed,
        },
        "prices": {"source": config.prices.source, "seed": config.prices.seed},
        "co2": {"source": config.co2.source, "seed": config.co2.seed},
        "tariffs": {
            "eeg_type": t.eeg_type,
            "energy_price": t.energy_price,
            "grid_fee_energy": t.grid_fee_energy,
            "levies_taxes": t.levies_taxes,
            "feed_in_tariff": t.feed_in_tariff,
            "eeg_internal_price": t.eeg_internal_price,
            "spot_markup": t.spot_markup,
            "grid_fee_reduction": t.grid_fee_reduction,
        },
        "allocation": {"strategy": config.allocation.strategy},
        "optimization": {
            "objective": config.optimization.objective,
            "solver": config.optimization.solver,
        },
        "households": [_household_to_dict(hh) for hh in config.households],
    }


def _household_to_dict(hh: HouseholdConfig) -> dict[str, Any]:
    load: dict[str, Any] = {"source": hh.load.source}
    if hh.load.profile is not None:
        load["profile"] = hh.load.profile
    if hh.load.path is not None:
        load["path"] = hh.load.path
    if hh.load.annual_kwh is not None:
        load["annual_kwh"] = hh.load.annual_kwh
    load["seed"] = hh.load.seed
    out: dict[str, Any] = {"id": hh.id, "load": load}
    if hh.devices:
        out["devices"] = [dict(d) for d in hh.devices]
    return out


class _Dumper(yaml.SafeDumper):
    """SafeDumper that double-quotes strings with line-break or control characters.

    PyYAML folds NEL/LS/PS and similar characters in plain or single-quoted scalars
    into spaces; double quotes escape them, so the roundtrip stays exact.
    """


_LINE_SEPARATOR: Final = 0x2028
_PARAGRAPH_SEPARATOR: Final = 0x2029
_BYTE_ORDER_MARK: Final = 0xFEFF


def _needs_double_quotes(code: int) -> bool:
    """C0 controls (incl. tab/newline), DEL, C1 controls (incl. NEL), LS, PS, BOM."""
    return (
        code < 0x20
        or 0x7F <= code <= 0x9F
        or code in (_LINE_SEPARATOR, _PARAGRAPH_SEPARATOR, _BYTE_ORDER_MARK)
    )


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    special = any(_needs_double_quotes(ord(ch)) for ch in value)
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"' if special else None)


_Dumper.add_representer(str, _represent_str)


def save_config(config: CommunityConfig, path: Path | str) -> None:
    """Write the fully merged configuration as YAML (UTF-8)."""
    text = yaml.dump(
        config_to_dict(config),
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    Path(path).write_text(text, encoding="utf-8")


__all__ = [
    "DEFAULTS_PATH",
    "GRID_FEE_REDUCTION_BY_EEG_TYPE",
    "MAX_HOUSEHOLDS",
    "AllocationConfig",
    "CommunityConfig",
    "ConfigError",
    "HouseholdConfig",
    "LoadConfig",
    "Location",
    "OptimizationConfig",
    "PeriodConfig",
    "PvConfig",
    "SeriesSourceConfig",
    "TariffConfig",
    "config_to_dict",
    "load_config",
    "merge_dicts",
    "parse_config",
    "save_config",
]
