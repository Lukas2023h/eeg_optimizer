# Fortschritt / Übergabe-Log

Neueste Einträge **oben**. Ein Eintrag pro Session. Vorlage:

```
## YYYY-MM-DD – <Feature-ID> – <Titel>
- Ergebnis: PASS | FAIL | REPARATUR | HARNESS
- Gemacht: …
- Neue Abhängigkeiten: – | <paket> (Begründung)
- Offene Probleme: – | …
- Fehlertypen: – | <tag-in-kebab-case>, … (Evaluator-FAIL, Reviewer blocker/major, eigener Bug, Abweichung von Spec/Architektur)
- Nächster Schritt: …
```

Fehlertypen allgemein benennen (`einheiten-suffix`, nicht `bug-in-timeseries`) und vorhandene Tags wiederverwenden: `.venv/bin/python scripts/error_tags.py` zeigt alle Tags aus progress.md und Archiv.

## Offene Punkte (bleiben hier, bis sie erledigt sind)

Wird von `scripts/rotate_progress.py` nie archiviert. Nur Punkte, die spätere Features betreffen. Erledigte Punkte löschen, neue mit Quelle `(Fx-yy)` ergänzen.

**Für F2 (Konfiguration):**
- F3-06/F5-10 (Ergebnisseiten): Die aktive Konfiguration liegt in `st.session_state[config_store.SESSION_CONFIG_KEY]`. Fehlt sie, `config_store.DEFAULT_CONFIG_PATH` laden (architecture.md §6). Standardszenario ist `config/example_community.yaml` (HTW, braucht Netzwerk), offline `config/example_community_offline.yaml`. Die E2E-Tests der Ergebnisseiten wählen die Offline-Datei über die Auswahl „Konfigurationsdatei“. Zeitreihen über `community.build_inputs(cfg)`. (F2-02, F2-03, F2-04)
- F8-02/F7-03: Die Haushaltszeilen der Konfigurationsseite baut `_household_inputs` in `app/pages/configuration.py`, Geräte `_device_inputs` (Aufklapper „Geräte Haushalt N“, Entwurf in session_state mit `uid`). Die Widget-Keys tragen das Suffix `_v<n>` und werden beim Laden einer Datei neu erzeugt. E2E: Nach Hinzufügen/Entfernen zeigt Streamlit kurz alte und neue Elemente zugleich; erst per `to_have_count` auf das Rerun-Ende warten, dann Werte prüfen (Strict-Mode bricht sonst sofort ab). (F2-04, F4-04)
- OPSD (`sources/opsd.py`) ist noch nicht in der Config: `LoadSource` kennt `opsd` nicht, die Funktion nimmt `household_id` statt `LoadConfig`. Bei Bedarf `LoadConfig.opsd_household` + Adapter `(LoadConfig, year, cache) -> load_kwh` in `community.LOAD_SOURCES`; OPSD deckt nur ca. 2015–2018 ganz ab, kein Abbilden auf 2023 (`year=` verlangt ein vollständig abgedecktes Jahr). Annahme: Zählerstand gilt zum Zeitpunkt `utc_timestamp` (nur Kopfzeile per Network-Test geprüft, nicht die Zeitsemantik). (F1-09)
- F8-01: `smartmeter` ist in der Config erlaubt, hat aber noch keinen Loader. `build_inputs` meldet dann `CommunitySourceError`. F8-01 trägt einen Adapter `(LoadConfig, year, cache) -> load_kwh` in `community.LOAD_SOURCES` ein. (F2-03)
- Das Standardszenario (pvgis/htw/awattar/energy_charts) läuft durch `build_inputs` bisher nur mit gemockten Quellen. Ein echter Lauf gehört in einen Network-Test bzw. in F3/F2-04. (F2-03)
- F5: `devices.devices_for(cfg)` liefert typisierte Geräte je Haushalt (`ModulatingDevice`/`ProgramDevice`, `TimeWindow` mit `contains`/`remaining_after`). `load_config` validiert die Geräte **nicht**; die UI prüft sie über `config_store.apply_edits` → `validate_devices` (inkl. Baseline übers Jahr). Eine von Hand geschriebene Datei kann aber ungeprüfte Geräte enthalten, der Optimierer muss `devices_for` selbst aufrufen. Uhrzeiten müssen in YAML in Anführungszeichen stehen ("18:00"), Zeiten und Dauern auf dem 15-min-Raster. Zusätzlich zur Spec wird verlangt, dass die Energie ab `baseline_start` bis Fensterende mit `power_kw` erreichbar ist (Baseline-Fahrplan darf das Fenster nicht verlassen). (F4-01)
- F4-04/F5/F5-09: Beide Beispielkonfigurationen haben jetzt identische Geräte (IDs `ev`, `boiler`, `heat_pump`, `washing_machine`, `dishwasher`; hh05 hat alle fünf, insgesamt 12 Geräte, davon 6 `program` → Binärvariablen). Ein Test (`test_online_and_offline_examples_have_the_same_devices`) verhindert Abweichungen. Tests, die eigene Geräte prüfen, müssen die Beispielgeräte vorher entfernen (siehe `test_devices_flow_into_baseline_balance`). Die Kommentarprüfung der YAMLs akzeptiert `kW` und `Minuten` als Einheit. (F4-03)
- `pv.kwp` muss > 0 sein, eine Gemeinschaft ohne PV ist nicht konfigurierbar. Bei Bedarf auf ≥ 0 lockern. (F2-01)

