# EEG-Optimizer – Architektur

## 1. Stack und Begründung

| Baustein | Wahl | Begründung |
|---|---|---|
| Sprache | Python 3.12, `venv` in `.venv/`, `pyproject.toml`, `requirements.lock` | Ökosystem für Energie-Daten (pvlib, pandas). Der Lockfile sorgt für dieselbe Umgebung in jeder Session. |
| Daten | pandas, numpy | Zeitreihen mit Zeitzonen und Resampling |
| PV | pvlib (`iotools.get_pvgis_hourly`) | Offizieller PVGIS-Client, kein eigener HTTP-Code nötig |
| Optimierung | PuLP **2.9.x** (`<3`) + CBC | Frei, ohne Lizenz, MILP-fähig. CBC wird mitgeliefert (`PULP_CBC_CMD`). PuLP 3/4 ändern die API grundlegend (kein `LpStatus`, Variablen nur über `problem.add_variable`) und liefern CBC nur noch als Extra. Gepinnt, damit Code und Doku zusammenpassen. |
| HTTP | requests | aWATTar, Energy-Charts, HTW-Download |
| Konfiguration | PyYAML → typisierte `dataclass`es | Menschenlesbar. Die Validierung erfolgt an einer Stelle beim Laden. |
| Cache | Parquet (`pyarrow`) | Schnell, typerhaltend, ohne Datenbank |
| UI | Streamlit + Plotly | Schnelles Dashboard ohne Frontend-Build. Interaktive Diagramme. |
| Qualität | ruff, mypy `--strict`, pytest + hypothesis, Playwright (Python) | Lint/Format, Typsicherheit, Property-Tests für Invarianten, E2E wie ein echter Nutzer |

Zusätzliche Abhängigkeiten: `pyarrow` (Parquet), `pandas-stubs`, `types-PyYAML`, `types-requests` (für mypy `--strict`).

## 2. Ordnerstruktur

```
eeg_optimizer/
├── CLAUDE.md  init.sh  feature_list.json  progress.md  pyproject.toml  requirements.lock
├── .claude/{settings.json, agents/, skills/}
├── config/
│   ├── defaults.yaml              # Tarife, CO₂-/Modellannahmen (spec §7)
│   └── example_community.yaml     # Beispielgemeinschaft (5 Haushalte, 2023)
├── src/eeg_optimizer/
│   ├── units.py                   # Einheiten-Typen, Spalten-Suffixe, Validierung
│   ├── timeseries.py              # internes Format: Raster, DST, Resampling
│   ├── config.py                  # YAML → dataclasses (CommunityConfig, …)
│   ├── sources/                   # ein Modul pro Quelle, alle → internes Format
│   │   ├── cache.py  pvgis.py  htw.py  opsd.py  awattar.py  energy_charts.py  smartmeter.py
│   ├── community.py               # baut Gemeinschafts-Zeitreihen aus Config + Quellen
│   ├── balance.py                 # shared/export/import, KPIs (Baseline)
│   ├── allocation.py              # Verteilschlüssel (Strategy)
│   ├── devices.py                 # flexible Lasten, Baseline-Fahrplan
│   ├── optimize/
│   │   ├── objectives.py  milp.py  greedy.py  runner.py  # runner: Tageszerlegung, Fallback
│   ├── economics.py               # €, CO₂
│   └── compare.py                 # Baseline vs. optimiert
├── app/
│   ├── streamlit_app.py           # Einstieg, Navigation
│   └── pages/                     # Konfiguration, Ist-Analyse, Optimierung, Vergleich, Export
├── tests/
│   ├── unit/  property/  e2e/     # e2e = Playwright gegen laufende App
│   ├── fixtures/                  # kleine Offline-Dateien (pro Quelle ein Beispiel)
│   └── conftest.py
├── scripts/autoloop.sh            # optionale headless Schleife
├── data/raw/  data/cache/         # gitignored
└── logs/                          # gitignored
```

## 3. Datenmodell

### 3.1 Internes Zeitreihenformat (INV-4, INV-6)

