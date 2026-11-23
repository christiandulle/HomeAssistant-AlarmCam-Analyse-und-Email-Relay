"""Schwellen-Hilfe: aus den eigenen Labels der Favoriten die beste Schwelle je Klasse finden.

Grundlage ist je Foto die hoechste Sicherheit jeder Klasse (auch unter der Schwelle), die die Erkennung merkt.
Damit laesst sich fuer jede Schwelle genau nachrechnen, was gemeldet worden waere - ohne das Modell neu laufen zu lassen.
Das Label gilt pro Foto (Klasse da oder nicht); gezaehlt wird also pro Foto und Klasse.
"""

CLASSES = ("tier", "mensch", "fahrzeug")
COST_FP = 1                # Fehlalarm: etwas gemeldet, was nicht da ist
COST_MISS_ALONE = 2        # uebersehen, und auf dem Foto wird gar nichts gemeldet: Foto gilt als leer, Mail geht evtl. nicht raus
COST_MISS_WITH_OTHER = 0.5  # uebersehen, aber eine andere Klasse wird gemeldet (Person erkannt, Auto nicht): Mail geht trotzdem raus
MIN_PCT, MAX_PCT = 10, 95


def parse_low(raw):
    """\"tier:0.18,mensch:0.12\" -> {"tier": 18, "mensch": 12} (Prozent, ganze Zahlen)."""
    out = {}
    for part in (raw or "").split(","):
        name, _, val = part.partition(":")
        if name in CLASSES:
            try:
                out[name] = int(round(float(val) * 100))
            except ValueError:
                pass
    return out


def sample(truth, low_raw):
    """Ein Foto: (wahr, Sicherheit) je Klasse in der Reihenfolge CLASSES; fehlt eine Klasse, ist ihre Sicherheit 0."""
    conf = parse_low(low_raw)
    return (tuple(1 if c in truth else 0 for c in CLASSES), tuple(conf.get(c, 0) for c in CLASSES))


def errors(samples, k, pct):
    """(Fehlalarme, uebersehen) fuer Klasse k (Index) bei Schwelle pct (Prozent)."""
    fp = miss = 0
    for truth, conf in samples:
        found = conf[k] >= pct
        if found and not truth[k]:
            fp += 1
        elif truth[k] and not found:
            miss += 1
    return fp, miss


def photo_cost(truth, conf, thr):
    """Ein Foto bei den Schwellen thr (Prozent je Klasse): (Fehlalarme, uebersehen, Kosten).
    Ein uebersehenes Objekt kostet wenig, wenn auf dem Foto sonst etwas gemeldet wird (auch Falsches: die Mail geht raus),
    und viel, wenn das Foto dadurch als leer gilt."""
    found = [conf[k] >= thr[CLASSES[k]] for k in range(3)]
    any_found = any(found)
    fp = sum(1 for k in range(3) if found[k] and not truth[k])
    miss = sum(1 for k in range(3) if truth[k] and not found[k])
    return fp, miss, COST_FP * fp + miss * (COST_MISS_WITH_OTHER if any_found else COST_MISS_ALONE)


def total_cost(samples, thr):
    """Alle Fotos: (Fehlalarme, uebersehen, gewichtete Kosten)."""
    fp = miss = 0
    cost = 0.0
    for truth, conf in samples:
        a, b, c = photo_cost(truth, conf, thr)
        fp += a
        miss += b
        cost += c
    return fp, miss, cost


FWD_COST_NOT = 2   # nicht weitergeleitet, obwohl etwas da ist
FWD_COST_EMPTY = 1  # weitergeleitet, obwohl das Foto leer ist


def forwarding(samples, thr):
    """Weiterleitung bei den Schwellen thr: (nicht weitergeleitet, Fotos mit Inhalt, leer weitergeleitet, leere Fotos).
    Weitergeleitet wird, wenn mindestens eine Klasse gemeldet wird."""
    nf = lw = n_content = n_empty = 0
    for truth, conf in samples:
        found = any(conf[k] >= thr[CLASSES[k]] for k in range(3))
        if any(truth):
            n_content += 1
            nf += 0 if found else 1
        else:
            n_empty += 1
            lw += 1 if found else 0
    return nf, n_content, lw, n_empty


def photos_right(samples, thr):
    """Anzahl Fotos, bei denen alle drei Klassen stimmen, wenn die Schwellen thr (Prozent je Klasse) gelten."""
    return sum(1 for truth, conf in samples if photo_cost(truth, conf, thr)[:2] == (0, 0))


def _runs(costs):
    """Zusammenhaengende Strecken (Start-, Endindex) mit den geringsten Kosten."""
    low = min(costs)
    runs, start = [], None
    for i, c in enumerate(costs + [None]):
        if c == low and start is None:
            start = i
        elif c != low and start is not None:
            runs.append((start, i - 1))
            start = None
    return low, runs


def recommend(samples, cur):
    """Beste Schwellen (Prozent je Klasse) nach den eigenen Labels. Die Kosten einer Klasse haengen von den Schwellen der anderen ab
    (uebersehen ist billiger, wenn etwas anderes gemeldet wird); deshalb wird reihum verbessert, bis sich nichts mehr lohnt, und
    zuletzt jede Schwelle in die Mitte ihrer besten Strecke gesetzt (robuster gegen Ausreisser). Ohne Fotos bleibt cur."""
    thr = {c: min(MAX_PCT, max(MIN_PCT, int(cur.get(c, 30)))) for c in CLASSES}
    if not samples:
        return thr

    def slice_costs(c):
        # zuerst zaehlt die Weiterleitung (nicht weitergeleitet 2, leer weitergeleitet 1), bei Gleichstand die Klassen-Kosten
        t = dict(thr)
        out = []
        for pct in range(MIN_PCT, MAX_PCT + 1):
            t[c] = pct
            nf, _n, lw, _e = forwarding(samples, t)
            out.append((FWD_COST_NOT * nf + FWD_COST_EMPTY * lw, total_cost(samples, t)[2]))
        return out

    for _ in range(12):
        changed = False
        for c in CLASSES:
            costs = slice_costs(c)
            low, runs = _runs(costs)
            if costs[thr[c] - MIN_PCT] > low:   # lohnt sich: laengste beste Strecke, davon die Mitte
                a, b = max(runs, key=lambda r: r[1] - r[0])
                thr[c] = MIN_PCT + (a + b) // 2
                changed = True
        if not changed:
            break
    for c in CLASSES:   # Mitte der besten Strecke, in der die Schwelle schon liegt (Kosten bleiben gleich)
        costs = slice_costs(c)
        low, runs = _runs(costs)
        for a, b in runs:
            if a <= thr[c] - MIN_PCT <= b:
                thr[c] = MIN_PCT + (a + b) // 2
                break
    return thr
