# Sicherheitsmaßnahmen und Prüfstand

Stand: 31.10.2026. Dies ist eine Prüfung und Härtung des Quellcodes, kein unabhängiger Penetrationstest der laufenden LXC-/Proxy-Installation.

## Umgesetzter Schutz

- **Anmeldung:** Argon2id mit individuellen Salts, verdeckte und gehashte Sitzungstokens, HttpOnly-/SameSite-Cookies, Secure-Cookies unter HTTPS, widerrufbare Sitzungen. Fehlversuche sind zeitlich begrenzt; Passwortänderungen sperren bestehende Sitzungen.
- **Berechtigungen:** Nur Administratoren verwalten Verbindungen und Benutzer. Chats werden serverseitig dem jeweiligen Konto zugeordnet. Verbundene Quellen und Projektkontext werden zwischen allen freigeschalteten Konten geteilt. Der interne Bearer-API-Key ist weiterhin ein privilegierter technischer Zugang und gehört nicht in die Browser-Oberfläche.
- **Browser:** Eine Content Security Policy erlaubt Skripte ausschließlich zur eigenen App. Browser-API-Verbindungen sind auf die eigene App und den festen BigDataCloud-Endpunkt für ausdrücklich freigegebene Gerätestandorte begrenzt; keine Inline-Skripte und kein `eval`. Fremde Frames, Plugins, Formziele und Basis-URLs werden blockiert. Mikrofon- und Standortzugriff sind ausschließlich für die eigene App und mit Browserfreigabe möglich; Kamera, Zahlung und USB werden per Permissions Policy deaktiviert. Der Standort wird nicht dauerhaft beobachtet. Die freiwillige automatische Regionserkennung durch BigDataCloud ist ein gesonderter externer Datenfluss (Standort/IP-Abgleich); die manuelle Ortswahl vermeidet ihn. HTTPS-Antworten enthalten HSTS für den jeweiligen Host, ohne Subdomain-Zwang oder Preload.
- **CSRF und Host-Prüfung:** Schreibzugriffe über Sitzungscookies erfordern die eigene Origin. Anmeldung ist nur unter HTTPS oder auf echtem localhost möglich. Weitergeleitete Proxy-Header dürfen nur vom eingestellten Proxy angenommen werden.
- **Überlastung:** API-Schreibanfragen werden vor der JSON-Verarbeitung auf 1 MiB begrenzt, lokale Audioaufnahmen auf 4 MiB. Auch Bodies ohne oder mit falscher Content-Length werden geprüft. Das Empfangen eines Bodys hat ein Zeitlimit von 30 Sekunden. Höchstens zwei Chat-Recherchen und zwei Passwortoperationen laufen gleichzeitig pro Backend-Prozess. Das ersetzt keine Firewall oder ein vorgeschaltetes Verbindungs-/Rate-Limit gegen verteilte Angriffe.
- **Dateien:** Das lokale Datenverzeichnis hat Modus 0700, die SQLite-Datenbank 0600. Die LXC-Installation verwendet zusätzlich einen eigenen Dienstbenutzer, UMask 0077 und eingeschränkte Schreibpfade. API-Antworten und die App-Startseite sind nicht cachebar. Der Service Worker speichert nur öffentliche Offline-Dateien.
- **Verbindungen:** Paperless- und ntfy-Basisadressen dürfen keine eingebetteten Zugangsdaten, URL-Parameter, Fragmente oder Steuerzeichen enthalten. Zugangsdaten gehören in die separaten Felder. Bestehende Einstellungen werden durch das Update nicht umgeschrieben. Private Netzwerkziele bleiben ausdrücklich erlaubt, damit lokale Dienste erreichbar sind; Administratoren müssen diese vertrauenswürdig konfigurieren.
- **Daten und KI:** Kein Rendering von Mail-/KI-Inhalten als HTML, kein autonomer Mailversand und keine KI-Schreibzugriffe auf ioBroker. Externe KI-Aufrufe erfolgen über HTTPS mit Zertifikatsprüfung und den vorhandenen Freigaben. Ausgewählte Inhalte sind beim KI-Anbieter lesbar. Lokale Datenbank und Backups werden durch die App nicht verschlüsselt; Dateirechte ersetzen keine Datenträgerverschlüsselung.
- **Angriffsfläche:** Öffentliche Swagger-/ReDoc-/OpenAPI-Endpunkte sind deaktiviert. `/healthz` enthält nur einen einfachen Startstatus.

## Verifikation

Die automatisierten Tests prüfen unter anderem Kontoisolation, Rollen, Widerruf von Sitzungen, Origin-Prüfung, Geheimnis-Redaktion, Sicherheitsheader, private Dateirechte, Body-Grenzen einschließlich falscher Content-Length sowie das Freigeben belegter Chat-Slots.

