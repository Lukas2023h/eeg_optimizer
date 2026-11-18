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
- `HouseholdConfig.devices` sind rohe dicts. F4-01 parst und validiert sie zu typisierten Geräten. (F2-01)
- `pv.kwp` muss > 0 sein, eine Gemeinschaft ohne PV ist nicht konfigurierbar. Bei Bedarf auf ≥ 0 lockern. (F2-01)

**Für den planner (Spec anpassen):**
- spec §7.2: HTW nutzt die 1-min-Variante statt „1 s → 15 min“ (gleicher Datensatz, 1-min = Mittel der Sekundenwerte, nicht gegen 1-s geprüft). (F1-06)
- spec §7: PVGIS `map_to_year` setzt für 1.1. 00:00–01:00 lokal die Nachtstunde 31.12. 23:00 UTC ein (PV = 0, harmlos), nicht dokumentiert. (F1-05)

**Für neue Datenquellen und Frames:**
- `hourly_energy_to_15min`/`rate_to_15min` verlangen Europe/Vienna; UTC-Quellen vorher `tz_convert`. Nicht-Zahlen-Metadatenspalten (z. B. `household_id`) vorher entfernen. (F1-02)
- `validate_frame` akzeptiert Zahlen-Strings und object-Spalten **ohne** Suffix; CSV-Quellen (F8-01) müssen Zahlen vorher parsen. tz-Prüfung über `str(tz)`: dateutil-`Europe/Vienna` wird abgelehnt. (F1-01)
- bool-Spalten ohne Suffix werden abgelehnt. Braucht der Optimierer Flag-Spalten, vorher entscheiden und architecture.md §3.1 ergänzen. (F1-01)
- Suffix-Erkennung existiert doppelt: `units._unit_suffix` und `timeseries._suffix_of`. Bei neuer Nutzung nicht ein drittes Mal bauen, sondern `units` öffentlich machen. (F1-02)
- `units.kw_to_kwh` gibt es nur skalar; eine Array-Variante wäre sauberer. Der Architekturtest erkennt nur `0.25`, nicht `/ 4` oder `/ 3600`. (F1-01, F1-04)

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

**Für den planner (Spec anpassen):**
- INV-4: Aggregierte Kennzahl-Tabellen tragen dimensionslose Anteile mit dem Suffix `_ratio` (architecture.md §3.1). In spec §5 und CLAUDE.md „Einheiten“ ist das noch nicht nachgezogen, `units.ALLOWED_SUFFIXES` kennt `_ratio` nicht (Zeitreihen brauchen es nicht). (F3-02)

---

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
