# Fortschritt - Archiv

Ältere Einträge aus `progress.md`, neueste oben. Verschoben von `scripts/rotate_progress.py`. Bei Bedarf gezielt durchsuchen (grep), nicht komplett lesen.

---

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
