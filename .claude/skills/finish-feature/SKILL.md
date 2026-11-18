---
name: finish-feature
description: Abschluss eines Features im EEG-Optimizer. Führt Tests aus, lässt Evaluator und Reviewer prüfen, setzt passes nur bei PASS, committet und schreibt einen progress.md-Eintrag. Verwenden, wenn ein Feature umgesetzt ist.
---

# finish-feature (Session-Protokoll, Schritte 5–7)

Voraussetzung: Das Feature `<id>` ist umgesetzt, inklusive Tests.

## 1. Eigene Prüfung
- `scripts/check.sh` muss grün sein. Hat das Feature eine UI-Komponente: `scripts/check.sh --e2e`.
- Bei Performance-Bezug zusätzlich `.venv/bin/pytest -q -m slow`.
- Ist etwas rot, zuerst reparieren. Tests werden nie abgeschwächt, übersprungen oder gelöscht.

## 2. Evaluator
- Subagent `evaluator` mit diesem Prompt aufrufen: `Prüfe Feature <id> aus feature_list.json. Server: ./init.sh.`
- Bei `VERDICT: FAIL`: die Befunde beheben und erneut prüfen lassen. Nach **3 FAIL in Folge** wird gestoppt. `passes` bleibt dann `false`, der Stand wird lauffähig committet (Teilfortschritt), und progress.md bekommt den Eintrag `Ergebnis: FAIL` mit den offenen Befunden.

## 3. Reviewer (nur nach PASS)
- Subagent `reviewer` aufrufen.
- Punkte der Stufen `blocker` und `major` werden behoben, danach `scripts/check.sh` erneut ausführen. Bei nicht-trivialen Änderungen den Evaluator noch einmal starten.
- `minor` und `nit` dürfen offen bleiben, wandern dann aber in progress.md unter „Offene Probleme“.

## 4. Abschluss
1. `passes` setzen, **nur** mit `.venv/bin/python scripts/set_passes.py <id>`. Die Datei nie von Hand oder per json.dump umschreiben.
2. `.venv/bin/python scripts/guard.py` muss ok sein.
3. Einen Eintrag **oben** in `progress.md` ergänzen, nach der Vorlage in der Datei: Datum (`date +%F`), Feature-ID, Ergebnis PASS/FAIL, was gemacht wurde, neue Abhängigkeiten mit Begründung, offene Probleme, nächster Schritt. Knapp halten: Details gehören in Docstrings oder `docs/`, nicht ins Log. Außerdem die Zeile `- Fehlertypen:` mit allgemeinen kebab-case-Tags (vorhandene wiederverwenden) für jeden Evaluator-FAIL, jeden Reviewer-Punkt der Stufe blocker/major, jeden eigenen Bug und jede Abweichung von Spec/Architektur. Gab es nichts davon: `- Fehlertypen: –`.
   - **Offene Punkte pflegen** (Abschnitt oben in progress.md, wird nie archiviert): Offene Probleme, die ein späteres Feature betreffen (Adapter, Spec-Änderung für den planner, Fallstrick für neue Quellen), dort mit `(<id>)` eintragen. Punkte, die dieses Feature erledigt hat, dort löschen. Reine Kleinigkeiten bleiben nur im Eintrag.
   - `.venv/bin/python scripts/error_tags.py` ausführen. Bei Exit 1 zuerst den Skill `lessons-learned` für die genannten Tags anwenden.
   - `.venv/bin/python scripts/rotate_progress.py` ausführen. Es verschiebt alle Einträge außer den 5 neuesten nach `docs/progress_archive.md`.
4. Commit:
   ```bash
   git add -A && git commit -m "feat(<id>): <title>" -m "Evaluator: PASS. <kurz>"   # + Attribution-Zeile laut Harness-Vorgabe
   ```
   Bei FAIL lautet der Präfix `wip(<id>):`. Kein Push.
5. `git status --short` muss leer sein.
6. Als **letzte Zeile** der Antwort genau eine dieser Zeilen ausgeben, `scripts/autoloop.sh` wertet sie aus:
   - `LOOP_STATUS: PASS <id>`
   - `LOOP_STATUS: FAIL <id>`
