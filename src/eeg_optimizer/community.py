"""Community time series from a configuration: one input bundle for balance/optimization.

``build_inputs(config)`` loads every series the configuration names (shared PV, one load
per household, spot prices, CO₂ intensity) and returns them as :class:`CommunityInputs`.
All frames are checked with :func:`units.validate_frame`, carry exactly their canonical
column (architecture.md §3.2) and share the same 15-min index of ``period.year``.

Source dispatch goes through the mappings :data:`PV_SOURCES`, :data:`LOAD_SOURCES`,
:data:`PRICE_SOURCES` and :data:`CO2_SOURCES` (source name -> loader). A new source only
adds an entry there (e.g. ``smartmeter`` in F8-01). A source name without an entry raises
:class:`CommunitySourceError` with a German message and the config key path.

The loaders in this module are the thin adapters from the config dataclasses to the
keyword signatures of ``sources/*`` (architecture.md §4 lists the config-based form).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

import pandas as pd

from eeg_optimizer.config import CommunityConfig, LoadConfig, Location, PvConfig, SeriesSourceConfig
from eeg_optimizer.sources import awattar, energy_charts, htw, pvgis, synthetic
from eeg_optimizer.sources.cache import Cache
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import KWh, validate_frame

PV_COLUMN: Final = "pv_kwh"
LOAD_COLUMN: Final = "load_kwh"
PRICE_COLUMN: Final = "spot_ct_per_kwh"
CO2_COLUMN: Final = "co2_g_per_kwh"

PvLoader = Callable[[PvConfig, Location, int, Cache | None], pd.DataFrame]
LoadLoader = Callable[[LoadConfig, int, Cache | None], pd.DataFrame]
SeriesLoader = Callable[[SeriesSourceConfig, int, Cache | None], pd.DataFrame]


class CommunitySourceError(ValueError):
    """Unknown/unavailable source or a source that returned an unusable frame."""


@dataclass(frozen=True)
class CommunityInputs:
    """All input series of one community, on one common 15-min index."""

    pv: pd.DataFrame
    """Shared PV generation, column ``pv_kwh``."""
    loads: Mapping[str, pd.DataFrame]
    """Household id -> base load, column ``load_kwh`` (config order)."""
    prices: pd.DataFrame
    """Spot prices, column ``spot_ct_per_kwh``."""
    co2: pd.DataFrame
    """Grid carbon intensity, column ``co2_g_per_kwh``."""

    @property
    def index(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.pv.index)


# --------------------------------------------------------------------------- adapters


def _pv_pvgis(cfg: PvConfig, loc: Location, year: int, cache: Cache | None) -> pd.DataFrame:
    return pvgis.load_pv(
        kwp=cfg.kwp,
        tilt_deg=cfg.tilt_deg,
        azimuth_deg=cfg.azimuth_deg,
        latitude_deg=loc.lat,
        longitude_deg=loc.lon,
        year=year,
        loss_pct=cfg.loss_pct,
        cache=cache,
    )


def _pv_synthetic(cfg: PvConfig, loc: Location, year: int, cache: Cache | None) -> pd.DataFrame:
    return synthetic.synthetic_pv(
        kwp=cfg.kwp, seed=cfg.seed, year=year, latitude_deg=loc.lat, longitude_deg=loc.lon
    )


def _load_htw(cfg: LoadConfig, year: int, cache: Cache | None) -> pd.DataFrame:
    if cfg.profile is None:
        raise CommunitySourceError("load.profile: Pflichtwert fehlt für source htw")
    return htw.load_household(cfg.profile, year=year, cache=cache)


def _load_synthetic(cfg: LoadConfig, year: int, cache: Cache | None) -> pd.DataFrame:
    annual = None if cfg.annual_kwh is None else KWh(cfg.annual_kwh)
    return synthetic.synthetic_load(seed=cfg.seed, annual_kwh=annual, year=year)


def _prices_awattar(cfg: SeriesSourceConfig, year: int, cache: Cache | None) -> pd.DataFrame:
    return awattar.load_spot_prices(year, cache=cache)


def _prices_synthetic(cfg: SeriesSourceConfig, year: int, cache: Cache | None) -> pd.DataFrame:
    return synthetic.synthetic_spot_prices(seed=cfg.seed, year=year)


def _co2_energy_charts(cfg: SeriesSourceConfig, year: int, cache: Cache | None) -> pd.DataFrame:
    return energy_charts.load_co2_intensity(year, cache=cache)


def _co2_synthetic(cfg: SeriesSourceConfig, year: int, cache: Cache | None) -> pd.DataFrame:
    return synthetic.synthetic_co2_intensity(seed=cfg.seed, year=year)


# --------------------------------------------------------------------------- registry

PV_SOURCES: Final[dict[str, PvLoader]] = {"pvgis": _pv_pvgis, "synthetic": _pv_synthetic}
LOAD_SOURCES: Final[dict[str, LoadLoader]] = {"htw": _load_htw, "synthetic": _load_synthetic}
PRICE_SOURCES: Final[dict[str, SeriesLoader]] = {
    "awattar": _prices_awattar,
    "synthetic": _prices_synthetic,
}
CO2_SOURCES: Final[dict[str, SeriesLoader]] = {
    "energy_charts": _co2_energy_charts,
    "synthetic": _co2_synthetic,
}


def _lookup[L](registry: Mapping[str, L], source: str, key_path: str) -> L:
    loader = registry.get(source)
    if loader is None:
        available = ", ".join(sorted(registry)) or "keine"
        raise CommunitySourceError(
            f"{key_path}: Datenquelle {source!r} ist nicht verfügbar, verfügbar: {available}"
        )
    return loader


def _checked(
    frame: pd.DataFrame, column: str, index: pd.DatetimeIndex, key_path: str
) -> pd.DataFrame:
    """Validate a source frame at the module boundary (INV-4, INV-6)."""
    if list(frame.columns) != [column]:
        raise CommunitySourceError(
            f"{key_path}: Quelle lieferte Spalten {list(frame.columns)}, erwartet [{column!r}]"
        )
    validate_frame(frame)
    if not frame.index.equals(index):
        raise CommunitySourceError(
            f"{key_path}: Zeitachse passt nicht zum Jahr {index[0].year} "
            f"({len(frame)} statt {len(index)} Intervalle oder andere Zeitpunkte)"
        )
    return frame


# --------------------------------------------------------------------------- build


def build_inputs(config: CommunityConfig, cache: Cache | None = None) -> CommunityInputs:
    """Load PV, household loads, spot prices and CO₂ intensity for ``config``.

    ``cache`` is passed to the network sources (``None`` = their default cache under
    ``data/cache``); synthetic sources ignore it.

    Raises:
        CommunitySourceError: if a configured source has no loader or returns a frame
            with the wrong column or time axis.
        units.FrameValidationError: if a source frame breaks the internal format.
        Source-specific errors (``PvgisError``, ``HtwError``, ``requests`` errors, …)
            are passed through unchanged.
    """
    year = config.period.year
    index = year_index(year)

    pv_loader = _lookup(PV_SOURCES, config.pv.source, "pv.source")
    pv = _checked(pv_loader(config.pv, config.location, year, cache), PV_COLUMN, index, "pv")

    loads: dict[str, pd.DataFrame] = {}
    for i, hh in enumerate(config.households):
        path = f"households[{i}].load"
        loader = _lookup(LOAD_SOURCES, hh.load.source, f"{path}.source")
        loads[hh.id] = _checked(loader(hh.load, year, cache), LOAD_COLUMN, index, path)

    price_loader = _lookup(PRICE_SOURCES, config.prices.source, "prices.source")
    prices = _checked(price_loader(config.prices, year, cache), PRICE_COLUMN, index, "prices")

    co2_loader = _lookup(CO2_SOURCES, config.co2.source, "co2.source")
    co2 = _checked(co2_loader(config.co2, year, cache), CO2_COLUMN, index, "co2")

    return CommunityInputs(pv=pv, loads=loads, prices=prices, co2=co2)


__all__ = [
    "CO2_COLUMN",
    "CO2_SOURCES",
    "LOAD_COLUMN",
    "LOAD_SOURCES",
    "PRICE_COLUMN",
    "PRICE_SOURCES",
    "PV_COLUMN",
    "PV_SOURCES",
    "CommunityInputs",
    "CommunitySourceError",
    "LoadLoader",
    "PvLoader",
    "SeriesLoader",
    "build_inputs",
]
