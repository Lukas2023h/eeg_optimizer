# EEG-Optimizer – Spezifikation

## 1. Ziel

Ein lokales Werkzeug, das für eine kleine Energiegemeinschaft (EEG) mit gemeinsamer PV-Anlage zeigt,

1. **Ist-Zustand:** wie viel PV-Strom die Mitglieder gleichzeitig selbst nutzen und wie viel ins Netz geht,
2. **Potenzial:** wie viel mehr in der Gemeinschaft bliebe, wenn verschiebbare Verbraucher optimal eingeplant wären,

ausgedrückt in **kWh, kg CO₂ und €**, pro Haushalt und gesamt. Es ist ein Lern- und Wirkungsprojekt, kein Produkt.

## 2. Nutzer

| Nutzer | Bedürfnis |
|---|---|
| Entwickler (Hauptnutzer) | Szenarien durchrechnen, Annahmen variieren, Ergebnisse nachvollziehen |
| Mitglieder einer EEG (3–15 Haushalte, technische Laien) | Ergebnisse verstehen: „Was bringt es, wenn ich mittags wasche?“ |

Läuft lokal (`streamlit run`, Port 8501), ein Nutzer, kein Login. UI und Doku auf Deutsch.

## 3. Features (Was & Warum)

Nach Priorität. Die feingranulare, testbare Zerlegung steht in `feature_list.json`.

| # | Feature | Warum |
|---|---|---|
| F1 | **Datenimport und Normalisierung:** PV (PVGIS), Haushaltslast (HTW Berlin, ergänzend OPSD), Spotpreis (aWATTar), CO₂-Intensität (Energy-Charts) laden, lokal cachen und ins interne Format bringen | Ohne saubere, einheitliche Daten ist jede Kennzahl wertlos. Ein internes Format macht Beispiel- und Echtdaten austauschbar. |
| F2 | **Virtuelle Gemeinschaft konfigurieren:** Haushalte, Zuordnung der Lastprofile, PV-Größe/-Neigung/-Ausrichtung, Zeitraum, Tarife, EEG-Typ, per YAML und UI | Szenarien sollen ohne Code reproduzierbar und vergleichbar sein. |
| F3 | **Ist-Analyse (Baseline):** Eigenverbrauchsquote, Autarkiegrad, Einspeisung, Netzbezug; Tages- und Monatsprofile; Überschuss-Heatmap (Stunde × Tag) | Zeigt, wo und wann Überschuss ungenutzt bleibt. |
| F4 | **Flexible Lasten modellieren:** pro Haushalt Geräte mit Leistung, Tagesenergie, Zeitfenster, ggf. Mindestlaufzeit, Baseline-Startzeit | Die Baseline-Startzeit beschreibt, wie das Gerät heute läuft. Das Fenster beschreibt den Spielraum. |
| F5 | **Optimierung:** flexible Lasten so einplanen, dass das gewählte Ziel bestmöglich erreicht wird: (a) gemeinsam genutzte Energie maximieren (Standard), (b) Kosten minimieren (Spotpreis), (c) CO₂ minimieren. Exaktes Verfahren (LP/MILP) und Greedy-Heuristik als Vergleich und Fallback | Kernaussage des Tools: das Potenzial. |
| F6 | **Vergleich Baseline vs. optimiert:** zusätzlich gemeinsam genutzte kWh, gesparte €, vermiedene kg CO₂, pro Haushalt und gesamt | Macht die Wirkung greifbar. |
| F7 | **Dashboard:** Konfiguration, Diagramme, Kennzahlen, CSV-Export | Zugang für Laien. Export für eigene Auswertungen. |
| F8 | **Import echter Smart-Meter-CSV** (15-min-Export Netzbetreiber, z. B. Netz OÖ) | Später echte Daten statt Beispieldaten. Niedrigste Priorität. |

## 4. Nicht-Ziele

- Keine Gerätesteuerung und keine Hardware (ESP32, SG-Ready).
- Keine rechtlich korrekte EEG-Abrechnung und keine Energiezuteilung nach EDA-Regeln. Der Verteilschlüssel dient nur der Veranschaulichung.
- Kein Login, kein Multi-User, kein Hosting oder Deployment.
- Keine Last- oder Wetterprognose. Optimiert wird mit **perfekter Voraussicht** auf historischen Daten. Das Ergebnis ist eine **Obergrenze** des Potenzials und muss in der UI so gekennzeichnet sein.
- Keine Mobile-Optimierung.
- **Keine Batteriespeicher** (weder Heim- noch Gemeinschaftsspeicher). *Möglicher Ausbau:* Speicher als weiteres flexibles Element im Optimierungsmodell.
- Keine PV pro Haushalt im MVP, nur eine gemeinsame Anlage. Das Datenmodell bleibt so offen, dass sich das ergänzen lässt.
- Keine thermische Gebäudesimulation für die Wärmepumpe. Sie wird als verschiebbarer Energiebedarf modelliert.

## 5. Fachliche Invarianten (müssen als Tests abgesichert sein)

