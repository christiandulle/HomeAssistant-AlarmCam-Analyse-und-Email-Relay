"""Videos auswerten: Standbilder herausschneiden und das beste auswaehlen (reine Logik, ohne Datenbank)."""
import email
import email.policy
import io
import logging
from email.parser import BytesParser

log = logging.getLogger("relay")

# Zeitpunkte der Standbilder (Sekunden): die ersten 10 s alle 2 s, danach alle 4 s bis 30 s
SAMPLE_SECONDS = (0, 2, 4, 6, 8, 10, 14, 18, 22, 26, 30)
SHARP_BAND = 0.05   # liegen Frames bei der Sicherheit so nah beieinander, entscheidet die Schaerfe
VIDEO_EXT = {"video/mp4": "mp4", "video/quicktime": "mov", "video/x-msvideo": "avi", "video/x-matroska": "mkv",
             "video/webm": "webm", "video/mpeg": "mpg", "video/3gpp": "3gp"}
VIDEO_TYPE = {"mp4": "video/mp4", "mov": "video/quicktime", "avi": "video/x-msvideo", "mkv": "video/x-matroska",
              "webm": "video/webm", "mpg": "video/mpeg", "3gp": "video/3gpp"}


def video_part(content):
    """Erstes Video einer Mail: (Bytes, Dateiendung) oder None."""
    try:
        msg = BytesParser(policy=email.policy.default).parsebytes(content)
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype.startswith("video/"):
                data = part.get_payload(decode=True) or b""
                if data:
                    return data, VIDEO_EXT.get(ctype, "mp4")
    except Exception as exc:
        log.debug("Video konnte nicht gelesen werden: %r", exc)
    return None


def sample_times(duration):
    """Zeitpunkte (s) fuer ein Video der Laenge duration. Unbekannte Laenge: nur der Anfang."""
    if not duration or duration <= 0:
        return [0]
    out = [t for t in SAMPLE_SECONDS if t < duration - 0.05]
    return out or [0]


def extract_frames(data):
    """Schneidet aus einem Video die Standbilder aus: [(Sekunde, JPEG-Bytes)]. Fehler -> leere Liste."""
    try:
        import av
    except ImportError:
        log.warning("Videoauswertung nicht verfuegbar (Bibliothek av fehlt)")
        return []
    frames = []
    try:
        with av.open(io.BytesIO(data)) as container:
            stream = container.streams.video[0]
            duration = float(container.duration) / 1_000_000.0 if container.duration else 0.0
            if not duration and stream.duration and stream.time_base:
                duration = float(stream.duration * stream.time_base)
            for t in sample_times(duration):
                try:
                    container.seek(int(t * 1_000_000), any_frame=False, backward=True, stream=None)
                    got = None
                    for frame in container.decode(stream):
                        ft = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
                        if ft + 0.001 >= t or t == 0:
                            got = frame
                            break
                    if got is None:
                        continue
                    buf = io.BytesIO()
                    got.to_image().save(buf, "JPEG", quality=90)
                    frames.append((t, buf.getvalue()))
                except Exception as exc:
                    log.debug("Standbild bei %s s nicht lesbar: %r", t, exc)
    except Exception as exc:
        log.warning("Video nicht lesbar: %r", exc)
        return []
    return frames


def sharpness(jpeg, box=None):
    """Schaerfe (Varianz des Laplace-Filters) im Bereich box (0..1) oder im ganzen Bild. Nur zum Vergleichen
    von Bildern desselben Videos gedacht, der Absolutwert sagt wenig."""
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(jpeg)).convert("L")
        if box:
            w, h = img.size
            x1, y1, x2, y2 = box
            c = img.crop((int(x1 * w), int(y1 * h), max(int(x2 * w), int(x1 * w) + 8), max(int(y2 * h), int(y1 * h) + 8)))
            if c.size[0] >= 8 and c.size[1] >= 8:
                img = c
        img.thumbnail((256, 256))
        a = np.asarray(img, dtype="float32")
        if a.shape[0] < 3 or a.shape[1] < 3:
            return 0.0
        lap = 4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
        return float(lap.var())
    except Exception as exc:
        log.debug("Schaerfe nicht messbar: %r", exc)
        return 0.0


def score(res):
    """(Anzahl Klassen ueber der Schwelle, hoechste Sicherheit; ohne Treffer die hoechste unter der Schwelle)."""
    confs = res.get("confs") or {}
    if confs:
        return len(confs), max(confs.values())
    low = res.get("low") or {}
    return 0, max([v[0] for v in low.values()] or [0.0])


def _box_of(res):
    boxes = res.get("boxes") or []
    if boxes:
        b = max(boxes, key=lambda x: x[1])
        return tuple(b[2:6])
    low = res.get("low") or {}
    if low:
        k = max(low, key=lambda x: low[x][0])
        v = low[k]
        if v[3] - v[1] > 0.002 and v[4] - v[2] > 0.002:
            return tuple(v[1:5])
    return None


def pick_best(cands):
    """Waehlt den besten Kandidaten: {"t", "data", "res"}. Reihenfolge: meiste Klassen ueber der Schwelle, dann
    hoechste Sicherheit; liegen mehrere nahe beieinander (SHARP_BAND), gewinnt der schaerfste, bei Gleichstand der
    mittlere. Gibt (Kandidat, Schaerfe) zurueck."""
    if not cands:
        return None, 0.0
    keyed = [(score(c["res"]), c) for c in cands]
    (n, top), _ = max(keyed, key=lambda kc: kc[0])
    pool = [c for (cn, ct), c in keyed if cn == n and ct >= top - SHARP_BAND]
    if len(pool) == 1:
        return pool[0], 0.0
    sharp = [(sharpness(c["data"], _box_of(c["res"])), c) for c in pool]
    best_s = max(s for s, _ in sharp)
    top_pool = [c for s, c in sharp if s >= best_s * 0.98 - 1e-9]
    top_pool.sort(key=lambda c: c["t"])
    return top_pool[len(top_pool) // 2], best_s
