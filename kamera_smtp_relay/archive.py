"""Bildablage fuer die spaetere Analyse (z. B. auf dem Mac mini).

Jede angenommene Mail - auch gedrosselte - legt ihre Fotos hier ab:

    <ordner>/<Revier>/<Kamera>/<JJJJ-MM-TT>/<Revier>_<Kamera>_<JJJJ-MM-TT>_<HH-MM-SS>.jpg
    <ordner>/<Revier>/<Kamera>/<JJJJ-MM-TT>/<Revier>_<Kamera>_<JJJJ-MM-TT>_<HH-MM-SS>.json

Mehrere Fotos in einer Mail bekommen _1, _2, ... Revier und Kamera stammen aus der
Kameratabelle im Board (Umlaute werden fuer Dateinamen umgeschrieben, z. B. ae, oe, ue, ss).
Zusaetzlich liegt im Ordner eine kameras.json mit der ganzen Tabelle.

Die Ablage laeuft getrennt von der Weiterleitung: Ein Fehler hier aendert an der
Zustellung nichts.
"""

import email
import email.policy
import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import time
from email.parser import BytesParser

log = logging.getLogger("relay")

DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
CAMERAS_FILE = "kameras.json"


def slug(text, fallback="unbekannt"):
    """Dateiname-tauglicher Text: Umlaute umgeschrieben, Leerzeichen werden zu '-'."""
    text = (text or "").translate(UMLAUTS)
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text[:60] or fallback



