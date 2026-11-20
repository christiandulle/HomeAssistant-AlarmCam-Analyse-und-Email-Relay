"""Warteschlange und Archiv der Kamera-Mails (SQLite + Dateien im Datenordner).

Jede angenommene Mail liegt nur so lange als .eml-Datei im Ordner spool/, bis sie
zugestellt oder entschieden ist (gedrosselt, gefiltert, gescheitert). Dauerhaft bleiben
das Foto (Ordner previews/) und die Metadaten samt Status und Empfaengeradresse in relay.db.
Zum erneuten Senden wird die Mail aus dem Foto neu gebaut. Aeltestes Foto zuerst (FIFO)
wird geloescht, wenn der Speicher voll ist.

Status einer Mail:
  queued     wartet auf Zustellung (oder auf den naechsten Versuch)
  sending    wird gerade zugestellt
  sent       zugestellt
  failed     endgueltig gescheitert
  throttled  durch die Drosselung nicht weitergeleitet
  held       wartet kurz auf die Bilderkennung (nur bei Filter "an")
  filtered   Bilderkennung hat nichts gefunden, nicht weitergeleitet
  discarded  (alt) Mail ohne Datei und ohne Foto
"""

import email.policy
import email.utils
import json
import logging
import os
import re
import sqlite3
import threading
import time
from email.message import EmailMessage
from email.parser import BytesHeaderParser, BytesParser

log = logging.getLogger("relay")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mails (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received REAL NOT NULL,
  queued REAL,
  peer TEXT,
  mail_from TEXT,
  rcpts TEXT,
  camera TEXT,
  size INTEGER DEFAULT 0,
  subject TEXT DEFAULT '',
  mail_date REAL,
  preview INTEGER DEFAULT -1,
  preview_type TEXT DEFAULT '',
  n_images INTEGER DEFAULT 0,
  n_videos INTEGER DEFAULT 0,
  status TEXT NOT NULL,
  passed REAL,
  attempts INTEGER DEFAULT 0,
  next_attempt REAL DEFAULT 0,
  last_error TEXT DEFAULT '',
  finished REAL,
  has_file INTEGER DEFAULT 1,
  via TEXT DEFAULT '',
  det_label TEXT DEFAULT '',
  det_conf REAL DEFAULT 0,
  det_n INTEGER DEFAULT 0,
  det_model TEXT DEFAULT '',
  det_confs TEXT DEFAULT '',
  det_boxes TEXT DEFAULT '',
  fav INTEGER DEFAULT 0,
  test_label TEXT DEFAULT '',
  test_confs TEXT DEFAULT '',
  test_boxes TEXT DEFAULT '',
  test_model TEXT DEFAULT '',
  pbytes INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_status ON mails(status, next_attempt);
CREATE INDEX IF NOT EXISTS idx_camera ON mails(camera, passed);
CREATE INDEX IF NOT EXISTS idx_received ON mails(received);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started REAL NOT NULL,
  ip TEXT,
  camera TEXT,
  known INTEGER DEFAULT 1,
  enc TEXT DEFAULT '',
  outcome TEXT NOT NULL,
  detail TEXT DEFAULT '',
  mail_id INTEGER,
  size INTEGER DEFAULT 0,
  n_images INTEGER DEFAULT 0,
  n_videos INTEGER DEFAULT 0,
  subject TEXT DEFAULT '',
  duration REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started);
