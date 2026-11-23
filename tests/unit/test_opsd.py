"""Tests for the OPSD household data source (F1-09)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

from eeg_optimizer.sources import opsd
from eeg_optimizer.sources.cache import Cache, CacheMissError
from eeg_optimizer.sources.opsd import (
    COLUMN,
    OpsdError,
    check_household,
    household_ids,
    household_load,
    load_household,
    load_household_meters,
    read_readings,
    readings_to_intervals,
)
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import TIMEZONE, validate_frame

FIXTURE = Path(__file__).parents[1] / "fixtures" / "opsd_household_15min_synthetic_2h.csv"
"""Synthetic readings in the layout of the OPSD 15-min CSV (8 rows, 2 households)."""

RES1 = "DE_KN_residential1"
RES2 = "DE_KN_residential2"

# Hand-computed from the fixture: reading(t + 15 min) - reading(t).
RES1_GRID_IMPORT = [0.25, 0.65, 0.1, 0.0, 0.125, 0.25, 0.125]
RES1_PV = [0.0, 0.0, 0.4, 0.3, 0.1, 0.0, 0.0]
RES1_GRID_EXPORT = [0.0, 0.0, 0.1, 0.2, 0.0, 0.0, 0.0]
RES1_DISHWASHER = [0.0, 0.5, 0.4, 0.0, 0.0, 0.0, 0.0]
RES1_LOAD = [0.25, 0.65, 0.4, 0.1, 0.225, 0.25, 0.125]
RES2_GRID_IMPORT = [0.2, 0.3, 0.1, 0.1]
RES2_WASHING_MACHINE = [0.0, 0.3, 0.3, 0.0]


def _stamps(start: str, periods: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=periods, freq="15min", tz=TIMEZONE)


def _edited_fixture(tmp_path: Path, edit: Callable[[list[str]], list[str]]) -> Path:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    path = tmp_path / "opsd.csv"
    path.write_text("\n".join(edit(lines)) + "\n", encoding="utf-8")
    return path


def _set_cell(lines: list[str], row: int, column: str, value: str) -> list[str]:
    header = lines[0].split(",")
    cells = lines[row].split(",")
    cells[header.index(column)] = value
    return [*lines[:row], ",".join(cells), *lines[row + 1 :]]


def _write_readings(path: Path, index: pd.DatetimeIndex, columns: dict[str, np.ndarray]) -> Path:
    frame = pd.DataFrame(columns)
    frame.insert(0, "utc_timestamp", index.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"))
    frame["interpolated"] = ""
    frame.to_csv(path, index=False)
    return path


# --- household IDs ------------------------------------------------------------------------


def test_household_ids_lists_all_households_of_the_file() -> None:
    assert household_ids(FIXTURE) == [RES1, RES2]


def test_check_household_returns_only_the_meters_of_that_household() -> None:
    assert check_household(FIXTURE, RES2) == [
        "DE_KN_residential2_grid_import",
        "DE_KN_residential2_washing_machine",
    ]


@pytest.mark.parametrize("unknown", ["DE_KN_residential9", "residential1", "DE_KN", ""])
def test_unknown_household_id_gives_a_clear_error_listing_the_known_ids(unknown: str) -> None:
    with pytest.raises(OpsdError, match="Unbekannter OPSD-Haushalt") as info:
        read_readings(FIXTURE, unknown)
    assert RES1 in str(info.value)
    assert RES2 in str(info.value)


def test_unknown_household_id_fails_before_any_download(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    with pytest.raises(OpsdError, match="Unbekannter OPSD-Haushalt"):
        load_household("DE_KN_residential9", cache=cache, csv_path=FIXTURE)


def test_file_without_timestamp_column_is_rejected(tmp_path: Path) -> None:
    path = _edited_fixture(tmp_path, lambda ls: [ls[0].replace("utc_timestamp", "time"), *ls[1:]])
    with pytest.raises(OpsdError, match="utc_timestamp"):
        household_ids(path)


# --- cumulative readings -> kWh per interval ----------------------------------------------


def test_read_readings_uses_utc_stamps_in_vienna_and_quantity_names() -> None:
    readings = read_readings(FIXTURE, RES1)
    assert list(readings.columns) == ["dishwasher", "grid_export", "grid_import", "pv"]
    assert readings.index[0] == pd.Timestamp("2016-01-01 01:00", tz=TIMEZONE)
    assert str(pd.DatetimeIndex(readings.index).tz) == TIMEZONE
    assert readings["grid_import"].iloc[1] == pytest.approx(500.25)


def test_cumulative_readings_are_differenced_into_kwh_per_interval() -> None:
    meters = readings_to_intervals(read_readings(FIXTURE, RES1))
    validate_frame(meters)
    assert list(meters.columns) == [
        "dishwasher_kwh",
        "grid_export_kwh",
        "grid_import_kwh",
        "pv_kwh",
    ]
    # Stamp = interval start: the last reading (02:45) starts no interval.
    assert meters.index.equals(_stamps("2016-01-01 01:00", 7))
    np.testing.assert_allclose(meters["grid_import_kwh"], RES1_GRID_IMPORT, atol=1e-9)
    np.testing.assert_allclose(meters["pv_kwh"], RES1_PV, atol=1e-9)
    np.testing.assert_allclose(meters["grid_export_kwh"], RES1_GRID_EXPORT, atol=1e-9)
    np.testing.assert_allclose(meters["dishwasher_kwh"], RES1_DISHWASHER, atol=1e-9)


def test_interval_energy_sums_to_the_difference_of_first_and_last_reading() -> None:
    readings = read_readings(FIXTURE, RES1)
    meters = readings_to_intervals(readings)
    total = readings.iloc[-1] - readings.iloc[0]
    for quantity, value in total.items():
        assert meters[f"{quantity}_kwh"].sum() == pytest.approx(value, abs=1e-9)


def test_leading_and_trailing_empty_rows_are_cut() -> None:
    meters = readings_to_intervals(read_readings(FIXTURE, RES2))
    validate_frame(meters)
    assert meters.index.equals(_stamps("2016-01-01 01:30", 4))
    np.testing.assert_allclose(meters["grid_import_kwh"], RES2_GRID_IMPORT, atol=1e-9)
    np.testing.assert_allclose(meters["washing_machine_kwh"], RES2_WASHING_MACHINE, atol=1e-9)


def test_constant_reading_gives_exactly_zero_energy() -> None:
    meters = readings_to_intervals(read_readings(FIXTURE, RES1))
    assert (meters["dishwasher_kwh"].iloc[3:] == 0.0).all()


def test_gap_inside_the_span_is_an_error_not_interpolated(tmp_path: Path) -> None:
    path = _edited_fixture(tmp_path, lambda ls: _set_cell(ls, 4, f"{RES1}_pv", ""))
    with pytest.raises(OpsdError, match=r"Zählerstand fehlt ab 2016-01-01 01:45.*pv"):
        readings_to_intervals(read_readings(path, RES1))


def test_falling_meter_reading_is_an_error(tmp_path: Path) -> None:
    path = _edited_fixture(tmp_path, lambda ls: _set_cell(ls, 5, f"{RES1}_grid_import", "500.999"))
    with pytest.raises(OpsdError, match=r"sinkt.*grid_import"):
        readings_to_intervals(read_readings(path, RES1))


def test_missing_row_breaks_the_15_min_grid(tmp_path: Path) -> None:
    path = _edited_fixture(tmp_path, lambda ls: [*ls[:4], *ls[5:]])
    with pytest.raises(OpsdError, match="15-min-Raster"):
        readings_to_intervals(read_readings(path, RES1))


def test_household_without_two_complete_readings_is_an_error(tmp_path: Path) -> None:
    path = _edited_fixture(tmp_path, lambda ls: [ls[0], ls[1]])
    with pytest.raises(OpsdError, match="keine zwei"):
        readings_to_intervals(read_readings(path, RES1))


def test_unparsable_meter_value_is_an_error(tmp_path: Path) -> None:
    path = _edited_fixture(tmp_path, lambda ls: _set_cell(ls, 3, f"{RES1}_pv", "n/a?"))
    with pytest.raises(OpsdError, match="nicht lesbar"):
        read_readings(path, RES1)


def test_readings_across_the_spring_clock_change_keep_92_intervals(tmp_path: Path) -> None:
    index = _stamps("2016-03-27 00:00", 93)  # 23 h local day + closing reading
    path = _write_readings(
        tmp_path / "dst.csv",
        index,
        {"DE_KN_residential3_grid_import": np.arange(93) * 0.125},
    )
    meters = readings_to_intervals(read_readings(path, "DE_KN_residential3"))
    validate_frame(meters)
    assert len(meters) == 92
    assert meters.index[0] == pd.Timestamp("2016-03-27 00:00", tz=TIMEZONE)
    assert (meters["grid_import_kwh"] == 0.125).all()


def test_readings_across_the_autumn_clock_change_keep_100_intervals(tmp_path: Path) -> None:
    index = _stamps("2016-10-30 00:00", 101)  # 25 h local day + closing reading
    path = _write_readings(
        tmp_path / "dst.csv",
        index,
        {"DE_KN_residential3_grid_import": np.arange(101) * 0.125},
    )
    meters = readings_to_intervals(read_readings(path, "DE_KN_residential3"))
    validate_frame(meters)
    assert len(meters) == 100
    assert meters.index[-1] == pd.Timestamp("2016-10-30 23:45", tz=TIMEZONE)
    assert (meters["grid_import_kwh"] == 0.125).all()


# --- household load -----------------------------------------------------------------------


def test_household_load_adds_self_consumed_pv_to_grid_import() -> None:
    load = household_load(readings_to_intervals(read_readings(FIXTURE, RES1)))
    validate_frame(load)
    assert list(load.columns) == [COLUMN]
    np.testing.assert_allclose(load[COLUMN], RES1_LOAD, atol=1e-9)


def test_household_load_without_pv_is_grid_import() -> None:
    load = household_load(readings_to_intervals(read_readings(FIXTURE, RES2)))
    np.testing.assert_allclose(load[COLUMN], RES2_GRID_IMPORT, atol=1e-9)


def test_household_load_needs_grid_import() -> None:
    meters = pd.DataFrame({"pv_kwh": [0.1]}, index=_stamps("2016-01-01", 1))
    with pytest.raises(OpsdError, match="grid_import"):
        household_load(meters)


def test_household_load_with_pv_needs_grid_export() -> None:
    meters = pd.DataFrame(
        {"grid_import_kwh": [0.1], "pv_kwh": [0.2]}, index=_stamps("2016-01-01", 1)
    )
    with pytest.raises(OpsdError, match="grid_export"):
        household_load(meters)


def test_household_load_rejects_negative_balance() -> None:
    meters = pd.DataFrame(
        {"grid_import_kwh": [0.0], "pv_kwh": [0.1], "grid_export_kwh": [0.2]},
        index=_stamps("2016-01-01", 1),
    )
    with pytest.raises(OpsdError, match="negativ"):
        household_load(meters)


def test_household_load_sets_summation_noise_from_cumulative_readings_to_zero() -> None:
    # pv 0.1 -> 0.3 gives 0.19999999999999998, export 0.2 -> 0.4 gives 0.2: load -2.8e-17.
    readings = pd.DataFrame(
        {"grid_import": [5.0, 5.0], "pv": [0.1, 0.3], "grid_export": [0.2, 0.4]},
        index=_stamps("2016-01-01", 2),
    )
    meters = readings_to_intervals(readings)
    raw = meters["grid_import_kwh"] + meters["pv_kwh"] - meters["grid_export_kwh"]
    assert -opsd.NEGATIVE_LOAD_TOLERANCE_KWH < raw.iloc[0] < 0.0
    assert household_load(meters)[COLUMN].iloc[0] == 0.0


@pytest.mark.parametrize(
    ("deficit", "accepted"),
    [
        (0.5 * opsd.NEGATIVE_LOAD_TOLERANCE_KWH, True),
        (opsd.NEGATIVE_LOAD_TOLERANCE_KWH, True),
        (2.0 * opsd.NEGATIVE_LOAD_TOLERANCE_KWH, False),
    ],
)
def test_negative_load_tolerance_boundary(deficit: float, accepted: bool) -> None:
    meters = pd.DataFrame(
        {"grid_import_kwh": [0.0], "pv_kwh": [0.2], "grid_export_kwh": [0.2 + deficit]},
        index=_stamps("2016-01-01", 1),
    )
    if accepted:
        assert household_load(meters)[COLUMN].iloc[0] == 0.0
    else:
        with pytest.raises(OpsdError, match="negativ"):
            household_load(meters)


def test_household_load_accepts_export_exactly_equal_to_pv() -> None:
    meters = pd.DataFrame(
        {"grid_import_kwh": [0.0], "pv_kwh": [0.2], "grid_export_kwh": [0.2]},
        index=_stamps("2016-01-01", 1),
    )
    assert household_load(meters)[COLUMN].iloc[0] == 0.0


# --- loading with cache and year ----------------------------------------------------------


def test_load_household_from_fixture_csv_passes_validate_frame(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    load = load_household(RES1, cache=cache, csv_path=FIXTURE)
    validate_frame(load)
    np.testing.assert_allclose(load[COLUMN], RES1_LOAD, atol=1e-9)


def test_gap_in_an_appliance_meter_does_not_shorten_the_load(tmp_path: Path) -> None:
    path = _edited_fixture(tmp_path, lambda ls: _set_cell(ls, 4, f"{RES1}_dishwasher", ""))
    cache = Cache(tmp_path / "cache")
    load = load_household(RES1, cache=cache, csv_path=path)
    np.testing.assert_allclose(load[COLUMN], RES1_LOAD, atol=1e-9)
    with pytest.raises(OpsdError, match=r"fehlt.*dishwasher"):
        load_household_meters(RES1, cache=cache, csv_path=path)


def test_load_reads_only_the_load_meters(tmp_path: Path) -> None:
    readings = read_readings(FIXTURE, RES1, opsd.LOAD_QUANTITIES)
    assert sorted(readings.columns) == ["grid_export", "grid_import", "pv"]
    with pytest.raises(OpsdError, match="keinen der Zähler"):
        read_readings(FIXTURE, RES2, {"pv"})


@pytest.mark.parametrize("bad", ["../x", "DE_KN_residential1/..", "DE_KN_", "de_kn_res1"])
def test_invalid_id_format_is_rejected_before_the_cache(tmp_path: Path, bad: str) -> None:
    cache = Cache(tmp_path / "cache", allow_fetch=False)
    with pytest.raises(OpsdError, match="Ungültige OPSD-Haushalts-ID"):
        load_household(bad, cache=cache, csv_path=FIXTURE)
    with pytest.raises(OpsdError, match="Ungültige OPSD-Haushalts-ID"):
        load_household_meters(bad, cache=cache, csv_path=FIXTURE)


def test_load_household_meters_are_cached_per_household(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    first = load_household_meters(RES2, cache=cache, csv_path=FIXTURE)
    assert cache.path(opsd.SOURCE, f"{opsd.VERSION}-{RES2}-meters-15min").exists()
    offline = Cache(tmp_path / "cache", allow_fetch=False)
    second = load_household_meters(RES2, cache=offline, csv_path=tmp_path / "missing.csv")
    pd.testing.assert_frame_equal(first, second)
    with pytest.raises(CacheMissError):
        load_household_meters(RES1, cache=offline, csv_path=FIXTURE)


def test_missing_csv_is_downloaded_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []

    def fake_download(target: Path) -> Path:
        calls.append(target)
        target.write_bytes(FIXTURE.read_bytes())
        return target

    monkeypatch.setattr(opsd, "download_csv", fake_download)
    target = tmp_path / "raw" / "opsd.csv"
    target.parent.mkdir()
    load = load_household(RES1, cache=Cache(tmp_path / "cache"), csv_path=target)
    assert calls == [target]
    assert len(load) == len(RES1_LOAD)


def test_default_csv_path_lies_under_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EEG_DATA_DIR", str(tmp_path))
    assert opsd.default_csv_path() == tmp_path / "raw" / "opsd" / opsd.CSV_NAME


def test_year_must_be_covered_completely(tmp_path: Path) -> None:
    with pytest.raises(OpsdError, match="Jahr 2016 nicht vollständig"):
        load_household(RES1, year=2016, cache=Cache(tmp_path / "cache"), csv_path=FIXTURE)


def test_year_selects_exactly_that_vienna_year(tmp_path: Path) -> None:
    # Readings from 2016-12-31 23:00 to 2018-01-01 01:00 (local), 0.1 kWh per interval.
    index = _stamps("2016-12-31 23:00", len(year_index(2017)) + 9)
    path = _write_readings(
        tmp_path / "year.csv",
        index,
        {
            "DE_KN_residential4_grid_import": np.arange(len(index)) * 0.1,
            "DE_KN_residential4_freezer": np.arange(len(index)) * 0.01,
        },
    )
    cache = Cache(tmp_path / "cache")
    load = load_household("DE_KN_residential4", year=2017, cache=cache, csv_path=path)
    validate_frame(load)
    assert load.index.equals(year_index(2017))
    np.testing.assert_allclose(load[COLUMN], 0.1, atol=1e-9)
    meters = load_household_meters("DE_KN_residential4", year=2017, cache=cache, csv_path=path)
    assert meters.index.equals(year_index(2017))
    np.testing.assert_allclose(meters["freezer_kwh"], 0.01, atol=1e-9)


# --- real download (network) --------------------------------------------------------------


@pytest.mark.network
def test_network_download_has_the_layout_the_fixture_mimics(tmp_path: Path) -> None:
    """Streams only the header line of the real CSV and checks the fixture layout."""
    with requests.get(opsd.DOWNLOAD_URL, stream=True, timeout=opsd.HTTP_TIMEOUT_S) as response:
        response.raise_for_status()
        header = next(response.iter_lines()).decode("utf-8")
    path = tmp_path / "header.csv"
    path.write_text(header + "\n", encoding="utf-8")
    columns = header.split(",")
    assert columns[:2] == ["utc_timestamp", "cet_cest_timestamp"]
    assert columns[-1] == "interpolated"
    ids = household_ids(path)
    assert {RES1, "DE_KN_residential3"} <= set(ids)
    assert f"{RES1}_grid_import" in check_household(path, RES1)
