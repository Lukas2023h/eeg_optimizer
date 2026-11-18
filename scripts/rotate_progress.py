"""Keep progress.md short: move all but the newest N session entries to docs/progress_archive.md.

Entries are the `## ` sections after the `---` line below the template. Nothing is lost;
the archive holds the older entries, newest first. Idempotent: does nothing if there are
at most N entries.

Usage: .venv/bin/python scripts/rotate_progress.py [N]   (default 5)
"""

from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS = ROOT / "progress.md"
ARCHIVE = ROOT / "docs" / "progress_archive.md"
SEPARATOR = "\n---\n"
ENTRY_START = re.compile(r"^## \d{4}-\d{2}-\d{2} ", re.MULTILINE)
ARCHIVE_HEADER = (
    "# Fortschritt - Archiv\n\n"
    "Ältere Einträge aus `progress.md`, neueste oben. Verschoben von "
    "`scripts/rotate_progress.py`. Bei Bedarf gezielt durchsuchen (grep), nicht komplett lesen.\n"
)
DEFAULT_KEEP = 5


def split_entries(body: str) -> list[str]:
    """Split the text after the separator into stripped `## ` entries."""
    starts = [m.start() for m in ENTRY_START.finditer(body)]
    if starts and body[: starts[0]].strip():
        raise SystemExit("progress.md: Text zwischen '---' und dem ersten Eintrag, Abbruch.")
    bounds = [*starts, len(body)]
    return [body[a:b].strip() for a, b in itertools.pairwise(bounds)]


def rotate(keep: int) -> int:
    text = PROGRESS.read_text(encoding="utf-8")
    if SEPARATOR not in text:
        raise SystemExit("progress.md: Trennlinie '---' nach der Vorlage fehlt, Abbruch.")
    head, body = text.split(SEPARATOR, 1)
    entries = split_entries(body)
    if len(entries) <= keep:
        return 0
    kept, moved = entries[:keep], entries[keep:]

    if ARCHIVE.exists():
        archive_text = ARCHIVE.read_text(encoding="utf-8")
        if SEPARATOR not in archive_text:
            raise SystemExit(f"{ARCHIVE.name}: Trennlinie '---' fehlt, Abbruch.")
        archive_head, archive_body = archive_text.split(SEPARATOR, 1)
        old_archived = split_entries(archive_body)
    else:
        archive_head, old_archived = ARCHIVE_HEADER, []

    # Write the archive first: if anything fails afterwards, entries are duplicated, never lost.
    ARCHIVE.write_text(
        archive_head + SEPARATOR + "\n" + "\n\n".join(moved + old_archived) + "\n", encoding="utf-8"
    )
    PROGRESS.write_text(head + SEPARATOR + "\n" + "\n\n".join(kept) + "\n", encoding="utf-8")
    return len(moved)


def main() -> None:
    keep = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_KEEP
    if keep < 1:
        raise SystemExit("N muss mindestens 1 sein.")
    moved = rotate(keep)
    print(f"progress.md: {moved} Eintrag/Einträge ins Archiv verschoben, {keep} behalten.")


if __name__ == "__main__":
    main()
