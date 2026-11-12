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

## 2026-11-12 – Harness – Initiales Setup
- Ergebnis: HARNESS
- Gemacht: Spec und Architektur (`docs/`), Projektgerüst (pyproject, venv, ruff, mypy --strict, pytest, hypothesis, Playwright), `init.sh`, `scripts/check.sh`, `scripts/guard.py`, `scripts/autoloop.sh`, `feature_list.json`, Subagents (planner, evaluator, reviewer), Skills (start-session, finish-feature, debug, lessons-learned), `.claude/settings.json` mit Allowlist und Hooks.
- Neue Abhängigkeiten: `pulp>=2.9,<3` statt aktueller 4.x. Grund: PuLP 3/4 brechen die API und liefern CBC nicht mehr mit (siehe docs/architecture.md). Außerdem `pyarrow` für Parquet, `pandas-stubs`, `types-PyYAML` und `types-requests` für mypy, `pytest-timeout` gegen hängende Solver-Tests, `pytest-playwright` für E2E-Fixtures.
- Offene Probleme: –
- Nächster Schritt: Probelauf mit H-01 (Healthcheck-Seite).
