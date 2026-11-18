# Test-Fixtures – Herkunft und Lizenz

| Datei | Quelle | Lizenz |
|---|---|---|
| `pvgis_linz_10kwp_30deg_south_2023-06-20_3d.json` | PVGIS (EU JRC), echte API-Antwort, gekürzt auf 72 h | PVGIS-Nutzungsbedingungen (frei mit Quellenangabe) |
| `htw_1min_profiles_1-2_2010-01-01_2d.zip` | HTW Berlin, Tjaden et al.: „Repräsentative elektrische Lastprofile für Einfamilienhäuser in Deutschland auf 1-sekündiger Datenbasis“, DOI 10.13140/RG.2.1.5112.0080/1. Ausschnitt: Profile 1 und 2, 2010-01-01/02 (MEZ), 1-min-Variante, gleiche Dateistruktur wie der Download | CC BY-NC 4.0 |
| `awattar_at_2023-07-02_1d.json` | aWATTar API (`api.awattar.at/v1/marketdata`), echte Antwort für 2023-07-02 (Ortszeit), 24 h, enthält −500 €/MWh | aWATTar-Nutzungsbedingungen (öffentliche Marktdaten, EPEX Spot) |
| `awattar_at_2023-03-26_dst_1d.json` | aWATTar API, echte Antwort für den Umstellungstag 2023-03-26, 23 h | wie oben |
| `energy_charts_public_power_at_2023-07-02_1d.json` | Energy-Charts API (Fraunhofer ISE, `api.energy-charts.info/public_power?country=at`), echte Antwort für 2023-07-02 (Ortszeit), 96 Viertelstunden | CC BY 4.0 (Energy-Charts) |
| `energy_charts_public_power_at_2023-03-26_dst_1d.json` | Energy-Charts API, echte Antwort für den Umstellungstag 2023-03-26, 92 Viertelstunden | wie oben |

Das sind öffentliche Referenzdatensätze, keine eigenen Verbrauchsdaten. Eigene Daten gehören nur nach `data/`.