| ID | Invariante |
|---|---|
| INV-1 | Energiebilanz pro Intervall (Toleranz 1e-6 kWh): `pv = shared + export` und `load = shared + grid_import` |
| INV-2 | Die Optimierung ändert den Tages-Energiebedarf einer flexiblen Last nie, verletzt nie ihr Zeitfenster, überschreitet nie ihre Leistung und hält die Mindestlaufzeit ein. |
| INV-3 | Das optimierte Ergebnis ist bezogen auf das gewählte Ziel nie schlechter als die Baseline. |
| INV-4 | Leistung (kW) und Energie (kWh) werden nie vermischt. Erzwungen über Namenskonvention und Typen (siehe `architecture.md`). |
| INV-5 | Performance: 15 Haushalte × 1 Jahr × 15 min werden auf einem Laptop in < 60 s optimiert. |
| INV-6 | Zeitreihen im internen Format haben ein lückenloses 15-min-Raster in Europe/Vienna. Tage mit Zeitumstellung haben 92 bzw. 100 Intervalle. Bei Resampling bleibt die Energiesumme erhalten. |
| INV-7 | Die Aufteilung auf Haushalte ist vollständig: Die Summe der Haushaltsanteile ergibt pro Intervall die Gemeinschaftswerte (shared, grid_import). |

## 6. Akzeptanzkriterien (Gesamtprodukt)

1. `./init.sh` läuft auf einem frischen Klon ohne manuelle Schritte durch, abgesehen von den einmaligen Systemabhängigkeiten für Playwright.
2. Mit der mitgelieferten Beispielkonfiguration (`config/example_community.yaml`, 5 Haushalte, 2023) zeigt die App nach dem Start alle Baseline-Kennzahlen und Diagramme an.
3. „Optimieren“ liefert für alle drei Ziele ein Ergebnis. Der Vergleich zeigt kWh, € und kg CO₂ pro Haushalt und gesamt.
4. Die UI weist sichtbar auf die Obergrenze durch perfekte Voraussicht und auf die Annahmen in Abschnitt 7 hin.
5. CSV-Export der Zeitreihen und Kennzahlen ist möglich und lässt sich mit pandas wieder einlesen.
6. Alle Invarianten aus Abschnitt 5 sind durch Tests abgedeckt und grün. Unit-Tests laufen offline.
7. Ein Smart-Meter-CSV im Netz-OÖ-Format lässt sich statt eines Beispiel-Lastprofils einem Haushalt zuordnen.

## 7. Annahmen und Defaults

Alle Werte sind als YAML-Parameter überschreibbar (`config/defaults.yaml`). Die UI verlinkt diesen Abschnitt.

### 7.1 Tarife (ct/kWh, brutto inkl. 20 % USt)

| Parameter | Default | Bemerkung |
|---|---|---|
| `energy_price` | 13.0 | Energiepreis des Lieferanten |
| `grid_fee_energy` | 6.0 | Netznutzungsentgelt, Arbeitspreis (inkl. Netzverlustentgelt) |
| `levies_taxes` | 6.0 | Abgaben, Steuern, Förderbeiträge |
| *Summe Netzbezug* | *25.0* | |
| `feed_in_tariff` | 7.0 | Vergütung für Einspeisung ins öffentliche Netz |
| `eeg_internal_price` | 12.0 | Preis für gemeinschaftsintern bezogene kWh (ersetzt `energy_price`) |
| `eeg_type` | `local` | `local` (Netzebene 6/7) oder `regional` |
| `grid_fee_reduction` | local: 0.57, regional: 0.28 | Ermäßigung, **nur** auf `grid_fee_energy` für gemeinschaftsintern bezogene kWh |
| `spot_markup` | 1.5 | Aufschlag auf den Spotpreis beim Ziel „Kosten minimieren“ |

- Preis einer gemeinschaftsintern bezogenen kWh für Mitglieder: `eeg_internal_price + grid_fee_energy × (1 − grid_fee_reduction) + levies_taxes`.
- Weitere mögliche Befreiungen (z. B. Erneuerbaren-Förderbeitrag, Elektrizitätsabgabe) werden **nicht** modelliert.
- **Gemeinschaftssicht:** Die gemeinsame PV-Anlage gehört der Gemeinschaft, interne Zahlungen heben sich auf. Kosten = Netzbezug × voller Preis + shared × (ermäßigtes Netzentgelt + Abgaben) − Einspeisung × Einspeisevergütung.
- **Wert einer gemeinsam genutzten kWh (Gemeinschaftssicht)**, eigener Unit-Test:
  `value_shared = energy_price + grid_fee_energy × grid_fee_reduction − feed_in_tariff`.
  Default lokal: 13 + 6 × 0.57 − 7 = **9.42 ct/kWh** (regional: 13 + 6 × 0.28 − 7 = 7.68 ct/kWh). Ermäßigtes Netzentgelt und Abgaben fallen weiterhin an. Die kWh ist also *nicht* 25 ct wert.
  Gesparte € (Gemeinschaft, fixer Tarif) = Δ shared_kWh × value_shared. Das folgt direkt aus der Kostenformel oben, weil jede zusätzlich geteilte kWh eine kWh Netzbezug und eine kWh Einspeisung ersetzt.
