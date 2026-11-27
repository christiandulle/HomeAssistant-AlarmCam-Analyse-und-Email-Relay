# Changelog

## 1.3.4

- **Bessere Bildauswahl bei Videos:** Die Klassen-Anzahl entscheidet weiter zuerst. Unter den Bildern, deren Sicherheit höchstens 10 Prozentpunkte unter dem besten liegt, haben jetzt Bilder Vorrang, bei denen kein erkanntes Objekt den Bildrand berührt (nicht abgeschnitten). Danach gewinnt das Bild, auf dem die Objekte der Bildmitte am nächsten sind (bei mehreren Objekten nach Sicherheit gewichtet). Erst bei fast gleicher Lage entscheidet die Schärfe, zuletzt das mittlere Bild. Ein deutlich sichereres Bild (mehr als 10 Punkte) gewinnt weiter

## 1.3.3

- **Login mit der RevierSpion-Adresse:** Beim SMTP-Login gilt zusätzlich zum festen Benutzer jede Adresse aus den erlaubten Domains (z. B. `5193_xxxxxx@revierspion.de`) als Benutzername. Das Passwort muss weiterhin stimmen und ist für alle Kameras gleich. Trägst du an der Reolink-Kamera die RevierSpion-Adresse als Absender ein, legt Reolink sie automatisch auch als Empfänger an, die Adresse `kamera@…` als zweiter Empfänger entfällt
- Das Log nennt die Kamera sofort nach dem Login („Login ok von … - Kamera Revier Name (Adresse)“), auch wenn danach keine Mail ankommt. Bei einem fehlgeschlagenen Login steht der verwendete Benutzername im Log (nie das Passwort)
- Die Sperre nach zu vielen falschen Logins gilt unverändert auch für Logins mit Adresse

## 1.3.2

- Video im Vollbild: Auf Handy und Tablet (Touchscreen) startet das Video jetzt automatisch im Vollbild. Auf dem iPhone, auch mit Chrome, öffnet dafür der Vollbild-Player von iOS, nach „Fertig“ ist man wieder in der Vergrößerung. Am Rechner mit Maus startet das Video wie bisher klein in der Vergrößerung, der Vollbild-Knopf des Players bleibt dort verfügbar

## 1.3.1

- Bilder, Vergrößerung: „▶ Video“ sitzt jetzt direkt unter dem Foto, daneben „Rahmen“. Ein Klick auf „Video“ startet das Video sofort im Vollbild (iPhone: Vollbild-Player von Safari). Wer das Vollbild verlässt, bleibt in der Vergrößerung, ein zweiter Klick geht zurück zum Standbild. Weiterblättern oder Schließen beendet auch das Vollbild
- Bilder, Vergrößerung: die Zeitraffer-Knöpfe stehen in einer eigenen Zeile darunter (Play, Richtung, Tempo). Die Richtung heißt „vorw.“ bzw. „rückw.“ statt „alt → neu“ bzw. „neu → alt“
- Bilder, Vergrößerung: die Label-Leiste (Tier, Mensch, Fahrzeug, Leer) steht oben unter dem Stern in der zweiten Reihe und erscheint nur, wenn der Stern aktiv ist (Tasten 1 bis 4 wie bisher)
- Bilder-Raster: die Etiketten unter den Fotos (Tier, Leer, Ohne Erkennung usw.) sind kleiner, damit sie am Handy weniger Platz brauchen
- Leerlauf-Grenze beim Mail-Empfang von 60 auf 180 Sekunden erhöht, damit zähe Uploads (z. B. Videos aus dem WLAN) nicht vom Add-on abgebrochen werden

## 1.3.0

