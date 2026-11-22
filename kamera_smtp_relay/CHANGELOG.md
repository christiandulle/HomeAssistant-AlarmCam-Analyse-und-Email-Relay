# Changelog

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
