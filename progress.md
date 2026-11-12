# Fortschritt / Übergabe-Log

Neueste Einträge **oben**. Ein Eintrag pro Session. Vorlage:

```
## YYYY-MM-DD – <Feature-ID> – <Titel>
- Ergebnis: PASS | FAIL | REPARATUR | HARNESS
- Gemacht: …
- Neue Abhängigkeiten: – | <paket> (Begründung)
- Offene Probleme: – | …
- Nächster Schritt: …
```

---

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
- Nächster Schritt: F1-01 (Einheiten-Modul und Frame-Validierung).

## 2026-11-12 – Harness – Initiales Setup
- Ergebnis: HARNESS
- Gemacht: Spec und Architektur (`docs/`), Projektgerüst (pyproject, venv, ruff, mypy --strict, pytest, hypothesis, Playwright), `init.sh`, `scripts/check.sh`, `scripts/guard.py`, `scripts/autoloop.sh`, `feature_list.json`, Subagents (planner, evaluator, reviewer), Skills (start-session, finish-feature, debug, lessons-learned), `.claude/settings.json` mit Allowlist und Hooks.
- Neue Abhängigkeiten: `pulp>=2.9,<3` statt aktueller 4.x. Grund: PuLP 3/4 brechen die API und liefern CBC nicht mehr mit (siehe docs/architecture.md). Außerdem `pyarrow` für Parquet, `pandas-stubs`, `types-PyYAML` und `types-requests` für mypy, `pytest-timeout` gegen hängende Solver-Tests, `pytest-playwright` für E2E-Fixtures.
- Offene Probleme: –
- Nächster Schritt: Probelauf mit H-01 (Healthcheck-Seite).
