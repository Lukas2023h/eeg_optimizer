"""Parquet cache for downloaded source data.

Files live at ``<cache_dir>/<source>/<key>.parquet``. The default cache directory is
``<data_dir>/cache`` where ``data_dir`` is ``$EEG_DATA_DIR`` or ``data/`` in the project
root. Tests inject a :class:`Cache` pointing at ``tmp_path`` or ``tests/fixtures/`` and
may set ``allow_fetch=False`` so that a cache miss fails instead of touching the network.

Parquet keeps the index (including its time zone), the index name and the column
names. It does not keep the index ``freq``; callers that need it must set it again.
"""

import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

import pandas as pd

# Assumes the editable install (pip install -e .), as set up by init.sh.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR_ENV = "EEG_DATA_DIR"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

# One path component, no hidden files, no separators, no "..".
_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")

FetchFn = Callable[[], pd.DataFrame]


class CacheError(Exception):
    """Invalid cache name, unreadable cache file or unusable fetch result."""


class CacheMissError(CacheError):
    """The entry is not cached and fetching is disabled."""


def data_dir() -> Path:
    """Data directory: ``$EEG_DATA_DIR`` if set and non-empty, else ``data/`` in the project.

    A relative ``$EEG_DATA_DIR`` is resolved against the current working directory.
    """
    override = os.environ.get(DATA_DIR_ENV, "")
    return Path(override) if override else DEFAULT_DATA_DIR


def default_cache() -> "Cache":
    """Cache under ``<data_dir>/cache`` (evaluated at call time, so the env var is honoured)."""
    return Cache(data_dir() / "cache")


def _check_name(kind: str, name: str) -> None:
    if not _NAME_RE.fullmatch(name) or ".." in name:
        raise CacheError(
            f"Ungültiger Cache-{kind} {name!r}: erlaubt sind Buchstaben, Ziffern, '_', '-', '.' "
            "(kein Pfadtrenner, kein '..', nicht mit '.' oder '-' beginnend)"
        )


class Cache:
    """Parquet cache keyed by ``(source, key)``."""

    def __init__(self, directory: Path, *, allow_fetch: bool = True) -> None:
        self.directory = Path(directory)
        self.allow_fetch = allow_fetch

    def path(self, source: str, key: str) -> Path:
        """File path of an entry; validates both names."""
        _check_name("Quelle", source)
        _check_name("Schlüssel", key)
        return self.directory / source / f"{key}.parquet"

    def get_or_fetch(self, source: str, key: str, fetch_fn: FetchFn) -> pd.DataFrame:
        """Return the cached frame, or call ``fetch_fn`` once, store its result and return it."""
        target = self.path(source, key)
        if target.exists():
            return self._read(target)
        if not self.allow_fetch:
            raise CacheMissError(f"Kein Cache-Eintrag {target} und Nachladen ist deaktiviert")
        frame = fetch_fn()
        if not isinstance(frame, pd.DataFrame):
            raise CacheError(
                f"fetch_fn für {source}/{key} lieferte {type(frame).__name__} statt DataFrame"
            )
        self._write(target, frame)
        # Read back so a miss returns exactly what later hits return (e.g. no index freq).
        return self._read(target)

    @staticmethod
    def _read(target: Path) -> pd.DataFrame:
        try:
            return pd.read_parquet(target)
        except (OSError, ValueError) as exc:
            raise CacheError(
                f"Cache-Datei {target} ist nicht lesbar ({exc}). Datei löschen, um neu zu laden."
            ) from exc

    @staticmethod
    def _write(target: Path, frame: pd.DataFrame) -> None:
        bad = [c for c in frame.columns if not isinstance(c, str)]
        if bad:
            raise CacheError(f"Parquet braucht String-Spaltennamen, gefunden: {bad!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file and rename, so an interrupted write never leaves a
        # truncated file that would later be read as a valid cache entry.
        # mkstemp gives a unique name per write (Streamlit sessions are threads in one process).
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            frame.to_parquet(tmp)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
