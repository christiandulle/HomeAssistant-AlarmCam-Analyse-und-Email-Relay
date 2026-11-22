"""Lokale, einfache Bilderkennung (MegaDetector V6) fuer die Kamerabilder.

Liefert nur eine grobe Einordnung je Foto: Mensch, Tier, Fahrzeug oder leer.
Das Modell ist auf Wildkamerabilder trainiert (Klassen: animal, person, vehicle)
und erkennt Wild, auch bei Infrarot-Nachtaufnahmen. Eine Artbestimmung findet
bewusst nicht statt.
"""
import io
import logging
import os
import queue
import threading
import time

log = logging.getLogger("relay")

BUILTIN_PATH = os.environ.get("RELAY_DETECT_MODEL", "/app/models/megadetector_v6.onnx")
# Modellordner: Hauptordner = produktives Modell, test/ = Testmodell (nur Favoriten), vorher/ = zuletzt produktives Modell
MODEL_DIR = os.environ.get("RELAY_MODEL_DIR", "/share/kamera_relay")
TEST_DIR = os.path.join(MODEL_DIR, "test")
PREV_DIR = os.path.join(MODEL_DIR, "vorher")
MAX_UPLOAD = 600 * 1024 * 1024   # Obergrenze fuer den Upload im Board
DEFAULT_SIZE = 1280   # Eingabegroesse, falls das Modell keine feste angibt
NMS_IOU = 0.5
CLASS_LABELS = {0: "tier", 1: "mensch", 2: "fahrzeug"}
DEFAULT_THRESHOLDS = {"tier": 0.25, "mensch": 0.30, "fahrzeug": 0.50}
ORDER = ("mensch", "tier", "fahrzeug")   # Reihenfolge der Anzeige
BASE_TAG = "megadetector-v6-4"   # Kennung des eingebauten Modells
PRIORITY = {"mensch": 3, "tier": 2, "fahrzeug": 1, "leer": 0}
LOW_FLOOR = 0.10   # darunter wird nichts mehr gemerkt (kleinste einstellbare Schwelle)
THR_FILE = os.path.join(MODEL_DIR, "schwellen.json")   # Schwellen je Modell (Reiter Modelle), gilt vor der Add-on-Konfiguration
CFG_THRESHOLDS = dict(DEFAULT_THRESHOLDS)               # Standard aus der Add-on-Konfiguration (setzt der Worker)


def thr_key(tag):
    """Schluessel eines Modells fuer die Schwellen: der Inhalts-Hash (Test und Produktiv derselben Datei teilen ihn)."""
    for pre in ("extern-", "test-"):
        if tag.startswith(pre):
            return tag[len(pre):]
    return tag or "eingebaut"


def _read_thr():
    import json
    try:
        with open(THR_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def thresholds_for(tag, default=None):
    """Schwellen eines Modells: eigene Einstellung (Reiter Modelle), sonst die Add-on-Konfiguration."""
    th = dict(default if isinstance(default, dict) else CFG_THRESHOLDS)
    own = _read_thr().get(thr_key(tag))
    if isinstance(own, dict):
        for k in DEFAULT_THRESHOLDS:
            try:
                th[k] = min(0.95, max(LOW_FLOOR, float(own[k])))
            except (KeyError, TypeError, ValueError):
                pass
    return th


def has_own_thresholds(tag):
    return isinstance(_read_thr().get(thr_key(tag)), dict)


def save_thresholds(tag, th):
    """Schwellen eines Modells dauerhaft speichern (Werte 0.10 bis 0.95)."""
    import json
    data = _read_thr()
    data[thr_key(tag)] = {k: round(min(0.95, max(LOW_FLOOR, float(th[k]))), 2) for k in DEFAULT_THRESHOLDS}
    os.makedirs(os.path.dirname(THR_FILE), exist_ok=True)
    tmp = THR_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, THR_FILE)