**Für den planner (Spec anpassen):**
- architecture §5 „Ziele“: Die Hilfsvariable `import ≥ load − shared` ist bei negativem Wert eines geteilten kWh (Spot < ca. −2 ct) nicht exakt; umgesetzt ist Elimination von import/export plus erzwungenes `shared = min(pv, load)` (architecture.md §4/§5 schon nachgezogen, Beleg `test_negative_shared_value_keeps_the_balance_rule_shared_is_min_of_pv_and_load`). (F5-05)
- Vorschlag (braucht Nutzerfreigabe, `.claude/skills` headless gesperrt): Skill `finish-feature` §1 um den Schritt „Abweichung von Spec/Architektur → architecture.md im selben Commit nachziehen, Begründungstest, Planner-Eintrag“ ergänzen (Lesson spec-abweichung). (F5-05)
- spec §7.2: HTW nutzt die 1-min-Variante statt „1 s → 15 min“ (gleicher Datensatz, 1-min = Mittel der Sekundenwerte, nicht gegen 1-s geprüft). (F1-06)
- spec §7: PVGIS `map_to_year` setzt für 1.1. 00:00–01:00 lokal die Nachtstunde 31.12. 23:00 UTC ein (PV = 0, harmlos), nicht dokumentiert. (F1-05)

**Für neue Datenquellen und Frames:**
- `hourly_energy_to_15min`/`rate_to_15min` verlangen Europe/Vienna; UTC-Quellen vorher `tz_convert`. Nicht-Zahlen-Metadatenspalten (z. B. `household_id`) vorher entfernen. (F1-02)
- `validate_frame` akzeptiert Zahlen-Strings und object-Spalten **ohne** Suffix; CSV-Quellen (F8-01) müssen Zahlen vorher parsen. tz-Prüfung über `str(tz)`: dateutil-`Europe/Vienna` wird abgelehnt. (F1-01)
- bool-Spalten ohne Suffix werden abgelehnt. Braucht der Optimierer Flag-Spalten, vorher entscheiden und architecture.md §3.1 ergänzen. (F1-01)
- Suffix-Erkennung existiert doppelt: `units._unit_suffix` und `timeseries._suffix_of`. Bei neuer Nutzung nicht ein drittes Mal bauen, sondern `units` öffentlich machen. (F1-02)
- `units.kw_to_kwh` gibt es nur skalar (`kwh_to_kw` kann seit F3-06 auch Arrays); eine Array-Variante wäre sauberer. Der Architekturtest erkennt nur `0.25`, nicht `/ 4` oder `/ 3600`. (F1-01, F1-04)

