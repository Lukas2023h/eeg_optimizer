"""Tests for the HTW Berlin household load profiles (F1-06)."""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import requests

from eeg_optimizer.sources import htw
from eeg_optimizer.sources.cache import Cache
from eeg_optimizer.sources.htw import (
    COLUMN,
    PROFILE_COUNT,
    SOURCE_TZ,
    SOURCE_YEAR,
    HtwError,
    aggregate_samples,
    check_profile,
    load_all_profiles,
    load_household,
    map_to_year,
    profile_column,
    read_samples,
    source_days,
)
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import validate_frame

FIXTURE = Path(__file__).parents[1] / "fixtures" / "htw_1min_profiles_1-2_2010-01-01_2d.zip"
"""First two days (2010-01-01/02, MEZ) of profiles 1 and 2, same layout as the download.

Excerpt of the HTW Berlin dataset (Tjaden et al., CC BY-NC 4.0), see ``htw`` docstring.
"""

MINUTES = 2 * 24 * 60


def _raw_phase(member: str) -> np.ndarray:
    with zipfile.ZipFile(FIXTURE) as zf, zf.open(member) as fh:
        return pd.read_csv(fh, header=None).to_numpy(dtype=float)


def _raw_total_w() -> np.ndarray:
    return sum((_raw_phase(m) for m in htw.PHASE_MEMBERS), start=np.zeros((MINUTES, 2)))


def _date_coded_source(columns: int = 1) -> pd.DataFrame:
    """Full source year; every value is the MEZ date ordinal of its source day."""
    index = year_index(SOURCE_YEAR)
    ordinals = np.array([ts.date().toordinal() for ts in index.tz_convert(SOURCE_TZ)])
    values = np.repeat(ordinals[:, None], columns, axis=1).astype(float)
    return pd.DataFrame(values, index=index, columns=[f"c{i}_kwh" for i in range(columns)])


def _dates(index: pd.Index) -> np.ndarray:
    return np.asarray(pd.DatetimeIndex(index).date)


def _source_dates_by_target_day(out: pd.DataFrame) -> dict[date, date]:
    """Per MEZ target day: the single source date its values came from."""
    mez = out.tz_convert(SOURCE_TZ)
    per_day = mez.iloc[:, 0].groupby(_dates(mez.index)).agg(["min", "max"])
    assert (per_day["min"] == per_day["max"]).all(), "a target day mixes several source days"
    days = _dates(per_day.index)
    return {day: date.fromordinal(int(o)) for day, o in zip(days, per_day["min"], strict=True)}


# --- reading and aggregation (offline fixture) -------------------------------------------


def test_read_samples_sums_phases_in_watt_and_uses_mez() -> None:
    samples = read_samples(FIXTURE, [1, 2])
    assert list(samples.columns) == ["profile_01_w", "profile_02_w"]
    assert len(samples) == MINUTES
    assert str(pd.DatetimeIndex(samples.index).tz) == SOURCE_TZ
    # 2010-01-01 00:00 MEZ = 2009-12-31 23:00 UTC
    assert samples.index[0] == pd.Timestamp("2009-12-31 23:00", tz="UTC")
    # First row of the excerpt: PL1 = 61, 0; PL2 = 34, 419; PL3 = 0, 82
    assert samples.iloc[0].tolist() == [95.0, 501.0]
    np.testing.assert_array_equal(samples.to_numpy(), _raw_total_w())


def test_read_samples_single_profile_matches_column() -> None:
    both = read_samples(FIXTURE, [1, 2])
    second = read_samples(FIXTURE, [2])
    np.testing.assert_array_equal(second["profile_02_w"].to_numpy(), both["profile_02_w"])


def test_aggregation_conserves_energy_and_is_valid() -> None:
    out = aggregate_samples(read_samples(FIXTURE, [1, 2]))
    validate_frame(out)
    assert list(out.columns) == [profile_column(1), profile_column(2)]
    assert len(out) == 2 * 96
    assert out.index[0] == pd.Timestamp("2010-01-01 00:00", tz="Europe/Vienna")
    # 1 W for 1 min = 1 / 60000 kWh
    expected_kwh = _raw_total_w().sum(axis=0) / 60_000
    np.testing.assert_allclose(out.sum().to_numpy(), expected_kwh, rtol=1e-12)
    first_quarter = _raw_total_w()[:15].sum(axis=0) / 60_000
    np.testing.assert_allclose(out.iloc[0].to_numpy(), first_quarter, rtol=1e-12)


def test_profile_missing_in_file_raises() -> None:
    with pytest.raises(HtwError, match="Profil 3 fehlt"):
        read_samples(FIXTURE, [1, 3])


def test_missing_member_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as zf:
        zf.writestr("PL1.csv", "1,2\n")
    with pytest.raises(HtwError, match=r"PL2\.csv"):
        read_samples(broken, [1])


