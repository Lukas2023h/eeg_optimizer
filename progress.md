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
- F5-03/F5-04/F5-05/F5-07: `optimize.milp.build_block_model`/`solve_block(inputs, objective, time_limit_s)` (Block-LP, `inputs` vorher auf den Block schneiden). Alle drei Ziele und alle Geräteoptionen sind seit F5-05 im Modell; `milp.objective_weights` liefert die Gewichte je Intervall (auch für Greedy F5-06 nutzbar: „bester Zielwert“ = `load_weight` bzw. Überschuss). `DeviceVars.row` hat je Intervall eine Variable (modulating), einen Ausdruck der Startbinärvariablen (program) bzw. `0.0`; `spans` die Aufgaben (bei Programmen mit `start_vars`/`run_intervals`). CBC liefert nur ~8 signifikante Stellen: `_exact_energy` reskaliert modulierende Aufgaben, Programme werden aus dem gewählten Start rekonstruiert. Runner fängt `BlockSolveError` (Status ≠ Optimal, auch Zeitlimit mit nur zulässiger Lösung) — **auch schon beim Aufbau** (`build_block_model`), wenn ein Programm am Frühjahrs-DST-Tag nicht ins Fenster passt; dieser Rückfall muss gezählt und ausgewiesen werden. Leerer Block-Index wird nicht abgefangen (IndexError). (F5-02, F5-03)
- F5-07 (Rückfall auf Baseline): Der Baseline-Lauf hält `min_power_kw` im letzten Teilintervall und `min_run_min` nicht unbedingt ein. Fällt ein Block auf die Baseline zurück, ist INV-2 dafür nicht garantiert; Runner/Greedy entsprechend prüfen. (F4-02)
- F5-07: Mit min-Optionen ist INV-3 **nicht** durch das Modell garantiert (Baseline verletzt sie ggf. und kann mehr teilen, Test `test_min_run_can_make_the_optimum_worse_than_a_baseline_that_violates_it`). Der Runner muss je Block gegen die Baseline vergleichen und entscheiden (Rückfall zählen/ausweisen oder Ergebnis behalten und vermerken). Nicht aufteilbare Energie (z. B. 1 kWh bei ≥ 4,1 kW) → `BlockSolveError` „Infeasible“. (F5-04)
- F5-07/INV-5: Große semikontinuierliche Geräte (min_power_kw) können CBC beim Optimalitätsbeweis lange beschäftigen (3 von 600 zufälligen 2-Tage-Blöcken > 20 s, mit `mip_gap=1e-2` alle ≤ 2 s). `solve_block(..., time_limit_s, mip_gap)` nutzen; Gap und Zeitlimit im Runner festlegen und ausweisen. (F5-04)

- F5-07/F5-09: Bei min_cost mit sehr negativen Spotpreisen (Wert eines geteilten kWh < 0) erzwingt das MILP `shared = min(pv, load)`, offene Intervalle bekommen eine Binärvariable (Big-M) → mehr MIP-Anteil; Laufzeit im Jahreslauf (INV-5) beobachten. Unter `SHARED_BINARY_MIN_KWH` (1e-6 kWh PV) bewusst ausgelassen, Abweichung zu `evaluate` < 1e-6 € (akzeptiert, nicht gezählt). (F5-05)

---

## 2026-11-21 – F5-05 – MILP: Ziele Kosten und CO2
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `optimize/milp.py`: `objective_weights`/`ObjectiveWeights` (Ziel = Σ shared·w_s − Σ load·w_l + Σ pv·w_pv inkl. Konstante, also exakt `evaluate`), `_force_shared_to_minimum` (bei negativem Wert eines geteilten kWh: shared = min(pv, load) als Konstante bzw. Binärvariable), `SHARED_BINARY_MIN_KWH`. `economics.py`: Koeffizienten öffentlich (`energy_price_ct_per_kwh`, `import_price_ct_per_kwh`, `community_shared_price_ct_per_kwh`, `co2_intensity_g_per_kwh`), von `community_costs`/`costs` genutzt. Tests: `tests/unit/test_milp_objectives.py` (Handbeispiele billigstes/CO₂-ärmstes Intervall, Programm, PV vs. billiger Bezug, negativer Spotpreis, Gewichte, Fehler), `tests/unit/test_zz_repro_f505.py` (Regression winzige Werte), Property-Tests INV-3 für beide Ziele und „Modellwert = evaluate“ für alle Ziele (zufällige, auch negative Spotpreise). `test_unsupported_objective_is_rejected` prüft jetzt einen unbekannten Namen (min_cost ist absichtlich unterstützt). architecture.md §4/§5 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor): Dateiname `test_zz_repro_f505.py` ist ein Scratch-Name (Umbenennen headless nicht erlaubt), bei Gelegenheit in `test_milp_negative_shared.py` umbenennen. PV < 1e-6 kWh ohne Binärvariable (siehe Offene Punkte).
- Fehlertypen: solver-precision, spec-abweichung
- Anmerkung: Eigener Bug (Property-Test): Big-M-Binärvariable mit PV 1e-12 neben Last 1e-9 → CBC „Infeasible“. Abweichung: architecture §5 sah Hilfsvariable `import ≥ load − shared` vor; das ist bei negativem Spotpreis nicht exakt, daher Elimination + erzwungenes Minimum.
- Lesson: spec-abweichung → CLAUDE.md „Gelernte Regeln“ (Skill-Schritt als Vorschlag unter „Für den planner“, Skills headless gesperrt).
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F5-06).

