# Fortschritt - Archiv

Ältere Einträge aus `progress.md`, neueste oben. Verschoben von `scripts/rotate_progress.py`. Bei Bedarf gezielt durchsuchen (grep), nicht komplett lesen.

---

## 2026-11-12 – F1-01 – Einheiten-Modul und Frame-Validierung
- Ergebnis: PASS (Evaluator: 1× FAIL, dann PASS; Reviewer OK)
- Gemacht: `src/eeg_optimizer/units.py` mit den NewTypes `KWh`, `KW`, `Eur`, `KgCO2`, `kw_to_kwh`/`kwh_to_kw` (Default 15 min, Intervall > 0) und `validate_frame` (`FrameValidationError`). Geprüft werden DatetimeIndex, tz Europe/Vienna, nicht leer, eindeutig, sortiert, 15-min-ausgerichtet, lückenlos, Spaltennamen eindeutig, Zahlenspalten mit Suffix, Suffix-Spalten numerisch (nicht bool/complex), kein NaN/inf, kein negatives `_kwh`. Die Meldungen nennen Spalte und ersten betroffenen Zeitstempel. `_ct_per_kwh` zählt nicht als Energie, negative Preise sind also erlaubt. `interval` muss 1 h teilen. Tests: `tests/unit/test_units.py` (inkl. DST-Tage 92/100), `test_units_typing.py` (mypy.api --strict auf Snippet), `test_units_architecture.py` (kein `* 0.25`/`/ 0.25` außerhalb units.py, laut architecture.md §3.1) und `tests/property/test_units_properties.py` (Roundtrip, Lücken- und Negativ-Erkennung über ganz 2023).
- Evaluator-FAIL behoben: Spalten mit Einheiten-Suffix und object/string-dtype wurden ungeprüft akzeptiert. Jetzt gilt: Suffix → numerischer dtype Pflicht.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit):
  - Zahlen-Strings (`"1"`, `"2"`), gemischte object-Spalten und kategoriale Spalten **ohne** Suffix werden noch akzeptiert. CSV-Quellen müssen Zahlen vor `validate_frame` parsen.
  - bool-Spalten ohne Suffix werden abgelehnt. Falls der Optimierer Flag-Spalten braucht, vorher entscheiden und architecture.md §3.1 ergänzen.
  - Die tz-Prüfung vergleicht `str(tz)`, deshalb wird dateutil-`Europe/Vienna` abgelehnt (zoneinfo/pytz gehen).
  - Der Architekturtest erkennt nur 0.25, nicht `/ 4` oder `/ 3600`.
  - Die zwei mypy-Läufe im Typing-Test kosten zusammen ca. 5 s.
- Fehlertypen: einheiten-suffix
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-12 – Harness – autoloop-Testlauf (1 Iteration)
- Ergebnis: REPARATUR (Harness)
- Gemacht: `scripts/autoloop.sh 1` lief gegen F1-01. Die headless Session meldete korrekt `LOOP_STATUS: FAIL F1-01`, ohne Codeänderung, mit sauberem Worktree. Ursache: Der Workspace war nicht vertraut, deshalb ignorierte Claude alle 53 Allow-Einträge aus `.claude/settings.json`. Behoben: Die Vorabprüfung in autoloop.sh liest `hasTrustDialogAccepted` aus `~/.claude.json`. Der Status wird nur noch aus JSON-Zeilen gelesen (die vorangestellte Warnzeile hatte jq gebrochen). Verweigerte Aufrufe stehen jetzt in `summary.log`. CLAUDE.md verlangt, Befehle nicht zu verketten.
- Neue Abhängigkeiten: –
- Offene Probleme: Der Workspace muss einmal interaktiv als vertraut bestätigt werden (Nutzer). Danach autoloop erneut mit 1 Iteration testen.
- Fehlertypen: headless-permissions
- Nächster Schritt: F1-01.

## 2026-11-12 – H-01 – Healthcheck-Seite (Harness-Probelauf)
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `health.collect_status()` (Version, Python, Solver, Ordner) mit Unit-Tests. Navigation per `st.navigation` mit den Seiten Start und Systemstatus (`/systemstatus`). Zwei E2E-Tests (Navigation, direkte URL). Evaluator-Minors behoben (Python-Version-Assert, unveränderliche Felder).
- Probelauf-Befunde am Harness, alle behoben und separat committet:
  1. jq-Filter für die Feature-Auswahl war falsch und lieferte keine Kandidaten. Ersetzt durch `scripts/next_feature.sh`.
  2. Umschreiben von `feature_list.json` per json.dump erzeugte riesige Diffs. Jetzt kanonisches Format über `scripts/set_passes.py`, vom Guard erzwungen.
  3. `pytest -m slow|network` endet mit rc=5, solange keine Tests existieren. Der Evaluator wertet das als n/a.
  4. Subagents, die in einer Session neu angelegt werden, sind erst in der nächsten Session verfügbar (betrifft nur das Setup).
  5. Der Lint-Hook greift live (RUF001 erkannt). Stop-Hook und Guard sind mit Negativfällen getestet.
- Neue Abhängigkeiten: –
- Offene Probleme (Reviewer minor/nit): `health.PROJECT_ROOT` setzt die editierbare Installation voraus (für dieses Projekt gegeben). `SystemStatus.folders` ist ein Mapping in einer frozen dataclass, also nur flach unveränderlich. CBC-Status erscheint auf der Statusseite doppelt (Metrik und Textzeile).
- Fehlertypen: harness-skript
- Nächster Schritt: F1-01 (Einheiten-Modul und Frame-Validierung).

## 2026-11-12 – Harness – Initiales Setup
- Ergebnis: HARNESS
- Gemacht: Spec und Architektur (`docs/`), Projektgerüst (pyproject, venv, ruff, mypy --strict, pytest, hypothesis, Playwright), `init.sh`, `scripts/check.sh`, `scripts/guard.py`, `scripts/autoloop.sh`, `feature_list.json`, Subagents (planner, evaluator, reviewer), Skills (start-session, finish-feature, debug, lessons-learned), `.claude/settings.json` mit Allowlist und Hooks.
- Neue Abhängigkeiten: `pulp>=2.9,<3` statt aktueller 4.x. Grund: PuLP 3/4 brechen die API und liefern CBC nicht mehr mit (siehe docs/architecture.md). Außerdem `pyarrow` für Parquet, `pandas-stubs`, `types-PyYAML` und `types-requests` für mypy, `pytest-timeout` gegen hängende Solver-Tests, `pytest-playwright` für E2E-Fixtures.
- Offene Probleme: –
- Fehlertypen: –
- Nächster Schritt: Probelauf mit H-01 (Healthcheck-Seite).
