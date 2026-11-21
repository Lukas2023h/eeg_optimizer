# Fortschritt - Archiv

Ältere Einträge aus `progress.md`, neueste oben. Verschoben von `scripts/rotate_progress.py`. Bei Bedarf gezielt durchsuchen (grep), nicht komplett lesen.

---

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

## 2026-11-19 – F4-01 – Gerätemodell flexibler Lasten
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: Neues Modul `src/eeg_optimizer/devices.py` mit `TimeWindow` (über Mitternacht bei end ≤ start), `ModulatingDevice`, `ProgramDevice`, `parse_device`/`parse_devices`/`devices_for`. Die Prüfung nutzt den Section-Reader aus `config.py`, der jetzt öffentlich als `ConfigSection` verfügbar ist und `optional_integer` kennt. Damit gelten dieselben Meldungen mit Schlüsselpfad und unbekannte Schlüssel werden abgelehnt. Tests: `tests/unit/test_devices.py` (YAML-Laden, alle Validierungsfehler, Fenster 18–07/10–16/20–00) und `tests/property/test_devices.py` (Fensterform, Annahme genau bei erreichbarer Energie). architecture.md §4 ist ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): Das Orakel des Property-Tests nutzt dieselbe `kw_to_kwh`-Formel wie die Implementierung. `start == end` wird doppelt geprüft (im Dataclass und im Parser, dort mit deutscher ConfigError-Meldung).
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F4-02).

## 2026-11-19 – F3-06 – UI: Ist-Analyse
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: Neues Modul `src/eeg_optimizer/analysis.py` (`analyze`, `daily_profile` mit mittlerer Leistung je 15-min-Slot, `surplus_by_day_hour` mit Export je Tag und Stunde, `ASSUMPTION_NOTES`). Neue Seite `app/pages/analysis.py` (URL `/ist-analyse`) mit 8 KPI-Kacheln (Quoten in %, Energie in kWh, Kosten in €, CO₂ in kg), Tagesprofil, Monatsbalken und Überschuss-Heatmap (Plotly) sowie einer Hinweisbox. Die Berechnung ist per `st.cache_data` gecacht. `units.kwh_to_kw` kann jetzt auch Arrays. Tests: `tests/unit/test_analysis.py` (Slot-Mittel, Zeitumstellung, Summen, Fehlerfälle, Offline-Beispiel < 10 s) und `tests/e2e/test_analysis_page.py` (Kacheln mit Einheiten, 3 Diagramme, Hinweise, gecachter Aufbau < 10 s). architecture.md §4 und §6 sind ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): `daily_profile`/`surplus_by_day_hour` prüfen den Eingang über einen `balance.kpis`-Aufruf (eine eigene Validierungsfunktion wäre sauberer). `use_container_width` ist in neueren Streamlit-Versionen veraltet. Das 10-s-Budget im Unit-Test ist eine Wanduhr-Messung. Die E2E-Tests prüfen den Diagramminhalt nicht.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-19 – F3-05 – CO2-Bilanz
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `economics.emissions(balance, allocation, co2) -> EmissionResult`. Emissionen = grid_import_kwh × co2_g_per_kwh / 1000 kg, PV mit 0 g/kWh. Ausgewiesen werden die Gemeinschaft pro Intervall und Summe sowie jeder Haushalt über seinen grid_import aus der Allocation. Die Eingänge werden geprüft (Spalte, Index, negative Intensität → `EmissionInputError`, NaN → `FrameValidationError`, Allocation passt nicht). Die Eingangsprüfung ist von `costs` übernommen und erhält jetzt die Fehlerklasse als Parameter. Neu ist `units.g_to_kg_co2`. Die Spaltenkonstante kommt aus `community.CO2_COLUMN`, es gibt kein Duplikat. Tests: `tests/unit/test_emissions.py` (handgerechnetes Mini-Beispiel gesamt und je Haushalt, nur PV, ganzes Jahr, Fehlerfälle) und `tests/property/test_emissions.py` (Formel, Haushalte = Gemeinschaft, mehr PV → nie mehr CO₂). architecture.md §4 ist ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (nit): Statt des `error`-Parameters in `_check_balance`/`_household_frames` wäre eine gemeinsame Basisklasse `EconomicsInputError` sauberer. Die Signatur weicht von der Skizze `emissions(balance, co2)` ab, weil die Haushaltssicht die Allocation braucht; architecture.md ist angepasst.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F3-06).