- `pandas.DataFrame` mit `DatetimeIndex`, tz `Europe/Vienna`, Frequenz 15 min, lückenlos. Der Zeitstempel bezeichnet den **Intervallbeginn**.
- Intern wird in UTC gerechnet und für die Darstellung nach Europe/Vienna konvertiert, damit DST-Tage korrekt 92 bzw. 100 Intervalle haben.
- **Einheiten über Spalten-Suffix**, Pflicht für jede numerische Spalte:
  `_kwh` (Energie pro Intervall), `_kw` (Leistung), `_eur`, `_ct_per_kwh`, `_g_per_kwh`, `_kg_co2`.
- `units.validate_frame(df)` prüft Raster, Zeitzone, Suffixe, NaN und negative Energien. Es wird an jeder Modulgrenze aufgerufen (Ausgang jeder Quelle, Eingang von balance/optimize).
- Für Skalare gibt es `NewType`s: `KWh`, `KW`, `Eur`, `KgCO2`. Umrechnung nur über `units.kw_to_kwh(p, interval)` und `units.kwh_to_kw(e, interval)`. mypy `--strict` verhindert eine Vermischung.
- Ausnahme für aggregierte Kennzahl-Tabellen (kein 15-min-Raster, z. B. `balance.monthly_kpis`): dimensionslose Anteile in [0, 1] tragen das Suffix `_ratio`. Sie sind keine Zeitreihen und gehen nicht durch `validate_frame`.
- Ein Architekturtest prüft, dass `* 0.25` bzw. `/ 0.25` außerhalb von `units.py` nicht vorkommt.

### 3.2 Kanonische Spalten

| Frame | Spalten |
|---|---|
| PV | `pv_kwh` |
| Last (pro Haushalt) | `load_kwh` |
| Preis | `spot_ct_per_kwh` |
| CO₂ | `co2_g_per_kwh` |
| Gemeinschaft | `pv_kwh`, `load_kwh`, `shared_kwh`, `export_kwh`, `grid_import_kwh` |
| Haushalt h | `load_kwh`, `shared_kwh`, `grid_import_kwh` (Long-Format mit Spalte `household_id`) |

### 3.3 Konfiguration (YAML → dataclasses)

```yaml
period: {year: 2023}
location: {lat: 48.31, lon: 14.29}
pv: {kwp: 30.0, tilt_deg: 30, azimuth_deg: 0}       # 0 = Süd (PVGIS-Konvention)
tariffs: {eeg_type: local, ...}                     # überschreibt defaults.yaml
allocation: {strategy: dynamic_proportional}
optimization: {objective: max_shared, solver: milp} # max_shared | min_cost | min_co2; milp | greedy
households:
  - id: hh01
    load: {source: htw, profile: 12}                # oder {source: smartmeter, path: ...}
    devices:
      - {id: ev, kind: modulating, power_kw: 11.0, energy_kwh_per_day: 8.0,
         window: {start: "18:00", end: "07:00"}, baseline_start: "18:00"}
      - {id: wm, kind: program, power_kw: 2.0, duration_min: 90,
         window: {start: "08:00", end: "20:00"}, baseline_start: "19:00"}
```

Laden (`config.py`): `load_config(path, defaults_path=config/defaults.yaml)` führt die Gemeinschaftsdatei über die Defaults zusammen (Abschnitte Schlüssel für Schlüssel, Listen werden ersetzt) und validiert an einer Stelle. Fehler werfen `ConfigError` mit deutscher Meldung und Schlüsselpfad (z. B. `pv.kwp: muss größer als 0 sein`). Unbekannte Schlüssel sind Fehler. Weitere Abschnitte: `pv.source` (pvgis | synthetic), `prices: {source: awattar | synthetic}`, `co2: {source: energy_charts | synthetic}`, `load.source` (htw | synthetic | smartmeter), optional `seed`. Erlaubt sind 1–15 Haushalte mit eindeutiger `id`. Ist `tariffs.grid_fee_reduction` nicht gesetzt (`null`), folgt der Wert aus `eeg_type` (`TariffConfig.effective_grid_fee_reduction`). `devices` bleiben rohe Mappings, die `devices.py` (F4-01) validiert. `save_config` schreibt die vollständig zusammengeführte Konfiguration, damit gilt `load_config(save_config(cfg)) == cfg`.