**Bekannte Grenzen (akzeptiert, nur bei Bedarf angehen):**
- PVGIS (SARAH3) liefert Jahre nur bis 2023, kein Rückfall. HTW: andere Jahre als 2023 bis zu 6 Tage um 1 h verschoben, keine Feiertage; erster Parse ca. 5 s und 1 GB RAM. (F1-05, F1-06)
- Cache ohne Lock (paralleler Miss lädt doppelt, Datei bleibt gültig). `PROJECT_ROOT` setzt die editierbare Installation voraus. (F1-03, H-01)

**Für F5-05 / F6 (CO₂):**
- `co2_g_per_kwh` ist ein Lebenszyklus-Mittel (IPCC AR5) der AT-Erzeugung ohne Importe, 2023 im Mittel 90,7 g/kWh. Die UI muss die Vereinfachung vermerken (spec §7.2). (F1-08)

**Für F3 (Bilanz/Kennzahlen):**
- F3-02..F3-05: `balance.community_balance(pv, loads)` liefert `BALANCE_COLUMNS` (pv/load/shared/export/grid_import `_kwh`). Eingänge passen direkt zu `CommunityInputs.pv`/`.loads`. Die Spaltennamen sind als Konstanten in `balance.py` doppelt zu `community.PV_COLUMN`/`LOAD_COLUMN` definiert; bei Gelegenheit zusammenführen. (F3-01)

- F3-06/F6-01: `allocation.strategy_for(cfg.allocation).allocate(balance, loads)` liefert die Haushaltsaufteilung (Spalten-MultiIndex `household_id` × `quantity`). Zugriff typisiert über `household_ids`, `household_frame(result, hh)` (load/shared/grid_import `_kwh`) und `community_totals(result)`; `result[hh]` ist laut pandas-stubs eine Series (mypy). `balance` muss zu genau diesen `loads` gehören, sonst `AllocationInputError`. Ein unbekannter Strategiename wirft `KeyError` mit deutscher Meldung. (F3-03)

- F3-06: `balance.kpis(balance)` liefert `Kpis` (KWh-Summen + `self_consumption_ratio`/`autarky_ratio` in [0, 1]). `balance.monthly_kpis(balance)` liefert einen DataFrame mit einer Zeile pro Monat (Index `month` = Monatsbeginn, tz Vienna), Spalten `MONTHLY_KPI_COLUMNS`. Für die Anzeige in % mit 100 multiplizieren. (F3-02)

- F3-06/F5-01/F6-01: `economics.costs(balance, allocation, tariffs, prices=None)` liefert `CostResult` (Gemeinschaft pro Intervall `community_cost_eur` usw., `community_eur`, Tabelle `households` mit `cost_eur`, `household_eur(hh)`). `prices=None` ist der fixe Tarif, `CommunityInputs.prices` der Spot-Tarif (Ziel min_cost). `value_shared_ct_per_kwh` gilt nur für den fixen Tarif. Die Haushaltssicht summiert sich bewusst **nicht** zur Gemeinschaftssicht (interner Preis vs. Gemeinschaftskosten). Cent → € nur über `units.ct_to_eur`. (F3-04)

- F3-06/F5-01/F6-01: `economics.emissions(balance, allocation, co2)` liefert `EmissionResult` (`community` pro Intervall mit `emissions_kg_co2`, `community_kg_co2: KgCO2`, Tabelle `households` mit `emissions_kg_co2`, `household_kg_co2(hh)`). `co2` = `CommunityInputs.co2`. Vermiedene kg = Baseline − optimiert. g → kg nur über `units.g_to_kg_co2`. Die UI muss die CO₂-Vereinfachung vermerken (siehe F1-08 oben). (F3-05)