## 2026-11-18 – F3-04 – Kostenmodell (€)
- Ergebnis: PASS (Evaluator PASS ×2, Reviewer-Major behoben)
- Gemacht: `src/eeg_optimizer/economics.py` mit `value_shared_ct_per_kwh`, `reduced_grid_fee_ct_per_kwh` und `costs(balance, allocation, tariffs, prices=None) -> CostResult` (Gemeinschaftssicht pro Intervall in `_eur`, Haushaltssicht als Tabelle, Spot-Variante spot + markup). Neu ist `units.ct_to_eur` (skalar/Array). Die Eingänge werden geprüft (Balance-Spalten, Preis-Spalte/Index, Allocation-Ebenen/Spalten, `validate_frame` je Haushalt, Summe = Balance mit 1e-6 kWh). Tests: `tests/unit/test_economics.py` (9.42/7.68, handgerechnetes Mini-Beispiel fix und Spot mit negativem Preis, Verschiebe-Test 1 kWh = value_shared, ganzes Jahr, Fehlerfälle inkl. NaN) und `tests/property/test_economics.py` (Kosten = ohne Teilen − shared·value_shared, Spot-Formel mit Preisen −50..50, Spot = fix bei spot = energy_price − markup). architecture.md §4 ist ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor): Reviewer-Major (NaN in der Allocation führte still zu NaN-Kosten) ist behoben. Die Signatur weicht von der alten Skizze `costs(balance, tariffs, prices)` ab, weil die Haushaltssicht die Allocation braucht; architecture.md ist angepasst.
- Fehlertypen: verschluckter-fehler
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F3-05).

