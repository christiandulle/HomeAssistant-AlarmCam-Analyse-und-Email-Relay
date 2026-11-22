"""Schwellen-Hilfe: aus den eigenen Labels der Favoriten die beste Schwelle je Klasse finden.

Grundlage ist je Foto die hoechste Sicherheit jeder Klasse (auch unter der Schwelle), die die Erkennung merkt.
Damit laesst sich fuer jede Schwelle genau nachrechnen, was gemeldet worden waere - ohne das Modell neu laufen zu lassen.
Das Label gilt pro Foto (Klasse da oder nicht); gezaehlt wird also pro Foto und Klasse.
"""

CLASSES = ("tier", "mensch", "fahrzeug")
COST_FP = 1      # Fehlalarm: etwas gemeldet, was nicht da ist
COST_MISS = 2    # uebersehen zaehlt doppelt (so von Christian festgelegt)
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


def cost(fp, miss):
    return COST_FP * fp + COST_MISS * miss


def best(samples, k, current=None):
    """Beste Schwelle fuer Klasse k: geringste Kosten; bei mehreren gleich guten die in der Mitte der laengsten gleich guten Strecke.
    Gibt (Schwelle, Fehlalarme, uebersehen) zurueck oder None ohne Fotos."""
    if not samples:
        return None
    table = []
    for pct in range(MIN_PCT, MAX_PCT + 1):
        fp, miss = errors(samples, k, pct)
        table.append((pct, fp, miss, cost(fp, miss)))
    low = min(t[3] for t in table)
    runs, run = [], []
    for t in table:
        if t[3] == low:
            run.append(t)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    longest = max(runs, key=len)   # bei gleicher Laenge die erste (niedrigere Schwelle: lieber empfindlicher)
    pick = longest[(len(longest) - 1) // 2]
    return pick[0], pick[1], pick[2]


def photos_right(samples, thr):
    """Anzahl Fotos, bei denen alle drei Klassen stimmen, wenn die Schwellen thr (Prozent je Klasse) gelten."""
    n = 0
    for truth, conf in samples:
        if all((conf[k] >= thr[CLASSES[k]]) == bool(truth[k]) for k in range(3)):
            n += 1
    return n


def total_cost(samples, thr):
    fp = miss = 0
    for k, c in enumerate(CLASSES):
        a, b = errors(samples, k, thr[c])
        fp += a
        miss += b
    return fp, miss, cost(fp, miss)
