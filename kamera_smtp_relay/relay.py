#!/usr/bin/env python3
"""Kamera SMTP-Relay.

Nimmt Mails mit Login an, speichert sie sofort auf der Platte und bestaetigt der
Kamera mit "250 OK". Danach stellt das Relay die Mail unveraendert (1:1) an einen
Ziel-SMTP-Server zu und wiederholt es bei Fehlern. Die Kamera bekommt nie einen
Fehler des Zielservers zu sehen (fire and forget).

Pro Kamera kann die Weiterleitung gedrosselt werden. Gedrosselte und gescheiterte
Mails bleiben einige Tage gespeichert und lassen sich im Board erneut senden.

Es gibt genau einen Port. Ob die Kamera mit SSL/TLS, mit STARTTLS oder
unverschluesselt sendet, wird pro Verbindung automatisch erkannt.
"""

VERSION = "1.1.3"

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import re
import signal
import smtplib
import socket
import ssl
import sys
import threading
import time
import warnings

from aiosmtpd.smtp import SMTP, AuthResult, LoginPassword, TLSSetupException

from board import Board
from store import Runtime, Store, drop_second_image
import detect

warnings.filterwarnings("ignore", module="aiosmtpd")

OPTIONS_PATH = os.environ.get("RELAY_OPTIONS", "/data/options.json")
DATA_DIR = os.environ.get("RELAY_DATA", "/data")

# Board (Seitenleiste): nur auf der internen Adresse des Add-ons, nur fuer den Supervisor
BOARD_BIND = os.environ.get("RELAY_BOARD_BIND", "172.30.32.1")
BOARD_PORT = int(os.environ.get("RELAY_BOARD_PORT", "8099"))
BOARD_ALLOW = [
    ip.strip() for ip in os.environ.get("RELAY_BOARD_ALLOW", "172.30.32.2").split(",") if ip.strip()
]

# Wartezeiten zwischen den Zustellversuchen einer Mail (Sekunden); danach gilt der letzte Wert
RETRY_STEPS = tuple(
    int(x) for x in os.environ.get("RELAY_RETRY_STEPS", "30,60,120,300,600").split(",")
)

# So lange wird nach dem Verbindungsaufbau gewartet, ob die Gegenseite sofort
# verschluesselt beginnt (SSL/TLS). Kommt nichts, ist es normales SMTP.
TLS_DETECT_SECONDS = float(os.environ.get("RELAY_TLS_DETECT_SECONDS", "0.8"))

# Sperre nach zu vielen falschen Logins (nur fuer oeffentliche IP-Adressen)
MAX_FAILED_LOGINS = 5
LOCK_SECONDS = 15 * 60
TARGET_TIMEOUT = 60
# Backup-Server: nach einem Ausfall des Hauptservers gehen neue Mails so lange direkt ueber den
# Backup-Server (danach wird der Hauptserver wieder versucht); Wechsel nach einem Fehler sofort.
BACKUP_SKIP_SECONDS = int(os.environ.get("RELAY_BACKUP_SKIP", "600"))
FAILOVER_DELAY = int(os.environ.get("RELAY_FAILOVER_DELAY", "2"))
IDLE_TIMEOUT = 60
DIAG_REFERENCE_HOST = os.environ.get("RELAY_DIAG_HOST", "smtp.gmail.com")

# Der Standard erlaubt 1000 Zeichen pro Zeile. Manche Kameras halten sich nicht
# daran, deshalb wird grosszuegiger angenommen (die Mail geht trotzdem 1:1 weiter).
SMTP.line_length_limit = 64 * 1024

DEFAULTS = {
    # Funktionen
    "photo_storage": True,          # Fotos auf dem Share ablegen (aus: nur nicht weitergeleitete Fotos bleiben erhalten)
    "forward_enabled": True,        # Fotos an das Ziel weiterleiten
    "detect_enabled": True,         # Bilderkennung (Tier / Mensch / Fahrzeug / leer)
    "detect_only_forward": False,   # nur Fotos mit Erkennung weiterleiten
    # Relay am Home Assistant
    "relay_port": 0,   # Pflichtfeld, kein Standard
    "relay_user": "",
    "relay_password": "",
    # Ziel-SMTP-Server
    "target_host": "smtp.revierspion.de",
    "target_user": "revierspion",
    "target_password": "",
    "target_port": 25,
    # Optionale Einstellungen
    "allowed_domains": "",
    "require_tls": False,
    "tls_compat": True,
    "certfile": "/ssl/fullchain.pem",
    "keyfile": "/ssl/privkey.pem",
    "target_ssl": None,  # None = automatisch (an bei Port 465)
    "target_starttls": False,
    "target_sender": "",
    # Backup-SMTP-Server (optional): wird bei einem Fehler im naechsten Versuch verwendet
    "backup_host": "",
    "backup_port": 587,
    "backup_user": "",
    "backup_password": "",
    "backup_sender": "",
    "max_message_mb": 50,
    # Zustellung
    "throttle_minutes": 1,
    "send_delay_seconds": 3,
    "retry_minutes": 30,
    "retention_days": 180,
    "storage_mb": 1024,
    # Bilderkennung (lokal, MegaDetector V6): markiert Mensch / Tier / Fahrzeug / leer
    "detect_threads": 1,
    "detect_threshold_tier": 25,
    "detect_threshold_mensch": 30,
    "detect_threshold_fahrzeug": 30,
}

# Gueltige Bereiche der Zahlenwerte (Minimum, Maximum)
LIMITS = {
    "throttle_minutes": (0, 1440),
    "send_delay_seconds": (0, 300),
    "retry_minutes": (0, 1440),
    "retention_days": (1, 3650),
    "storage_mb": (50, 1000000),
    "backup_port": (1, 65535),
    "detect_threads": (1, 4),
    "detect_threshold_tier": (10, 95),
    "detect_threshold_mensch": (10, 95),
    "detect_threshold_fahrzeug": (10, 95),
}
HOLD_MAX_SECONDS = 120   # laenger wartet keine Mail auf die Bilderkennung

# Optionale Vorbelegung der Kameratabelle (Adresse, Klarname, Revier) aus einer JSON-Datei,
# z. B. /share/kamera_relay/kameras_vorbelegung.json mit [["adresse@domain", "Klarname", "Revier"], ...].
# Wird nur angelegt, wenn die Adresse noch nicht in der Kameratabelle steht - Aenderungen im Board bleiben erhalten.
PHOTOS_DIR = os.environ.get("RELAY_PHOTOS_DIR", "/share/kamera_relay")
CAMERAS_FILE = os.environ.get("RELAY_CAMERAS_FILE", "/share/kamera_relay/kameras_vorbelegung.json")


def load_seed_cameras(path=None):
    path = path or CAMERAS_FILE
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return [(str(a), str(n), str(r)) for a, n, r in data]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError) as exc:
        log.warning("Kamera-Vorbelegung %s nicht lesbar: %r", path, exc)
        return []


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("relay")