## 2026-11-18 – F3-03 – Verteilschlüssel dynamisch anteilig
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `src/eeg_optimizer/allocation.py` mit `AllocationStrategy`-Protocol, `DynamicProportional` (shared_h = load_h · shared/Σload, grid_import_h = load_h − shared_h, 0 bei Σload = 0), Registry `STRATEGIES` und `strategy_for(AllocationConfig)` sowie den Helfern `household_ids`, `household_frame`, `community_totals`. Die Eingänge werden geprüft (Balance-Spalten, `validate_frame`, Index, Balance passt zu den Lasten mit 1e-6 kWh). Tests: `tests/unit/test_allocation.py` (Werte, Gesamtlast 0 unter `np.errstate(all="raise")`, Summen über die Zeitumstellung, Fehlerfälle, Auswahl per Config) und `tests/property/test_allocation.py` (INV-7 mit 1e-6, shared_h ≤ load_h, alles ≥ 0). architecture.md §4 ist ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (nit): `strategy_for` wirft `KeyError` statt einer `ValueError`-Unterklasse (in `str()` erscheint die Meldung in Anführungszeichen). Die Spaltennamen-Konstanten bleiben doppelt (siehe Offene Punkte F3-01).
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-18 – F3-02 – Baseline-Kennzahlen
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: In `balance.py` sind `Kpis` (frozen dataclass), `kpis()` und `monthly_kpis()` dazugekommen (Monatssummen per `resample("MS")` in Ortszeit, Quoten mit Nenner 0 ergeben 0.0). Der Eingang wird über `BALANCE_COLUMNS` und `validate_frame` geprüft. Tests: `tests/unit/test_kpis.py` (handgerechnetes Beispiel, Division durch 0, 12 Monate 2023, DST-Monate, Teilzeitraum, Fehlerfälle) und Properties in `tests/property/test_balance.py` (Quoten endlich und in [0, 1], Monatssummen = Gesamt). architecture.md §3.1 und §4 sind ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit): `_ratio` clippt auf [0, 1] und würde damit eine echte Verletzung von shared ≤ pv verdecken (die Property-Tests decken das ab). `kpis` baut 1-Element-Arrays für `_ratio`. Der Indexname `"month"` ist ein Literal ohne Konstante. Die Monats-Property läuft nur über ≤ 48 Intervalle.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-18 – F3-01 – Energiebilanz der Gemeinschaft
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `src/eeg_optimizer/balance.py` mit `community_balance(pv, loads)`: shared = min(pv, Summe Last), export = pv − shared, grid_import = Last − shared. Die Eingänge werden geprüft (genau eine kanonische Spalte, `validate_frame`, gleicher Index, ≥ 1 Haushalt, sonst `BalanceInputError`), der Ausgang ebenfalls per `validate_frame`. Tests: `tests/unit/test_balance.py` (Werte, PV = 0, Last = 0, PV == Last, ganzes Jahr mit Zeitumstellung, Fehlerfälle) und `tests/property/test_balance.py` (INV-1 mit Toleranz 1e-6, alle Werte ≥ 0, 1–15 Haushalte). architecture.md §4 ist ergänzt.
- Neue Abhängigkeiten: –
- Offene Probleme (nit): `test_columns_and_values` vergleicht exakt und braucht deshalb dyadische Testwerte.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-18 – F2-04 – UI: Konfigurationsseite
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `src/eeg_optimizer/config_store.py` (ohne Streamlit) bietet `available_configs`, `apply_edits` (validiert nur über `parse_config`), `resize_households`, `save_user_config` nach `data/configs/<name>.yaml` mit sicherem Dateinamen und Session-State-Schlüssel. Neue Seite `app/pages/configuration.py` (`/konfiguration`) mit Dateiauswahl, Name, PV (kWp/Neigung/Ausrichtung), EEG-Typ, Tarifen, Anzahl Haushalte und Lastprofil je Haushalt sowie den Knöpfen „Übernehmen“ (nur Session State) und „Speichern“. Fehler erscheinen als deutsche `st.error`-Meldung, die letzte gültige Konfiguration bleibt aktiv. Tests: `tests/unit/test_config_store.py`, `tests/unit/test_configuration_page.py` (Streamlit-AppTest, Session State) und `tests/e2e/test_configuration_page.py` (kWp 20 speichern, kWp negativ, Seitenwechsel, Haushaltsanzahl). Ergänzt ist architecture.md §6. Die Reviewer-Minors „MAX_HOUSEHOLDS hart codiert“ und „`_on_select` bei gelöschter Datei“ sind behoben.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit):
  - „Speichern“ überschreibt eine vorhandene Datei gleichen Namens ohne Hinweis.
  - `_collect_edits` ist ca. 50 Zeilen lang, bei der nächsten Änderung (F4-04) PV- und Tarif-Teil auslagern.
  - Für „Speichern“ gibt es keinen AppTest, nur E2E.
  - Jahr, Standort, Quellen von PV/Preis/CO₂ und Seeds sind in der UI nicht editierbar (nicht gefordert).
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-18 – F2-03 – Gemeinschafts-Zeitreihen aus Konfiguration bauen
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `src/eeg_optimizer/community.py`. `build_inputs(config, cache=None)` gibt `CommunityInputs` zurück (pv, loads je Haushalts-ID in Config-Reihenfolge, prices, co2). Die Registries `PV_SOURCES`/`LOAD_SOURCES`/`PRICE_SOURCES`/`CO2_SOURCES` bilden den Quellnamen auf dünne Adapter ab (Config-dataclasses → Schlüsselwort-Signaturen der Quellen), damit ist der Adapter-Punkt aus F1-04..F1-06 erledigt. Fehlt eine Quelle, kommt `CommunitySourceError` mit Schlüsselpfad und der Liste der verfügbaren Quellen. An der Grenze wird jeder Frame geprüft: genau die kanonische Spalte, `validate_frame` und Index == `year_index(year)`. architecture.md §4 ist ergänzt. Tests: `tests/unit/test_community.py` (Offline-Build mit gesperrtem Socket, Dispatch, Erweiterbarkeit, Adapter-Argumente der Netzquellen per Mock, Grenzprüfungen) und `tests/property/test_community_properties.py` (INV-4/INV-6, Jahre 2015–2030).
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit):
  - Das Standardszenario mit echten Quellen ist nur gemockt getestet (siehe Offene Punkte).
  - `__all__` exportiert auch die Loader-Typaliase.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F2-04).