Gerätearten:
- **`modulating`** (E-Auto, Boiler, Wärmepumpe): Leistung stufenlos 0…`power_kw`. Optional sind `min_run_min` (zusammenhängende Laufzeit) und `min_power_kw` (Mindestleistung im Betrieb, z. B. Wallbox 1.4 bzw. 4.1). Beide sind standardmäßig aus, dann bleibt das Gerät rein linear (LP). Erst wenn eines davon gesetzt ist, kommen Binärvariablen hinzu (MILP).
- **`program`** (Waschmaschine, Geschirrspüler): feste Leistung für feste Dauer am Stück, nur der Start ist frei.

Fenster dürfen über Mitternacht gehen. Eine Aufgabe gehört zum Tag ihres Fensterbeginns.

## 4. Schnittstellen (Kernfunktionen)

**HTW-Lastprofile (`sources/htw.py`):** Tjaden et al., HTW Berlin, Lizenz CC BY-NC 4.0. Geladen wird `CSV_74_Loadprofiles_1min_W_var.zip` (0,21 GB) aus dem Nextcloud-Share `https://cloud.htw-berlin.de/s/wLEKCqpAniw4TpQ` (Passwort `profile`). Die 1-s-Variante (5,4 GB) wird nicht verwendet: Die 1-min-Werte sind Mittelwerte und liefern dieselbe 15-min-Energie. Originalzeitzone ist ganzjährig MEZ (UTC+1, ohne Sommerzeit), Datenjahr 2010. Das Roharchiv liegt unter `data/raw/htw/`, alle 74 Profile werden einmal geparst und als 15-min-Frame gecacht. Wochentagsgetreu auf das Zieljahr abgebildet wird in MEZ-Tagen, Details im Modul-Docstring.

**Spotpreise (`sources/awattar.py`):** `GET https://api.awattar.at/v1/marketdata?start=<ms>&end=<ms>`, ohne API-Key, Timeout 30 s. Zeitstempel sind UTC-Millisekunden (Intervallbeginn), Preise in €/MWh (EPEX Day-Ahead, Gebotszone AT). Geladen wird in Monats-Chunks nach Ortszeit, jeder Monat wird als `awattar/<jahr>-<monat>` gecacht. Die Umrechnung €/MWh → ct/kWh läuft über `units.eur_per_mwh_to_ct_per_kwh` (÷ 10), die Stundenwerte werden unverändert auf 15 min wiederholt. Negative Preise bleiben erhalten, fehlende Stunden führen zu `AwattarError`.

**CO₂-Intensität (`sources/energy_charts.py`):** `GET https://api.energy-charts.info/public_power?country=at&start=<UTC-ISO>&end=<UTC-ISO>` (beide Grenzen inklusive), ohne API-Key, Timeout 120 s. Es gibt keinen CO₂-Endpunkt für AT. `co2_g_per_kwh` wird pro 15-min-Intervall als erzeugungsgewichtetes Mittel der Emissionsfaktoren berechnet (Methode und Faktoren: spec §7.2). Geladen wird **ein Request pro Jahr**, Monats-Chunks laufen in das Rate-Limit (HTTP 429). Gecacht wird als `energy_charts/co2-<FACTORS_VERSION>-<jahr>`. Ändert sich ein Faktor, muss `FACTORS_VERSION` erhöht werden. Unbekannte Erzeugungsarten, `null`-Werte, negative Erzeugung und Lücken führen zu `EnergyChartsError`.

