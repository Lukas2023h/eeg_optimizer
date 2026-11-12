# EEG-Optimizer

Lokales Tool, das für eine kleine österreichische Energiegemeinschaft (3–15 Haushalte, gemeinsame PV) den Ist-Eigenverbrauch berechnet. Es plant verschiebbare Verbraucher (E-Auto, Boiler, Wärmepumpe, Waschmaschine …) optimal ein, mit perfekter Voraussicht und damit als Obergrenze. Die Wirkung wird in kWh, € und kg CO₂ ausgewiesen.
Spec: `docs/spec.md` (Features, Nicht-Ziele, **Invarianten §5**, **Annahmen §7**). Architektur: `docs/architecture.md`.
Arbeitsliste: `feature_list.json`. Übergabe: `progress.md`.

## Befehle
| Zweck | Befehl |
|---|---|
| Setup + Server + Smoke-Test (idempotent) | `./init.sh` (stoppen: `./init.sh stop`) |
| Alle Offline-Checks | `scripts/check.sh` (`--e2e` mit Playwright, `--all` mit Perf) |
| Unit- + Property-Tests | `.venv/bin/pytest -q` |
| E2E (Playwright) | `.venv/bin/pytest -q -m e2e` |
| Performance (INV-5) | `.venv/bin/pytest -q -m slow` |
| Echte APIs | `.venv/bin/pytest -q -m network` |
| Lint / Format | `.venv/bin/ruff check .` / `.venv/bin/ruff format .` |
| Typecheck | `.venv/bin/mypy` (strict, Konfiguration in pyproject) |
| Harness-Regeln prüfen | `.venv/bin/python scripts/guard.py` |
| App manuell | `.venv/bin/streamlit run app/streamlit_app.py` → http://localhost:8501 |
| Build | entfällt (Python, Installation per `pip install -e ".[dev]"` aus `init.sh`) |

## Struktur
`src/eeg_optimizer/` Fachlogik (units, timeseries, config, sources/, balance, allocation, devices, optimize/, economics, compare) · `app/` Streamlit (Seiten in `app/pages/`) · `tests/{unit,property,perf,e2e,fixtures}` · `config/` YAML · `scripts/` · `data/`, `logs/` gitignored.

## Konventionen
- Code, Bezeichner und Commits auf Englisch. UI-Texte und Doku auf Deutsch.
- **Einheiten (INV-4):** Jede numerische Spalte endet auf `_kwh`, `_kw`, `_eur`, `_ct_per_kwh`, `_g_per_kwh` oder `_kg_co2`. Skalare nutzen die NewTypes aus `units.py`. kW↔kWh wird **nur** über `units.kw_to_kwh`/`kwh_to_kw` umgerechnet.
- Zeitreihen: 15-min-Raster, tz `Europe/Vienna`, Zeitstempel = Intervallbeginn, `units.validate_frame` an Modulgrenzen.
- Tests offline. Nur `@pytest.mark.network` darf echte APIs aufrufen. Fixtures liegen in `tests/fixtures/` und sind klein.
- Invarianten aus spec §5 werden mit hypothesis-Property-Tests in `tests/property/` abgesichert.
- Fehler nie verschlucken: kein `except: pass`, kein stiller Default. Ein Rückfall wird gezählt und angezeigt.
- PuLP ist auf 2.9.x gepinnt, also gilt die klassische API (`LpProblem`, `LpVariable`, `PULP_CBC_CMD`).
- UI-Elemente bekommen stabile Labels bzw. `key`s, damit Playwright sie findet.

## Session-Protokoll (immer so)
1. **Orientieren:** `progress.md` + `git log --oneline -20` + `feature_list.json` lesen (Skill `start-session`).
2. **Zustand prüfen:** `./init.sh` + `scripts/check.sh`. Ist etwas rot, **zuerst reparieren** (Skill `debug`).
3. **GENAU EIN Feature wählen:** niedrigste `priority`, alle `depends_on` erfüllt, bei Gleichstand die Reihenfolge in der Datei.
4. **Umsetzen inkl. Tests** (Unit, Property für Invarianten, E2E bei UI).
5. **Evaluator-Subagent** prüfen lassen (Skill `finish-feature`).
6. **Nur bei `VERDICT: PASS`** `passes: true` setzen.
7. **Committen** und `progress.md` oben ergänzen. Am Ende ist `git status` leer.

## Harte Regeln
- In `feature_list.json` **nur** das Feld `passes` ändern. Einträge nie löschen oder umschreiben. Neue Einträge legt nur der `planner` an. `scripts/guard.py` erzwingt das.
- **Keine Tests löschen oder abschwächen** (kein skip/xfail, keine größeren Toleranzen), um grün zu werden. Der Guard zählt die Testfunktionen.
- Keine neuen Dependencies ohne Begründung in `progress.md`. Danach den Lockfile aktualisieren: `.venv/bin/pip freeze --exclude-editable > requirements.lock`.
- Nie „fertig“ melden ohne in dieser Session ausgeführte Tests mit sichtbarer Ausgabe.
- Am Session-Ende ist der Stand lauffähig (`./init.sh` grün) und committet. Kein Push, kein Force, kein sudo.
- Echte Verbrauchsdaten nur unter `data/`. Sie kommen nie ins Repo und nie in `tests/fixtures/`.
- Ein Rückfall-Block im Standardszenario (Beispielgemeinschaft 2023) ist ein Fehler, kein Detail.

## Gelernte Regeln
<!-- Skill lessons-learned ergänzt hier je eine Zeile: - <Regel> (Grund: <Fehlertyp>, <Datum>) -->
