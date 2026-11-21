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
- MILP: Leerer Block-Index wird in `solve_block` nicht abgefangen (IndexError); der Runner erzeugt keine leeren Blöcke. (F5-02)

---

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

## 2026-11-21 – F5-07 – Runner: Tageszerlegung, Rückfall und Ausweis
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: Neues Modul `optimize/runner.py`: `optimize` (Block je Tag mit Fensterbeginn, Horizont bis spätestes Fensterende; feste Last = aktueller Fahrplan der anderen Tage im Block; Kette MILP → Greedy → Baseline mit Vergleich per `evaluate`, `SCORE_TOLERANCE` 1e-6), `Schedule`, `FallbackBlock`, `RunnerInputError`. `devices.tasks`/`baseline_schedule` mit `day=`, `OptimizationInputs.task_day` + `.tasks(device)`, genutzt von MILP und Greedy. Tests: `tests/unit/test_runner.py` (Mock-Rückfälle zählen, schlechteres Ergebnis verworfen, Toleranzgrenze, 18–07 über Blockgrenze, Folgetag-Fenster nicht doppelt, feste Last, greedy ohne MILP, Begründungstest spätere Baseline), `tests/unit/test_runner_year.py` (Jahr, 3 Haushalte, INV-2/INV-3), `tests/property/test_runner.py` (alle Ziele × beide Solver). architecture.md §4/§5 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): Jahrestest ~12 s in check.sh (Akzeptanzkriterium, nicht slow markiert). Nachbarblock-Auswahl `blocks[i-1:i+2]` setzt Fenster ≤ 24 h ab Tagesbeginn voraus (Kommentar im Code, durch Gerätevalidierung gegeben). INV-3 übers Jahr nur bis Blöcke × 1e-6. Greedy kann im Jahr minimal besser sein als MILP (myopische Blöcke, Evaluator-Befund, kein Verstoß).
- Fehlertypen: spec-abweichung
- Anmerkung: Abweichung: architecture §5 sah nur frühere Blöcke als feste Last vor; das kann das Jahr unter die Baseline drücken (Begründungstest, Variante −1,2 statt −0,2 kg). Folge: keine Parallelisierung der Blöcke.
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F5-08, Laufzeit siehe Offene Punkte).

## 2026-11-21 – F5-06 – Greedy-Heuristik
- Ergebnis: PASS (Evaluator PASS, Reviewer: 1 major behoben)
- Gemacht: Neues Modul `optimize/greedy.py`: `solve_greedy` (Aufgaben nach Schlupf sortiert, Wert je kWh aus `milp.objective_weights`; modulierend: Wasserfüllung; Programm: bester Start; min-Optionen: ein Lauf mit k Intervallen à E/k, bestes (k, Start)), `GreedyScheduleError`, Toleranzen `ENERGY_NOISE_KWH`/`BOUND_NOISE_KWH`. `milp._inflexible_total` → öffentlich `inflexible_total`. Tests: `tests/unit/test_greedy.py` (Handbeispiele je Ziel/Geräteart, Reihenfolge nach Flexibilität, Werte genau auf floor/cap, Fehler), `tests/property/test_greedy.py` (INV-2 alle Gerätearten × Ziele, Greedy ≤ MILP inkl. min-Optionen), `tests/perf/test_greedy_perf.py` (slow, 15 Haushalte × 48 h: 0,04 s). architecture.md §4/§5 ergänzt (Flexibilität = Schlupf statt Fensterlänge).
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): Greedy-≤-MILP mit min-Optionen verwirft Beispiele, bei denen CBC das 20-s-Zeitlimit erreicht (nur diese, sonst Fehler); Laufzeit des Tests 4–11 s, check.sh-Gesamtlaufzeit schwankte (93 s / 186 s). Property-Test importiert Strategien aus `tests/property/test_milp.py` (bei Gelegenheit in ein Strategien-Modul auslagern). Rückgabetyp `dict[...]` statt Alias `DeviceSchedule` (wie `solve_block`).
- Fehlertypen: solver-precision, spec-abweichung
- Anmerkung: Reviewer-major: Untergrenze `floor ≤ E/k` ohne Toleranz (0,3/3 < 0,1) → gültiges k verworfen; behoben mit `BOUND_NOISE_KWH` + Tests genau auf floor und cap. Abweichung: Sortierung nach Schlupf statt Fensterlänge (architecture §5 nachgezogen, Begründungstest).
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F5-07).

## 2026-11-21 – F5-05 – MILP: Ziele Kosten und CO2
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `optimize/milp.py`: `objective_weights`/`ObjectiveWeights` (Ziel = Σ shared·w_s − Σ load·w_l + Σ pv·w_pv inkl. Konstante, also exakt `evaluate`), `_force_shared_to_minimum` (bei negativem Wert eines geteilten kWh: shared = min(pv, load) als Konstante bzw. Binärvariable), `SHARED_BINARY_MIN_KWH`. `economics.py`: Koeffizienten öffentlich (`energy_price_ct_per_kwh`, `import_price_ct_per_kwh`, `community_shared_price_ct_per_kwh`, `co2_intensity_g_per_kwh`), von `community_costs`/`costs` genutzt. Tests: `tests/unit/test_milp_objectives.py` (Handbeispiele billigstes/CO₂-ärmstes Intervall, Programm, PV vs. billiger Bezug, negativer Spotpreis, Gewichte, Fehler), `tests/unit/test_zz_repro_f505.py` (Regression winzige Werte), Property-Tests INV-3 für beide Ziele und „Modellwert = evaluate“ für alle Ziele (zufällige, auch negative Spotpreise). `test_unsupported_objective_is_rejected` prüft jetzt einen unbekannten Namen (min_cost ist absichtlich unterstützt). architecture.md §4/§5 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor): Dateiname `test_zz_repro_f505.py` ist ein Scratch-Name (Umbenennen headless nicht erlaubt), bei Gelegenheit in `test_milp_negative_shared.py` umbenennen. PV < 1e-6 kWh ohne Binärvariable (siehe Offene Punkte).
- Fehlertypen: solver-precision, spec-abweichung
- Anmerkung: Eigener Bug (Property-Test): Big-M-Binärvariable mit PV 1e-12 neben Last 1e-9 → CBC „Infeasible“. Abweichung: architecture §5 sah Hilfsvariable `import ≥ load − shared` vor; das ist bei negativem Spotpreis nicht exakt, daher Elimination + erzwungenes Minimum.
- Lesson: spec-abweichung → CLAUDE.md „Gelernte Regeln“ (Skill-Schritt als Vorschlag unter „Für den planner“, Skills headless gesperrt).
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F5-06).
