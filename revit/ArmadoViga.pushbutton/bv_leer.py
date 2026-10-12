# -*- coding: utf-8 -*-
"""Armado que ya tiene la viga -> configuracion de la ventana (Revit 2024 - 2026).

Al seleccionar una viga que ya tiene varillas, su armado se carga en la configuracion (corridos,
bastones, piel y estribos) para verlo en la ventana como si se hubiera capturado y, si hace falta,
corregirlo y volver a crearlo.

1. Si el armado lo creo esta herramienta y nadie lo ha cambiado, se recupera la configuracion EXACTA
   con que se creo: se guarda en la viga (Extensible Storage) al pulsar OK.
2. Si no (armado hecho a mano, editado o de una version anterior), se reconstruye de las varillas:
   - estribos: estilo Estribo/Atadura (o cortos a lo largo de la viga) -> grupos por tramo n@sep;
   - longitudinales: superior / inferior / piel por su altura. Las piezas de una misma linea se unen
     aunque esten traslapadas y desfasadas (un diametro); las lineas que van de apoyo extremo a apoyo
     extremo son corridos, las demas son bastones (INICIO / FINAL / CENTRO sobre apoyo / centro de
     tramo), agrupados por posicion y longitud.

Unidades: cm."""
from __future__ import division
import json

import System
from Autodesk.Revit.DB import BuiltInParameter
from Autodesk.Revit.DB.Structure import RebarHostData, RebarStyle, MultiplanarOption

import bv_geom as G
import bv_revit as R

FT = R.FT
JOIN_TOL = 5.0               # cm: piezas de una misma linea que se tocan o traslapan
SAME_LEN = 10.0              # cm: bastones del mismo grupo
END_TOL = 15.0               # cm: una linea "llega" al extremo si queda a esto del pano interior del apoyo

try:
    from Autodesk.Revit.DB.ExtensibleStorage import SchemaBuilder, Schema, Entity, AccessLevel
except Exception:            # pragma: no cover
    SchemaBuilder = None

SCHEMA_GUID = u'6f1d3c52-8a4e-4b1f-9c7e-2b5a7d0e4c31'
SCHEMA_NAME = u'ArmadoVigaConfig'


# ================================================================ configuracion guardada en la viga
def _schema(create):
    if SchemaBuilder is None:
        return None
    guid = System.Guid(SCHEMA_GUID)
    sc = Schema.Lookup(guid)
    if sc is not None or not create:
        return sc
    sb = SchemaBuilder(guid)
    sb.SetReadAccessLevel(AccessLevel.Public)
    sb.SetWriteAccessLevel(AccessLevel.Public)
    sb.SetSchemaName(SCHEMA_NAME)
    sb.SetDocumentation(u'Configuración con que la herramienta Armado de viga creó el armado')
    sb.AddSimpleField(u'data', System.String)
    return sb.Finish()


def store_cfg(doc, info, cfg, created):
    """Guarda en cada viga de la cadena la configuracion y las varillas creadas (dentro de la transaccion)."""
    sc = _schema(True)
    if sc is None:
        return False
    data = json.dumps({'cfg': cfg, 'rebars': sorted(R.eid_int(rb.Id) for rb in created),
                       'beams': sorted(R.eid_int(c['elem'].Id) for c in info['chain'])}, ensure_ascii=False)
    ok = False
    for c in info['chain']:
        try:
            ent = Entity(sc)
            ent.Set[System.String](u'data', data)
            c['elem'].SetEntity(ent)
            ok = True
        except Exception:
            pass
    return ok


def _stored(info):
    sc = _schema(False)
    if sc is None:
        return None
    for c in info['chain']:
        try:
            ent = c['elem'].GetEntity(sc)
            if ent is None or not ent.IsValid():
                continue
            s = ent.Get[System.String](u'data')
            if s:
                return json.loads(s)
        except Exception:
            pass
    return None


