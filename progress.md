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
- Quellen-Signaturen weichen von architecture.md §4 ab: `load_pv(*, kwp, tilt_deg, …)`, `load_household(profile, *, year, cache, zip_path)` und die `synthetic_*`-Generatoren statt `load_pv(cfg, loc, year, cache)` / `load_household(cfg, year, cache)`. F2-03 braucht dünne Adapter auf die jetzt vorhandenen `PvConfig`/`Location`/`LoadConfig` aus `config.py`. (F1-04, F1-05, F1-06, F2-01)
- F2-03/F2-04: Standardszenario ist `config/example_community.yaml` (HTW-Profile 1, 6, 12, 25, 46; braucht Netzwerk), offline `config/example_community_offline.yaml` (alles synthetic, gleiche IDs/PV/Tarife). (F2-02)
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