- **Haushaltssicht:** Kosten des Haushalts als Verbraucher (Netzbezug × voller Preis + shared-Anteil × interner Preis inkl. Netzentgelt und Abgaben).
- **Ziel „Kosten minimieren“:** Der Energiepreis des Netzbezugs ist stündlich `spot + spot_markup` (dynamischer Tarif). Die €-Kennzahlen dieses Laufs verwenden dasselbe Preismodell.

### 7.2 Daten und Modell

- **Referenzjahr 2023.** Alle Zeitreihen werden auf 2023 abgebildet.
- **PVGIS** liefert Stundenwerte. Die Energie wird gleichmäßig auf die vier 15-min-Intervalle verteilt, die Summe bleibt erhalten. Standard ist Linz (48.31 N, 14.29 E).
- **Lastprofile (HTW Berlin)** stammen aus **Deutschland** und einem anderen Jahr. Sie werden wochentagsgetreu auf 2023 abgebildet und von 1 s auf 15 min aggregiert.
- **CO₂:** stündliche Durchschnittsintensität der **österreichischen Erzeugung ohne Importe** (Energy-Charts). PV zählt mit 0 g/kWh. Vermiedene Emissionen = vermiedener Netzbezug × Intensität im jeweiligen Intervall. Diese Vereinfachung wird in der UI vermerkt.
  - *Methode:* Energy-Charts hat keinen CO₂-Endpunkt für AT. Die Intensität wird pro 15-min-Intervall aus dem Erzeugungsmix berechnet (`/public_power?country=at`, Netto-Erzeugung in MW, für AT 2023 in 15-min-Auflösung): `Σ P_i · f_i / Σ P_i`. Das ist feiner als stündlich. Nicht gezählt werden Import/Export (`Cross border electricity trading`) und Pumpstrom (`Hydro pumped storage consumption`). Pumpspeicher-Erzeugung zählt mit dem Wasserkraftfaktor, obwohl ein Teil davon mit Importstrom gepumpt wurde.
  - *Emissionsfaktoren* (g CO₂-Äq./kWh, Lebenszyklus-Mediane aus IPCC AR5 WG III, 2014, Annex III, Tab. A.III.2): Wasserkraft 24, Wind an Land 11, Wind auf See 12, PV 41 (Aufdach), Biomasse 230 (dediziert), Geothermie 38, Kernkraft 12, Erdgas 490 (GuD), Stein- und Braunkohle 820.
  - *Eigene Annahmen* (keine IPCC-Kategorie): Öl 650, Kuppelgas aus Kohle 820, Müll 700, Sonstige 700. Müll und Sonstige machen in AT 2023 ca. 1,8 % der Erzeugung aus.
  - Ergebnis für 2023: Mittel 90,7 g/kWh, Bereich 31–290 g/kWh. Unbekannte Erzeugungsarten, Lücken oder negative Werte führen zu einem Fehler, nichts wird aufgefüllt.
- **Verteilschlüssel** (gemeinsam genutzte Energie → Haushalte): dynamisch, anteilig zum Verbrauch im Intervall. Er ist austauschbar, statisch kommt eventuell später.
- **Flexible Lasten** kommen **zusätzlich** zum Lastprofil dazu. Ihr Baseline-Fahrplan ergibt sich aus einer pro Gerät konfigurierbaren Startzeit, z. B. E-Auto 18:00, Boiler 22:00, Waschmaschine 19:00.
- **Perfekte Voraussicht:** Die Optimierung kennt PV, Last, Preise und CO₂ im Voraus.
- **Wärmepumpe** wird als `modulating` mit festem Tagesbedarf modelliert. Das ist eine Vereinfachung: keine Gebäudeträgheit, keine Komfortgrenzen. Das Default-Zeitfenster ist deshalb eng, 10:00–16:00.
- **Mindestleistung im Betrieb** (z. B. Wallbox ca. 1,4 kW einphasig, ca. 4,1 kW dreiphasig) ist optional und standardmäßig aus.
- **Tageszerlegung:** Optimiert wird blockweise, pro Tag mit Horizont ≤ 48 h. Das Ergebnis ist pro Block optimal, nicht global über das Jahr. Die UI weist neben dem Ergebnis darauf hin.
- **Rückfall:** Liefert der Solver für einen Block kein gültiges oder ein schlechteres Ergebnis, kommt Greedy bzw. die Baseline zum Einsatz. Die Anzahl der Blöcke mit Rückfall wird im Ergebnis und in der UI ausgewiesen. Im Standardszenario (Beispielgemeinschaft, 2023) **muss die Rückfallquote 0 sein**. Das ist durch einen Test abgesichert, und der Evaluator wertet einen Verstoß als FAIL.