```python
# sources/*: jede Quelle
def load_pv(cfg: PvConfig, loc: Location, year: int, cache: Cache) -> pd.DataFrame        # pv_kwh
def load_household(cfg: LoadConfig, year: int, cache: Cache) -> pd.DataFrame              # load_kwh
def load_spot_prices(year: int, cache: Cache) -> pd.DataFrame                             # spot_ct_per_kwh
def load_co2_intensity(year: int, cache: Cache) -> pd.DataFrame                           # co2_g_per_kwh

# community.py – lädt alle Quellen einer Konfiguration
def build_inputs(config: CommunityConfig, cache: Cache | None = None) -> CommunityInputs
# CommunityInputs(pv, loads: Mapping[household_id, DataFrame], prices, co2), gemeinsamer Index

# balance.py
def community_balance(pv: pd.DataFrame, loads: Mapping[str, pd.DataFrame]) -> pd.DataFrame
# Eingang: pv genau `pv_kwh`, jede Last genau `load_kwh`, ≥ 1 Haushalt, gleicher Index, sonst BalanceInputError.
# Ausgang (BALANCE_COLUMNS): pv_kwh, load_kwh (Summe), shared_kwh = min(pv, load),
# export_kwh = pv − shared, grid_import_kwh = load − shared (INV-1, alle ≥ 0)
def kpis(balance: pd.DataFrame) -> Kpis   # self_consumption_ratio, autarky_ratio, export_kwh, grid_import_kwh, shared_kwh
# Kpis (frozen dataclass): pv_kwh, load_kwh, shared_kwh, export_kwh, grid_import_kwh als KWh,
# self_consumption_ratio = shared/pv, autarky_ratio = shared/load als float in [0, 1];
# Nenner 0 (keine PV bzw. keine Last) -> 0.0, nie NaN.
def monthly_kpis(balance: pd.DataFrame) -> pd.DataFrame
# Eine Zeile je Kalendermonat (Ortszeit), Index `month` = Monatsbeginn tz Europe/Vienna (12 für ein Jahr),
# Spalten MONTHLY_KPI_COLUMNS = BALANCE_COLUMNS (Monatssummen) + self_consumption_ratio, autarky_ratio.
# Eingang beider Funktionen: Ausgang von community_balance, sonst BalanceInputError/FrameValidationError.

# allocation.py
class AllocationStrategy(Protocol):
    def allocate(self, balance: pd.DataFrame, loads: Mapping[str, pd.DataFrame]) -> pd.DataFrame
class DynamicProportional: ...   # shared_h = load_h · shared / Σload (0 bei Σload = 0), grid_import_h = load_h − shared_h
def strategy_for(config: AllocationConfig) -> AllocationStrategy   # Registry STRATEGIES: allocation.strategy -> Klasse
# Eingang: balance = community_balance(pv, loads) zu genau diesen loads (Abweichung > 1e-6 kWh -> AllocationInputError).
# Ausgang: Spalten-MultiIndex (household_id, quantity), Haushalte in der Reihenfolge von loads;
# household_ids(result), household_frame(result, hh) -> flacher Frame mit HOUSEHOLD_COLUMNS =
# load_kwh, shared_kwh, grid_import_kwh (validate_frame); community_totals(result) = Summe über Haushalte.
# INV-7: community_totals(result).shared_kwh == balance.shared_kwh pro Intervall (dito grid_import, Toleranz 1e-6).

# devices.py
def baseline_schedule(device: Device, index: pd.DatetimeIndex) -> pd.Series   # device_kwh

# optimize/runner.py
def optimize(inputs: OptimizationInputs, objective: Objective, solver: Literal["milp","greedy"]) -> Schedule

# economics.py / compare.py
def value_shared_ct_per_kwh(tariffs: TariffConfig) -> float   # spec §7.1, Default local 9.42, regional 7.68
def costs(balance, allocation, tariffs: TariffConfig, prices: pd.DataFrame | None = None) -> CostResult
# prices None = fixer Tarif (energy_price); Frame mit genau `spot_ct_per_kwh` = dynamisch (spot + spot_markup),
# betrifft nur den Energiepreis des Netzbezugs, eeg_internal_price bleibt fix. Cent → € nur über units.ct_to_eur.
# CostResult (frozen): tariff ("fixed" | "spot"); community = Frame pro Intervall mit COMMUNITY_COST_COLUMNS
# (grid_import_cost_eur, shared_cost_eur, export_revenue_eur, community_cost_eur = import + shared − export);
# community_eur: Eur (Summe); households = Tabelle, Index household_id (Reihenfolge der Allocation),
# Spalten grid_import_cost_eur, shared_cost_eur (shared_h × interner Preis inkl. ermäßigtem Netzentgelt und Abgaben),
# cost_eur; household_eur(hh) -> Eur. Fehler: CostInputError (Spalten, Index, Allocation passt nicht, Toleranz 1e-6 kWh).
def emissions(balance, allocation, co2: pd.DataFrame) -> EmissionResult
# emissions_kg_co2 = grid_import_kwh × co2_g_per_kwh / 1000 pro Intervall (g → kg nur über units.g_to_kg_co2);
# PV (shared und Export) zählt mit 0 g/kWh, Export ohne Gutschrift. co2 = genau `co2_g_per_kwh` (≥ 0) auf dem
# Index der Balance. EmissionResult (frozen): community = Frame pro Intervall mit `emissions_kg_co2`,
# community_kg_co2: KgCO2 (Summe); households = Tabelle, Index household_id, Spalte `emissions_kg_co2`
# (grid_import_h × co2 je Intervall, Summe == Gemeinschaft dank INV-7); household_kg_co2(hh) -> KgCO2.
# Vermiedene kg CO₂ = Differenz zweier Ergebnisse (F6-01). Fehler: EmissionInputError (Spalten, Index,
# negative Intensität, Allocation passt nicht, Toleranz 1e-6 kWh).
def compare(baseline: Scenario, optimized: Scenario) -> Comparison

# analysis.py (F3-06, ohne Streamlit, auch für optimierte Balance nutzbar)
def analyze(config: CommunityConfig, inputs: CommunityInputs) -> BaselineAnalysis
# BaselineAnalysis (frozen): balance, kpis, monthly (monthly_kpis), profile, surplus, costs (fixer Tarif), emissions.
def daily_profile(balance) -> pd.DataFrame   # 96 Zeilen, Index time_of_day "HH:MM", Spalten pv_kw, load_kw (Mittel)
def surplus_by_day_hour(balance) -> pd.Series   # export_kwh, MultiIndex (day: date, hour: 0..23), Ortszeit
# ASSUMPTION_NOTES: deutsche Hinweistexte (perfekte Voraussicht, HTW-Profile, CO₂ ohne Importe, Tarife).
```