- **Videos werden ausgewertet.** Kommt eine Mail mit Video, schneidet das Add-on Standbilder heraus: in den ersten 10 Sekunden alle 2 Sekunden, danach alle 4 Sekunden, bis höchstens 30 Sekunden. Die Videodekodierung übernimmt die Bibliothek PyAV, ein eigenes ffmpeg ist nicht nötig
- Alle Standbilder laufen durch die normale Erkennung (Schwellen des Modells). Ein in der Mail mitgeschicktes Foto zählt als zusätzlicher Kandidat
- **Bestes Bild:** zuerst die meisten Klassen über der Schwelle, dann die höchste Sicherheit. Liegen mehrere Bilder nah beieinander (bis 5 Prozentpunkte), gewinnt das schärfste (Schärfe im Bereich des erkannten Motivs), bei Gleichstand das mittlere. Findet kein Bild etwas, zählt der höchste Wert unter der Schwelle
- **Weitergeleitet wird nur das beste Bild** als Foto, das Video hängt nicht an. Findet kein Bild etwas, verhält sich die Mail wie ein leeres Foto (bei Filter „an“ wird sie nicht weitergeleitet). Das Video bleibt in jedem Fall abgelegt (neben dem Foto im Kameraordner)
- Board: Kacheln mit Video tragen den Marker „Video“. In der Vergrößerung öffnet der Knopf „Video“ einen Player direkt im Bild (mit Vor- und Zurückspulen), ein zweiter Klick geht zurück zum Standbild. Löschen im Board entfernt Foto und Video
- Videos kommen in der Warteschlange nach den Fotos dran und laufen wie die Bilderkennung mit niedriger Priorität. Lässt sich ein Video nicht lesen, wird die Mail unverändert (mit Video) weitergeleitet, es geht nichts verloren
- Der belegte Speicher zählt Standbild und Video. Bitte das Speicherlimit im Blick behalten: Videos sind deutlich größer als Fotos

## 1.2.0

- Kontrolle: Zwei Weiterleitungs-Kennzahlen je Modell: **„nicht weitergeleitet, obwohl etwas da ist“** (Label mit Inhalt, Modell meldet nichts) und **„leer weitergeleitet“** (Label „Leer“, Modell meldet etwas). Weitergeleitet heißt: mindestens eine Klasse wird gemeldet. Die Zahlen stehen in „Gegen deine Labels“, im Schwellen-Vergleich (aktuelle und beste Schwellen) und in der Live-Nachrechnung bei den Schwellen-Feldern
- Kontrolle: **Rahmen der Kacheln**: rot = nicht weitergeleitet, obwohl etwas da ist; orange = weitergeleitet, obwohl leer (Fehlalarm); gelb = nur ein Label falsch (z. B. Auto nicht erkannt, aber die Mail geht raus) oder die Modelle sind uneinig. Mit Legende
- Kontrolle: zwei neue Filter **„Nicht weitergeleitet“** und **„Leer weitergeleitet“** (kombinierbar mit Modell und Klasse)
- Schwellen-Empfehlung richtet sich zuerst nach der Weiterleitung (nicht weitergeleitet zählt 2, leer weitergeleitet 1), bei Gleichstand nach den Klassen-Kosten wie bisher

## 1.1.9

- Schwellen-Empfehlung: **neue Gewichtung der Fehler**. Fehlalarm zählt 1. Ein übersehenes Objekt zählt **2**, wenn auf dem Foto gar nichts gemeldet wird (das Foto gilt als leer, die Mail geht eventuell nicht raus), aber nur **0,5**, wenn auf dem Foto eine andere Klasse gemeldet wird (z. B. Person erkannt, Auto nicht: die Mail geht trotzdem raus)
- Weil die Kosten einer Klasse jetzt von den Schwellen der anderen abhängen, sucht die Empfehlung die Schwellen reihum, bis sich nichts mehr verbessert, und setzt jede in die Mitte ihrer besten Strecke
- Der Schwellen-Vergleich und die Live-Nachrechnung bei den Schwellen-Feldern zeigen zusätzlich die **Kosten**; die Zähler für Fehlalarm und übersehen bleiben ganze Zahlen

