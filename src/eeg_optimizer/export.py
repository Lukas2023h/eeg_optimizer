"""CSV export of a comparison (F7-01): time series and key figures for own analyses.

Two files per comparison (:func:`export_csv`):

* :data:`TIMESERIES_FILE`: one row per 15-min interval, first column :data:`TIMESTAMP_COLUMN`
  (interval start as ISO 8601 with UTC offset, e.g. ``2023-03-26T03:00:00+02:00``), then the
  community PV and per scenario (``baseline``/``optimized``) load, shared, export, grid import
  (kWh), community cost (€, tariff of the comparison) and grid import emissions (kg CO₂).
* :data:`KPI_FILE`: :attr:`compare.Comparison.table` (one row per household plus
  :data:`compare.TOTAL_ROW`), first column ``household_id``.

Every value column carries its unit in the name (INV-4). The files use ``,`` as separator and
``.`` as decimal point; floats are written with their shortest round-trip representation, so a
plain ``pandas.read_csv`` (pandas 3 parses round-trip by default; see also
:func:`read_timeseries_csv`, :func:`read_kpi_csv`) returns exactly the exported values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pandas as pd

from eeg_optimizer.balance import (
    EXPORT_COLUMN,
    GRID_IMPORT_COLUMN,
    LOAD_COLUMN,
    PV_COLUMN,
    SHARED_COLUMN,
)
from eeg_optimizer.compare import Comparison, Scenario
from eeg_optimizer.economics import COMMUNITY_COST_COLUMN, EMISSIONS_COLUMN
from eeg_optimizer.units import TIMEZONE, validate_frame

TIMESTAMP_COLUMN: Final = "timestamp"
"""First column of the time series file: interval start, ISO 8601 with UTC offset."""
HOUSEHOLD_COLUMN: Final = "household_id"
"""First column of the key figure file."""
TIMESERIES_FILE: Final = "zeitreihen.csv"
KPI_FILE: Final = "kennzahlen.csv"
SCENARIOS: Final = ("baseline", "optimized")

_SCENARIO_COLUMNS: Final = (
    (LOAD_COLUMN, "load_{}_kwh"),
    (SHARED_COLUMN, "shared_{}_kwh"),
    (EXPORT_COLUMN, "export_{}_kwh"),
    (GRID_IMPORT_COLUMN, "grid_import_{}_kwh"),
)
_COST_TEMPLATE: Final = "community_cost_{}_eur"
_EMISSIONS_TEMPLATE: Final = "emissions_{}_kg_co2"

TIMESERIES_COLUMNS: Final = (
    PV_COLUMN,
    *(
        template.format(name)
        for name in SCENARIOS
        for template in (
            *(t for _, t in _SCENARIO_COLUMNS),
            _COST_TEMPLATE,
            _EMISSIONS_TEMPLATE,
        )
    ),
)
"""Value columns of the time series file, in file order."""


@dataclass(frozen=True)
class ExportFiles:
    """Paths written by :func:`export_csv`."""

    timeseries: Path
    kpis: Path


def _scenario_columns(sc: Scenario, name: str) -> dict[str, pd.Series]:
    columns = {template.format(name): sc.balance[source] for source, template in _SCENARIO_COLUMNS}
    columns[_COST_TEMPLATE.format(name)] = sc.costs.community[COMMUNITY_COST_COLUMN]
    columns[_EMISSIONS_TEMPLATE.format(name)] = sc.emissions.community[EMISSIONS_COLUMN]
    return columns


def timeseries_frame(comparison: Comparison) -> pd.DataFrame:
    """Community time series of both scenarios (columns :data:`TIMESERIES_COLUMNS`).

    The index is the index of the balance (interval start, Europe/Vienna).

    Raises:
        units.FrameValidationError: the scenarios do not form a valid time series frame.
    """
    columns: dict[str, pd.Series] = {PV_COLUMN: comparison.baseline.balance[PV_COLUMN]}
    columns.update(_scenario_columns(comparison.baseline, SCENARIOS[0]))
    columns.update(_scenario_columns(comparison.optimized, SCENARIOS[1]))
    frame = pd.DataFrame(columns, index=comparison.baseline.balance.index).astype(float)
    frame = frame[list(TIMESERIES_COLUMNS)]
    validate_frame(frame)
    return frame


def kpi_frame(comparison: Comparison) -> pd.DataFrame:
    """Key figures per household plus total (copy of :attr:`Comparison.table`)."""
    frame = comparison.table.astype(float)
    frame.index.name = HOUSEHOLD_COLUMN
    return frame


def timeseries_csv(frame: pd.DataFrame) -> str:
    """CSV text of a time series frame, timestamps as ISO 8601 with UTC offset."""
    index = pd.DatetimeIndex(frame.index)
    out = frame.copy()
    out.index = pd.Index([ts.isoformat() for ts in index], name=TIMESTAMP_COLUMN)
    return out.to_csv()


def kpi_csv(frame: pd.DataFrame) -> str:
    """CSV text of the key figure table."""
    return frame.to_csv(index_label=HOUSEHOLD_COLUMN)


def export_csv(comparison: Comparison, directory: Path) -> ExportFiles:
    """Write :data:`TIMESERIES_FILE` and :data:`KPI_FILE` of ``comparison`` to ``directory``.

    The directory is created if needed; existing files of the same name are replaced.

    Raises:
        OSError: the files cannot be written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    files = ExportFiles(timeseries=directory / TIMESERIES_FILE, kpis=directory / KPI_FILE)
    files.timeseries.write_text(timeseries_csv(timeseries_frame(comparison)), encoding="utf-8")
    files.kpis.write_text(kpi_csv(kpi_frame(comparison)), encoding="utf-8")
    return files


def read_timeseries_csv(path: Path) -> pd.DataFrame:
    """Read a time series file back (index = interval start, Europe/Vienna).

    Raises:
        units.FrameValidationError: the file does not hold a valid time series frame.
    """
    frame = pd.read_csv(path, index_col=TIMESTAMP_COLUMN)
    # mixed offsets (summer/winter time) parse only via UTC
    index = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True)).tz_convert(TIMEZONE)
    frame.index = index.rename(None)
    validate_frame(frame)
    return frame


def read_kpi_csv(path: Path) -> pd.DataFrame:
    """Read a key figure file back (index ``household_id``)."""
    return pd.read_csv(path, index_col=HOUSEHOLD_COLUMN)


__all__ = [
    "HOUSEHOLD_COLUMN",
    "KPI_FILE",
    "SCENARIOS",
    "TIMESERIES_COLUMNS",
    "TIMESERIES_FILE",
    "TIMESTAMP_COLUMN",
    "ExportFiles",
    "export_csv",
    "kpi_csv",
    "kpi_frame",
    "read_kpi_csv",
    "read_timeseries_csv",
    "timeseries_csv",
    "timeseries_frame",
]