**Quellen-Dispatch (`community.py`):** Die Quellenmodule behalten ihre Schlüsselwort-Signaturen. `community.py` enthält dünne Adapter in der oben gezeigten Config-Form und die Registries `PV_SOURCES`, `LOAD_SOURCES`, `PRICE_SOURCES`, `CO2_SOURCES` (Quellname → Adapter). Eine neue Quelle braucht nur einen Eintrag dort (z. B. `smartmeter` in F8-01). Fehlt ein Eintrag, kommt `CommunitySourceError` mit Schlüsselpfad, z. B. `households[2].load.source: Datenquelle 'smartmeter' ist nicht verfügbar, verfügbar: htw, synthetic`. Jeder Quell-Frame muss genau seine kanonische Spalte haben, `validate_frame` bestehen und exakt `year_index(period.year)` als Index haben.

Die Quellen holen über HTTP nur, wenn der Cache fehlt (`data/cache/<source>/<key>.parquet`). Unit-Tests injizieren einen Cache, der auf `tests/fixtures/` zeigt. `@pytest.mark.network` ist standardmäßig abgewählt.

## 5. Optimierung

- **Ziele** (linear, mit Hilfsvariablen `shared_t ≤ pv_t`, `shared_t ≤ load_t`, `import_t ≥ load_t − shared_t`, `export_t = pv_t − shared_t`):
  - `max_shared`: max Σ shared_t
  - `min_cost`: min Σ import_t·(spot_t + markup + Netzentgelt + Abgaben) + Σ shared_t·(ermäßigtes Netzentgelt + Abgaben) − Σ export_t·Einspeisetarif
  - `min_co2`: min Σ import_t·co2_t