class _NoTlsSetupNoise(logging.Filter):
    """Ein gescheiterter STARTTLS-Aufbau wird in einer eigenen Zeile gemeldet."""

    def filter(self, record):
        exc = record.exc_info[1] if record.exc_info else None
        return not isinstance(exc, TLSSetupException)


_smtp_log = logging.getLogger("mail.log")
_smtp_log.setLevel(logging.ERROR)
_smtp_log.addFilter(_NoTlsSetupNoise())


def default_domain(host):
    """Leitet die erlaubte Empfaenger-Domain aus dem Zielserver ab."""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    parts = host.strip(".").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as fh:
            cfg.update({k: v for k, v in json.load(fh).items() if v is not None})
    except FileNotFoundError:
        log.error("Konfiguration %s nicht gefunden", OPTIONS_PATH)
        sys.exit(1)
    cfg["relay_user"] = str(cfg["relay_user"]).strip().lower()
    cfg["target_host"] = str(cfg["target_host"]).strip()
    cfg["backup_host"] = str(cfg["backup_host"]).strip()
    cfg["backup_user"] = str(cfg["backup_user"]).strip()
    cfg["backup_sender"] = str(cfg["backup_sender"]).strip()
    domains = [d.strip().lower().lstrip("@") for d in str(cfg["allowed_domains"]).split(",")]
    domains = [d for d in domains if d]
    if not domains and default_domain(cfg["target_host"]):
        domains = [default_domain(cfg["target_host"])]
    cfg["allowed_domains"] = domains

    for key, (low, high) in LIMITS.items():
        try:
            value = int(cfg[key])
        except (TypeError, ValueError):
            log.warning("Einstellung %s ist keine Zahl - Standard %s wird verwendet", key, DEFAULTS[key])
            value = DEFAULTS[key]
        if not low <= value <= high:
            clamped = min(max(value, low), high)
            log.warning("Einstellung %s=%s liegt ausserhalb von %s..%s - verwende %s", key, value, low, high, clamped)
            value = clamped
        cfg[key] = value

    for key in ("photo_storage", "forward_enabled", "detect_enabled", "detect_only_forward"):
        value = cfg[key]
        cfg[key] = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "ja", "yes", "an", "on")
    # intern: "an" = nur Fotos mit Erkennung weiterleiten (haelt Mails bis zur Entscheidung zurueck)
    cfg["detect_filter"] = "an" if (cfg["detect_only_forward"] and cfg["detect_enabled"] and cfg["forward_enabled"]) else "aus"
    problems = []
    try:
        if not 1 <= int(cfg["relay_port"]) <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("Relay-Port fehlt (frei waehlbar, z. B. 20023)")
    if not cfg["relay_user"]:
        problems.append("Relay-Benutzer fehlt")
    if not cfg["relay_password"]:
        problems.append("Relay-Passwort fehlt")
    if not cfg["target_host"]:
        problems.append("Ziel-SMTP-Server fehlt")
    if not cfg["allowed_domains"]:
        problems.append("erlaubte Empfaenger-Domains (allowed_domains) fehlen")
    if problems:
        for p in problems:
            log.error("Konfiguration unvollstaendig: %s", p)
        sys.exit(1)
    if cfg["backup_host"] and cfg["backup_user"] and not cfg["backup_password"]:
        log.warning("Backup-Server: Benutzer ist gesetzt, aber das Passwort fehlt - Anmeldung wird scheitern")
    if (cfg["backup_user"] or cfg["backup_password"]) and not cfg["backup_host"]:
        log.warning("Backup-Server: Benutzer/Passwort sind gesetzt, aber der Server fehlt - Backup ist aus")
    if cfg["backup_host"] and not (cfg["backup_sender"] or cfg["target_sender"]):
        log.warning("Backup-Server: weder 'Optional: Backup-SMTP: Absender' noch 'Ziel: Absender' ist gesetzt - "
                    "es wuerde die Kamera-Adresse als Absender verwendet, die meisten Anbieter (z. B. Brevo) lehnen das ab")
    if len(cfg["relay_password"]) < 12:
        log.warning("Relay-Passwort ist kurz - empfohlen sind mindestens 12 Zeichen")
    return cfg


class Authenticator:
    """Prueft Benutzer und Passwort der Kameras (ein Login fuer alle)."""

    def __init__(self, user, password):
        self._user = user.encode()
        self._password = password.encode()
        self._failed = {}
        self._lock = threading.Lock()

    @staticmethod
    def _is_public(ip):
        try:
            return ipaddress.ip_address(ip).is_global
        except ValueError:
            return False

    def _is_locked(self, ip, now):
        with self._lock:
            recent = [t for t in self._failed.get(ip, []) if now - t < LOCK_SECONDS]
            if recent:
                self._failed[ip] = recent
            else:
                self._failed.pop(ip, None)
            return len(recent) >= MAX_FAILED_LOGINS

    def _note_failure(self, ip, now):
        with self._lock:
            self._failed.setdefault(ip, []).append(now)

    def __call__(self, server, session, envelope, mechanism, auth_data):
        ip = session.peer[0] if session.peer else "?"
        now = time.monotonic()
        public = self._is_public(ip)
        if public and self._is_locked(ip, now):
            log.warning("Login abgelehnt: %s ist nach Fehlversuchen gesperrt", ip)
            note_session(session, "locked", "zu viele falsche Logins - Adresse zeitweise gesperrt", known=False)
            return AuthResult(success=False, handled=False)
        ok = False
        if isinstance(auth_data, LoginPassword):
            login = auth_data.login.decode("utf-8", "replace").strip().lower().encode()
            user_ok = hmac.compare_digest(login, self._user)
            pass_ok = hmac.compare_digest(auth_data.password, self._password)
            ok = user_ok and pass_ok
        if not ok:
            if public:
                self._note_failure(ip, now)
            session.relay_stage = "Login fehlgeschlagen"
            log.warning("Login fehlgeschlagen von %s (Benutzer oder Passwort falsch)", ip)
            if not getattr(session, "relay_login_failed", False):  # smtplib probiert mehrere Verfahren
                session.relay_login_failed = True
                note_session(session, "login_fail", "Benutzer oder Passwort falsch")
            return AuthResult(success=False, handled=False)
        transport = getattr(server, "transport", None)
        encrypted = bool(transport and transport.get_extra_info("ssl_object"))
        session.relay_logged_in = True
        session.relay_stage = "Login ok, noch keine Mail gesendet"
        log.info("Login ok von %s (%s)%s", ip, "verschluesselt" if encrypted else "unverschluesselt", who_ip(ip))
        return AuthResult(success=True)


def peer_ip(session):
    return session.peer[0] if session is not None and session.peer else "?"


NAME_STORE = [None]  # wird beim Start gesetzt; dient nur der Anzeige von Kameranamen im Protokoll


def title_of(row, address):
    name = ((row["name"] if row else "") or "").strip()
    revier = ((row["revier"] if row else "") or "").strip()
    title = name if (not revier or revier == name) else ("%s %s" % (revier, name) if name else revier)
    return "%s (%s)" % (title, address) if title else address


