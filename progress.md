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

---

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