- **Variablen:** `modulating` → kontinuierlich pro Intervall (plus Binärvariablen nur bei `min_run_min`/`min_power_kw`). `program` → eine Binärvariable pro möglichem Start.
- **Zerlegung (INV-5):** Es wird tageweise gelöst. Ein Block umfasst alle Aufgaben, deren Fenster an Tag d beginnt, mit Horizont bis zum spätesten Fensterende (≤ 48 h). Bereits eingeplante Lasten früherer Blöcke gehen als feste Last ein. Die Blöcke sind unabhängig genug für die Parallelisierung per `concurrent.futures` (optional). Erwartet werden ca. 365 kleine MILPs.
- **Garantie INV-3:** Der Runner bewertet Baseline und Ergebnis mit derselben Zielfunktion pro Block. Ist das Ergebnis schlechter oder scheitert der Solver (Timeout, infeasible), greift die Greedy-Heuristik und notfalls der Baseline-Fahrplan des Blocks. `Schedule.fallback_blocks` (Liste mit Datum und Grund) und `Schedule.fallback_ratio` werden im Ergebnis und in der UI ausgewiesen, also nie stillschweigend. Ein Test verlangt `fallback_ratio == 0` für das Standardszenario, und der Evaluator wertet einen Rückfall dort als FAIL.
- **Greedy:** Aufgaben nach Flexibilität sortieren (kleines Fenster zuerst). Energie in die Intervalle mit dem besten Zielwert legen (größter verbleibender Überschuss, niedrigster Preis bzw. niedrigstes CO₂), unter Beachtung von Leistung und Fenster.

## 6. UI (Streamlit)

Seiten: **Konfiguration** (YAML laden/bearbeiten/speichern), **Ist-Analyse** (KPIs, Tages-/Monatsprofil, Heatmap), **Optimierung** (Ziel und Solver wählen, Start, Laufzeit), **Vergleich** (pro Haushalt und gesamt), **Export** (CSV). Hinweisbox auf jeder Ergebnisseite: „Obergrenze bei perfekter Voraussicht. Annahmen siehe …“. Neben dem Optimierungsergebnis stehen außerdem „pro Tagesblock optimal, nicht global“ und die Anzahl der Rückfall-Blöcke. Für Playwright erhalten wichtige Elemente stabile Labels bzw. `key`s. Berechnungen werden per `st.cache_data` gecacht.

**Aktive Konfiguration (F2-04):** Die Seite Konfiguration (`app/pages/configuration.py`, URL `/konfiguration`) legt die gewählte bzw. übernommene Konfiguration als `CommunityConfig` in `st.session_state["community_config"]` ab (Konstante `config_store.SESSION_CONFIG_KEY`), die Herkunftsdatei in `SESSION_PATH_KEY`. Fehlt der Eintrag, laden andere Seiten `config_store.DEFAULT_CONFIG_PATH`. Die Logik ohne Streamlit liegt in `config_store.py`: `available_configs` (Beispiele aus `config/` ohne `defaults.yaml` plus `data/configs/*.yaml`), `apply_edits(cfg, teil_mapping)` (Validierung nur über `parse_config`), `resize_households` und `save_user_config` (nach `data/configs/<name>.yaml`, Name nur `[A-Za-z0-9_-]`). Ungültige Eingaben zeigen die `ConfigError`-Meldung und lassen die zuletzt gültige Konfiguration aktiv.

**Ist-Analyse (F3-06):** `app/pages/analysis.py`, URL `/ist-analyse`. `st.cache_data` auf `_analyze(config_json)`, Schlüssel ist `config_to_dict` als JSON (sortiert). KPI-Kacheln sind `st.metric` mit eindeutigen Labels, die Diagramme haben die Keys `analysis_profile`, `analysis_monthly`, `analysis_heatmap`, die Hinweisbox den Key `analysis_assumptions` (Text aus `analysis.ASSUMPTION_NOTES`). Fehler beim Laden der Zeitreihen werden mit Meldung angezeigt (`st.error`), nicht verschluckt.

## 7. Tests

| Ebene | Inhalt | Läuft |
|---|---|---|
| `tests/unit` | Parser je Quelle gegen Fixtures, Balance, Economics, Config-Validierung | offline, immer |
| `tests/property` | hypothesis: INV-1, INV-2, INV-3, INV-6, INV-7 auf zufälligen kleinen Gemeinschaften | offline, immer |
| `tests/perf` | INV-5 (15 HH × 1 Jahr, synthetische Daten) | offline, Marker `slow` |
| `tests/e2e` | Playwright gegen `streamlit run` auf Port 8501 | Marker `e2e` |
| Marker `network` | echte API-Aufrufe | nur explizit |
