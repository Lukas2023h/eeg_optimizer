"""Static typing guard for INV-4: mypy --strict must reject mixing KW and KWh (F1-01)."""

import textwrap
from pathlib import Path

from mypy import api as mypy_api

_SNIPPET = textwrap.dedent(
    """
    from eeg_optimizer.units import KW, KWh, kw_to_kwh


    def needs_energy(e: KWh) -> KWh:
        return e


    needs_energy(KW(4.0))        # line 9: power where energy is expected
    needs_energy(kw_to_kwh(KW(4.0)))  # ok: explicit conversion
    kw_to_kwh(KWh(1.0))          # line 11: energy where power is expected
    """
)


def _run_mypy(source: str, tmp_path: Path) -> tuple[str, int]:
    snippet = tmp_path / "snippet.py"
    snippet.write_text(source)
    stdout, stderr, status = mypy_api.run(
        [
            "--strict",
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            "--config-file",
            "",
            str(snippet),
        ]
    )
    return stdout + stderr, status


def test_mypy_rejects_kw_where_kwh_expected(tmp_path: Path) -> None:
    output, status = _run_mypy(_SNIPPET, tmp_path)
    assert status == 1, output
    assert 'snippet.py:9: error: Argument 1 to "needs_energy" has incompatible type "KW"; ' in (
        output
    )
    assert 'expected "KWh"' in output
    assert 'snippet.py:11: error: Argument 1 to "kw_to_kwh" has incompatible type "KWh"; ' in (
        output
    )
    assert "Found 2 errors" in output


def test_mypy_accepts_explicit_conversion(tmp_path: Path) -> None:
    ok = "from eeg_optimizer.units import KW, KWh, kw_to_kwh\n\ne: KWh = kw_to_kwh(KW(4.0))\n"
    output, status = _run_mypy(ok, tmp_path)
    assert status == 0, output