def note_session(session, outcome, detail="", camera=None, ip=None, mail_id=None, size=0,
                 n_images=0, n_videos=0, subject="", known=None):
    """Haelt eine Verbindung fuer den Reiter 'Auswertung' fest (nie ein Fehler fuer die Mail selbst)."""
    store = NAME_STORE[0]
    if store is None:
        return
    try:
        ip = ip or peer_ip(session)
        camera = camera or getattr(session, "relay_camera", None)
        if known is None:
            known = bool(camera) or bool(store.cameras_by_ip(ip))
        started = getattr(session, "relay_started", None) or time.time()
        enc = getattr(session, "relay_enc", "") or ""
        store.add_session(started, ip, camera, enc, outcome, detail, mail_id, size, n_images, n_videos,
                          subject, max(0.0, time.time() - started), known)
        if session is not None:
            session.relay_recorded = True
    except Exception as exc:  # die Auswertung darf nie den Mailempfang stoeren
        log.debug("Auswertung: Eintrag fehlgeschlagen: %r", exc)


def who_ip(ip):
    """Beim Verbindungsaufbau ist die Kamera noch nicht bekannt (die Adresse kommt erst mit RCPT TO).
    Als Hinweis dient die Kamera, die zuletzt von dieser IP-Adresse gesendet hat."""
    store = NAME_STORE[0]
    if store is None or not ip or ip == "?":
        return ""
    try:
        cams = store.cameras_by_ip(ip)
        if not cams:
            return ""
        known = store.camera_map()
        names = [title_of(known.get(c), c) for c in cams]
    except Exception:
        return ""
    return " - vermutlich %s" % (names[0] if len(names) == 1 else "eine von: " + ", ".join(names))


class RelaySMTP(SMTP):
    """SMTP-Sitzung, die fuer die Fehlersuche jeden Schritt der Kamera festhaelt."""

    def connection_made(self, transport):
        super().connection_made(transport)
        session = self.session
        if session is not None:
            session.relay_started = time.time()
            session.relay_enc = "SSL/TLS" if transport.get_extra_info("ssl_object") else "Klartext"
            session.relay_mails = 0

    async def smtp_MAIL(self, arg):
        self.session.relay_stage = "Absender uebermittelt (MAIL FROM)"
        return await super().smtp_MAIL(arg)

    async def smtp_RCPT(self, arg):
        self.session.relay_stage = "Empfaenger uebermittelt (RCPT TO)"
        return await super().smtp_RCPT(arg)

    async def smtp_DATA(self, arg):
        self.session.relay_stage = "Datenuebertragung (DATA) - Mail nicht vollstaendig angekommen"
        return await super().smtp_DATA(arg)

    async def smtp_STARTTLS(self, arg):
        self.session.relay_stage = "STARTTLS angefordert"
        log.info("%s fordert STARTTLS an%s", peer_ip(self.session), who_ip(peer_ip(self.session)))
        try:
            await super().smtp_STARTTLS(arg)
        except TLSSetupException as exc:
            self.session.relay_stage = "STARTTLS fehlgeschlagen: %s" % (exc.__cause__ or "unbekannt")
            raise

    async def smtp_AUTH(self, arg):
        self.session.relay_stage = "Login begonnen"
        await super().smtp_AUTH(arg)

    def connection_lost(self, error):
        session = self.session
        if session is not None and not getattr(session, "relay_recorded", False) and not getattr(session, "relay_mails", 0):
            stage = getattr(session, "relay_stage", "verbunden, noch kein Befehl")
            if stage.startswith("STARTTLS fehlgeschlagen"):
                note_session(session, "starttls_fail", stage)
            elif not getattr(session, "relay_logged_in", False):
                if stage not in ("verbunden, noch kein Befehl",) or who_ip(peer_ip(session)):
                    note_session(session, "no_login", stage + (" (%s)" % error if error and str(error) else ""))
            else:
                note_session(session, "no_mail", stage + (" (%s)" % error if error and str(error) else ""))
                log.info("Verbindung von %s nach dem Login beendet, ohne dass eine Mail angekommen ist "
                         "(letzter Schritt: %s)%s", peer_ip(session), stage, who_ip(peer_ip(session)))
        if session is not None and not getattr(session, "relay_logged_in", False):
            log.info(
                "Verbindung von %s beendet ohne Login (letzter Schritt: %s)%s%s",
                peer_ip(session),
                getattr(session, "relay_stage", "verbunden, noch kein Befehl"),
                ", Fehler: %s" % error if error and str(error) else "",
                who_ip(peer_ip(session)),
            )
        super().connection_lost(error)


def fmt_dur(seconds):
    seconds = int(max(0, round(seconds)))
    if seconds < 90:
        return "%d Sek" % seconds
    if seconds < 5400:
        return "%d Min" % round(seconds / 60)
    return "%.1f Std" % (seconds / 3600)


