---
name: start-session
description: Session-Start für den EEG-Optimizer. Liest den Übergabe-Stand, prüft den Zustand mit init.sh und wählt genau ein Feature aus. Immer zu Beginn einer Arbeits-Session verwenden.
---

# start-session (Session-Protokoll, Schritte 1–3)

## 1. Orientieren
- `progress.md` lesen, mindestens die letzten 3 Einträge.
- `git log --oneline -20` und `git status --short` ausführen. Uncommittete Änderungen aus einer früheren Session gelten als **roter Zustand**.
- `feature_list.json` lesen. Offen sind alle Features mit `passes: false`.

## 2. Zustand prüfen
- `./init.sh` ausführen, der Befehl ist idempotent.
- `scripts/check.sh` ausführen.
- **Bei rotem Zustand** (init.sh, check.sh oder ein uncommitteter Rest) gilt: **zuerst reparieren**, mit dem `debug`-Skill. Erst danach kommt ein neues Feature dran. Die Reparatur zählt als Arbeit dieser Session und wird committet und in progress.md eingetragen.
- Wenn init.sh wegen fehlender Systembibliotheken scheitert: abbrechen und den Nutzer bitten, die ausgegebene Meldung umzusetzen. Kein sudo.

## 3. Genau EIN Feature wählen
Die Auswahl erfolgt deterministisch. Mit folgendem Befehl die Kandidaten ermitteln:

```bash
jq -r '[.[] | select(.passes)] | map(.id) as $done
  | [ .[] | select(.passes|not) | select(all(.depends_on[]; . as $d | $done | index($d))) ]
  | sort_by(.priority) | .[0:3][] | "\(.priority)\t\(.id)\t\(.title)"' feature_list.json
```

- Es gewinnt der **erste** Eintrag: niedrigste `priority`, bei Gleichstand die Reihenfolge in der Datei.
- Ausgabe an den Nutzer: `Feature: <id> – <title>` sowie die acceptance-Kriterien als Checkliste.
- Ist das Feature zu groß für eine Session (Faustregel: mehr als ca. 300 geänderte Zeilen inkl. Tests), wird der `planner`-Subagent beauftragt, es zu zerlegen. Danach das erste Teil-Feature wählen.
- Gibt es keinen Kandidaten mehr, dies melden und die Session beenden.

Danach geht es weiter mit Schritt 4 (umsetzen inkl. Tests) und am Ende mit dem Skill `finish-feature`.