## 2026-11-18 – F2-02 – Defaults und Beispielgemeinschaft
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `config/example_community.yaml` (5 Haushalte, Linz, 2023, 30 kWp, PVGIS/HTW/aWATTar/Energy-Charts) und `config/example_community_offline.yaml` (gleiche Gemeinschaft, alle Quellen synthetic mit Seeds und `annual_kwh`). `defaults.yaml` bekam Kommentare für die Abschnitte und Seeds. Tests: `tests/unit/test_example_configs.py` (Laden beider Dateien, Tarife gegen die geparste Tabelle aus spec §7.1 und gegen fest notierte Zahlen, Offline-Reihen mit gesperrtem Socket, jeder Schlüssel mit deutschem Kommentar, Zahlen mit Einheitshinweis).
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit):
  - Der Einheiten-Test ist eine Heuristik (Stichwörter wie „Grad“, „ganze Zahl“) und erkennt keine falsche Einheit.
  - Die Jahresverbräuche in den Kommentaren der HTW-Profile (ca. 1 400 / 3 230 / 8 600 kWh) stammen aus F1-06 und sind nicht per Test geprüft; die Offline-`annual_kwh` sind nur grob daran angelehnt.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F2-03).

## 2026-11-18 – F2-01 – Konfigurationsmodell und YAML-Loader
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `src/eeg_optimizer/config.py` enthält frozen dataclasses (`CommunityConfig`, `PvConfig`, `Location`, `LoadConfig`, `TariffConfig`, …) sowie `load_config`, `parse_config`, `merge_dicts`, `config_to_dict` und `save_config`. Die Defaults werden Schlüssel für Schlüssel zusammengeführt. Die Validierung passiert an einer Stelle mit deutschen Meldungen samt Schlüsselpfad. Unbekannte Schlüssel sind Fehler. `effective_grid_fee_reduction` richtet sich nach `eeg_type`, solange nicht explizit gesetzt. `config/defaults.yaml` liegt bei. Tests: `tests/unit/test_config.py` und `tests/property/test_config_properties.py` (Roundtrip, Merge). Die Schnittstelle steht in architecture.md §3.3.
- Neue Abhängigkeiten: – (pyyaml war schon da)
- Offene Probleme (nit):
  - Der eigene YAML-Dumper setzt Steuer- und Zeilentrennzeichen in doppelte Anführungszeichen, weil PyYAML NEL sonst zu einem Leerzeichen macht. Das hat der Property-Test gefunden.
  - config importiert `PROFILE_COUNT` aus `sources.htw`.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F2-02).

## 2026-11-18 – F1-08 – CO₂-Intensität Österreich aus Energy-Charts
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: `src/eeg_optimizer/sources/energy_charts.py`. Energy-Charts hat keinen CO₂-Endpunkt für AT, deshalb wird die Intensität aus `/public_power?country=at` (15-min, MW) berechnet: erzeugungsgewichtete Emissionsfaktoren (IPCC AR5 plus 4 eigene Annahmen), ohne Import/Export und Pumpstrom. Das Ergebnis ist 15-min statt stündlich. Ein Request pro Jahr, Cache `energy_charts/co2-ipcc-ar5-v1-<jahr>`. Unbekannte Typen, `null`, negative Werte und Lücken führen zu `EnergyChartsError`. Methode und Faktoren stehen in spec §7.2, die Schnittstelle in architecture.md §4. Fixtures: 2.7.2023 und der DST-Tag 26.3.2023. Der Network-Test ist grün, Jahresmittel 2023 = 90,7 g/kWh.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit):
  - Monats-Chunks wie bei aWATTar liefen in HTTP 429 (Rate-Limit), deshalb ein Jahres-Request. Ein 429 wird nicht automatisch wiederholt, er wird als HTTPError gemeldet.
  - Die Faktoren für Müll/Sonstige (700) und Öl (650) sind eigene Annahmen.
  - Die Lizenz der Fixtures ist im README als CC BY 4.0 (Energy-Charts) angegeben, nicht gesondert geprüft.
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-18 – F1-07 – Spotpreise aus aWATTar (AT)
- Ergebnis: PASS (Evaluator PASS, Reviewer OK)
- Gemacht: Die Umsetzung aus der unterbrochenen Vorsession (WIP-Commit 3274fcb) wurde geprüft und abgeschlossen. `src/eeg_optimizer/sources/awattar.py` lädt die Daten in Monats-Chunks nach Ortszeit und cacht sie unter `awattar/<jahr>-<monat>`. `parse_marketdata` wirft bei Lücken, Duplikaten, Stunden außerhalb des Zeitraums, falscher Einheit oder Nicht-Stundenzeilen einen `AwattarError`. Die Stundenwerte werden über `rate_to_15min` auf 15 min wiederholt. Neu in `units` ist `eur_per_mwh_to_ct_per_kwh` (skalar und für Arrays). Fixtures: 2.7.2023 und der DST-Tag 26.3.2023. Der Network-Test für Januar 2023 ist grün (1,3 s). Die Signaturabweichung von architecture.md §4 steht im Modul-Docstring, sie ist kompatibel und braucht keinen Adapter.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit):
  - `to_15min` ist ein reiner Wrapper um `rate_to_15min`.
  - Das erwartete 15-min-Raster wird in `load_month` lokal neu berechnet.
  - Die Preisgrenzen im Network-Test (5–30 ct/kWh im Mittel) sind grob.
  - Ab 1.10.2025 gibt es 15-min-Day-Ahead-Produkte. Sie werden bewusst mit Fehler abgelehnt, nur Stundenprodukte sind unterstützt.
