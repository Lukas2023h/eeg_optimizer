"""CSV export of a comparison (F7-01)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from eeg_optimizer.allocation import DynamicProportional
from eeg_optimizer.balance import EXPORT_COLUMN, GRID_IMPORT_COLUMN, LOAD_COLUMN, PV_COLUMN
from eeg_optimizer.compare import TOTAL_ROW, Comparison, compare_schedule
from eeg_optimizer.economics import COMMUNITY_COST_COLUMN, EMISSIONS_COLUMN
from eeg_optimizer.export import (
    HOUSEHOLD_COLUMN,
    KPI_FILE,
    TIMESERIES_COLUMNS,
    TIMESERIES_FILE,
    TIMESTAMP_COLUMN,
    export_csv,
    kpi_frame,
    read_kpi_csv,
    read_timeseries_csv,
    timeseries_csv,
    timeseries_frame,
)
from eeg_optimizer.optimize.objectives import baseline_device_schedule
from eeg_optimizer.optimize.report import comparison_tariff
from eeg_optimizer.optimize.runner import optimize
from eeg_optimizer.units import ALLOWED_SUFFIXES, TIMEZONE
from tests.unit.test_compare import _inputs
from tests.unit.test_compare_day import _shifted

ISO_WITH_OFFSET = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")


@pytest.fixture(scope="module")
def comparison() -> Comparison:
    inputs = _inputs(days=3)
    schedule = optimize(inputs, "max_shared", "milp")
    assert schedule.fallback_ratio == 0.0
    return compare_schedule(inputs, schedule.devices, DynamicProportional(), tariff="spot")


def _has_unit(column: str) -> bool:
    return any(column.endswith(suffix) and len(column) > len(suffix) for suffix in ALLOWED_SUFFIXES)


def test_export_writes_both_files_into_a_new_directory(
    comparison: Comparison, tmp_path: Path
) -> None:
    target = tmp_path / "a" / "b"

    files = export_csv(comparison, target)

    assert files.timeseries == target / TIMESERIES_FILE
    assert files.kpis == target / KPI_FILE
    assert files.timeseries.is_file()
    assert files.kpis.is_file()


def test_timeseries_roundtrip_with_plain_pandas_is_identical(
    comparison: Comparison, tmp_path: Path
) -> None:
    files = export_csv(comparison, tmp_path)
    expected = timeseries_frame(comparison)

    raw = pd.read_csv(files.timeseries)

    assert list(raw.columns) == [TIMESTAMP_COLUMN, *TIMESERIES_COLUMNS]
    assert all(_has_unit(column) for column in TIMESERIES_COLUMNS)
    assert all(ISO_WITH_OFFSET.fullmatch(stamp) for stamp in raw[TIMESTAMP_COLUMN])
    stamps = pd.DatetimeIndex(pd.to_datetime(raw[TIMESTAMP_COLUMN], utc=True)).tz_convert(TIMEZONE)
    assert stamps.equals(pd.DatetimeIndex(expected.index))
    for column in TIMESERIES_COLUMNS:
        # exact: every float is written in its shortest round-trip form
        assert raw[column].tolist() == expected[column].tolist(), column


def test_read_timeseries_csv_restores_the_frame(comparison: Comparison, tmp_path: Path) -> None:
    files = export_csv(comparison, tmp_path)

    restored = read_timeseries_csv(files.timeseries)

    pd.testing.assert_frame_equal(
        restored, timeseries_frame(comparison), check_exact=True, check_freq=False
    )
    assert str(pd.DatetimeIndex(restored.index).tz) == TIMEZONE


def test_timeseries_hold_the_values_of_both_scenarios(comparison: Comparison) -> None:
    frame = timeseries_frame(comparison)
    base, opt = comparison.baseline, comparison.optimized

    assert frame[PV_COLUMN].tolist() == base.balance[PV_COLUMN].tolist()
    assert frame["load_baseline_kwh"].tolist() == base.balance[LOAD_COLUMN].tolist()
    assert frame["load_optimized_kwh"].tolist() == opt.balance[LOAD_COLUMN].tolist()
    assert frame["export_optimized_kwh"].tolist() == opt.balance[EXPORT_COLUMN].tolist()
    assert frame["grid_import_baseline_kwh"].tolist() == base.balance[GRID_IMPORT_COLUMN].tolist()
    assert (
        frame["community_cost_optimized_eur"].tolist()
        == opt.costs.community[COMMUNITY_COST_COLUMN].tolist()
    )
    assert (
        frame["emissions_baseline_kg_co2"].tolist()
        == base.emissions.community[EMISSIONS_COLUMN].tolist()
    )
    additional = frame["shared_optimized_kwh"].sum() - frame["shared_baseline_kwh"].sum()
    assert additional == pytest.approx(float(comparison.additional_shared_kwh), abs=1e-9)
    assert additional > 0.0


def test_kpi_roundtrip_is_identical(comparison: Comparison, tmp_path: Path) -> None:
    files = export_csv(comparison, tmp_path)

    restored = read_kpi_csv(files.kpis)
    plain = pd.read_csv(files.kpis, index_col=HOUSEHOLD_COLUMN)

    expected = kpi_frame(comparison)
    pd.testing.assert_frame_equal(restored, expected, check_exact=True, check_freq=False)
    pd.testing.assert_frame_equal(plain, expected, check_exact=True, check_freq=False)
    assert restored.index[-1] == TOTAL_ROW
    assert all(_has_unit(str(column)) for column in restored.columns)


def test_dst_fall_back_keeps_both_0200_hours_apart(tmp_path: Path) -> None:
    inputs = _shifted(_inputs(days=3), "2023-10-28", 3)
    result = compare_schedule(inputs, baseline_device_schedule(inputs), DynamicProportional())
    path = tmp_path / TIMESERIES_FILE

    path.write_text(timeseries_csv(timeseries_frame(result)), encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    restored = read_timeseries_csv(path)

    assert "2023-10-29T02:00:00+02:00" in text
    assert "2023-10-29T02:00:00+01:00" in text
    assert len(restored) == 3 * 96 + 4
    pd.testing.assert_frame_equal(
        restored, timeseries_frame(result), check_exact=True, check_freq=False
    )


def test_read_timeseries_csv_rejects_a_column_without_unit(tmp_path: Path) -> None:
    path = tmp_path / TIMESERIES_FILE
    path.write_text(
        "timestamp,pv\n2023-01-01T00:00:00+01:00,1.0\n2023-01-01T00:15:00+01:00,2.0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no unit suffix"):
        read_timeseries_csv(path)


def test_comparison_tariff_is_spot_only_for_min_cost() -> None:
    assert comparison_tariff("min_cost") == "spot"
    assert comparison_tariff("max_shared") == "fixed"
    assert comparison_tariff("min_co2") == "fixed"
