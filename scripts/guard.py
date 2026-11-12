"""Harness guard: enforce hard rules against the last commit (HEAD).

1. feature_list.json: existing entries may only change the field `passes`; no entry may be
   removed; new entries must be well-formed with `passes: false`.
2. Tests: the number of test functions must not decrease.
3. A feature may only be marked `passes: true` if all its `depends_on` pass.

Exit code 1 with a message per violation. Usage: python scripts/guard.py [--base REV]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"id", "title", "description", "priority", "depends_on", "acceptance", "passes"}
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+", re.MULTILINE)


def git_show(rev: str, path: str) -> str | None:
    res = subprocess.run(
        ["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return res.stdout if res.returncode == 0 else None


def git_files(rev: str, prefix: str) -> list[str]:
    res = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", rev, prefix],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return res.stdout.split() if res.returncode == 0 else []


def check_features(base: str) -> list[str]:
    errors: list[str] = []
    current: list[dict[str, Any]] = json.loads((ROOT / "feature_list.json").read_text())
    ids = [f.get("id") for f in current]
    if len(ids) != len(set(ids)):
        errors.append("feature_list.json: doppelte IDs")
    by_id = {f["id"]: f for f in current}
    for f in current:
        missing = REQUIRED - f.keys()
        if missing:
            errors.append(f"{f.get('id')}: fehlende Felder {sorted(missing)}")
        for dep in f.get("depends_on", []):
            if dep not in by_id:
                errors.append(f"{f['id']}: unbekannte Abhängigkeit {dep}")
            elif f.get("passes") and not by_id[dep].get("passes"):
                errors.append(f"{f['id']}: passes=true, aber Abhängigkeit {dep} nicht erfüllt")

    old_raw = git_show(base, "feature_list.json")
    if old_raw is None:
        return errors
    old = {f["id"]: f for f in json.loads(old_raw)}
    for fid, old_f in old.items():
        new_f = by_id.get(fid)
        if new_f is None:
            errors.append(f"{fid}: Eintrag gelöscht (verboten)")
            continue
        for key in old_f.keys() | new_f.keys():
            if key != "passes" and old_f.get(key) != new_f.get(key):
                errors.append(f"{fid}: Feld '{key}' geändert (nur 'passes' erlaubt)")
    for fid in by_id.keys() - old.keys():
        if by_id[fid].get("passes") is not False:
            errors.append(f"{fid}: neuer Eintrag muss passes=false haben")
    return errors


def count_tests_worktree() -> int:
    return sum(len(TEST_DEF.findall(p.read_text())) for p in (ROOT / "tests").rglob("test_*.py"))


def count_tests_rev(rev: str) -> int:
    total = 0
    for path in git_files(rev, "tests"):
        if Path(path).name.startswith("test_") and path.endswith(".py"):
            total += len(TEST_DEF.findall(git_show(rev, path) or ""))
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD")
    args = parser.parse_args()
    errors = check_features(args.base)
    before, now = count_tests_rev(args.base), count_tests_worktree()
    if now < before:
        errors.append(f"Anzahl Testfunktionen gesunken: {before} -> {now} (Tests nie löschen)")
    for e in errors:
        print(f"GUARD: {e}", file=sys.stderr)
    if not errors:
        print(f"guard ok ({now} Testfunktionen)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