CREATE TABLE IF NOT EXISTS cameras (
  address TEXT PRIMARY KEY,
  name TEXT DEFAULT '',
  revier TEXT DEFAULT '',
  first_seen REAL,
  last_seen REAL,
  mails INTEGER DEFAULT 0,
  second_image TEXT DEFAULT ''
);
"""

SESSION_OK = ("ok_mail", "ok_test")
SESSION_FILTERS = {
    "all": None,
    "open": ("failed", "discarded", "throttled", "filtered"),
    "problems": "problems",
    "tests": ("ok_test",),
    "mails": ("ok_mail",),
}

# Bilderkennung: Filterwerte der Oberflaeche -> gespeicherte Labels
DET_FILTERS = {
    "erkannt": None,          # mindestens eines von Mensch / Tier / Fahrzeug
    "tier": "tier",
    "mensch": "mensch",
    "fahrzeug": "fahrzeug",
    "leer": ("leer",),
    "offen": ("", "fehler"),
}

FILTERS = {
    "all": None,
    "open": ("failed", "discarded", "throttled", "filtered"),
    "problems": ("failed", "discarded"),
    "throttled": ("throttled",),
    "filtered": ("filtered",),
    "queued": ("queued", "sending", "held"),
    "sent": ("sent",),
}


class Runtime:
    """Laufzeitzustand, den Zustellung und Board gemeinsam sehen."""

    def __init__(self):
        self.pause_until = 0.0
        self.last_success = None
        self.last_error = ""
        self.last_error_time = None
        self.primary_skip_until = 0.0  # Hauptserver ausgefallen: neue Mails direkt ueber das Backup
        self.started = time.time()


def inspect_mail(content):
    """Liest Betreff, Datum und Foto-/Videoteile einer Mail (ohne sie zu veraendern)."""
    result = {
        "subject": "",
        "mail_date": None,
        "preview": -1,
        "preview_type": "",
        "n_images": 0,
        "n_videos": 0,
    }
    try:
        msg = BytesParser(policy=email.policy.default).parsebytes(content)
        result["subject"] = str(msg.get("subject", "") or "")[:200]
        date = msg.get("date")
        if date:
            try:
                result["mail_date"] = email.utils.parsedate_to_datetime(str(date)).timestamp()
            except (TypeError, ValueError):
                pass
        for index, part in enumerate(msg.walk()):
            ctype = part.get_content_type()
            if ctype.startswith("image/"):
                result["n_images"] += 1
                kind = "image"
            elif ctype.startswith("video/"):
                result["n_videos"] += 1
                kind = "video"
            else:
                continue
            if result["preview"] < 0:
                result["preview"] = index
                result["preview_type"] = kind
    except Exception as exc:  # defekte Mails duerfen die Annahme nie verhindern
        log.debug("Mail konnte nicht analysiert werden: %r", exc)
    return result


def drop_second_image(content, n=2):
    """Entfernt das n-te Foto (Standard: das 2.) aus einer Mail.

    Gedacht fuer PTZ-Kameras, die neben dem normalen Bild ein Zoom-Bild mitschicken.
    Die Mail wird nur an der Stelle des Fotos geaendert (Teile werden am Boundary
    getrennt), alles andere bleibt Byte fuer Byte gleich. Gibt (Inhalt, entfernte Bytes)
    zurueck; ohne zweites Foto kommt der Inhalt unveraendert zurueck."""
    state = {"count": 0, "removed": 0}

    def split(raw):
        match = re.search(rb"\r?\n\r?\n", raw)
        if not match:
            return raw, b"", b""
        return raw[: match.start()], match.group(0), raw[match.end():]

    def walk(raw):
        head, sep, body = split(raw)
        try:
            hdr = BytesHeaderParser(policy=email.policy.compat32).parsebytes(head)
            ctype = hdr.get_content_type()
            boundary = hdr.get_boundary() if hdr.get_content_maintype() == "multipart" else None
        except Exception:
            return raw
        if ctype.startswith("image/"):
            state["count"] += 1
            if state["count"] == n:
                state["removed"] = len(raw)
                return None
            return raw
        if not boundary:
            return raw
        tag = b"--" + boundary.encode("utf-8", "replace")
        pre, segments, tail, current, closed = [], [], [], None, False
        for line in body.splitlines(keepends=True):
            bare = line.rstrip(b"\r\n").rstrip(b" \t")
            if closed:
                tail.append(line)
            elif bare == tag + b"--":
                closed = True
                tail.append(line)
            elif bare == tag:
                current = [line, []]
                segments.append(current)
            elif current is None:
                pre.append(line)
            else:
                current[1].append(line)
        out = list(pre)
        for delimiter, part in segments:
            raw_part = b"".join(part)
            new_part = walk(raw_part)
            if new_part is None:
                continue
            out.append(delimiter)
            out.append(new_part)
        out.extend(tail)
        return head + sep + b"".join(out)

    try:
        result = walk(content)
    except Exception as exc:  # im Zweifel die Mail unveraendert lassen
        log.debug("2. Foto konnte nicht entfernt werden: %r", exc)
        return content, 0
    if result is None or not state["removed"]:
        return content, 0
    return result, state["removed"]


PREVIEW_MAX_BYTES = 6 * 1024 * 1024
PREVIEW_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}
PREVIEW_TYPE = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}


def preview_image(content, index):
    """Liefert (Bytes, Dateiendung) des Vorschaubilds (Teil Nr. index) oder None."""
    try:
        msg = BytesParser(policy=email.policy.default).parsebytes(content)
        for i, part in enumerate(msg.walk()):
            if i != index:
                continue
            ext = PREVIEW_EXT.get(part.get_content_type())
            payload = part.get_payload(decode=True) or b""
            if ext and 0 < len(payload) <= PREVIEW_MAX_BYTES:
                return payload, ext
            return None
    except Exception as exc:
        log.debug("Vorschaubild konnte nicht gelesen werden: %r", exc)
    return None


def build_mail(row, data, ext):
    """Baut aus Foto und Metadaten eine neue Mail (zum erneuten Senden). RevierSpion ordnet
    nur nach der Empfaengeradresse zu, Absender und Text sind unwichtig."""
    msg = EmailMessage()
    try:
        rcpts = json.loads(row["rcpts"] or "[]")
    except ValueError:
        rcpts = []
    msg["From"] = row["mail_from"] or "kamera@localhost"
    msg["To"] = ", ".join(rcpts) or row["camera"]
    msg["Subject"] = row["subject"] or "Kamera-Alarm"
    msg["Date"] = email.utils.formatdate(row["mail_date"] or row["received"])
    msg["Message-ID"] = email.utils.make_msgid(domain="kamera.local")
    msg.set_content("Foto der Kamera (erneut gesendet).")
    maintype, _, subtype = PREVIEW_TYPE.get(ext, "image/jpeg").partition("/")
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename="foto.%s" % ext)
    return msg.as_bytes(policy=email.policy.SMTP)   # CRLF-Zeilenenden, sonst lehnt das Ziel die Mail ab


class Store:
    def __init__(self, data_dir, retention_days=180, storage_mb=5120):
        self.dir = data_dir
        self.spool = os.path.join(data_dir, "spool")
        self.previews = os.path.join(data_dir, "previews")
        os.makedirs(self.spool, exist_ok=True)
        os.makedirs(self.previews, exist_ok=True)
        self.retention = int(retention_days) * 86400
        self.cap = int(storage_mb) * 1024 * 1024
        self.evicted_at = 0.0
        self.lock = threading.RLock()
        self.db = sqlite3.connect(os.path.join(data_dir, "relay.db"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)
            mail_cols = [r[1] for r in self.db.execute("PRAGMA table_info(mails)")]
            if "via" not in mail_cols:  # Update von aelteren Versionen
                self.db.execute("ALTER TABLE mails ADD COLUMN via TEXT DEFAULT ''")
            for col, ddl in (("det_label", "TEXT DEFAULT ''"), ("det_conf", "REAL DEFAULT 0"),
                             ("det_n", "INTEGER DEFAULT 0"), ("det_model", "TEXT DEFAULT ''"), ("det_confs", "TEXT DEFAULT ''"),
                             ("det_boxes", "TEXT DEFAULT ''"), ("fav", "INTEGER DEFAULT 0"),
                             ("test_label", "TEXT DEFAULT ''"), ("test_confs", "TEXT DEFAULT ''"),
                             ("test_boxes", "TEXT DEFAULT ''"), ("test_model", "TEXT DEFAULT ''"),
                             ("pbytes", "INTEGER DEFAULT 0")):
                if col not in mail_cols:  # Bilderkennung (ab 1.11.0)
                    self.db.execute("ALTER TABLE mails ADD COLUMN %s %s" % (col, ddl))
            columns = [r[1] for r in self.db.execute("PRAGMA table_info(cameras)")]
            if "second_image" not in columns:  # Update von aelteren Versionen
                self.db.execute("ALTER TABLE cameras ADD COLUMN second_image TEXT DEFAULT ''")
            # Kameras, deren Mails schon vor der Tabelle angenommen wurden, nachtragen
            self.db.execute(
                "INSERT OR IGNORE INTO cameras (address, first_seen, last_seen, mails)"
                " SELECT camera, MIN(received), MAX(received), COUNT(*) FROM mails"
                " WHERE camera<>'' GROUP BY camera"
            )
            self.db.commit()

    # -- Hilfen ---------------------------------------------------------

    def path(self, mail_id):
        return os.path.join(self.spool, "%d.eml" % int(mail_id))

    def preview_path(self, mail_id):
        """Pfad des aufbewahrten Vorschaubilds (bleibt auch nach der Zustellung) oder None."""
        for ext in PREVIEW_TYPE:
            path = os.path.join(self.previews, "%d.%s" % (int(mail_id), ext))
            if os.path.isfile(path):
                return path
        return None

    def _remove_preview(self, mail_id):
        for ext in PREVIEW_TYPE:
            try:
                os.remove(os.path.join(self.previews, "%d.%s" % (int(mail_id), ext)))
            except OSError:
                pass

    def _remove_file(self, mail_id):
        try:
            os.remove(self.path(mail_id))
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("[#%s] Datei konnte nicht geloescht werden: %s", mail_id, exc)

    def _shed(self, mail_id):
        """Mail-Datei loeschen, sobald das Foto sicher aufbewahrt ist (Mail ist dann entschieden).
        Mails ohne Foto behalten ihre Datei, sonst gaebe es nichts zum erneuten Senden."""
        if self.preview_path(mail_id):
            with self.lock:
                self.db.execute("UPDATE mails SET has_file=0 WHERE id=?", (mail_id,))
                self.db.commit()
            self._remove_file(mail_id)

    def _exec(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _query(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # -- Annahme --------------------------------------------------------

    def accept(self, peer, mail_from, rcpts, content, window_seconds, now=None, hold=False):
        """Speichert eine Mail und entscheidet, ob sie weitergeleitet oder gedrosselt wird."""
        now = time.time() if now is None else now
        info = inspect_mail(content)
        camera = (rcpts[0] if rcpts else "").strip().lower()
        with self.lock:
            last = None
            throttled = False
            if window_seconds > 0 and camera:
                last = self.db.execute(
                    "SELECT MAX(passed) FROM mails WHERE camera=?", (camera,)
                ).fetchone()[0]
                throttled = last is not None and (now - last) < window_seconds
            # hold: Mail mit Foto wartet, bis die Bilderkennung entschieden hat (Filter "an")
            if throttled:
                status = "throttled"
            elif hold and info["n_images"]:
                status = "held"
            else:
                status = "queued"
            try:
                cur = self.db.execute(
                    "INSERT INTO mails (received, queued, peer, mail_from, rcpts, camera, size,"
                    " subject, mail_date, preview, preview_type, n_images, n_videos, status,"
                    " passed, has_file) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        now,
                        None if throttled else now,
                        peer,
                        mail_from,
                        json.dumps(rcpts),
                        camera,
                        len(content),
                        info["subject"],
                        info["mail_date"],
                        info["preview"],
                        info["preview_type"],
                        info["n_images"],
                        info["n_videos"],
                        status,
                        None if throttled else now,
                    ),
                )
                mail_id = cur.lastrowid
                if camera:
                    self.db.execute(
                        "INSERT INTO cameras (address, first_seen, last_seen, mails)"
                        " VALUES (?,?,?,1) ON CONFLICT(address) DO UPDATE SET"
                        " last_seen=excluded.last_seen, mails=mails+1",
                        (camera, now, now),
                    )
                tmp = self.path(mail_id) + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(content)
                os.replace(tmp, self.path(mail_id))
                self.db.commit()
                if info["preview_type"] == "image":
                    found = preview_image(content, info["preview"])
                    if found:
                        with open(os.path.join(self.previews, "%d.%s" % (mail_id, found[1])), "wb") as fh:
                            fh.write(found[0])
                        self.db.execute("UPDATE mails SET pbytes=? WHERE id=?", (len(found[0]), mail_id))
                        self.db.commit()
                        if throttled:
                            self._shed(mail_id)
            except Exception:
                self.db.rollback()
                raise
            discarded = self._enforce_cap()
        return {
            "id": mail_id,
            "status": status,
            "throttled": throttled,
            "since": None if last is None else now - last,
            "discarded": discarded,
            "info": info,
            "camera": camera,
        }

    # -- Kameras (Klarname und Revier) ----------------------------------

    def seed_cameras(self, entries):
        """Legt bekannte Kameras an, ohne vorhandene Eintraege zu veraendern.

        entries: Liste von (Adresse, Klarname, Revier)."""
        with self.lock:
            for address, name, revier in entries:
                address = address.strip().lower()
                self.db.execute(
                    "INSERT OR IGNORE INTO cameras (address, name, revier) VALUES (?,?,?)",
                    (address, name, revier),
                )
            self.db.commit()

    def cameras(self):
        """Alle jemals gesehenen Kameras: zuerst unbenannte, dann nach Revier und Name."""
        return self._query(
            "SELECT * FROM cameras ORDER BY (name='' AND revier='') DESC,"
            " revier COLLATE NOCASE, name COLLATE NOCASE, address"
        )

    def camera_map(self):
        return {r["address"]: r for r in self._query("SELECT * FROM cameras")}

    def add_session(self, started, ip, camera, enc, outcome, detail="", mail_id=None, size=0,
                    n_images=0, n_videos=0, subject="", duration=0.0, known=1):
        """Eine Verbindung einer Kamera fuer die Auswertung festhalten (Test, Alarm oder Fehler)."""
        self._exec(
            "INSERT INTO sessions (started, ip, camera, known, enc, outcome, detail, mail_id, size, n_images,"
            " n_videos, subject, duration) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (started, ip, camera, 1 if known else 0, enc, outcome, detail[:300], mail_id, size, n_images,
             n_videos, (subject or "")[:200], duration),
        )

    @staticmethod
    def _session_where(flt, addresses, since, until, with_unknown):
        clauses, args = [], []
        statuses = SESSION_FILTERS.get(flt)
        if statuses == "problems":
            clauses.append("outcome NOT IN (%s)" % ",".join("?" * len(SESSION_OK)))
            args.extend(SESSION_OK)
        elif statuses:
            clauses.append("outcome IN (%s)" % ",".join("?" * len(statuses)))
            args.extend(statuses)
        if addresses is not None:
            if not addresses:
                clauses.append("0")
            else:
                clauses.append("camera IN (%s)" % ",".join("?" * len(addresses)))
                args.extend(addresses)
        if since is not None:
            clauses.append("started >= ?")
            args.append(since)
        if until is not None:
            clauses.append("started < ?")
            args.append(until)
        if not with_unknown:
            clauses.append("known = 1")
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def list_sessions(self, flt="all", addresses=None, since=None, until=None, limit=500, with_unknown=False):
        where, args = self._session_where(flt, addresses, since, until, with_unknown)
        return self._query("SELECT * FROM sessions%s ORDER BY id DESC LIMIT ?" % where, (*args, limit))

    def session_counts(self, addresses=None, since=None, until=None, with_unknown=False):
        where, args = self._session_where("all", addresses, since, until, with_unknown)
        rows = self._query("SELECT outcome, COUNT(*) AS n FROM sessions%s GROUP BY outcome" % where, args)
        return {r["outcome"]: r["n"] for r in rows}

    def cameras_by_ip(self, ip, limit=3):
        """Kameras, die zuletzt von dieser IP-Adresse Mails geschickt haben (neueste zuerst)."""
        rows = self._query(
            "SELECT camera, MAX(id) AS last FROM (SELECT id, camera FROM mails WHERE peer=? AND camera IS NOT NULL "
            "AND camera<>'' ORDER BY id DESC LIMIT 30) GROUP BY camera ORDER BY last DESC LIMIT ?",
            (ip, limit),
        )
        return [r["camera"] for r in rows]

    def set_camera(self, address, name, revier, second_image=None):
        """Speichert Klarname, Revier und (optional) die Behandlung des 2. Fotos.

        second_image: '' = weiterleiten und ablegen, 'archive' = nur ablegen,
        'drop' = verwerfen, None = unveraendert lassen."""
        clean = lambda text: " ".join("".join(c for c in str(text) if c.isprintable()).split())[:80]
        address = address.strip().lower()
        if not address:
            return False
        with self.lock:
            cur = self.db.execute(
                "UPDATE cameras SET name=?, revier=? WHERE address=?",
                (clean(name), clean(revier), address),
            )
            if second_image is not None and cur.rowcount > 0:
                mode = second_image if second_image in ("archive", "drop") else ""
                self.db.execute(
                    "UPDATE cameras SET second_image=? WHERE address=?", (mode, address)
                )
            self.db.commit()
            return cur.rowcount > 0

    def second_image_mode(self, address):
        rows = self._query(
            "SELECT second_image FROM cameras WHERE address=?", ((address or "").strip().lower(),)
        )
        return (rows[0]["second_image"] or "") if rows else ""

    def label(self, address):
        """Revier, Klarname und eindeutiger Name einer Kamera fuer Dateinamen.

        Ohne Eintrag: Revier 'Unbenannt', Name = Adresse. Gibt es denselben Klarnamen im
        selben Revier zweimal, wird der Adressteil angehaengt, damit nichts vermischt wird."""
        address = (address or "").strip().lower()
        local = address.split("@")[0]
        rows = self.camera_map()
        row = rows.get(address)
        name = (row["name"] if row else "") or ""
        revier = (row["revier"] if row else "") or ""
        if not name and not revier:
            return {"revier": "Unbenannt", "name": local, "unique": local, "named": False}
        revier = revier or "Ohne Revier"
        name = name or local
        unique = name
        for other_addr, other in rows.items():
            if (other_addr != address and (other["name"] or "").casefold() == name.casefold()
                    and (other["revier"] or "Ohne Revier").casefold() == revier.casefold()):
                unique = "%s %s" % (name, local.split("_")[-1])
                break
        return {"revier": revier, "name": name, "unique": unique, "named": True}

    # -- Zustellung -----------------------------------------------------

    def next_due(self, now):
        rows = self._query(
            "SELECT * FROM mails WHERE status='queued' AND next_attempt<=? ORDER BY id LIMIT 1",
            (now,),
        )
        return rows[0] if rows else None

    def read(self, mail_id):
        try:
            with open(self.path(mail_id), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def mark_sending(self, mail_id, via=""):
        self._exec("UPDATE mails SET status='sending', via=? WHERE id=?", (via, mail_id))

    def mark_sent(self, mail_id, attempts, now):
        self._exec(
            "UPDATE mails SET status='sent', attempts=?, finished=?, has_file=0, last_error=''"
            " WHERE id=?",
            (attempts, now, mail_id),
        )
        self._remove_file(mail_id)

    def mark_retry(self, mail_id, attempts, next_attempt, error):
        self._exec(
            "UPDATE mails SET status='queued', attempts=?, next_attempt=?, last_error=?"
            " WHERE id=?",
            (attempts, next_attempt, error, mail_id),
        )

    def mark_failed(self, mail_id, attempts, error, now):
        self._exec(
            "UPDATE mails SET status='failed', attempts=?, last_error=?, finished=?"
            " WHERE id=?",
            (attempts, error, now, mail_id),
        )
        self._shed(mail_id)

    def release_held(self, mail_id, forward, now=None):
        """Bilderkennung hat entschieden: weiterleiten (queued) oder nicht weiterleiten (filtered).
        Gibt True zurueck, wenn die Mail noch wartete."""
        now = time.time() if now is None else now
        with self.lock:
            if forward:
                cur = self.db.execute(
                    "UPDATE mails SET status='queued', queued=?, next_attempt=0 WHERE id=? AND status='held'",
                    (now, mail_id))
            else:
                # passed zuruecksetzen: eine nicht weitergeleitete Mail soll die Drosselung nicht ausloesen
                cur = self.db.execute(
                    "UPDATE mails SET status='filtered', queued=NULL, passed=NULL WHERE id=? AND status='held'",
                    (mail_id,))
            self.db.commit()
            changed = cur.rowcount > 0
        if changed and not forward:
            self._shed(mail_id)
        return changed

    def release_stale_held(self, max_age, now=None):
        """Sicherheitsnetz: wartet eine Mail zu lange auf die Erkennung, wird sie weitergeleitet."""
        now = time.time() if now is None else now
        with self.lock:
            cur = self.db.execute(
                "UPDATE mails SET status='queued', queued=?, next_attempt=0 WHERE status='held' AND received<?",
                (now, now - max_age))
            self.db.commit()
            return cur.rowcount

    def requeue(self, mail_id, now=None):
        """Stellt ein Foto erneut zur Zustellung ein (jeder Status ausser wartend).
        Ist die Mail-Datei weg, wird sie aus Foto und Metadaten neu gebaut."""
        now = time.time() if now is None else now
        with self.lock:
            row = self.db.execute("SELECT * FROM mails WHERE id=?", (mail_id,)).fetchone()
            if row is None or row["status"] not in ("throttled", "failed", "filtered", "sent", "discarded"):
                return False
            size = row["size"]
            if not os.path.isfile(self.path(mail_id)):
                photo = self.preview_path(mail_id)
                if not photo:
                    return False
                try:
                    with open(photo, "rb") as fh:
                        data = fh.read()
                    content = build_mail(row, data, photo.rsplit(".", 1)[-1])
                    tmp = self.path(mail_id) + ".tmp"
                    with open(tmp, "wb") as fh:
                        fh.write(content)
                    os.replace(tmp, self.path(mail_id))
                except OSError as exc:
                    log.error("[#%s] Mail konnte nicht neu erzeugt werden: %r", mail_id, exc)
                    return False
                size = len(content)
            self.db.execute(
                "UPDATE mails SET status='queued', queued=?, attempts=0, next_attempt=0,"
                " last_error='', finished=NULL, via='', has_file=1, size=? WHERE id=?",
                (now, size, mail_id),
            )
            self.db.commit()
        return True

    def requeue_failed(self, now=None):
        ids = [r["id"] for r in self._query("SELECT id FROM mails WHERE status='failed'")]
        return [i for i in ids if self.requeue(i, now)]

    def delete(self, mail_id):
        with self.lock:
            self.db.execute("DELETE FROM mails WHERE id=?", (mail_id,))
            self.db.commit()
        self._remove_file(mail_id)
        self._remove_preview(mail_id)

    def delete_many(self, ids):
        """Loescht Mails (nur solche, die nicht gerade auf die Zustellung warten; Favoriten bleiben)."""
        done = []
        for mail_id in ids:
            with self.lock:
                row = self.db.execute("SELECT status, fav FROM mails WHERE id=?", (mail_id,)).fetchone()
                if row is None or row["fav"] or row["status"] in ("queued", "sending"):
                    continue
                self.db.execute("DELETE FROM mails WHERE id=?", (mail_id,))
                self.db.commit()
            self._remove_file(mail_id)
            self._remove_preview(mail_id)
            done.append(mail_id)
        return done

    # -- Abfragen fuer das Board ---------------------------------------

    def get(self, mail_id):
        rows = self._query("SELECT * FROM mails WHERE id=?", (mail_id,))
        return rows[0] if rows else None

    @staticmethod
    def _where(flt, addresses, since=None, until=None, det="", fav=False):
        clauses, args = [], []
        if fav:
            clauses.append("fav=1")
        if det in DET_FILTERS:
            sel = DET_FILTERS[det]
            if sel is None:
                clauses.append("det_label NOT IN ('', 'leer', 'fehler')")
            elif isinstance(sel, str):   # ein Etikett unter mehreren ("mensch,fahrzeug")
                clauses.append("(',' || det_label || ',') LIKE ?")
                args.append("%%,%s,%%" % sel)
            else:
                clauses.append("det_label IN (%s)" % ",".join("?" * len(sel)))
                args.extend(sel)
        if since is not None:
            clauses.append("received >= ?")
            args.append(since)
        if until is not None:
            clauses.append("received < ?")
            args.append(until)
        statuses = FILTERS.get(flt)
        if statuses:
            clauses.append("status IN (%s)" % ",".join("?" * len(statuses)))
            args.extend(statuses)
        if addresses is not None:
            if not addresses:
                clauses.append("0")
            else:
                clauses.append("camera IN (%s)" % ",".join("?" * len(addresses)))
                args.extend(addresses)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def list_rows(self, flt="all", limit=300, addresses=None, det=""):
        where, args = self._where(flt, addresses, det=det)
        return self._query("SELECT * FROM mails%s ORDER BY id DESC LIMIT ?" % where, (*args, limit))

    def gallery_rows(self, addresses=None, scan=5000, limit=1000, since=None, until=None, det="", fav=False):
        """Neueste Mails mit Foto (fuer die Bilderseite); aeltere Eintraege ohne Bild werden uebersprungen.
        since/until: Empfangszeit (Unix-Sekunden), since einschliesslich, until ausschliesslich."""
        where, args = self._where("all", addresses, since, until, det, fav)
        where += (" AND" if where else " WHERE") + " preview_type='image'"
        rows = self._query("SELECT * FROM mails%s ORDER BY id DESC LIMIT ?" % where, (*args, scan))
        out = []
        for r in rows:
            if r["has_file"] or self.preview_path(r["id"]):
                out.append(r)
                if len(out) >= limit:
                    break
        return out

    # -- Bilderkennung ----------------------------------------------------

    def mail_images(self, mail_id, limit=6):
        """Bilddaten (Bytes) der Fotos einer gespeicherten Mail, hoechstens limit Stueck."""
        try:
            with open(self.path(mail_id), "rb") as fh:
                msg = BytesParser(policy=email.policy.default).parsebytes(fh.read())
        except (OSError, ValueError) as exc:
            # Mail schon zugestellt: nur das aufbewahrte Vorschaubild (erstes Foto) ist noch da
            preview = self.preview_path(mail_id)
            if preview:
                try:
                    with open(preview, "rb") as fh:
                        return [fh.read()]
                except OSError:
                    pass
            log.debug("Mail %s fuer die Erkennung nicht lesbar: %r", mail_id, exc)
            return []
        out = []
        for part in msg.walk():
            if PREVIEW_EXT.get(part.get_content_type()):
                payload = part.get_payload(decode=True) or b""
                if payload:
                    out.append(payload)
                    if len(out) >= limit:
                        break
        return out

    def set_detection(self, mail_id, label, conf=0.0, n=0, model="", confs="", boxes=""):
        """label: "mensch,tier" (Etiketten mit Komma), "leer" oder "fehler"; confs: "mensch:0.88,tier:0.9"."""
        self._exec("UPDATE mails SET det_label=?, det_conf=?, det_n=?, det_model=?, det_confs=?, det_boxes=? WHERE id=?",
                   (label, float(conf), int(n), model, confs, boxes, mail_id))

    def set_detection_model(self, mail_id, model):
        """Nur das Modell vermerken (Ergebnis bleibt), z. B. wenn die Fotos nicht mehr da sind."""
        self._exec("UPDATE mails SET det_model=? WHERE id=?", (model, mail_id))

    def set_fav(self, mail_id, value):
        """Favoritenstern setzen; gibt den neuen Wert (0/1) zurueck oder None, wenn es die Mail nicht gibt."""
        cur = self._exec("UPDATE mails SET fav=? WHERE id=?", (1 if value else 0, mail_id))
        return (1 if value else 0) if cur.rowcount else None

    def fav(self, mail_id):
        rows = self._query("SELECT fav FROM mails WHERE id=?", (mail_id,))
        return int(rows[0]["fav"]) if rows else None

    def set_test(self, mail_id, label, confs="", boxes="", model=""):
        """Ergebnis des Testmodells (nur Favoriten); das Ergebnis des laufenden Modells bleibt unberuehrt."""
        self._exec("UPDATE mails SET test_label=?, test_confs=?, test_boxes=?, test_model=? WHERE id=?",
                   (label, confs, boxes, model, mail_id))

    def pending_test(self, model):
        """Favoriten mit Foto, die noch nicht mit dem Testmodell geprueft wurden (neueste zuerst)."""
        return [r["id"] for r in self._query(
            "SELECT id FROM mails WHERE fav=1 AND test_model!=? AND n_images>0"
            " AND (has_file=1 OR preview_type='image') ORDER BY id DESC", (model,))]

    def detection_label(self, mail_id):
        rows = self._query("SELECT det_label FROM mails WHERE id=?", (mail_id,))
        return rows[0]["det_label"] if rows else ""

    def pending_detection(self, limit=300, model="", favorites_only=False):
        """Mails mit Foto ohne Ergebnis (neueste zuerst) und Favoriten, die mit einem anderen Modell ausgewertet wurden.
        Andere Mails werden bei einem Modellwechsel bewusst nicht neu bewertet. limit 0 = ohne Grenze."""
        cond = "(fav=1 AND det_model!=?)" if favorites_only else "(det_label='' OR (fav=1 AND det_model!=?))"
        return [r["id"] for r in self._query(
            "SELECT id FROM mails WHERE " + cond + " AND n_images>0"
            " AND (has_file=1 OR preview_type='image')"
            " ORDER BY id DESC LIMIT ?", (model, limit if limit and limit > 0 else -1))]

    def all_image_ids(self):
        """Alle Mails mit auswertbarem Foto, neueste zuerst (fuer \"Bestand neu pruefen\")."""
        return [r["id"] for r in self._query(
            "SELECT id FROM mails WHERE n_images>0 AND (has_file=1 OR preview_type='image') ORDER BY id DESC")]

    def promote_test(self, test_tag, prod_tag):
        """Testmodell wird produktiv: bei Favoriten das schon berechnete Testergebnis als normales Ergebnis uebernehmen."""
        n = 0
        for r in self._query("SELECT id, det_n, test_label, test_confs, test_boxes FROM mails"
                             " WHERE fav=1 AND test_model=? AND test_label NOT IN ('', 'fehler')", (test_tag,)):
            best = 0.0
            for part in (r["test_confs"] or "").split(","):
                name, _, val = part.partition(":")
                try:
                    best = max(best, float(val))
                except ValueError:
                    pass
            self.set_detection(r["id"], r["test_label"], best, r["det_n"] or 1, prod_tag, r["test_confs"], r["test_boxes"])
            n += 1
        return n

    def matching_ids(self, flt="all", addresses=None, deletable_only=True):
        where, args = self._where(flt, addresses)
        if deletable_only:
            where += (" AND" if where else " WHERE") + " fav=0 AND status NOT IN ('queued','sending','held')"
        return [r["id"] for r in self._query("SELECT id FROM mails%s" % where, args)]

    def counts(self, addresses=None):
        where, args = self._where("all", addresses)
        rows = self._query("SELECT status, COUNT(*) AS n FROM mails%s GROUP BY status" % where, args)
        return {r["status"]: r["n"] for r in rows}

    def oldest_waiting(self, now):
        """Alter der aeltesten wartenden Mail in Sekunden (oder None)."""
        rows = self._query(
            "SELECT MIN(queued) AS q FROM mails WHERE status IN ('queued','sending')"
        )
        value = rows[0]["q"] if rows else None
        return None if value is None else now - value

    def summary(self, start, end):
        rows = self._query(
            "SELECT status, COUNT(*) AS n FROM mails WHERE received>=? AND received<? GROUP BY status",
            (start, end),
        )
        return {r["status"]: r["n"] for r in rows}

    def storage_bytes(self):
        """Belegter Speicher: Fotos plus Mail-Dateien ohne Foto (z. B. Videos)."""
        rows = self._query(
            "SELECT COALESCE(SUM(pbytes),0) + COALESCE(SUM(CASE WHEN has_file=1 AND pbytes=0 THEN size ELSE 0 END),0)"
            " AS s FROM mails")
        return rows[0]["s"]

    def storage_info(self):
        """used/cap in Bytes; full = Speicher voll, aelteste Fotos werden bereits geloescht (FIFO)."""
        used = self.storage_bytes()
        recent = self.evicted_at and time.time() - self.evicted_at < 86400
        return {"used": used, "cap": self.cap, "full": bool(recent or used >= 0.95 * self.cap)}

    # -- Pflege ---------------------------------------------------------

    def recover(self):
        """Nach einem Neustart: unterbrochene Zustellungen wieder einreihen, Waisen entfernen."""
        with self.lock:
            self.db.execute("UPDATE mails SET status='queued' WHERE status IN ('sending', 'held')")
            known = set()
            for row in self.db.execute("SELECT id, status FROM mails WHERE has_file=1").fetchall():
                if os.path.isfile(self.path(row["id"])):
                    known.add("%d.eml" % row["id"])
                elif row["status"] == "queued" and self.preview_path(row["id"]):
                    # Datei fehlt, Foto ist da: neu bauen lassen statt zu verwerfen
                    self.db.execute("UPDATE mails SET status='failed', has_file=0,"
                                    " last_error='Mail-Datei fehlte nach Neustart' WHERE id=?", (row["id"],))
                else:
                    self.db.execute(
                        "UPDATE mails SET status='discarded', has_file=0,"
                        " last_error='Datei fehlt' WHERE id=?",
                        (row["id"],),
                    )
            self.db.commit()
        with self.lock:
            ids = {str(r["id"]) for r in self.db.execute("SELECT id FROM mails").fetchall()}
        for name in os.listdir(self.previews):
            if name.split(".")[0] not in ids:
                try:
                    os.remove(os.path.join(self.previews, name))
                except OSError:
                    pass
        for name in os.listdir(self.spool):
            if name not in known:
                try:
                    os.remove(os.path.join(self.spool, name))
                except OSError:
                    pass

    def _enforce_cap(self):
        """Speicherlimit einhalten: aelteste Fotos zuerst loeschen (FIFO). Favoriten und noch
        wartende Mails bleiben. Gibt die geloeschten Nummern zurueck."""
        removed = []
        with self.lock:
            total = self.storage_bytes()
            while total > self.cap:
                row = self.db.execute(
                    "SELECT id, size, pbytes, has_file FROM mails WHERE fav=0"
                    " AND status NOT IN ('queued','sending','held') AND (pbytes>0 OR has_file=1)"
                    " ORDER BY id LIMIT 1"
                ).fetchone()
                if row is None:
                    break
                self.db.execute("DELETE FROM mails WHERE id=?", (row["id"],))
                self.db.commit()
                self._remove_file(row["id"])
                self._remove_preview(row["id"])
                removed.append(row["id"])
                total -= row["pbytes"] or (row["size"] if row["has_file"] else 0)
            if removed:
                self.evicted_at = time.time()
        return removed

    def purge(self, now=None):
        """Loescht Eintraege und Dateien, die aelter als die Aufbewahrungszeit sind."""
        now = time.time() if now is None else now
        cutoff = now - self.retention
        with self.lock:
            rows = self.db.execute(
                "SELECT id FROM mails WHERE received<? AND fav=0 AND status NOT IN ('queued','sending','held')",
                (cutoff,),
            ).fetchall()
            for row in rows:
                self.db.execute("DELETE FROM mails WHERE id=?", (row["id"],))
            self.db.execute("DELETE FROM sessions WHERE started<?", (cutoff,))
            self.db.commit()
        for row in rows:
            self._remove_file(row["id"])
            self._remove_preview(row["id"])
        discarded = self._enforce_cap()
        return len(rows), discarded