# ================================================================ lectura de las varillas
def _curves(rb, pos):
    try:
        return list(rb.GetCenterlineCurves(False, False, True, MultiplanarOption.IncludeOnlyPlanarCurves, pos))
    except Exception:
        try:
            return list(rb.GetCenterlineCurves(False, False, True))
        except Exception:
            return []


def _pts(curves):
    out = []
    for c in curves:
        try:
            q = list(c.Tessellate())
        except Exception:
            try:
                q = [c.GetEndPoint(0), c.GetEndPoint(1)]
            except Exception:
                q = []
        if out and q and out[-1].DistanceTo(q[0]) < 1e-6:
            q = q[1:]
        out.extend(q)
    return out


def _positions(rb):
    try:
        n = int(rb.NumberOfBarPositions)
    except Exception:
        n = 1
    out = []
    for i in range(max(n, 1)):
        try:
            if not rb.DoesIncludeBarAtPosition(i):
                continue
        except Exception:
            pass
        out.append(i)
    return out or [0]


def _bars(rb, poss):
    """Puntos de la linea central de cada posicion del conjunto: {indice: [XYZ]}.

    La posicion de cada barra se obtiene con su transformada respecto a la primera
    (GetBarPositionTransform): GetCenterlineCurves puede devolver la misma barra para todas las
    posiciones, y entonces la separacion de estribos y la posicion de las varillas salian en cero."""
    base = _pts(_curves(rb, 0))
    out = {}
    if len(base) < 2:
        return out
    try:
        acc = rb.GetShapeDrivenAccessor()
        inv0 = acc.GetBarPositionTransform(0).Inverse
        for i in poss:
            T = acc.GetBarPositionTransform(i).Multiply(inv0)
            out[i] = [T.OfPoint(p) for p in base]
        return out
    except Exception:
        pass
    for i in poss:                       # armadura de forma libre: curvas de cada posicion
        pts = base if i == 0 else _pts(_curves(rb, i))
        if len(pts) >= 2:
            out[i] = pts
    return out


def _kind(comment):
    """Funcion guardada en el comentario por esta herramienta (ArmadoViga|top_main, ...) o None."""
    if comment.startswith(R.TAG + u'|'):
        return comment.split(u'|', 1)[1].strip() or None
    return None


def _is_stirrup(doc, rb):
    try:
        sh = doc.GetElement(rb.GetShapeId())
        if sh is not None:
            return sh.RebarStyle == RebarStyle.StirrupTie
    except Exception:
        pass
    return None


def _comment(rb):
    try:
        p = rb.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        return (p.AsString() or u'') if p is not None else u''
    except Exception:
        return u''


class _Ctx(object):
    def __init__(self, info):
        self.info = info
        self.fr = info['frame']
        ax = info.get('axis') or {}
        try:
            self.axis = R.Axis(self.fr, ax.get('runs') or [], ax.get('bends') or [], ax.get('s0', info['s_min']))
        except Exception:
            self.axis = None
        self.z_of = dict((R.eid_int(c['elem'].Id), (c['z0'], c['z1'])) for c in info['chain'])

    def s_cm(self, p):
        s = self.fr.s(p)
        if self.axis is not None:
            try:
                return self.axis.to_arc(s) * FT
            except Exception:
                pass
        return (s - self.info['s_min']) * FT

    def l_cm(self, p):
        return (self.fr.l(p) - self.info['l_c']) * FT

    def zrange(self, host_id):
        return self.z_of.get(host_id, (self.info['z0'], self.info['z1']))


def _leg(p, q, ctx):
    """Largo de la pata si el tramo p-q es vertical (cm), si no 0."""
    dz = abs(q.Z - p.Z) * FT
    return dz if dz > 3.0 and abs(ctx.s_cm(q) - ctx.s_cm(p)) < 1.0 else 0.0


