"""Tests for the Parquet source cache (F1-03)."""

import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from eeg_optimizer.sources import cache as cache_mod
from eeg_optimizer.sources.cache import (
    DATA_DIR_ENV,
    Cache,
    CacheError,
    CacheMissError,
    data_dir,
    default_cache,
)
from eeg_optimizer.timeseries import year_index
from eeg_optimizer.units import validate_frame

REPO_ROOT = Path(__file__).resolve().parents[2]


def _frame() -> pd.DataFrame:
    # 2023-03-26 is the spring DST day in Vienna (92 intervals).
    index = pd.date_range("2023-03-26", "2023-03-27", freq="15min", tz="Europe/Vienna")[:-1]
    return pd.DataFrame(
        {
            "pv_kwh": [i * 0.01 for i in range(len(index))],
            "spot_ct_per_kwh": [-1.5 + i for i in range(len(index))],
        },
        index=index,
    )


class _Counter:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.calls = 0
        self.frame = frame

    def __call__(self) -> pd.DataFrame:
        self.calls += 1
        return self.frame


def test_fetch_called_only_once(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    fetch = _Counter(_frame())

    first = cache.get_or_fetch("pvgis", "linz_2023", fetch)
    second = cache.get_or_fetch("pvgis", "linz_2023", fetch)
    third = Cache(tmp_path).get_or_fetch("pvgis", "linz_2023", fetch)

    assert fetch.calls == 1
    assert (tmp_path / "pvgis" / "linz_2023.parquet").is_file()
    pd.testing.assert_frame_equal(first, second, check_freq=False)
    pd.testing.assert_frame_equal(first, third, check_freq=False)


def test_miss_and_hit_return_identical_frames(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    miss = cache.get_or_fetch("src", "k", _frame)
    hit = cache.get_or_fetch("src", "k", _fail_fetch)
    pd.testing.assert_frame_equal(miss, hit)  # check_freq=True by default


def test_different_keys_and_sources_are_separate(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    fetch = _Counter(_frame())
    cache.get_or_fetch("pvgis", "a", fetch)
    cache.get_or_fetch("pvgis", "b", fetch)
    cache.get_or_fetch("awattar", "a", fetch)
    assert fetch.calls == 3


def test_roundtrip_keeps_timezone_columns_and_values(tmp_path: Path) -> None:
    original = _frame()
    original.index.name = "start"
    cache = Cache(tmp_path)
    cache.get_or_fetch("src", "k", lambda: original)

    loaded = Cache(tmp_path).get_or_fetch("src", "k", _fail_fetch)

    assert isinstance(loaded.index, pd.DatetimeIndex)
    assert str(loaded.index.tz) == "Europe/Vienna"
    assert loaded.index.name == "start"
    assert list(loaded.columns) == ["pv_kwh", "spot_ct_per_kwh"]
    assert len(loaded) == 92
    pd.testing.assert_frame_equal(loaded, original, check_freq=False)
    validate_frame(loaded)


def test_roundtrip_full_year(tmp_path: Path) -> None:
    index = year_index(2023)
    original = pd.DataFrame({"load_kwh": [0.1] * len(index)}, index=index)
    cache = Cache(tmp_path)
    cache.get_or_fetch("synthetic", "y2023", lambda: original)
    loaded = cache.get_or_fetch("synthetic", "y2023", _fail_fetch)
    assert loaded.index.equals(index)
    validate_frame(loaded)


def test_roundtrip_utc_index_stays_utc(tmp_path: Path) -> None:
    index = pd.date_range("2023-01-01", periods=4, freq="h", tz="UTC")
    original = pd.DataFrame({"pv_kwh": [0.0, 1.0, 2.0, 3.0]}, index=index)
    cache = Cache(tmp_path)
    cache.get_or_fetch("pvgis", "raw", lambda: original)
    loaded = cache.get_or_fetch("pvgis", "raw", _fail_fetch)
    assert isinstance(loaded.index, pd.DatetimeIndex)
    assert str(loaded.index.tz) == "UTC"
    pd.testing.assert_frame_equal(loaded, original, check_freq=False)


def _fail_fetch() -> pd.DataFrame:
    raise AssertionError("fetch_fn must not be called on a cache hit")


def test_offline_cache_raises_on_miss(tmp_path: Path) -> None:
    cache = Cache(tmp_path, allow_fetch=False)
    with pytest.raises(CacheMissError, match="Nachladen ist deaktiviert"):
        cache.get_or_fetch("pvgis", "missing", _fail_fetch)


def test_injected_cache_reads_prepared_files_offline(tmp_path: Path) -> None:
    Cache(tmp_path).get_or_fetch("pvgis", "fixture", _frame)
    offline = Cache(tmp_path, allow_fetch=False)
    loaded = offline.get_or_fetch("pvgis", "fixture", _fail_fetch)
    assert list(loaded.columns) == ["pv_kwh", "spot_ct_per_kwh"]


def test_fetch_error_propagates_and_writes_nothing(tmp_path: Path) -> None:
    def boom() -> pd.DataFrame:
        raise RuntimeError("HTTP 503")

    cache = Cache(tmp_path)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        cache.get_or_fetch("awattar", "2023-01", boom)
    assert not cache.path("awattar", "2023-01").exists()


def test_non_dataframe_result_is_rejected(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    with pytest.raises(CacheError, match="statt DataFrame"):
        cache.get_or_fetch("src", "k", lambda: [1, 2, 3])  # type: ignore[arg-type,return-value]
    assert not cache.path("src", "k").exists()


def test_non_string_column_names_are_rejected(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    frame = pd.DataFrame({0: [1.0], "pv_kwh": [2.0]})
    with pytest.raises(CacheError, match="String-Spaltennamen"):
        cache.get_or_fetch("src", "k", lambda: frame)
    assert not cache.path("src", "k").exists()
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_failed_write_leaves_no_partial_file(tmp_path: Path) -> None:
    # Object column with mixed types: pyarrow fails while converting.
    frame = pd.DataFrame({"note": [1, "x"]})
    cache = Cache(tmp_path)
    with pytest.raises(pa.ArrowException):
        cache.get_or_fetch("src", "k", lambda: frame)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []


def test_corrupt_file_raises_clear_error(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    target = cache.path("src", "broken")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not a parquet file")
    with pytest.raises(CacheError, match="nicht lesbar"):
        cache.get_or_fetch("src", "broken", _fail_fetch)


@pytest.mark.parametrize(
    ("source", "key"),
    [
        ("../etc", "k"),
        ("src", "../../x"),
        ("src", "a/b"),
        ("src", ""),
        ("", "k"),
        (".hidden", "k"),
        ("src", "a..b"),
        ("src", "-x"),
        ("src", "a\\b"),
    ],
)
def test_unsafe_names_are_rejected(tmp_path: Path, source: str, key: str) -> None:
    cache = Cache(tmp_path)
    with pytest.raises(CacheError, match="Ungültiger Cache-"):
        cache.get_or_fetch(source, key, _fail_fetch)


def test_typical_keys_are_accepted(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    assert cache.path("pvgis", "48.30_14.29_10kwp_30deg_2023").name.endswith(".parquet")
    assert cache.path("awattar", "2023-01").parent == tmp_path / "awattar"


def test_data_dir_default_is_project_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert data_dir() == REPO_ROOT / "data"
    assert default_cache().directory == REPO_ROOT / "data" / "cache"


def test_data_dir_empty_env_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, "")
    assert data_dir() == REPO_ROOT / "data"


def test_data_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    assert data_dir() == tmp_path
    cache = default_cache()
    assert cache.directory == tmp_path / "cache"
    cache.get_or_fetch("pvgis", "k", _frame)
    assert (tmp_path / "cache" / "pvgis" / "k.parquet").is_file()


def test_project_root_points_to_repo() -> None:
    assert (cache_mod.PROJECT_ROOT / "pyproject.toml").is_file()


def test_cache_files_are_git_ignored() -> None:
    git = shutil.which("git")
    assert git is not None, "git is required for this repository"
    result = subprocess.run(
        [git, "check-ignore", "data/cache/x.parquet", "data/cache/pvgis/linz.parquet"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["data/cache/x.parquet", "data/cache/pvgis/linz.parquet"]