- F5-10/F6-02/F7-02: `analysis.analyze(config, inputs)` liefert `BaselineAnalysis` (balance, kpis, monthly, profile, surplus, costs, emissions); `daily_profile`/`surplus_by_day_hour` sind auch für die optimierte Balance nutzbar. Hinweistexte stehen in `analysis.ASSUMPTION_NOTES` (Seite `/ist-analyse`, Box-Key `analysis_assumptions`). Ein Link zur Annahmen-Seite fehlt noch, F7-02 ergänzt ihn. Die Seite cacht per `st.cache_data` mit dem Config-JSON als Schlüssel. (F3-06)

**Für den planner (Spec anpassen):**
- INV-4: Aggregierte Kennzahl-Tabellen tragen dimensionslose Anteile mit dem Suffix `_ratio` (architecture.md §3.1). In spec §5 und CLAUDE.md „Einheiten“ ist das noch nicht nachgezogen, `units.ALLOWED_SUFFIXES` kennt `_ratio` nicht (Zeitreihen brauchen es nicht). (F3-02)

- F4-03/F5/F5-07: `devices.tasks(device, index)` liefert die Tagesaufgaben (`Task` mit tz-aware `window_start`/`window_end`/`baseline_start`, `day` = Tag des Fensterbeginns). Nur Aufgaben mit ganzem Fenster im Index zählen (2023: E-Auto 18–07 → 364 Aufgaben); der Optimierer muss dieselbe Menge verwenden, sonst ist der Vergleich unfair. `devices.baseline_schedule` → `device_kwh`, `devices.baseline_loads(loads, devices_for(cfg))` = Baseline-Last; `analysis.analyze` nutzt sie schon. Die Optimierung braucht die **unflexiblen** `CommunityInputs.loads` plus Geräte separat. (F4-02)
- F5-02..F5-07: `optimize.objectives.evaluate(schedule, inputs, objective)` bewertet einen `DeviceSchedule` (hh → Geräte-ID → Series `device_kwh`), höher = besser (+kWh shared, −€ Gemeinschaftskosten mit Spot-Tarif, −kg CO₂). `OptimizationInputs.from_community(inputs, cfg)` bündelt Zeitreihen, Tarife und `devices_for`. Der Fahrplan muss **genau** die Geräte aus `inputs.devices` enthalten. Für Blöcke die Frames vorher auf den Block-Index schneiden (eine `restrict`-Hilfe gibt es noch nicht). Das MILP muss dieselben Koeffizienten wie `economics.community_costs`/`community_emissions` verwenden. `baseline_device_schedule(inputs)` liefert die Baseline. (F5-01)
- F5-08/F5-09/F5-10/F6-01: `optimize.runner.optimize(OptimizationInputs.from_community(inputs, cfg), cfg.optimization.objective, cfg.optimization.solver, time_limit_s=20, mip_gap=None, progress=cb)` → `Schedule` (`devices` = DeviceSchedule übers Jahr für `schedule_loads`/`evaluate`, `block_days`, `fallback_blocks` mit `day`/`reason`/`used`, `fallback_ratio`, `score`, `baseline_score`, `runtime_s`). `progress(done, total)` je Block für den Fortschrittsbalken. Blöcke laufen sequenziell (spätere Tage gehen mit ihrer Baseline als feste Last ein, architecture §5), keine Parallelisierung. (F5-07)
- INV-5: `tests/perf/test_performance.py` (15 HH, 60 Geräte, 2023, max_shared, MILP) läuft in 47,9 s, Puffer ca. 12 s. Der Rest steckt fast ganz in PuLP/CBC: Modellaufbau, MPS-Datei, ~30 ms Solver-Prozess je Block. Wenn es knapp wird, gibt es diese Hebel: `evaluate` je Block 2× (Geräte-Series einzeln validiert, ~15 % profiliert), Block-Bewertung über `milp.objective_weights`, `baseline_device_schedule` je Block. Große semikontinuierliche Geräte (min_power_kw) brauchen ggf. `mip_gap` (3 von 600 2-Tage-Blöcken > 20 s ohne Gap). (F5-04, F5-07, F5-08)
- F5-09: Mit min-Optionen kann die Baseline sie verletzen und mehr teilen; dann fällt der Block zurück (MILP und Greedy schlechter als Baseline) und INV-2 gilt für diesen Block nicht, aber er wird ausgewiesen. Beispielkonfigurationen haben keine min-Optionen. Bei min_cost mit sehr negativen Spotpreisen Big-M-Binärvariablen (mehr MIP-Anteil). (F4-02, F5-04, F5-05)
- CBC 2.10.3: Die MIP-Vorverarbeitung liefert auf manchen Blöcken Werte über der Obergrenze trotz „Optimal“. `solve_block` löst dann ohne Vorverarbeitung neu (architecture §4). Bei einem PuLP/CBC-Upgrade schlägt `test_regression_example_community_january_min_cost_has_no_fallback` absichtlich fehl (`False in calls`); dann prüfen, ob der Workaround noch nötig ist. Standardszenario-Tests (`tests/unit/test_standard_scenario.py`, 3 Jahresläufe ~48 s) laufen bewusst in check.sh. (F5-09)
- F6-02/F7-01: `compare.compare_schedule(OptimizationInputs.from_community(inputs, cfg), run.schedule.devices, allocation.strategy_for(cfg.allocation), tariff="fixed")` liefert `Comparison` (`table` mit Haushaltszeilen + Zeile `compare.TOTAL_ROW` = "total" für die UI-Beschriftung „Summe“, Skalare `additional_shared_kwh`, `saved_community_eur`, `saved_households_eur`, `avoided_kg_co2`; `baseline`/`optimized` = `Scenario` mit `loads`, `balance`, `allocation`, `costs`, `emissions` für Tagesprofil und CSV-Export). € in `table` = Haushaltssicht, Gemeinschaftssicht nur als Skalar. Tarif "fixed" passt zu `analysis.analyze`; für das Ziel min_cost ggf. zusätzlich "spot" zeigen. (F6-01)
- MILP-Ziel steht seit F6-01 in ct bzw. g (`milp.OBJECTIVE_SCALE`); wer `pulp.value(model.problem.objective)` liest, teilt durch `model.objective_scale`. (F6-01)
- MILP: Leerer Block-Index wird in `solve_block` nicht abgefangen (IndexError); der Runner erzeugt keine leeren Blöcke. (F5-02)
- F6-02/F7-03: Die Seite `/optimierung` legt den letzten Lauf als `optimize.report.OptimizationRun` (`config`, `schedule`) in `st.session_state[report.SESSION_RUN_KEY]` ab. Die Vergleichsseite prüft `run.config == aktive Konfiguration` (sonst Hinweis „neu rechnen“) und holt die Zeitreihen wieder über `build_inputs` (gecacht). Hinweistexte `report.UPPER_BOUND_NOTE`/`BLOCK_NOTE`. Ein Jahreslauf MILP (Offline-Beispiel) dauert ca. 15 s. E2E: Auf Text `^Rückfall-Blöcke: \d+ von \d+` warten (Timeout 180 s). `_number` (deutsches Zahlenformat) gibt es jetzt doppelt in `analysis.py` und `optimization.py`; F6-02 sollte es in ein gemeinsames Modul ziehen. (F5-10)