def read_existing(doc, info):
    """Varillas alojadas en la cadena: {'rebars', 'own', 'longs', 'stirrups'} o None si no hay."""
    ctx = _Ctx(info)
    seen = set()
    rebars = []
    for c in info['chain']:
        try:
            for rb in RebarHostData.GetRebarHostData(c['elem']).GetRebarsInHost():
                k = R.eid_int(rb.Id)
                if k not in seen:
                    seen.add(k)
                    rebars.append(rb)
        except Exception:
            pass
    if not rebars:
        return None
    longs, stirrups, own = [], [], set()
    for rb in rebars:
        com = _comment(rb)
        if com.startswith(R.TAG):
            own.add(R.eid_int(rb.Id))
        kind = _kind(com)
        bt = doc.GetElement(rb.GetTypeId())
        bname = R.el_name(bt) if bt is not None else u'?'
        try:
            dm, dn = R._bt_diams(bt)
        except Exception:
            dm = dn = 1.0
        try:
            host_id = R.eid_int(rb.GetHostId())
        except Exception:
            host_id = None
        z0, z1 = ctx.zrange(host_id)
        poss = _positions(rb)
        bars = _bars(rb, poss)
        if not bars:
            continue
        first = bars[min(bars)]
        ss = [ctx.s_cm(p) for p in first]
        if kind is not None:
            st = kind == 'stirrups'
        else:
            st = _is_stirrup(doc, rb)
            if st is None:
                st = max(ss) - min(ss) < 0.5 * (z1 - z0) * FT
        if st:
            cs = sorted(sum(ctx.s_cm(p) for p in pts) / len(pts) for pts in bars.values())
            try:
                n = int(rb.Quantity)
            except Exception:
                n = len(cs)
            n = max(n, 1)
            ls = [ctx.l_cm(p) for p in first]
            zs = [p.Z * FT for p in first]
            stirrups.append({'bt': bname, 'dm': dm, 'n': n, 's0': cs[0], 's1': cs[-1],
                             'sp': (cs[-1] - cs[0]) / (n - 1) if n > 1 else 0.0,
                             'b': max(ls) - min(ls) + dm, 'h': max(zs) - min(zs) + dm})
            continue
        for i in sorted(bars):
            pts = bars[i]
            ss = [ctx.s_cm(p) for p in pts]
            a, b = min(ss), max(ss)
            # posicion en la seccion: centro del tramo mas largo a lo largo de la viga (sin patas ni ganchos)
            k = max(range(len(pts) - 1), key=lambda j: abs(ss[j + 1] - ss[j]))
            pm = [pts[k], pts[k + 1]]
            zc = (pm[0].Z + pm[1].Z) / 2.0
            rel = (zc - z0) / max(z1 - z0, 1e-9)
            # patas: al principio y al final de la polilinea, del lado de menor y mayor s
            l_first, l_last = _leg(pts[0], pts[1], ctx), _leg(pts[-2], pts[-1], ctx)
            if ss[0] <= ss[-1]:
                leg0, leg1 = l_first, l_last
            else:
                leg0, leg1 = l_last, l_first
            if kind in ('top_main', 'top_add'):
                layer = 'sup'
            elif kind in ('bot_main', 'bot_add'):
                layer = 'inf'
            elif kind == 'side':
                layer = 'piel'
            else:
                layer = 'sup' if rel >= 0.75 else ('inf' if rel <= 0.25 else 'piel')
            longs.append({'bt': bname, 'dm': dm, 'dn': dn, 'layer': layer, 'kind': kind,
                          'l': (ctx.l_cm(pm[0]) + ctx.l_cm(pm[1])) / 2.0, 'z': zc * FT, 'a': a, 'b': b,
                          'leg0': leg0, 'leg1': leg1})
    return {'rebars': sorted(seen), 'own': own, 'longs': longs, 'stirrups': stirrups}