- Fehlertypen: signatur-abweichung
- Nächster Schritt: `scripts/next_feature.sh`.

## 2026-11-13 – F1-06 – Haushaltslast aus HTW-Berlin-Profilen
- Ergebnis: PASS (Evaluator 2× PASS, vor und nach den Reviewer-Fixes; Reviewer OK)
- Gemacht: `src/eeg_optimizer/sources/htw.py` mit folgenden Funktionen:
  - `download_zip`: lädt `CSV_74_Loadprofiles_1min_W_var.zip` (222 837 241 Bytes) über das öffentliche WebDAV des HTW-Nextcloud-Shares `wLEKCqpAniw4TpQ` (Passwort `profile`). Der Download ist atomar, die Größe wird geprüft, das Archiv landet unter `data/raw/htw/`.
  - `read_samples`: summiert PL1+PL2+PL3 in W und liest die Zeitstempel als feste MEZ (`Etc/GMT-1`).
  - `aggregate_samples`: über `timeseries.aggregate_energy` von 1 min auf 15 min kWh.
  - `map_to_year`: wochentagsgetreu über MEZ-Tage mit konstantem Versatz k ∈ −3..3 (2023 ← 2010: +2) und ±7-Tage-Umbruch an den Jahresenden. Für 2023 fallen die DST-Tage genau aufeinander.
  - `load_all_profiles`: parst alle 74 Profile einmal und cacht sie als `htw/profiles_2010_15min`.
  - `load_household(profile, year=2023)` liefert `load_kwh`. `check_profile` lässt nur 1..74 zu, sonst kommt ein `HtwError` mit deutscher Meldung.

  URL, Lizenz (CC BY-NC 4.0), Zitation, Originalzeitzone (MEZ ganzjährig, Jahr 2010) und Abbildungsregel stehen im Modul-Docstring, Kurzfassung in architecture.md §4. Jahresverbräuche 2010: 1398,5 kWh (Profil 6) bis 8635,1 kWh (Profil 46), Profil 1 = 3238,7 kWh, auf 2023 abgebildet 3228,2 kWh. Tests: `tests/unit/test_htw.py` (Fixture-ZIP mit 2 Tagen und Profilen 1–2, Stichtag Montag, DST-Tage, Jahresende, Schaltjahr, Cache, Download-Mocks, Network-Test 146 s) und `tests/property/test_htw_properties.py`. `tests/fixtures/README.md` nennt Herkunft und Lizenz der Fixtures.
