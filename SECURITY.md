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

`pip-audit` hat am 31.10.2026 für die 47 tatsächlich in der lokalen Entwicklungsumgebung installierten Pakete keine bekannten Schwachstellen gemeldet. Das ist eine Momentaufnahme bekannter Meldungen, keine Sicherheitsgarantie. Der Paketstand im LXC kann abweichen und wurde nicht direkt geprüft. Eine separate, temporäre Tool-Umgebung wurde verwendet; private Konfiguration oder Dokumentinhalte wurden nicht an den Prüfdienst übertragen.

## Paketaktualisierung bei schnelleren Updates

Unveränderte Python-Abhängigkeiten werden bis zu sieben Tage in einer unveränderten Umgebung wiederverwendet. Geänderte Paketanforderungen oder Python-/Sprachkonfiguration erzeugen eine neue Umgebung; `aibutler update --refresh-deps` erzwingt dies sofort. Alte Umgebungen bleiben für Rollbacks erhalten. Die automatische Altersprüfung findet beim Update-Aufruf statt und ersetzt keine regelmäßigen Betriebssystem- und Anwendungsupdates. Passwörter sind auf Wunsch ab acht Zeichen zulässig; Argon2id, Fehlversuchsbegrenzung und Sitzungssperren bleiben aktiv.

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