_tag_cache = {}
BAD = {}   # Kennung -> Fehlertext fuer Modelldateien, die sich nicht laden lassen


def list_models(folder):
    """*.onnx-Dateien eines Ordners, zuletzt geaenderte zuerst."""
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".onnx") and not n.startswith(".")]
    except OSError:
        return []
    found = []
    for n in names:
        path = os.path.join(folder, n)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if os.path.isfile(path):
            found.append((max(st.st_ctime, st.st_mtime), path))
    return [p for _, p in sorted(found, reverse=True)]


def _file_tag(path):
    """Kennung eines eigenen Modells aus dem Dateiinhalt (gleicher Inhalt = gleiche Kennung, egal wie die Datei heisst)."""
    import hashlib
    try:
        st = os.stat(path)
    except OSError:
        return "extern-unbekannt"
    key = (path, st.st_size, st.st_mtime_ns)
    if key in _tag_cache:
        return _tag_cache[key]
    try:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        tag = "extern-" + h.hexdigest()[:12]
    except OSError:
        return "extern-unbekannt"
    _tag_cache[key] = tag
    return tag


MODEL_PATH, MODEL_TAG = BUILTIN_PATH, BASE_TAG
TEST_PATH, TEST_TAG = "", ""


def refresh():
    """Liest die Ordner neu: produktives Modell (Hauptordner, sonst eingebaut) und Testmodell (Ordner test)."""
    global MODEL_PATH, MODEL_TAG, TEST_PATH, TEST_TAG
    path, tag = BUILTIN_PATH, BASE_TAG
    for f in list_models(MODEL_DIR):
        t = _file_tag(f)
        if t not in BAD:
            path, tag = f, t
            break
    MODEL_PATH, MODEL_TAG = path, tag
    tp, tt = "", ""
    for f in list_models(TEST_DIR):
        tp, tt = f, "test-" + _file_tag(f)[7:]
        break
    TEST_PATH, TEST_TAG = tp, tt


refresh()