def test_row_mismatch_raises(tmp_path: Path) -> None:
    broken = tmp_path / "short.zip"
    with zipfile.ZipFile(FIXTURE) as src, zipfile.ZipFile(broken, "w") as dst:
        for member in src.namelist():
            data = src.read(member)
            if member == "PL2.csv":
                data = b"\n".join(data.splitlines()[:-1]) + b"\n"
            dst.writestr(member, data)
    with pytest.raises(HtwError, match=r"PL2\.csv hat 2879 Zeilen"):
        read_samples(broken, [1])


# --- profile number --------------------------------------------------------------------


@pytest.mark.parametrize("profile", [1, 37, PROFILE_COUNT, np.int64(5)])
def test_valid_profile_numbers(profile: Any) -> None:
    assert check_profile(profile) == int(profile)


@pytest.mark.parametrize("profile", [0, 75, -1, 1.0, 1.5, True, "1", None])
def test_invalid_profile_number_has_clear_message(profile: Any) -> None:
    with pytest.raises(HtwError, match=r"Ungültige HTW-Lastprofil-Nummer .* von 1 bis 74"):
        check_profile(profile)


@pytest.mark.parametrize("profile", [0, 75])
def test_load_household_rejects_profile_before_any_io(tmp_path: Path, profile: int) -> None:
    def no_io(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no download or cache access expected")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(htw, "load_all_profiles", no_io)
        with pytest.raises(HtwError, match="von 1 bis 74"):
            load_household(profile, cache=Cache(tmp_path, allow_fetch=False))


# --- weekday-faithful mapping ----------------------------------------------------------


def test_monday_stays_monday_on_stichtag() -> None:
    out = map_to_year(_date_coded_source(), 2023)
    origin = _source_dates_by_target_day(out)
    # Stichtag Monday 2023-01-02 <- Monday 2010-01-04 (shift +2 days)
    assert date(2023, 1, 2).weekday() == 0
    assert origin[date(2023, 1, 2)] == date(2010, 1, 4)
    # Summer Monday as well
    assert origin[date(2023, 7, 17)] == date(2010, 7, 19)


def test_every_day_keeps_its_weekday_2023() -> None:
    origin = _source_dates_by_target_day(map_to_year(_date_coded_source(), 2023))
    assert len(origin) == 365
    for target, source in origin.items():
        assert target.weekday() == source.weekday()
        assert source.year == SOURCE_YEAR


def test_dst_switch_days_2023_come_from_dst_switch_days_2010() -> None:
    origin = _source_dates_by_target_day(map_to_year(_date_coded_source(), 2023))
    assert origin[date(2023, 3, 26)] == date(2010, 3, 28)
    assert origin[date(2023, 10, 29)] == date(2010, 10, 31)


def test_year_end_wraps_one_week_back() -> None:
    origin = _source_dates_by_target_day(map_to_year(_date_coded_source(), 2023))
    # 2023-12-30/31 would need 2011-01-01/02 -> same weekday one week earlier
    assert origin[date(2023, 12, 30)] == date(2010, 12, 25)
    assert origin[date(2023, 12, 31)] == date(2010, 12, 26)


def test_dst_days_2023_are_filled() -> None:
    out = map_to_year(_date_coded_source(columns=2), 2023)
    validate_frame(out)
    assert out.index.equals(year_index(2023))
    counts = out.groupby(_dates(out.index)).size()
    assert counts[date(2023, 3, 26)] == 92
    assert counts[date(2023, 10, 29)] == 100
    assert counts.drop([date(2023, 3, 26), date(2023, 10, 29)]).eq(96).all()
    assert np.isfinite(out.to_numpy()).all()
    # Spring forward: the wall-clock hour 02:00-02:59 does not exist
    spring = pd.DatetimeIndex(out.loc["2023-03-26"].index)
    assert not (spring.hour == 2).any()
    # Fall back: 02:00-02:45 occurs twice, both filled
    autumn = pd.DatetimeIndex(out.loc["2023-10-29"].index)
    assert (autumn.hour == 2).sum() == 8


def test_mapping_conserves_daily_energy() -> None:
    rng = np.random.default_rng(0)
    index = year_index(SOURCE_YEAR)
    source = pd.DataFrame({"load_kwh": rng.uniform(0, 1, len(index))}, index=index)
    out = map_to_year(source, 2023)
    days = source_days(2023)
    src_daily = source["load_kwh"].to_numpy().reshape(-1, 96).sum(axis=1)
    out_daily = out["load_kwh"].to_numpy().reshape(-1, 96).sum(axis=1)
    np.testing.assert_allclose(out_daily, src_daily[days], rtol=1e-12)


def test_leap_target_year() -> None:
    out = map_to_year(_date_coded_source(), 2024)
    assert out.index.equals(year_index(2024))
    origin = _source_dates_by_target_day(out)
    assert len(origin) == 366
    assert all(t.weekday() == s.weekday() for t, s in origin.items())


def test_map_to_year_rejects_partial_source() -> None:
    with pytest.raises(HtwError, match="nicht genau das Jahr 2010"):
        map_to_year(_date_coded_source().iloc[:-96], 2023)


# --- cache and download ----------------------------------------------------------------


def _all_profiles_frame() -> pd.DataFrame:
    index = year_index(SOURCE_YEAR)
    data = {profile_column(n): np.full(len(index), n / 1000) for n in range(1, PROFILE_COUNT + 1)}
    return pd.DataFrame(data, index=index)


def _seed_cache(directory: Path, frame: pd.DataFrame) -> None:
    Cache(directory).get_or_fetch(htw.SOURCE, htw._all_profiles_key(), lambda: frame)


def test_load_household_from_cache_without_network(tmp_path: Path) -> None:
    _seed_cache(tmp_path, _all_profiles_frame())
    out = load_household(5, year=2023, cache=Cache(tmp_path, allow_fetch=False))
    validate_frame(out)
    assert list(out.columns) == [COLUMN]
    assert out.index.equals(year_index(2023))
    np.testing.assert_allclose(out[COLUMN].to_numpy(), 0.005)


def test_cache_entry_with_wrong_columns_raises(tmp_path: Path) -> None:
    _seed_cache(tmp_path, _all_profiles_frame().iloc[:, :10])
    with pytest.raises(HtwError, match="löschen, um neu zu laden"):
        load_household(1, cache=Cache(tmp_path, allow_fetch=False))


def test_existing_zip_is_not_downloaded_again(tmp_path: Path) -> None:
    def no_download(_target: Path) -> Path:
        raise AssertionError("zip exists, no download expected")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(htw, "download_zip", no_download)
        # The 2-profile fixture is parsed, but lacks profiles 3..74 -> clear error, no cache.
        with pytest.raises(HtwError, match="Profil 74 fehlt"):
            load_all_profiles(cache=Cache(tmp_path / "cache"), zip_path=FIXTURE)
    assert not (tmp_path / "cache").exists() or not any((tmp_path / "cache").rglob("*.parquet"))


def test_truncated_zip_in_default_location_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EEG_DATA_DIR", str(tmp_path))
    target = htw.default_zip_path()
    target.parent.mkdir(parents=True)
    target.write_bytes(b"PK truncated")
    monkeypatch.setattr(htw, "download_zip", lambda _t: pytest.fail("no download expected"))
    with pytest.raises(HtwError, match="Datei löschen, um neu zu laden"):
        load_all_profiles(cache=Cache(tmp_path / "cache"))


class _FakeStream:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise requests.HTTPError(f"HTTP {self.status}")

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for start in range(0, len(self.payload), chunk_size):
            yield self.payload[start : start + chunk_size]


def test_download_uses_share_credentials_and_is_atomic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeStream:
        calls.append({"url": url, **kwargs})
        return _FakeStream(b"x" * 10)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(htw, "ZIP_SIZE_BYTES", 10)
    target = tmp_path / "raw" / "htw.zip"
    assert htw.download_zip(target) == target
    assert target.read_bytes() == b"x" * 10
    assert calls[0]["url"] == htw.DOWNLOAD_URL
    assert calls[0]["auth"] == ("wLEKCqpAniw4TpQ", "profile")
    assert calls[0]["timeout"] == htw.HTTP_TIMEOUT_S
    assert sorted(p.name for p in target.parent.iterdir()) == ["htw.zip"]


def test_truncated_download_raises_and_leaves_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: _FakeStream(b"x" * 9))
    monkeypatch.setattr(htw, "ZIP_SIZE_BYTES", 10)
    target = tmp_path / "htw.zip"
    with pytest.raises(HtwError, match="9 statt 10 Bytes"):
        htw.download_zip(target)
    assert list(tmp_path.iterdir()) == []


