# -*- coding: utf-8 -*-
"""Calculo puro del armado de vigas (sin Revit ni WPF).

Unidades internas: cm.
Coordenadas: s = a lo largo de la viga (0 = inicio fisico de la cadena),
x = ancho (0 = eje de la seccion, + hacia la izquierda mirando en +s),
y = altura (0 = centro de la seccion, + hacia arriba).
Compatible con IronPython 2.7 y CPython 3.
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
STEEL_KG_M3 = 7850.0
CFG_VERSION = 1


# ================================================================ modelo
class Node(object):
    """Apoyo o extremo libre. s0 < s1 para apoyos; s0 == s1 en extremos libres."""

    def __init__(self, s0, s1, name=u'', free=False, kind=u'columna'):
        self.s0 = float(s0)
        self.s1 = float(s1)
        self.name = name
        self.free = bool(free)
        self.kind = kind
        self.zparts = None         # apoyo extremo: [(s0, s1, y0, y1)] de cada elemento (dado, pila...)

    @property
    def width(self):
        return self.s1 - self.s0

    @property
    def mid(self):
        return (self.s0 + self.s1) / 2.0

    def __repr__(self):
        return u'Node(%s, %.1f-%.1f%s)' % (self.name, self.s0, self.s1, u', libre' if self.free else u'')


def same_section(a, b, tol=1.0):
    return all(abs(a[k] - b[k]) <= tol for k in ('b', 'h', 'xc', 'yc'))


def as_section(o):
    """Seccion {'b','h','xc','yc'} (cm) de un dict o de un BeamModel (envolvente centrada)."""
    if isinstance(o, dict):
        return o
    return {'b': o.b, 'h': o.h, 'xc': 0.0, 'yc': 0.0}


class BeamModel(object):
    """b, h: seccion envolvente (cm). L: longitud fisica de la cadena (cm).
    supports: lista de (s0, s1, nombre, tipo) en coordenadas de la cadena.
    parts: (s0, s1, b, h, xc, yc) de cada viga de la cadena (cm; xc, yc = centro de su seccion
    respecto al centro de la envolvente). Cada tramo toma la seccion de la viga que lo ocupa."""

    def __init__(self, b, h, L, supports, tol=5.0, parts=None, bends=None, end_parts=None, vkinks=None):
        self.b = float(b)
        self.h = float(h)
        self.L = float(L)
        sup = sorted([Node(s0, s1, n, False, k) for (s0, s1, n, k) in supports], key=lambda n: n.s0)
        nodes = []
        rest = list(sup)
        if rest and rest[0].s0 <= tol and rest[0].s1 >= -tol:
            nodes.append(rest.pop(0))
        else:
            nodes.append(Node(0.0, 0.0, u'', True, u'libre'))
        end = None
        if rest and rest[-1].s1 >= self.L - tol and rest[-1].s0 <= self.L + tol:
            end = rest.pop(-1)
        for n in rest:
            if tol < n.mid < self.L - tol:
                nodes.append(n)
        nodes.append(end if end is not None else Node(self.L, self.L, u'', True, u'libre'))
        nodes.sort(key=lambda n: n.s0)
        k = 1
        for n in nodes:
            if not n.name:
                n.name = u'%d' % k if not n.free else u''
            k += 1
        self.nodes = nodes
        self.parts = []
        for q in (parts or []):
            self.parts.append({'s0': float(q[0]), 's1': float(q[1]), 'b': float(q[2]), 'h': float(q[3]),
                               'xc': float(q[4]), 'yc': float(q[5])})
        # cambio de seccion entre dos vigas sin apoyo en medio: nodo sin ancho ('cambio') que divide
        # el claro, para que cada zona tenga su armado
        ps = sorted(self.parts, key=lambda q: q['s0'])
        k = 1
        lo, hi = self.nodes[0].s1, self.nodes[-1].s0
        for A, B in zip(ps, ps[1:]):
            if same_section(A, B):
                continue
            pos = (A['s1'] + B['s0']) / 2.0
            if any(not n.free and n.s0 - 1.0 <= pos <= n.s1 + 1.0 for n in self.nodes):
                continue                                   # el cambio cae en un apoyo
            if not (lo + 1.0 < pos < hi - 1.0):
                continue
            self.nodes.append(Node(pos, pos, u'CS%d' % k, False, u'cambio'))
            k += 1
        # quiebre en planta sin apoyo (la viga gira lejos de un apoyo): nodo sin ancho que divide el claro
        self.kinks = []                      # quiebres en planta sin ancho: (s, nombre)
        for bd in (bends or []):
            if bd[1] - bd[0] < 1.0:
                pos = (bd[0] + bd[1]) / 2.0
                self.kinks.append((pos, bd[2] if len(bd) > 2 else u'Q'))
                if (len(bd) < 4 or bd[3]) and lo + 1.0 < pos < hi - 1.0 and \
                        not any(n.s0 - 1.0 <= pos <= n.s1 + 1.0 for n in self.nodes):
                    self.nodes.append(Node(pos, pos, bd[2] if len(bd) > 2 else u'Q', False, u'cambio'))
        # cambio de pendiente sin apoyo (viga horizontal seguida de una inclinada): nodo sin ancho que divide el
        # claro, para que ningun conjunto de estribos pase el quiebre. Las varillas siguen corridas (se doblan en
        # su plano vertical)
        self.vkinks = []
        for pos, name in (vkinks or []):
            self.vkinks.append((float(pos), name))
            if lo + 1.0 < pos < hi - 1.0 and not any(n.s0 - 1.0 <= pos <= n.s1 + 1.0 for n in self.nodes):
                self.nodes.append(Node(pos, pos, name, False, u'cambio'))
        self.nodes.sort(key=lambda n: n.s0)
        self.sections = [self._span_section(i) for i in range(self.n_spans)]
        # apoyos donde la viga cambia de eje (contratrabe desplazada o girada): (s0, s1) de cada uno.
        # Las varillas se doblan en planta dentro del apoyo o se anclan en el (set_bend_mode)
        self.bend_nodes = set()              # apoyos con cambio de eje (doblar en planta o anclar)
        self.kink_nodes = set()              # quiebres sin apoyo (siempre se doblan)
        for bd in (bends or []):
            a, b_ = bd[0], bd[1]
            m = (a + b_) / 2.0
            for ni in range(1, len(self.nodes) - 1):
                n = self.nodes[ni]
                if b_ - a < 1.0:
                    if n.kind == u'cambio' and abs(n.mid - m) <= 1.0:
                        self.kink_nodes.add(ni)
                elif not n.free and n.kind != u'cambio' and n.s0 - 1.0 <= m <= n.s1 + 1.0:
                    self.bend_nodes.add(ni)
        self.anchor_bends = False
        # apoyos extremos: elementos que lo forman con su altura, para que cada varilla llegue al pano
        # lejano del que esta a su altura (dado arriba, pila abajo...) y no salga del concreto
        if end_parts:
            n0, n1 = self.nodes[0], self.nodes[-1]
            if end_parts[0] and not n0.free and n0.s0 < 0.0:
                n0.zparts = [tuple(q) for q in end_parts[0]]
            if len(end_parts) > 1 and end_parts[1] and not n1.free and n1.s1 > self.L:
                n1.zparts = [tuple(q) for q in end_parts[1]]

    def set_bend_mode(self, mode):
        """'bend': las varillas siguen corridas y se doblan en planta en los apoyos con cambio de eje;
        'anchor': en esos apoyos cada lado termina recto (o con gancho) dentro del apoyo."""
        self.anchor_bends = (mode == 'anchor')

    def is_anchor_bend(self, ni):
        return self.anchor_bends and ni in self.bend_nodes

    def _span_section(self, i):
        a, b = self.nodes[i].s1, self.nodes[i + 1].s0
        best = None
        for q in self.parts:
            ov = min(b, q['s1']) - max(a, q['s0'])
            if best is None or ov > best[0]:
                best = (ov, q)
        if best is None or best[0] <= 0.0:
            return {'b': self.b, 'h': self.h, 'xc': 0.0, 'yc': 0.0}
        q = best[1]
        return {'b': q['b'], 'h': q['h'], 'xc': q['xc'], 'yc': q['yc']}

    def sec(self, i):
        return self.sections[max(0, min(int(i), self.n_spans - 1))]

    def is_change(self, ni):
        """Nodo de cambio de seccion sin apoyo."""
        return 0 < ni < len(self.nodes) - 1 and self.nodes[ni].kind == u'cambio'

    def is_joint(self, ni):
        """El apoyo ni separa tramos de seccion distinta (ancho, peralte, nivel o eje)."""
        return 0 < ni < len(self.nodes) - 1 and (not same_section(self.sections[ni - 1], self.sections[ni]) or
                                                  self.is_anchor_bend(ni))

    @property
    def joints(self):
        return [ni for ni in self.inner_supports if self.is_joint(ni)]

    def segments(self):
        """Grupos de tramos seguidos con la misma seccion: [(primer tramo, ultimo tramo)]."""
        out = []
        start = 0
        for ni in self.inner_supports:
            if self.is_joint(ni):
                out.append((start, ni - 1))
                start = ni
        out.append((start, self.n_spans - 1))
        return out

    @property
    def n_spans(self):
        return len(self.nodes) - 1

    @property
    def inner_supports(self):
        """Indices de nodos intermedios (apoyos entre tramos)."""
        return list(range(1, len(self.nodes) - 1))

    def span_faces(self, i):
        """Caras libres del tramo i (cm)."""
        return self.nodes[i].s1, self.nodes[i + 1].s0

    def anchor_start(self, node, cover):
        """Extremo de una varilla que nace dentro de 'node' y va hacia +s."""
        return node.s0 + cover

    def anchor_end(self, node, cover):
        """Extremo de una varilla que termina dentro de 'node' viniendo de -s."""
        return node.s1 - cover

    def extents(self):
        s0 = min(0.0, self.nodes[0].s0)
        s1 = max(self.L, self.nodes[-1].s1)
        return s0, s1


# ================================================================ secciones
def x_range(model, cover, ds, db):
    sc = as_section(model)
    return max(sc['b'] / 2.0 - cover - ds - db / 2.0, 0.0)


def y_row1(model, cover, ds, db, top):
    sc = as_section(model)
    y = sc['h'] / 2.0 - cover - ds - db / 2.0
    return sc['yc'] + (y if top else -y)


def spread(n, xr):
    n = int(n)
    if n <= 0:
        return []
    if n == 1:
        return [0.0]
    return [-xr + 2.0 * xr * k / (n - 1.0) for k in range(n)]


def is_uniform(xs, tol=0.05):
    if len(xs) < 3:
        return True
    d0 = xs[1] - xs[0]
    return all(abs((xs[k + 1] - xs[k]) - d0) < tol for k in range(len(xs) - 1))


def main_row(model, cover, ds, bt, n, top):
    sc = as_section(model)
    db = bt['dm']
    xr = x_range(sc, cover, ds, db)
    return {'xs': [sc['xc'] + x for x in spread(n, xr)], 'y': y_row1(sc, cover, ds, db, top), 'db': db}


def row_fits(row, db, model, cover, ds, tol=0.5):
    """La fila de varillas (xs, y) queda dentro de la zona de varillas de la seccion."""
    sc = as_section(model)
    xr = x_range(sc, cover, ds, db)
    yt = y_row1(sc, cover, ds, db, True)
    yb = y_row1(sc, cover, ds, db, False)
    return yb - tol <= row['y'] <= yt + tol and all(abs(x - sc['xc']) <= xr + tol for x in row['xs'])


def row_inside(row, db, model, cover, ds, tol=0.5):
    """La fila cabe en la zona de varillas de la seccion, o (escalon entre vigas de distinto nivel) queda
    dentro de su concreto: entre sus caras con al menos medio diametro y a lo ancho dentro de la zona de
    varillas. Asi, en un escalon como el de los planos (vigas encimadas en el apoyo) las varillas de cada
    viga siguen rectas su anclaje dentro de la otra en lugar de terminar con gancho."""
    if row_fits(row, db, model, cover, ds, tol):
        return True
    sc = as_section(model)
    xr = x_range(sc, cover, ds, db)
    lo = sc['yc'] - sc['h'] / 2.0 + db / 2.0
    hi = sc['yc'] + sc['h'] / 2.0 - db / 2.0
    return lo - tol <= row['y'] <= hi + tol and all(abs(x - sc['xc']) <= xr + tol for x in row['xs'])


def layer_rows(model, cover, ds, tmin, mrow, layers, bts, top):
    """Posicion (xs, y, db) de cada capa adicional.
    Capa 1: en los huecos entre las varillas corridas si caben; si no, en
    un lecho interior. Capas siguientes: un lecho interior cada una."""
    sign = -1.0 if top else 1.0                      # hacia el interior
    gaps = []
    mx = sorted(mrow['xs'])
    for k in range(len(mx) - 1):
        gaps.append((mx[k] + mx[k + 1]) / 2.0)
    gaps.sort(key=lambda g: (abs(g), g))
    out = []
    warns = []
    cur_y = mrow['y']
    cur_db = mrow['db']
    in_row1 = True
    for i, ly in enumerate(layers):
        bt = bts.get(ly['bt'])
        if bt is None:
            out.append(None)
            warns.append(u'Capa %d: tipo de varilla "%s" no existe' % (i + 1, ly['bt']))
            continue
        db = bt['dm']
        n = int(ly['n'])
        if i == 0 and 0 < n <= len(gaps):
            xs = sorted(gaps[:n])
            y = y_row1(model, cover, ds, db, top)
            # separacion libre minima con las corridas
            if len(mx) > 1:
                clear = min(abs(x - m) for x in xs for m in mx) - (db + mrow['db']) / 2.0
                if clear < tmin - 0.01:
                    warns.append(u'Capa 1: separación libre %.1f cm < t min' % clear)
            cur_db = max(cur_db, db)
            out.append({'xs': xs, 'y': y, 'db': db})
            continue
        if i == 0:
            in_row1 = False
        y = cur_y + sign * (cur_db / 2.0 + tmin + db / 2.0)
        xr = x_range(model, cover, ds, db)
        out.append({'xs': [as_section(model)['xc'] + x for x in spread(n, xr)], 'y': y, 'db': db})
        cur_y = y
        cur_db = db
    return out, warns


# ================================================================ config
def round_up(v, step):
    return math.ceil(v / step - 1e-9) * step


def closest_bt(bts_list, d):
    best = None
    for bt in bts_list:
        e = abs(bt['dn'] - d)
        if best is None or e < best[0]:
            best = (e, bt)
    return best[1]['name'] if best else u''


def add_default(model):
    """Refuerzo adicional (superior o inferior): INICIO / FINAL en los extremos de la viga, CENTRO
    sobre cada apoyo intermedio ('mid', L1/L2 desde el eje) o en el centro de cada tramo ('span',
    L1/L2 desde el centro del claro)."""
    return {'start': [], 'end': [], 'mid': [[] for _ in model.inner_supports],
            'span': [[] for _ in range(model.n_spans)]}


def convert_old_bot_add(old, model, cover):
    """Formato anterior del refuerzo inferior (por tramo: start / mid / end) -> INICIO / CENTRO / FINAL.
    Las varillas quedan en el mismo lugar; las que nacian en un apoyo intermedio pasan a CENTRO sobre
    ese apoyo (sin pata)."""
    new = add_default(model)
    inner = model.inner_supports
    ns = model.n_spans
    for i in range(min(len(old), ns)):
        o = old[i] or {}
        fa, fb = model.span_faces(i)
        c = (fa + fb) / 2.0
        for ly in o.get('mid') or []:
            new['span'][i].append({'bt': ly['bt'], 'n': ly['n'], 'L1': max(c - (fa + ly['L1']), 0.0),
                                   'L2': max((fb - ly['L2']) - c, 0.0)})
        for ly in o.get('start') or []:
            if i == 0:
                new['start'].append(dict(ly))
            elif i in inner:
                nd = model.nodes[i]
                s0 = model.anchor_start(nd, cover) - ly.get('Ln', 0.0)
                new['mid'][inner.index(i)].append({'bt': ly['bt'], 'n': ly['n'], 'L1': max(nd.mid - s0, 0.0),
                                                   'L2': max(fa + ly['L'] - nd.mid, 0.0)})
        for ly in o.get('end') or []:
            if i == ns - 1:
                new['end'].append(dict(ly))
            elif (i + 1) in inner:
                nd = model.nodes[i + 1]
                s1 = model.anchor_end(nd, cover) + ly.get('Ln', 0.0)
                new['mid'][inner.index(i + 1)].append({'bt': ly['bt'], 'n': ly['n'],
                                                       'L1': max(nd.mid - (fb - ly['L']), 0.0),
                                                       'L2': max(s1 - nd.mid, 0.0)})
    return new


def main_cfg(bt_name, n, La, Lb):
    """La / Lb: patas en los extremos (en un cambio de seccion, la pata si la varilla no cabe).
    Lna / Lnb: prolongacion en los extremos de la viga. Lj0 / Lj1: largo recto dentro del otro tramo
    en un cambio de seccion (None = el general de Configuracion)."""
    return {'bt': bt_name, 'n': int(n), 'La': float(La), 'Lb': float(Lb), 'Lna': 0.0, 'Lnb': 0.0,
            'Lj0': None, 'Lj1': None}


SIDE_HOOKS = ('none', 'down', 'in')      # gancho de la barra central: ninguno, vertical, horizontal al centro
SIDE_ROWS_MAX = 20


def side_default(bt):
    return {'on': False, 'bt': bt, 'rows': 1, 'sp': 0.0, 'exl': 0.0, 'exr': 0.0}


def stir_group_default():
    return {'left': u'10@10,5@15,5@20', 'mid': 30.0, 'right': u''}


STIR_KINDS = ('none', 'inner', 'overlap')    # sencillo, perimetral + interior, dos traslapados
AXIS_MODES = ('bend', 'anchor')              # cambio de eje en un apoyo: doblar en planta / anclar en el apoyo


def stir_span_default():
    return {'bt': u'', 'double': 'none'}     # bt vacio = el de Configuracion


def default_cfg(model, bts_list):
    t5 = closest_bt(bts_list, 1.59)
    t3 = closest_bt(bts_list, 0.95)
    leg = round_up(12 * 1.59, 5.0)
    ns = model.n_spans
    return {
        'version': CFG_VERSION,
        'cover': 4.0, 'tmin': 3.0, 'stir_bt': t3, 'partition': u'',
        'views': {'elev': False, 'elev_tpl': u'', 'cross': False, 'cross_tpl': u''},
        'top_style': 'single',
        'top_single': main_cfg(t5, 2, leg, leg),
        'top_zones': [],
        'top_spans': [main_cfg(t5, 2, leg if i == 0 else 0.0, leg if i == ns - 1 else 0.0)
                      for i in range(ns)],
        'bot_style': 'single',
        'bot_single': main_cfg(t5, 2, leg, leg),
        'bot_zones': [],
        'bot_spans': [main_cfg(t5, 2, leg if i == 0 else 0.0, leg if i == ns - 1 else 0.0)
                      for i in range(ns)],
        'top_add': add_default(model),
        'bot_add': add_default(model),
        'side_style': 'single',
        'side_single': side_default(t3),
        'side': [side_default(t3) for _ in range(ns)],
        'side_hook': {'dir': 'down', 'L': round_up(12 * 0.95, 5.0)},
        'stirrups': {'on': True, 'hook': u'', 'lc': round_up(2 * model.h, 5.0), 'sc': 10.0,
                     'sm': 20.0, 'first': 5.0, 'mode': 'zones',
                     'groups': [stir_group_default() for _ in range(ns)],
                     'spans': [stir_span_default() for _ in range(ns)]},
        'joint': {'L': 100.0},
        'axis_mode': 'bend',
        'mid_ref': 'axis',
        'lap': {'k': 40.0, 'stock_on': True, 'stock': 1200.0, 'crank': False, 'pos_top': u'', 'pos_bot': u''},
    }


def _fit_list(lst, n, factory):
    lst = list(lst or [])
    if not lst:
        return [factory() for _ in range(n)]
    while len(lst) < n:
        lst.append(copy.deepcopy(lst[-1]))
    return lst[:n]


def fit_cfg(cfg, model, bts_list):
    """Ajusta una configuracion (p. ej. importada) al numero de tramos/apoyos
    de la viga actual y completa claves faltantes."""
    base = default_cfg(model, bts_list)
    out = copy.deepcopy(base)
    for k, v in cfg.items():
        if k in ('views', 'stirrups', 'top_single', 'bot_single', 'side_single', 'side_hook', 'top_add',
                 'lap', 'joint') and isinstance(v, dict):
            out[k].update(copy.deepcopy(v))
        else:
            out[k] = copy.deepcopy(v)
    # configuraciones anteriores: inferior y piel por tramo, sin gancho en la piel
    if cfg and 'bot_style' not in cfg and cfg.get('bot_spans'):
        out['bot_style'] = 'multi'
        out['bot_single'] = copy.deepcopy(cfg['bot_spans'][0])
        out['bot_single']['Lb'] = cfg['bot_spans'][-1].get('Lb', out['bot_single'].get('Lb', 0.0))
        out['bot_single']['Lnb'] = cfg['bot_spans'][-1].get('Lnb', 0.0)
    if cfg and 'side_style' not in cfg and cfg.get('side'):
        out['side_style'] = 'multi'
        out['side_hook']['dir'] = 'none'
    if out.get('side_hook', {}).get('dir') not in SIDE_HOOKS:
        out['side_hook']['dir'] = 'none'
    ns = model.n_spans
    nm = len(model.inner_supports)
    out['top_spans'] = _fit_list(out.get('top_spans'), ns, lambda: copy.deepcopy(base['top_spans'][0]))
    out['bot_spans'] = _fit_list(out.get('bot_spans'), ns, lambda: copy.deepcopy(base['bot_spans'][0]))
    if isinstance(out.get('bot_add'), list):
        out['bot_add'] = convert_old_bot_add(out['bot_add'], model, float(out.get('cover', 4.0)))
    out['side'] = _fit_list(out.get('side'), ns, lambda: copy.deepcopy(base['side'][0]))
    for sd in out['side'] + [out['side_single']]:
        sd.setdefault('sp', 0.0)
    st = out['stirrups']
    st.setdefault('mode', 'zones')
    st['groups'] = _fit_list(st.get('groups'), ns, stir_group_default)
    st['spans'] = _fit_list(st.get('spans'), ns, stir_span_default)
    if out.get('axis_mode') not in AXIS_MODES:
        out['axis_mode'] = 'bend'
    model.set_bend_mode(out['axis_mode'])
    # zonas de 'Barra unica' (una por grupo de tramos con la misma seccion; la zona 1 es top/bot_single;
    # None = igual que la zona 1)
    nz = len(model.segments())
    for key in ('top_zones', 'bot_zones'):
        lst = list(out.get(key) or [])[:max(nz - 1, 0)]
        out[key] = lst + [None] * (max(nz - 1, 0) - len(lst))
    for sp in st['spans']:
        if sp.get('double') not in STIR_KINDS:
            sp['double'] = 'none'
    # refuerzo superior CENTRO: antes L1/L2 desde los panos, ahora desde el eje del apoyo
    if cfg and cfg.get('mid_ref') != 'axis':
        for j, ni in enumerate(model.inner_supports):
            if j < len(out['top_add']['mid']):
                w = model.nodes[ni].width / 2.0
                for ly in out['top_add']['mid'][j]:
                    ly['L1'] = float(ly.get('L1', 0.0)) + w
                    ly['L2'] = float(ly.get('L2', 0.0)) + w
    out['mid_ref'] = 'axis'
    for key in ('top_add', 'bot_add'):
        ta = out[key]
        ta.setdefault('start', [])
        ta.setdefault('end', [])
        ta['mid'] = _fit_list(ta.get('mid'), nm, lambda: [])
        ta['span'] = _fit_list(ta.get('span'), ns, lambda: [])
        if len(ta['span']) and all(x is ta['span'][0] for x in ta['span'][1:]):
            ta['span'] = [list(x) for x in ta['span']]
    return out


def remap_bar_names(cfg, names_in_cfg_diams, bts_list):
    """Sustituye nombres de tipo inexistentes por el tipo de diametro mas
    cercano. names_in_cfg_diams: {nombre: dn cm} guardado en el archivo."""
    have = set(bt['name'] for bt in bts_list)
    changes = {}

    def fix(name):
        if not name or name in have:
            return name
        if name in changes:
            return changes[name]
        d = names_in_cfg_diams.get(name)
        new = closest_bt(bts_list, d) if d else (bts_list[0]['name'] if bts_list else name)
        changes[name] = new
        return new

    def walk(o):
        if isinstance(o, dict):
            for k in list(o.keys()):
                if k in ('bt', 'stir_bt') and isinstance(o[k], (str, type(u''))):
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
                if k in ('bt', 'stir_bt') and v:
                    names.add(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(cfg)
    return names


# ------------------------------------------------------------ archivos
def config_dir():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'ArmadoViga', 'configuraciones')
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
    txt = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(u'%s' % txt)


def load_cfg(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        data = json.loads(f.read())
    diams = data.pop('bar_diams', {}) or {}
    return data, diams


def list_saved(folder):
    out = []
    try:
        for fn in sorted(os.listdir(folder)):
            if fn.lower().endswith('.json'):
                out.append(fn[:-5])
    except Exception:
        pass
    return out


# ================================================================ armado
def _bar(kind, bt, row, s0, s1, leg0, leg1, span=None, label=u''):
    return {'kind': kind, 'bt': bt, 'xs': list(row['xs']), 'y': row['y'], 's0': float(s0),
            's1': float(s1), 'leg0': float(leg0), 'leg1': float(leg1), 'span': span, 'label': label}


def compute(model, cfg, bts):
    """Devuelve dict con 'bars' (varillas longitudinales), 'stirrups' (conjuntos de estribos, cada uno
    con su poligono 'poly', tipo 'bt' y diametro 'ds'), 'stir_polys' (poligonos por tramo) y
    'warnings'. bts: {nombre: {'name','dm','dn',...}}.
    Cada tramo usa su seccion; en los apoyos donde cambia la seccion (juntas) las varillas corridas
    se cortan: siguen rectas 'joint L' dentro del otro tramo si caben en su seccion, si no terminan en
    el pano lejano del apoyo con gancho a 90 grados."""
    cover = float(cfg['cover'])
    tmin = float(cfg['tmin'])
    model.set_bend_mode(cfg.get('axis_mode', 'bend'))
    nodes = model.nodes
    ns = model.n_spans
    bars = []
    warns = []
    stc = cfg['stirrups']
    spans_st = stc.get('spans') or []
    sbt = bts.get(cfg.get('stir_bt'))

    def st_bt(i):
        nm = (spans_st[i].get('bt') if i < len(spans_st) else u'') or cfg.get('stir_bt')
        return bts.get(nm) or sbt

    ds_span = [(st_bt(i) or {}).get('dm', 0.0) for i in range(ns)]
    segs = model.segments()
    ds_bar = [0.0] * ns             # diametro de estribo para colocar varillas: el mayor del grupo
    for a, b in segs:
        d = max(ds_span[a:b + 1] or [0.0])
        for i in range(a, b + 1):
            ds_bar[i] = d
    jL = float((cfg.get('joint') or {}).get('L', 100.0))

    def joint_end(ni, side, row, bt, top, leg_user=0.0, Lj=None):
        """Extremo en el apoyo (o cambio) ni con cambio de seccion. side 'start': la varilla empieza aqui
        (es del tramo ni); 'end': termina aqui (tramo ni-1). Devuelve (s, pata con signo,
        'recta'|'gancho'). Lj: largo recto dentro del otro tramo (None = el general)."""
        nd = nodes[ni]
        other = ni - 1 if side == 'start' else ni
        L = float(Lj) if (Lj is not None and float(Lj) > 0.0) else jL
        if model.is_anchor_bend(ni):
            # cambio de eje (contratrabe desplazada o girada): recta hasta el pano lejano del apoyo si
            # cabe en la otra seccion y alcanza el anclaje; si no, gancho a 90 grados
            s = nd.s0 + cover if side == 'start' else nd.s1 - cover
            if nd.width - cover >= L - 0.01 and row_fits(row, bt['dm'], model.sec(other), cover, ds_bar[other]):
                return s, 0.0, 'recta'
            leg = leg_user if leg_user > 0.01 else round_up(12.0 * bt['dn'], 5.0)
            return s, (-leg if top else leg), 'gancho'
        if row_inside(row, bt['dm'], model.sec(other), cover, ds_bar[other]):
            return (nd.s0 - L if side == 'start' else nd.s1 + L), 0.0, 'recta'
        leg = leg_user if leg_user > 0.01 else round_up(12.0 * bt['dn'], 5.0)
        s = nd.s0 + cover if side == 'start' else nd.s1 - cover
        return s, (-leg if top else leg), 'gancho'

    def same_row(r1, r2, tol=0.5):
        return r1 is not None and r2 is not None and len(r1['xs']) == len(r2['xs']) and \
            abs(r1['y'] - r2['y']) <= tol and all(abs(a - b) <= tol for a, b in zip(sorted(r1['xs']), sorted(r2['xs'])))

    def main_bars(kind, style, single, spans, top, zones_key):
        rows = [None] * ns
        sg = -1.0 if top else 1.0
        tag = u'sup.' if top else u'inf.'
        if style == 'single':
            # una configuracion por zona (grupo de tramos con la misma seccion); la varilla sigue corrida
            # por el cambio si la zona siguiente tiene la misma varilla y la fila no cambia de lugar
            zc = [single] + list(cfg.get(zones_key) or [])
            items = []
            for z, (a, b) in enumerate(segs):
                c = (zc[z] if z < len(zc) else None) or single
                bt = bts.get(c['bt'])
                if bt is None or int(c['n']) <= 0:
                    items.append(None)
                    continue
                items.append((a, b, c, bt, main_row(model.sec(a), cover, ds_bar[a], bt, c['n'], top)))
            runs = []
            for it in items:
                if it is not None and runs and runs[-1][-1] is not None and \
                        runs[-1][-1][3]['name'] == it[3]['name'] and int(runs[-1][-1][2]['n']) == int(it[2]['n']) \
                        and runs[-1][-1][1] + 1 == it[0] and same_row(runs[-1][-1][4], it[4]) and \
                        not model.is_anchor_bend(it[0]):
                    runs[-1].append(it)
                else:
                    runs.append([it])
            for run in runs:
                if run[0] is None:
                    continue
                a, _, c0, bt, row = run[0]
                b, c1 = run[-1][1], run[-1][2]
                if a == 0:
                    s0, l0 = model.anchor_start(nodes[0], cover) - c0['Lna'], sg * c0['La']
                else:
                    s0, l0, _ = joint_end(a, 'start', row, bt, top, c0['La'] if len(segs) > 1 else 0.0,
                                          c0.get('Lj0'))
                if b == ns - 1:
                    s1, l1 = model.anchor_end(nodes[-1], cover) + c1['Lnb'], sg * c1['Lb']
                else:
                    s1, l1, _ = joint_end(b + 1, 'end', row, bt, top, c1['Lb'] if len(segs) > 1 else 0.0,
                                          c1.get('Lj1'))
                if len(runs) == 1 and a == 0 and b == ns - 1:
                    label = u'%d %s corrido %s' % (c0['n'], bt['name'], tag)
                else:
                    label = u'%d %s corrido %s T%d-T%d' % (c0['n'], bt['name'], tag, a + 1, b + 1)
                bars.append(_bar(kind, bt, row, s0, s1, l0, l1, a, label))
                for i in range(a, b + 1):
                    rows[i] = row
            return rows
        for i in range(ns):
            c = spans[i]
            bt = bts.get(c['bt'])
            if bt is None or int(c['n']) <= 0:
                continue
            row = main_row(model.sec(i), cover, ds_bar[i], bt, c['n'], top)
            if i > 0 and model.is_joint(i):
                s0, l0, _ = joint_end(i, 'start', row, bt, top, c['La'], c.get('Lj0'))
            else:
                s0, l0 = model.anchor_start(nodes[i], cover) - c['Lna'], sg * c['La']
            if i < ns - 1 and model.is_joint(i + 1):
                s1, l1, _ = joint_end(i + 1, 'end', row, bt, top, c['Lb'], c.get('Lj1'))
            else:
                s1, l1 = model.anchor_end(nodes[i + 1], cover) + c['Lnb'], sg * c['Lb']
            bars.append(_bar(kind, bt, row, s0, s1, l0, l1, i, u'%d %s %s T%d' % (c['n'], bt['name'], tag, i + 1)))
            rows[i] = row
        return rows

    def clip_at_joints(b):
        home = b.get('span')
        if home is None or b['kind'] == 'side':
            return
        row = {'xs': b['xs'], 'y': b['y']}
        top = b['kind'].startswith('top')
        leg = (-1.0 if top else 1.0) * round_up(12.0 * b['bt']['dn'], 5.0)
        db = b['bt']['dm']

        def cut_leg(nd, ni, other):
            # en un cambio de eje la varilla queda recta dentro del apoyo si cabe del otro lado y alcanza
            # el anclaje
            if model.is_anchor_bend(ni) and nd.width - cover >= jL - 0.01 and \
                    row_fits(row, db, model.sec(other), cover, ds_bar[other]):
                return 0.0
            return leg

        def why(ni):
            return u'cambio de eje' if model.is_anchor_bend(ni) else u'cambio de sección'

        for ni in range(home + 1, len(nodes) - 1):              # hacia adelante
            nd = nodes[ni]
            if b['s1'] <= nd.s1 + 0.5:
                break
            if model.is_joint(ni) and (model.is_anchor_bend(ni) or
                                       not row_inside(row, db, model.sec(ni), cover, ds_bar[ni])):
                b['s1'] = nd.s1 - cover
                b['leg1'] = cut_leg(nd, ni, ni)
                warns.append(u'%s: se corta en el %s %s' % (b['label'], why(ni), nd.name))
                break
        for ni in range(home, 0, -1):                           # hacia atras
            nd = nodes[ni]
            if b['s0'] >= nd.s0 - 0.5:
                break
            if model.is_joint(ni) and (model.is_anchor_bend(ni) or
                                       not row_inside(row, db, model.sec(ni - 1), cover, ds_bar[ni - 1])):
                b['s0'] = nd.s0 + cover
                b['leg0'] = cut_leg(nd, ni, ni - 1)
                warns.append(u'%s: se corta en el %s %s' % (b['label'], why(ni), nd.name))
                break

    # ---------------- corridos
    top_rows = main_bars('top_main', cfg['top_style'], cfg['top_single'], cfg['top_spans'], True, 'top_zones')
    bot_rows = main_bars('bot_main', cfg.get('bot_style'), cfg.get('bot_single'), cfg['bot_spans'], False,
                         'bot_zones')

    def ref_row(rows, i, top):
        r = rows[i] if 0 <= i < ns else None
        if r is None:
            j = max(0, min(i, ns - 1))
            return {'xs': [], 'y': y_row1(model.sec(j), cover, ds_bar[j], 0.0, top), 'db': 0.0}
        return r

    # ---------------- refuerzo adicional (superior e inferior, misma estructura)
    def add_bars(kind, top, rows_main, add):
        tag = u'sup.' if top else u'inf.'
        wtag = u'Ref. sup.' if top else u'Ref. inf.'
        sg = -1.0 if top else 1.0
        # INICIO / FINAL: en los extremos de la viga, anclados en el apoyo extremo
        for where, layers in (('start', add.get('start') or []), ('end', add.get('end') or [])):
            if not layers:
                continue
            si = 0 if where == 'start' else ns - 1
            rows, w = layer_rows(model.sec(si), cover, ds_bar[si], tmin, ref_row(rows_main, si, top), layers, bts, top)
            warns.extend(u'%s %s: %s' % (wtag, u'inicio' if where == 'start' else u'final', x) for x in w)
            for k, (ly, row) in enumerate(zip(layers, rows)):
                if row is None:
                    continue
                bt = bts[ly['bt']]
                if where == 'start':
                    nd = nodes[0]
                    s0 = model.anchor_start(nd, cover) - ly['Ln']
                    s1 = nd.s1 + ly['L']
                    bars.append(_bar(kind, bt, row, s0, s1, sg * ly['La'], 0.0, 0,
                                     u'%d %s %s inicio c%d' % (ly['n'], bt['name'], tag, k + 1)))
                else:
                    nd = nodes[-1]
                    s0 = nd.s0 - ly['L']
                    s1 = model.anchor_end(nd, cover) + ly['Ln']
                    bars.append(_bar(kind, bt, row, s0, s1, 0.0, sg * ly['La'], ns - 1,
                                     u'%d %s %s final c%d' % (ly['n'], bt['name'], tag, k + 1)))
        def passes(row, db, other):
            """La capa sigue recta dentro de la seccion 'other': cabe y no queda en el cuarto de la cara
            opuesta (donde van las varillas de la otra cara)."""
            sc = model.sec(other)
            if not row_fits(row, db, sc, cover, ds_bar[other]):
                return False
            opp = sc['yc'] - sc['h'] / 2.0 if top else sc['yc'] + sc['h'] / 2.0
            return abs(row['y'] - opp) >= sc['h'] / 4.0

        # CENTRO sobre apoyos intermedios: L1 / L2 desde el eje del apoyo
        mids = add.get('mid') or []
        for j, ni in enumerate(model.inner_supports):
            layers = mids[j] if j < len(mids) else []
            if not layers:
                continue
            nd = nodes[ni]
            rows_l, w_l = layer_rows(model.sec(ni - 1), cover, ds_bar[ni - 1], tmin, ref_row(rows_main, ni - 1, top),
                                     layers, bts, top)
            joint = model.is_joint(ni)
            rows_r, w_r = [None] * len(layers), []
            if joint:
                rows_r, w_r = layer_rows(model.sec(ni), cover, ds_bar[ni], tmin, ref_row(rows_main, ni, top),
                                         layers, bts, top)
            used_l = not joint
            used_r = False
            for k, ly in enumerate(layers):
                bt = bts.get(ly['bt'])
                if bt is None:
                    continue
                if nd.width > 0 and min(ly['L1'], ly['L2']) < nd.width / 2.0 + 1.0:
                    warns.append(u'%s apoyo %s c%d: L1/L2 se miden desde el eje; no sale del apoyo'
                                 % (wtag, nd.name, k + 1))
                lbl = u'%d %s %s apoyo %s c%d' % (ly['n'], bt['name'], tag, nd.name, k + 1)
                if not joint:
                    if rows_l[k] is not None:
                        bars.append(_bar(kind, bt, rows_l[k], nd.mid - ly['L1'], nd.mid + ly['L2'], 0.0, 0.0,
                                         ni - 1, lbl))
                    continue
                # cambio de seccion: si la capa de un lado cabe tambien en la seccion del otro, es UNA varilla
                # de L1 + L2 en ese lugar (p. ej. el fondo de la viga somera que entra a la zona profunda);
                # si no (o si del otro lado quedaria junto a la cara opuesta, como la superior de una viga
                # que sube de nivel), una varilla por lado anclada en la junta
                rl, rr = rows_l[k], rows_r[k]
                if not model.is_anchor_bend(ni):
                    one = None
                    if rl is not None and passes(rl, bt['dm'], ni):
                        one = (rl, ni - 1)
                        used_l = True
                    elif rr is not None and passes(rr, bt['dm'], ni - 1):
                        one = (rr, ni)
                        used_r = True
                    if one is not None:
                        bars.append(_bar(kind, bt, one[0], nd.mid - ly['L1'], nd.mid + ly['L2'], 0.0, 0.0,
                                         one[1], lbl))
                        continue
                if rl is not None:
                    s1, l1, _ = joint_end(ni, 'end', rl, bt, top)
                    if s1 - (nd.mid - ly['L1']) >= 5.0:
                        used_l = True
                        bars.append(_bar(kind, bt, rl, nd.mid - ly['L1'], s1, 0.0, l1, ni - 1, lbl + u' izq.'))
                if rr is not None:
                    s0, l0, _ = joint_end(ni, 'start', rr, bt, top)
                    if nd.mid + ly['L2'] - s0 >= 5.0:
                        used_r = True
                        bars.append(_bar(kind, bt, rr, s0, nd.mid + ly['L2'], l0, 0.0, ni, lbl + u' der.'))
            if used_l:
                warns.extend(u'%s apoyo %s: %s' % (wtag, nd.name, x) for x in w_l)
            if used_r:
                warns.extend(u'%s apoyo %s: %s' % (wtag, nd.name, x) for x in w_r)
        # CENTRO de tramo: L1 / L2 desde el centro del claro
        spans_l = add.get('span') or []
        for i in range(ns):
            layers = spans_l[i] if i < len(spans_l) else []
            if not layers:
                continue
            fa, fb = model.span_faces(i)
            c = (fa + fb) / 2.0
            lo = model.anchor_start(nodes[i], cover) if not nodes[i].free else fa + cover
            hi = model.anchor_end(nodes[i + 1], cover) if not nodes[i + 1].free else fb - cover
            if model.is_joint(i):
                lo = nodes[i].s1 + cover            # no cruza un cambio de seccion
            if model.is_joint(i + 1):
                hi = nodes[i + 1].s0 - cover
            rows, w = layer_rows(model.sec(i), cover, ds_bar[i], tmin, ref_row(rows_main, i, top), layers, bts, top)
            warns.extend(u'%s centro T%d: %s' % (wtag, i + 1, x) for x in w)
            for k, (ly, row) in enumerate(zip(layers, rows)):
                if row is None:
                    continue
                bt = bts[ly['bt']]
                s0, s1 = c - ly['L1'], c + ly['L2']
                if s0 < lo - 0.01 or s1 > hi + 0.01:
                    warns.append(u'%s centro T%d c%d: se recorta al tramo' % (wtag, i + 1, k + 1))
                    s0, s1 = max(s0, lo), min(s1, hi)
                if s1 - s0 < 5.0:
                    warns.append(u'%s centro T%d c%d: longitud nula' % (wtag, i + 1, k + 1))
                    continue
                bars.append(_bar(kind, bt, row, s0, s1, 0.0, 0.0, i,
                                 u'%d %s %s centro T%d c%d' % (ly['n'], bt['name'], tag, i + 1, k + 1)))

    add_bars('top_add', True, top_rows, cfg['top_add'])
    bot_add = cfg['bot_add']
    if isinstance(bot_add, list):                 # configuracion anterior sin pasar por fit_cfg
        bot_add = convert_old_bot_add(bot_add, model, cover)
    add_bars('bot_add', False, bot_rows, bot_add)

    # ---------------- barra central (piel / temperatura)
    sh = cfg.get('side_hook') or {}
    hdir = sh.get('dir') if sh.get('dir') in SIDE_HOOKS else 'none'
    hL = float(sh.get('L') or 0.0)

    def side_rows_key(i):
        """Posicion de las filas de piel de un tramo (para saber si siguen corridas por un cambio)."""
        sd = cfg['side_single']
        bt = bts.get(sd['bt'])
        if bt is None:
            return None
        yt = ref_row(top_rows, i, True)['y']
        yb = ref_row(bot_rows, i, False)['y']
        n = side_rows(sd, yt, yb)
        xr = x_range(model.sec(i), cover, ds_bar[i], bt['dm'])
        return (round(model.sec(i)['xc'], 0), round(xr, 0), n,
                tuple(round(yb + (yt - yb) * (k + 1.0) / (n + 1.0), 0) for k in range(n)))

    def side_set(sd, span, i0, i1, ends, label):
        """ends: (inicio, fin), cada uno ('fijo', s, lleva_gancho) o ('junta', ni)."""
        bt = bts.get(sd['bt'])
        if bt is None:
            return
        sc = model.sec(i0)
        yt = min(ref_row(top_rows, i, True)['y'] for i in range(i0, i1 + 1))
        yb = max(ref_row(bot_rows, i, False)['y'] for i in range(i0, i1 + 1))
        rows_n = side_rows(sd, yt, yb)
        xr = x_range(sc, cover, ds_bar[i0], bt['dm'])
        if hdir == 'in' and hL - bt['dm'] / 2.0 > xr - bt['dm'] / 2.0:
            warns.append(u'Barra central: los ganchos horizontales de los dos lados se cruzan al centro '
                         u'(L = %.0f cm > %.0f cm); acórtelos o use gancho vertical' % (hL, xr))
        for k in range(rows_n):
            row = {'xs': [sc['xc'] - xr, sc['xc'] + xr], 'y': yb + (yt - yb) * (k + 1.0) / (rows_n + 1.0)}
            ss, LL = [], []
            for side, e in (('start', ends[0]), ('end', ends[1])):
                if e[0] == 'fijo':
                    ss.append(e[1])
                    LL.append(hL if (e[2] and hdir != 'none') else 0.0)
                    continue
                nd = nodes[e[1]]
                other = e[1] - 1 if side == 'start' else e[1]
                if model.is_anchor_bend(e[1]):
                    ss.append(nd.s0 + cover if side == 'start' else nd.s1 - cover)
                    ok = nd.width - cover >= jL - 0.01 and row_fits(row, bt['dm'], model.sec(other), cover,
                                                                     ds_bar[other])
                    LL.append(0.0 if (ok or hdir == 'none') else hL)
                elif row_fits(row, bt['dm'], model.sec(other), cover, ds_bar[other]):
                    ss.append(nd.s0 - jL if side == 'start' else nd.s1 + jL)
                    LL.append(0.0)
                else:
                    ss.append(nd.s0 + cover if side == 'start' else nd.s1 - cover)
                    LL.append(hL if hdir != 'none' else 0.0)
            b = _bar('side', bt, row, ss[0], ss[1], -LL[0] if hdir == 'down' else 0.0,
                     -LL[1] if hdir == 'down' else 0.0, span, u'2 %s %s f%d' % (bt['name'], label, k + 1))
            if hdir == 'in':
                b['hin'] = (LL[0], LL[1])
            bars.append(b)

    if cfg.get('side_style') == 'single':
        sd = cfg['side_single']
        side_segs = []
        for a, b in segs:
            if side_segs and side_rows_key(side_segs[-1][0]) == side_rows_key(a) and not model.is_anchor_bend(a):
                side_segs[-1] = (side_segs[-1][0], b)
            else:
                side_segs.append((a, b))
        if sd.get('on'):
            for a, b in side_segs:
                e0 = ('fijo', model.anchor_start(nodes[0], cover) - sd['exl'], True) if a == 0 else ('junta', a)
                e1 = ('fijo', model.anchor_end(nodes[-1], cover) + sd['exr'], True) if b == ns - 1 \
                    else ('junta', b + 1)
                lbl = u'piel corrida' if len(side_segs) == 1 else u'piel corrida T%d-T%d' % (a + 1, b + 1)
                side_set(sd, None if len(side_segs) == 1 else a, a, b, (e0, e1), lbl)
    else:
        for i in range(ns):
            sd = cfg['side'][i]
            if not sd.get('on'):
                continue
            fa, fb = model.span_faces(i)
            if nodes[i].free:
                fa = model.anchor_start(nodes[i], cover)
            if nodes[i + 1].free:
                fb = model.anchor_end(nodes[i + 1], cover)
            side_set(sd, i, i, i, (('fijo', fa - sd['exl'], i == 0), ('fijo', fb + sd['exr'], i == ns - 1)),
                     u'piel T%d' % (i + 1))

    # ---------------- estribos (tipo y forma por tramo)
    st_sets = []
    polys = []
    for i in range(ns):
        bt = st_bt(i)
        if bt is None:
            polys.append([])
            continue
        ps = stirrup_polys(model.sec(i), cover, bt['dm'], spans_st[i].get('double', 'none') if i < len(spans_st)
                           else 'none', top_rows[i] or bot_rows[i], warns, i)
        polys.append(ps)
        if not stc.get('on'):
            continue
        fa, fb = model.span_faces(i)
        if nodes[i].free:
            fa = model.anchor_start(nodes[i], cover)
        if nodes[i + 1].free:
            fb = model.anchor_end(nodes[i + 1], cover)
        if stc.get('mode') == 'groups':
            g = (stc.get('groups') or [])[i] if i < len(stc.get('groups') or []) else stir_group_default()
            left = parse_groups(g.get('left'))
            right = parse_groups(g.get('right'), left)
            if left is None or right is None:
                warns.append(u'Estribos T%d: grupos no válidos (use n@sep en cm, p. ej. 10@10,5@15)' % (i + 1))
                continue
            sets = group_sets(fa, fb, left, right, float(g.get('mid') or 0.0), stc['first'])
        else:
            sets = stirrup_sets([fa, fb], stc['lc'], stc['sc'], stc['sm'], stc['first'])
        for z0, n, sp, zone in sets:
            for k, poly in enumerate(ps):
                # el segundo estribo del doble va un diametro a un lado (no se enciman)
                st_sets.append({'s0': z0 + k * bt['dm'], 'n': int(n), 'sp': sp, 'zone': zone, 'span': i,
                                'poly': poly, 'bt': bt['name'], 'ds': bt['dm'], 'dn': bt['dn'], 'est': k + 1})

    # apoyos extremos: la varilla llega al pano lejano del elemento del apoyo que esta a su altura (el dado,
    # la pila de abajo...); si ninguno llega a su altura, termina en el extremo de la viga
    n0, n1 = nodes[0], nodes[-1]

    def covered(qs, ya, yb):
        cur = ya
        for q in sorted(qs, key=lambda q: q[2]):
            if q[2] > cur + 0.5:
                break
            cur = max(cur, q[3])
        return cur >= yb - 0.5

    def reach(nd, y, leg, end):
        """Hasta donde hay concreto del apoyo a la altura de la varilla (con su pata): el pano mas lejano
        de los elementos (dado, pila...) que juntos cubren esa altura."""
        ya, yb = min(y, y + leg) - cover, max(y, y + leg) + cover
        qs = nd.zparts or []
        faces = sorted(set(q[1] if end else q[0] for q in qs), reverse=end)
        for f in faces:
            sel = [q for q in qs if (q[0] <= f + 0.5 and q[1] >= f - 0.5)]
            if covered(sel, ya, yb):
                return f
        return model.L if end else 0.0

    short = set()
    for b in bars:
        y = b['y']
        if n0.zparts and b['s0'] < cover - 0.01:
            lim = min(reach(n0, y, b['leg0'], False), 0.0) + cover
            if b['s0'] < lim - 0.01:
                b['s0'] = lim
                short.add(n0.name)
        if n1.zparts and b['s1'] > model.L - cover + 0.01:
            lim = max(reach(n1, y, b['leg1'], True), model.L) - cover
            if b['s1'] > lim + 0.01:
                b['s1'] = lim
                short.add(n1.name)
    for nm in sorted(short):
        warns.append(u'Apoyo %s: no todo el apoyo llega a la altura de algunas varillas; esas terminan donde '
                     u'termina el concreto a su altura' % nm)

    # ninguna varilla pasa recta un cambio de seccion (ancho, peralte o nivel) si su fila no cabe del otro
    # lado: se corta en la junta con gancho a 90 grados, como los corridos (bastones largos, etc.)
    if model.joints:
        for b in bars:
            clip_at_joints(b)

    for b in bars:
        if b['s1'] - b['s0'] < 5.0:
            warns.append(u'%s: longitud nula o negativa' % b['label'])
    bars = split_laps([b for b in bars if b['s1'] - b['s0'] >= 5.0], cfg.get('lap') or {}, model)
    regions = [] if model.anchor_bends else [(nodes[ni].s0, nodes[ni].s1, nodes[ni].name)
                                             for ni in sorted(model.bend_nodes)]
    regions += [(pos, pos, name) for pos, name in getattr(model, 'kinks', [])]
    if regions:
        bars = split_at_bends(bars, model, cfg.get('lap') or {}, cover, warns, sorted(regions))
    first = next((ps[0] for ps in polys if ps), None)
    return {'bars': bars, 'stirrups': st_sets, 'stir_polys': polys, 'stir_poly': first,
            'stir_ds': ds_span, 'ds': ds_bar[0] if ds_bar else 0.0, 'bar_ds': ds_bar,
            'stir_dn': sbt['dn'] if sbt else 0.0,
            'warnings': warns, 'top_rows': top_rows, 'bot_rows': bot_rows}


def _rect(x0, x1, y0, y1, start_right=False):
    """Estribo rectangular (x0 < x1). Empieza en la esquina superior del lado x0 (o x1)."""
    pts = [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]
    return pts[1:] + pts[:1] if start_right else pts


def stirrup_polys(sc, cover, ds, kind, row, warns=None, span=0):
    """Ejes de los estribos de un tramo: sencillo, perimetral + interior o dos traslapados.
    El estribo interior abraza las varillas del lecho (fila 'row') desde un lado; en la vista de la
    ventana el lado -x queda a la derecha, como en los detalles tipicos."""
    xc, yc = sc['xc'], sc['yc']
    sx = sc['b'] / 2.0 - cover - ds / 2.0
    sy = sc['h'] / 2.0 - cover - ds / 2.0
    y0, y1 = yc - sy, yc + sy
    per = _rect(xc - sx, xc + sx, y0, y1)
    if kind not in ('inner', 'overlap'):
        return [per]
    xs = sorted(row['xs']) if row else []
    db = row['db'] if row else 0.0
    gap = db / 2.0 + ds / 2.0
    if len(xs) < 3:
        if warns is not None:
            warns.append(u'Estribos T%d: el estribo doble necesita al menos 3 varillas por lecho; '
                         u'la rama interior va al centro' % (span + 1))
        if kind == 'inner':
            return [per, _rect(xc - sx, xc + ds, y0, y1)]
        return [_rect(xc - ds, xc + sx, y0, y1, True), _rect(xc - sx, xc + ds, y0, y1)]
    n = len(xs)
    if kind == 'inner':
        # perimetral + interior: el interior va del lado -x hasta la penultima varilla
        return [per, _rect(xc - sx, xs[n - 2] + gap, y0, y1)]
    a, b = (n - 1) // 2, n // 2
    return [_rect(xs[a] - gap, xc + sx, y0, y1, True), _rect(xc - sx, xs[b] + gap, y0, y1)]


def side_rows(sd, yt, yb):
    """Filas de barra central: por separacion maxima (sp > 0) o por numero."""
    sp = float(sd.get('sp') or 0.0)
    if sp > 0 and yt - yb > sp:
        return max(1, min(60, int(math.ceil((yt - yb) / sp - 1e-9)) - 1))
    return max(1, int(sd.get('rows') or 1))


def support_cuts(b, model, lap, stock):
    """Inicios de traslape (relativos a s0) para que los traslapes queden centrados en los apoyos
    intermedios, con piezas de hasta 'stock'. Lo que no alcance a un apoyo lo corta despues la
    longitud comercial."""
    if stock is None:
        return []
    legs0 = abs(b['leg0']) + (b.get('hin') or (0.0, 0.0))[0]
    legs1 = abs(b['leg1']) + (b.get('hin') or (0.0, 0.0))[1]
    if b['s1'] - b['s0'] + legs0 + legs1 <= stock:
        return []
    centers = sorted(n.mid for n in (model.nodes[i] for i in model.inner_supports)
                     if n.kind != u'cambio' and b['s0'] + lap < n.mid < b['s1'] - lap)
    cuts = []
    start = b['s0']
    extra = legs0
    while b['s1'] - start + legs1 + extra > stock:
        ok = [c for c in centers if c + lap / 2.0 - start + extra <= stock and c - lap / 2.0 > start + lap]
        if not ok:
            break
        c = max(ok)
        cuts.append(c - lap / 2.0 - b['s0'])
        start = c - lap / 2.0
        extra = 0.0
        centers = [x for x in centers if x > c]
    return cuts


def split_laps(bars, lc, model):
    """Parte las varillas por longitud comercial y por las posiciones del usuario (m desde el inicio
    de la viga; superior e inferior por separado). Las inferiores y la barra central, si no hay
    posiciones del usuario, traslapan centradas en los apoyos intermedios. La pieza de la izquierda
    lleva la bayoneta."""
    k = float(lc.get('k', 40.0))
    stock = float(lc.get('stock', 1200.0)) if lc.get('stock_on', True) else None
    pos = {'top': [v * 100.0 for v in (LP.parse_positions(lc.get('pos_top', u'')) or [])],
           'bot': [v * 100.0 for v in (LP.parse_positions(lc.get('pos_bot', u'')) or [])]}
    out = []
    for b in bars:
        side = b['kind'] == 'side'
        top = b['kind'].startswith('top') or (side and b['y'] > 0)
        user = pos['top' if b['kind'].startswith('top') else 'bot'] if not side else []
        lap = k * b['bt']['dn']
        hin = b.get('hin') or (0.0, 0.0)
        legs = abs(b['leg0']) + abs(b['leg1']) + hin[0] + hin[1]
        cuts = [u - b['s0'] for u in user if b['s0'] + 5.0 < u < b['s1'] - 5.0]
        if not cuts and b['kind'] in ('bot_main', 'side'):
            cuts = support_cuts(b, model, lap, stock)
        pcs = LP.plan_pieces(b['s1'] - b['s0'], cuts, lap, (stock - legs) if stock else None)
        if len(pcs) <= 1:
            out.append(b)
            continue
        # la barra central con gancho horizontal se desfasa en horizontal (hacia el centro)
        off_x = side and bool(hin[0] or hin[1])
        for i, (a, e) in enumerate(pcs):
            bb = dict(b)
            bb['s0'], bb['s1'] = b['s0'] + a, b['s0'] + e
            bb['leg0'] = b['leg0'] if i == 0 else 0.0
            bb['leg1'] = b['leg1'] if i == len(pcs) - 1 else 0.0
            if b.get('hin'):
                bb['hin'] = (hin[0] if i == 0 else 0.0, hin[1] if i == len(pcs) - 1 else 0.0)
            style = LP.lap_style(lc)
            bb['crank'] = (b['s0'] + pcs[i + 1][0], -1.0 if top else 1.0) \
                if (i < len(pcs) - 1 and style == 'crank') else None
            # desfasado: la pieza completa un diametro hacia el interior (dentro del estribo)
            bb['shift'] = (-1.0 if top else 1.0) if (style == 'offset' and i % 2) else None
            bb['off_x'] = off_x
            bb['lap_piece'] = i
            out.append(bb)
    return out


def split_at_bends(bars, model, lc, cover, warns=None, regions=None):
    """Doblar en planta: una varilla que se dobla en planta dentro de un apoyo (cambio de eje) no puede
    llevar tambien patas verticales ni bayoneta (no seria plana). Esas piezas se parten en el apoyo del
    doblez mas cercano a la pata, con traslape centrado en el apoyo (dentro del concreto del apoyo)."""
    k = float(lc.get('k', 40.0))
    out = []
    todo = list(bars)
    done = set()
    while todo:
        b = todo.pop(0)
        legs0 = abs(b['leg0']) > 0.01
        legs1 = abs(b['leg1']) > 0.01 or bool(b.get('crank'))
        if regions is None:
            regions = [(model.nodes[ni].s0, model.nodes[ni].s1, model.nodes[ni].name) for ni in sorted(model.bend_nodes)]
        skip = b.get('_bent') or ()
        cross = [r for r in regions if b['s0'] < r[0] - 0.01 and b['s1'] > r[1] + 0.01 and r not in skip]
        if not cross or not (legs0 or legs1):
            out.append(b)
            continue
        r0, r1, rname = cross[0] if legs0 else cross[-1]
        width, mid = r1 - r0, (r0 + r1) / 2.0
        lap = k * b['bt']['dn'] if width < 1.0 else min(k * b['bt']['dn'], max(width - 2.0 * cover, 0.0))
        hin = b.get('hin') or (0.0, 0.0)
        A = dict(b)
        B = dict(b)
        A['_bent'] = tuple(skip) + (cross[0] if legs0 else cross[-1],)
        B['_bent'] = A['_bent']
        A['s1'] = mid + lap / 2.0
        A['leg1'] = 0.0
        A['crank'] = None
        B['s0'] = mid - lap / 2.0
        B['leg0'] = 0.0
        if b.get('hin'):
            A['hin'] = (hin[0], 0.0)
            B['hin'] = (0.0, hin[1])
        if warns is not None and (b['label'], rname) not in done:
            done.add((b['label'], rname))
            warns.append(u'%s: traslapa en %s %s (la pieza con gancho no puede doblarse en planta)'
                         % (b['label'], u'el quiebre' if width < 1.0 else u'el apoyo', rname))
        todo[:0] = [A, B]
    return out


def parse_groups(text, default=None):
    """'10@10,5@15,5@20' -> [(10, 10.0), (5, 15.0), (5, 20.0)] (separaciones en cm).
    Vacio -> default; '0' o '-' -> [] (sin grupos); None si no se entiende."""
    t = (u'%s' % (text or u'')).strip()
    if not t:
        return list(default) if default is not None else []
    if t in (u'0', u'-'):
        return []
    out = []
    for part in re.split(r'[,;]+', t):
        part = part.strip()
        if not part:
            continue
        m = re.match(r'^(\d+)\s*@\s*(\d+(?:[.]\d+)?)$', part.replace(u' ', u''))
        if not m:
            return None
        n, sp = int(m.group(1)), float(m.group(2))
        if n <= 0 or sp <= 0:
            return None
        out.append((n, sp))
    return out


def fmt_groups(groups):
    return u','.join(u'%d@%s' % (n, fcm_(sp)) for n, sp in groups)


def fcm_(v):
    s = u'%.1f' % v
    return s[:-2] if s.endswith(u'.0') else s


def group_sets(fa, fb, left, right, mid_sp, first):
    """Estribos por grupos desde cada apoyo (como en los planos: 10@10,5@15,5@20 y @30 al centro).
    El primer estribo va a 'first' del pano; cada grupo sigue con su separacion. Los grupos de
    los dos lados no pasan de la mitad del claro; el hueco central se reparte con separacion
    <= mid_sp. Devuelve [(s_inicio, n, sep, zona)]."""
    L = fb - fa
    if L <= TOL:
        return []
    first = max(float(first), 0.0)
    half = L / 2.0

    def positions(groups):
        out = []                  # (distancia al pano, separacion, indice de grupo)
        cur = None
        for gi, (n, sp) in enumerate(groups):
            for _ in range(int(n)):
                cur = first if cur is None else cur + sp
                out.append((cur, sp, gi))
        return [p for p in out if p[0] <= half + TOL]

    lp = positions(left)
    rp = positions(right)
    while lp and rp:
        gap = L - lp[-1][0] - rp[-1][0]
        if gap >= 0.5 * min(lp[-1][1], rp[-1][1]) - TOL:
            break
        rp.pop()
    sets = []

    def to_sets(pp, from_end):
        grp = {}
        for d, sp, gi in pp:
            grp.setdefault(gi, []).append(d)
        for gi in sorted(grp):
            ds = grp[gi]
            sp = (ds[1] - ds[0]) if len(ds) > 1 else 0.0
            if from_end:
                sets.append((fb - ds[-1], len(ds), sp, 'conf'))
            else:
                sets.append((fa + ds[0], len(ds), sp, 'conf'))

    to_sets(lp, False)
    lo = fa + lp[-1][0] if lp else None
    hi = fb - rp[-1][0] if rp else None
    a = lo if lo is not None else fa + first
    b = hi if hi is not None else fb - first
    gap = b - a
    mid_sp = mid_sp if mid_sp > TOL else max([p[1] for p in lp + rp] or [20.0])
    if gap > TOL:
        nseg = max(1, int(math.ceil(gap / mid_sp - 1e-9)))
        spm = gap / nseg
        k0 = 0 if lo is None else 1
        k1 = nseg if hi is None else nseg - 1
        if k1 >= k0:
            sets.append((a + k0 * spm, k1 - k0 + 1, spm, 'mid'))
    elif lo is None and hi is None:
        sets.append((a, 1, 0.0, 'mid'))
    to_sets(rp, True)
    sets.sort(key=lambda t: t[0])
    return sets


# ------------------------------------------------------------ estribos
def stirrup_sets(bounds, lc, sc, sm, first):
    """bounds: [a, b] del tramo. Devuelve [(s_inicio, n, separacion, zona)]."""
    sets = []
    sc = max(sc, 1.0)
    sm = max(sm, 1.0)
    first = max(first, 0.0)
    za, zb = bounds[0], bounds[1]
    h = zb - za
    usable = h - 2.0 * first
    if usable <= TOL:
        if h > TOL:
            sets.append((za + h / 2.0, 1, 0.0, 'conf'))
        return sets
    nb = int(math.floor((lc - first) / sc + 1e-9)) + 1 if lc >= first else 1
    zb_last = za + first + (nb - 1) * sc
    zt_first = zb - first - (nb - 1) * sc
    if 2.0 * lc >= h - TOL or zt_first - zb_last < 0.5 * sc or sm <= sc:
        n = int(math.ceil(usable / sc - 1e-9)) + 1
        sp = usable / (n - 1.0) if n > 1 else 0.0
        sets.append((za + first, n, sp, 'conf'))
        return sets
    sets.append((za + first, nb, sc, 'conf'))
    gap = zt_first - zb_last
    if gap > sm + TOL:
        nm = int(math.ceil(gap / sm - 1e-9)) - 1
        spm = gap / (nm + 1.0)
        if nm > 0:
            sets.append((zb_last + spm, nm, spm, 'mid'))
    sets.append((zt_first, nb, sc, 'conf'))
    return sets


# ------------------------------------------------------------ resumen
def kg_per_m(d_cm):
    return math.pi * (d_cm / 100.0) ** 2 / 4.0 * STEEL_KG_M3


def bar_length(b):
    L = b['s1'] - b['s0']
    L += abs(b['leg0']) + abs(b['leg1'])
    L += sum(b.get('hin') or (0.0, 0.0))
    return L


def stirrup_length(poly):
    n = len(poly)
    return sum(math.hypot(poly[(i + 1) % n][0] - poly[i][0], poly[(i + 1) % n][1] - poly[i][1])
               for i in range(n))


def summary(res, bts, hook_a):
    """Peso aproximado (kg) por tipo de elemento. hook_a: longitud del gancho de estribo (cm), numero
    o {nombre de varilla: cm}."""
    w = {'top_main': 0.0, 'bot_main': 0.0, 'top_add': 0.0, 'bot_add': 0.0, 'side': 0.0, 'stirrups': 0.0}
    for b in res['bars']:
        w[b['kind']] += len(b['xs']) * bar_length(b) / 100.0 * kg_per_m(b['bt']['dn'])
    n_st = 0
    for st in res['stirrups']:
        poly = st.get('poly') or res.get('stir_poly')
        if not poly:
            continue
        a = hook_a.get(st.get('bt'), 0.0) if isinstance(hook_a, dict) else hook_a
        L = stirrup_length(poly) + 2 * (a or 0.0)
        w['stirrups'] += st['n'] * L / 100.0 * kg_per_m(st.get('dn') or res.get('stir_dn') or 0.0)
        n_st += st['n']
    return w, n_st


def parse_num(text, default=None):
    try:
        s = (u'%s' % text).strip().replace(',', '.')
        if not s:
            return default
        return float(s)
    except Exception:
        return default