def rss_mb():
    """Arbeitsspeicher des Add-on-Prozesses in MB (aus /proc; 0, wenn nicht lesbar)."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def available():
    return os.path.isfile(MODEL_PATH) or os.path.isfile(BUILTIN_PATH)


class Detector:
    def __init__(self, thresholds=None, model_path=None, threads=2):
        import numpy as np
        import onnxruntime as ort
        self.np = np
        th = dict(DEFAULT_THRESHOLDS)
        if isinstance(thresholds, dict):
            th.update(thresholds)
        elif thresholds is not None:       # eine Zahl gilt fuer alle Klassen
            th = {k: float(thresholds) for k in th}
        self.set_thresholds(th)
        self.threads = max(1, int(threads))
        self.rss0 = rss_mb()
        self.load_mb = 0.0   # Zuwachs des Arbeitsspeichers durch das Modell (inkl. Probelauf)
        self.peak_mb = 0.0   # hoechster Zuwachs waehrend der Auswertung
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = int(threads)
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(model_path or MODEL_PATH, opts,
                                            providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.input = inp.name
        shp = inp.shape
        self.size = int(shp[2]) if len(shp) == 4 and isinstance(shp[2], int) and shp[2] > 0 else DEFAULT_SIZE
        oshp = self.session.get_outputs()[0].shape
        if len(oshp) != 3 or (isinstance(oshp[1], int) and oshp[1] != 7):
            raise ValueError("Modell-Ausgabe %s nicht unterstuetzt (erwartet [1, 7, N]: 4 Box-Werte + 3 Klassen)" % (oshp,))
        self.lock = threading.Lock()
        try:   # Probelauf: der erste Durchlauf ist langsamer und belegt den Arbeitsspeicher; er soll die Messung nicht verfaelschen
            self.session.run(None, {self.input: np.full((1, 3, self.size, self.size), 0.45, dtype="float32")})
        except Exception:
            pass
        self.load_mb = max(0.0, rss_mb() - self.rss0)
        self.peak_mb = self.load_mb

    def set_thresholds(self, th):
        """Schwellen setzen (gilt ab dem naechsten Foto)."""
        self.thresholds = dict(th)
        self.thr = self.np.array([th[CLASS_LABELS[i]] for i in range(3)], dtype="float32")

    def _prepare(self, data):
        from PIL import Image
        np = self.np
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        size = self.size
        scale = min(size / w, size / h)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        canvas = Image.new("RGB", (size, size), (114, 114, 114))
        canvas.paste(img.resize((nw, nh), Image.BILINEAR), ((size - nw) // 2, (size - nh) // 2))
        arr = np.asarray(canvas, dtype="float32").transpose(2, 0, 1)[None] / 255.0
        return arr, ((size - nw) // 2, (size - nh) // 2, nw, nh)

    def _nms(self, boxes, scores):
        np = self.np
        order = scores.argsort()[::-1]
        keep = []
        while order.size:
            i = order[0]
            keep.append(i)
            if order.size == 1:
                break
            rest = order[1:]
            x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
            y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
            x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
            y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
            inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
            area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
            area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
            iou = inter / np.maximum(area_i + area_r - inter, 1e-6)
            order = rest[iou < NMS_IOU]
        return keep

    def analyse(self, data):
        """Gibt {"confs": {"mensch": 0.9, ...}, "n"} zurueck; confs ist leer, wenn nichts gefunden wurde."""
        np = self.np
        t0 = time.perf_counter()
        blob, (padx, pady, nw, nh) = self._prepare(data)
        with self.lock:
            out = self.session.run(None, {self.input: blob})[0][0].T   # [Anker, 4 + Klassen]
        self.peak_mb = max(self.peak_mb, rss_mb() - self.rss0)
        cls = out[:, 4:7]
        cid = cls.argmax(1)
        score = cls.max(1)

        def norm(b):   # Mittelpunkt/Breite/Hoehe im Modellbild -> Ecken im Originalbild (0..1), Auffuellrand abgezogen
            x1, x2 = ((b[0] - b[2] / 2 - padx) / nw, (b[0] + b[2] / 2 - padx) / nw)
            y1, y2 = ((b[1] - b[3] / 2 - pady) / nh, (b[1] + b[3] / 2 - pady) / nh)
            return tuple(min(1.0, max(0.0, float(v))) for v in (x1, y1, x2, y2))

        low = {}   # je Klasse die sicherste Erkennung, auch unter der Schwelle: {"tier": (0.18, x1, y1, x2, y2)}
        for c in range(3):
            idx = np.flatnonzero((cid == c) & (score >= LOW_FLOOR))
            if idx.size:
                j = idx[score[idx].argmax()]
                low[CLASS_LABELS[c]] = (round(float(score[j]), 2),) + norm(out[j, :4])
        mask = score >= self.thr[cid]
        if not mask.any():
            return {"confs": {}, "n": 0, "boxes": [], "low": low, "ms": round((time.perf_counter() - t0) * 1000)}
        o = out[mask]
        boxes = np.stack((o[:, 0] - o[:, 2] / 2, o[:, 1] - o[:, 3] / 2,
                          o[:, 0] + o[:, 2] / 2, o[:, 1] + o[:, 3] / 2), 1)
        keep = self._nms(boxes, score[mask])
        ids, sc = cid[mask][keep], score[mask][keep]
        confs, found = {}, []
        kb = boxes[keep]
        for i, c, b in zip(ids, sc, kb):
            name = CLASS_LABELS[int(i)]
            confs[name] = max(confs.get(name, 0.0), round(float(c), 2))
            # Rahmen auf das Originalbild umrechnen (0..1), Auffuellrand abziehen
            x1, x2 = ((b[0] - padx) / nw, (b[2] - padx) / nw)
            y1, y2 = ((b[1] - pady) / nh, (b[3] - pady) / nh)
            x1, y1, x2, y2 = (min(1.0, max(0.0, float(v))) for v in (x1, y1, x2, y2))
            if x2 - x1 > 0.002 and y2 - y1 > 0.002:
                found.append((name, round(float(c), 2), x1, y1, x2, y2))
        return {"confs": confs, "n": len(keep), "boxes": found, "low": low,
                "ms": round((time.perf_counter() - t0) * 1000)}


def encode_boxes(boxes):
    """[(name, conf, x1, y1, x2, y2)] -> "tier:0.91:0.123:0.2:0.4:0.5;..." (Koordinaten 0..1)"""
    return ";".join("%s:%.2f:%.3f:%.3f:%.3f:%.3f" % b for b in boxes)


def encode_low(low):
    """{"tier": (0.18, x1, y1, x2, y2), ...} -> ("tier:0.18", "tier:0.18:x1:y1:x2:y2")  (Sicherheit je Klasse, Rahmen)"""
    names = [k for k in ORDER if k in low]
    return (",".join("%s:%.2f" % (k, low[k][0]) for k in names),
            encode_boxes([(k,) + tuple(low[k]) for k in names if low[k][3] - low[k][1] > 0.002 and low[k][4] - low[k][2] > 0.002]))


def encode(confs):
    """{"mensch": .9, "tier": .8} -> ("mensch,tier", "mensch:0.9,tier:0.8", beste Sicherheit)"""
    names = [k for k in ORDER if k in confs]
    if not names:
        return "leer", "", 0.0
    return ",".join(names), ",".join("%s:%.2f" % (k, confs[k]) for k in names), max(confs.values())


def validate_model(path):
    """Prueft eine .onnx-Datei: ladbar, Ausgabeformat passt, Probebild laeuft. Gibt (ok, Meldung, Eingabegroesse) zurueck."""
    try:
        from PIL import Image
        det = Detector(None, model_path=path, threads=1)
        buf = io.BytesIO()
        Image.new("RGB", (640, 360), (110, 110, 110)).save(buf, "JPEG")
        det.analyse(buf.getvalue())
        return True, "", det.size
    except Exception as exc:
        return False, str(exc)[:300], 0


class DetectWorker(threading.Thread):
    """Arbeitet neue Mails im Hintergrund ab, damit der Mail-Empfang nie wartet."""

    MAX_IMAGES = 6
    CHECK_SECONDS = 30        # so oft schaut das Add-on in die Modellordner
    TEST_IDLE_SECONDS = 120   # danach wird das Testmodell aus dem Speicher genommen

    def __init__(self, store, thresholds=None, backlog=300, threads=1, filter_mode="aus", wake=None):
        super().__init__(name="erkennung", daemon=True)
        import itertools
        self.store = store
        self.thresholds = thresholds
        global CFG_THRESHOLDS
        if isinstance(thresholds, dict):
            CFG_THRESHOLDS = dict(DEFAULT_THRESHOLDS, **thresholds)
        self.backlog = backlog
        self.threads = max(1, int(threads))
        self.queue = queue.PriorityQueue()   # neue Mails (0) vor dem Bestand (1) vor der manuellen Neu-Pruefung (2)
        self.seq = itertools.count()
        self.filter_mode = filter_mode
        self.wake = wake
        self.detector = None
        self.loaded_tag = ""
        self.test_detector = None
        self.test_loaded_tag = ""
        self.test_failed = False
        self.test_error = ""
        self.test_used = 0.0
        self.fav_gen = 0     # Nummer des laufenden Favoriten-Tests (veraltete Auftraege in der Warteschlange werden uebersprungen)
        self.favtest = {"active": False, "total": 0, "done": 0, "started": 0.0, "finished": 0.0, "cur": 0,
                        "loading": False, "ms_sum": 0, "n": 0, "cancelled": False, "manual": False, "tag": ""}
        self.mem = {}        # Modellkennung -> {"threads", "load_mb", "peak_mb"} (bleibt auch nach dem Entladen)
        self.last_ms = 0     # mittlere Zeit pro Foto der zuletzt ausgewerteten Mail
        self.recheck = {"active": False, "total": 0, "done": 0, "cancel": False, "started": 0.0, "finished": 0.0}
        self.ok = True

    def submit(self, mail_id):
        self.queue.put((0, next(self.seq), mail_id, "det"))

    def submit_test(self, mail_id):
        """Neuer Favorit: mit dem Testmodell pruefen (falls ein Testmodell im Ordner test liegt)."""
        if TEST_TAG and not self.test_failed:
            self.queue.put((0, next(self.seq), mail_id, "test"))

    def check(self):
        """Modellordner sofort neu lesen (statt auf die naechste 30-Sekunden-Runde zu warten)."""
        self.queue.put((0, next(self.seq), 0, "check"))

    def favtest_start(self, manual=True):
        """Alle Favoriten mit dem Testmodell (neu) auswerten. Gibt die Anzahl zurueck (0 = nichts zu tun oder laeuft schon)."""
        if not TEST_TAG or self.test_failed or self.favtest["active"]:
            return 0
        ids = self.store.all_fav_ids() if manual else self.store.pending_test(TEST_TAG)
        self.fav_gen += 1
        self.favtest.update(active=bool(ids), total=len(ids), done=0, started=time.time(), finished=0.0, cur=0,
                            loading=False, ms_sum=0, n=0, cancelled=False, manual=manual, tag=TEST_TAG)
        for mail_id in ids:
            self.queue.put((1, next(self.seq), mail_id, "favrun:%d" % self.fav_gen))
        if ids:
            log.info("Testmodell %s: %d Favorit(en) werden geprueft", os.path.basename(TEST_PATH), len(ids))
        return len(ids)

    def favtest_cancel(self):
        f = self.favtest
        if f["active"]:
            self.fav_gen += 1   # offene Auftraege werden damit uebersprungen
            f.update(active=False, cancelled=True, finished=time.time(), loading=False, cur=0)
            log.info("Testmodell: Favoriten-Test abgebrochen (%d von %d)", f["done"], f["total"])

    def recheck_start(self, favorites_only=False):
        """Manuell: alle Fotos im Bestand (oder nur die Favoriten) mit dem produktiven Modell neu auswerten. Gibt die Anzahl zurueck."""
        if self.recheck["active"]:
            return 0
        ids = self.store.all_fav_ids() if favorites_only else self.store.all_image_ids()
        self.recheck.update(active=bool(ids), total=len(ids), done=0, cancel=False, started=time.time())
        for mail_id in ids:
            self.queue.put((2, next(self.seq), mail_id, "recheck"))
        if ids:
            log.info("Bestand neu pruefen: %d Foto(s) werden mit dem produktiven Modell ausgewertet", len(ids))
        return len(ids)

    def set_thresholds(self, which, th):
        """Schwellen eines Modells speichern ("det" = produktiv, "test") und beim geladenen Modell sofort setzen."""
        tag = MODEL_TAG if which == "det" else TEST_TAG
        if not tag:
            return False
        save_thresholds(tag, th)
        det = self.detector if which == "det" else self.test_detector
        if det is not None:
            det.set_thresholds(thresholds_for(tag, self.thresholds))
        return True

    def recheck_cancel(self):
        if self.recheck["active"]:
            self.recheck["cancel"] = True

    def status(self):
        return {
            "ok": self.ok, "prod_path": MODEL_PATH, "prod_tag": MODEL_TAG, "builtin": MODEL_PATH == BUILTIN_PATH,
            "size": getattr(self.detector, "size", 0), "test_path": TEST_PATH, "test_tag": TEST_TAG,
            "test_error": self.test_error, "test_loaded": self.test_detector is not None,
            "recheck": dict(self.recheck), "favtest": dict(self.favtest), "mem": dict(self.mem), "threads": self.threads,
            "bad": [(os.path.basename(f), BAD[_file_tag(f)]) for f in list_models(MODEL_DIR) if _file_tag(f) in BAD],
        }

    def _release(self, mail_id, label):
        """Filter "an": Mail ohne Erkennung bleibt gespeichert, alles andere wird weitergeleitet."""
        if self.filter_mode != "an":
            return
        forward = label != "leer"   # "fehler" und alles Unklare wird weitergeleitet
        if not self.store.release_held(mail_id, forward):
            return   # Mail wartete nicht (z. B. aus dem Bestand)
        if forward:
            if self.wake:
                self.wake()
        else:
            log.info("[#%d] Bilderkennung: nichts erkannt - nicht weitergeleitet (im Board unter 'Ohne Erkennung')", mail_id)

    def _load_prod(self):
        """Laedt das produktive Modell. Eine nicht ladbare Datei im Hauptordner wird gemerkt und uebergangen."""
        while True:
            try:
                self.detector = Detector(thresholds_for(MODEL_TAG, self.thresholds), model_path=MODEL_PATH, threads=self.threads)
                self.loaded_tag = MODEL_TAG
                return
            except Exception as exc:
                if MODEL_PATH == BUILTIN_PATH:
                    raise
                BAD[MODEL_TAG] = str(exc)[:200]
                log.warning("Modell %s nicht ladbar (%r) - es wird uebergangen", MODEL_PATH, exc)
                refresh()

    def _load(self):
        try:
            self._load_prod()
            th = self.detector.thresholds
            log.info("Bilderkennung bereit (%s, %d px, Schwellen: Tier %d %%, Mensch %d %%, Fahrzeug %d %%, "
                     "%d Thread(s), niedrige Prioritaet)",
                     "Modell " + MODEL_PATH if MODEL_PATH != BUILTIN_PATH else "MegaDetector V6 eingebaut",
                     self.detector.size,
                     round(th["tier"] * 100), round(th["mensch"] * 100), round(th["fahrzeug"] * 100), self.threads)
        except Exception as exc:  # fehlendes Modell, fehlende Bibliothek ...
            self.ok = False
            log.error("Bilderkennung nicht verfuegbar: %r", exc)

    def _load_test(self):
        try:
            self.test_detector = Detector(thresholds_for(TEST_TAG, self.thresholds), model_path=TEST_PATH, threads=self.threads)
            self.test_error = ""
            log.info("Testmodell bereit (%s, %d px): wertet nur Favoriten aus", TEST_PATH, self.test_detector.size)
        except Exception as exc:
            self.test_failed = True
            self.test_error = str(exc)[:200]
            log.warning("Testmodell %s nicht ladbar - es wird ignoriert: %r", TEST_PATH, exc)

    def _check_models(self):
        """Modellordner lesen: neues produktives Modell oder neues Testmodell werden ohne Neustart uebernommen."""
        refresh()
        if self.ok and self.detector is not None and MODEL_TAG != self.loaded_tag:
            try:
                self._load_prod()
                log.info("Produktives Modell gewechselt: %s (%d px)",
                         "eingebaut" if MODEL_PATH == BUILTIN_PATH else os.path.basename(MODEL_PATH), self.detector.size)
                # nur Favoriten werden neu ausgewertet (wenn das Testmodell schon gerechnet hat, ist das Ergebnis schon uebernommen)
                for mail_id in self.store.pending_detection(0, MODEL_TAG, favorites_only=True):
                    self.queue.put((0, next(self.seq), mail_id, "det"))
            except Exception as exc:
                log.error("Wechsel des produktiven Modells fehlgeschlagen: %r", exc)
        if TEST_TAG != self.test_loaded_tag:
            self.test_detector = None
            self.test_failed = False
            self.test_error = ""
            self.test_loaded_tag = TEST_TAG
            self.fav_gen += 1   # ein laufender Test mit dem alten Modell ist damit beendet
            self.favtest.update(active=False, loading=False, cur=0)
            if TEST_TAG:
                self.favtest_start(manual=False)
            else:
                log.info("Testmodell entfernt")

    def _idle(self):
        self._check_models()
        if self.test_detector is not None and time.time() - self.test_used > self.TEST_IDLE_SECONDS:
            self.test_detector = None   # Speicher wieder freigeben
            import gc
            gc.collect()

    def _remember(self, tag, detector):
        self.mem[tag] = {"threads": detector.threads, "load_mb": round(detector.load_mb),
                         "peak_mb": round(detector.peak_mb), "size": detector.size}

    def _analyse_all(self, detector, mail_id, images):
        """Wertet alle Fotos einer Mail aus: (Klassen, Fotos, Treffer, Rahmen, Sicherheiten unter Schwelle, Rahmen dazu)."""
        merged, done, total, first_boxes, ms = {}, 0, 0, "", 0
        lowc, first_low = {}, {}
        for idx, data in enumerate(images):
            try:
                res = detector.analyse(data)
            except Exception as exc:
                log.debug("Bild in Mail %s nicht auswertbar: %r", mail_id, exc)
                continue
            done += 1
            ms += res.get("ms", 0)
            total += res["n"]
            if idx == 0:   # Rahmen nur fuers erste Foto (das Vorschaubild im Board)
                first_boxes = encode_boxes(res.get("boxes", []))
                first_low = res.get("low", {})
            for k, v in res["confs"].items():
                merged[k] = max(merged.get(k, 0.0), v)
            for k, v in res.get("low", {}).items():
                lowc[k] = max(lowc.get(k, 0.0), v[0])
        self.last_ms = round(ms / done) if done else 0
        low = {}
        for k, c in lowc.items():   # Sicherheit: bestes Foto der Mail; Rahmen nur, wenn er aus dem ersten Foto stammt
            low[k] = first_low[k] if k in first_low and first_low[k][0] >= c else (c, 0, 0, 0, 0)
        return merged, done, total, first_boxes, low

    def process_test(self, mail_id):
        """Wertet einen Favoriten mit dem Testmodell aus. Gibt die Zeit pro Foto in ms zurueck (0 = nichts gemessen)."""
        if not TEST_TAG or self.test_failed:
            return 0
        if self.test_detector is None:
            self.favtest["loading"] = True
            try:
                self._load_test()
            finally:
                self.favtest["loading"] = False
            if self.test_detector is None:
                return 0
        self.test_used = time.time()
        images = self.store.mail_images(mail_id, self.MAX_IMAGES)
        merged, done, total, first_boxes, low = self._analyse_all(self.test_detector, mail_id, images) if images else ({}, 0, 0, "", {})
        if not done:
            self.store.set_test(mail_id, "fehler", "", "", TEST_TAG)
            return 0
        label, confs, _best = encode(merged)
        lowc, lowb = encode_low(low)
        self.store.set_test(mail_id, label, confs, first_boxes, TEST_TAG, ms=self.last_ms, low=lowc, lowb=lowb)
        self._remember(TEST_TAG, self.test_detector)
        return self.last_ms

    def process(self, mail_id):
        images = self.store.mail_images(mail_id, self.MAX_IMAGES)
        if not images:
            if self.store.detection_label(mail_id):   # Fotos nicht mehr da: altes Ergebnis behalten
                self.store.set_detection_model(mail_id, MODEL_TAG)
            else:
                self.store.set_detection(mail_id, "leer", 0.0, 0, MODEL_TAG)
            self._release(mail_id, "fehler")   # nichts auswertbar: nie wegen fehlender Fotos filtern
            return
        merged, done, total, first_boxes, low = self._analyse_all(self.detector, mail_id, images)
        if not done:
            self.store.set_detection(mail_id, "fehler", 0.0, 0, MODEL_TAG)
            self._release(mail_id, "fehler")
            return
        label, confs, best = encode(merged)
        lowc, lowb = encode_low(low)
        self.store.set_detection(mail_id, label, best, done, MODEL_TAG, confs, first_boxes, ms=self.last_ms, low=lowc, lowb=lowb)
        self._remember(self.loaded_tag or MODEL_TAG, self.detector)
        self._release(mail_id, label)

    def run(self):
        # Niedrigste Prioritaet: Home Assistant und Automationen bekommen die CPU zuerst.
        # Vor dem Laden des Modells setzen, damit auch dessen Rechen-Threads sie erben.
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
        except (OSError, AttributeError) as exc:
            log.warning("Prioritaet der Bilderkennung nicht setzbar: %r", exc)
        self._load()
        if not self.ok:
            return
        self._check_models()   # Testmodell vorhanden? Dann zuerst die Favoriten damit pruefen
        # Bestand: nur Mails ganz ohne Ergebnis (neueste zuerst) und Favoriten mit anderem Modell
        pending = self.store.pending_detection(self.backlog, MODEL_TAG)
        for mail_id in pending:
            self.queue.put((1, next(self.seq), mail_id, "det"))
        total, done, started = len(pending), 0, time.time()
        log.info("Bilderkennung: %d Mail(s) aus dem Bestand werden geprueft", total)
        while True:
            try:
                prio, _, mail_id, kind = self.queue.get(timeout=self.CHECK_SECONDS)
            except queue.Empty:
                self._idle()
                continue
            if kind == "check":
                self._check_models()
                continue
            if kind == "test":   # neuer Favorit
                try:
                    self.process_test(mail_id)
                except Exception as exc:
                    log.warning("Testmodell fuer Mail %s fehlgeschlagen: %r", mail_id, exc)
                continue
            if kind.startswith("favrun:"):
                f = self.favtest
                if int(kind[7:]) != self.fav_gen or not f["active"]:
                    continue   # veralteter Auftrag (Abbruch oder neues Testmodell)
                f["cur"] = mail_id
                ms = 0
                try:
                    ms = self.process_test(mail_id)
                except Exception as exc:
                    log.warning("Testmodell fuer Mail %s fehlgeschlagen: %r", mail_id, exc)
                if int(kind[7:]) != self.fav_gen:
                    continue   # waehrend der Auswertung abgebrochen
                f["done"] += 1
                if ms:
                    f["ms_sum"] += ms
                    f["n"] += 1
                if f["done"] >= f["total"]:
                    f.update(active=False, finished=time.time(), cur=0)
                    log.info("Testmodell: Favoriten fertig (%d s)", round(f["finished"] - f["started"]))
                continue
            if kind == "recheck":
                r = self.recheck
                if not r["cancel"]:
                    try:
                        self.process(mail_id)
                    except Exception as exc:
                        log.debug("Neu-Pruefung Mail %s fehlgeschlagen: %r", mail_id, exc)
                r["done"] += 1
                if r["done"] >= r["total"]:
                    log.info("Bestand neu pruefen: %s (%d s)", "abgebrochen" if r["cancel"] else "fertig",
                             round(time.time() - r["started"]))
                    r.update(active=False, cancel=False, finished=time.time())
                continue
            try:
                self.process(mail_id)
            except Exception as exc:
                log.warning("Erkennung fuer Mail %s fehlgeschlagen: %r", mail_id, exc)
                try:
                    self.store.set_detection(mail_id, "fehler", 0.0, 0, MODEL_TAG)
                    self._release(mail_id, "fehler")
                except Exception:
                    pass
            if prio == 1 and done < total:
                done += 1
                if done % 25 == 0 and done < total:
                    log.info("Bilderkennung: %d von %d Mails aus dem Bestand geprueft", done, total)
                elif done == total:
                    log.info("Bilderkennung: Bestand fertig (%d Mails, %d s)", total, round(time.time() - started))
