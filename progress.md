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
- F4-04/F8-02: Die Haushaltszeilen der Konfigurationsseite baut `_household_inputs` in `app/pages/configuration.py`. Geräte (`devices`) bleiben beim Bearbeiten erhalten, haben aber noch keine Widgets. Die Widget-Keys tragen das Suffix `_v<n>` und werden beim Laden einer Datei neu erzeugt. (F2-04)
- F8-01: `smartmeter` ist in der Config erlaubt, hat aber noch keinen Loader. `build_inputs` meldet dann `CommunitySourceError`. F8-01 trägt einen Adapter `(LoadConfig, year, cache) -> load_kwh` in `community.LOAD_SOURCES` ein. (F2-03)
- Das Standardszenario (pvgis/htw/awattar/energy_charts) läuft durch `build_inputs` bisher nur mit gemockten Quellen. Ein echter Lauf gehört in einen Network-Test bzw. in F3/F2-04. (F2-03)
- F4-02/F4-03/F4-04/F5: `devices.devices_for(cfg)` liefert typisierte Geräte je Haushalt (`ModulatingDevice`/`ProgramDevice`, `TimeWindow` mit `contains`/`remaining_after`). `load_config` validiert die Geräte **nicht**; `devices_for` muss beim Aufbau der Eingänge (F4-02) bzw. beim Speichern in der UI (F4-04) aufgerufen werden, damit fehlerhafte Geräte früh auffallen. Uhrzeiten müssen in YAML in Anführungszeichen stehen ("18:00"), Zeiten und Dauern auf dem 15-min-Raster. Zusätzlich zur Spec wird verlangt, dass die Energie ab `baseline_start` bis Fensterende mit `power_kw` erreichbar ist (Baseline-Fahrplan darf das Fenster nicht verlassen); F4-03 muss die Beispielgeräte danach wählen. (F4-01)
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
- F4-03: Geräte, deren Fenster 02:00–03:00 überdeckt, brauchen ≥ 1 h Reserve ab `baseline_start`, sonst wirft `baseline_schedule` am 26.3. `BaselineScheduleError` (Fenster real 1 h kürzer) und die Ist-Analyse bricht ab. (F4-02)
- F5-07 (Rückfall auf Baseline): Der Baseline-Lauf hält `min_power_kw` im letzten Teilintervall und `min_run_min` nicht unbedingt ein. Fällt ein Block auf die Baseline zurück, ist INV-2 dafür nicht garantiert; Runner/Greedy entsprechend prüfen. (F4-02)

---

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
