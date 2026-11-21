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
- F5-03/F5-04/F5-05/F5-07: `optimize.milp.build_block_model`/`solve_block(inputs, objective, time_limit_s)` (Block-LP, `inputs` vorher auf den Block schneiden). Erweiterungen in `build_block_model`: `_check_supported` lehnt program, min-Optionen und min_cost/min_co2 bisher mit `UnsupportedModelError` ab, dort jeweils freischalten. `DeviceVars.row` hat je Intervall eine Variable bzw. `0.0`, `spans` die Aufgaben. CBC liefert nur ~8 signifikante Stellen: `_exact_energy` reskaliert je Aufgabe (bei Programmen mit Binärvariablen anders lösen, dort ist das Profil fix). Runner fängt `BlockSolveError` (Status ≠ Optimal, auch Zeitlimit mit nur zulässiger Lösung). Leerer Block-Index wird nicht abgefangen (IndexError). (F5-02)
- F5-07 (Rückfall auf Baseline): Der Baseline-Lauf hält `min_power_kw` im letzten Teilintervall und `min_run_min` nicht unbedingt ein. Fällt ein Block auf die Baseline zurück, ist INV-2 dafür nicht garantiert; Runner/Greedy entsprechend prüfen. (F4-02)

---

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

## 2026-11-21 – F4-04 – UI: Geräte je Haushalt bearbeiten
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: Konfigurationsseite: je Haushalt Aufklapper „Geräte Haushalt N“ mit ID, Art, Leistung, Energie/Tag bzw. Dauer, Fenster Beginn/Ende, Baseline-Start (Uhrzeit-Auswahl im 15-min-Raster), „Gerät N entfernen“ und „Gerät hinzufügen“ aus Vorlagen. `config_store`: `DEVICE_PRESETS`, `new_device` (freie ID `<vorlage>_2`), `validate_devices` (devices_for + Baseline übers Jahr, DST-Fehler als ConfigError mit Pfad), von `apply_edits` aufgerufen. Tests: `tests/unit/test_config_store_devices.py`, AppTests in `test_configuration_page.py` (hinzufügen, entfernen, bearbeiten, Art wechseln, Fehler, versteckte Optionen bleiben, Verkleinern), `tests/e2e/test_device_editing.py` (Waschmaschine speichern + neu laden, 500 kWh → deutsche Meldung, entfernen). architecture.md §6 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (nit): `_single_device` ist lang (~75 Zeilen). `min_run_min`/`min_power_kw` haben keine Widgets (bleiben erhalten). Speichern dauert durch die Baseline-Prüfung ca. 0,3 s.
- Fehlertypen: e2e-flaky-rerun
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F5-01).

## 2026-11-19 – F4-03 – Geräte in der Beispielgemeinschaft
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `config/example_community.yaml` und `_offline.yaml` haben jetzt gleiche, kommentierte Geräte. hh01: E-Auto + Waschmaschine, hh02: Geschirrspüler, hh03: Wärmepumpe + Geschirrspüler, hh04: Boiler + Waschmaschine, hh05: alle fünf. Die Wärmepumpe ist `modulating`, läuft 10–16 Uhr und hat einen Kommentar zur Vereinfachung (spec §7.2). `min_power_kw`/`min_run_min` sind nicht gesetzt. Tests: `tests/unit/test_example_devices.py` (Laden, Arten, Wärmepumpe inkl. Kommentar, keine Mindestleistung, online = offline, Baseline über ganz 2023 inkl. DST). `test_devices_flow_into_baseline_balance` entfernt jetzt erst die Beispielgeräte. `_UNIT_HINTS` kennt `kW`/`Minuten`.
- Neue Abhängigkeiten: –
- Offene Probleme (nit): Die Geräteblöcke sind in beiden YAMLs dupliziert (durch Test abgesichert). Wasch- und Spülmaschine laufen vereinfacht einmal täglich.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F4-04 oder F5-01).

## 2026-11-19 – F4-02 – Baseline-Fahrplan flexibler Lasten
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: In `devices.py` neu `Task`, `tasks`, `baseline_schedule` (volle Leistung ab `baseline_start`, Rest im letzten Intervall, Programm feste Dauer; nicht existente Uhrzeit → 03:00, doppelte → erstes Auftreten; echte Intervalle zählen; Lauf über Fensterende → `BaselineScheduleError`) und `baseline_loads`. `analysis.analyze` rechnet die Baseline-Bilanz jetzt mit Gerätelasten. Tests: `tests/unit/test_baseline_schedule.py` (Werte, Mitternacht, beide DST-Tage, ganzes Jahr, Lasten addiert, Ist-Analyse mit E-Auto) und `tests/property/test_baseline_schedule.py` (Tagesenergie, Leistungsgrenze, nur im Fenster, Aufgaben disjunkt, über DST-Tage). architecture.md §4 ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): Randaufgaben (31.12. abends) fallen bewusst weg. `_checked_index` baut einen Wegwerf-DataFrame für `validate_frame`; `_run_energies` ist als bare `np.ndarray` typisiert.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F4-03).