## 1.1.8

- Kontrolle: **Filter** für Modell (Produktiv, Test), Art (Fehlalarm, Übersehen, Richtig) und Klasse (Mensch, Tier, Fahrzeug), jeweils antippen zum Ein- und Ausschalten und beliebig kombinierbar (z. B. „Produktiv, Fehlalarm, Tier“); Art und Klasse beurteilen nur Fotos mit deinem Label
- Kontrolle: **Schwellen-Vergleich** je Modell nach deinen Labels: Fotos richtig, Fehlalarme und Übersehenes bei den aktuellen und bei den besten Schwellen (übersehen zählt doppelt so schwer wie ein Fehlalarm)
- Modelle: Bei den Schwellen-Feldern rechnet das Board **sofort nach**, was bei den eingegebenen Werten gemeldet worden wäre (Fehlalarm und übersehen je Klasse, Fotos richtig), ohne zu speichern und ohne das Modell neu laufen zu lassen. „Empfehlung eintragen“ übernimmt die besten Schwellen in die Felder; gespeichert wird erst mit „Speichern“
- Favoriten werden nach den aktuell eingestellten Schwellen angezeigt und bewertet (Kachel, großes Bild in der Kontrolle, „Gegen deine Labels“, Trefferquote), sobald für sie Werte unter der Schwelle gemerkt sind; das damals gespeicherte Ergebnis bleibt unverändert
- Technik: neue Datei `tune.py` für die Rechnung

## 1.1.7

- Erkennung merkt sich je Foto und Modell die **sicherste Erkennung jeder Klasse auch unter der Schwelle** (ab 10 %) samt Rahmen. Das passiert bei jeder Auswertung, die Anzeige gilt nur für Favoriten
- Große Bild und Kontrolle: Erkennungen unter der Schwelle erscheinen **grau** („Tier 18 % (unter Schwelle)“), im Foto als grauer gepunkteter Rahmen, getrennt für „Bisher“ und „Test“. Werte gibt es für Favoriten, die nach dem Update mit „Favoriten jetzt prüfen“ (Test) bzw. „Favoriten mit diesem Modell neu prüfen“ (Produktiv) gelaufen sind
- Modelle: **eigene Schwellen je Modell** (Tier, Mensch, Fahrzeug in %), einstellbar für Produktiv- und Testmodell direkt im Reiter. Sie gelten sofort für neue Fotos, werden in `schwellen.json` im Modellordner gespeichert und gehen beim „Test in Produktion setzen“ mit dem Modell mit. Ohne eigene Einstellung gelten die Werte aus der Add-on-Konfiguration
- Modelle: neuer Knopf „Favoriten mit diesem Modell neu prüfen“ beim Produktivmodell (nur Favoriten, mit Fortschritt und Abbrechen)
- „Test in Produktion setzen“ übernimmt auch die Werte unter der Schwelle der Favoriten

## 1.1.6

- Modelle: Jedes Modell (Produktiv, Test) zeigt unter dem hervorgehobenen Namen Dateigröße, Eingabegröße in Pixeln, durchschnittliche Zeit pro Foto und die **Trefferquote** auf deinen gelabelten Favoriten; die Werte aktualisieren sich während des Favoriten-Tests selbst
- Modelle: Testmodell mit Kennzeichnung „aktives Testmodell“; Reihenfolge: Name, Upload, Fortschritt, darunter die Knöpfe „Test in Produktion setzen“ und „Testmodell löschen“ ganz unten
- Modelle: Eine einzige Statuszeile ab dem Start eines Uploads: „Upload: Teil 1 von x“ mit Prozent, danach „Modell wird geprüft“, danach „Favoriten werden geprüft: Foto x von y“

## 1.1.5