`pip-audit` wurde nach Ergänzung der iCloud-Parser erneut ausgeführt und hat für die 56 tatsächlich in der lokalen Entwicklungsumgebung installierten Pakete keine bekannten Schwachstellen gemeldet. Das ist eine Momentaufnahme bekannter Meldungen, keine Sicherheitsgarantie. Der Paketstand im LXC kann abweichen und wurde nicht direkt geprüft. Eine separate, temporäre Tool-Umgebung wurde verwendet; private Konfiguration oder Dokumentinhalte wurden nicht an den Prüfdienst übertragen.

## Paketaktualisierung bei schnelleren Updates

Unveränderte Python-Abhängigkeiten werden bis zu sieben Tage in einer unveränderten Umgebung wiederverwendet. Geänderte Paketanforderungen oder Python-/Sprachkonfiguration erzeugen eine neue Umgebung; `aibutler update --refresh-deps` erzwingt dies sofort. Alte Umgebungen bleiben für Rollbacks erhalten. Die automatische Altersprüfung findet beim Update-Aufruf statt und ersetzt keine regelmäßigen Betriebssystem- und Anwendungsupdates. Passwörter sind auf Wunsch ab acht Zeichen zulässig; Argon2id, Fehlversuchsbegrenzung und Sitzungssperren bleiben aktiv.

## iCloud und dynamische Identitätsangaben

Der iCloud-Adapter verwendet app-spezifische Passwörter, feste erlaubte Apple-HTTPS-Hosts, geprüfte Weiterleitungen, sichere XML-Verarbeitung und begrenzte Antworten. Schreibende DAV-Methoden sind nicht implementiert. Credentials bleiben write-only in den lokalen Einstellungen; Kalender-/Kontaktinhalte gehen nur bei bestehender Chatfreigabe und passender Frage an die KI. Die eigene Kontaktkarten-Zuordnung ist serverseitig je Benutzerkonto getrennt. Firmenangaben werden nicht als feste Profilfakten gespeichert. Kalender-/Kontaktsnapshots werden bei Fehlern als veraltet gekennzeichnet und bei erfolgreichem Vollabruf ersetzt. Kontakt-Notizen werden nicht importiert. Quellen bleiben zwischen berechtigten Butler-Konten gemeinsam nutzbar.

Die neu hinzugekommenen Parser werden über `requirements.txt` installiert; dadurch wird beim ersten Update eine neue Runtime aufgebaut. Details zur Einrichtung und zu Daten-/Zeitgrenzen stehen in der README.

## Betrieb und verbleibende Grenzen

1. Updates installieren, bevor die Anwendung über den Proxy verwendet wird: `aibutler update`.
2. Nur HTTPS über den vorgesehenen Reverse-Proxy freigeben. Den Backend-Port per Firewall auf den Proxy begrenzen; eine konfigurierte Proxy-IP allein ist keine Netzwerk-Firewall.
3. Der HTTP-Upstream zwischen Proxy und LXC sowie lokale Dienste mit HTTP sind unverschlüsselte Netzwerkabschnitte. Bei Bedarf auch dort TLS oder einen verschlüsselten Tunnel einrichten.
4. Starke individuelle Passwörter verwenden, Konten nur für vertrauenswürdige Personen anlegen und Backups geschützt außerhalb des LXC speichern. Eine zweite Anmeldestufe (MFA) ist noch nicht implementiert.
5. Bei Verlust eines Geräts das Passwort ändern, um bestehende Sitzungen zu widerrufen. Bei offengelegten Dienstschlüsseln auch die jeweiligen Schlüssel beim Anbieter austauschen.
6. Schutz vor großen Mengen kleiner Anfragen, Angriffe auf DNS/Router/Proxy sowie die Verschlüsselung des Hosts müssen zusätzlich auf Infrastruktur-Ebene erfolgen.

## Referenzen

