# AlarmCam Analyse und Mail-Relay

Zentrale Verwaltung der Mailweiterleitung von Reolink- und anderen Kameras, die bei
Alarm eine E-Mail senden. Die Fotos werden analysiert und selektiv weitergeleitet.
Die Grundfunktion von RevierSpion ist nachempfunden, mit deutlich besserer Bildanalyse.

Das Add-on nimmt Mails der Kameras mit Login an, **speichert sie sofort** und bestätigt
der Kamera mit „OK“. Danach stellt es die Mail an einen Ziel-SMTP-Server zu
(voreingestellt: RevierSpion) und wiederholt es bei Fehlern.
Die Kamera sendet nur und bekommt nie einen Fehler des Zielservers zu sehen.

**Das Foto steht im Mittelpunkt, nicht die Mail:** Gespeichert wird je Alarm das erste
Foto samt Status (weitergeleitet, gedrosselt, ohne Erkennung, Weiterleitung aus, gescheitert), Kamera,
Zeit, Bilderkennung und Empfängeradresse. Die Mail selbst wird nur bis zur Zustellung
gebraucht. Jedes Foto lässt sich jederzeit neu senden, das Add-on baut die Mail dafür
aus dem Foto und der Empfängeradresse neu zusammen (RevierSpion ordnet nur nach der
Empfängeradresse zu, Betreff und Absender werden dort nicht ausgewertet).
Weitere Fotos einer Mail (z. B. das Zoom-Bild einer PTZ-Kamera) werden verworfen.

Das Relay bringt die Stabilität: Fällt das Ziel kurz aus, wird später erneut
zugestellt. Kommen zu viele Mails einer Kamera, werden sie gedrosselt.

## Ablauf einer Mail

1. Die Kamera sendet an das Relay (Login, Empfänger-Domain und Größe werden geprüft).
2. Die Mail wird gespeichert und mit „OK“ bestätigt. Jede Mail bekommt eine Nummer (`#17`).
3. **Drosselung:** Pro Empfängeradresse (= Kamera) geht in einem Zeitfenster
   (Standard 1 Minute) nur die erste Mail durch. Alle weiteren im Fenster werden
   gespeichert und als „gedrosselt“ markiert.
4. **Zustellung:** Die Mails gehen nacheinander zum Ziel. Zwischen zwei Mails wartet
   das Relay die eingestellte Wartezeit (Standard 3 Sekunden).
5. **Wiederholung:** Klappt es nicht, versucht das Relay es nach 30 Sek, 1, 2, 5 und
   10 Min und danach alle 10 Min, insgesamt bis zu 30 Minuten. Dann gilt die Mail als
   „gescheitert“. Ist das Ziel nicht erreichbar, pausiert die gesamte Zustellung bis
   zum nächsten Versuch.
6. Nach der Zustellung (oder wenn die Mail gedrosselt, gefiltert oder gescheitert ist)
   wird die Mail-Datei gelöscht. Das Foto bleibt mit seinem Status erhalten
   (Standard 180 Tage) und lässt sich im Board jederzeit neu senden. Mit
   „Fotos auf dem Share ablegen“ = aus bleiben nur die nicht weitergeleiteten Fotos erhalten.

Endgültige Ablehnungen des Ziels (zum Beispiel „Empfänger unbekannt“) werden nicht
wiederholt, die Mail gilt sofort als gescheitert.

## Board in der Seitenleiste

Auf der Info-Seite des Add-ons gibt es den Schalter **„In der Seitenleiste anzeigen“**.
Das Board „AlarmCam“ ist nur nach dem Login in Home Assistant erreichbar und zeigt:

- Zustand des Ziels, wartende, gedrosselte und gescheiterte Mails
- ein Warnbanner, wenn Mails länger als 15 Minuten auf die Zustellung warten
- in der Listenansicht des Reiters „Bilder“ standardmäßig nur, was **nicht weitergeleitet** wurde (gescheitert, gedrosselt, ohne Erkennung, Weiterleitung aus), mit Kamera, Status, Fehlergrund und Vorschaubild; „Alle“ zeigt auch zugestellte Fotos
- **Neu senden** (einzeln, auch bei zugestellten Fotos, oder alle gescheiterten), Download des Fotos als Datei, Löschen
- einen Hinweis, **sobald der Speicher voll ist** (dann werden die ältesten Fotos automatisch gelöscht)

Hinweis: RevierSpion verwendet als Zeit den Empfang der Mail. Eine nachgesendete Mail
erscheint dort mit der Uhrzeit des Nachsendens.

## Backup-SMTP-Server (optional)

Unter „Optional: Backup-SMTP: …“ kannst du einen zweiten Zielserver eintragen (Server, Port, Benutzer, Passwort, Absender), z. B. Brevo als Ersatz für GMX. Wirkung:

