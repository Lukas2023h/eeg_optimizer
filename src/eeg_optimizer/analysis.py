"""Baseline analysis of a community (F3-06): everything the page 'Ist-Analyse' shows.

``analyze(config, inputs)`` runs balance, KPIs, allocation, costs (fixed tariff) and
emissions on the baseline loads (inflexible load plus the baseline schedule of every
device, :func:`devices.baseline_loads`, no shifting) and adds two views used for charts:

* :func:`daily_profile`: mean power per 15-min slot of the day (local time), PV vs. load.
* :func:`surplus_by_day_hour`: PV surplus (``export_kwh``) summed per calendar day and hour.

The functions are free of Streamlit so the optimization and comparison pages (F5-10, F6)
can reuse them for the optimized balance as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd

from eeg_optimizer import allocation, balance, devices, economics
from eeg_optimizer.balance import Kpis
from eeg_optimizer.community import CommunityInputs
from eeg_optimizer.config import CommunityConfig
from eeg_optimizer.units import kwh_to_kw

PV_POWER_COLUMN: Final = "pv_kw"
LOAD_POWER_COLUMN: Final = "load_kw"
PROFILE_COLUMNS: Final = (PV_POWER_COLUMN, LOAD_POWER_COLUMN)
TIME_OF_DAY_NAME: Final = "time_of_day"
DAY_NAME: Final = "day"
HOUR_NAME: Final = "hour"

ASSUMPTION_NOTES: Final = (
    "Obergrenze bei perfekter Voraussicht: Optimierte Werte setzen voraus, dass Erzeugung "
    "und Verbrauch des ganzen Jahres im Voraus bekannt sind.",
    "Lastprofile: deutsche HTW-Berlin-Standardprofile (bzw. synthetische Profile im "
    "Offline-Beispiel), keine gemessenen österreichischen Haushalte.",
    "CO₂: Durchschnittsintensität der österreichischen Stromerzeugung ohne Importe "
    "(Lebenszyklus-Werte); PV-Strom zählt mit 0 g/kWh.",
    "Kosten: Tarife aus der Konfiguration; weitere Befreiungen (z. B. Elektrizitätsabgabe) "
    "sind nicht berücksichtigt.",
)
"""German notes on the main simplifications (spec §7), shown on every result page."""


@dataclass(frozen=True)
class BaselineAnalysis:
    """Baseline (no shifted devices) of one community over the configured year."""

    balance: pd.DataFrame
    """Output of :func:`balance.community_balance`."""
    kpis: Kpis
    monthly: pd.DataFrame
    """Output of :func:`balance.monthly_kpis`."""
    profile: pd.DataFrame
    """Output of :func:`daily_profile`."""
    surplus: pd.Series
    """Output of :func:`surplus_by_day_hour`."""
    costs: economics.CostResult
    """Costs with the fixed tariff."""
    emissions: economics.EmissionResult


def daily_profile(balance_frame: pd.DataFrame) -> pd.DataFrame:
    """Mean power per 15-min slot of the day (local time), averaged over all days.

    Returns:
        96 rows, index ``time_of_day`` (``"HH:MM"`` = interval start), columns ``pv_kw``
        and ``load_kw``. On DST days a slot that does not exist (spring) is missing for
        that day and a doubled slot (autumn) counts twice; the mean stays per interval.

    Raises:
        balance.BalanceInputError / units.FrameValidationError: as :func:`balance.kpis`.
    """
    balance.kpis(balance_frame)  # same input check as the KPIs
    index = pd.DatetimeIndex(balance_frame.index)
    slots = index.strftime("%H:%M")
    energy = balance_frame[[balance.PV_COLUMN, balance.LOAD_COLUMN]].astype(float)
    mean_kwh = energy.groupby(slots).mean().sort_index()
    profile = pd.DataFrame(
        {
            PV_POWER_COLUMN: kwh_to_kw(mean_kwh[balance.PV_COLUMN].to_numpy(dtype=float)),
            LOAD_POWER_COLUMN: kwh_to_kw(mean_kwh[balance.LOAD_COLUMN].to_numpy(dtype=float)),
        },
        index=pd.Index(mean_kwh.index, name=TIME_OF_DAY_NAME),
    )
    return profile


def surplus_by_day_hour(balance_frame: pd.DataFrame) -> pd.Series:
    """PV surplus fed into the grid, summed per calendar day and hour (local time).

    Returns:
        Series ``export_kwh`` with a MultiIndex (``day`` as ``datetime.date``, ``hour``
        0..23). A pivot table hour by day gives the heatmap. The missing
        hour of the spring DST day is absent; the doubled autumn hour holds both hours.

    Raises:
        balance.BalanceInputError / units.FrameValidationError: as :func:`balance.kpis`.
    """
    balance.kpis(balance_frame)
    index = pd.DatetimeIndex(balance_frame.index)
    export = balance_frame[balance.EXPORT_COLUMN].astype(float)
    grouped = export.groupby(
        [pd.Index(index.date, name=DAY_NAME), pd.Index(index.hour, name=HOUR_NAME)]
    ).sum()
    grouped.name = balance.EXPORT_COLUMN
    return grouped


def analyze(config: CommunityConfig, inputs: CommunityInputs) -> BaselineAnalysis:
    """Baseline analysis of ``inputs`` (from :func:`community.build_inputs(config)`).

    Raises:
        Every input error of balance, allocation and economics (all ``ValueError``).
    """
    loads = devices.baseline_loads(inputs.loads, devices.devices_for(config))
    bal = balance.community_balance(inputs.pv, loads)
    alloc = allocation.strategy_for(config.allocation).allocate(bal, loads)
    return BaselineAnalysis(
        balance=bal,
        kpis=balance.kpis(bal),
        monthly=balance.monthly_kpis(bal),
        profile=daily_profile(bal),
        surplus=surplus_by_day_hour(bal),
        costs=economics.costs(bal, alloc, config.tariffs),
        emissions=economics.emissions(bal, alloc, inputs.co2),
    )


__all__ = [
    "ASSUMPTION_NOTES",
    "DAY_NAME",
    "HOUR_NAME",
    "LOAD_POWER_COLUMN",
    "PROFILE_COLUMNS",
    "PV_POWER_COLUMN",
    "TIME_OF_DAY_NAME",
    "BaselineAnalysis",
    "analyze",
    "daily_profile",
    "surplus_by_day_hour",
]