- Favoriten selbst labeln: im großen Bild (nur bei Favoriten) Knöpfe **Tier, Mensch, Fahrzeug, Leer** (mehrere wählbar, „Leer“ schließt die anderen aus; Tasten 1 bis 4) und dieselben Knöpfe unter jedem Foto in der Kontrolle
- Kontrolle: Jedes Modell (Bisher, Test) wird automatisch mit deinem Label verglichen, ✓ bei Übereinstimmung, sonst „Fehlalarm: …“ bzw. „übersehen: …“; abweichende Kacheln sind markiert, „nur Abweichungen“ zeigt genau diese
- Kontrolle: Zusammenfassung „Gegen deine Labels“ je Modell (x von y richtig, Fotos mit Fehlalarm, Fotos mit Übersehenem)
- Labels bleiben dauerhaft am Foto und gelten für jedes spätere Modell

## 1.1.4

- Modelle: Upload in Stücken zu 10 MB (der Home-Assistant-Zugang lehnt große Einzel-Uploads mit Fehler 413 ab). Fehlgeschlagene Stücke werden bis zu dreimal wiederholt, ein abgebrochener Upload wird beim nächsten Versuch mit derselben Datei fortgesetzt; Anzeige „Stück 5 von 12 · 42 %“
- Modelle: Knopf „Favoriten jetzt prüfen“ rechnet alle Favoriten mit dem Testmodell neu (z. B. nach einer Änderung der Threads), mit „Abbrechen“
- Modelle: Fortschrittsblock für den Favoriten-Test mit aktuellem Foto, vergangener Zeit, Zeit pro Foto und geschätzter Restzeit; die Seite aktualisiert sich dabei selbst (ohne Neuladen), die Kacheln füllen sich live; nach dem Lauf steht eine Zusammenfassung (Dauer, Zeit pro Foto, Arbeitsspeicher)

## 1.1.3

- Modelle: Leistungsmessung. Reiter „Modelle“ zeigt je Modell (Produktiv, Test) die Zeit pro Foto (Mittel und längste), die Thread-Zahl, den Arbeitsspeicher (Zuwachs beim Laden und Spitze) und eine Hochrechnung der Rechenzeit pro Tag; beim Testmodell zusätzlich der Vergleich auf denselben Favoriten („x-mal so lang“)
- Kontrolle der Favoriten: Zeit pro Foto neben „Bisher“ und „Test“
- Beim Laden eines Modells läuft ein Probelauf, damit der langsamere erste Durchlauf die Messung nicht verfälscht

## 1.1.2

- Bilder und Liste aktualisieren sich selbst: alle 30 Sekunden Prüfung auf neue Fotos (nur bei sichtbarem Reiter und geschlossenem Popup); ganz oben lädt die Seite neu, sonst erscheint „N neue Fotos – anzeigen“
- Erkennungsrahmen im Popup: 1 px und gedeckte Farben (Tier dunkelgrün, Mensch Orange, Fahrzeug Blau)
- Uhrzeit im Popup in Grün hervorgehoben
- Fehlerbehebung: Gelöschte Reviere kehrten nach jedem Start aus der Kamera-Vorbelegung zurück; Reviere aus der Vorbelegung entstehen nur noch mit einer neu angelegten Kamera
- Das Board heißt jetzt „AlarmCam“ (vorher „Kamera-Mails“)

## 1.1.1

- Liste: Umschalter Galerie/Liste wieder als Knöpfe dargestellt (Darstellungsfehler)
- Galerie und Liste teilen sich die Auswahl (Zeitraum, Revier, Kamera, Status); Standard-Zeitraum ist „Alle Bilder“
- Liste mit Zeitauswahl wie die Galerie, „Alle in dieser Ansicht löschen“ wirkt auch zeitlich eingeschränkt
- 500 Einträge je Seite in Galerie und Liste, „Weitere 500 laden“
- Texte der Fotospeicher-Einstellungen: gespeichert wird auf dem Share (bei Ablage aus: intern)

## 1.1.0