class RelayHandler:
    """Nimmt Mails an, speichert sie und uebergibt sie der Zustellung."""

    def __init__(self, cfg, store, wake, detector=None):
        self.cfg = cfg
        self.detector = detector
        self.store = store
        self.wake = wake
        self.allowed = set(cfg["allowed_domains"])

    async def handle_EHLO(self, server, session, envelope, hostname, responses):
        session.host_name = hostname
        session.relay_stage = "Begruessung (EHLO)"
        return responses

    async def handle_HELO(self, server, session, envelope, hostname):
        session.host_name = hostname
        session.relay_stage = "Begruessung (HELO)"
        return "250 %s" % server.hostname

    def handle_STARTTLS(self, server, session, envelope):
        session.relay_stage = "STARTTLS ok"
        session.relay_enc = "STARTTLS"
        log.info("STARTTLS mit %s erfolgreich%s", peer_ip(session), who_ip(peer_ip(session)))
        return True

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        domain = address.rsplit("@", 1)[1].lower() if "@" in address else ""
        if domain not in self.allowed:
            log.warning("Empfaenger abgelehnt (Domain nicht erlaubt): %s", address)
            note_session(session, "rcpt_denied", "Empfaenger-Domain nicht erlaubt: %s" % address, camera=address, known=True)
            return "550 5.7.1 Empfaenger nicht erlaubt"
        session.relay_camera = address.strip().lower()
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        peer = peer_ip(session)
        rcpts = list(envelope.rcpt_tos)
        content = envelope.original_content or envelope.content
        window = int(self.cfg["throttle_minutes"]) * 60
        loop = asyncio.get_running_loop()
        zoom_dropped = 0
        if rcpts:   # nur das erste Foto zaehlt, weitere (z. B. Zoom-Bild der PTZ-Kamera) werden verworfen
            def _only_first(raw):
                total = 0
                for _ in range(10):
                    raw, removed = drop_second_image(raw)
                    if not removed:
                        break
                    total += removed
                return raw, total
            content, zoom_dropped = await loop.run_in_executor(None, _only_first, content)
        try:
            hold = bool(self.detector and self.detector.ok and self.cfg["detect_filter"] == "an")
            forward = bool(self.cfg["forward_enabled"])
            result = await loop.run_in_executor(
                None, lambda: self.store.accept(peer, envelope.mail_from, rcpts, content, window, hold=hold, forward=forward)
            )
        except Exception as exc:
            log.error("Mail von %s konnte nicht gespeichert werden: %r", peer, exc)
            return "451 4.3.0 Speichern fehlgeschlagen, bitte spaeter erneut senden"

        mail_id = result["id"]
        info = result["info"]
        if self.detector and info["n_images"]:
            self.detector.submit(mail_id)
        session.relay_mails = getattr(session, "relay_mails", 0) + 1
        attach = info["n_images"] + info["n_videos"]
        note_session(
            session, "ok_mail" if attach else "ok_test",
            ("Weiterleitung aus (gespeichert, nicht weitergeleitet)" if result["stored"] else
             "gedrosselt (gespeichert, nicht weitergeleitet)") if result["throttled"] else "",
            camera=result["camera"] or None, mail_id=mail_id, size=len(content),
            n_images=info["n_images"], n_videos=info["n_videos"], subject=info.get("subject", ""), known=True,
        )
        session.relay_recorded = False  # weitere Mails derselben Verbindung werden einzeln erfasst
        what = []
        if info["n_images"]:
            what.append("%d Foto(s)" % info["n_images"])
        if info["n_videos"]:
            what.append("%d Video(s)" % info["n_videos"])
        log.info(
            "[#%d] angenommen von %s: Kamera %s, %.0f kB%s",
            mail_id,
            peer,
            cam_names(self.store, rcpts),
            len(content) / 1024,
            " (%s)" % ", ".join(what) if what else " - OHNE FOTO/VIDEO im Anhang",
        )
        if zoom_dropped:
            log.info(
                "[#%d] weitere Fotos der Mail (%.0f kB) entfernt - es wird nur das erste verwendet",
                mail_id, zoom_dropped / 1024,
            )
        for dropped in result["discarded"]:
            log.warning(
                "[#%d] Speicherlimit (%s MB) erreicht - aeltestes Foto geloescht",
                dropped,
                self.cfg["storage_mb"],
            )
        cam_row = self.store.camera_map().get(result["camera"]) if result["camera"] else None
        if result["new_camera"] and cam_row is not None and not cam_row["name"] and not cam_row["revier"]:
            log.info("Neue Kamera erkannt: %s - bitte im Board unter 'Kameras' benennen", result["camera"])
        if result["throttled"] and not result["stored"]:
            log.info(
                "[#%d] GEDROSSELT: letzte weitergeleitete Mail dieser Kamera vor %s "
                "(Limit %d Min) - gespeichert, nicht weitergeleitet (im Board erneut senden)",
                mail_id,
                fmt_dur(result["since"]),
                self.cfg["throttle_minutes"],
            )
        elif result["status"] == "held":
            log.info("[#%d] wartet auf die Bilderkennung (Filter an), danach Zustellung an %s:%s", mail_id,
                     self.cfg["target_host"], self.cfg["target_port"])
        else:
            log.info("[#%d] in der Warteschlange, Zustellung an %s:%s", mail_id,
                     self.cfg["target_host"], self.cfg["target_port"])
        self.wake()
        return "250 OK"


_warned_unverified = False


def _unverified_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _warn_unverified(host, exc):
    global _warned_unverified
    if not _warned_unverified:
        _warned_unverified = True
        log.warning(
            "Zertifikat von %s ist nicht pruefbar (%s) - Verbindung wird trotzdem "
            "verschluesselt aufgebaut",
            host,
            getattr(exc, "verify_message", None) or exc,
        )


def target_uses_ssl(cfg):
    """SSL/TLS von Beginn an: ausdruecklich gesetzt oder automatisch bei Port 465."""
    if cfg["target_ssl"] is None:
        return int(cfg["target_port"]) == 465
    return bool(cfg["target_ssl"])


def open_target(cfg):
    """Baut die Verbindung zum Zielserver auf und meldet sich an."""
    host, port = cfg["target_host"], int(cfg["target_port"])
    if target_uses_ssl(cfg):
        try:
            target = smtplib.SMTP_SSL(
                host, port, timeout=TARGET_TIMEOUT, context=ssl.create_default_context()
            )
        except ssl.SSLCertVerificationError as exc:
            _warn_unverified(host, exc)
            target = smtplib.SMTP_SSL(
                host, port, timeout=TARGET_TIMEOUT, context=_unverified_context()
            )
    else:
        target = smtplib.SMTP(host, port, timeout=TARGET_TIMEOUT)
    try:
        target.ehlo()
        if not target_uses_ssl(cfg) and (
            cfg["target_starttls"]
            or (target.has_extn("starttls") and (cfg.get("_auto_starttls") or port in (587, 2525)))
        ):
            try:
                target.starttls(context=ssl.create_default_context())
            except ssl.SSLCertVerificationError as exc:   # groesste Kompatibilitaet: Zertifikatsfehler nicht fatal
                _warn_unverified(host, exc)
                target.close()
                target = smtplib.SMTP(host, port, timeout=TARGET_TIMEOUT)
                target.ehlo()
                target.starttls(context=_unverified_context())
            target.ehlo()
        if cfg["target_user"]:
            target.login(cfg["target_user"], cfg["target_password"])
    except Exception:
        target.close()
        raise
    return target


def backup_enabled(cfg):
    return bool(cfg.get("backup_host"))


def provider_cfg(cfg, via):
    """Einstellungen fuer den Hauptserver ('primary') oder den Backup-Server ('backup')."""
    if via != "backup":
        return cfg
    return dict(
        cfg,
        target_host=cfg["backup_host"],
        target_port=cfg["backup_port"],
        target_user=cfg["backup_user"],
        target_password=cfg["backup_password"],
        target_ssl=None,  # automatisch: SSL/TLS bei Port 465
        target_starttls=False,
        _auto_starttls=True,  # sonst STARTTLS, sobald der Server es anbietet (Port 587)
        # Leer = derselbe Absender wie beim Hauptserver ("Ziel: Absender")
        target_sender=cfg["backup_sender"] or cfg["target_sender"],
    )


def cam_names(store, rcpts):
    """Empfaenger fuers Protokoll: 'Revier Klarname (adresse)', ohne Eintrag nur die Adresse."""
    known = store.camera_map()
    out = []
    for address in rcpts:
        out.append(title_of(known.get((address or "").strip().lower()), address))
    return ", ".join(out)