- **Bewusste Abweichung:** Die Spec (§7.2) und die Feature-Beschreibung sagen „1 s → 15 min“, verwendet wird aber die 1-min-Variante des Datensatzes (0,21 GB statt 5,4 GB gezippt bzw. 45 GB entpackt). Laut HTW-Readme gehören beide Varianten zum selben Datensatz, die 1-min-Werte sind Mittelwerte der Sekundenwerte. Für die 15-min-Energie ist das gleichwertig, das wurde aber nicht gegen die 1-s-Daten geprüft. Spec §7.2 sollte der planner anpassen.
- Neue Abhängigkeiten: – (requests war schon da)
- Offene Probleme (minor/nit):
  - Der „dokumentierte Bereich“ (`ANNUAL_KWH_MIN/MAX`, `PROFILE_1_ANNUAL_KWH`) wurde aus demselben Download berechnet, es gibt keine externe Referenz. Gegen veränderte Daten schützt die Größenprüfung des ZIPs.
  - In anderen Jahren als 2023 können die Tage zwischen den Zeitumstellungen von Quelle und Ziel um 1 h verschoben sein (höchstens 6 Tage, z. B. 2024). Feiertage werden nicht berücksichtigt.
  - Der erste Parse dauert ca. 5 s und braucht ca. 1 GB RAM, weil alle 74 Spalten pro Phase gelesen werden.
  - Die Signatur `load_household(profile, *, year, cache, zip_path)` weicht von architecture.md (`LoadConfig`) ab. Für F2 braucht es einen Adapter.
  - Die Fixture ist ein Ausschnitt öffentlicher Fremddaten (CC BY-NC), keine eigenen Verbrauchsdaten. Laut Acceptance ist das so gewollt.
- Fehlertypen: signatur-abweichung, spec-abweichung
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F1-07 aWATTar).

## 2026-11-13 – F1-05 – PV-Erzeugung aus PVGIS
- Ergebnis: PASS (Evaluator 2× PASS, vor und nach den Reviewer-Fixes; Reviewer OK)
- Gemacht: `src/eeg_optimizer/sources/pvgis.py` mit folgenden Funktionen:
  - `hourly_to_15min(raw)`: PVGIS-Stundenwerte (UTC, Stempel `HH:10` liegt **innerhalb** der Stunde, `P` = mittlere Leistung in W) werden auf den Stundenbeginn abgerundet. Danach W → kWh über `units`, Umrechnung nach Europe/Vienna, gleichmäßig auf 4 Viertelstunden verteilt. Uneinheitliche Minuten-Versätze, NaN/inf/negativ und Lücken führen zu einem Fehler. Die Konvention steht im Modul-Docstring.
  - `map_to_year(frame, year)`: Das PVGIS-Jahr ist ein UTC-Jahr. Die letzte UTC-Stunde (31.12. 23:00 UTC, Nacht) wird an den Anfang des Wiener Jahres verschoben, die Jahressumme bleibt erhalten.
  - `load_pv(*, kwp, tilt_deg, azimuth_deg, latitude_deg, longitude_deg, year=2023, loss_pct=14, cache=None)`: azimuth 0 = Süd (PVGIS-Konvention), an pvlib geht er als +180. Die Parameter werden vor dem Request geprüft. Gecacht wird der fertige 15-min-Frame, der Key enthält die exakten Parameterwerte. Bei einem Cache-Hit werden Spalte und Jahresindex geprüft. Der HTTP-Timeout ist explizit (60 s).
  - Fixture `tests/fixtures/pvgis_linz_10kwp_30deg_south_2023-06-20_3d.json` (echte PVGIS-Antwort, gekürzt auf 72 h). Tests: `tests/unit/test_pvgis.py` (Fixture, Mock auf pvlib- und auf requests-Ebene, Cache, Fehlerpfade, Network-Test) und `tests/property/test_pvgis_properties.py`.
  - Real-API: Linz, 10 kWp, 30°, Süd, 2023 ergibt 11 281,7 kWh.
