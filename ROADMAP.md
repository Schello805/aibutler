# Vorgemerkte Erweiterungen

## Für später: Echo als Sprachgerät

- Einen vorhandenen Amazon Echo als Mikrofon-/Lautsprechergerät für den Butler prüfen.
- Der Butler bleibt auf dem LXC; ein ESP32-S3 könnte Aktivierungswort und Audioübertragung übernehmen.
- Echo-Modell und Generation vor der Teileauswahl klären. Übernahme der originalen Mikrofonplatine ist modellabhängig.
- Nötige Software: gerätegebundener, eingeschränkter Zugang, Audioanbindung und lokale serverseitige Sprachausgabe.
- Noch nicht implementiert und keine Hardware ausgewählt.

## Kandidaten für öffentliche API-Erweiterungen

Ausgangspunkt: die vom Nutzer bestätigte Sammlung [public-apis/public-apis](https://github.com/public-apis/public-apis). Verfügbarkeit, Datenlizenz, Rate-Limits und Wartungsstand müssen je Anbieter separat geprüft werden; die Aufnahme in die Liste garantiert keine dauerhaft kostenlose oder offene Nutzung.

| Priorität | Kandidat | Möglicher Nutzen im Butler | Datensparsame Anbindung |
| --- | --- | --- | --- |
| 1 | [Open-Meteo](https://open-meteo.com/en/docs) | Wetter zu geplanten Aktivitäten; Regen/Frost zusammen mit passenden ioBroker-Zuständen einordnen | Konfigurierter Ort bzw. gerundete Koordinaten; keine Mails, Dokumente oder Gesprächsverläufe zum Wetterdienst senden. Nichtkommerzielle Gratis-API und erforderliche Quellenangabe beachten. |
| 2 | [Nager.Date](https://github.com/nager/Nager.Date) | Feiertage bei Terminen und Planungen berücksichtigen | Jahresdaten für Land/Region lokal zwischenspeichern; regionale bzw. kommunale Sonderfälle gesondert prüfen. Private Termine bleiben lokal. |
| 3 | [Frankfurter](https://frankfurter.dev/) | Fremdwährungsbeträge aus Rechnungen mit einem datierten Referenzkurs einordnen | Öffentliche Kurse abrufen und lokal rechnen; keine Rechnung, Händlerdaten oder Beträge hochladen. Referenzkurs von tatsächlicher Kartenabrechnung trennen. |

Wetter und Feiertage sind jetzt implementiert und jeweils in den Einstellungen aktivierbar. Die Ortswahl erfolgt pro Gerät über freiwillige Standortfreigabe oder manuelle Suche. Die automatische Regionserkennung nutzt zusätzlich BigDataCloud; die Oberfläche erläutert dessen Standort/IP-Abgleich. Frankfurter bleibt ein Kandidat und ist nicht integriert. Verkehr, Warnmeldungen und Energiepreise sind weitere Kandidaten, wenn ein konkreter persönlicher Anwendungsfall feststeht.

### Vorgesehene Umsetzung

- Jede Verbindung einzeln aktivierbar und testbar; keine automatischen externen Abfragen ohne eingerichtete Verbindung.
- Adapter im Backend mit festen erlaubten HTTPS-Zielen, begrenzten Antworten, Timeouts, Rate-Limits und lokalem Cache.
- Öffentliche Live-Daten getrennt vom privaten Dokumentindex führen; Abrufzeit, Gültigkeit und Anbieter als Belege anzeigen.
- Ausgefallene oder veraltete Daten ausdrücklich kennzeichnen.
- Proaktive Meldungen nur für bewusst eingerichtete Regeln und relevante Änderungen; keine Push-Meldung zu jeder Frage.
- Analyse privater Unterlagen und Verknüpfung mit öffentlichen API-Daten erfolgen im Butler. Die bestehende Freigabe für externe Chat-KI bleibt davon getrennt.
