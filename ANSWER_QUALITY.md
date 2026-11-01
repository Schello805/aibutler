# Antwortqualität: geprüfte Recherchefälle

Die Regressionstests verwenden synthetische E-Mails und Dokumente. Sie prüfen die Quellenauswahl und lokale Antworten; bei Tests der KI-Schnittstelle ist der Anbieter simuliert. Das ist keine Messung der Antwortgenauigkeit eines echten Sprachmodells im Haushalt und kein Test der laufenden LXC-Verbindungen.

| Frage / Situation | Geprüftes Verhalten |
| --- | --- |
| Wann war die letzte Heizungswartung? | Eine neuere Wartungsrechnung stützt einen als wahrscheinlich bezeichneten Zeitraum; ältere genaue Daten verdrängen diese Fundstelle nicht. Werbung und Stellenangebote werden herausgefiltert. |
| Leistungsdatum auf einer späteren Dokumentseite | Der relevante Auszug bleibt auch hinter den ersten 10.000 Zeichen für Antwort und Belege verfügbar. |
| Mehr als 50 ältere Treffer | Für Fragen nach dem neuesten Vorgang wird bereits vor der Begrenzung der Kandidaten zeitlich sortiert. |
| Wartung storniert / nicht durchgeführt | Wird nicht als bestätigte Durchführung mit genauem Datum ausgegeben. |
| Ist Paket XYZ schon da? | E-Mail und ntfy werden gemeinsam recherchiert, auch wenn die KI-Suchplanung ausfällt oder eine Quelle auslässt. Eine Klingelmeldung allein ist kein Zustellbeweis. |
| Was habe ich zuletzt gekauft? | Bestätigte Bestellungen und Kaufbelege werden gegenüber Versandmeldungen und Werbung berücksichtigt. Deutsche Tausender-/Dezimaltrennzeichen bleiben lesbar. |
| Was wollte Tanja zuletzt? | Tatsächliche Absendernachrichten werden von bloßen Namensnennungen in anderen E-Mails getrennt. HTML und ältere zitierte Korrespondenz werden bei der lokalen Kurzantwort berücksichtigt. |
| KI-Suchplanung nicht verfügbar | Ein lokaler Rechercheplan hält typische Dienstabfragen verfügbar. Eine anschließend funktionierende Antwort-KI kann deren Belege verwenden; eine Freigabe für externe KI wird dadurch nicht automatisch aktiviert. |

Die Antwortanweisung verlangt kurze, belegte Antworten und trennt Vermutungen von bestätigten Tatsachen. Ohne passende persönliche Belege soll die KI ihr Nichtwissen ausdrücken. Belege und technische Suchgrenzen bleiben im separaten Dialog.

## Grenzen

- Fehlende oder noch nicht indexierte Daten, begrenzte Postfachsuchen und abgelaufene ntfy-Meldungen können weiterhin zu unvollständigen Antworten führen.
- Eine Rechnung ist ein Hinweis auf eine Wartung, aber keine Garantie für deren Durchführung. Ohne Leistungsdatum wird kein genauer Tag daraus erfunden.
- Bei sehr langen Dokumenten wird weiterhin ein begrenzter Auszug verwendet, nicht das gesamte Dokument an den KI-Anbieter übertragen.
- Die automatische Ausweichplanung deckt typische Fragen ab; freie mehrdeutige Fragen und Gesprächsrückbezüge profitieren weiterhin von der semantischen KI-Planung.
- Ein Test des echten Modells mit repräsentativen Fragen und anschließendem Abgleich gegen die Originalbelege im LXC steht noch aus.

Tests ausführen:

```bash
python -m pytest -q tests/test_answer_quality.py
```
