# EEG-Optimizer – Architektur

## 1. Stack und Begründung

| Baustein | Wahl | Begründung |
|---|---|---|
| Sprache | Python 3.12, `venv` in `.venv/`, `pyproject.toml`, `requirements.lock` | Ökosystem für Energie-Daten (pvlib, pandas). Der Lockfile sorgt für dieselbe Umgebung in jeder Session. |
| Daten | pandas, numpy | Zeitreihen mit Zeitzonen und Resampling |
| PV | pvlib (`iotools.get_pvgis_hourly`) | Offizieller PVGIS-Client, kein eigener HTTP-Code nötig |
| Optimierung | PuLP + CBC | Frei, ohne Lizenz, MILP-fähig, CBC wird mitgeliefert |
| HTTP | requests | aWATTar, Energy-Charts, HTW-Download |
| Konfiguration | PyYAML → typisierte `dataclass`es | Menschenlesbar. Die Validierung erfolgt an einer Stelle beim Laden. |
| Cache | Parquet (`pyarrow`) | Schnell, typerhaltend, ohne Datenbank |
| UI | Streamlit + Plotly | Schnelles Dashboard ohne Frontend-Build. Interaktive Diagramme. |
| Qualität | ruff, mypy `--strict`, pytest + hypothesis, Playwright (Python) | Lint/Format, Typsicherheit, Property-Tests für Invarianten, E2E wie ein echter Nutzer |

`pyarrow` (Parquet) und `pandas-stubs` (für mypy) sind als zusätzliche Abhängigkeiten nötig.

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

Gerätearten:
- **`modulating`** (E-Auto, Boiler, Wärmepumpe): Leistung stufenlos 0…`power_kw`, optional `min_run_min` (zusammenhängende Laufzeit).
- **`program`** (Waschmaschine, Geschirrspüler): feste Leistung für feste Dauer am Stück, nur der Start ist frei.

Fenster dürfen über Mitternacht gehen. Eine Aufgabe gehört zum Tag ihres Fensterbeginns.

## 4. Schnittstellen (Kernfunktionen)

```python
# sources/*: jede Quelle
def load_pv(cfg: PvConfig, loc: Location, year: int, cache: Cache) -> pd.DataFrame        # pv_kwh
def load_household(cfg: LoadConfig, year: int, cache: Cache) -> pd.DataFrame              # load_kwh
def load_spot_prices(year: int, cache: Cache) -> pd.DataFrame                             # spot_ct_per_kwh
def load_co2_intensity(year: int, cache: Cache) -> pd.DataFrame                           # co2_g_per_kwh

# balance.py
def community_balance(pv: pd.DataFrame, loads: Mapping[str, pd.DataFrame]) -> pd.DataFrame
def kpis(balance: pd.DataFrame) -> Kpis   # self_consumption_ratio, autarky_ratio, export_kwh, grid_import_kwh, shared_kwh

# allocation.py
class AllocationStrategy(Protocol):
    def allocate(self, balance: pd.DataFrame, loads: Mapping[str, pd.DataFrame]) -> pd.DataFrame
class DynamicProportional: ...

# devices.py
def baseline_schedule(device: Device, index: pd.DatetimeIndex) -> pd.Series   # device_kwh

# optimize/runner.py
def optimize(inputs: OptimizationInputs, objective: Objective, solver: Literal["milp","greedy"]) -> Schedule

# economics.py / compare.py
def costs(balance, tariffs, prices) -> CostResult
def emissions(balance, co2) -> EmissionResult
def compare(baseline: Scenario, optimized: Scenario) -> Comparison
```

Die Quellen holen über HTTP nur, wenn der Cache fehlt (`data/cache/<source>/<key>.parquet`). Unit-Tests injizieren einen Cache, der auf `tests/fixtures/` zeigt. `@pytest.mark.network` ist standardmäßig abgewählt.

## 5. Optimierung

- **Ziele** (linear, mit Hilfsvariablen `shared_t ≤ pv_t`, `shared_t ≤ load_t`, `import_t ≥ load_t − shared_t`, `export_t = pv_t − shared_t`):
  - `max_shared`: max Σ shared_t
  - `min_cost`: min Σ import_t·(spot_t + markup + Netzentgelt + Abgaben) + Σ shared_t·(ermäßigtes Netzentgelt + Abgaben) − Σ export_t·Einspeisetarif
  - `min_co2`: min Σ import_t·co2_t
- **Variablen:** `modulating` → kontinuierlich pro Intervall (plus Binärvariablen nur bei `min_run_min`). `program` → eine Binärvariable pro möglichem Start.
- **Zerlegung (INV-5):** Es wird tageweise gelöst. Ein Block umfasst alle Aufgaben, deren Fenster an Tag d beginnt, mit Horizont bis zum spätesten Fensterende (≤ 48 h). Bereits eingeplante Lasten früherer Blöcke gehen als feste Last ein. Die Blöcke sind unabhängig genug für die Parallelisierung per `concurrent.futures` (optional). Erwartet werden ca. 365 kleine MILPs.
- **Garantie INV-3:** Der Runner bewertet Baseline und Ergebnis mit derselben Zielfunktion pro Block. Ist das Ergebnis schlechter oder scheitert der Solver (Timeout, infeasible), greift die Greedy-Heuristik und notfalls der Baseline-Fahrplan des Blocks. Das wird im Ergebnis protokolliert, nicht stillschweigend übergangen.
- **Greedy:** Aufgaben nach Flexibilität sortieren (kleines Fenster zuerst). Energie in die Intervalle mit dem besten Zielwert legen (größter verbleibender Überschuss, niedrigster Preis bzw. niedrigstes CO₂), unter Beachtung von Leistung und Fenster.

## 6. UI (Streamlit)

Seiten: **Konfiguration** (YAML laden/bearbeiten/speichern), **Ist-Analyse** (KPIs, Tages-/Monatsprofil, Heatmap), **Optimierung** (Ziel und Solver wählen, Start, Laufzeit), **Vergleich** (pro Haushalt und gesamt), **Export** (CSV). Hinweisbox auf jeder Ergebnisseite: „Obergrenze bei perfekter Voraussicht. Annahmen siehe …“. Für Playwright erhalten wichtige Elemente stabile Labels bzw. `key`s. Berechnungen werden per `st.cache_data` gecacht.

## 7. Tests

| Ebene | Inhalt | Läuft |
|---|---|---|
| `tests/unit` | Parser je Quelle gegen Fixtures, Balance, Economics, Config-Validierung | offline, immer |
| `tests/property` | hypothesis: INV-1, INV-2, INV-3, INV-6, INV-7 auf zufälligen kleinen Gemeinschaften | offline, immer |
| `tests/perf` | INV-5 (15 HH × 1 Jahr, synthetische Daten) | offline, Marker `slow` |
| `tests/e2e` | Playwright gegen `streamlit run` auf Port 8501 | Marker `e2e` |
| Marker `network` | echte API-Aufrufe | nur explizit |