def jpeg_size(data):
    """Breite und Hoehe eines JPEG (aus dem Kopf gelesen), sonst (0, 0)."""
    try:
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = int.from_bytes(data[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + length
    except Exception:
        pass
    return 0, 0


class Archive:
    def __init__(self, base_dir, days, max_mb=10240, videos=False):
        self.base = base_dir
        self.days = int(days)
        self.max_bytes = int(max_mb) * 1024 * 1024
        self.videos = bool(videos)
        self.lock = threading.Lock()
        self.used = None  # wird beim ersten Bedarf gezaehlt
        self._cameras_written = None

    # -- Ablegen --------------------------------------------------------

    def save(self, mail_id, rcpts, content, received, throttled, peer="", label=None):
        """Legt die Fotos einer Mail ab. Gibt die Anzahl gespeicherter Dateien zurueck."""
        msg = BytesParser(policy=email.policy.default).parsebytes(content)
        address = (rcpts[0] if rcpts else "").lower()
        label = label or {"revier": "Unbenannt", "name": address.split("@")[0], "unique": address.split("@")[0], "named": False}
        local = time.localtime(received)
        folder, stem = self._target(label, received, mail_id)
        if os.path.exists(os.path.join(folder, stem + ".json")):
            stem += "_m%d" % mail_id  # zwei Mails in derselben Sekunde

        attachments = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if not (ctype.startswith("image/") or (self.videos and ctype.startswith("video/"))):
                continue
            payload = part.get_payload(decode=True)
            if payload:
                attachments.append((part, ctype, payload))
        if not attachments:
            return 0

        os.makedirs(folder, exist_ok=True)
        files = []
        for number, (part, ctype, payload) in enumerate(attachments, 1):
            ext = EXTENSIONS.get(ctype) or mimetypes.guess_extension(ctype) or ".bin"
            name = "%s%s%s" % (stem, "_%d" % number if len(attachments) > 1 else "", ext)
            self._write(os.path.join(folder, name), payload)
            entry = {"datei": name, "typ": ctype, "bytes": len(payload),
                     "original": part.get_filename() or ""}
            if ctype == "image/jpeg":
                entry["breite"], entry["hoehe"] = jpeg_size(payload)
            files.append(entry)

        text = ""
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            try:
                text = body.get_content().strip()[:600]
            except Exception:
                text = ""
        meta = {
            "mail": mail_id,
            "empfangs_email": address,
            "klarname": label["name"] if label.get("named") else "",
            "revier": label["revier"] if label.get("named") else "",
            "empfangen": time.strftime("%Y-%m-%dT%H:%M:%S%z", local),
            "empfangen_unix": int(received),
            "gedrosselt": bool(throttled),
            "absender_ip": peer,
            "betreff": str(msg.get("subject", "") or ""),
            "text": text,
            "mail_datum": str(msg.get("date", "") or ""),
            "dateien": files,
        }
        meta_bytes = json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8")
        self._write(os.path.join(folder, stem + ".json"), meta_bytes)
        with self.lock:
            if self.used is not None:
                self.used += sum(f["bytes"] for f in files) + len(meta_bytes)
        return len(files)

    def _target(self, label, received, mail_id, count_hint=None):
        """Ordner und Dateiname (ohne Endung/Nummer) fuer eine Mail."""
        revier = slug(label["revier"], "Unbenannt")
        camera = slug(label["unique"], "kamera")
        local = time.localtime(received)
        day = time.strftime("%Y-%m-%d", local)
        parts = [revier] if revier.casefold() == camera.casefold() else [revier, camera]
        stem = "_".join(parts + [day, time.strftime("%H-%M-%S", local)])
        return os.path.join(self.base, revier, camera, day), stem

    def relabel(self, address, label):
        """Sortiert bereits abgelegte Bilder einer Kamera nach neuem Klarnamen/Revier um.

        Gibt die Anzahl umgezogener Mails zurueck."""
        address = (address or "").lower()
        moved = 0
        if not os.path.isdir(self.base):
            return 0
        found = []
        for root, _dirs, names in os.walk(self.base):
            for name in names:
                if name.endswith(".json") and name != CAMERAS_FILE:
                    found.append(os.path.join(root, name))
        for json_path in sorted(found):
            try:
                with open(json_path, "rb") as fh:
                    meta = json.loads(fh.read().decode("utf-8"))
            except (OSError, ValueError):
                continue
            if str(meta.get("empfangs_email", "")).lower() != address:
                continue
            old_folder = os.path.dirname(json_path)
            folder, stem = self._target(label, meta.get("empfangen_unix", 0), meta.get("mail", 0))
            if os.path.normpath(folder) == os.path.normpath(old_folder) and \
                    os.path.basename(json_path) == stem + ".json":
                continue
            if os.path.exists(os.path.join(folder, stem + ".json")):
                stem += "_m%d" % meta.get("mail", 0)
            os.makedirs(folder, exist_ok=True)
            files = meta.get("dateien", [])
            for number, entry in enumerate(files, 1):
                ext = os.path.splitext(entry["datei"])[1]
                new_name = "%s%s%s" % (stem, "_%d" % number if len(files) > 1 else "", ext)
                try:
                    os.replace(os.path.join(old_folder, entry["datei"]), os.path.join(folder, new_name))
                    entry["datei"] = new_name
                except OSError as exc:
                    log.warning("Umbenennen von %s fehlgeschlagen: %s", entry["datei"], exc)
            meta["klarname"] = label["name"] if label.get("named") else ""
            meta["revier"] = label["revier"] if label.get("named") else ""
            self._write(os.path.join(folder, stem + ".json"),
                        json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))
            try:
                os.remove(json_path)
            except OSError:
                pass
            folder_up = old_folder
            for _ in range(3):
                try:
                    os.rmdir(folder_up)
                except OSError:
                    break
                folder_up = os.path.dirname(folder_up)
            moved += 1
        return moved

    def export_cameras(self, rows):
        """Schreibt die Kameratabelle als kameras.json (nur wenn sich etwas geaendert hat)."""
        data = [
            {
                "empfangs_email": r["address"],
                "klarname": r["name"] or "",
                "revier": r["revier"] or "",
                "erste_mail": r["first_seen"],
                "letzte_mail": r["last_seen"],
                "mails": r["mails"],
            }
            for r in rows
        ]
        payload = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        if payload == self._cameras_written:
            return
        os.makedirs(self.base, exist_ok=True)
        self._write(os.path.join(self.base, CAMERAS_FILE), payload)
        self._cameras_written = payload

    @staticmethod
    def _write(path, data):
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    # -- Speicherlimit (aelteste zuerst) -------------------------------

    def _files(self):
        """Alle abgelegten Dateien: (Pfad, Groesse)."""
        result = []
        if not os.path.isdir(self.base):
            return result
        for root, _dirs, names in os.walk(self.base):
            for name in names:
                if name.endswith(".tmp") or name.startswith(".") or name == CAMERAS_FILE:
                    continue
                path = os.path.join(root, name)
                try:
                    result.append((path, os.path.getsize(path)))
                except OSError:
                    continue
        return result

    def usage(self):
        with self.lock:
            if self.used is None:
                self.used = sum(size for _path, size in self._files())
            return self.used

    @staticmethod
    def _mail_key(path):
        """Gruppiert Fotos und .json einer Mail; sortiert nach Aufnahmezeit."""
        folder, name = os.path.split(path)
        stem = re.sub(r"_\d+$", "", name.rsplit(".", 1)[0])
        found = STAMP.search(name)
        return (found.group(0) if found else "0000"), os.path.join(folder, stem)

    def enforce_limit(self):
        """Speicher voll -> aelteste Bilder zuerst loeschen (first in, first out).

        Geloescht wird immer eine ganze Mail (Fotos + .json), bis wieder Platz ist.
        Gibt (geloeschte Mails, freigegebene Bytes) zurueck."""
        with self.lock:
            if self.used is not None and self.used <= self.max_bytes:
                return 0, 0
            files = self._files()
            self.used = sum(size for _path, size in files)
            if self.used <= self.max_bytes:
                return 0, 0
            groups = {}
            for path, size in files:
                stamp, stem = self._mail_key(path)
                groups.setdefault((stamp, stem), []).append((path, size))
            mails = freed = 0
            for key in sorted(groups):
                if self.used <= self.max_bytes:
                    break
                for path, size in groups[key]:
                    try:
                        os.remove(path)
                        self.used -= size
                        freed += size
                    except OSError:
                        pass
                mails += 1
                folder = os.path.dirname(key[1])
                for _ in range(3):  # Tag, Kamera, Revier - leere Ordner aufraeumen
                    try:
                        os.rmdir(folder)
                    except OSError:
                        break
                    folder = os.path.dirname(folder)
            return mails, freed

    # -- Aufbewahrungsdauer -------------------------------------------

    def purge(self, now=None):
        """Loescht Tagesordner, die aelter als die Aufbewahrungsdauer sind."""
        now = time.time() if now is None else now
        limit = time.strftime("%Y-%m-%d", time.localtime(now - self.days * 86400))
        removed = 0
        if not os.path.isdir(self.base):
            return 0
        for revier in os.listdir(self.base):
            revier_dir = os.path.join(self.base, revier)
            if not os.path.isdir(revier_dir):
                continue
            for camera in os.listdir(revier_dir):
                cam_dir = os.path.join(revier_dir, camera)
                if not os.path.isdir(cam_dir):
                    continue
                for day in os.listdir(cam_dir):
                    if DAY_DIR.match(day) and day < limit:
                        shutil.rmtree(os.path.join(cam_dir, day), ignore_errors=True)
                        removed += 1
                try:
                    if not os.listdir(cam_dir):
                        os.rmdir(cam_dir)
                except OSError:
                    pass
            try:
                if not os.listdir(revier_dir):
                    os.rmdir(revier_dir)
            except OSError:
                pass
        if removed:
            with self.lock:
                self.used = None
        return removed