def rewrite_from(content, new_sender):
    """Ersetzt den From-Kopf einer Mail (nur den Kopf, der Rest bleibt Byte fuer Byte gleich).

    Manche Anbieter (z. B. GMX) lehnen Mails ab, deren From-Kopf nicht zum
    angemeldeten Konto passt. Die urspruengliche Adresse bleibt als Reply-To
    und X-Original-From erhalten. Gibt (neuer Inhalt, urspruengliche Adresse) zurueck.
    """
    match = re.search(rb"\r?\n\r?\n", content)
    end = match.start() if match else len(content)
    head, rest = content[:end], content[end:]
    nl = b"\r\n" if b"\r\n" in head else b"\n"
    found = re.search(rb"(?im)^from:[^\r\n]*(?:\r?\n[ \t][^\r\n]*)*", head)
    if not found:
        return content, ""
    original = found.group(0)[5:].replace(b"\r", b"").replace(b"\n", b" ").strip()
    if original.lower().find(new_sender.lower().encode("utf-8", "ignore")) >= 0:
        return content, ""
    extra = b"From: " + new_sender.encode("utf-8", "replace")
    if not re.search(rb"(?im)^reply-to:", head):
        extra += nl + b"Reply-To: " + original
    extra += nl + b"X-Original-From: " + original
    head = head[: found.start()] + extra + head[found.end() :]
    return head + rest, original.decode("utf-8", "replace")