- Neue Abhängigkeiten: – (pvlib war schon deklariert)
- Offene Probleme (minor/nit):
  - Signatur weicht von architecture.md (`load_pv(cfg, loc, year, cache)`) ab, weil PvConfig/Location erst mit F2 kommen. Dann braucht es einen dünnen Adapter.
  - `map_to_year` setzt für 1.1. 00:00–01:00 lokal die Nachtstunde 31.12. 23:00 UTC desselben Datenjahres ein. Bei österreichischen Breiten ist das harmlos (PV = 0), in spec §7 aber nicht erwähnt.
  - Azimuth `-0.0` und `0.0` erzeugen verschiedene Cache-Keys, das kostet höchstens einen zusätzlichen Download.
  - Der Fixture wurde mit `components=1` aufgenommen, der Code fragt `components=False` ab. Für die Spalte `P` ist das ohne Wirkung.
  - Verfügbare PVGIS-Jahre (SARAH3) reichen bis 2023. Spätere Jahre scheitern mit einem HTTP-Fehler von PVGIS, es gibt keinen Rückfall.
- Fehlertypen: signatur-abweichung
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F1-06 HTW-Lastprofile).

## 2026-11-13 – F1-04 – Synthetische Beispieldaten
- Ergebnis: PASS (Evaluator 2× PASS, vor und nach den Reviewer-Fixes; Reviewer OK)
- Gemacht: `src/eeg_optimizer/sources/synthetic.py` enthält vier Generatoren. Sie nehmen nur Schlüsselwortargumente, sind deterministisch per Seed und liefern ein ganzes Jahr im 15-min-Raster, das `validate_frame` besteht:
  - `synthetic_pv(kwp, seed, year, latitude_deg, longitude_deg)`: einfache Sonnenstandsrechnung (Intervallmitte, Zeitgleichung), PR 0,85 und Klarheitsindex pro Tag. Die Leistung ist ≤ kWp, das ergibt ca. 1100–1170 kWh/kWp in Linz. Nur Nordhalbkugel (0–66° N).
  - `synthetic_load(seed, annual_kwh=None, year)`: Spitzen morgens, mittags und abends, Wochenende verschoben, Winterzuschlag, lognormales Rauschen. Normiert exakt auf `annual_kwh`. Ohne Angabe wird der Wert aus 2500–4500 kWh gezogen, über einen eigenen Zufallsstrom.
  - `synthetic_spot_prices(seed, year)`: ca. 10 ct/kWh, stundenkonstant, gelegentlich negativ zu sonnigen Mittagen.
  - `synthetic_co2_intensity(seed, year)`: ca. 180 g/kWh, immer ≥ 30.

  Jede Größe nutzt einen eigenen RNG-Strom `[seed, stream]`. numpy-Integer-Seeds werden akzeptiert, Fehler werfen `SyntheticDataError` mit deutscher Meldung. Tests: `tests/unit/test_synthetic.py` und `tests/property/test_synthetic_properties.py` (beliebige Seeds und kWp bzw. Jahresverbrauch).
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit):
  - Die Signaturen weichen von der Quellen-Schnittstelle in docs/architecture.md (Abschnitt `sources/*`, `load_pv(cfg, loc, year, cache)`) ab, deshalb braucht F2-01/F2-03 einen Adapter. Laut Akzeptanz ist das so gewollt.
  - PV wird über `power_kw * kw_to_kwh(KW(1.0))` umgerechnet (Faktor aus units, wie in timeseries.py). Eine Array-Variante in `units.py` wäre sauberer, spätestens für F1-05 überlegen.
  - Der Architekturtest (`* 0.25`) schlug bei einer Saisonamplitude von 0,25 falsch an. Gelöst über eine benannte Konstante (0,3).
  - PV ignoriert Neigung und Azimut (horizontale Fläche). Für Demo und Tests reicht das.
- Fehlertypen: signatur-abweichung, architekturtest-fehlalarm
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F1-05 PVGIS).