- Neue Funktionsschalter ganz oben in der Konfiguration: Fotos auf dem Share ablegen, Fotos weiterleiten, Bilderkennung, nur Fotos mit Erkennung weiterleiten (ersetzt den Filter aus/test/an, der Testmodus entfällt)
- Fotos liegen auf dem Share: `/share/kamera_relay/<Revier>/<Klarname>/<Datum>_<Uhrzeit>_<Nr>.jpg`. Ordner werden beim Umbenennen und Verschieben von Kamera und Revier nachgezogen. Interne Fotos werden beim Start dorthin verschoben
- Ablage aus: nur nicht weitergeleitete Fotos bleiben erhalten, weitergeleitete werden gelöscht
- Weiterleitung aus: alle Fotos werden nur gespeichert (Status „Weiterleitung aus“) und lassen sich einzeln neu senden
- Eigene Pflegeliste der Reviere (neuer Reiter „Reviere“), Revier in der Kameratabelle als Auswahl, Klarname je Revier eindeutig
- Reiter neu geordnet: Bilder | Kameras | Reviere | Verbindungen (früher Auswertung) | Modelle. Der Mails-Reiter ist in „Bilder“ aufgegangen (Umschalter Galerie/Liste, Statusfilter)
- Bilder: 1000 Fotos je Seite mit „Weitere laden“, Popup mit umschaltbarer Abspielrichtung (neu → alt, alt → neu)
- Kameratabelle ohne Foto-Spalte

## 1.0.1

- Konfiguration gruppiert und nummeriert (1 Relay, 2 Ziel-SMTP, 3 Backup-SMTP, 4 Zustellung, 5 Fotospeicher, 6 Bilderkennung; ab 1.1.0 mit Gruppe 1 Funktionen), alle Einstellungen mit Standardwert sichtbar
- Relay-Port ist Pflichtfeld ohne Vorbelegung, mit ausführlicher Beschreibung
- Neue Standardwerte: Drosselung 1 Minute, Fotospeicher 180 Tage / 1 GB, Schwelle Auto/Fahrzeug 30 %
- Entfernt: Bildablage (Ordner /share/kamera_bilder). Das Board ist der einzige Speicher, Aufbewahrung und Limit gelten dort für alle Fotos
- Fix: Foto-Zähler im Reiter Kameras zählt nur noch Mails mit Foto
- Fotospeicher (Aufbewahrung, Speicherlimit) sichtbar und für alle Status beschrieben
- Entfernt: Verschlüsselung erzwingen, Verschlüsselung für ältere Kameras, Zertifikat, privater Schlüssel, SSL/TLS und STARTTLS zum Ziel, maximale Mailgröße. Es gilt größtmögliche Kompatibilität (alle Verbindungsarten, auch alte TLS-Versionen; STARTTLS zum Ziel automatisch bei Port 587)

## 1.0.0

Erste Veröffentlichung.

- Annahme der Alarmmails von Reolink- und anderen Kameras per SMTP, Weiterleitung an einen Ziel-Server (z. B. RevierSpion) mit Wiederholung, Drosselung je Kamera und optionalem Backup-Server
- Foto im Mittelpunkt: je Alarm wird das erste Foto mit Status (weitergeleitet, gedrosselt, gefiltert, gescheitert) und Empfängeradresse gespeichert, jedes Foto lässt sich neu senden
- Speicher: Aufbewahrung (Standard 180 Tage) und Speicherlimit mit FIFO, Hinweis im Board nur bei vollem Speicher
- Lokale Bilderkennung mit MegaDetector V6 (Tier, Mensch, Fahrzeug, leer) mit Filter aus/test/an
- Board mit Bilderseite, Popup mit Zoom und Diashow, Favoriten, Reiter Mails, Auswertung, Kameras und Modelle
- Austausch und Test von Modellen ohne neue Add-on-Version (Ordner in share, Testmodell, Rückgängig)