def explain(exc):
    """Uebersetzt einen Fehler in Klartext.

    Liefert (Text, endgueltig, Verbindungsproblem). "Endgueltig" heisst: Eine
    Wiederholung hilft nicht. "Verbindungsproblem" heisst: Das Ziel als Ganzes ist
    gerade nicht nutzbar, die Zustellung pausiert dann kurz.
    """
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in exc.recipients.values()]
        text = "Empfaenger vom Ziel abgelehnt (%s)" % "; ".join(
            "%s: %s %s" % (addr, code, msg.decode("utf-8", "replace") if isinstance(msg, bytes) else msg)
            for addr, (code, msg) in exc.recipients.items()
        )
        return text, all(code >= 500 for code in codes), False
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "Anmeldung am Ziel abgelehnt (%s) - Ziel-Benutzer und Ziel-Passwort pruefen"
            % exc.smtp_code,
            False,
            True,
        )
    if isinstance(exc, smtplib.SMTPSenderRefused):
        text = (
            "Absender vom Ziel abgelehnt (%s %s) - 'Absender gegenueber dem Ziel' pruefen"
            % (exc.smtp_code, exc.smtp_error.decode("utf-8", "replace"))
        )
        return text, False, True
    if isinstance(exc, smtplib.SMTPConnectError):
        return "Zielserver weist die Verbindung ab (%s)" % exc.smtp_code, False, True
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "Zielserver hat die Verbindung beendet", False, True
    if isinstance(exc, smtplib.SMTPResponseException):
        detail = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else exc.smtp_error
        if exc.smtp_code == 552:
            return "Mail ist fuer das Ziel zu gross (%s)" % detail, True, False
        text = "Ziel antwortet mit Fehler %s %s" % (exc.smtp_code, " ".join(detail.split()))
        if "sender" in detail.lower() or "absender" in detail.lower():
            # Einstellungsfehler, der alle Mails betrifft: wiederholen und pausieren
            return text + " - 'Absender gegenueber dem Ziel' pruefen", False, True
        return text, exc.smtp_code >= 500, False
    if isinstance(exc, ssl.SSLError):
        return "SSL/TLS-Fehler zum Ziel (%s)" % (getattr(exc, "reason", None) or exc), False, True
    if isinstance(exc, ConnectionRefusedError):
        return (
            "Verbindung abgelehnt (das Ziel nimmt auf diesem Port keine Verbindung an)",
            False,
            True,
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return (
            "Zeitueberschreitung (keine Antwort vom Ziel - Port evtl. vom Anbieter gesperrt)",
            False,
            True,
        )
    if isinstance(exc, socket.gaierror):
        return "Name des Zielservers nicht aufloesbar", False, True
    if isinstance(exc, OSError):
        return "Netzwerkfehler (%s)" % (exc.strerror or exc), False, True
    if isinstance(exc, smtplib.SMTPException):
        return "SMTP-Fehler (%s)" % exc, False, True
    return "Interner Fehler (%r)" % exc, True, False


class Deliverer(threading.Thread):
    """Stellt die gespeicherten Mails nacheinander an den Zielserver zu."""

    def __init__(self, cfg, store, runtime):
        super().__init__(name="deliverer", daemon=True)
        self.cfg = cfg
        self.store = store
        self.runtime = runtime
        self.stop_event = threading.Event()
        self._wake = threading.Event()

    def wake(self):
        self._wake.set()

    def stop(self):
        self.stop_event.set()
        self._wake.set()

    def run(self):
        while not self.stop_event.is_set():
            now = time.time()
            if now < self.runtime.pause_until:
                self.stop_event.wait(min(1.0, self.runtime.pause_until - now))
                continue
            row = self.store.next_due(now)
            if row is None:
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            try:
                self.deliver(row)
            except Exception as exc:  # darf die Zustellung nie beenden
                log.error("[#%d] Interner Fehler bei der Zustellung: %r", row["id"], exc)
                self.store.mark_failed(row["id"], row["attempts"] + 1, "Interner Fehler: %r" % exc, time.time())

    def deliver(self, row):
        cfg = self.cfg
        mail_id = row["id"]
        attempt = row["attempts"] + 1
        content = self.store.read(mail_id)
        if content is None:
            self.store.mark_failed(mail_id, row["attempts"], "Mail-Datei fehlt", time.time())
            log.error("[#%d] NICHT WEITERGELEITET: Mail-Datei fehlt", mail_id)
            return
        rcpts = json.loads(row["rcpts"])
        via = self.choose_via(row)
        pcfg = provider_cfg(cfg, via)
        where = " (Backup-Server)" if via == "backup" else ""
        sender = pcfg["target_sender"] or row["mail_from"]
        if pcfg["target_sender"]:
            content, original = rewrite_from(content, pcfg["target_sender"])
            if original:
                log.info("[#%d] Absender im Mailkopf von %s auf %s umgeschrieben", mail_id, original, sender)
        self.store.mark_sending(mail_id, via)
        log.info(
            "[#%d] Zustellversuch %d an %s:%s%s (%.0f kB)",
            mail_id,
            attempt,
            pcfg["target_host"],
            pcfg["target_port"],
            where,
            len(content) / 1024,
        )
        started = time.monotonic()
        try:
            target = open_target(pcfg)
            try:
                refused = target.sendmail(sender, rcpts, content)
            finally:
                # Ein Fehler beim Abmelden darf eine zugestellte Mail nicht erneut senden lassen.
                try:
                    target.quit()
                except Exception:
                    target.close()
        except Exception as exc:
            self._failed(row, attempt, exc, via)
            return
        now = time.time()
        self.store.mark_sent(mail_id, attempt, now)
        self.runtime.last_success = now
        if via == "primary" or not backup_enabled(cfg):
            self.runtime.primary_skip_until = 0.0
        log.info(
            "[#%d] WEITERGELEITET an %s%s (%.0f kB, %.1f s, Versuch %d)",
            mail_id,
            cam_names(self.store, rcpts),
            " ueber den Backup-Server" if via == "backup" else "",
            len(content) / 1024,
            time.monotonic() - started,
            attempt,
        )
        if refused:
            log.warning("[#%d] Ziel hat einzelne Empfaenger abgelehnt: %s", mail_id, refused)
        delay = int(cfg["send_delay_seconds"])
        if delay > 0:
            log.info("[#%d] Wartezeit %d s bis zur naechsten Mail (Einstellung)", mail_id, delay)
            self.stop_event.wait(delay)

    def choose_via(self, row):
        """Welcher Server ist dran: nach einem Fehler immer der jeweils andere."""
        if not backup_enabled(self.cfg):
            return "primary"
        if (row["via"] or "") == "primary":
            return "backup"
        if self.runtime.primary_skip_until > time.time():
            return "backup"
        return "primary"

    def _failed(self, row, attempt, exc, via="primary"):
        cfg = self.cfg
        mail_id = row["id"]
        text, permanent, connection = explain(exc)
        has_backup = backup_enabled(cfg)
        if has_backup:
            text = "%s: %s" % ("Backup-Server" if via == "backup" else "Hauptserver", text)
        now = time.time()
        self.runtime.last_error = text
        self.runtime.last_error_time = now
        if has_backup and via == "primary":
            # Fehler beim Hauptserver: der naechste Versuch geht sofort an den Backup-Server.
            if connection:
                self.runtime.primary_skip_until = now + BACKUP_SKIP_SECONDS
            self.store.mark_retry(mail_id, attempt, now + FAILOVER_DELAY, text)
            log.warning(
                "[#%d] NICHT WEITERGELEITET (Versuch %d): %s - naechster Versuch sofort ueber den Backup-Server",
                mail_id, attempt, text,
            )
            if connection:
                log.warning(
                    "Hauptserver nicht nutzbar - neue Mails gehen die naechsten %s direkt ueber den Backup-Server",
                    fmt_dur(BACKUP_SKIP_SECONDS),
                )
            return
        if has_backup:
            # Auch das Backup scheitert: der Hauptserver soll beim naechsten Versuch wieder drankommen.
            self.runtime.primary_skip_until = 0.0
        if permanent:
            self.store.mark_failed(mail_id, attempt, text, now)
            log.error(
                "[#%d] NICHT WEITERGELEITET (endgueltig abgelehnt): %s - im Board erneut senden",
                mail_id,
                text,
            )
            return
        deadline = (row["queued"] or row["received"]) + int(cfg["retry_minutes"]) * 60
        step = (attempt - 1) // 2 if has_backup else attempt - 1
        delay = RETRY_STEPS[min(step, len(RETRY_STEPS) - 1)]
        if now + delay > deadline:
            reason = "nach %d Versuch%s in %d Min nicht zustellbar: %s" % (
                attempt,
                "" if attempt == 1 else "en",
                int(cfg["retry_minutes"]),
                text,
            )
            self.store.mark_failed(mail_id, attempt, reason, now)
            log.error(
                "[#%d] ENDGUELTIG GESCHEITERT, %s - im Board erneut senden", mail_id, reason
            )
            return
        next_attempt = now + delay
        self.store.mark_retry(mail_id, attempt, next_attempt, text)
        log.warning(
            "[#%d] NICHT WEITERGELEITET (Versuch %d): %s - naechster Versuch in %s",
            mail_id,
            attempt,
            text,
            fmt_dur(delay),
        )
        if connection:
            # Das Ziel ist als Ganzes nicht nutzbar: alle Mails warten bis zum naechsten Versuch.
            self.runtime.pause_until = next_attempt


def day_bounds(ymd):
    start = time.mktime(time.strptime(ymd, "%Y-%m-%d"))
    return start, start + 86400


class Maintenance(threading.Thread):
    """Raeumt alte Mails auf und schreibt jeden Tag einen Bericht ins Protokoll."""

    def __init__(self, store, wake=None):
        super().__init__(name="maintenance", daemon=True)
        self.wake = wake
        self.store = store
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        day = time.strftime("%Y-%m-%d")
        last_purge = 0.0
        while not self.stop_event.wait(30):
            now = time.time()
            try:   # Sicherheitsnetz: zu lange auf die Erkennung wartende Mails werden weitergeleitet
                stale = self.store.release_stale_held(HOLD_MAX_SECONDS, now)
                if stale:
                    log.warning("%d Mail(s) warteten zu lange auf die Bilderkennung - werden weitergeleitet", stale)
                    if self.wake:
                        self.wake()
            except Exception as exc:
                log.error("Pruefung wartender Mails fehlgeschlagen: %r", exc)
            if now - last_purge >= 3600:
                last_purge = now
                try:
                    removed, discarded = self.store.purge(now)
                    if removed:
                        log.info("Aufraeumen: %d alte(s) Foto(s) geloescht", removed)
                    for mail_id in discarded:
                        log.warning("[#%d] Speicherlimit erreicht - aeltestes Foto geloescht", mail_id)
                except Exception as exc:
                    log.error("Aufraeumen fehlgeschlagen: %r", exc)
            today = time.strftime("%Y-%m-%d")
            if today != day:
                try:
                    start, end = day_bounds(day)
                    c = self.store.summary(start, end)
                    log.info(
                        "Tagesbericht %s: %d Mails angenommen - %d zugestellt, %d gedrosselt, "
                        "%d gescheitert, %d wartend",
                        day,
                        sum(c.values()),
                        c.get("sent", 0),
                        c.get("throttled", 0),
                        c.get("failed", 0) + c.get("discarded", 0),
                        c.get("queued", 0) + c.get("sending", 0),
                    )
                except Exception as exc:
                    log.error("Tagesbericht fehlgeschlagen: %r", exc)
                day = today


def check_target(cfg):
    """Einmaliger Test beim Start: Ist das Ziel erreichbar, klappt die Anmeldung?"""
    name = "%s:%s" % (cfg["target_host"], cfg["target_port"])
    try:
        with open_target(cfg):
            pass
        log.info(
            "Zieltest OK: %s erreichbar%s, Anmeldung erfolgreich",
            name,
            " (SSL/TLS)" if target_uses_ssl(cfg) else "",
        )
    except smtplib.SMTPAuthenticationError as exc:
        log.error("Zieltest: %s erreichbar, aber Anmeldung abgelehnt (%s)", name, exc.smtp_code)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Zieltest FEHLGESCHLAGEN: %s nicht erreichbar (%r)", name, exc)
        diagnose_target(cfg)


def check_backup(cfg):
    """Einmaliger Test des Backup-Servers beim Start (nur Meldung, keine Diagnose)."""
    pcfg = provider_cfg(cfg, "backup")
    name = "%s:%s" % (pcfg["target_host"], pcfg["target_port"])
    try:
        with open_target(pcfg):
            pass
        log.info("Backup-Zieltest OK: %s erreichbar, Anmeldung erfolgreich", name)
    except smtplib.SMTPAuthenticationError as exc:
        log.error("Backup-Zieltest: %s erreichbar, aber Anmeldung abgelehnt (%s)", name, exc.smtp_code)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Backup-Zieltest FEHLGESCHLAGEN: %s nicht erreichbar (%r)", name, exc)


def probe(host, port, timeout=5):
    """Prueft, ob ein Port erreichbar ist, und sagt bei Misserfolg warum nicht."""
    try:
        socket.create_connection((host, port), timeout).close()
        return "offen"
    except socket.gaierror:
        return "Name nicht aufloesbar"
    except ConnectionRefusedError:
        return "abgelehnt"
    except (TimeoutError, socket.timeout):
        return "keine Antwort"
    except OSError as exc:
        return "Fehler (%s)" % (exc.strerror or exc)


def diagnose_target(cfg):
    """Grenzt ein, warum das Ziel nicht erreichbar ist."""
    host, port = cfg["target_host"], int(cfg["target_port"])
    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
        log.info("Diagnose: %s wird aufgeloest zu %s", host, ", ".join(addresses))
    except OSError as exc:
        log.error("Diagnose: %s ist nicht aufloesbar (%s)", host, exc)
        return
    ports = sorted({25, 587, 465, 2525, port})
    states = [(p, probe(host, p)) for p in ports]
    log.info(
        "Diagnose: Ports an %s: %s", host, ", ".join("%s %s" % item for item in states)
    )
    reference = [(p, probe(DIAG_REFERENCE_HOST, p)) for p in sorted({25, 465, 587, port})]
    log.info(
        "Diagnose: zum Vergleich %s: %s",
        DIAG_REFERENCE_HOST,
        ", ".join("%s %s" % item for item in reference),
    )
    target_state = dict(states)[port]
    reference_state = dict(reference)[port]
    if target_state == "offen":
        log.error(
            "Diagnose-Ergebnis: Port %s an %s ist erreichbar, aber der Mail-Dialog scheitert "
            "(falsche Verschluesselungsart oder Anmeldedaten).",
            port,
            host,
        )
    elif reference_state != "offen":
        log.error(
            "Diagnose-Ergebnis: Port %s ist von diesem Anschluss aus generell nicht nutzbar "
            "(Sperre beim Internetanbieter oder am Router).",
            port,
        )
    else:
        log.error(
            "Diagnose-Ergebnis: Port %s ist von diesem Anschluss aus grundsaetzlich nutzbar, "
            "nur %s nimmt darauf keine Verbindung an.",
            port,
            host,
        )
    usable = [str(p) for p, state in states if state == "offen" and p != port]
    if usable:
        log.info(
            "Diagnose: %s antwortet auf Port %s - diesen als Ziel-Port versuchen.",
            host,
            " und ".join(usable),
        )


def make_tls_context(certfile, keyfile, compat=True):
    """TLS-Einstellungen fuer die Kameraseite.

    Mit compat (Standard) werden auch alte TLS-Versionen (ab 1.0) und aeltere Verfahren
    akzeptiert, wie sie 5-6 Jahre alte Kamera-Firmware noch spricht. Das ist immer noch
    besser als unverschluesselt. Zum Ziel-Server wird davon nichts beruehrt.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    if compat:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    else:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


class RelayServer:
    """Ein Port fuer alles: SSL/TLS, STARTTLS oder unverschluesselt."""

    def __init__(self, cfg, tls_ctx, store, runtime):
        self.cfg = cfg
        self.tls_ctx = tls_ctx
        self.require_tls = bool(cfg["require_tls"])
        self.store = store
        self.runtime = runtime
        self.deliverer = Deliverer(cfg, store, runtime)
        self.detector = None
        if cfg["detect_enabled"]:
            if detect.available():
                self.detector = detect.DetectWorker(store, {
                    "tier": cfg["detect_threshold_tier"] / 100.0,
                    "mensch": cfg["detect_threshold_mensch"] / 100.0,
                    "fahrzeug": cfg["detect_threshold_fahrzeug"] / 100.0},
                    threads=cfg["detect_threads"], filter_mode=cfg["detect_filter"], wake=self.deliverer.wake)
            else:
                log.warning("Bilderkennung aktiv, aber das Modell %s fehlt - Erkennung ist aus", detect.MODEL_PATH)
        self.maintenance = Maintenance(store, self.deliverer.wake)
        self.board = Board(
            cfg, store, runtime, self.deliverer.wake, BOARD_BIND, BOARD_PORT, BOARD_ALLOW,
            detector=self.detector,
        )
        self.handler = RelayHandler(cfg, store, self.deliverer.wake, self.detector)
        self.auth = Authenticator(cfg["relay_user"], cfg["relay_password"])
        self.loop = None
        self._tasks = set()

    def _protocol(self, encrypted):
        common = dict(
            authenticator=self.auth,
            auth_required=True,
            decode_data=False,
            data_size_limit=int(self.cfg["max_message_mb"]) * 1024 * 1024,
            ident="Kamera-SMTP-Relay",
            timeout=IDLE_TIMEOUT,
            loop=self.loop,
        )
        if encrypted:
            # Verbindung ist von Beginn an verschluesselt (SSL/TLS)
            return RelaySMTP(self.handler, auth_require_tls=False, **common)
        return RelaySMTP(
            self.handler,
            tls_context=self.tls_ctx,
            require_starttls=self.require_tls,
            auth_require_tls=self.require_tls,
            **common,
        )

    async def _first_byte(self, conn):
        """Wartet kurz, ob die Gegenseite von sich aus zu senden beginnt."""
        fut = self.loop.create_future()
        fd = conn.fileno()

        def readable():
            if not fut.done():
                fut.set_result(None)

        self.loop.add_reader(fd, readable)
        try:
            await asyncio.wait_for(fut, TLS_DETECT_SECONDS)
        except asyncio.TimeoutError:
            return b""
        finally:
            self.loop.remove_reader(fd)
        try:
            return conn.recv(1, socket.MSG_PEEK)
        except OSError:
            return b""

    async def _serve(self, conn, addr):
        ip = addr[0]
        started = time.time()
        try:
            first = await self._first_byte(conn)
            encrypted = first == b"\x16"  # Beginn eines TLS-Handshakes
            if encrypted and self.tls_ctx is None:
                log.warning("%s sendet mit SSL/TLS, aber es ist kein Zertifikat vorhanden", ip)
                note_session(type("S", (), {"relay_started": started, "relay_enc": "SSL/TLS"})(), "tls_fail",
                             "SSL/TLS-Handshake nicht moeglich: im Relay ist kein Zertifikat vorhanden", ip=ip)
                conn.close()
                return
            log.info("Verbindung von %s (%s)%s", ip, "SSL/TLS" if encrypted else "Klartext", who_ip(ip))
            if encrypted:
                await self.loop.connect_accepted_socket(
                    lambda: self._protocol(True),
                    sock=conn,
                    ssl=self.tls_ctx,
                    ssl_handshake_timeout=20,
                )
            else:
                await self.loop.connect_accepted_socket(lambda: self._protocol(False), sock=conn)
        except (OSError, ssl.SSLError, asyncio.TimeoutError) as exc:
            log.info("SSL/TLS-Aufbau mit %s fehlgeschlagen: %s%s", ip, exc or exc.__class__.__name__, who_ip(ip))
            fake = type("S", (), {"relay_started": started, "relay_enc": "SSL/TLS"})()
            note_session(fake, "tls_fail", "SSL/TLS-Handshake fehlgeschlagen: %s" % (exc or exc.__class__.__name__), ip=ip)
            try:
                conn.close()
            except OSError:
                pass

    async def _accept_loop(self, lsock):
        while True:
            conn, addr = await self.loop.sock_accept(lsock)
            # jeder Kontaktversuch wird festgehalten, auch wenn danach nichts mehr kommt (Portscan, Abbruch)
            log.info("Kontaktversuch auf Port %s von %s:%s%s", self.cfg["relay_port"], addr[0], addr[1], who_ip(addr[0]))
            task = self.loop.create_task(self._serve(conn, addr))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _watch_certificate(self):
        """Laedt ein erneuertes Zertifikat automatisch nach (Pruefung stuendlich)."""
        certfile, keyfile = self.cfg["certfile"], self.cfg["keyfile"]
        last = os.path.getmtime(certfile)
        while True:
            await asyncio.sleep(3600)
            try:
                current = os.path.getmtime(certfile)
                if current != last:
                    self.tls_ctx.load_cert_chain(certfile, keyfile)
                    last = current
                    log.info("Erneuertes Zertifikat geladen")
            except (OSError, ssl.SSLError) as exc:
                log.error("Zertifikat konnte nicht neu geladen werden: %r", exc)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        port = int(self.cfg["relay_port"])
        lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            lsock.bind(("0.0.0.0", port))
        except OSError as exc:
            log.error("Port %s kann nicht geoeffnet werden: %s", port, exc)
            sys.exit(1)
        lsock.listen(64)
        lsock.setblocking(False)

        if self.tls_ctx is None:
            modes = "unverschluesselt"
        elif self.require_tls:
            modes = "SSL/TLS oder STARTTLS (Verschluesselung ist Pflicht)"
        else:
            modes = "SSL/TLS, STARTTLS oder unverschluesselt"
        log.info("Relay hoert auf Port %s: %s", port, modes)
        log.info(
            "Ziel: %s:%s%s, erlaubte Empfaenger-Domains: %s",
            self.cfg["target_host"],
            self.cfg["target_port"],
            " mit SSL/TLS" if target_uses_ssl(self.cfg) else "",
            ", ".join(self.cfg["allowed_domains"]),
        )

        if backup_enabled(self.cfg):
            log.info(
                "Backup-Server: %s:%s - wird nach einem Fehler beim Hauptserver sofort verwendet",
                self.cfg["backup_host"], self.cfg["backup_port"],
            )
        log.info(
            "Zustellung: Drosselung %s, Wartezeit zwischen Mails %d s, Wiederholung bis %d Min, "
            "Aufbewahrung %d Tage, Speicherlimit %d MB",
            "%d Min pro Kamera" % self.cfg["throttle_minutes"]
            if self.cfg["throttle_minutes"]
            else "aus",
            self.cfg["send_delay_seconds"],
            self.cfg["retry_minutes"],
            self.cfg["retention_days"],
            self.cfg["storage_mb"],
        )
        counts = self.store.counts()
        log.info(
            "Warteschlange beim Start: %d wartend, %d gescheitert, %d gedrosselt gespeichert",
            counts.get("queued", 0),
            counts.get("failed", 0),
            counts.get("throttled", 0),
        )

        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.loop.add_signal_handler(sig, stop.set)

        self.deliverer.start()
        self.maintenance.start()
        if self.detector:
            self.detector.start()
        self.board.start()
        background = [self.loop.create_task(self._accept_loop(lsock))]
        if self.tls_ctx is not None:
            background.append(self.loop.create_task(self._watch_certificate()))
        # Eigener Thread, damit ein haengender Zieltest das Beenden nicht aufhaelt
        if self.cfg["forward_enabled"]:
            threading.Thread(target=check_target, args=(self.cfg,), name="zieltest", daemon=True).start()
        else:
            log.info("Weiterleitung ist ausgeschaltet - Fotos werden nur gespeichert")
        if self.cfg["forward_enabled"] and backup_enabled(self.cfg):
            threading.Thread(target=check_backup, args=(self.cfg,), name="backuptest", daemon=True).start()

        await stop.wait()
        log.info("Relay wird beendet")
        for task in background:
            task.cancel()
        lsock.close()
        self.deliverer.stop()
        self.maintenance.stop()
        self.board.stop()
        await self.loop.run_in_executor(None, self.deliverer.join, 20)


def main():
    cfg = load_config()
    log.info("Kamera SMTP-Relay %s startet", VERSION)

    tls_ctx = None
    certfile, keyfile = cfg["certfile"], cfg["keyfile"]
    if certfile and keyfile and os.path.isfile(certfile) and os.path.isfile(keyfile):
        try:
            tls_ctx = make_tls_context(certfile, keyfile, bool(cfg["tls_compat"]))
            log.info("Zertifikat geladen: %s", certfile)
            log.info(
                "Verschluesselung zur Kamera: %s",
                "auch aeltere Kameras (TLS ab 1.0)" if cfg["tls_compat"] else "nur modern (TLS ab 1.2)",
            )
        except (OSError, ssl.SSLError) as exc:
            log.error("Zertifikat %s konnte nicht geladen werden: %r", certfile, exc)
    else:
        log.info("Kein Zertifikat gefunden (%s) - Betrieb ohne Verschluesselung", certfile)

    if cfg["require_tls"] and tls_ctx is None:
        log.error("require_tls ist aktiv, aber es wurde kein gueltiges Zertifikat gefunden")
        sys.exit(1)

    try:
        store = Store(DATA_DIR, cfg["retention_days"], cfg["storage_mb"], share_dir=PHOTOS_DIR,
                      use_share=cfg["photo_storage"], keep_sent=cfg["photo_storage"])
        NAME_STORE[0] = store
    except OSError as exc:
        log.error("Datenordner %s nicht nutzbar: %s", DATA_DIR, exc)
        sys.exit(1)
    store.recover()
    store.seed_cameras(load_seed_cameras())
    if cfg["photo_storage"]:
        moved = store.migrate_to_share()
        if moved:
            log.info("%d Foto(s) vom internen Speicher nach %s verschoben", moved, PHOTOS_DIR)
        log.info("Fotoablage: an - %s/<Revier>/<Kamera>/", PHOTOS_DIR)
    else:
        log.info("Fotoablage: aus - nur nicht weitergeleitete Fotos werden aufbewahrt")
    runtime = Runtime()

    asyncio.run(RelayServer(cfg, tls_ctx, store, runtime).run())


if __name__ == "__main__":
    main()
