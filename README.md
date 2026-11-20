# AlarmCam Analyse und Mail-Relay

Home-Assistant-Add-on: zentrale Verwaltung der Mailweiterleitung von Reolink- und anderen Kameras, die bei Alarm eine E-Mail senden. Die Fotos werden lokal analysiert (Tier, Mensch, Fahrzeug, leer) und selektiv weitergeleitet.

Die Grundfunktion von RevierSpion ist nachempfunden, mit deutlich besserer Bildanalyse.

## Installation

1. In Home Assistant: **Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories**
2. `https://github.com/christiandulle/HomeAssistant-AlarmCam-Analyse-und-Email-Relay` hinzufügen
3. „AlarmCam Analyse und Mail-Relay“ installieren und konfigurieren (siehe Reiter *Dokumentation* im Add-on)

[![Repository in Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fchristiandulle%2FHomeAssistant-AlarmCam-Analyse-und-Email-Relay)

Unterstützt aarch64 (z. B. Raspberry Pi 5) und amd64.

## Funktionen

- Mail-Annahme mit Login, Drosselung je Kamera, Zustellung mit Wiederholung, optionaler Backup-Server
- Das Foto steht im Mittelpunkt: Status, Kamera und Empfängeradresse liegen am Foto, jedes Foto lässt sich neu senden
- Bilderseite mit Popup, Zoom, Diashow und Favoriten
- Bilderkennung lokal auf dem Gerät, Filter *aus / test / an*
- Modelle austauschen und testen, ohne das Add-on neu zu bauen

Die ausführliche Beschreibung steht in [DOCS.md](kamera_smtp_relay/DOCS.md).

## Lizenzhinweis

Das mitgelieferte Erkennungsmodell (MegaDetector V6, Microsoft) steht unter der **AGPL-3.0**. Für die Weitergabe dieses Add-ons gilt die Lizenz des Modells.