- [OWASP: Content Security Policy](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
- [OWASP: HTTP Security Headers](https://cheatsheetseries.owasp.org/cheatsheets/HTTP_Headers_Cheat_Sheet.html)
- [Starlette: Middleware und TrustedHost](https://www.starlette.io/middleware/)

Lokale semantische Suche: FastEmbed berechnet Embeddings mit lokal vorhandenen ONNX-Gewichten; Qdrant läuft eingebettet ohne eigenen Netzwerkdienst. Dokumenttexte werden zur Vektorberechnung nicht hochgeladen. Der Installer lädt ausschließlich öffentliche Modellgewichte; Inferenz verwendet `local_files_only`. Hugging-Face- und ONNX-Telemetrie sind deaktiviert. Vektoren bleiben sensible abgeleitete Daten und erfordern dieselben Zugriffsrechte wie die Quelldaten. Rekonstruierbare Vektordateien werden aus verwalteten Backups ausgeschlossen.

Langzeitgedächtnis: Alle Lese- und Schreibzugriffe auf Erinnerungen sind an die authentifizierte Konto-ID gebunden; ein technischer Bearer-Key ohne Benutzerkonto kann keine persönlichen Erinnerungen verwalten. Offene Vorschläge sind kein Langzeitkontext und werden nicht im geteilten Quellenindex gespeichert. Bestätigen, Bearbeiten und Löschen prüfen die Eigentümerschaft serverseitig. Änderungsrevisionen schützen vor überschriebenen parallelen Änderungen. Erinnerungen sind unverschlüsselte private SQLite-Daten und in lokalen Backups enthalten. Das Entfernen einer Erinnerung löscht weder die Quelle im Chat noch ältere Backups. Erinnerungsinhalte werden für die KI als Daten behandelt und ersetzen keine Systemregeln.

Neural speech output uses local Piper inference with a fixed German voice installed in advance. The authenticated `/api/speech/synthesize` endpoint accepts at most 2,000 characters and returns uncached WAV bytes. Runtime inference does not download voice models or call external synthesis services. Generated audio is kept in memory only; stopping playback aborts the browser request, while an already executing local synthesis thread may finish under its exclusive lock. Public voice weights are fetched only by the installation script. No secrets or private answer texts are used by that downloader.

Selected Paperless originals may be read for bounded local OCR. Download targets are restricted to the configured Paperless instance and numeric document IDs; credentials are not followed across redirects. Limits apply to download size, page count, rendered pixels and OCR execution time. Temporary raster files are removed. The local SQLite OCR cache contains private derived text and therefore belongs in the same protected backup scope as other local content. For licence-date questions only matching class/date rows are passed onward as chat evidence; original images, licence numbers and unrelated OCR lines are not added to the external model request by this path.

Chat SSE streams have the same authentication/ownership checks as ordinary chat requests. Provider text deltas are provisional, held in server memory and never persisted as a completed response before successful provider completion. Failed/truncated streams are not accepted as finished model answers. Browsers render provisional text with textContent; final Markdown remains restricted to the existing inert DOM renderer. API responses and SSE are not service-worker cached. The provider request preserves OpenAI store:false and existing Gemini consent checks.

The optional admin-only benchmark sends three fixed synthetic tasks per selected model, not source documents, memories or chat history. It uses only fixed provider HTTPS endpoints; arbitrary model IDs cannot set a URL origin. Keys are never included in benchmark results, persisted benchmark state or logs. Results are owner-scoped and changes to the configured model require an explicit action. Comparison requests are bounded and cancellable; closing the dialog alone does not cancel an already authorized comparison.

## Bestätigte ioBroker-Gerätesteuerung

Die neue optionale Steuerung bleibt standardmäßig aus, ohne Geräteliste. Nur Administratoren können Geräte und deren genaue IDs/Rohwertzuordnung konfigurieren. Berechtigte angemeldete Konten können anschließend für diese freigegebenen Geräte Einzelaufträge vorschlagen und explizit bestätigen. Sprach-/KI-Ausgaben selbst führen keine Writes aus. Vor Ausführung werden Rolle, Typ, Schreibbarkeit und Wertebereich vom Adapter gelesen. Skript-/Systempunkte, unsichere ID-Zeichen und unpassende Aktorrollen werden zurückgewiesen; REST-Schreibzugriffe sind nicht implementiert. Die Freigabeliste ist für ungefährliche Lampen, Steckdosen und Rollos gedacht; eine Rolle allein kann nicht garantieren, welches physische Gerät hinter einer Steckdose steckt.

Ausführungsendpunkte benötigen bestehende Authentifizierung und dieselbe Origin-Prüfung wie andere Schreibanfragen. Zufällige Bestätigungstokens sind kontogebunden, fünf Minuten gültig, auf die genaue Verbindungskonfiguration gebunden und vor Netzwerkzugriff einmalig verbraucht. Widerruf/Neukonfiguration und Dienstneustart verwerfen offene Freigaben. Vor dem Write wird ein lokaler Auftragseintrag gespeichert. Keine automatischen Retries, keine Sammelschaltungen und keine vom LLM gewählten IDs. Unklare Schreibresultate werden als möglicherweise ausgeführt gemeldet. Ein quittierter Adapterzustand wird nicht als unabhängige physische Prüfung ausgegeben. Die End-to-End-Tests nutzen simulierte Adapter; reale Rollos wurden nicht bewegt.