# ================================================================ lineas (piezas traslapadas unidas)
def _lines(longs):
    """Une las piezas de una misma linea: mismo lecho y tipo, posicion en la seccion a menos de 1.6
    diametros (el traslape desfasado o en bayoneta corre la pieza un diametro) y que se tocan o traslapan."""
    lines = []
    for p in sorted(longs, key=lambda q: q['a']):
        tol = max(2.0, 1.6 * p['dm'])
        best = None
        for ln in lines:
            q = ln['last']
            if ln['layer'] != p['layer'] or ln['bt'] != p['bt'] or ln['kind'] != p.get('kind') or \
                    ln['b'] >= p['b'] - 1.0:
                continue
            if abs(q['l'] - p['l']) > tol or abs(q['z'] - p['z']) > tol or p['a'] > ln['b'] + JOIN_TOL:
                continue
            d = abs(q['l'] - p['l']) + abs(q['z'] - p['z'])
            if best is None or d < best[0]:
                best = (d, ln)
        if best is not None:
            ln = best[1]
            ln['b'] = p['b']
            ln['leg1'] = p['leg1']
            ln['pieces'] += 1
            ln['items'].append(p)
            ln['last'] = p
        else:
            lines.append({'layer': p['layer'], 'bt': p['bt'], 'kind': p.get('kind'), 'dm': p['dm'], 'dn': p['dn'],
                          'a': p['a'],
                          'b': p['b'], 'leg0': p['leg0'], 'leg1': p['leg1'], 'pieces': 1, 'last': p, 'items': [p],
                          'z': p['z']})
    return lines


def _slots(pieces):
    """Numero de posiciones distintas a lo ancho (las piezas a menos de 1.6 diametros son la misma)."""
    ls = sorted(x['l'] for x in pieces)
    n = 0
    prev = None
    for x, l in zip(sorted(pieces, key=lambda q: q['l']), ls):
        if prev is None or l - prev > max(2.0, 1.6 * x['dm']):
            n += 1
        prev = l
    return n