## 2026-11-21 – F5-04 – MILP: Mindestlaufzeit und Mindestleistung
- Ergebnis: PASS (Evaluator PASS, Reviewer: 2 major behoben)
- Gemacht: `optimize/milp.py`: bei `min_power_kw`/`min_run_min` Binärvariable `on` je Fensterintervall (`floor·on ≤ x ≤ cap·on`, floor = Mindestleistung bzw. bei nur min_run 1 % cap), Kardinalitätsschranken `ceil(E/cap) ≤ Σ on ≤ floor(E/floor)` (machte harte Fälle von > 20 s auf < 1 s), `_add_min_run` (Startindikatoren, keine späten Starts, Frühjahrs-DST → `BlockSolveError`), Reparatur der CBC-Rundung in `[floor, cap]` bzw. exakt 0, neuer Parameter `solve_block(..., mip_gap=None)`. Tests: `tests/unit/test_milp_min_options.py`, Property-Test `test_inv2_min_power_and_min_run_are_kept` (mit `mip_gap=1e-2`, Begründung im Test). `test_min_options_are_rejected_not_ignored` → `..._planned_not_ignored` (Verhalten absichtlich geändert). architecture.md §4 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): Optimalität mit min-Optionen nur an Handbeispielen geprüft (kein Brute-Force-Vergleich). Tests nutzen private Helfer (`_exact_energy`, `_running_count_bounds`) und Variablennamen (`on_0_1_`).
- Fehlertypen: solver-precision, solver-performance
- Anmerkung: Reviewer-major 1: Reparatur warf bei 1-ulp-Abweichung, wenn alle Werte am Rand lagen (Energie = Vielfaches von floor/cap). Reviewer-major 2: INV-3-Grenze mit min-Optionen nicht durch Test/Offene Punkte abgesichert. Eigener Befund: seed 42 hing > 10 min (schwache Relaxation).
- Lesson: solver-precision → CLAUDE.md „Gelernte Regeln“ + Regressionstests für Werte genau auf den Schranken.
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F5-05).

## 2026-11-21 – F5-03 – MILP: Programm-Geräte (Startzeit-Binärvariablen)
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `optimize/milp.py`: `_add_program_task` (eine Binärvariable je möglichem Start, Σ = 1, Energie als Ausdruck `cap · Σ y`), `_add_modulating_task` herausgelöst, `_chosen_start` (genau ein Start = 1, sonst `BlockSolveError`), `TaskSpan.start_vars`/`run_intervals`, `BINARY_TOLERANCE`. Programm passt nicht ins Fenster (Frühjahrs-DST) → `BlockSolveError` beim Aufbau. Tests: `tests/unit/test_milp_program.py` (Handbeispiel 90 min in den Überschuss 11:00–12:30, exaktes Profil, Binärstruktur, Fensterende, über Mitternacht, beide DST-Tage, gemischte Geräte, `_chosen_start`-Fehler), Property-Tests INV-2 (genau ein Lauf am Stück im Fenster) und INV-3 mit gemischten Geräten. `test_program_devices_are_rejected_not_ignored` → `..._planned_not_ignored` (Verhalten absichtlich geändert). architecture.md §4 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor): Runner muss den Aufbau-Fehler mitfangen (Offene Punkte). Programmenergie = `duration/15 · kw_to_kwh(power)` wie Baseline, nicht `task.energy_kwh` direkt (Abweichung nur Float-Rundung).
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F5-04).

## 2026-11-21 – F5-02 – MILP für einen Block: modulierende Lasten (LP)
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: Neues Modul `src/eeg_optimizer/optimize/milp.py`: `build_block_model` (PuLP-LP: Gerätevariablen nur in Aufgabenfenstern aus `devices.tasks`, Energie je Aufgabe, Leistungsgrenze, `shared ≤ pv`, `shared ≤ Last + Geräte`, max Σ shared), `solve_block` (CBC, Status-/sol_status-Prüfung, Energie-Reparatur der CBC-Rundung), `BlockModel`/`DeviceVars`/`TaskSpan`, `UnsupportedModelError`, `BlockSolveError`. Tests: `tests/unit/test_milp.py` (Handbeispiel 12–13 Uhr, Leistungsgrenze, kein Binär, Variablen nur im Fenster, Reparatur, Ablehnungen) und `tests/property/test_milp.py` (INV-2, INV-3 inkl. DST-Tage, 1–2 Tage, mehrere Haushalte). architecture.md §4 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): Leerer Block-Index → IndexError statt eigener Meldung. `DeviceVars.row` als `list[Any]` typisiert (PuLP ohne Stubs). Kein Test mit echtem Zeitlimit-Abbruch.
- Fehlertypen: solver-precision
- Anmerkung: Eigener Bug, vom Property-Test gefunden. Die CBC-Rundung verfehlte die Tagesenergie um 1e-6 kWh.
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F5-03).

## 2026-11-21 – F5-01 – Zielfunktionen und Bewertung
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: Neues Paket `src/eeg_optimizer/optimize/` mit `objectives.py`: `Objective`/`OBJECTIVES`, `DeviceSchedule`, `OptimizationInputs` (+ `from_community`), `schedule_loads`, `score_balance`, `evaluate` (höher = besser), `baseline_device_schedule`, `ObjectiveInputError`. `economics.py`: Gemeinschaftssicht als `community_costs` und `community_emissions` herausgelöst (von `costs`/`emissions` genutzt). Tests: `tests/unit/test_objectives.py` (Mini-Beispiel 3 Fahrpläne × 3 Ziele handberechnet, Rangfolge je Ziel, Spot statt Fixpreis, Baseline-Wert = berichtete Kennzahlen des Offline-Beispiels, Fehlerfälle). architecture.md §4 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor): `costs`/`emissions` berechnen Energiepreis bzw. CO₂-Intensität doppelt (einmal in `community_*`). Kein Property-Test für Schranken des Scores (INV-3-Property kommt mit F5-02).
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F5-02).