- Schlägt der Versuch beim Hauptserver fehl, geht der **nächste Versuch sofort** über den Backup-Server (nach ca. 2 Sekunden, nicht erst nach der Wartezeit). Danach wechseln die Versuche sich ab, solange die Mail nicht zugestellt ist.
- War der Hauptserver gar nicht erreichbar (Verbindung, Anmeldung, Zeitüberschreitung), gehen **neue** Mails die nächsten 10 Minuten direkt über den Backup-Server. Das verhindert, dass jede Mail erst auf die Zeitüberschreitung des Hauptservers wartet. Danach wird der Hauptserver wieder versucht, und bei Erfolg läuft alles wieder darüber.
- Die Verschlüsselung wählt das Add-on selbst: Port 465 = SSL/TLS, sonst STARTTLS, sobald der Server es anbietet (Port 587).
- „Optional: Backup-SMTP: Absender“ legt fest, mit welcher Adresse der Backup-Server sendet; die Adresse der Kamera wird dann nie als Absender verwendet. **Leer** = es gilt „Ziel: Absender“. Ist beides leer, warnt das Add-on beim Start. Brevo akzeptiert nur Absenderadressen oder Domains, die dort bestätigt sind.
- Alle Backup-Felder sind optional und stehen in der Konfiguration unter „Nicht verwendete optionale Konfigurationsoptionen anzeigen“, bis sie ausgefüllt sind.
- Im Board erscheint eine Karte „Backup“, zugestellte Mails tragen den Vermerk „über den Backup-Server“, und bei einem Ausfall zeigt das Board einen Hinweis. Das Protokoll nennt bei jedem Versuch den verwendeten Server.
- Beim Start wird auch der Backup-Server getestet („Backup-Zieltest OK“).
- Ist der Backup-Server leer, ändert sich nichts gegenüber früher.

## Reiter im Board

**Bilder | Kameras | Reviere | Verbindungen | Modelle**

- **Bilder** (Startseite): alle Fotos als Galerie, oder per Umschalter **Liste** mit Status, Fehlergrund, Mehrfachauswahl, Löschen und „Neu senden“.
- **Kameras:** Klarname und Revier je Empfangsadresse.
- **Reviere:** Pflegeliste der Reviere (anlegen, umbenennen, löschen).
- **Verbindungen:** jede Verbindung einer Kamera zum Relay (bisher „Auswertung“).
- **Modelle:** Modell der Bilderkennung.

## Bilderseite (erster Reiter)

**Zeitraum:** Oben auf der Seite sitzt eine Datumsauswahl wie im Energie-Dashboard von Home Assistant. Beim Öffnen der Seite sind „Alle Bilder“ eingestellt. Zeitraum, Revier, Kamera und Status gelten gemeinsam für **Galerie und Liste**: Was du in einer Ansicht einschränkst, bleibt beim Umschalten erhalten. Ein Klick auf das Kalender-Symbol oder den Text öffnet ein Fenster mit den Voreinstellungen („Alle Bilder“, „Heute“, „Gestern“, „Diese Woche“, „Dieser Monat“, „Dieses Quartal“, „Dieses Jahr“, „Letzte 7 Tage“, „Letzte 30 Tage“, „Letzte 365 Tage“, „Letzte 12 Monate“) und einem Kalender. Für einen eigenen Zeitraum tippst du zuerst den Starttag und dann den Endtag an (ein einzelner Tag genügt) und bestätigst mit „Auswählen“. Unter dem Kalender kannst du optional die Uhrzeit von/bis einstellen (Standard 00:00 bis 23:59). Die Pfeile daneben schieben den Zeitraum um dessen Länge vor und zurück (Monat → Vormonat, Woche → Vorwoche), „Jetzt“ springt zurück auf die aktuelle Woche, den aktuellen Monat usw. Die Voreinstellungen werden bei jedem Aufruf neu berechnet. Popup, Vor/Zurück und Diashow laufen nur über die gefilterten Bilder.

Der erste Reiter **Bilder** zeigt alle Vorschaubilder als Kacheln, ähnlich wie RevierSpion: Bild, Revier und Klarname der Kamera, Datum und Uhrzeit des Empfangs. Oben filterst du nach **Revier** und **Kamera**. Ein Klick auf ein Bild öffnet es groß als Popup, mit Pfeilen, Pfeiltasten oder Wischen blätterst du durch alle Bilder der aktuellen Filterung. Das Feld **Status** filtert nach Weiterleitungsstatus (Standard: alle). Es erscheinen 500 Fotos je Seite, die neuesten zuerst. Gibt es mehr, lädt „Weitere 500 laden“ die nächsten nach, und die Vorschau läuft nahtlos weiter. Ohne Foto gibt es keine Kachel. Empfangsadresse, Mehrfachauswahl und Löschen findest du in der **Liste** (Umschalter unter den Reitern).

