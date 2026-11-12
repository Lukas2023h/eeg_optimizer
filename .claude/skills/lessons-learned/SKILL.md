---
name: lessons-learned
description: Verhindert Wiederholungsfehler im EEG-Optimizer. Wenn ein Fehlertyp zum zweiten Mal auftritt, wird er dauerhaft abgesichert, durch eine Lint-Regel, einen Test, eine CLAUDE.md-Regel oder einen Skill. Verwenden, sobald progress.md denselben Fehler zweimal zeigt oder der Evaluator denselben Befund wiederholt.
---

# lessons-learned

Auslöser: Derselbe Fehlertyp tritt zum zweiten Mal auf. Das kann in progress.md, in Evaluator-Befunden oder im Debugging sichtbar werden.

1. **Fehlertyp benennen**, allgemein statt konkret. Beispiel: „kW als kWh summiert“ statt „Bug in balance.py Zeile 40“.
2. **Die stärkste mögliche Absicherung wählen**, in dieser Reihenfolge:
   1. **Automatisch prüfbar → Lint-Regel oder Test.** Eine ruff-Regel in `pyproject.toml` aktivieren oder einen Architektur-Test in `tests/unit/` schreiben, der Code-Muster prüft (Vorbild: `test_no_raw_interval_factor_outside_units`). Der Test muss am alten Fehler rot gewesen sein.
   2. **Ablauf-Fehler → Skill anpassen.** Die betroffene `.claude/skills/*/SKILL.md` um einen konkreten Schritt ergänzen.
   3. **Nur als Wissen fassbar → CLAUDE.md.** Unter „Gelernte Regeln“ **eine** Zeile ergänzen: `- <Regel> (Grund: <Fehlertyp>, <Datum>)`. CLAUDE.md bleibt unter ca. 100 Zeilen. Ist es zu voll, Regeln zusammenfassen oder in Tests überführen.
3. `scripts/check.sh` ausführen, alles muss grün sein.
4. Im progress.md-Eintrag vermerken: `Lesson: <Fehlertyp> → <Absicherung>`.

Änderungen an `.claude/settings.json`, `.claude/agents/` oder `.claude/hooks/` werden nur vorgeschlagen. Sie brauchen die Freigabe des Nutzers.
