# -*- coding: utf-8 -*-
"""Calculo puro del armado de escaleras y rampas (sin Revit ni WPF). Unidades: cm.

El elemento se describe por su PERFIL longitudinal (poligono en el plano
vertical que contiene la direccion de subida) y su ANCHO. Coordenadas locales:
x = s (a lo largo de la subida, desde el extremo inicial), y = a lo ancho
(0 .. W), z = elevacion (0 = punto mas bajo). Compatible con IronPython 2.7 y
CPython 3.
"""
from __future__ import division
import copy
import io
import json
import math
import os
import re
import sys

try:
    import armado_traslapes as LP
except ImportError:                      # carpeta lib de la extension
    sys.path.append(os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'lib')))
    import armado_traslapes as LP

TOL = 1e-6
CFG_VERSION = 1
STEEL = 7850.0
KINDS = ('escalera', 'rampa')
SIN_KINK = math.sin(math.radians(8.0))


# ================================================================ vectores 2D (s, z)
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _mul(a, k):
    return (a[0] * k, a[1] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _len(a):
    return math.hypot(a[0], a[1])


def _unit(a):
    L = _len(a)
    return (a[0] / L, a[1] / L) if L > TOL else (1.0, 0.0)


def _left(d):
    return (-d[1], d[0])


def signed_area(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        a += pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
    return a / 2.0


def clean_polygon(pts, tol=0.2):
    out = []
    for p in pts:
        p = (float(p[0]), float(p[1]))
        if not out or _len(_sub(p, out[-1])) > tol:
            out.append(p)
    if len(out) > 1 and _len(_sub(out[0], out[-1])) <= tol:
        out.pop()
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            d1, d2 = _sub(b, a), _sub(c, b)
            if _len(d1) < tol or _len(d2) < tol or (abs(_cross(_unit(d1), _unit(d2))) < 0.004 and _dot(d1, d2) > 0):
                out.pop(i)
                changed = True
                break
    return out


def point_in_poly(p, poly):
    x, y = p
    c = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > y) != (b[1] > y):
            xi = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < xi:
                c = not c
    return c


def ray_hits(poly, p, d, maxlen=1e6):
    """Intersecciones del rayo p + t d (0 < t <= maxlen) con las aristas: [(t, dir_arista)]."""
    hits = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        e = _sub(b, a)
        den = _cross(d, e)
        if abs(den) < 1e-12:
            continue
        w = _sub(a, p)
        t = _cross(w, e) / den
        u = _cross(w, d) / den
        if 1e-6 < t <= maxlen and -1e-9 <= u <= 1.0 + 1e-9:
            hits.append((t, _unit(e)))
    hits.sort(key=lambda h: h[0])
    return hits


def path_len(path):
    return sum(_len(_sub(b, a)) for a, b in zip(path, path[1:]))


def sub_path(path, t0, t1):
    """Tramo de la polilinea entre las longitudes t0 y t1."""
    out = []
    acc = 0.0
    for a, b in zip(path, path[1:]):
        L = _len(_sub(b, a))
        if L < TOL:
            continue
        d = _unit(_sub(b, a))
        s0, s1 = max(t0, acc), min(t1, acc + L)
        if s1 > s0 + 1e-9:
            p0 = _add(a, _mul(d, s0 - acc))
            p1 = _add(a, _mul(d, s1 - acc))
            if not out or _len(_sub(out[-1], p0)) > 1e-6:
                out.append(p0)
            out.append(p1)
        acc += L
    return out


def point_at(path, t):
    acc = 0.0
    for a, b in zip(path, path[1:]):
        L = _len(_sub(b, a))
        if acc + L >= t - 1e-9 and L > TOL:
            return _add(a, _mul(_unit(_sub(b, a)), t - acc)), _unit(_sub(b, a))
        acc += L
    return path[-1], _unit(_sub(path[-1], path[-2]))


# ================================================================ modelo
class StairModel(object):
    """profile: poligono (s, z) en cm del perfil longitudinal. W: ancho (cm)."""

    def __init__(self, profile, W, name=u''):
        pts = clean_polygon(profile)
        if len(pts) < 3:
            raise ValueError(u'perfil vacío')
        if signed_area(pts) < 0:
            pts = pts[::-1]
        s0 = min(p[0] for p in pts)
        z0 = min(p[1] for p in pts)
        self.shift = (s0, z0)
        self.poly = [(p[0] - s0, p[1] - z0) for p in pts]
        self.W = float(W)
        self.name = name
        self._chains()
        self._steps()

    # ------------------------------------------------------------ cadenas
    def _chains(self):
        P = self.poly
        n = len(P)
        smin = min(p[0] for p in P)
        smax = max(p[0] for p in P)
        # extremos del intrados: el vertice mas bajo cerca de cada extremo. En una losa / rampa inclinada con los
        # extremos cortados perpendiculares a ella, la esquina superior queda unos cm 'antes' que la inferior;
        # con una tolerancia de 0.5 cm se tomaba la superior y la cara del extremo pasaba a ser intrados
        # (garganta = largo de la rampa)
        tol = max(0.5, min(30.0, 0.1 * (smax - smin)))
        iL = min([i for i in range(n) if P[i][0] <= smin + tol], key=lambda i: (P[i][1], P[i][0]))
        iR = min([i for i in range(n) if P[i][0] >= smax - tol], key=lambda i: (P[i][1], -P[i][0]))
        low = [iL]
        i = iL
        while i != iR:
            i = (i + 1) % n
            low.append(i)
        up = []
        i = iR
        while True:
            i = (i + 1) % n
            if i == iL:
                break
            up.append(i)
        self.lower = [P[k] for k in low]
        self.upper = [P[k] for k in up]
        self.length = smax - smin
        self.height = max(p[1] for p in P)
        # espesor local de cada tramo del intrados
        self.seg_t = []
        for a, b in zip(self.lower, self.lower[1:]):
            d = _unit(_sub(b, a))
            nrm = _left(d)
            L = _len(_sub(b, a))
            best = None
            for v in self.upper:
                u = _dot(_sub(v, a), d)
                if -1.0 <= u <= L + 1.0:
                    h = _dot(_sub(v, a), nrm)
                    if h > 0.5 and (best is None or h < best):
                        best = h
            if best is None:
                cands = [_dot(_sub(v, a), nrm) for v in self.upper]
                cands = [h for h in cands if h > 0.5]
                best = min(cands) if cands else 10.0
            self.seg_t.append(best)

    def _steps(self):
        """Narices de escalon: vertices convexos de la cara superior entre huella y peralte."""
        P = self.poly
        n = len(P)
        noses = []
        for v in self.upper:
            i = P.index(v)
            a, c = P[i - 1], P[(i + 1) % n]
            e1, e2 = _sub(v, a), _sub(c, v)
            if _cross(e1, e2) <= 0:
                continue
            u1, u2 = _unit(e1), _unit(e2)
            # huella recorrida hacia atras (e1) y peralte que baja (e2): nariz de un escalon que sube en +s
            if u1[0] < -0.98 and u2[1] < -0.98 and 5.0 <= _len(e2) <= 30.0 and _len(e1) >= 15.0:
                noses.append({'v': v, 'n1': _left(u1), 'n2': _left(u2)})
        noses.sort(key=lambda d: d['v'][0])
        self.noses = noses
        self.riser = self.tread = None
        if len(noses) >= 2:
            ds = sorted(noses[k + 1]['v'][0] - noses[k]['v'][0] for k in range(len(noses) - 1))
            dz = sorted(noses[k + 1]['v'][1] - noses[k]['v'][1] for k in range(len(noses) - 1))
            self.tread = ds[len(ds) // 2]
            self.riser = dz[len(dz) // 2]

    def guess_kind(self):
        return 'escalera' if len(self.noses) >= 2 else 'rampa'

    def throat(self):
        """Espesor minimo (garganta) de los tramos inclinados, o minimo general."""
        inc = [t for (a, b), t in zip(zip(self.lower, self.lower[1:]), self.seg_t)
               if abs(_unit(_sub(b, a))[1]) > 0.1]
        vals = inc or self.seg_t
        return min(vals) if vals else 0.0


# ================================================================ config
def closest_bt(bl, d):
    best = None
    for bt in bl:
        e = abs(bt['dn'] - d)
        if best is None or e < best[0]:
            best = (e, bt)
    return best[1]['name'] if best else u''


def default_cfg(model, bl):
    t3 = closest_bt(bl, 0.95)
    t4 = closest_bt(bl, 1.27)
    Ld = max(60.0, round(model.length / 4.0 / 10.0) * 10.0)
    return {
        'version': CFG_VERSION, 'kind': model.guess_kind(), 'rot': 0,
        'cover_b': 2.5, 'cover_t': 2.5, 'cover_s': 2.5, 'partition': u'',
        'bot': {'bt': t4, 'sp': 20.0, 'ext0': 0.0, 'ext1': 0.0, 'leg0': 15.0, 'leg1': 15.0, 'split': True, 'ld': 60.0},
        'top': {'on': True, 'bt': t3, 'sp': 20.0, 'mode': 'full', 'L0': Ld, 'L1': Ld, 'leg0': 15.0, 'leg1': 15.0,
                'split': True, 'ld': 50.0},
        'tr': {'bot_on': True, 'bot_bt': t3, 'bot_sp': 25.0, 'top_on': True, 'top_bt': t3, 'top_sp': 25.0},
        'nose': {'on': True, 'bt': t3},
        'lap': {'k': 40.0, 'stock_on': True, 'stock': 1200.0, 'crank': False, 'pos': u''},
    }


def fit_cfg(cfg, model, bl):
    out = default_cfg(model, bl)
    for k, v in (cfg or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(copy.deepcopy(v))
        else:
            out[k] = copy.deepcopy(v)
    return out


BT_KEYS = ('bt', 'bot_bt', 'top_bt')


def _walk(cfg, fn):
    def walk(o):
        if isinstance(o, dict):
            for k in list(o.keys()):
                if k in BT_KEYS and isinstance(o[k], (str, type(u''))):
                    o[k] = fn(o[k])
                else:
                    walk(o[k])
    walk(cfg)


def remap_bar_names(cfg, diams, bl):
    have = set(b['name'] for b in bl)
    changes = {}

    def fix(n):
        if not n or n in have:
            return n
        if n not in changes:
            d = diams.get(n)
            changes[n] = closest_bt(bl, d) if d else (bl[0]['name'] if bl else n)
        return changes[n]

    _walk(cfg, fix)
    return changes


def used_bar_names(cfg):
    names = set()

    def add(n):
        if n:
            names.add(n)
        return n

    _walk(copy.deepcopy(cfg), add)
    return names


def config_dir():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'ArmadoEscalera', 'configuraciones')
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            pass
    return d


def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|]+', u'_', u'%s' % name).strip().strip('.')
    return name or u'config'


def save_cfg(path, cfg, bts):
    data = copy.deepcopy(cfg)
    data['version'] = CFG_VERSION
    data['bar_diams'] = dict((n, bts[n]['dn']) for n in used_bar_names(cfg) if n in bts)
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(u'%s' % json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def load_cfg(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        data = json.loads(f.read())
    return data, (data.pop('bar_diams', {}) or {})


def list_saved(folder):
    try:
        return [fn[:-5] for fn in sorted(os.listdir(folder)) if fn.lower().endswith('.json')]
    except Exception:
        return []


# ================================================================ trayectorias
def _even(a, b, sp):
    if b - a <= TOL:
        return [(a + b) / 2.0]
    n = max(1, int(math.ceil((b - a) / max(sp, 1.0) - 1e-9)))
    return [a + (b - a) * k / n for k in range(n + 1)]


def offset_chain(chain, offs):
    """Polilinea paralela a 'chain' desplazada hacia el interior (izquierda) offs[i] en cada tramo."""
    segs = []
    for (a, b), off in zip(zip(chain, chain[1:]), offs):
        if _len(_sub(b, a)) < 0.01:
            continue
        d = _unit(_sub(b, a))
        nrm = _left(d)
        segs.append((_add(a, _mul(nrm, off)), _add(b, _mul(nrm, off)), d))
    if not segs:
        return []
    pts = [segs[0][0]]
    for (a1, b1, d1), (a2, b2, d2) in zip(segs, segs[1:]):
        den = _cross(d1, d2)
        if abs(den) < 1e-6:
            pts.append(b1)
            if _len(_sub(a2, b1)) > 0.05:
                pts.append(a2)
            continue
        t = _cross(_sub(a2, a1), d2) / den
        pts.append(_add(a1, _mul(d1, t)))
    pts.append(segs[-1][1])
    return pts


def clip_ends(path, poly, ce):
    """Recorta/prolonga los extremos de la polilinea hasta quedar a 'ce' de las caras extremas."""
    if len(path) < 2:
        return path
    path = list(path)
    # inicio
    d0 = _unit(_sub(path[1], path[0]))
    hits = ray_hits(poly, path[1], _mul(d0, -1.0))
    if hits:
        t, e = hits[0]
        sn = max(abs(_cross(d0, e)), 0.2)
        back = t - ce / sn
        if back > 0.5:
            path[0] = _add(path[1], _mul(d0, -back))
        else:
            path = path[1:]
    if len(path) < 2:
        return path
    d1 = _unit(_sub(path[-1], path[-2]))
    hits = ray_hits(poly, path[-2], d1)
    if hits:
        t, e = hits[0]
        sn = max(abs(_cross(d1, e)), 0.2)
        fwd = t - ce / sn
        if fwd > 0.5:
            path[-1] = _add(path[-2], _mul(d1, fwd))
        else:
            path = path[:-1]
    return path


def _dangerous(path, sign):
    """Indices de vertices interiores donde la barra en traccion 'jala' hacia afuera.
    sign = -1 (inferior: giro horario), +1 (superior: giro antihorario)."""
    out = []
    for k in range(1, len(path) - 1):
        d1 = _unit(_sub(path[k], path[k - 1]))
        d2 = _unit(_sub(path[k + 1], path[k]))
        if sign * _cross(d1, d2) > SIN_KINK:
            out.append(k)
    return out


def split_path(path, poly, ks, ld, cover_opp, db):
    """Corta la polilinea en los vertices ks: cada pieza se prolonga recta 'ld' (limitada)."""
    if not ks:
        return [path]
    pieces = []
    start = [path[0]]
    cur = start
    for k in range(1, len(path)):
        cur.append(path[k])
        if k in ks:
            V = path[k]
            d1 = _unit(_sub(path[k], path[k - 1]))
            d2 = _unit(_sub(path[k + 1], path[k]))
            sn = max(abs(_cross(d1, d2)), 0.3)
            h = ray_hits(poly, V, d1)
            eA = min(ld, max((h[0][0] if h else 0.0) - (cover_opp + db) / sn, 0.0))
            if eA > 1.0:
                cur.append(_add(V, _mul(d1, eA)))
            pieces.append(cur)
            h = ray_hits(poly, V, _mul(d2, -1.0))
            eB = min(ld, max((h[0][0] if h else 0.0) - (cover_opp + db) / sn, 0.0))
            cur = ([_add(V, _mul(d2, -eB))] if eB > 1.0 else []) + [V]
    pieces.append(cur)
    return [p for p in pieces if len(p) >= 2 and path_len(p) > 1.0]


def _legs(piece, first, last, ext0, ext1, leg0, leg1, sgn, t0, t1, room):
    """Prolongaciones y patas a 90 grados en los extremos. sgn: +1 pata hacia la izquierda (arriba)."""
    p = list(piece)
    if first and ext0 > 0:
        d = _unit(_sub(p[1], p[0]))
        p[0] = _add(p[0], _mul(d, -ext0))
    if last and ext1 > 0:
        d = _unit(_sub(p[-1], p[-2]))
        p[-1] = _add(p[-1], _mul(d, ext1))
    if first and leg0 > 0:
        d = _unit(_sub(p[1], p[0]))
        L = min(leg0, t0 - room)
        if L >= 3.0:
            p.insert(0, _add(p[0], _mul(_left(d), sgn * L)))
    if last and leg1 > 0:
        d = _unit(_sub(p[-1], p[-2]))
        L = min(leg1, t1 - room)
        if L >= 3.0:
            p.append(_add(p[-1], _mul(_left(d), sgn * L)))
    return p


# ================================================================ calculo
def _bar(res, kind, bt, pts, n=1, step=(0.0, 0.0, 0.0), plane=None, label=u''):
    L = sum(math.sqrt(sum((b[i] - a[i]) ** 2 for i in range(3))) for a, b in zip(pts, pts[1:]))
    res['bars'].append({'kind': kind, 'bt': bt, 'pts': pts, 'n': int(n), 'step': step, 'plane': plane,
                        'len': L, 'label': label})


def _across(W, cs, db, sp):
    ys = _even(cs + db / 2.0, W - cs - db / 2.0, sp)
    return ys[0], (ys[1] - ys[0]) if len(ys) > 1 else 0.0, len(ys)


def _long_sets(res, kind, bt, pieces, W, cs, sp):
    y0, dy, n = _across(W, cs, bt['dm'], sp)
    for pc in pieces:
        pts = [(q[0], y0, q[1]) for q in pc]
        _bar(res, kind, bt, pts, n, (0.0, dy, 0.0), (0.0, 1.0, 0.0), u'%d %s @ %s' % (n, bt['name'], fmt(sp)))


def _trans_sets(res, kind, bt, path, W, cs, sp, keep=None):
    """Barras a lo ancho repartidas a lo largo de 'path'; un conjunto por tramo recto."""
    if len(path) < 2:
        return []
    Lp = path_len(path)
    ts = _even(0.0, Lp, sp)
    if keep is not None:
        ts = [t for t in ts if keep(t, Lp)]
    y0, y1 = cs + bt['dm'] / 2.0, W - cs - bt['dm'] / 2.0
    cum = [0.0]
    for a, b in zip(path, path[1:]):
        cum.append(cum[-1] + _len(_sub(b, a)))
    groups = {}
    for t in ts:
        k = 0
        while k < len(cum) - 2 and t > cum[k + 1] + 1e-6:
            k += 1
        groups.setdefault(k, []).append(t)
    pos2d = []
    for k in sorted(groups):
        g = groups[k]
        a, b = path[k], path[k + 1]
        d = _unit(_sub(b, a))
        p0 = _add(a, _mul(d, g[0] - cum[k]))
        step = (g[1] - g[0]) if len(g) > 1 else 0.0
        pts = [(p0[0], y0, p0[1]), (p0[0], y1, p0[1])]
        _bar(res, kind, bt, pts, len(g), (d[0] * step, 0.0, d[1] * step), (d[0], 0.0, d[1]),
             u'%s @ %s' % (bt['name'], fmt(sp)))
        pos2d += [_add(a, _mul(d, t - cum[k])) for t in g]
    return pos2d


def compute(model, cfg, bts):
    m = model
    res = {'bars': [], 'warnings': [], 'kind': cfg.get('kind') or m.guess_kind(), 'draw': {}}
    dr = res['draw']
    cb, ct, cs = float(cfg['cover_b']), float(cfg['cover_t']), float(cfg['cover_s'])
    b, t, tr, no = cfg['bot'], cfg['top'], cfg['tr'], cfg['nose']
    bb, tb = bts.get(b['bt']), bts.get(t['bt'])
    tbb, ttb = bts.get(tr['bot_bt']), bts.get(tr['top_bt'])
    if bb is None:
        res['warnings'].append(u'Tipo de varilla inexistente (longitudinal inferior)')
        return res
    low = m.lower
    segT = m.seg_t
    if min(segT) < cb + ct + 4.0:
        res['warnings'].append(u'Espesor insuficiente para los recubrimientos en algún tramo')
    # ---------------- longitudinal inferior
    offs_b = [cb + bb['dm'] / 2.0] * len(segT)
    pb = clip_ends(offset_chain(low, offs_b), m.poly, cs + bb['dm'] / 2.0)
    pieces_b = []
    if len(pb) >= 2:
        ks = _dangerous(pb, -1) if b.get('split') else []
        pieces_b = split_path(pb, m.poly, ks, b['ld'], ct, bb['dm'])
        room = cb + ct + bb['dm'] + (tb['dm'] if tb else 0.0)
        nb = len(pieces_b)
        pieces_b = [_legs(pc, i == 0, i == nb - 1, b['ext0'], b['ext1'], b['leg0'], b['leg1'], 1.0,
                          segT[0], segT[-1], room) for i, pc in enumerate(pieces_b)]
        _long_sets(res, 'long_bot', bb, pieces_b, m.W, cs, b['sp'])
        if len(pieces_b) > 1:
            res['warnings'].append(u'Inferior cortado y cruzado en %d quiebre(s) del intradós' % (len(pieces_b) - 1))
    dr['bot'] = pieces_b
    # ---------------- longitudinal superior
    pieces_t = []
    top_keep = None
    if t['on'] and tb is not None:
        offs_t = [max(T - ct - tb['dm'] / 2.0, cb + bb['dm'] + tb['dm']) for T in segT]
        pt = clip_ends(offset_chain(low, offs_t), m.poly, cs + tb['dm'] / 2.0)
        if len(pt) >= 2:
            Lt = path_len(pt)
            if t.get('mode') == 'ends' and t['L0'] + t['L1'] < Lt - 1.0:
                L0, L1 = t['L0'], t['L1']
                raw = [sub_path(pt, 0.0, L0), sub_path(pt, Lt - L1, Lt)]
                top_keep = lambda s, Lp, L0=L0, L1=L1: s <= L0 + 1.0 or s >= Lp - L1 - 1.0
                firsts = [(True, False), (False, True)]
            else:
                ks = _dangerous(pt, +1) if t.get('split') else []
                raw = split_path(pt, m.poly, ks, t['ld'], cb, tb['dm'])
                firsts = [(i == 0, i == len(raw) - 1) for i in range(len(raw))]
            room = cb + ct + bb['dm'] + tb['dm']
            for pc, (f, l) in zip(raw, firsts):
                if len(pc) >= 2:
                    pieces_t.append(_legs(pc, f, l, 0.0, 0.0, t['leg0'], t['leg1'], -1.0, segT[0], segT[-1], room))
            _long_sets(res, 'long_top', tb, pieces_t, m.W, cs, t['sp'])
    dr['top'] = pieces_t
    # ---------------- reparticion (transversal)
    dr['tr_bot'] = dr['tr_top'] = []
    if tr['bot_on'] and tbb is not None:
        offs = [cb + bb['dm'] + tbb['dm'] / 2.0] * len(segT)
        p = clip_ends(offset_chain(low, offs), m.poly, cs + tbb['dm'] / 2.0)
        dr['tr_bot'] = _trans_sets(res, 'tr_bot', tbb, p, m.W, cs, tr['bot_sp'])
    if tr['top_on'] and ttb is not None and t['on'] and tb is not None:
        offs = [max(T - ct - tb['dm'] - ttb['dm'] / 2.0, cb + bb['dm'] + ttb['dm']) for T in segT]
        p = clip_ends(offset_chain(low, offs), m.poly, cs + ttb['dm'] / 2.0)
        dr['tr_top'] = _trans_sets(res, 'tr_top', ttb, p, m.W, cs, tr['top_sp'], top_keep)
    # ---------------- narices de escalon
    dr['nose'] = []
    nbt = bts.get(no['bt'])
    if no['on'] and nbt is not None and res['kind'] == 'escalera' and m.noses:
        c = ct + nbt['dm'] / 2.0
        pts = []
        for ns in m.noses:
            n1, n2 = ns['n1'], ns['n2']
            den = 1.0 + _dot(n1, n2)
            q = _add(ns['v'], _mul(_add(n1, n2), c / max(den, 0.2)))
            pts.append(q)
        dr['nose'] = pts
        y0, y1 = cs + nbt['dm'] / 2.0, m.W - cs - nbt['dm'] / 2.0
        groups = [[pts[0]]]
        for q in pts[1:]:
            g = groups[-1]
            if len(g) >= 2:
                d0 = _sub(g[1], g[0])
                if _len(_sub(_sub(q, g[-1]), d0)) < 0.5:
                    g.append(q)
                    continue
                groups.append([q])
            else:
                g.append(q)
        for g in groups:
            st = _sub(g[1], g[0]) if len(g) > 1 else (0.0, 0.0)
            plane = _unit(st) if len(g) > 1 else (1.0, 0.0)
            _bar(res, 'nose', nbt, [(g[0][0], y0, g[0][1]), (g[0][0], y1, g[0][1])], len(g), (st[0], 0.0, st[1]),
                 (plane[0], 0.0, plane[1]), u'%d %s en narices' % (len(g), nbt['name']))
    split_laps(cfg, res)
    return res


def split_laps(cfg, res):
    """Traslapes: longitud comercial y posiciones del usuario (cm horizontales desde el inicio).
    La bayoneta sigue la pendiente del tramo donde cae el traslape (hacia el interior)."""
    lc = cfg.get('lap') or {}
    k = float(lc.get('k', 40.0))
    stock = float(lc.get('stock', 1200.0)) if lc.get('stock_on', True) else None
    user = LP.parse_positions(lc.get('pos', u'')) or []
    style = LP.lap_style(lc)
    out = []
    for b in res['bars']:
        db = b['bt']['dm']
        pts = b['pts']
        if b['kind'] in ('long_bot', 'long_top'):
            sgn = 1.0 if b['kind'] == 'long_bot' else -1.0
            cuts = [t for t in (LP.t_at(pts, lambda q: q[0], v) for v in user) if t is not None]

            def off(t, pts=pts, sgn=sgn, db=db):
                d = LP.point_at(pts, t)[1]
                return (-d[2] * sgn * db, 0.0, d[0] * sgn * db)
        else:
            cuts = []
            toward = (0.0, 0.0, 1.0) if b['kind'] in ('tr_bot',) else (0.0, 0.0, -1.0)
            v = LP.in_plane_perp((0.0, 1.0, 0.0), b['plane'], toward)
            off = (v[0] * db, v[1] * db, v[2] * db)
        # desfasado: en el plano del lecho, perpendicular a la varilla (a lo ancho para las longitudinales)
        q0, st_ = pts[0], b.get('step') or (0.0, 0.0, 0.0)
        n_ = max(int(b.get('n', 1)), 1)
        if b['kind'] in ('long_bot', 'long_top'):
            mid = q0[1] + st_[1] * (n_ - 1) / 2.0
            shift = (0.0, (-db if mid > 0 else db), 0.0)
        else:
            shift = None                              # transversales: se desfasan como la bayoneta
        out.extend(LP.split_bar(b, cuts, k * b['bt']['dn'], stock, off if style != 'none' else None,
                                style=style, shift=shift))
    res['bars'] = out


# ================================================================ resumen
def kg_m(d):
    return math.pi * (d / 100.0) ** 2 / 4.0 * STEEL


def summary(res):
    w = {}
    for b in res['bars']:
        k = b['kind']
        w[k] = w.get(k, 0.0) + b['n'] * b['len'] / 100.0 * kg_m(b['bt']['dn'])
    return w


def fmt(v):
    if v is None:
        return u'-'
    s = u'%.1f' % v
    return s[:-2] if s.endswith(u'.0') else s


def parse_num(text, default=None):
    try:
        s = (u'%s' % text).strip().replace(',', '.')
        return float(s) if s else default
    except Exception:
        return default