**Diashow:** Der grüne Knopf mit der Anzahl der Fotos („▶ 6 Fotos") spielt die gefilterten Fotos automatisch ab, vom ältesten zum neuesten. Im Popup wählst du mit dem Knopf **neu → alt / alt → neu** die **Abspielrichtung** (der Wert wird im Browser gemerkt, der grüne Knopf startet immer beim ältesten Foto). Im Popup stellst du mit dem Regler **Tempo** die Anzeigedauer pro Foto ein (0,1 bis 3 Sekunden, Standard 0,8 s; der Wert wird im Browser gemerkt). ▶/❚❚ oder die Leertaste starten und pausieren, die Pfeile blättern von Hand weiter, am Ende hält die Show an.

**Zoomen:** Im Popup kannst du Fotos mit zwei Fingern aufziehen (Pinch), mit einem Finger verschieben und per Doppeltipp rein- bzw. rauszoomen (am Rechner: Mausrad, Doppelklick, Ziehen). Gewischt wird zum Blättern nur, solange das Foto nicht vergrößert ist; beim Wechsel des Fotos wird der Zoom zurückgesetzt.

## Board: Vorschau, Filter und Löschen

- **Vorschau**: Jede Mail mit Foto zeigt ein Vorschaubild, auch nach der Zustellung. Ein Klick öffnet es als Popup. Mit den Pfeilen (oder den Pfeiltasten, am Handy Wischen) blätterst du durch alle Bilder der aktuellen Ansicht, `Esc` schließt. Die Vorschau ist das erste Foto der Mail (nur Fotos bis 6 MB, Videos haben keine bleibende Vorschau).
- **Filter**: Zusätzlich zum Status (Alle, Probleme, Gedrosselt, …) lässt sich nach **Revier** und nach **einzelner Kamera** filtern. Das Blättern im Popup folgt dem Filter. Die Liste zeigt 500 Einträge je Seite, „Weitere 500 laden“ fügt die nächsten hinzu. Sie hat dieselbe Zeitauswahl wie die Galerie, auch „Alle in dieser Ansicht löschen“ wirkt nur auf die gewählte Auswahl (Zeitraum, Revier, Kamera, Status) und nennt vorher die Anzahl.
- **Löschen**: pro Mail über „Löschen", mehrere über die Häkchen und „Ausgewählte löschen", oder „Alle in dieser Ansicht löschen" (nur die Treffer des aktuellen Status- und Kamerafilters). Mails, die noch auf die Zustellung warten, werden nie gelöscht.
- **Automatisch**: Fotos samt Status werden nach der eingestellten Aufbewahrung (Standard 180 Tage) gelöscht. Ist das Speicherlimit voll (Standard 1024 MB = 1 GB), wird das jeweils älteste Foto gelöscht (FIFO), Favoriten und noch wartende Mails bleiben. Der Hinweis im Board erscheint nur, wenn der Speicher voll ist.
- **Neu senden im Popup:** Oben im großen Bild steht neben dem Stern der Knopf „Neu senden“ (bei nicht weitergeleiteten Fotos mit dem Status in Klammern).

## Kameras

**Vorbelegung (optional):** Wer viele Kameras auf einmal benennen will, legt die Datei `/share/kamera_relay/kameras_vorbelegung.json` an, Inhalt: `[["adresse@revierspion.de", "Klarname", "Revier"], ...]`. Beim Start werden daraus nur Kameras ergänzt, die noch nicht in der Tabelle stehen. Änderungen im Board bleiben erhalten.

**Kameratabelle:** Im Board gibt es den Reiter „Kameras“. Jede Empfangsadresse, von der
jemals eine Mail ankam, wird dort automatisch angelegt (neue stehen oben, markiert mit „neu“).
Du trägst **Klarname** ein und wählst das **Revier** aus der Liste, dann „Alles speichern“.
Der Klarname muss **im Revier eindeutig** sein, sonst lehnt das Board ab und nennt die Kamera, die ihn schon trägt.
Neue Kameras meldet das Protokoll mit „Neue Kamera erkannt“ und tragen vorläufig den Namen `Neu-<Adressteil>`.

**Reviere:** Im Reiter „Reviere“ pflegst du die Liste (anlegen, umbenennen, löschen). Ein Revier lässt sich nur löschen, wenn ihm keine Kamera mehr zugeordnet ist. Bestehende Revier-Einträge aus älteren Ständen und aus der Vorbelegung werden beim Start automatisch übernommen.

**Ordner auf dem Share:** Mit „Fotos auf dem Share ablegen“ = an liegen die Fotos unter

```
/share/kamera_relay/<Revier>/<Klarname>/<JJJJ-MM-TT>_<HH-MM-SS>_<Nr>.jpg
```

Kameras ohne Revier liegen unter `Ohne Revier`. Benennst du eine Kamera um oder ordnest sie einem anderen Revier zu, wird ihr Ordner umbenannt bzw. verschoben, und beim Umbenennen eines Reviers der Revier-Ordner. Die Ordner sind für andere Benutzer geschützt (lesen ja, ändern nur durch das Add-on). Die Zuordnung steht in der Datenbank, **löschen geht im Board**: Revier und Kamera filtern, dann „Alle in dieser Ansicht löschen“, auch zeitlich eingeschränkt. Lässt sich der Share nicht beschreiben, bleibt das Foto intern erhalten, die Weiterleitung läuft immer weiter.

## Konfiguration

Home Assistant kennt keine Abschnitte in der Add-on-Konfiguration. Die Einstellungen sind
deshalb nummeriert und stehen in dieser Reihenfolge: 1 Funktionen, 2 Relay, 3 Ziel-SMTP, 4 Backup-SMTP,
5 Zustellung, 6 Fotospeicher, 7 Bilderkennung.

**1 Funktionen (ganz oben)**

| Schalter | Standard | Bedeutung |
|---|---|---|
| Fotos auf dem Share ablegen | an | Alle Fotos liegen unter `/share/kamera_relay/<Revier>/<Kamera>/`. Aus = reines Relay: nur Fotos, die nicht weitergeleitet wurden (gedrosselt, gescheitert, ohne Erkennung, Weiterleitung aus), bleiben intern erhalten und sind im Board neu sendbar. Weitergeleitete Fotos werden gelöscht |
| Fotos an das Ziel weiterleiten | an | Aus = nichts wird weitergeleitet, alle Fotos werden nur gespeichert (Status „Weiterleitung aus“) und lassen sich einzeln neu senden |
| Bilderkennung | an | Markiert jedes Foto als Mensch, Tier, Fahrzeug oder leer |
| Nur Fotos mit Erkennung weiterleiten | aus | Fotos ohne Erkennung werden nicht weitergeleitet, sondern gespeichert. Wirkt nur bei aktiver Bilderkennung und Weiterleitung |

**2 Relay (am Home Assistant)**

| Parameter | Bedeutung |
|---|---|
| Relay: Port | **Pflichtfeld, kein Standard.** Frei wählbar (z. B. 20023, besser über 1024, viele Provider sperren Port 25). Am Router von außen weiterleiten und in jeder Kamera als SMTP-Port eintragen |
| Relay: Benutzer | Login der Kameras (= Absenderadresse, mit @) |
| Relay: Passwort | Passwort der Kameras (mindestens 12 Zeichen) |

**2 Ziel-SMTP** (voreingestellt für RevierSpion)

| Parameter | Voreinstellung |
|---|---|
| Server | smtp.revierspion.de |
| Port | 25 |
| Benutzername | revierspion |
| Passwort | leer, muss eingetragen werden (liegt nur in deiner Konfiguration, nicht im Add-on) |
| Erlaubte Empfänger-Domains | revierspion.de |
| Absender | leer (Absender der Kamera wird unverändert weitergegeben) |

Die Verschlüsselung zum Ziel wird automatisch gewählt: SSL/TLS bei Port 465, STARTTLS bei 587, sonst unverschlüsselt. Ein nicht prüfbares Zertifikat des Ziels wird mit Warnung im Protokoll akzeptiert.

**4 Backup-SMTP** (optional, leer = kein Backup): Server, Port (587), Benutzer, Passwort, Absender.

**5 Zustellung**

| Parameter | Standard | Bedeutung |
|---|---|---|
| Drosselung pro Kamera (Minuten) | 1 | Höchstens ein Foto pro Kamera in diesem Zeitraum, 0 = aus |
| Wartezeit zwischen zwei Mails (Sekunden) | 3 | Pause zwischen zwei Mails zum Ziel |
| Wiederholen bei Fehlern (Minuten) | 30 | Danach gilt die Mail als gescheitert |

**6 Fotospeicher**

| Parameter | Standard | Bedeutung |
|---|---|---|
| Aufbewahrung (Tage) | 180 | Gilt für alle Fotos, auch gedrosselte, gescheiterte und nicht erkannte. Favoriten bleiben |
| Speicherlimit (MB) | 1024 | Ist er voll, wird das älteste Foto gelöscht (FIFO), das Board zeigt einen Hinweis |

**7 Bilderkennung:** 1 Thread, Schwellen Tier 25 %, Mensch 30 %, Auto/Fahrzeug 30 %.

**Zwischenserver (GMX, Gmail, Brevo):** Bei „Ziel: Erlaubte Empfänger-Domains“ muss
weiterhin `revierspion.de` stehen, sonst lehnt das Relay die Mails der Kameras ab.
Bei „Ziel: Absender“ trägst du die Adresse des Kontos ein (z. B. die GMX-Adresse).
Das Relay setzt sie dann als Absender und ersetzt auch den From-Kopf der Mail, weil
viele Anbieter fremde Absender ablehnen. Die ursprüngliche Adresse der Kamera bleibt
als Reply-To und X-Original-From erhalten. Ein Fehler wie „Unauthorized sender
address“ wird wiederholt und zeigt sich im Protokoll mit dem Hinweis, diese
Einstellung zu prüfen.

## Einrichtung

1. Relay-Benutzer und Relay-Passwort eintragen, Add-on starten.
2. Im **Protokoll** prüfen:
   - `Zieltest OK` – der Zielserver ist erreichbar, die Anmeldung klappt
   - `Zieltest FEHLGESCHLAGEN` – danach folgen `Diagnose`-Zeilen: welche Ports am
     Ziel erreichbar sind und ob der Anschluss den Port generell sperrt
3. Am Router den Relay-Port auf dieses Gerät weiterleiten (gleicher Port).
4. Schalter „In der Seitenleiste anzeigen“ einschalten.

## Einstellungen an der Kamera

| Feld | Wert |
|---|---|
| SMTP-Server | öffentlicher Name dieses Home Assistant |
| Port | Relay-Port |
| SSL/TLS | an oder aus – das Relay erkennt es automatisch |
| Absender | Relay-Benutzer |
| Passwort | Relay-Passwort |
| Empfänger | die Adresse der Kamera bei RevierSpion |

## Protokoll lesen

Jede Mail hat eine Nummer. So lässt sich der ganze Weg einer Mail verfolgen:

| Eintrag | Bedeutung |
|---|---|
| `Verbindung von …` | Die Kamera erreicht das Relay |
| `Login ok` / `Login fehlgeschlagen` | Benutzer und Passwort der Kamera |
| `beendet ohne Login (letzter Schritt: …)` | Die Kamera hat abgebrochen, der letzte Schritt steht dabei |
| `[#17] angenommen von …` | Die Mail ist gespeichert, die Kamera hat „OK“ bekommen |
| `[#17] GEDROSSELT …` | Nicht weitergeleitet wegen der Drosselung, im Board neu sendbar |
| `[#17] Zustellversuch 1 an …` | Das Relay sendet zum Ziel |
| `[#17] WEITERGELEITET …` | Das Ziel hat die Mail angenommen |
| `[#17] NICHT WEITERGELEITET (Versuch 2): …` | Fehler mit Ursache im Klartext, nächster Versuch folgt |
| `[#17] ENDGUELTIG GESCHEITERT …` | Aufgegeben, im Board neu sendbar |
| `Tagesbericht …` | Tägliche Zusammenfassung um Mitternacht |

## Verschlüsselung

Auf der Kameraseite nimmt das Relay Verbindungen **so kompatibel wie möglich** an:
unverschlüsselt, mit STARTTLS und mit SSL/TLS, auch mit alten TLS-Versionen (ab 1.0) für
ältere Kamera-Firmware. Dafür gibt es keine Einstellungen.

Liegt unter `/ssl/fullchain.pem` und `/ssl/privkey.pem` ein Zertifikat (z. B. vom
Duck-DNS-Add-on), wird es automatisch verwendet und nach einer Erneuerung selbstständig
neu geladen. Damit die Kamera es akzeptiert, muss als SMTP-Server der Name eingetragen
sein, auf den das Zertifikat lautet. Ohne Zertifikat läuft das Relay unverschlüsselt.

## Schutz

- Annahme nur mit Benutzer und Passwort
- Weiterleitung nur an Empfänger in der erlaubten Domain
- Öffentliche Adressen werden nach 5 falschen Logins für 15 Minuten gesperrt
- Das Board lauscht nur auf der internen Adresse des Add-ons und ist nur über Home
  Assistant (Login) erreichbar

## Protokoll mit Kameranamen

Im Protokoll steht bei jeder angenommenen und weitergeleiteten Mail der Klarname der Kamera mit Revier, dazu die Adresse in Klammern, z. B. `Kamera Hochleithen Vier (5193_xxxxxx@revierspion.de)`. Mails ohne Foto oder Video im Anhang sind mit „OHNE FOTO/VIDEO im Anhang“ markiert.

Beim Verbindungsaufbau (Zeilen „Verbindung von …“, „fordert STARTTLS an“, „Login ok von …“) ist die Kamera noch nicht bekannt, weil die Empfängeradresse erst danach kommt. Dort steht deshalb der Hinweis „vermutlich <Kamera>“. Er stammt von der Kamera, die zuletzt von derselben IP-Adresse gesendet hat. Teilen sich mehrere Kameras eine IP-Adresse (Mobilfunk), steht „eine von: …“. Ist die IP neu, gibt es keinen Hinweis. Die endgültige Zuordnung steht in der Zeile „angenommen von …“.

## Reiter „Verbindungen“ (früher „Auswertung“)

Hier steht jede Verbindung einer Kamera zum Relay, auch Test-Mails und Fehler: Datum und Uhrzeit, Klarname der Kamera, Status und Einzelheiten (IP-Adresse, Verschlüsselung, Dauer, Größe, Betreff). Gefiltert wird nach Revier, Kamera und Zeitraum (Standard: letzte 7 Tage) und nach „Alle“, „Probleme“, „Tests“ oder „Mails mit Foto/Video“.

Als **Test-Mail** gilt jede Mail ohne Foto und Video im Anhang (so schickt Reolink den Test). Mögliche Probleme:
- **SSL/TLS-Handshake nicht ok** oder **STARTTLS-Handshake nicht ok**: Die Verschlüsselung kam nicht zustande, z. B. weil die Kamera ein zu altes Verfahren nutzt.
- **Login falsch**: Benutzer oder Passwort in der Kamera stimmen nicht.
- **Abbruch vor dem Login**: Die Kamera hat die Verbindung begonnen und vorher aufgegeben, der letzte Schritt steht dabei.
- **Abbruch nach Login, Mail nicht angekommen**: Die Kamera war angemeldet, die Mail (meist mit Anhang) ist aber nicht vollständig angekommen. Der letzte Schritt steht dabei.
- **Empfänger nicht erlaubt**: Die Empfängeradresse gehört nicht zu den erlaubten Domains.
- **Adresse gesperrt**: zu viele falsche Logins von derselben IP-Adresse.

Verbindungen, die keiner Kamera zuzuordnen sind (z. B. Portscans aus dem Internet), sind standardmäßig ausgeblendet und lassen sich mit „einblenden“ anzeigen. Die Einträge werden wie die Mails nach der eingestellten Aufbewahrungszeit gelöscht.

Jeder Kontaktversuch auf den Relay-Port steht sofort im Protokoll („Kontaktversuch auf Port … von IP:Port“), auch wenn danach nichts mehr kommt, etwa bei Portscans oder wenn die Kamera nach dem Verbinden abbricht.

## Bilderkennung

Jedes Foto einer Mail wird lokal auf dem Gerät mit dem Wildtier-Modell MegaDetector V6 geprüft (etwa 0,5 bis 1,5 Sekunden pro Bild auf dem Pi 5, im Hintergrund, der Mail-Empfang wartet nie darauf). Das Modell ist auf Wildkamerabilder trainiert und erkennt auch Wild bei Infrarot-Nachtaufnahmen. Das Ergebnis ist pro Mail eine grobe Einordnung:

- **Tier** (jedes Wild, Vögel, Katze, Hund u. a.), **Mensch**, **Fahrzeug** oder **leer** (nichts erkannt, möglicher Fehlalarm).
- Es stehen **alle** erkannten Klassen nebeneinander, z. B. „Mensch 88 %, Fahrzeug 93 %". Bei mehreren Fotos zählt je Klasse der beste Wert, geprüft werden bis zu 6 Fotos je Mail.
- Links oben im Bild steht der Kurztext mit der Sicherheit, z. B. „Tier 87 %". Er ist nur eine Einblendung im Board, das Foto selbst und die weitergeleitete Mail bleiben unverändert.
- Im Reiter „Bilder" filtert das Feld **Erkennung** zusätzlich zu Revier, Kamera und Zeitraum (z. B. alle Kameras mit „leer“ oder Revier Tauchendorf mit „Tier“) (irgendeine Erkennung, mit Tier, mit Mensch, mit Fahrzeug, leer, noch nicht geprüft). „Mit Mensch" zeigt jede Mail, in der ein Mensch erkannt wurde, auch wenn zusätzlich ein Fahrzeug oder Tier dabei ist; es wird immer ein Etikett ausgewählt. In der Liste zeigt ein Etikett das Ergebnis.
- Im großen Bild (Klick aufs Foto) werden die erkannten Mensch, Tier und Fahrzeug mit farbigem Rahmen, Klasse und Prozent eingezeichnet (Mensch orange, Tier grün, Fahrzeug blau). Der Knopf **Rahmen** unten blendet sie ein oder aus. Rahmen gibt es nur für das angezeigte erste Foto der Mail. Die Rahmen sind eine Orientierung: dicht stehende Tiere werden teils zu einem Rahmen zusammengefasst, genau gezählt wird nicht.
- **Rechenlast:** Nur während ein Foto geprüft wird (etwa 1 bis 2 s mit dem Standard von 1 Kern), sonst nichts. Eine Mail mit mehreren Fotos belegt die CPU für ein paar Sekunden. Dazu kommen rund 400 MB Arbeitsspeicher, solange die Erkennung an ist.
- **Standardmäßig wird alles weitergeleitet.** Die Erkennung markiert nur. Optional kann der Filter (siehe unten) Mails ohne Erkennung zurückhalten.

**Grenzen:** Eine Artbestimmung (Reh, Wildschwein …) gibt es nicht, nur Tier, Mensch und Fahrzeug. Sehr kleine, verdeckte oder stark angeschnittene Tiere und Spinnweben vor der Linse können trotzdem durchrutschen. „Leer" heißt deshalb nur „das Modell hat nichts gefunden", nicht „sicher ein Fehlalarm".

**Einstellungen:** *Bilderkennung* (Gruppe 1 Funktionen, Standard an; zum Ausschalten abhaken und das Add-on neu starten) und optional *Threads* (Standard 1; so viele CPU-Kerne nutzt die Erkennung höchstens, sie läuft immer mit niedrigster Priorität, Home Assistant hat Vorrang) sowie drei Schwellen in % (*Schwelle Tier* Standard 25, *Schwelle Mensch* Standard 30, *Schwelle Fahrzeug* Standard 30; niedriger findet mehr, auch mehr Falsches). Beim Start werden die letzten 300 Mails nachträglich geprüft, die noch nicht oder mit einem älteren Modell geprüft wurden (nach einem Modellwechsel werden alte Ergebnisse also neu berechnet; bei bereits zugestellten Mails wird nur das aufbewahrte Vorschaubild, also das erste Foto, geprüft; ist auch das nicht mehr da, bleibt das alte Ergebnis).

**Nur Fotos mit Erkennung weiterleiten** (Schalter in Gruppe 1, Standard aus):
- **aus:** alles wird weitergeleitet, die Erkennung markiert nur. Mit dem Feld *Erkennung* = „leer“ im Reiter „Bilder“ siehst du vorab, was der Filter stoppen würde.
- **an:** Mails mit Foto warten kurz (etwa 1 bis 2 Sekunden) auf die Erkennung. Wurde nichts erkannt, wird die Mail **nicht** weitergeleitet, sondern als Foto mit Status „ohne Erkennung“ gespeichert (Liste, dort mit „Neu senden“). Sie zählt nicht für die Drosselung der Kamera.
- **Sicherheitsnetz:** Mails ohne Foto, Mails mit nicht auswertbaren Fotos und Mails, bei denen die Erkennung ausfällt oder länger als 2 Minuten braucht, werden immer weitergeleitet. Ist die Bilderkennung aus oder das Modell nicht ladbar, wirkt der Filter nicht.
- Fotos ohne Erkennung bleiben so lange gespeichert wie alle anderen (Aufbewahrung laut Einstellung).

**Modelle (Reiter „Modelle")**: Das Add-on bringt MegaDetector V6 (klein) mit. Neuere oder größere Modelle im ONNX-Format lassen sich ohne neue Add-on-Version einsetzen, und zwar über Ordner in *share* (z. B. per Samba- oder File-Editor-Add-on) oder im Reiter „Modelle":

- `/share/kamera_relay/` (Hauptordner): Die Datei hier ist das **produktive** Modell. Der Dateiname ist egal. Liegt dort keine, gilt das eingebaute. Liegen mehrere dort, gilt die zuletzt geänderte.
- `/share/kamera_relay/test/`: Die Datei hier ist das **Testmodell**. Es wertet nur Favoriten aus (siehe unten).
- `/share/kamera_relay/vorher/`: Das zuletzt produktive Modell, für den Weg zurück.

Das Add-on schaut alle 30 Sekunden in die Ordner und übernimmt Änderungen **ohne Neustart**. Im Reiter: Anzeige von produktivem und Testmodell (mit Dateigröße, Eingabegröße, Ø-Zeit pro Foto und Trefferquote auf deinen Labels), **Hochladen** eines Testmodells (in Stücken zu 10 MB, bei Abbruch wird fortgesetzt; wird vor dem Ablegen geprüft: ladbar, richtiges Ausgabeformat, Probebild), **Test in Produktion setzen** (das bisherige produktive Modell kommt nach `vorher/`), **Vorheriges Modell wiederherstellen**, **Auf eingebautes Modell zurück** und **Testmodell löschen**. Ist eine Datei nicht ladbar, bleibt das bisherige Modell aktiv und der Reiter zeigt eine Meldung.

Beim Wechsel des produktiven Modells werden **nur die Favoriten** neu ausgewertet (hat das Testmodell sie schon berechnet, wird dieses Ergebnis übernommen). Alle anderen Fotos behalten ihre Etiketten, auch beim Start oder nach einem Add-on-Update. Wer den Bestand doch neu bewerten will, drückt im Reiter **Bestand neu prüfen** (läuft nie von selbst, mit Fortschritt und Abbrechen, etwa eine Sekunde pro Foto). Beim Start werden nur Fotos ohne Ergebnis nachgeholt.

Unterstützt werden die YOLO-Varianten von MegaDetector V6 (v9/v10) im ONNX-Format mit den Klassen Tier, Mensch, Fahrzeug; die Eingabegröße (z. B. 640 oder 1280 px) liest das Add-on aus der Datei. Das Umwandeln neuer Gewichte (`.pt`) nach `.onnx` geschieht außerhalb des Add-ons. Das Hochladen im Browser ist auf 600 MB begrenzt; bei Problemen die Datei direkt in den Ordner kopieren.

**Favoriten (Stern):** Im großen Bild oben links steht ein Stern (Taste **F**). Ein Tipp macht das Foto zum Favoriten, ein zweiter nimmt die Markierung weg. Favorit-Fotos haben auf der Kachel einen gelben Stern. Im Reiter „Bilder" blendet der Haken **★ Favoriten** nur sie ein (der Zeitraum springt dabei auf „Alle Bilder") und lässt sich mit der Erkennung kombinieren. **Favoriten werden nie automatisch gelöscht**: weder durch die Aufbewahrungsfrist noch durch das Speicherlimit, und auch „Alle in dieser Ansicht löschen" überspringt sie. Nur der einzelne Löschen-Knopf entfernt einen Favoriten.

**Testmodell (nur Favoriten):** Liegt ein Modell im Ordner `test/` (oder wurde es im Reiter hochgeladen), prüft das Add-on damit **alle Favoriten automatisch**, auch jeden neuen Favoriten sofort. Der laufende Betrieb, der Filter und die Etiketten bleiben beim produktiven Modell. Im großen Bild stehen beide Ergebnisse („Bisher: …", „Test: …"), die Rahmen des Testmodells sind gestrichelt. Im Reiter „Modelle" zeigt die **Kontrolle** alle Favoriten mit beiden Ergebnissen, mit Zähler (gleich / abweichend) und dem Schalter „nur Abweichungen". **Eigene Labels:** Bei Favoriten kannst du im großen Bild (Knöpfe oder Tasten 1 bis 4) und unter jedem Foto in der Kontrolle festhalten, was wirklich im Bild ist (Tier, Mensch, Fahrzeug, mehrere möglich, oder Leer). Das Add-on vergleicht Bisher und Test damit: ✓ bei Übereinstimmung, sonst „Fehlalarm“ (gemeldet, aber nicht da) oder „übersehen“ (da, aber nicht gemeldet); oben steht je Modell, wie viele Favoriten richtig liegen. Das Label gilt pro Foto, nicht pro Rahmen. Damit auch Fehlalarme auf leeren Bildern auffallen, auch einige Fotos ohne Inhalt als Favorit markieren und mit „Leer“ labeln. Taugt das Testmodell, setzt der Knopf es in Produktion. **Unter der Schwelle:** Bei Favoriten zeigt das Board auch, was ein Modell knapp **nicht** gemeldet hat (grau, mit Prozentwert und gepunktetem Rahmen, nur die sicherste Erkennung je Klasse). Das hilft beim Einstellen der Schwellen. Jedes Modell hat eigene **Schwellen** (Tier, Mensch, Fahrzeug), einstellbar im Reiter Modelle; sie gelten vor den Werten der Add-on-Konfiguration und gehen mit dem Modell in die Produktion. Die grauen Werte entstehen beim Auswerten: für das Testmodell mit **Favoriten jetzt prüfen**, für das Produktivmodell mit **Favoriten mit diesem Modell neu prüfen**. Der Knopf **Favoriten jetzt prüfen** rechnet alle Favoriten neu (sinnvoll nach einer Änderung der Threads). Der Fortschrittsblock zeigt Foto x von y, vergangene Zeit, Zeit pro Foto und die geschätzte Restzeit, er aktualisiert sich selbst; mit **Abbrechen** lässt sich der Lauf beenden. Das Testmodell wird nach zwei Minuten ohne Arbeit wieder aus dem Arbeitsspeicher genommen.

**Hinweis:** Das Add-on-Image basiert auf Debian statt Alpine (onnxruntime gibt es nur für Debian) und ist dadurch größer.

**Lizenzhinweis:** Das Modell (MegaDetector V6, Microsoft, Lizenz AGPL-3.0) liegt als Datei im Add-on. Für den privaten Einsatz ist das unproblematisch; bei Weitergabe des Add-ons gilt die Lizenz des Modells.