def test_http_error_propagates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: _FakeStream(b"", status=404))
    with pytest.raises(requests.HTTPError):
        htw.download_zip(tmp_path / "htw.zip")
    assert list(tmp_path.iterdir()) == []


def test_default_zip_path_honours_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EEG_DATA_DIR", str(tmp_path))
    assert htw.default_zip_path() == tmp_path / "raw" / "htw" / htw.ZIP_NAME


def test_documented_range_is_consistent() -> None:
    assert htw.ANNUAL_KWH_MIN <= htw.PROFILE_1_ANNUAL_KWH <= htw.ANNUAL_KWH_MAX


# --- real download ---------------------------------------------------------------------


@pytest.mark.network
def test_network_profile_1_annual_consumption(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    zip_path = tmp_path / "raw" / htw.ZIP_NAME
    all_profiles = load_all_profiles(cache=cache, zip_path=zip_path)
    annual = all_profiles.sum()
    assert annual.min() >= htw.ANNUAL_KWH_MIN
    assert annual.max() <= htw.ANNUAL_KWH_MAX
    assert annual[profile_column(1)] == pytest.approx(htw.PROFILE_1_ANNUAL_KWH, abs=0.05)

    out = load_household(1, year=2023, cache=cache, zip_path=zip_path)
    validate_frame(out)
    total = float(out[COLUMN].sum())
    assert htw.ANNUAL_KWH_MIN <= total <= htw.ANNUAL_KWH_MAX
    # 2023 drops two source days and repeats two others -> close to the 2010 total
    assert total == pytest.approx(htw.PROFILE_1_ANNUAL_KWH, rel=0.02)