---

## 2026-11-23 – F6-01 – Vergleich Baseline vs. optimiert
- Ergebnis: PASS (Evaluator PASS nach 1 FAIL; Reviewer OK, 3 minor/nit behoben)
- Gemacht: Neues Modul `compare.py`: `scenario` (Fahrplan → Lasten, Bilanz, Allocation, Kosten fix/spot, CO₂), `compare` → `Comparison` (Tabelle je Haushalt + `total`, Spalten shared/cost/emissions baseline/optimized + Deltas, Skalare Gemeinschafts- und Haushaltssicht), `compare_schedule` (gegen `baseline_device_schedule`), `ComparisonInputError` (Haushalte, Index, PV, Tarifart, reservierte ID "total"). Tests `tests/unit/test_compare.py` (17), `tests/property/test_compare.py` (2). architecture.md §4 ergänzt.
- Reparatur: Evaluator-FAIL: `test_greedy_is_never_better_than_the_milp[min_co2]` war rot (Greedy 1e-6 kg besser als MILP, Fall vom 28./29.10.). Ursache: Das MILP-Ziel stand in kg/€, Gewichtsunterschiede von ~1e-7 kg/kWh (co2 1e-4 vs. 6e-8 g/kWh) lagen in der CBC-Dualtoleranz. Fix: `milp.OBJECTIVE_SCALE` (LP in ct bzw. g), `BlockModel.objective_scale`. Regressionstest `test_milp_objectives.py::test_tiny_weight_differences_are_not_lost_in_the_cbc_tolerance` (vorher rot). Drei Tests lesen den LP-Wert jetzt geteilt durch die Skala, die Toleranzen sind unverändert. INV-5 weiterhin grün (slow 48 s).
- Neue Abhängigkeiten: –
- Offene Probleme (minor): `tests/property/test_compare.py` importiert Strategie/Settings aus `tests.property.test_runner` (wie test_runner aus test_milp); ein gemeinsames Helper-Modul wäre sauberer.
- Fehlertypen: solver-precision
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F6-02).