def _median(v, default=0.0):
    v = sorted(v)
    return v[len(v) // 2] if v else default


def _r(v, step=5.0):
    return round(v / step) * step


def _most(items):
    cnt = {}
    for x in items:
        cnt[x] = cnt.get(x, 0) + 1
    return max(cnt.items(), key=lambda kv: kv[1])[0] if cnt else None


# ================================================================ configuracion
def _groups(sets, from_end):
    """Conjuntos de estribos desde un pano -> '10@10,5@15'. Un conjunto de un solo estribo toma como
    separacion la distancia al estribo anterior."""
    out = []
    prev = None
    for s in sets:
        near, far = (s['s1'], s['s0']) if from_end else (s['s0'], s['s1'])
        if s['n'] > 1:
            sp = s['sp']
        else:
            sp = abs(near - prev) if prev is not None else 10.0
        out.append(u'%d@%s' % (s['n'], G.fcm_(round(max(sp, 1.0), 1))))
        prev = far
    return u','.join(out)


def to_cfg(ex, model, bts_list):
    """Configuracion de la ventana que reproduce el armado leido. Devuelve (cfg, avisos)."""
    cfg = G.fit_cfg({}, model, bts_list)
    notes = []
    nodes = model.nodes
    ns = model.n_spans
    inner = model.inner_supports
    have = set(b['name'] for b in bts_list)
    # ---- recubrimiento (de los estribos) y estribos
    st = ex['stirrups']
    if st:
        cov = _median([(model.b - s['b']) / 2.0 for s in st] + [(model.h - s['h']) / 2.0 for s in st], 4.0)
        if 1.5 <= cov <= 10.0:
            cfg['cover'] = round(cov * 2.0) / 2.0
        sbt = _most([s['bt'] for s in st])
        if sbt in have:
            cfg['stir_bt'] = sbt
    cover = float(cfg['cover'])
    stc = cfg['stirrups']
    stc['on'] = bool(st)
    if st:
        stc['mode'] = 'groups'
        firsts = []
        for i in range(ns):
            fa, fb = model.span_faces(i)
            sets = sorted([s for s in st if fa - 1.0 <= (s['s0'] + s['s1']) / 2.0 <= fb + 1.0],
                          key=lambda s: s['s0'])
            # estribos dobles (perimetral + interior o dos traslapados): mismo arranque y cantidad
            kept, double = [], 'none'
            for s in sets:
                twin = [k for k in kept if abs(k['s0'] - s['s0']) < 3.0 and k['n'] == s['n']]
                if twin:
                    double = 'inner' if abs(twin[0]['b'] - s['b']) > 3.0 else 'overlap'
                    continue
                kept.append(s)
            sp_cfg = stc['spans'][i]
            sp_cfg['double'] = double
            tb = _most([s['bt'] for s in kept])
            sp_cfg['bt'] = tb if (tb and tb != cfg['stir_bt'] and tb in have) else u''
            g = stc['groups'][i]
            if not kept:
                g['left'], g['right'], g['mid'] = u'0', u'0', 0.0
                continue
            half = (fa + fb) / 2.0
            mid = [s for s in kept if s['n'] > 1 and s['s0'] < half < s['s1']]
            mid = max(mid, key=lambda s: s['s1'] - s['s0']) if mid else None
            left = [s for s in kept if s is not mid and (s['s0'] + s['s1']) / 2.0 < half]
            right = [s for s in kept if s is not mid and (s['s0'] + s['s1']) / 2.0 >= half]
            if left:
                firsts.append(left[0]['s0'] - fa)
            if right:
                firsts.append(fb - right[-1]['s1'])
            g['left'] = _groups(left, False) if left else u'0'
            g['right'] = _groups(list(reversed(right)), True) if right else u'0'
            g['mid'] = round(mid['sp'], 1) if mid else (round(max(s['sp'] for s in kept), 1) or 20.0)
        if firsts:
            stc['first'] = max(round(_median(firsts) * 2.0) / 2.0, 0.0)
    # ---- longitudinales
    lines = _lines(ex['longs'])
    n0, n1 = nodes[0], nodes[-1]
    lo_end = n0.s1 + END_TOL if not n0.free else END_TOL
    hi_end = n1.s0 - END_TOL if not n1.free else model.L - END_TOL
    MAIN = ('top_main', 'bot_main')
    # funcion conocida (marcada al crearla): manda sobre la geometria; si no, de extremo a extremo = corrido
    full = [ln for ln in lines if ln['kind'] in MAIN or
            (ln['kind'] is None and ln['a'] <= lo_end and ln['b'] >= hi_end)]
    part = [ln for ln in lines if ln not in full]
    for lay, key in (('sup', 'top'), ('inf', 'bot')):
        fl = [ln for ln in full if ln['layer'] == lay]
        c = cfg[key + '_single']
        cfg[key + '_style'] = 'single'
        if not fl:
            c['n'] = 0
            continue
        bt = _most([ln['bt'] for ln in fl])
        sel = [ln for ln in fl if ln['bt'] == bt]
        c['bt'] = bt if bt in have else c['bt']
        # cantidad: posiciones distintas en la seccion (las piezas de un traslape, aunque no se hayan unido
        # en una linea o esten desfasadas un diametro, son la misma varilla)
        pcs = [x for ln in sel for x in ln['items']]
        c['n'] = _slots(pcs) or len(sel)
        a0 = min(x['a'] for x in pcs) if pcs else 0.0
        b1 = max(x['b'] for x in pcs) if pcs else 0.0
        c['La'] = _r(_median([x['leg0'] for x in pcs if x['a'] <= a0 + 5.0]))
        c['Lb'] = _r(_median([x['leg1'] for x in pcs if x['b'] >= b1 - 5.0]))
        c['Lna'] = c['Lnb'] = 0.0
        others = [ln for ln in fl if ln['bt'] != bt]
        if others:
            notes.append(u'Corridos %s: también hay %s; se cargaron como %d %s'
                         % (u'superiores' if lay == 'sup' else u'inferiores',
                            u', '.join(u'%d %s' % (_slots([x for ln in others if ln['bt'] == n
                                                            for x in ln['items']]), n)
                                       for n in sorted(set(x['bt'] for x in others))), c['n'], bt))
        if len(pcs) > c['n']:
            notes.append(u'Corridos %s con traslapes: se recalculan con la longitud comercial / posiciones '
                         u'de Configuración' % (u'superiores' if lay == 'sup' else u'inferiores'))
    # ---- bastones
    for lay, key in (('sup', 'top_add'), ('inf', 'bot_add')):
        add = G.add_default(model)
        groups = {}
        for ln in [x for x in part if x['layer'] == lay and x['kind'] in (None, key)]:
            a, b = ln['a'], ln['b']
            if a <= lo_end:
                k = ('start', 0)
                d = {'L': b - n0.s1, 'La': ln['leg0'], 'Ln': max(model.anchor_start(n0, cover) - a, 0.0)}
            elif b >= hi_end:
                k = ('end', 0)
                d = {'L': n1.s0 - a, 'La': ln['leg1'], 'Ln': max(b - model.anchor_end(n1, cover), 0.0)}
            else:
                over = [j for j, ni in enumerate(inner) if a < nodes[ni].s1 - 1.0 and b > nodes[ni].s0 + 1.0]
                if over:
                    j = max(over, key=lambda j: min(b, nodes[inner[j]].s1) - max(a, nodes[inner[j]].s0))
                    m = nodes[inner[j]].mid
                    k = ('mid', j)
                    d = {'L1': m - a, 'L2': b - m}
                else:
                    c = (a + b) / 2.0
                    i = max([i for i in range(ns) if model.span_faces(i)[0] <= c] or [0])
                    fa, fb = model.span_faces(i)
                    m = (fa + fb) / 2.0
                    k = ('span', i)
                    d = {'L1': m - a, 'L2': b - m}
            d = dict((kk, _r(v)) for kk, v in d.items())
            gk = k + (ln['bt'],) + tuple(sorted(d.items()))
            g = groups.setdefault(gk, dict(d, bt=ln['bt'], n=0))
            g['n'] += 1
        for gk, g in sorted(groups.items(), key=lambda kv: -kv[1]['n']):
            where, j = gk[0], gk[1]
            if g['bt'] not in have:
                continue
            ly = dict(g)
            if where in ('start', 'end'):
                add[where].append(ly)
            else:
                add[where][j].append(ly)
        cfg[key] = add
    # ---- piel
    side = [ln for ln in lines if ln['layer'] == 'piel']
    sd = cfg['side_single']
    cfg['side_style'] = 'single'
    sd['on'] = bool(side)
    if side:
        bt = _most([ln['bt'] for ln in side])
        sd['bt'] = bt if bt in have else sd['bt']
        zs = []
        for z in sorted(ln['z'] for ln in side):
            if not zs or z - zs[-1] > 3.0:
                zs.append(z)
        sd['rows'] = max(1, min(len(zs), G.SIDE_ROWS_MAX))
        if any(ln not in full for ln in side):
            notes.append(u'Piel: no toda corre de extremo a extremo; se cargó como piel corrida')
    return cfg, notes


# ================================================================ entrada
def existing_cfg(doc, info, model, bts_list):
    """(cfg, texto) del armado que ya tiene la viga, o (None, texto) si no tiene."""
    ex = read_existing(doc, info)
    if not ex:
        return None, u''
    nrb = len(ex['rebars'])
    data = _stored(info)
    if data and set(data.get('rebars') or []) == set(ex['rebars']) == ex['own'] and \
            set(data.get('beams') or []) == set(R.eid_int(c['elem'].Id) for c in info['chain']):
        try:
            cfg = G.fit_cfg(data['cfg'], model, bts_list)
            return cfg, (u'Armado existente (%d elementos de armadura): se cargó en la configuración tal como '
                         u'se creó con esta herramienta.' % nrb)
        except Exception:
            pass
    cfg, notes = to_cfg(ex, model, bts_list)
    why = u'hecho o editado fuera de esta herramienta' if ex['own'] != set(ex['rebars']) or data \
        else u'creado con una versión anterior'
    txt = (u'Armado existente (%d elementos de armadura, %s): se reconstruyó de las varillas y se cargó en '
           u'la configuración; revíselo antes de pulsar OK.' % (nrb, why))
    for n in notes:
        txt += u'\nAviso: ' + n
    return cfg, txt
