# -*- coding: utf-8 -*-
"""Calculo puro del armado de muros (sin Revit ni WPF). Unidades: cm.

Coordenadas del muro:
  s = a lo largo del muro (0 = extremo inicial, L = extremo final)
  x = a traves del espesor (0 = plano medio)
  z = altura desde la base del muro (0 = base)
Elementos de borde: seccion tipo columna de Lb x t. En coordenadas de zona
(cx, cy) el paño extremo del muro esta en cx = -Lb/2 para ambos extremos.
Compatible con IronPython 2.7 y CPython 3.
"""
from __future__ import division
import copy
import io
import json
import math
import os
import re

import aw_colgeom as CG
try:
    import armado_continuidad as CT
except Exception:                        # pragma: no cover
    CT = None

TOL = 1e-6
CFG_VERSION = 1
STEEL = 7850.0


# ================================================================ modelo
class WallModel(object):
    def __init__(self, t, L, H, loops=None, inner_levels=(), levels_view=(), base_name=u'', top_name=u''):
        self.t = float(t)
        self.L = float(L)
        self.H = float(H)
        self.loops = loops or [[(0.0, 0.0), (self.L, 0.0), (self.L, self.H), (0.0, self.H)]]
        self.inner_levels = [z for z in inner_levels if 1.0 < z < self.H - 1.0]
        self.levels_view = list(levels_view)
        self.base_name = base_name
        self.top_name = top_name
        self.openings = find_openings(self.loops, self.L, self.H)
        self.ends = {}       # muro transversal en cada extremo: {'L' / 'R': {'e', 't', 'name'}} (aw_revit)
        self.above = []      # muros apoyados encima: [{'s0', 's1', 'x0', 'x1', 't', 'name'}] (cm, coords. del muro)
        self.top_parts = None    # elementos de concreto arriba (armado_continuidad); None = no se leyeron

    def stories(self):
        b = [0.0] + sorted(self.inner_levels) + [self.H]
        return [(b[i], b[i + 1]) for i in range(len(b) - 1)]


def rect_with_openings(L, H, openings):
    """Lazos de un muro rectangular con huecos rectangulares interiores.
    Los huecos que tocan la base se incorporan al contorno exterior (puertas)."""
    outer = [(0.0, 0.0)]
    doors = sorted([o for o in openings if o[2] <= 0.5], key=lambda o: o[0])
    for s0, s1, z0, z1 in doors:
        outer += [(s0, 0.0), (s0, z1), (s1, z1), (s1, 0.0)]
    outer += [(L, 0.0), (L, H), (0.0, H)]
    loops = [outer]
    for s0, s1, z0, z1 in openings:
        if z0 > 0.5:
            loops.append([(s0, z0), (s1, z0), (s1, z1), (s0, z1)])
    return loops


def _cross(loops, along, value):
    """Cruces de la recta (s = value si along == 'z', z = value si along == 's')."""
    out = []
    for lp in loops:
        n = len(lp)
        for i in range(n):
            a = lp[i]
            b = lp[(i + 1) % n]
            if along == 'z':      # recta vertical s = value
                p, q, r1, r2 = a[0], b[0], a[1], b[1]
            else:                 # recta horizontal z = value
                p, q, r1, r2 = a[1], b[1], a[0], b[0]
            if (p > value) != (q > value):
                t = (value - p) / (q - p)
                out.append(r1 + t * (r2 - r1))
    out.sort()
    return out


def segs_v(loops, s):
    c = _cross(loops, 'z', s + 1e-4)
    return [(c[i], c[i + 1]) for i in range(0, len(c) - 1, 2) if c[i + 1] - c[i] > 0.01]


def segs_h(loops, z):
    c = _cross(loops, 's', z + 1e-4)
    return [(c[i], c[i + 1]) for i in range(0, len(c) - 1, 2) if c[i + 1] - c[i] > 0.01]


def point_in(loops, s, z):
    for a, b in segs_h(loops, z):
        if a <= s <= b:
            return True
    return False


def _uniq(vals, tol=0.5):
    out = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def find_openings(loops, L, H):
    """Huecos (s0, s1, z0, z1) como regiones vacias que no tocan los extremos
    ni la parte superior del muro (las puertas si tocan la base)."""
    xs = _uniq([p[0] for lp in loops for p in lp] + [0.0, L])
    zs = _uniq([p[1] for lp in loops for p in lp] + [0.0, H])
    nx, nz = len(xs) - 1, len(zs) - 1
    if nx <= 0 or nz <= 0 or nx * nz > 40000:
        return []
    empty = {}
    for i in range(nx):
        for j in range(nz):
            if not point_in(loops, (xs[i] + xs[i + 1]) / 2.0, (zs[j] + zs[j + 1]) / 2.0):
                empty[(i, j)] = True
    seen = set()
    out = []
    for key in list(empty.keys()):
        if key in seen:
            continue
        comp = []
        stack = [key]
        seen.add(key)
        while stack:
            c = stack.pop()
            comp.append(c)
            i, j = c
            for nb in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if nb in empty and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        i0 = min(c[0] for c in comp)
        i1 = max(c[0] for c in comp)
        j0 = min(c[1] for c in comp)
        j1 = max(c[1] for c in comp)
        if i0 == 0 or i1 == nx - 1 or j1 == nz - 1:
            continue
        out.append((xs[i0], xs[i1 + 1], zs[j0], zs[j1 + 1]))
    out.sort()
    return out


# ================================================================ config
def level_above(model, tol=1.0, max_gap=250.0):
    """(nombre, z) del nivel inmediato superior cuando la corona del muro queda abajo de el (trabe encima que
    confina el muro); None si el muro llega al nivel o no hay uno cerca (cm, desde la base del muro)."""
    top = model.H
    best = None
    for name, z in (getattr(model, 'levels_view', None) or []):
        try:
            z = float(z)
        except Exception:
            continue
        if top + tol < z <= top + max_gap and (best is None or z < best[1]):
            best = (name, z)
    return best


def round_up(v, step):
    return math.ceil(v / step - 1e-9) * step


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
    t5 = closest_bt(bl, 1.59)
    lb = min(60.0, round_up(model.L * 0.15, 5.0)) if model.L > 120 else 0.0
    return {
        'version': CFG_VERSION,
        'cover': 4.0,
        'gen': {'ext_bot': 0.0, 'ext_top': 0.0, 'hook_bot': True, 'hook_top': False, 'hook_len': 30.0,
                'hook_dir': 'out', 'split': False, 'lap_f': 40.0, 'partition': u'',
                'top_lap': True, 'top_fit': True, 'lap_level': True, 'top_cont': True, 'stock_on': True, 'stock': 1200.0, 'crank': False, 'lap_pos_v': u'', 'lap_pos_h': u'',
                'elev': False, 'elev_tpl': u''},
        'bnd': {'mirror': True, 'lenL': lb, 'lenR': lb,
                'L': {'bars': [], 'stirrups': []}, 'R': {'bars': [], 'stirrups': []}},
        'bstir': {'on': True, 'bt': t3, 'hook': u'', 'lc': 60.0, 'sc': 10.0, 'sm': 20.0,
                  'first': 5.0, 'top_zone': False},
        'web': {'curtains': 2 if model.t >= 15 else 1, 'outer': 'h', 'v_bt': t4, 'v_sp': 20.0,
                'h_bt': t4, 'h_sp': 20.0, 'h_first': 5.0, 'h_end': 'hook90', 'h_leg': 15.0,
                'v_on': True, 'h_on': True},          # armado del alma: verticales / horizontales (o sin alma)
        'open': {'on': True, 'bt': t5, 'n': 2, 'ext': 60.0, 'diag': False, 'diag_bt': t4, 'diag_L': 80.0},
    }