## 2026-11-23 – F1-09 – Haushaltslast aus Open Power System Data (optional)
- Ergebnis: PASS (Evaluator PASS 2×; Reviewer: 2 major behoben)
- Gemacht: Neues Modul `sources/opsd.py` (OPSD Household Data 2020-04-15, 15-min-CSV): `household_ids`, `check_household` (unbekannte ID → `OpsdError` mit Liste), `read_readings`, `readings_to_intervals` (kumulativ → kWh je Intervall, Lücken/sinkende Stände/Rasterbruch → Fehler), `household_load` (`grid_import + pv − grid_export`), `load_household`/`load_household_meters` (Cache je Haushalt, optional `year=`), `download_csv`. Synthetische Fixture `tests/fixtures/opsd_household_15min_synthetic_2h.csv`, Tests `tests/unit/test_opsd.py` (42 offline + 1 network, Kopfzeile der echten Datei geprüft: bestanden). architecture.md §4 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor): `read_readings` liefert Zählerstände ohne Einheitensuffix (dokumentiert, nur für `readings_to_intervals`). Sinkende Zählerstände ohne Toleranz (exakt gleiche Stände ergeben exakt 0). Config-Adapter fehlt (siehe Offene Punkte).
- Fehlertypen: zeit-einheit, solver-precision, signatur-abweichung
- Anmerkung: Eigener Bug: Rasterprüfung erst mit `Timedelta.value` (immer ns) gegen µs-Index, sofort durch Tests aufgefallen. Reviewer-major: Last schnitt die Spanne auf alle Gerätezähler zu (jetzt nur die drei Lastzähler) und Negativprüfung ohne Toleranz (jetzt `NEGATIVE_LOAD_TOLERANCE_KWH` mit Grenztests).
- Lesson: zeit-einheit → Architekturtest `tests/unit/test_package.py::test_no_raw_time_ticks_outside_units` (kein `.asi8`/`as_unit(` außerhalb `units.py`, Ausnahme `# unit-agnostic` in `synthetic._day_codes`) + CLAUDE.md-Regel.
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F6-01).

