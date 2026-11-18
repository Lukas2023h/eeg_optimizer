"""Find repeated error types that still lack a lesson (input for the lessons-learned skill).

Each progress entry has a line `- Fehlertypen: tag-a, tag-b` (or a dash for none). Tags are
counted over progress.md AND docs/progress_archive.md, so rotation never hides a repeat.
A tag is covered once a line under "## Gelernte Regeln" in CLAUDE.md contains `[tag]`.

Exit code 1 if a tag occurs in at least 2 entries and is not covered, else 0.
Usage: .venv/bin/python scripts/error_tags.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = (ROOT / "progress.md", ROOT / "docs" / "progress_archive.md")
CLAUDE_MD = ROOT / "CLAUDE.md"
TAG_LINE = re.compile(r"^- Fehlertypen:(.*)$", re.MULTILINE)
TAG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NONE_MARKERS = {"", "-", chr(0x2013)}  # en dash, as in the template


def count_tags() -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in SOURCES:
        if not path.exists():
            continue
        # Only the entries below the separator; the header holds the template.
        _, _, entries = path.read_text(encoding="utf-8").partition("\n---\n")
        for match in TAG_LINE.finditer(entries):
            raw = match.group(1).strip()
            if raw in NONE_MARKERS:
                continue
            tags = {t.strip() for t in raw.split(",")}
            bad = sorted(t for t in tags if not TAG.match(t))
            if bad:
                raise SystemExit(f"{path.name}: ungültige Fehlertypen {bad} (nur kebab-case).")
            counts.update(tags)
    return counts


def covered_tags() -> set[str]:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    _, _, rules = text.partition("## Gelernte Regeln")
    return set(re.findall(r"\[([a-z0-9-]+)\]", rules))


def main() -> None:
    counts = count_tags()
    covered = covered_tags()
    missing = [(tag, n) for tag, n in counts.most_common() if n >= 2 and tag not in covered]
    for tag, n in counts.most_common():
        state = "abgesichert" if tag in covered else ("LESSON NÖTIG" if n >= 2 else "einmalig")
        print(f"{n}x  {tag:<32} {state}")
    if not counts:
        print("Keine Fehlertypen erfasst.")
    if missing:
        print("\nSkill lessons-learned anwenden für: " + ", ".join(t for t, _ in missing))
        sys.exit(1)


if __name__ == "__main__":
    main()