def fit_cfg(cfg, model, bl):
    out = default_cfg(model, bl)
    for k, v in (cfg or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(copy.deepcopy(v))
        else:
            out[k] = copy.deepcopy(v)
    b = out['bnd']
    for k in ('L', 'R'):
        b.setdefault(k, {'bars': [], 'stirrups': []})
    return out


def remap_bar_names(cfg, diams, bl):
    have = set(bt['name'] for bt in bl)
    changes = {}

    def fix(name):
        if not name or name in have:
            return name
        if name not in changes:
            d = diams.get(name)
            changes[name] = closest_bt(bl, d) if d else (bl[0]['name'] if bl else name)
        return changes[name]

    def walk(o):
        if isinstance(o, dict):
            for k in list(o.keys()):
                if k in ('bt', 'v_bt', 'h_bt', 'diag_bt') and isinstance(o[k], (str, type(u''))):
                    o[k] = fix(o[k])
                else:
                    walk(o[k])
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(cfg)
    return changes


def used_bar_names(cfg):
    names = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ('bt', 'v_bt', 'h_bt', 'diag_bt') and v:
                    names.add(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(cfg)
    return names


def config_dir():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'ArmadoMuro', 'configuraciones')
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


# ------------------------------------------------ zonas (serializacion)
def zone_len(cfg, key):
    b = cfg['bnd']
    if key == 'R' and b['mirror']:
        return float(b['lenL'])
    return float(b['len' + key])


def zone_key_data(cfg, key):
    return 'L' if (key == 'R' and cfg['bnd']['mirror']) else key


def zone_section(model, cfg, key):
    return CG.Section('rect', max(zone_len(cfg, key), 1.0), model.t)


def side_ext(model, cfg, side, flag=None):
    """Cuanto entra el elemento de borde del lado 'L' / 'R' en el muro transversal de ese extremo (cm): la
    distancia del extremo del muro al pano lejano del otro muro, si esta activado (Lb medido desde ahi)."""
    e = float(((getattr(model, 'ends', None) or {}).get(side) or {}).get('e') or 0.0)
    if flag is None:
        flag = (cfg.get('bnd') or {}).get('into' + side, True)
    return e if (flag and e > 0.05) else 0.0


def shift_side(side, s, e):
    """s de la zona medida desde el pano lejano del muro transversal -> s del muro."""
    return s - e if side == 'L' else s + e


def zone_to_wall(model, cfg, key, cx, cy):
    s, x = side_to_wall(model.L, key, zone_len(cfg, key), cx, cy)
    return shift_side(key, s, side_ext(model, cfg, key)), x


def wall_to_zone(model, cfg, key, s, x):
    return wall_to_side(model.L, key, zone_len(cfg, key), shift_side(key, s, -side_ext(model, cfg, key)), x)


def side_to_wall(L, side, lb, cx, cy):
    """Seccion de un elemento de borde (centro) -> muro (s, x); side 'L' (inicio) o 'R' (fin)."""
    if side == 'L':
        return lb / 2.0 + cx, cy
    return L - lb / 2.0 - cx, cy


def wall_to_side(L, side, lb, s, x):
    if side == 'L':
        return s - lb / 2.0, x
    return L - lb / 2.0 - s, x


def serialize_zone(bars, stirrups):
    idx = dict((id(b), i) for i, b in enumerate(bars))
    sb = [{'fx': b.fx, 'fy': b.fy, 'bt': b.bt['name'], 'nb': getattr(b, 'nb', 1),
           'dx': getattr(b, 'dx', None), 'dy': getattr(b, 'dy', None)} for b in bars]
    ss = []
    for st in stirrups:
        refs = []
        for r in st.refs:
            if r[0] == 'bar' and id(r[1]) in idx:
                refs.append(['bar', idx[id(r[1])]])
            elif r[0] == 'pt':
                refs.append(['pt', r[1], r[2]])
        ss.append({'kind': st.kind, 'adjust': st.adjust, 'refs': refs})
    return {'bars': sb, 'stirrups': ss}


def deserialize_zone(data, bts):
    bars = []
    for b in data.get('bars', []):
        bt = bts.get(b['bt'])
        if bt is not None:
            bar = CG.Bar(b['fx'], b['fy'], bt, b.get('nb', 1))
            bar.dx, bar.dy = b.get('dx'), b.get('dy')
            bars.append(bar)
        else:
            bars.append(None)
    sts = []
    for s in data.get('stirrups', []):
        refs = []
        ok = True
        for r in s.get('refs', []):
            if r[0] == 'bar':
                if 0 <= r[1] < len(bars) and bars[r[1]] is not None:
                    refs.append(('bar', bars[r[1]]))
                else:
                    ok = False
            else:
                refs.append(('pt', float(r[1]), float(r[2])))
        if ok:
            sts.append(CG.Stirrup(s['kind'], refs, s.get('adjust', True)))
    return [b for b in bars if b is not None], sts


# ================================================================ calculo
def stirrup_sets_wall(za, zb, lc, sc, sm, first, top_zone):
    """Conjuntos (z0, n, sep, zona) en un entrepiso [za, zb]."""
    if top_zone:
        return CG.stirrup_sets([za, zb], lc, sc, sm, first)
    sc = max(sc, 1.0)
    sm = max(sm, 1.0)
    a = za + max(first, 0.0)
    b = zb - max(first, 0.0)
    if b - a <= TOL:
        return [(za + (zb - za) / 2.0, 1, 0.0, 'conf')] if zb > za else []
    out = []
    zc = min(za + lc, b)
    if zc - a >= sc - TOL and sm > sc:
        n1 = int(math.floor((zc - a) / sc + 1e-9)) + 1
        out.append((a, n1, sc, 'conf'))
        last = a + (n1 - 1) * sc
        rest = b - last
        if rest > TOL:
            n2 = int(math.ceil(rest / sm - 1e-9))
            sp2 = rest / n2
            out.append((last + sp2, n2, sp2, 'mid'))
        return out
    sp = sc if (lc >= b - a or sm <= sc) else sm
    n = int(math.ceil((b - a) / sp - 1e-9))
    out.append((a, n + 1, (b - a) / n if n else 0.0, 'conf' if sp == sc else 'mid'))
    return out


def _even(a, b, sp, incl_a=True, incl_b=True):
    if b - a <= TOL:
        return [a] if incl_a else []
    n = max(1, int(math.ceil((b - a) / sp - 1e-9)))
    pts = [a + (b - a) * k / n for k in range(n + 1)]
    if not incl_a:
        pts = pts[1:]
    if not incl_b and pts:
        pts = pts[:-1]
    return pts


def _group(positions, sigs):
    """Agrupa posiciones consecutivas con la misma firma."""
    out = []
    for p, sg in zip(positions, sigs):
        if out and out[-1][1] == sg:
            out[-1][0].append(p)
        else:
            out.append(([p], sg))
    return out


def web_x(model, cfg, bts):
    w = cfg['web']
    t = model.t
    c = cfg['cover']
    dv = bts[w['v_bt']]['dm'] if w['v_bt'] in bts else 1.27
    dh = bts[w['h_bt']]['dm'] if w['h_bt'] in bts else 1.27
    # sin horizontales (o sin verticales) en el alma, la otra capa queda al recubrimiento
    if not w.get('h_on', True):
        dh = 0.0
    if not w.get('v_on', True):
        dv = 0.0
    if int(w['curtains']) >= 2:
        if w['outer'] == 'h':
            xh = t / 2.0 - c - dh / 2.0
            xv = t / 2.0 - c - dh - dv / 2.0
        else:
            xv = t / 2.0 - c - dv / 2.0
            xh = t / 2.0 - c - dv - dh / 2.0
        return [-xv, xv], [-xh, xh], dv, dh
    return [0.0], [(dv + dh) / 2.0], dv, dh


def bundle_dir(x, y, db, sec, cover, ds, sts=()):
    """Lado (dx, dy) hacia donde van las demas varillas de un paquete en (x, y): hacia el interior del
    estribo cerrado mas chico que lo rodea; si no hay, hacia el centro del elemento."""
    tol = db + ds + 0.5
    best = None
    for st in sts or ():
        if not st.closed:
            continue
        try:
            pts = CG.evaluate_stirrup(st, sec, cover, ds)['pts']
        except Exception:
            continue
        if len(pts) < 3:
            continue
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        if not (x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol):
            continue
        area = (x1 - x0) * (y1 - y0)
        if best is None or area < best[0]:
            best = (area, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
    dx = -1.0 if x > 1e-6 else 1.0
    dy = -1.0 if y > 1e-6 else 1.0
    if best is not None:
        if abs(best[1] - x) > 0.5:
            dx = 1.0 if best[1] > x else -1.0
        if abs(best[2] - y) > 0.5:
            dy = 1.0 if best[2] > y else -1.0
    return dx, dy


def bundle_members(b, sec, cover, ds, sts=()):
    """Centros (en la seccion de la zona) de las varillas del paquete b. Las demas varillas del
    paquete van hacia el interior del estribo cerrado que rodea a b (el mas chico, como en los planos:
    en cada esquina del estribo el par queda dentro); si ninguno la rodea, hacia el centro de la zona."""
    x, y = b.xy(sec, cover, ds)
    nb = getattr(b, 'nb', 1)
    if nb <= 1:
        return [(x, y)]
    if getattr(b, 'dx', None) is not None:          # lado fijado al colocarlo (no se mueve con los estribos)
        return CG.bundle_xy(x, y, nb, b.dm, b.dx, b.dy if b.dy is not None else (-1.0 if y > 1e-6 else 1.0))
    tol = b.dm + ds + 0.5
    best = None
    for st in sts or ():
        if not st.closed:
            continue
        try:
            pts = CG.evaluate_stirrup(st, sec, cover, ds)['pts']
        except Exception:
            continue
        if len(pts) < 3:
            continue
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        if not (x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol):
            continue
        area = (x1 - x0) * (y1 - y0)
        if best is None or area < best[0]:
            best = (area, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
    dx = dy = None
    if best is not None:
        if abs(best[1] - x) > 0.5:
            dx = 1.0 if best[1] > x else -1.0
        if abs(best[2] - y) > 0.5:
            dy = 1.0 if best[2] > y else -1.0
    return CG.bundle_xy(x, y, nb, b.dm, dx, dy)


# ================================================================ esquinas (cadena de muros en L, U, Z...)
# Cadena: muros 0..n-1 seguidos; entre el muro k y el k+1 hay una esquina en L (cs['corners'][k]).
# Planta de cada esquina: origen en su esquina exterior, 'pa' a lo largo del muro A (= muro k, alejandose
# de la esquina) y 'pb' a lo largo del muro B (= muro k+1). El muro A ocupa 0 <= pb <= tA y el B
# 0 <= pa <= tB; el cuadro de la esquina es de los dos. cn[w] = {'L', 't', 's0', 'ds', 'xs', 'name'} lleva
# de la planta a las coordenadas (s, x) de cada muro: A: s = s0 + ds*pa, x = xs*(pb - t/2);
# B: s = s0 + ds*pb, x = xs*(pa - t/2). cn['plan'] = direcciones de A y B en planta (X, Y del modelo) y
# cn['O'] = esquina exterior en planta (cm, relativa a la primera esquina): todo se dibuja junto.
# Zonas: brazo 'C<k>A' / 'C<k>B' (esquina k, muro k / k+1) y extremo libre 'F0' / 'F<n-1>'.


def chain_of(cn):
    """Cadena de dos muros a partir de una esquina (compatibilidad)."""
    if cn is None or 'corners' in cn:
        return cn
    return {'walls': [{'L': cn['A']['L'], 't': cn['A']['t'], 'name': cn['A'].get('name', u'A')},
                      {'L': cn['B']['L'], 't': cn['B']['t'], 'name': cn['B'].get('name', u'B')}],
            'corners': [cn]}


def n_walls(cs):
    return len(cs['walls'])


def leg_keys(cs):
    out = []
    for k in range(len(cs['corners'])):
        out += ['C%dA' % k, 'C%dB' % k]
    return out


def free_keys(cs):
    return ['F0', 'F%d' % (n_walls(cs) - 1)]


def all_keys(cs):
    return leg_keys(cs) + free_keys(cs)


def is_leg(key):
    return isinstance(key, str) and len(key) >= 3 and key[0] == 'C' and key[-1] in 'AB' and key[1:-1].isdigit()


def is_free(key):
    return isinstance(key, str) and len(key) >= 2 and key[0] == 'F' and key[1:].isdigit()


def is_chain_key(key):
    return is_leg(key) or is_free(key)


def parse_leg(key):
    return int(key[1:-1]), key[-1]


def key_wall(key):
    """Indice del muro de una zona de la cadena."""
    if is_leg(key):
        k, w = parse_leg(key)
        return k + (1 if w == 'B' else 0)
    return int(key[1:])


def other_leg(key):
    k, w = parse_leg(key)
    return 'C%d%s' % (k, 'B' if w == 'A' else 'A')


def corner_cfg(cfg, cs):
    """Asegura cfg['corner']: Lb y armado de cada brazo y extremo libre, giro de la planta y alma por muro.
    Convierte las configuraciones de la esquina de dos muros (lenA / A / lenFA ...)."""
    cs = chain_of(cs)
    c = cfg.setdefault('corner', {})
    lens = c.setdefault('len', {})
    zs = c.setdefault('zones', {})
    into = c.setdefault('into', {})
    webs = c.setdefault('webs', {})
    # configuracion anterior (esquina de dos muros)
    old = {'lenA': 'C0A', 'lenB': 'C0B', 'lenFA': 'F0', 'lenFB': 'F1'}
    for ok_, nk in old.items():
        if isinstance(c.get(ok_), (int, float)) and nk not in lens:
            lens[nk] = float(c[ok_])
    for ok_, nk in (('A', 'C0A'), ('B', 'C0B'), ('FA', 'F0'), ('FB', 'F1')):
        if isinstance(c.get(ok_), dict) and nk not in zs:
            zs[nk] = c[ok_]
    for ok_, nk in (('intoFA', 'F0'), ('intoFB', 'F1')):
        if ok_ in c and nk not in into:
            into[nk] = bool(c[ok_])
    if isinstance(c.get('webB'), dict) and '1' not in webs:
        webs['1'] = c['webB']
    for ok_ in ('lenA', 'lenB', 'lenFA', 'lenFB', 'A', 'B', 'FA', 'FB', 'intoFA', 'intoFB', 'webB'):
        c.pop(ok_, None)
    if cs:
        # la cadena guardada puede tener otro numero de muros: el ultimo extremo libre es F<n-1>
        n = n_walls(cs)
        last = 'F%d' % (n - 1)
        for nk in ('F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7'):
            if nk != last and nk in lens and last not in lens and nk not in [u'F%d' % (n - 1)]:
                lens[last] = lens.pop(nk)
                if nk in zs:
                    zs[last] = zs.pop(nk)
                if nk in into:
                    into[last] = into.pop(nk)
        for key in leg_keys(cs):
            k, w = parse_leg(key)
            cn = cs['corners'][k]
            if not isinstance(lens.get(key), (int, float)):
                lens[key] = round_up(cn['B' if w == 'A' else 'A']['t'] + 60.0, 5.0)
            zs.setdefault(key, {'bars': [], 'stirrups': []})
        for key in free_keys(cs):
            if not isinstance(lens.get(key), (int, float)):
                lens[key] = 0.0
            zs.setdefault(key, {'bars': [], 'stirrups': []})
        for k in range(1, n):
            wb = webs.get(str(k))
            if not isinstance(wb, dict):
                webs[str(k)] = copy.deepcopy(cfg['web'])
            else:
                for kk, v in cfg['web'].items():
                    wb.setdefault(kk, copy.deepcopy(v))
    if c.get('rot') not in (0, 1, 2, 3):
        c['rot'] = 0
    c.setdefault('web_same', True)
    return c


def reverse_chain_cfg(cfg, n):
    """Configuracion de la misma cadena recorrida al reves (muro k -> n-1-k): brazos C<k>A <-> C<n-2-k>B,
    extremos libres F0 <-> F<n-1> y alma de cada muro. Las varillas de cada zona no cambian (sus coordenadas
    son las del muro y su esquina, iguales en los dos sentidos)."""
    c = cfg.get('corner')
    if not isinstance(c, dict) or n < 2:
        return cfg
    nk = n - 1

    def key(k):
        if is_leg(k):
            i, w = parse_leg(k)
            return 'C%d%s' % (nk - 1 - i, 'B' if w == 'A' else 'A')
        if is_free(k):
            return 'F%d' % (nk - key_wall(k))
        return k

    for part in ('len', 'zones', 'into'):
        d = c.get(part)
        if isinstance(d, dict):
            c[part] = dict((key(k), v) for k, v in d.items())
    webs = c.get('webs') or {}
    allw = [cfg.get('web')] + [webs.get(str(k)) for k in range(1, n)]
    if all(isinstance(w, dict) for w in allw):
        allw = list(reversed(allw))
        cfg['web'] = allw[0]
        c['webs'] = dict((str(k), allw[k]) for k in range(1, n))
    return cfg


def wall_cfg(cfg, k):
    """Configuracion con la que se calcula el muro k de la cadena: igual a cfg salvo el alma, si cada muro
    tiene la suya (el muro 0 usa cfg['web'])."""
    if k in ('A', 'B'):
        k = 0 if k == 'A' else 1
    c = cfg.get('corner') or {}
    wb = (c.get('webs') or {}).get(str(k))
    if k and not c.get('web_same', True) and isinstance(wb, dict):
        out = dict(cfg)
        out['web'] = wb
        return out
    return cfg


def leg_len(cfg, key):
    return float(((cfg.get('corner') or {}).get('len') or {}).get(key, 0.0) or 0.0)


def free_len(cfg, key):
    return leg_len(cfg, key)


def free_side(cs, key):
    """Lado ('L' inicio / 'R' fin) del extremo libre key del muro (el opuesto a su esquina)."""
    k = key_wall(key)
    if k == 0:
        d = cs['corners'][0]['A']
    else:
        d = cs['corners'][-1]['B']
    return 'R' if d['ds'] > 0 else 'L'


def _junc(cn, cfg, w, key):
    d = cn[w]
    s1 = d['s0'] + d['ds'] * leg_len(cfg, key)
    if d['ds'] > 0:
        return 'L', max(s1, 0.0)
    return 'R', max(d['L'] - s1, 0.0)


def junctions(cs, cfg, k):
    """{lado: longitud} que ocupan las esquinas en el muro k, medida desde su extremo."""
    out = {}
    if k >= 1:
        side, ln = _junc(cs['corners'][k - 1], cfg, 'B', 'C%dB' % (k - 1))
        out[side] = ln
    if k <= len(cs['corners']) - 1:
        side, ln = _junc(cs['corners'][k], cfg, 'A', 'C%dA' % k)
        out[side] = ln
    return out


def plan_axes(cn, rot=0):
    """Matriz de planta -> dibujo: (X, Y) = pa * (m[0], m[1]) + pb * (m[2], m[3]); X a la derecha, Y arriba,
    como se ve en planta (ejes del modelo), girada rot * 90 grados antihorario."""
    ax, ay, bx, by = cn.get('plan') or (1.0, 0.0, 0.0, 1.0)
    # B exactamente perpendicular a A (mismo sentido)
    sg = 1.0 if (ax * by - ay * bx) >= 0 else -1.0
    bx, by = -ay * sg, ax * sg
    for _ in range(int(rot) % 4):
        ax, ay = -ay, ax
        bx, by = -by, bx
    return ax, ay, bx, by


def rot_axes(rot=0):
    """Giro del dibujo (multiplos de 90 grados) como matriz (X, Y) -> (X', Y')."""
    return plan_axes({'plan': (1.0, 0.0, 0.0, 1.0)}, rot)


def plan_to_draw(m, pa, pb):
    return pa * m[0] + pb * m[2], pa * m[1] + pb * m[3]


def draw_to_plan(m, X, Y):
    return X * m[0] + Y * m[1], X * m[2] + Y * m[3]


def plan_to_world(cn, pa, pb):
    """Planta de la esquina -> planta comun de la cadena (cm, ejes del modelo)."""
    m = plan_axes(cn, 0)
    ox, oy = cn.get('O') or (0.0, 0.0)
    X, Y = plan_to_draw(m, pa, pb)
    return ox + X, oy + Y


def world_to_plan(cn, X, Y):
    m = plan_axes(cn, 0)
    ox, oy = cn.get('O') or (0.0, 0.0)
    return draw_to_plan(m, X - ox, Y - oy)


def leg_wall(key):
    return parse_leg(key)[1]


def leg_corner(cs, key):
    return cs['corners'][parse_leg(key)[0]]


def leg_section(cs, cfg, key):
    return CG.Section('rect', max(leg_len(cfg, key), 1.0), leg_corner(cs, key)[leg_wall(key)]['t'])


def leg_to_plan(cs, cfg, key, cx, cy):
    """Coordenadas de la seccion del brazo (centro) -> planta de su esquina (pa, pb)."""
    cn = leg_corner(cs, key)
    lb = leg_len(cfg, key)
    if leg_wall(key) == 'A':
        return lb / 2.0 + cx, cn['A']['t'] / 2.0 + cy
    return cn['B']['t'] / 2.0 + cy, lb / 2.0 + cx


def plan_to_leg(cs, cfg, key, pa, pb):
    cn = leg_corner(cs, key)
    lb = leg_len(cfg, key)
    if leg_wall(key) == 'A':
        return pa - lb / 2.0, pb - cn['A']['t'] / 2.0
    return pb - lb / 2.0, pa - cn['B']['t'] / 2.0


def leg_to_world(cs, cfg, key, cx, cy):
    return plan_to_world(leg_corner(cs, key), *leg_to_plan(cs, cfg, key, cx, cy))


def world_to_leg(cs, cfg, key, X, Y):
    return plan_to_leg(cs, cfg, key, *world_to_plan(leg_corner(cs, key), X, Y))


def in_leg(cs, cfg, key, pa, pb, tol=0.0):
    """(pa, pb) en la planta de la esquina del brazo."""
    cn = leg_corner(cs, key)
    lb = leg_len(cfg, key)
    if leg_wall(key) == 'A':
        return -tol <= pa <= lb + tol and -tol <= pb <= cn['A']['t'] + tol
    return -tol <= pb <= lb + tol and -tol <= pa <= cn['B']['t'] + tol


def in_leg_world(cs, cfg, key, X, Y, tol=0.0):
    return in_leg(cs, cfg, key, *world_to_plan(leg_corner(cs, key), X, Y), tol=tol)


def plan_to_wall(cn, w, pa, pb):
    d = cn[w]
    if w == 'A':
        return d['s0'] + d['ds'] * pa, d['xs'] * (pb - d['t'] / 2.0)
    return d['s0'] + d['ds'] * pb, d['xs'] * (pa - d['t'] / 2.0)


def wall_to_plan(cn, w, s, x):
    d = cn[w]
    along = (s - d['s0']) * d['ds']
    across = x * d['xs'] + d['t'] / 2.0
    return (along, across) if w == 'A' else (across, along)


def wall_corner(cs, k):
    """(esquina, 'A' / 'B') desde donde se ubica el muro k en la planta comun."""
    if k < len(cs['corners']):
        return cs['corners'][k], 'A'
    return cs['corners'][k - 1], 'B'


def wall_to_world(cs, k, s, x):
    cn, w = wall_corner(cs, k)
    return plan_to_world(cn, *wall_to_plan(cn, w, s, x))


def owner(cn, pa, pb, tol=0.5):
    """Muro cuyo concreto contiene el punto de la planta (el cuadro de la esquina es del que lo contiene)."""
    for w in ('A', 'B'):
        s, x = plan_to_wall(cn, w, pa, pb)
        d = cn[w]
        if -tol <= s <= d['L'] + tol and abs(x) <= d['t'] / 2.0 + tol:
            return w
    return 'A' if pb <= cn['A']['t'] else 'B'


def corner_plan(cs, cfg, zones, cover, ds):
    """Varillas y estribos de las esquinas: bars [(k, pa, pb, bt, key)], sts [{'k', 'plan', ...}] en la planta
    de cada esquina k. Las varillas repetidas en el cuadro de una esquina (puestas desde sus dos brazos) se
    cuentan una vez."""
    cs = chain_of(cs)
    bars, sts = [], []
    for key in leg_keys(cs):
        if leg_len(cfg, key) <= 0.5:
            continue
        k = parse_leg(key)[0]
        sec = leg_section(cs, cfg, key)
        bl, sl = zones.get(key, ([], []))
        for b in bl:
            for cx, cy in bundle_members(b, sec, cover, ds, sl):
                pa, pb = leg_to_plan(cs, cfg, key, cx, cy)
                if any(q[0] == k and math.hypot(pa - q[1], pb - q[2]) < b.dm * 0.6 for q in bars):
                    continue
                bars.append((k, pa, pb, b.bt, key))
        for st in sl:
            ev = CG.evaluate_stirrup(st, sec, cover, ds)
            pp = [leg_to_plan(cs, cfg, key, x, y) for x, y in ev['pts']]
            if len(pp) >= (3 if ev['closed'] else 2):
                sts.append({'k': k, 'plan': pp, 'closed': ev['closed'], 'kind': st.kind, 'leg': key})
    return bars, sts


def corner_split(cs, cfg, zones, cover, ds):
    """Reparte las esquinas entre los muros de la cadena: lista (un extra por muro) para compute(). Cada
    varilla y estribo va al muro que contiene su punto (el estribo, su centro); 'junc' = {lado: longitud}
    de los extremos que llegan a una esquina (el alma empieza despues); 'free' = extremo libre."""
    cs = chain_of(cs)
    n = n_walls(cs)
    out = []
    fks = free_keys(cs)
    for k in range(n):
        e = {'junc': junctions(cs, cfg, k), 'bars': [], 'st': [], 'free': None}
        for fk in fks:
            if key_wall(fk) == k:
                fb, fs = zones.get(fk, ([], []))
                e['free'] = {'len': free_len(cfg, fk), 'bars': list(fb), 'sts': list(fs), 'key': fk,
                             'into': bool(((cfg.get('corner') or {}).get('into') or {}).get(fk, True))}
        out.append(e)
    bars, sts = corner_plan(cs, cfg, zones, cover, ds)
    for k, pa, pb, bt, key in bars:
        cn = cs['corners'][k]
        w = owner(cn, pa, pb)
        s, x = plan_to_wall(cn, w, pa, pb)
        out[k + (1 if w == 'B' else 0)]['bars'].append((s, x, bt))
    for st in sts:
        cn = cs['corners'][st['k']]
        m = len(st['plan'])
        ca = sum(p[0] for p in st['plan']) / m
        cb = sum(p[1] for p in st['plan']) / m
        w = owner(cn, ca, cb)
        out[st['k'] + (1 if w == 'B' else 0)]['st'].append(
            {'pts': [plan_to_wall(cn, w, pa, pb) for pa, pb in st['plan']], 'closed': st['closed'], 'kind': st['kind']})
    return out


def compute(model, cfg, zones, bts, extra=None):
    """zones: {'L': (bars, stirrups), 'R': (bars, stirrups)} ya efectivos. extra: parte de una esquina en L
    (corner_split) que reemplaza al elemento de borde del extremo extra['end'].
    Devuelve dict con v (verticales), h (horizontales), u (horquillas),
    diag, st (estribos), warnings."""
    c = float(cfg['cover'])
    g = cfg['gen']
    L, H, t = model.L, model.H, model.t
    loops = model.loops
    res = {'v': [], 'h': [], 'u': [], 'diag': [], 'st': [], 'warnings': [], 'zones': {}}
    warns = res['warnings']
    bst = cfg['bstir']
    sbt = bts.get(bst['bt'])
    ds = sbt['dm'] if sbt else 0.0
    xs_v, xs_h, dv, dh = web_x(model, cfg, bts)
    res['xs_v'], res['xs_h'] = xs_v, xs_h

    def legdir(s, x):
        if g['hook_dir'] == 'long':
            return (1.0 if s < L / 2.0 else -1.0, 0.0)
        sx = 1.0 if x >= 0 else -1.0
        return (0.0, sx) if g['hook_dir'] == 'out' else (0.0, -sx)

    user_v = CG.LP.parse_positions(g.get('lap_pos_v', u'')) or []
    stock = float(g.get('stock', 1200.0)) if g.get('stock_on', True) else None

    above = getattr(model, 'above', None) or []
    fit_on = g.get('top_fit', True)

    def no_fit(s, x, db):
        """La espera no cabe en el muro de arriba (mas delgado o desfasado): remata en la corona con pata."""
        if not fit_on or x is None or not above:
            return False
        over = [a for a in above if a['s0'] - 0.5 <= s <= a['s1'] + 0.5]
        if not over:
            return False                      # sin muro encima en esa posicion: la espera sigue
        return not any(a['x0'] + c - 0.5 <= x - db / 2.0 and x + db / 2.0 <= a['x1'] - c + 0.5 for a in over)

    lv_up = level_above(model) if g.get('lap_level', True) else None
    lvl_top = lv_up[1] if lv_up else None
    # sin muro ni columna arriba: la espera remata con pata en la corona (o dentro de la losa / trabe de arriba)
    parts = getattr(model, 'top_parts', None)
    cont_on = bool(g.get('top_cont', True) and parts is not None and CT is not None)

    def top_end(s, x, z1):
        """(motivo, z del remate) si la espera no sigue: ('none', corona) / ('anchor', cara superior) o None."""
        if not cont_on or x is None:
            return None
        kind, zt = CT.classify(parts, s, x, z1)
        if kind == 'cont':
            return None
        return kind, zt

    def v_pieces(s, db, trim_all=False, zr=None, x=None):
        # fuera del muro (dentro del muro transversal) se toma la altura del extremo
        segs = segs_v(loops, min(max(s, 0.5), L - 0.5))
        if not segs:
            return []
        ztop = segs[-1][1]
        out = []
        for z0, z1 in segs:
            base = z0 <= 1.0
            top = z1 >= ztop - 0.5
            if trim_all:
                a, b = z0 + c, z1 - c
                if zr is not None:
                    a, b = max(a, zr[0]), min(b, zr[1])
                if b - a > 10.0:
                    out.append((a, b, False, False, None))
                continue
            lap = g['lap_f'] * db
            # sin extension (0 o negativa) la varilla respeta el recubrimiento en la base / la corona del muro;
            # con pata, el eje de la pata queda medio diametro mas adentro (la pata no se sale del concreto)
            eb, et = float(g['ext_bot']), float(g['ext_top'])
            if base:
                a = -eb if eb > 0 else max(-eb, c + (db / 2.0 if g['hook_bot'] else 0.0))
            else:
                a = z0 + c
            remate = bool(top and g.get('top_lap') and no_fit(s, x, db))
            why = 'fit' if remate else None
            if top and g.get('top_lap') and not remate:
                te = top_end(s, x, z1)
                if te is not None:
                    remate, why = True, te[0]
            if remate:
                b = (te[1] if why == 'anchor' else z1) - c - db / 2.0
            elif top and g.get('top_lap'):
                # muro confinado por una trabe: la espera sale del nivel superior, no de la corona
                b = (lvl_top if lvl_top is not None else z1) + lap
            elif top:
                b = z1 + et if et > 0 else min(z1 + et, z1 - c - (db / 2.0 if g['hook_top'] else 0.0))
            else:
                b = z1 - c
            if b - a < 10.0:
                continue
            style = CG.LP.lap_style(g)
            for i, (za, zb, fst, lst, zc) in enumerate(CG.long_segments(a, b, model.inner_levels, g['split'], lap,
                                                                         user_v, stock)):
                # 5o valor: z de la bayoneta, 'S' si la pieza va desfasada, o None
                mark = zc if style == 'crank' else ('S' if (style == 'offset' and i % 2) else None)
                rm = bool(remate and lst)
                out.append((za, zb, bool(g['hook_bot'] and base and fst), bool((g['hook_top'] or rm) and top and lst),
                            mark, rm, why if rm else None))
        return out

    def crank_dir(s, x, ld):
        """Direccion (ds, dx) de la bayoneta, en el plano de la varilla y hacia el interior."""
        if ld and abs(ld[0]) > 0.5:
            return (1.0 if s < L / 2.0 else -1.0, 0.0)
        return (0.0, -1.0 if x >= 0 else 1.0) if abs(x) > 1e-6 else (0.0, 1.0)

    def add_v(kind, bt, x, ss, pieces, ld):
        for pc in pieces:
            za, zb, l0, l1, zc = pc[:5]
            ld1 = ld
            if len(pc) > 5 and pc[5]:
                # remate bajo un muro mas delgado: la pata hacia el interior del muro
                ld1 = (0.0, -1.0 if x > 0 else 1.0) if abs(x) > 1e-6 else ld
            crank = (zc, crank_dir(ss[0], x, ld)) if isinstance(zc, (int, float)) else None
            # desfasado: a lo largo del muro, hacia el centro (queda en la misma parrilla)
            shift = ((1.0 if sum(ss) / len(ss) < L / 2.0 else -1.0), 0.0) if zc == 'S' else None
            leg1_len = None
            if len(pc) > 6 and pc[6] in ('none', 'fit') and abs(x) > 1e-6:
                # remate en la corona: la pata no sale por la cara opuesta (punta al recubrimiento)
                leg1_len = max(min(float(g['hook_len']), abs(x) + t / 2.0 - c), 2.0 * bt['dm'])
            res['v'].append({'kind': kind, 'bt': bt, 'x': x, 'ss': list(ss), 'z0': za, 'z1': zb,
                             'leg0': ld if l0 else None, 'leg1': ld1 if l1 else None,
                             'leg_len': g['hook_len'], 'leg1_len': leg1_len, 'crank': crank, 'shift': shift,
                             'remate': bool(len(pc) > 5 and pc[5]), 'remate_why': pc[6] if len(pc) > 6 else None})

    # ------------------------------------------------ elementos de borde
    lbL = zone_len(cfg, 'L')
    lbR = zone_len(cfg, 'R')
    # esquinas: extremos que llegan a una esquina ('junc' = {lado: longitud}; formato anterior 'end' / 'len')
    junc = {}
    if extra:
        junc = dict(extra.get('junc') or {})
        if not junc and extra.get('end') in ('L', 'R'):
            junc[extra['end']] = float(extra.get('len') or 0.0)
    if 'L' in junc:
        lbL = float(junc['L'] or 0.0)
    if 'R' in junc:
        lbR = float(junc['R'] or 0.0)
    free = extra.get('free') if extra else None
    fside = None
    if free is not None:
        # el otro extremo del muro lleva su propio elemento de borde (extremo libre)
        fside = free.get('side') or ('R' if 'L' in junc else 'L')
        if fside == 'L':
            lbL = float(free.get('len') or 0.0)
        else:
            lbR = float(free.get('len') or 0.0)
    # interseccion: el elemento de borde puede entrar en el muro transversal del extremo (Lb desde su pano lejano)
    ext = {}
    for key in ('L', 'R'):
        if key in junc:
            ext[key] = 0.0
        elif free is not None:
            ext[key] = side_ext(model, cfg, key, free.get('into', True))
        else:
            ext[key] = side_ext(model, cfg, key)
    res['ext'] = ext
    inL = max(lbL - ext['L'], 0.0) if lbL > 0.5 else 0.0          # lo que ocupa dentro del muro
    inR = max(lbR - ext['R'], 0.0) if lbR > 0.5 else 0.0
    if inL + inR > L - 10.0 and (lbL > 0 or lbR > 0):
        warns.append(u'Los elementos de borde se traslapan (Lb izq + Lb der > longitud del muro)')
    for key, lb in (('L', lbL), ('R', lbR)):
        if lb <= 0.5 or key in junc:
            continue
        sec = CG.Section('rect', max(lb, 1.0), model.t)
        bars, sts = (free.get('bars') or [], free.get('sts') or []) if free is not None else zones.get(key, ([], []))
        res['zones'][key] = {'len': lb, 'sec': sec}
        for b in bars:
            for cx, cy in bundle_members(b, sec, c, ds, sts):
                s, x = side_to_wall(L, key, lb, cx, cy)
                s = shift_side(key, s, ext[key])
                add_v('bnd', b.bt, x, [s], v_pieces(s, b.dn, x=x), legdir(s, x))
        if bst['on'] and sbt is not None and sts:
            for st in sts:
                ev = CG.evaluate_stirrup(st, sec, c, ds)
                pts = [side_to_wall(L, key, lb, px, py) for px, py in ev['pts']]
                pts = [(shift_side(key, a_, ext[key]), b_) for a_, b_ in pts]
                if len(pts) < (3 if ev['closed'] else 2):
                    continue
                for za, zb in model.stories():
                    for z0, n, sp, zone in stirrup_sets_wall(za, zb, bst['lc'], bst['sc'], bst['sm'],
                                                            bst['first'], bst['top_zone']):
                        res['st'].append({'zone': key, 'pts': pts, 'closed': ev['closed'], 'kind': st.kind, 'dn': sbt['dn'],
                                          'z0': z0, 'n': int(n), 'sp': sp, 'tag': zone})

    # ------------------------------------------------ esquina en L (la parte que cae en este muro)
    if extra:
        for s, x, bt in extra.get('bars') or ():
            add_v('bnd', bt, x, [s], v_pieces(s, bt['dn'], x=x), legdir(s, x))
        if bst['on'] and sbt is not None:
            for st in extra.get('st') or ():
                for za, zb in model.stories():
                    for z0, n, sp, zone in stirrup_sets_wall(za, zb, bst['lc'], bst['sc'], bst['sm'],
                                                            bst['first'], bst['top_zone']):
                        res['st'].append({'zone': 'C', 'pts': list(st['pts']), 'closed': st['closed'],
                                          'kind': st['kind'], 'dn': sbt['dn'], 'z0': z0, 'n': int(n), 'sp': sp,
                                          'tag': zone})

    # ------------------------------------------------ alma: verticales
    w = cfg['web']
    vbt = bts.get(w['v_bt'])
    hbt = bts.get(w['h_bt'])
    if vbt is not None and w.get('v_on', True):
        endc = c + (dh if (w['outer'] == 'h' and int(w['curtains']) >= 2) else 0.0) + dv / 2.0
        a = inL if inL > 0.5 else endc
        b = L - inR if inR > 0.5 else L - endc
        pos = _even(a, b, max(w['v_sp'], 5.0), incl_a=inL <= 0.5, incl_b=inR <= 0.5)
        for x in xs_v:
            sigs = []
            pcs = []
            for s in pos:
                pc = v_pieces(s, vbt['dn'], x=x)
                ld = legdir(s, x)
                pcs.append(pc)
                sigs.append((tuple((round(p[0], 1), round(p[1], 1), p[2], p[3], p[4], p[5]) for p in pc), ld))
            for grp, sg in _group(pos, sigs):
                pc = pcs[pos.index(grp[0])]
                add_v('web', vbt, x, grp, pc, sg[1])

    # ------------------------------------------------ alma: horizontales
    if hbt is not None and w.get('h_on', True):
        hend = w['h_end']
        if hend == 'u' and int(w['curtains']) < 2:
            warns.append(u'Horquillas U requieren 2 parrillas; se usa gancho 90°')
            hend = 'hook90'
        zpos = _even(w['h_first'], H - w['h_first'], max(w['h_sp'], 5.0))
        off = c + dh / 2.0 + (dh if hend == 'u' else 0.0)
        for x in xs_h:
            maxleg = abs(x) + t / 2.0 - c - dh / 2.0
            leg = min(max(w['h_leg'] - dh / 2.0, dh), maxleg) if hend == 'hook90' else 0.0
            ldx = -1.0 if x > 0 else 1.0
            sigs = []
            for z in zpos:
                sg = []
                for s0, s1 in segs_h(loops, z):
                    a, b = s0 + off, s1 - off
                    if b - a > 10.0:
                        sg.append((round(a, 1), round(b, 1)))
                sigs.append(tuple(sg))
            for grp, sg in _group(zpos, sigs):
                for a, b in sg:
                    res['h'].append({'kind': 'web', 'bt': hbt, 'x': x, 'zs': list(grp), 's0': a, 's1': b,
                                     'leg0': (0.0, ldx) if leg else None, 'leg1': (0.0, ldx) if leg else None,
                                     'leg_len': leg + dh / 2.0 if leg else 0.0})
        _split_h(res, g, stock)
        if hend == 'u':
            xh = abs(xs_h[0])
            usigs = []
            for z in zpos:
                sg = []
                for s0, s1 in segs_h(loops, z):
                    if s1 - s0 > 2 * (c + dh) + 10.0:
                        sg.append((round(s0 + c + dh / 2.0, 1), 1.0))
                        sg.append((round(s1 - c - dh / 2.0, 1), -1.0))
                usigs.append(tuple(sg))
            for grp, sg in _group(zpos, usigs):
                for s, d in sg:
                    res['u'].append({'bt': hbt, 's': s, 'dir': d, 'zs': [z + dh for z in grp], 'x1': -xh,
                                     'x2': xh, 'leg': max(w['h_leg'] - dh / 2.0, 5.0)})

    # ------------------------------------------------ huecos
    op = cfg['open']
    for k, (s0, s1, z0, z1) in enumerate(model.openings):
        if (inL > 0.5 and s0 < inL) or (inR > 0.5 and s1 > L - inR):
            warns.append(u'El hueco %d invade un elemento de borde' % (k + 1))
    obt = bts.get(op['bt'])
    if op['on'] and obt is not None:
        db = obt['dm']
        gap = db + 2.5
        n = max(1, int(op['n']))
        for (s0, s1, z0, z1) in model.openings:
            door = z0 <= 1.0
            for side in (-1, 1):
                for k in range(n):
                    s = (s0 - c - db / 2.0 - k * gap) if side < 0 else (s1 + c + db / 2.0 + k * gap)
                    for x in xs_v:
                        pcs = v_pieces(s, obt['dn'], True, (z0 - op['ext'], z1 + op['ext']))
                        add_v('op', obt, x, [s], pcs, legdir(s, x))
            zl = [z1 + c + db / 2.0 + k * gap for k in range(n)]
            if not door:
                zl += [z0 - c - db / 2.0 - k * gap for k in range(n)]
            for z in zl:
                for x in xs_h:
                    for a, b in segs_h(loops, z):
                        lo = max(a + c, s0 - op['ext'])
                        hi = min(b - c, s1 + op['ext'])
                        if hi - lo > 10.0:
                            res['h'].append({'kind': 'op', 'bt': obt, 'x': x, 'zs': [z], 's0': lo, 's1': hi,
                                             'leg0': None, 'leg1': None, 'leg_len': 0.0})
        dbt = bts.get(op['diag_bt'])
        if op['diag'] and dbt is not None:
            r2 = math.sqrt(0.5)
            for (s0, s1, z0, z1) in model.openings:
                corners = [(s0, z1, -1, 1), (s1, z1, 1, 1)]
                if z0 > 1.0:
                    corners += [(s0, z0, -1, -1), (s1, z0, 1, -1)]
                for cs, cz, ws, wz in corners:
                    ws, wz = ws * r2, wz * r2
                    dcent = c * 1.42 + 6.0
                    ms, mz = cs + ws * dcent, cz + wz * dcent
                    ps, pz = -wz, ws
                    half = op['diag_L'] / 2.0
                    p0 = (ms - ps * half, mz - pz * half)
                    p1 = (ms + ps * half, mz + pz * half)
                    if point_in(loops, p0[0], p0[1]) and point_in(loops, p1[0], p1[1]):
                        for x in xs_v:
                            res['diag'].append({'bt': dbt, 'x': x, 'p0': p0, 'p1': p1})
                    else:
                        warns.append(u'Diagonal omitida en esquina (%.0f, %.0f): no cabe' % (cs, cz))
    nrm = sum(len(v['ss']) for v in res['v'] if v.get('remate') and v.get('remate_why') in (None, 'fit'))
    nnc = sum(len(v['ss']) for v in res['v'] if v.get('remate') and v.get('remate_why') == 'none')
    nan = sum(len(v['ss']) for v in res['v'] if v.get('remate') and v.get('remate_why') == 'anchor')
    if nnc:
        warns.append(u'%d vertical(es) sin muro ni columna arriba: rematan con pata en la corona' % nnc)
    if nan:
        warns.append(u'%d vertical(es) con solo losa / trabe arriba: rematan con pata dentro de ella' % nan)
    if nrm:
        names = u', '.join(sorted(set(u'%s (%s cm)' % (a.get('name', u''), round(a.get('t', 0), 1)) for a in above)))
        warns.append(u'%d vertical(es) rematan con pata en la corona: no caben en el muro de arriba %s; '
                     u'ancle ese muro con su Extensión inferior' % (nrm, names))
    return res


def _split_h(res, g, stock):
    """Parte las horizontales del alma por longitud comercial y por las posiciones del usuario
    (cm desde el inicio del muro). La pieza de la izquierda lleva la bayoneta hacia el centro."""
    user = CG.LP.parse_positions(g.get('lap_pos_h', u'')) or []
    out = []
    for h in res['h']:
        if h['kind'] != 'web':
            out.append(h)
            continue
        lap = g['lap_f'] * h['bt']['dn']
        legs = (h['leg_len'] or 0.0) * (int(bool(h['leg0'])) + int(bool(h['leg1'])))
        span = h['s1'] - h['s0']
        st = (stock - legs) if stock else None
        cuts = [u - h['s0'] for u in user if h['s0'] + 5.0 < u < h['s1'] - 5.0]
        pcs = CG.LP.plan_pieces(span, cuts, lap, st)
        if len(pcs) <= 1:
            out.append(h)
            continue
        cdx = -1.0 if h['x'] >= 0 else 1.0
        for i, (a, b) in enumerate(pcs):
            hh = dict(h)
            hh['s0'], hh['s1'] = h['s0'] + a, h['s0'] + b
            hh['leg0'] = h['leg0'] if i == 0 else None
            hh['leg1'] = h['leg1'] if i == len(pcs) - 1 else None
            style = CG.LP.lap_style(g)
            hh['crank'] = (h['s0'] + pcs[i + 1][0], cdx) if (i < len(pcs) - 1 and style == 'crank') else None
            # desfasado: un diametro hacia arriba (queda en la misma parrilla)
            hh['shift'] = (0.0, 1.0) if (style == 'offset' and i % 2) else None
            hh['lap_piece'] = i
            out.append(hh)
    res['h'] = out


# ================================================================ resumen
def kg_m(d):
    return math.pi * (d / 100.0) ** 2 / 4.0 * STEEL


def summary(res, hook_a):
    w = {'bnd': 0.0, 'web_v': 0.0, 'web_h': 0.0, 'op': 0.0, 'st': 0.0}
    n_st = 0
    for v in res['v']:
        Lb = v['z1'] - v['z0'] + (v['leg_len'] if v['leg0'] else 0.0) + (v['leg_len'] if v['leg1'] else 0.0)
        key = 'bnd' if v['kind'] == 'bnd' else ('op' if v['kind'] == 'op' else 'web_v')
        w[key] += len(v['ss']) * Lb / 100.0 * kg_m(v['bt']['dn'])
    for h in res['h']:
        Lh = h['s1'] - h['s0'] + (h['leg_len'] if h['leg0'] else 0.0) + (h['leg_len'] if h['leg1'] else 0.0)
        key = 'op' if h['kind'] == 'op' else 'web_h'
        w[key] += len(h['zs']) * Lh / 100.0 * kg_m(h['bt']['dn'])
    for u in res['u']:
        Lu = 2 * u['leg'] + (u['x2'] - u['x1'])
        w['web_h'] += len(u['zs']) * Lu / 100.0 * kg_m(u['bt']['dn'])
    for d in res['diag']:
        Ld = math.hypot(d['p1'][0] - d['p0'][0], d['p1'][1] - d['p0'][1])
        w['op'] += Ld / 100.0 * kg_m(d['bt']['dn'])
    for st in res['st']:
        pts = st['pts']
        n = len(pts)
        rng = range(n) if st['closed'] else range(n - 1)
        Ls = sum(math.hypot(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1]) for i in rng)
        Ls += 2 * (hook_a or 0.0)
        n_st += st['n']
        w['st'] += st['n'] * Ls / 100.0 * kg_m(st.get('dn', 0.95))
    return w, n_st


def parse_num(text, default=None):
    try:
        s = (u'%s' % text).strip().replace(',', '.')
        return float(s) if s else default
    except Exception:
        return default
