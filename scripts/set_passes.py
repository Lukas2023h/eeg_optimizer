"""Set `passes` of one feature in feature_list.json, keeping the canonical format.

Usage: python scripts/set_passes.py <ID> [true|false]   (default: true)
       python scripts/set_passes.py --format            (rewrite canonically, no changes)
Canonical format = json.dumps(indent=2, ensure_ascii=False) + newline; guard.py enforces it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PATH = Path(__file__).resolve().parents[1] / "feature_list.json"


def dump(features: list[dict[str, Any]]) -> str:
    return json.dumps(features, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str]) -> int:
    features: list[dict[str, Any]] = json.loads(PATH.read_text())
    if argv == ["--format"]:
        PATH.write_text(dump(features))
        return 0
    if not argv or len(argv) > 2 or (len(argv) == 2 and argv[1] not in {"true", "false"}):
        print(__doc__, file=sys.stderr)
        return 2
    fid, value = argv[0], (argv[1] if len(argv) == 2 else "true") == "true"
    matches = [f for f in features if f["id"] == fid]
    if not matches:
        print(f"Unbekannte Feature-ID: {fid}", file=sys.stderr)
        return 1
    matches[0]["passes"] = value
    PATH.write_text(dump(features))
    print(f"{fid}: passes={str(value).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
