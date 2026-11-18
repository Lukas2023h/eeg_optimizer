---
name: start-session
description: Session-Start für den EEG-Optimizer. Liest den Übergabe-Stand, prüft den Zustand mit init.sh und wählt genau ein Feature aus. Immer zu Beginn einer Arbeits-Session verwenden.
---

# start-session (Session-Protokoll, Schritte 1–3)

## 1. Orientieren
- `progress.md` komplett lesen. Der Abschnitt **„Offene Punkte“** gilt dauerhaft: Betrifft ein Punkt das gewählte Feature, wird er mit erledigt. Die Datei enthält nur die neuesten Einträge. Ältere stehen in `docs/progress_archive.md`. Das Archiv wird **nicht** komplett gelesen, sondern gezielt mit dem Grep-Tool durchsucht, wenn ein Eintrag auf ältere Sessions verweist oder ein Fehler bekannt vorkommt.
- `git log --oneline -20` und `git status --short` ausführen. Uncommittete Änderungen aus einer früheren Session gelten als **roter Zustand**.
- `feature_list.json` **nicht** komplett lesen (zu groß). Das gewählte Feature zeigt `scripts/show_feature.sh` in Schritt 3.

## 2. Zustand prüfen
- `./init.sh` ausführen, der Befehl ist idempotent.
- `scripts/check.sh` ausführen.
- **Bei rotem Zustand** (init.sh, check.sh oder ein uncommitteter Rest) gilt: **zuerst reparieren**, mit dem `debug`-Skill. Erst danach kommt ein neues Feature dran. Die Reparatur zählt als Arbeit dieser Session und wird committet und in progress.md eingetragen.
- Wenn init.sh wegen fehlender Systembibliotheken scheitert: abbrechen und den Nutzer bitten, die ausgegebene Meldung umzusetzen. Kein sudo.

## 3. Genau EIN Feature wählen
Die Auswahl erfolgt deterministisch: `scripts/next_feature.sh`.

- Es gewinnt die **erste** Zeile: niedrigste `priority`, alle `depends_on` erfüllt, bei Gleichstand die Reihenfolge in der Datei. Nicht davon abweichen.
- `scripts/show_feature.sh` ausführen (ohne ID = erste Zeile von next_feature.sh). Es gibt Beschreibung, Abhängigkeiten und acceptance-Kriterien als Checkliste aus. Diese Ausgabe an den Nutzer weitergeben. Einen anderen Eintrag zeigt `scripts/show_feature.sh <ID>`.
- Den Abschnitt „Wird gebraucht von“ beachten: Bevor eine öffentliche Funktion oder ein Datenformat festgelegt wird, für die direkt abhängigen Features `scripts/show_feature.sh <ID>` ansehen, damit die Schnittstelle zu ihnen passt. Das Feature bleibt trotzdem auf seine eigenen acceptance-Kriterien begrenzt.
- Ist das Feature zu groß für eine Session (Faustregel: mehr als ca. 300 geänderte Zeilen inkl. Tests), wird der `planner`-Subagent beauftragt, es zu zerlegen. Danach das erste Teil-Feature wählen.
- Gibt es keinen Kandidaten mehr, dies melden und die Session beenden.

Danach geht es weiter mit Schritt 4 (umsetzen inkl. Tests) und am Ende mit dem Skill `finish-feature`.