## 2026-11-13 – F1-03 – Parquet-Cache für Datenquellen
- Ergebnis: PASS (Evaluator 2× PASS, vor und nach den Reviewer-Fixes; Reviewer OK)
- Gemacht: `src/eeg_optimizer/sources/cache.py` mit folgenden Teilen:
  - `Cache(directory, allow_fetch=True)` mit `get_or_fetch(source, key, fetch_fn)`. Dateien liegen unter `<dir>/<source>/<key>.parquet`. Ein Miss gibt den zurückgelesenen Frame zurück, damit Miss und Hit identisch sind (Parquet verliert `index.freq`). Mit `allow_fetch=False` wirft ein Miss `CacheMissError`, für Tests mit Fixture-Cache.
  - Namen werden validiert (kein Pfadtrenner, kein `..`, kein führender Punkt). Das Schreiben ist atomar über `mkstemp` + `os.replace`.
  - Nicht-DataFrame-Ergebnisse, Spaltennamen, die keine Strings sind, und korrupte Dateien führen zu `CacheError` mit deutscher Meldung.
  - `data_dir()`/`default_cache()` lesen `EEG_DATA_DIR` (Default `<projekt>/data`, Cache unter `data/cache`).

  Tests: `tests/unit/test_cache.py` (Zähler, Roundtrip am DST-Tag, ganzes Jahr, UTC, Env-Override, `git check-ignore`, unsichere Namen, Fehlerpfade) und `tests/property/test_cache_properties.py` (beliebige gültige Frames 2023 überstehen den Roundtrip).
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit):
  - `PROJECT_ROOT` (parents[3]) setzt die editierbare Installation voraus, wie schon in `health.py`.
  - Greifen zwei Sessions gleichzeitig auf denselben fehlenden Eintrag zu, wird doppelt geladen. Die Datei bleibt dank atomarem Ersetzen gültig, es gibt keinen Lock.
  - Ein relatives `EEG_DATA_DIR` wird gegen das CWD aufgelöst (im Docstring dokumentiert).
  - Cache-Dateien haben Modus 0600 (von mkstemp).
- Fehlertypen: –
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F1-04 Synthetische Beispieldaten).

## 2026-11-13 – F1-02 – Internes Zeitraster und Resampling
- Ergebnis: PASS (Evaluator 2× PASS, vor und nach den Reviewer-Fixes; Reviewer OK)
- Gemacht: `src/eeg_optimizer/timeseries.py` mit folgenden Funktionen:
  - `year_index(year)`: 15-min-Raster über UTC gebaut, ergibt 35040 Intervalle, an den DST-Tagen 92/100, im Schaltjahr 35136.
  - `hourly_energy_to_15min`: extensive Größen (`_kwh`, `_eur`, `_kg_co2`) werden durch 4 geteilt.
  - `rate_to_15min`: Raten (`_kw`, `_ct_per_kwh`, `_g_per_kwh`) werden unverändert wiederholt.
  - `aggregate_energy(df, sample_interval)`: summiert `_kwh`/`_kw`/`_w`-Abtastwerte zu 15-min-kWh. Unvollständige oder verschobene Intervalle, Lücken, NaN, negative Werte und doppelte oder unbekannte Spalten führen zu einem `ResampleError`.

  Neu in `units.py` ist `w_to_kw`, damit die Einheitenumrechnung nur dort passiert. Tests: `tests/unit/test_timeseries.py` (inkl. DST-Tage) und `tests/property/test_timeseries_properties.py` (Energieerhalt mit Toleranz 1e-9, Viertel == Stunde/4, Raten unverändert, Aggregation 1 s bis 5 min in W).
- Eigener Bug beim Testen gefunden: `price_ct_per_kwh` wurde in `aggregate_energy` als `_kwh` erkannt, weil die Suffixe nicht vollständig abgeglichen wurden. Behoben, ein Test sichert das ab.
- Neue Abhängigkeiten: –
- Offene Probleme (minor/nit):
  - Hourly-Funktionen verlangen Eingang in Europe/Vienna. UTC-Quellen (PVGIS, F1-05) müssen vorher `tz_convert` aufrufen.
  - `hourly_energy_to_15min`/`rate_to_15min` lehnen auch Nicht-Zahlen-Metadatenspalten ab (z. B. `household_id`). Diese Spalten vorher entfernen.
  - Die Suffix-Klassifikation (extensiv/intensiv) ist eine eigene Liste neben `units.ALLOWED_SUFFIXES`. Ein Unit-Test prüft, dass sie deckungsgleich sind.
- Fehlertypen: einheiten-suffix
- Nächster Schritt: `scripts/next_feature.sh` (voraussichtlich F1-03 Parquet-Cache).

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