## 2026-11-23 – F5-10 – UI: Optimierung starten
- Ergebnis: PASS (Evaluator PASS, Reviewer OK, 1 minor behoben: Fortschrittsbalken bei Fehler per `finally` entfernt)
- Gemacht: Neue Seite `app/pages/optimization.py` (`/optimierung`): Auswahl von Ziel (Selectbox „Ziel“) und Verfahren (Radio „Verfahren“), Button „Optimieren“, Fortschrittsbalken je Tagesblock, Ergebnis mit Ist/optimiert/Laufzeit, „Rückfall-Blöcke: n von m Tagesblöcken“ plus Tabelle bei Rückfällen, Box mit beiden Hinweisen. Neues Modul `optimize/report.py` (Labels, Hinweise, `OptimizationRun`, `score_summary` für kWh/€/kg CO₂). Tests: `tests/unit/test_optimization_report.py`, `tests/e2e/test_optimization_page.py` (max_shared MILP inkl. Fortschritt und Hinweisen, alle 3 Ziele × 2 Verfahren ohne Exception und ohne Rückfall). check.sh --e2e grün.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): `_number` ohne Unit-Test und doppelt (siehe Offene Punkte). Bei `improvement < 0` (nur Solver-Rauschen) stünde „-x besser“. E2E-Laufzeit der neuen Tests ca. 110 s.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-21 – F5-09 – Standardszenario ohne Rückfall
- Ergebnis: PASS (Evaluator PASS, Reviewer OK, 2 nits behoben)
- Gemacht: `tests/unit/test_standard_scenario.py`: Offline-Beispielgemeinschaft 2023 mit MILP für max_shared/min_cost/min_co2 (`fallback_ratio == 0`, 365 Blöcke, Ergebnis ≥ Baseline), Network-Test Online-Beispiel max_shared (bestanden, ~20 s). Der Test fand 7 min_cost-Rückfälle (E-Auto verfehlte 5,25 kWh): CBC-2.10.3-Bug in der MIP-Vorverarbeitung (Wert 8 kWh bei Cap 2,75, Status Optimal). Fix in `optimize/milp.py`: `run_cbc`, `bound_violations` (`BOUND_TOLERANCE_KWH` 1e-6), bei Verstoß zweiter Lauf mit `preprocess off`, danach noch Verstoß → `BlockSolveError`. Tests `tests/unit/test_milp_bounds.py` (Werte genau auf Grenze/Toleranz, Neulösen, Fehler, Regression Jan 2023 echt). architecture.md §4 ergänzt. INV-5 unverändert 47,8 s. check.sh 77 s → 128 s.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): Zweiter CBC-Lauf bekommt erneut das volle Zeitlimit (Docstring). `bound_violations` prüft nur modulierende Variablen (Programme über `_chosen_start`). `run_cbc`/`bound_violations` öffentlich v. a. für Tests.
- Fehlertypen: solver-bug
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-21 – F5-08 – Performance: 15 Haushalte × 1 Jahr < 60 s
- Ergebnis: PASS (Evaluator PASS nach 1 FAIL: Laufzeit fehlte noch in progress.md; Reviewer OK, Nit „Schrittweite 0 bei Intervall feiner als Index-Einheit“ behoben)
- Gemacht: `tests/perf/test_performance.py` (slow): synthetisches Jahr 2023, 15 Haushalte mit je 3–5 Beispielgeräten (60 insgesamt), max_shared, MILP, Wandzeit um `optimize` gemessen, Assert `< 60 s`, `fallback_ratio == 0`, Ergebnis ≥ Baseline. **Gemessen: 47,8 s / 47,9 s** (zwei Läufe, 365 Blöcke, fallback_ratio 0, shared 45 705 → 58 922 kWh). Vorher 80,3 s. Beschleunigung: `units._is_aligned_gapless_grid` (int64-Schnellpfad in `_validate_index`, Schrittweite in der Einheit des Index; nur ungültige Indizes laufen über die alten Prüfungen mit Meldung) und `lru_cache` auf `devices._local_instant`. Tests: `tests/unit/test_units.py` (Schnellpfad für s/ms/us/ns, Lücke/Versatz/umgekehrt je Einheit, Intervall). check.sh 105 s → 77 s.
- Neue Abhängigkeiten: –
- Offene Probleme: Puffer nur ~12 s auf diesem Laptop (siehe Offene Punkte INV-5).
- Fehlertypen: zeit-einheit
- Anmerkung: Eigener Fehlversuch ohne Folgen: Der erste Schnellpfad nutzte `Timedelta.value` (immer ns), der Index war aber in µs, deshalb griff der Pfad nie. Ein Test je Zeiteinheit sichert das jetzt ab.
- Nächster Schritt: `scripts/next_feature.sh`.
