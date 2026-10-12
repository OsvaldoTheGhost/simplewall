# -*- coding: utf-8 -*-
"""Lectura de vigas/apoyos y creacion de Rebar y vistas (Revit 2024 - 2026)."""
from __future__ import division
import math
import traceback

import clr
from System.Collections.Generic import List

from Autodesk.Revit.DB import (XYZ, Line, Curve, Options, ViewDetailLevel, Solid, GeometryInstance,
                               FilteredElementCollector, BuiltInParameter, BuiltInCategory,
                               Transaction, Element, ElementId, IFailuresPreprocessor,
                               FailureSeverity, FailureProcessingResult, BoundingBoxIntersectsFilter,
                               Outline, ElementMulticategoryFilter, Grid, ViewFamilyType, ViewFamily,
                               ViewSection, View, ViewType, BoundingBoxXYZ, Transform)
from Autodesk.Revit.DB.Structure import (Rebar, RebarBarType, RebarHookType, RebarStyle,
                                         RebarHookOrientation, RebarHostData, MultiplanarOption)

import bv_geom as G
import armado_rebar as AR

FT = 30.48
TAG = u'ArmadoViga'
RIGHT = RebarHookOrientation.Right
LEFT = RebarHookOrientation.Left


# ================================================================ util
def eid_int(eid):
    try:
        return int(eid.Value)
    except Exception:
        return int(eid.IntegerValue)


def el_name(el):
    try:
        return Element.Name.GetValue(el)
    except Exception:
        try:
            return el.Name
        except Exception:
            return u'?'


def _cat_is(el, bic):
    cat = el.Category if el is not None else None
    if cat is None:
        return False
    try:
        return cat.BuiltInCategory == bic
    except Exception:
        return eid_int(cat.Id) == int(bic)


def is_beam(el):
    return _cat_is(el, BuiltInCategory.OST_StructuralFraming) and \
        isinstance(getattr(getattr(el, 'Location', None), 'Curve', None), Line)


def _bt_diams(bt):
    dm = dn = None
    try:
        dm = bt.BarModelDiameter
    except Exception:
        pass
    try:
        dn = bt.BarNominalDiameter
    except Exception:
        pass
    if dm is None or dn is None:
        try:
            d = bt.BarDiameter
            dm = d if dm is None else dm
            dn = d if dn is None else dn
        except Exception:
            pass
    dm = dm or dn or (1.0 / FT)
    dn = dn or dm
    return dm * FT, dn * FT


def get_bar_types(doc):
    out = []
    for bt in FilteredElementCollector(doc).OfClass(RebarBarType):
        dm, dn = _bt_diams(bt)
        out.append({'name': el_name(bt), 'dm': dm, 'dn': dn, 'elem': bt})
    out.sort(key=lambda d: (d['dn'], d['name']))
    seen = set()
    uniq = []
    for d in out:                      # nombres unicos (se guardan por nombre)
        if d['name'] in seen:
            continue
        seen.add(d['name'])
        uniq.append(d)
    return uniq


def get_stirrup_hooks(doc):
    out = []
    for hk in FilteredElementCollector(doc).OfClass(RebarHookType):
        try:
            if hk.Style != RebarStyle.StirrupTie:
                continue
        except Exception:
            continue
        try:
            ang = math.degrees(hk.HookAngle)
        except Exception:
            ang = 0.0
        out.append({'name': el_name(hk), 'angle': round(ang, 1), 'elem': hk})
    out.sort(key=lambda d: (0 if abs(d['angle'] - 135.0) < 1.0 else 1, d['angle'], d['name']))
    return out


def hook_length_table(bar_types, hooks):
    tab = {}
    for bt in bar_types:
        for hk in hooks:
            v = None
            try:
                v = bt['elem'].GetHookLength(hk['elem'].Id) * FT
            except Exception:
                v = None
            if not v or v <= 0.01:
                v = max(6 * bt['dn'], 7.5)
            tab[(bt['name'], hk['name'])] = round(v, 1)
    return tab


def get_section_templates(doc):
    out = {}
    for v in FilteredElementCollector(doc).OfClass(View):
        try:
            if v.IsTemplate and v.ViewType == ViewType.Section:
                out[el_name(v)] = v.Id
        except Exception:
            pass
    return out


# ================================================================ geometria
def _solids(geo, out):
    for obj in geo:
        if isinstance(obj, Solid):
            try:
                if obj.Volume > 1e-6:
                    out.append(obj)
            except Exception:
                pass
        elif isinstance(obj, GeometryInstance):
            _solids(obj.GetInstanceGeometry(), out)


def _opts():
    opt = Options()
    opt.ComputeReferences = False
    opt.DetailLevel = ViewDetailLevel.Fine
    return opt


def _edge_points(sols):
    pts = []
    for s in sols:
        for e in s.Edges:
            try:
                for p in e.Tessellate():
                    pts.append(p)
            except Exception:
                pass
    return pts


def _original_points(el):
    """Geometria del ejemplar antes de uniones y cortes (FamilyInstance), en coordenadas del modelo."""
    try:
        geo = el.GetOriginalGeometry(_opts())
        if geo is None:
            return []
        geo = geo.GetTransformed(el.GetTransform())
    except Exception:
        return []
    sols = []
    _solids(geo, sols)
    return _edge_points(sols)


def _param_len(el, names, bips):
    """Primer parametro de longitud > 0 del ejemplar o de su tipo (pies)."""
    owners = [el]
    try:
        owners.append(el.Document.GetElement(el.GetTypeId()))
    except Exception:
        pass
    for o in owners:
        if o is None:
            continue
        for bip in bips:
            try:
                p = o.get_Parameter(bip)
                if p is not None and p.HasValue and p.AsDouble() > 1e-6:
                    return p.AsDouble()
            except Exception:
                pass
        for n in names:
            try:
                p = o.LookupParameter(n)
                if p is not None and p.HasValue and p.AsDouble() > 1e-6:
                    return p.AsDouble()
            except Exception:
                pass
    return None


def _section_box_points(el):
    """Caja de la viga a partir de su linea de ubicacion y el ancho / alto de su tipo (b, h).

    Sirve cuando la viga no tiene ningun solido (p. ej. embebida en un muro o losa que la corta
    por completo): su armado se sigue considerando con la seccion del tipo."""
    crv = getattr(getattr(el, 'Location', None), 'Curve', None)
    if not isinstance(crv, Line):
        return []
    b = _param_len(el, (u'b', u'B', u'Ancho', u'ancho', u'Width', u'width', u'Base', u'base'),
                   (BuiltInParameter.STRUCTURAL_SECTION_COMMON_WIDTH,))
    h = _param_len(el, (u'h', u'H', u'Altura', u'altura', u'Peralte', u'peralte', u'Height', u'height',
                        u'Depth', u'depth'),
                   (BuiltInParameter.STRUCTURAL_SECTION_COMMON_HEIGHT,))
    if not b or not h:
        return []
    p0, p1 = crv.GetEndPoint(0), crv.GetEndPoint(1)
    d = XYZ(p1.X - p0.X, p1.Y - p0.Y, 0.0)
    if d.GetLength() < 1e-6:
        return []
    d = d.Normalize()
    n = XYZ(-d.Y, d.X, 0.0)
    z1 = max(p0.Z, p1.Z)
    z0 = z1 - h
    try:                                   # la caja del ejemplar respeta la justificacion en z
        bb = el.get_BoundingBox(None)
        if bb is not None and bb.Max.Z - bb.Min.Z > 1e-3:
            z0, z1 = bb.Min.Z, bb.Max.Z
    except Exception:
        pass
    pts = []
    for p in (p0, p1):
        for k in (-0.5, 0.5):
            for z in (z0, z1):
                pts.append(XYZ(p.X + n.X * b * k, p.Y + n.Y * b * k, z))
    return pts


def solid_points(el, src=None):
    """Puntos de la geometria del elemento (pies).

    Si el elemento no tiene solido propio (embebido o cortado por completo por otro elemento) se usa,
    en orden: su geometria original (antes de uniones), la caja de su seccion (b x h del tipo sobre la
    linea de ubicacion) y por ultimo su caja envolvente. 'src' (lista) recibe la fuente usada."""
    pts = []
    try:
        geo = el.get_Geometry(_opts())
    except Exception:
        geo = None
    if geo is not None:
        sols = []
        _solids(geo, sols)
        pts = _edge_points(sols)
    if pts:
        how = 'solid'
    else:
        how = 'original'
        pts = _original_points(el)
        if not pts:
            how = 'section'
            pts = _section_box_points(el)
        if not pts:
            how = 'bbox'
            try:
                bb = el.get_BoundingBox(None)
                if bb is not None:
                    pts = [XYZ(x, y, z) for x in (bb.Min.X, bb.Max.X) for y in (bb.Min.Y, bb.Max.Y)
                           for z in (bb.Min.Z, bb.Max.Z)]
            except Exception:
                pts = []
    if src is not None and pts:
        src.append(how)
    return pts


class Frame(object):
    """Sistema local de la cadena de vigas (pies)."""

    def __init__(self, O, u):
        self.O = O
        self.u = u
        self.v = XYZ.BasisZ.CrossProduct(u).Normalize()

    def s(self, p):
        return (p.X - self.O.X) * self.u.X + (p.Y - self.O.Y) * self.u.Y

    def l(self, p):
        return (p.X - self.O.X) * self.v.X + (p.Y - self.O.Y) * self.v.Y


def _ranges(frame, pts):
    ss = [frame.s(p) for p in pts]
    ll = [frame.l(p) for p in pts]
    zz = [p.Z for p in pts]
    return min(ss), max(ss), min(ll), max(ll), min(zz), max(zz)


def _own_z(el, jz):
    """Altura propia de la viga, antes de uniones y cortes: (z0, z1) en pies.

    En Revit las losas / cimentaciones unidas cortan la viga (su parte superior desaparece del
    solido), pero el concreto es monolitico y el plano arma la viga con su altura completa (p. ej.
    hasta el NPT). Se usa la geometria original del ejemplar y, si no se puede, su caja; solo si la
    contienen, si no, la geometria unida."""
    tol = 1.0 / FT
    cands = []
    try:
        opt = Options()
        opt.ComputeReferences = False
        opt.DetailLevel = ViewDetailLevel.Fine
        geo = el.GetOriginalGeometry(opt)
        if geo is not None:
            geo = geo.GetTransformed(el.GetTransform())
            sols = []
            _solids(geo, sols)
            zs = []
            for s in sols:
                for e in s.Edges:
                    try:
                        zs.extend(p.Z for p in e.Tessellate())
                    except Exception:
                        pass
            if zs:
                cands.append((min(zs), max(zs)))
    except Exception:
        pass
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            cands.append((bb.Min.Z, bb.Max.Z))
    except Exception:
        pass
    h = jz[1] - jz[0]
    for z0, z1 in cands:
        if z0 <= jz[0] + tol and z1 >= jz[1] - tol and (z1 - z0) <= h + 200.0 / FT:
            return z0, z1
    return jz


def _hdir(crv):
    d = XYZ(crv.Direction.X, crv.Direction.Y, 0.0).Normalize()
    if d.X < -1e-6 or (abs(d.X) < 1e-6 and d.Y < 0):
        d = d.Negate()
    return d


ALIGN_TOL = 1.0 / FT                 # 1 cm: vigas en el mismo eje
SNAP_TOL = 1.5 / FT                  # el doblez se lleva al apoyo cercano si el eje se desvia hasta esto
MAX_SKEW_DEG = 30.0                  # mas giro que esto respecto a la cadena: no es parte de la viga continua
MIN_Z_OVERLAP = 10.0 / FT            # dos vigas seguidas deben traslaparse al menos esto en altura
MAX_STEP = 30.0 / FT                 # escalon: hueco vertical maximo entre dos vigas seguidas (si se separan mas,
                                     # como una contratrabe y una trabe de otro nivel, se arman aparte)
MAX_STEP_OVERLAP = 60.0 / FT         # en planta, una viga escalonada puede entrar en la otra a lo mas esto


def _level_step(A, B):
    """A y B son tramos seguidos de una viga con cambio de nivel (escalon): una termina donde empieza la otra
    en planta (sin encimarse, como en dos pisos distintos) y el desnivel no pasa de MAX_STEP."""
    if B['s0'] < A['s1'] - MAX_STEP_OVERLAP:
        return False                     # se enciman en planta: vigas de otro piso
    gap = max(A['z0'], B['z0']) - min(A['z1'], B['z1'])      # hueco vertical (negativo si se traslapan)
    return gap <= MAX_STEP


def analyze_chain(doc, beams):
    """Devuelve (info, errores). info contiene el modelo en cm y el marco local.

    Cada viga se mide en su propia direccion (ancho real aunque este girada) y con su altura propia
    (las uniones con losas no la recortan). Las contratrabes desplazadas o giradas unos grados (como
    en el plano) se arman con su propio eje: info['axis'] describe el eje en planta y info['bends'] los
    apoyos donde cambia (ahi las varillas se doblan en planta o se anclan)."""
    errors = []
    lines = []
    for bm in beams:
        crv = getattr(getattr(bm, 'Location', None), 'Curve', None)
        if not isinstance(crv, Line):
            errors.append((bm, u'no es una viga recta'))
            continue
        d = crv.Direction
        if abs(d.Z) > 0.02:
            errors.append((bm, u'viga inclinada (no soportada)'))
            continue
        lines.append((bm, crv))
    if not lines:
        return None, errors
    # direccion de la cadena: la que comparten mas metros de viga (una viga girada no la define)
    cos_same = math.cos(math.radians(0.1))
    best = None
    for bm, crv in lines:
        d = _hdir(crv)
        tot = sum(c.Length for _, c in lines if abs(_hdir(c).DotProduct(d)) >= cos_same)
        if best is None or tot > best[0] + 1e-6:
            best = (tot, d, crv)
    u = best[1]
    p0 = best[2].GetEndPoint(0)
    fr = Frame(XYZ(p0.X, p0.Y, 0.0), u)

    cos_max = math.cos(math.radians(MAX_SKEW_DEG))
    chain = []
    embedded = []                        # vigas sin solido propio (embebidas): se miden de otra forma
    for bm, crv in lines:
        d = _hdir(crv)
        if d.DotProduct(u) < 0:
            d = d.Negate()
        dot = d.DotProduct(u)
        if dot < cos_max:
            errors.append((bm, u'no es paralela a las demás vigas (%.1f°)'
                           % math.degrees(math.acos(max(-1.0, min(1.0, dot))))))
            continue
        src = []
        pts = solid_points(bm, src)
        if not pts:
            errors.append((bm, u'sin geometría (ni sólido, ni sección b × h, ni caja)'))
            continue
        if src[0] != 'solid':
            embedded.append((bm, src[0]))
        n = XYZ.BasisZ.CrossProduct(d).Normalize()
        if n.DotProduct(fr.v) < 0:
            n = n.Negate()
        P0 = crv.GetEndPoint(0)
        bb = [(p.X - P0.X) * n.X + (p.Y - P0.Y) * n.Y for p in pts]
        w = max(bb) - min(bb)
        bc = (max(bb) + min(bb)) / 2.0                      # eje geometrico respecto a la ubicacion
        C = XYZ(P0.X + n.X * bc, P0.Y + n.Y * bc, 0.0)
        cs, cl, du, dv = fr.s(C), fr.l(C), dot, d.DotProduct(fr.v)
        ss = [fr.s(p) for p in pts]
        s0, s1 = min(ss), max(ss)
        lc_a = cl + (s0 - cs) / du * dv
        lc_b = cl + (s1 - cs) / du * dv
        jz = (min(p.Z for p in pts), max(p.Z for p in pts))
        z0, z1 = _own_z(bm, jz)
        chain.append({'elem': bm, 's0': s0, 's1': s1, 'l0': min(lc_a, lc_b) - w / 2.0,
                      'l1': max(lc_a, lc_b) + w / 2.0, 'z0': z0, 'z1': z1,
                      'w': w, 'lc_a': lc_a, 'lc_b': lc_b, 'bc': bc, 'jz': jz,
                      'angle': math.degrees(math.atan2(dv, du))})
    if not chain:
        return None, errors

    # continuidad: cada viga debe seguir a la anterior (se traslapan en planta y en altura en la union).
    # Si la cadena se corta, se arma el grupo seguido mas largo y las demas se omiten con el motivo
    chain.sort(key=lambda c: c['s0'])
    groups = [[chain[0]]]
    why = {}
    for A, B in zip(chain, chain[1:]):
        sj = (A['s1'] + B['s0']) / 2.0
        la, lb = _c_at(A, sj), _c_at(B, sj)
        dz = min(A['z1'], B['z1']) - max(A['z0'], B['z0'])
        if abs(la - lb) > (A['w'] + B['w']) / 2.0:
            why[id(B)] = u'no sigue a la viga Id %d en planta (desfase de %.0f cm en la unión)' \
                % (eid_int(A['elem'].Id), abs(la - lb) * FT)
            groups.append([B])
        elif dz < MIN_Z_OVERLAP and not _level_step(A, B):
            why[id(B)] = u'está a otro nivel que la viga Id %d (no se traslapan en altura); arme cada una aparte' \
                % eid_int(A['elem'].Id)
            groups.append([B])
        else:
            if abs(A['z1'] - B['z1']) > 1.0 / FT or abs(A['z0'] - B['z0']) > 1.0 / FT:
                B['step'] = True                # cambio de nivel (escalon) entre A y B
            groups[-1].append(B)
    best = max(groups, key=lambda g: sum(c['s1'] - c['s0'] for c in g))
    for g in groups:
        if g is best:
            continue
        for c in g:
            errors.append((c['elem'], why.get(id(g[0])) or u'no es parte de la viga continua'))
    chain = list(best)

    chain.sort(key=lambda c: c['s0'])
    s_min = min(c['s0'] for c in chain)
    s_max = max(c['s1'] for c in chain)
    l0 = min(c['l0'] for c in chain)
    l1 = max(c['l1'] for c in chain)
    z0 = min(c['z0'] for c in chain)
    z1 = max(c['z1'] for c in chain)
    warns = []
    how_txt = {'original': u'su geometría original (antes de uniones)',
               'section': u'la sección b × h de su tipo sobre su línea de ubicación',
               'bbox': u'su caja envolvente (revise el ancho si está girada en planta)'}
    for bm, how in embedded:
        if any(c['elem'] is bm for c in chain):
            warns.append(u'la viga Id %d no tiene sólido propio (embebida o cortada por otro elemento); '
                         u'se midió con %s' % (eid_int(bm.Id), how_txt.get(how, how)))
    info = {'frame': fr, 'chain': chain, 's_min': s_min, 'L': (s_max - s_min) * FT,
            'l_c': (l0 + l1) / 2.0, 'z_c': (z0 + z1) / 2.0, 'b': (l1 - l0) * FT, 'h': (z1 - z0) * FT,
            'l0': l0, 'l1': l1, 'z0': z0, 'z1': z1, 'warns': warns}
    info['supports'] = find_supports(doc, info)

    # eje de la cadena en planta: cada contratrabe con su eje; en los apoyos donde cambia (desplazada o
    # girada) el eje pasa de una a otra dentro del apoyo
    runs, bends, notes = _build_axis(chain, info['supports'], s_min, info.get('support_full'))
    # hueco entre vigas que ningun apoyo llena (en la proyeccion, antes de pasar a longitudes sobre el eje)
    for a, b in zip(chain, chain[1:]):
        g0, g1 = (a['s1'] - s_min) * FT, (b['s0'] - s_min) * FT
        if g1 - g0 <= 100.0:
            continue
        cov = g0
        for q0, q1, _, _ in sorted(info['supports']):
            if q0 <= cov + 2.0:
                cov = max(cov, q1)
        if cov < g1 - 2.0:
            warns.append(u'hay un hueco de %.2f m sin apoyo entre las vigas Id %d e Id %d'
                         % ((g1 - g0) / 100.0, eid_int(a['elem'].Id), eid_int(b['elem'].Id)))
    # posiciones a lo largo del eje (longitud real de cada viga, tambien de las giradas)
    info['axis'] = {'runs': [r['line'] for r in runs], 'bends': [(b['lo'], b['hi']) for b in bends], 's0': s_min}
    ax = Axis(fr, info['axis']['runs'], info['axis']['bends'], s_min)

    def arc(cm):
        return ax.to_arc(s_min + cm / FT) * FT

    info['supports'] = [(arc(a), arc(b), n, k) for a, b, n, k in info['supports']]
    info['L'] = ax.to_arc(s_max) * FT
    # apoyos extremos: cada varilla llega al pano lejano del elemento del apoyo que esta a su altura (el
    # dado, la pila de abajo...); si ninguno llega a su altura, termina en el extremo de la viga
    sparts = info.get('support_parts') or [None] * len(info['supports'])
    ep = [None, None]
    for (a, b, n, k), pp in zip(info['supports'], sparts):
        if not pp:
            continue
        conv = [(arc(q[0]), arc(q[1]), (q[2] - info['z_c']) * FT, (q[3] - info['z_c']) * FT) for q in pp]
        if a <= 5.0 and b >= -5.0 and a < -1.0:
            ep[0] = conv
        if b >= info['L'] - 5.0 and a <= info['L'] + 5.0 and b > info['L'] + 1.0:
            ep[1] = conv
    info['end_parts'] = tuple(ep)
    full = info.get('support_full') or [True] * len(info['supports'])
    for (a, b, n, k), f in zip(info['supports'], full):
        if not f and ((a <= 5.0 and b >= -5.0 and a < -1.0) or (b >= info['L'] - 5.0 and b > info['L'] + 1.0)):
            warns.append(u'el apoyo %s no cubre todo el peralte de la viga: las varillas que quedan fuera de su '
                         u'altura terminan en el extremo de la viga' % n)
    info['bends'] = [(arc(b['a']), arc(b['b']), b['name'], b.get('node', True)) for b in bends]
    part_of = {}
    for k, r in enumerate(runs):
        for c in r['parts']:
            part_of[id(c)] = k
    parts = []
    half = 0.0
    for c in chain:
        r = runs[part_of[id(c)]]
        sm = (c['s0'] + c['s1']) / 2.0
        pl = c['lc_a'] + (c['lc_b'] - c['lc_a']) * 0.5
        xc = (pl - _line_at(r['line'], sm)) * FT
        yc = ((c['z0'] + c['z1']) / 2.0 - info['z_c']) * FT
        parts.append((ax.to_arc(c['s0']) * FT, ax.to_arc(c['s1']) * FT, c['w'] * FT, (c['z1'] - c['z0']) * FT, xc, yc))
        half = max(half, abs(xc) + c['w'] * FT / 2.0)
        m_c = (c['lc_b'] - c['lc_a']) / max(c['s1'] - c['s0'], 1e-9)
        if abs(math.degrees(math.atan(m_c) - math.atan(r['line'][2]))) > 0.1:
            ref = max(r['parts'], key=lambda q: q['s1'] - q['s0'])
            warns.append(u'la viga Id %d está girada %.2f° respecto a la viga Id %d y no hay un apoyo entre '
                         u'ellas donde doblar las varillas: se arma con el eje de la Id %d'
                         % (eid_int(c['elem'].Id), abs(math.degrees(math.atan(m_c) - math.atan(r['line'][2]))),
                            eid_int(ref['elem'].Id), eid_int(ref['elem'].Id)))
    info['parts'] = parts
    info['b'] = 2.0 * half
    for bd in bends:
        if bd.get('kink'):
            if bd.get('node'):
                warns.append(u'la viga gira %.2f° en %s (Id %d → Id %d), sin apoyo: las varillas se doblan en planta '
                             u'ahí y el claro se divide en ese punto' % (bd['ang'], bd['name'], bd['ids'][0],
                                                                        bd['ids'][1]))
            else:
                warns.append(u'la viga gira %.2f° en la unión de Id %d → Id %d (%s del apoyo %s, que no cubre todo '
                             u'el peralte): las varillas se doblan en planta en la unión'
                             % (bd['ang'], bd['ids'][0], bd['ids'][1],
                                (u'a %.0f cm' % bd['dist']) if bd['dist'] > 0.5 else u'dentro', bd['near']))
            continue
        t = []
        if bd['dl'] > 0.5:
            t.append(u'desfase %.1f cm' % bd['dl'])
        if bd['ang'] > 0.05:
            t.append(u'giro %.2f°' % bd['ang'])
        warns.append(u'en el apoyo %s cambia el eje de la viga (%s, Id %d → Id %d): el armado sigue el eje de '
                     u'cada viga; en el apoyo las varillas se doblan en planta o se anclan (Configuración)'
                     % (bd['name'], u', '.join(t) or u'ajuste', bd['ids'][0], bd['ids'][1]))
    for kind, name, v in notes:
        if kind == 'steep':
            warns.append(u'en el apoyo %s el desfase de %.1f cm es muy grande para doblar en planta (más de 1:6): '
                         u'se trata como cambio de sección' % (name, v))
    cut = [c for c in chain if c['jz'][1] < c['z1'] - 1.0 / FT or c['jz'][0] > c['z0'] + 1.0 / FT]
    if cut:
        parts_ = []
        for c in cut:
            t = []
            if c['jz'][1] < c['z1'] - 1.0 / FT:
                t.append(u'%.0f cm arriba' % ((c['z1'] - c['jz'][1]) * FT))
            if c['jz'][0] > c['z0'] + 1.0 / FT:
                t.append(u'%.0f cm abajo' % ((c['jz'][0] - c['z0']) * FT))
            parts_.append(u'Id %d (%s)' % (eid_int(c['elem'].Id), u' y '.join(t)))
        warns.append(u'elementos unidos (losa / cimentación) recortan la viga: %s. Se arma con la altura '
                     u'propia de la viga (concreto monolítico)' % u', '.join(parts_))
    ws = [p[2] for p in parts]
    hs = [p[3] for p in parts]
    ys = [p[5] for p in parts]
    xs = [p[4] for p in parts]
    if max(ws) - min(ws) > 1.0 or max(hs) - min(hs) > 1.0 or max(ys) - min(ys) > 1.0 or max(xs) - min(xs) > 1.0:
        warns.append(u'secciones o niveles distintos por tramo: cada tramo se arma con su sección y en '
                     u'los cambios las varillas se anclan en el apoyo')
    # cambios de nivel (vigas escalonadas): se arman como una sola cadena
    for A, B in zip(chain, chain[1:]):
        dt = (B['z1'] - A['z1']) * FT
        if abs(dt) > 1.0:
            warns.append(u'cambio de nivel entre Id %d e Id %d (lecho superior %s %.1f cm): las varillas que caben '
                         u'en la otra sección siguen rectas el anclaje de cambio de sección; las demás terminan con '
                         u'gancho a 90° en el paño lejano del apoyo'
                         % (eid_int(A['elem'].Id), eid_int(B['elem'].Id), u'sube' if dt > 0 else u'baja', abs(dt)))
    return info, errors


def _covers(ranges, z0, z1):
    """Los intervalos (z0, z1) juntos cubren [z0, z1] sin huecos (tolerancia 3 cm)."""
    cur = z0
    for a, b in sorted(ranges):
        if a > cur + 0.1:
            break
        cur = max(cur, b)
    return cur >= z1


def _c_at(c, s):
    """Eje geometrico (l, pies) de la viga c de la cadena en la posicion s (prolongado)."""
    return c['lc_a'] + (c['lc_b'] - c['lc_a']) * (s - c['s0']) / max(c['s1'] - c['s0'], 1e-9)


def _line_at(line, s):
    s_ref, l_ref, m = line
    return l_ref + m * (s - s_ref)


def _build_axis(chain, sup, s_min, full=None):
    """Eje en planta de la cadena (pies, marco de la cadena).
    Devuelve (runs, bends, notes): runs = tramos de eje recto [{'line': (s_ref, l_ref, pendiente),
    'parts': [...]}]; bends = apoyos donde el eje pasa de un run al siguiente [{'lo', 'hi' (pies),
    'a', 'b' (cm), 'name', 'dl' (cm), 'ang' (grados), 'ids'}]. Solo se dobla en un apoyo (ancho >= 10 cm),
    entre vigas del mismo ancho y con desfase <= 1:6 del apoyo; si no, el cambio queda como cambio de
    seccion (como antes).
    Solo cuentan los apoyos que cubren todo el peralte de la viga (si no, arriba del apoyo no hay concreto
    donde doblar). Si la viga gira a unos centimetros de uno de esos apoyos, el doblez se lleva al apoyo
    cuando el eje se desvia <= SNAP_TOL. Si no, el doblez queda en la union misma ('kink', sin ancho);
    solo si esta lejos de los apoyos se crea un nodo Q1, Q2... que divide el claro."""
    tan01 = math.tan(math.radians(0.1))
    full = list(full) if full else [True] * len(sup)
    full += [True] * (len(sup) - len(full))
    sup_full = [q for q, f in zip(sup, full) if f]
    groups = [[chain[0]]]
    bends = []
    notes = []
    nq = [0]
    for A, B in zip(chain, chain[1:]):
        lA = (A['s0'], A['lc_a'], (A['lc_b'] - A['lc_a']) / max(A['s1'] - A['s0'], 1e-9))
        lB = (B['s0'], B['lc_a'], (B['lc_b'] - B['lc_a']) / max(B['s1'] - B['s0'], 1e-9))
        J0, J1 = min(A['s1'], B['s0']), max(A['s1'], B['s0'])
        j0 = (J0 - s_min) * FT
        j1 = (J1 - s_min) * FT
        sp = [q for q in sup_full if q[0] - 2.0 <= j0 and q[1] + 2.0 >= j1 and q[1] - q[0] >= 10.0]
        if not sp:
            # apoyo a unos centimetros de la union: el doblez se hace ahi si el eje casi no se desvia
            best = None
            for q in sup_full:
                if q[1] - q[0] < 10.0:
                    continue
                lo_, hi_ = s_min + q[0] / FT, s_min + q[1] / FT
                dist = max(0.0, lo_ - J1, J0 - hi_)
                if dist > 100.0 / FT:
                    continue
                if J1 <= lo_:
                    dev = abs(_line_at(lA, lo_) - _line_at(lB, lo_))
                elif J0 >= hi_:
                    dev = abs(_line_at(lA, hi_) - _line_at(lB, hi_))
                else:
                    dev = 0.0
                if dev <= SNAP_TOL and (best is None or dist < best[0]):
                    best = (dist, q)
            if best is not None:
                sp = [best[1]]
        if not sp:
            # sin apoyo: si los ejes se cruzan en la union y solo cambia la direccion, quiebre ahi
            Jm = (J0 + J1) / 2.0
            if abs(_line_at(lA, Jm) - _line_at(lB, Jm)) <= ALIGN_TOL and abs(lA[2] - lB[2]) > tan01 and \
                    abs(A['w'] - B['w']) <= ALIGN_TOL:
                nq[0] += 1
                c = (Jm - s_min) * FT
                # apoyo mas cercano (o el que la contiene): si el quiebre queda cerca, no divide el claro
                dmin, near = None, None
                for q in sup:
                    if q[1] - q[0] < 10.0:
                        continue
                    d = max(0.0, q[0] - c, c - q[1]) / FT
                    if dmin is None or d < dmin:
                        dmin, near = d, q[2]
                tdif = abs((lB[2] - lA[2]) / (1.0 + lA[2] * lB[2]))
                node = dmin is None or dmin * tdif > SNAP_TOL
                bends.append({'lo': Jm, 'hi': Jm, 'a': c, 'b': c, 'name': u'Q%d' % nq[0], 'dl': 0.0,
                              'ang': abs(math.degrees(math.atan(lB[2]) - math.atan(lA[2]))),
                              'ids': (eid_int(A['elem'].Id), eid_int(B['elem'].Id)), 'kink': True,
                              'node': node, 'near': near, 'dist': (dmin or 0.0) * FT})
                groups.append([B])
                continue
            groups[-1].append(B)
            continue
        a, b, name = sp[0][0], sp[0][1], sp[0][2]
        lo, hi = s_min + a / FT, s_min + b / FT
        la, lb = _line_at(lA, lo), _line_at(lB, hi)
        if abs(la - lb) <= ALIGN_TOL and abs(lA[2] - lB[2]) <= tan01 and \
                abs(_line_at(lA, hi) - lb) <= ALIGN_TOL:
            groups[-1].append(B)
            continue
        if abs(A['w'] - B['w']) > ALIGN_TOL:
            groups[-1].append(B)                  # cambio de ancho: cambio de seccion (como antes)
            continue
        if abs(lb - la) > (hi - lo) / 6.0:
            groups[-1].append(B)
            notes.append(('steep', name, abs(lb - la) * FT))
            continue
        bends.append({'lo': lo, 'hi': hi, 'a': a, 'b': b, 'name': name,
                      'dl': abs(la - lb) * FT,
                      'ang': abs(math.degrees(math.atan(lB[2]) - math.atan(lA[2]))),
                      'ids': (eid_int(A['elem'].Id), eid_int(B['elem'].Id))})
        groups.append([B])
    runs = []
    for g in groups:
        ref = max(g, key=lambda c: c['s1'] - c['s0'])
        m = (ref['lc_b'] - ref['lc_a']) / max(ref['s1'] - ref['s0'], 1e-9)
        runs.append({'line': (ref['s0'], ref['lc_a'], m), 'parts': g})
    return runs, bends, notes


class Axis(object):
    """Eje de la cadena en planta (pies, marco de la cadena): runs rectos y, entre ellos, transiciones
    dentro de los apoyos donde cambia el eje."""

    def __init__(self, fr, runs, bends, s0=0.0):
        self.fr = fr
        self.runs = list(runs) or [(0.0, 0.0, 0.0)]
        self.bends = list(bends)
        self.s0 = s0                                     # origen de la longitud sobre el eje
        self.bp = sorted(set(x for lo, hi in self.bends for x in (lo, hi)))

    # ---- longitud a lo largo del eje (una viga girada mide su largo real, no su proyeccion)
    def _k(self, s):
        return math.sqrt(1.0 + self._m(s) ** 2)

    def _arc_between(self, a, b):
        pts = [a] + [x for x in self.bp if a < x < b] + [b]
        return sum((q - p) * self._k((p + q) / 2.0) for p, q in zip(pts, pts[1:]))

    def to_arc(self, s):
        """Longitud sobre el eje (pies) desde s0 hasta s (proyeccion sobre la cadena, pies)."""
        return self._arc_between(self.s0, s) if s >= self.s0 else -self._arc_between(s, self.s0)

    def from_arc(self, a):
        """Inversa de to_arc."""
        cur, rem = self.s0, a
        if a >= 0:
            for x in [x for x in self.bp if x > self.s0]:
                k = self._k((cur + x) / 2.0)
                if rem <= (x - cur) * k:
                    return cur + rem / k
                rem -= (x - cur) * k
                cur = x
            return cur + rem / self._k(cur + 1.0)
        rem = -a
        for x in reversed([x for x in self.bp if x < self.s0]):
            k = self._k((cur + x) / 2.0)
            if rem <= (cur - x) * k:
                return cur - rem / k
            rem -= (cur - x) * k
            cur = x
        return cur - rem / self._k(cur - 1.0)

    def run_l(self, k, s):
        return _line_at(self.runs[k], s)

    def locate(self, s):
        for k, (lo, hi) in enumerate(self.bends):
            if s < lo:
                return 'run', k
            if s <= hi:
                return 'bend', k
        return 'run', len(self.bends)

    def _bend_line(self, k):
        lo, hi = self.bends[k]
        la, lb = self.run_l(k, lo), self.run_l(k + 1, hi)
        if hi - lo < 1e-6:                               # quiebre sin ancho: pendiente media
            return lo, (la + lb) / 2.0, (self.runs[k][2] + self.runs[k + 1][2]) / 2.0
        return lo, la, (lb - la) / (hi - lo)

    def l(self, s):
        kind, k = self.locate(s)
        return self.run_l(k, s) if kind == 'run' else _line_at(self._bend_line(k), s)

    def _m(self, s):
        kind, k = self.locate(s)
        return self.runs[k][2] if kind == 'run' else self._bend_line(k)[2]

    def slope(self, s):
        """Pendiente del eje en s; en un quiebre, el promedio de los dos lados (bisectriz)."""
        e = 1e-6
        return (self._m(s - e) + self._m(s + e)) / 2.0

    def breaks(self, s0, s1):
        out = []
        for lo, hi in self.bends:
            for x in (lo, hi):
                if s0 + 1e-6 < x < s1 - 1e-6:
                    out.append(x)
        return sorted(out)

    def normal(self, m):
        fr = self.fr
        return XYZ(fr.v.X - fr.u.X * m, fr.v.Y - fr.u.Y * m, 0.0).Normalize()

    def direction(self, m):
        fr = self.fr
        return XYZ(fr.u.X + fr.v.X * m, fr.u.Y + fr.v.Y * m, 0.0).Normalize()

    def crosses(self, s0, s1):
        """La varilla (pies) cruza completo algun cambio de eje."""
        return any(s0 < lo - 1e-6 and s1 > hi + 1e-6 for lo, hi in self.bends)

    def home(self, s0, s1):
        """Run donde queda la mayor parte de la varilla (pies)."""
        nb = len(self.bends)
        best = None
        for k in range(nb + 1):
            lo = self.bends[k - 1][1] if k > 0 else -1e12
            hi = self.bends[k][0] if k < nb else 1e12
            ov = min(s1, hi) - max(s0, lo)
            mid = (s0 + s1) / 2.0
            key = ov if ov > 0 else -min(abs(mid - lo), abs(mid - hi))
            if best is None or key > best[0]:
                best = (key, k)
        return best[1]


def find_supports(doc, info):
    fr = info['frame']
    ids = set(eid_int(c['elem'].Id) for c in info['chain'])
    s_min = info['s_min']
    Lft = info['L'] / FT
    corners = []
    for s in (s_min - 3.0, s_min + Lft + 3.0):
        for l in (info['l0'] - 2.0, info['l1'] + 2.0):
            corners.append(fr.O.Add(fr.u.Multiply(s)).Add(fr.v.Multiply(l)))
    xs = [p.X for p in corners]
    ys = [p.Y for p in corners]
    outline = Outline(XYZ(min(xs), min(ys), info['z0'] - 0.5), XYZ(max(xs), max(ys), info['z1'] + 0.5))
    cats = List[BuiltInCategory]()
    for c in (BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_Walls,
              BuiltInCategory.OST_StructuralFraming):
        cats.Add(c)
    col = FilteredElementCollector(doc).WherePasses(ElementMulticategoryFilter(cats)) \
        .WherePasses(BoundingBoxIntersectsFilter(outline)).WhereElementIsNotElementType()
    found = []
    for el in col:
        if eid_int(el.Id) in ids:
            continue
        is_col = _cat_is(el, BuiltInCategory.OST_StructuralColumns)
        is_wall = _cat_is(el, BuiltInCategory.OST_Walls)
        kind = u'columna' if is_col else (u'muro' if is_wall else u'viga')
        pts = solid_points(el)
        if not pts:
            continue
        cs0, cs1, cl0, cl1, cz0, cz1 = _ranges(fr, pts)
        # altura propia del apoyo (una losa unida puede recortar el dado igual que a la viga)
        cz0, cz1 = _own_z(el, (cz0, cz1))
        # se compara con la viga de la cadena que esta ahi (su eje, su ancho, su nivel y su direccion),
        # no con la envolvente: la cadena puede girar o cambiar de nivel
        near = [c for c in info['chain'] if c['s0'] - 0.1 <= cs1 and c['s1'] + 0.1 >= cs0]
        if not near:
            near = [min(info['chain'], key=lambda c: min(abs(c['s0'] - cs1), abs(c['s1'] - cs0)))]
        sc = (cs0 + cs1) / 2.0
        pz0, pz1 = min(c['z0'] for c in near), max(c['z1'] for c in near)
        bl0 = min(_c_at(c, sc) - c['w'] / 2.0 for c in near)
        bl1 = max(_c_at(c, sc) + c['w'] / 2.0 for c in near)
        crv = getattr(getattr(el, 'Location', None), 'Curve', None)
        if not is_col and crv is not None:
            try:
                d = crv.Direction if isinstance(crv, Line) else \
                    crv.GetEndPoint(1).Subtract(crv.GetEndPoint(0)).Normalize()
                dot = 0.0
                for c in near:
                    m = (c['lc_b'] - c['lc_a']) / max(c['s1'] - c['s0'], 1e-9)
                    k = math.sqrt(1.0 + m * m)
                    dx, dy = (fr.u.X + fr.v.X * m) / k, (fr.u.Y + fr.v.Y * m) / k
                    dot = max(dot, abs(d.X * dx + d.Y * dy))
                if (is_wall and dot > 0.7) or (not is_wall and dot > 0.3):
                    continue
            except Exception:
                pass
        if cz1 < pz0 - 0.05 or cz0 > pz1 - 0.03:
            continue
        if is_col:
            if cl0 > bl1 - 0.03 or cl1 < bl0 + 0.03:
                continue
        else:
            if cl0 > bl0 + 0.03 or cl1 < bl1 - 0.03:
                continue
        a = (cs0 - s_min) * FT
        b = (cs1 - s_min) * FT
        if b < -3.0 or a > info['L'] + 3.0 or b - a > 300.0:
            continue
        # [a, b, tipo, elemento, partes (a, b, z0, z1) de cada elemento, z0 / z1 de la viga ahi]
        found.append([a, b, kind, el, [(a, b, cz0, cz1)], pz0, pz1])
    found.sort(key=lambda f: f[0])
    merged = []
    for f in found:
        if merged and f[0] <= merged[-1][1] + 0.5:
            merged[-1][1] = max(merged[-1][1], f[1])
            merged[-1][4] = merged[-1][4] + f[4]
            merged[-1][5] = min(merged[-1][5], f[5])
            merged[-1][6] = max(merged[-1][6], f[6])
            if f[2] == u'columna':
                merged[-1][2] = u'columna'
            continue
        merged.append(list(f))
    # el apoyo cubre todo el peralte si sus elementos juntos (p. ej. dado + pila) lo cubren sin huecos
    info['support_full'] = [_covers([(q[2], q[3]) for q in f[4]], f[5] + 0.05, f[6] - 0.05) for f in merged]
    info['support_parts'] = [list(f[4]) for f in merged]
    grids = []
    for g in FilteredElementCollector(doc).OfClass(Grid):
        c = g.Curve
        if isinstance(c, Line):
            grids.append((el_name(g), c.GetEndPoint(0), c.Direction))
    out = []
    for k, (a, b, kind, el, _p, _z0, _z1) in enumerate(merged):
        name = None
        best = None
        for gname, p, d in grids:
            den = fr.u.X * d.Y - fr.u.Y * d.X
            if abs(den) < 1e-6:
                continue
            wx, wy = p.X - fr.O.X, p.Y - fr.O.Y
            t = (wx * d.Y - wy * d.X) / den
            sg = (t - s_min) * FT
            if a - 2.0 <= sg <= b + 2.0:
                dist = abs(sg - (a + b) / 2.0)
                if best is None or dist < best:
                    best = dist
                    name = gname
        out.append((a, b, name or u'%d' % (k + 1), kind))
    return out


# ================================================================ creacion
class _Swallow(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        try:
            for f in fa.GetFailureMessages():
                if f.GetSeverity() == FailureSeverity.Warning:
                    fa.DeleteWarning(f)
        except Exception:
            pass
        return FailureProcessingResult.Continue


def _curves(pts):
    crv = List[Curve]()
    for a, b in zip(pts, pts[1:]):
        crv.Add(Line.CreateBound(a, b))
    return crv


def _create(doc, style, bt, hs, he, host, norm, pts, o1, o2):
    if style != RebarStyle.StirrupTie:
        pts = AR.clean_pts(pts)
    return Rebar.CreateFromCurves(doc, style, bt, hs, he, host, norm, _curves(pts), o1, o2, True, True)


def point_in_polygon(x, y, pts):
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _free_end(c, other):
    a, b = c.GetEndPoint(0), c.GetEndPoint(1)
    o0, o1 = other.GetEndPoint(0), other.GetEndPoint(1)
    da = min(a.DistanceTo(o0), a.DistanceTo(o1))
    db = min(b.DistanceTo(o0), b.DistanceTo(o1))
    return a if da > db else b


def _centerline(doc, rb, pos=0):
    doc.Regenerate()
    return list(rb.GetCenterlineCurves(False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, pos))


class Builder(object):
    def __init__(self, doc, info, model, cfg, res, bt_elems, hook_elems, rep):
        self.doc = doc
        self.info = info
        self.fr = info['frame']
        self.model = model
        self.cfg = cfg
        self.res = res
        self.bt_elems = bt_elems
        self.hook_elems = hook_elems
        self.rep = rep
        self.created = []
        self.st_orient = None
        self.hosts = [(c['s0'], c['s1'], c['elem']) for c in info['chain']]
        ax = info.get('axis') or {'runs': [(0.0, info['l_c'], 0.0)], 'bends': [], 's0': info['s_min']}
        self.axis = Axis(self.fr, ax['runs'], ax['bends'], ax.get('s0', info['s_min']))
        self.bend_note = False

    # --------------------------------------------------------- coordenadas
    def _ft(self, s):
        """Longitud sobre el eje (cm) -> posicion s del marco (pies)."""
        return self.axis.from_arc(s / FT)

    def _cm(self, s):
        """Posicion s del marco (pies) -> longitud sobre el eje (cm)."""
        return self.axis.to_arc(s) * FT

    def P(self, s, x, y, run=None):
        """Punto del modelo: s a lo largo de la cadena, x lateral desde el eje de la viga, y vertical
        (cm). run = None: sigue el eje con sus quiebres; run = k: sobre la recta del run k."""
        fr = self.fr
        ax = self.axis
        ss = self._ft(s)
        if run is None:
            ll, m = ax.l(ss), ax.slope(ss)
        else:
            ll, m = ax.run_l(run, ss), ax.runs[run][2]
        n = ax.normal(m)
        xf = x / FT
        return XYZ(fr.O.X + fr.u.X * ss + fr.v.X * ll + n.X * xf, fr.O.Y + fr.u.Y * ss + fr.v.Y * ll + n.Y * xf,
                   self.info['z_c'] + y / FT)

    def local(self, p):
        fr = self.fr
        ax = self.axis
        s = fr.s(p)
        x = 0.0
        for _ in range(4):
            ll, m = ax.l(s), ax.slope(s)
            n = ax.normal(m)
            bx, by = fr.O.X + fr.u.X * s + fr.v.X * ll, fr.O.Y + fr.u.Y * s + fr.v.Y * ll
            x = (p.X - bx) * n.X + (p.Y - by) * n.Y
            s = fr.s(XYZ(p.X - n.X * x, p.Y - n.Y * x, 0.0))
        return (x * FT, (p.Z - self.info['z_c']) * FT, self._cm(s))

    def run_of(self, s0, s1):
        """None si la varilla (cm) cruza un cambio de eje (se dobla en planta); si no, su run."""
        a, b = self._ft(s0), self._ft(s1)
        if self.axis.crosses(a, b):
            return None
        return self.axis.home(a, b)

    def host_for(self, s0, s1):
        a = self._ft(s0)
        b = self._ft(s1)
        best = None
        for h0, h1, el in self.hosts:
            ov = min(b, h1) - max(a, h0)
            dist = 0.0 if ov > 0 else min(abs(a - h1), abs(b - h0))
            key = (-ov if ov > 0 else 1e6 + dist)
            if best is None or key < best[0]:
                best = (key, el)
        return best[1]

    def tag(self, rb, kind=None):
        # la funcion de la varilla (top_main, bot_add, side, stirrups...) queda en el comentario: al leer el
        # armado de la viga se sabe que es cada pieza sin adivinarlo por su geometria
        try:
            p = rb.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if p is not None and not p.IsReadOnly:
                p.Set(TAG + (u'|' + kind if kind else u''))
        except Exception:
            pass
        part = self.cfg.get('partition')
        if part:
            try:
                p = rb.get_Parameter(BuiltInParameter.NUMBER_PARTITION_PARAM)
                if p is not None and not p.IsReadOnly:
                    p.Set(part)
            except Exception:
                pass
        self.created.append(rb)

    # --------------------------------------------------------- previos
    def delete_previous(self):
        ids = List[ElementId]()
        for c in self.info['chain']:
            try:
                for rb in RebarHostData.GetRebarHostData(c['elem']).GetRebarsInHost():
                    p = rb.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                    s = p.AsString() if p is not None else None
                    if s and s.startswith(TAG):
                        ids.Add(rb.Id)
            except Exception:
                pass
        if ids.Count:
            self.doc.Delete(ids)
        return ids.Count

    # --------------------------------------------------------- longitudinales
    def _bar_pts(self, b, x, run=0):
        P = lambda s, xx, yy: self.P(s, xx, yy, run)
        db = b['bt']['dm']
        ox = bool(b.get('off_x'))               # desfase del traslape en horizontal (hacia el centro)
        cx = -1.0 if x > 0 else 1.0             # hacia el eje de la viga
        y = b['y'] + (0.0 if ox else (b.get('shift') or 0.0) * db)
        x = x + (cx * db if (ox and b.get('shift')) else 0.0)
        hin = b.get('hin') or (0.0, 0.0)
        pts = []
        if hin[0] > 0.01:
            pts.append(P(b['s0'], x + cx * max(hin[0] - db / 2.0, db), y))
        elif abs(b['leg0']) > 0.01:
            L0 = math.copysign(max(abs(b['leg0']) - db / 2.0, db), b['leg0'])
            pts.append(P(b['s0'], x, y + L0))
        pts.append(P(b['s0'], x, y))
        ck = b.get('crank')
        if ck and b['s0'] < ck[0] < b['s1']:
            # bayoneta de un diametro (hacia el interior) en la zona de traslape
            sc, dy = ck
            pts.append(P(max(sc - 6.0 * db, b['s0'] + 0.5 * db), x, y))
            if ox:
                pts.append(P(sc, x + cx * db, y))
                pts.append(P(b['s1'], x + cx * db, y))
            else:
                pts.append(P(sc, x, y + dy * db))
                pts.append(P(b['s1'], x, y + dy * db))
            return pts
        if run is None:                         # quiebres del eje (doblez en planta)
            for sb in self.axis.breaks(self._ft(b['s0']), self._ft(b['s1'])):
                pts.append(P(self._cm(sb), x, y))
        pts.append(P(b['s1'], x, y))
        if hin[1] > 0.01:
            pts.append(P(b['s1'], x + cx * max(hin[1] - db / 2.0, db), y))
        elif abs(b['leg1']) > 0.01:
            L1 = math.copysign(max(abs(b['leg1']) - db / 2.0, db), b['leg1'])
            pts.append(P(b['s1'], x, y + L1))
        return pts

    def make_long(self, b):
        doc = self.doc
        bt = self.bt_elems[b['bt']['name']]
        host = self.host_for(b['s0'], b['s1'])
        run = self.run_of(b['s0'], b['s1'])
        if run is None and (abs(b['leg0']) > 0.01 or abs(b['leg1']) > 0.01 or b.get('crank')):
            # con patas verticales no puede doblarse en planta (no seria plana): recta en su tramo. Las
            # piezas que pasan un quiebre solo por su traslape (sin apoyo) quedan rectas sin aviso
            a_, b_ = self._ft(b['s0']), self._ft(b['s1'])
            wide = [1 for lo, hi in self.axis.bends if hi - lo > 1e-6 and a_ < lo - 1e-6 and b_ > hi + 1e-6]
            run = self.axis.home(a_, b_)
            if wide and not self.bend_note:
                self.bend_note = True
                self.rep['notes'].append(u'Alguna varilla con gancho cruza un cambio de eje y quedó recta; '
                                         u'revise el apoyo donde cambia el eje.')
        # barra central con gancho horizontal, traslape desfasado en horizontal o doblez en planta: plano
        # horizontal y cada varilla por separado (los ganchos de los dos lados son simetricos, no una copia)
        flat = bool(b.get('off_x')) or any(v > 0.01 for v in (b.get('hin') or ())) or run is None
        norm = XYZ.BasisZ if flat else self.axis.normal(self.axis.runs[run][2])
        xs = sorted(b['xs'])
        n = len(xs)
        if n > 1 and G.is_uniform(xs) and not flat:
            rb = _create(doc, RebarStyle.Standard, bt, None, None, host, norm, self._bar_pts(b, xs[0], run),
                         RIGHT, RIGHT)
            self._layout_fixed(rb, n, (xs[-1] - xs[0]), xs[-1], 'x', run)
            self.tag(rb, b['kind'])
            self.rep['count'][b['kind']] += n
            return
        for x in xs:
            rb = _create(doc, RebarStyle.Standard, bt, None, None, host, norm, self._bar_pts(b, x, run),
                         RIGHT, RIGHT)
            self.tag(rb, b['kind'])
            self.rep['count'][b['kind']] += 1

    def _axis_disp(self, total, axis, run=0):
        o = self.P(0.0, 0.0, 0.0, run)
        return (self.P(0.0, total, 0.0, run) if axis == 'x' else self.P(total, 0.0, 0.0, run)).Subtract(o)

    def _layout_fixed(self, rb, n, length_cm, expect_last, axis, run=0):
        r = AR.set_layout(self.doc, rb, n, length_cm / FT, self._axis_disp(length_cm, axis, run))
        if r == AR.UNVERIFIED:
            self.rep['notes'].append(u'No se pudo verificar el lado de un conjunto de varillas; revíselo.')
        return r != AR.UNVERIFIED

    def _layout_spacing(self, rb, n, sp_cm, expect_last, axis, run=0):
        r = AR.set_layout(self.doc, rb, n, sp_cm / FT, self._axis_disp(sp_cm * (int(n) - 1), axis, run), True)
        if r == AR.UNVERIFIED:
            self.rep['notes'].append(u'No se pudo verificar el sentido de un conjunto de estribos; revíselo.')
        return r != AR.UNVERIFIED

    # --------------------------------------------------------- estribos
    def make_stirrups(self, st):
        doc = self.doc
        bt = self.bt_elems.get(st.get('bt') or self.cfg['stir_bt'])
        hk = self.hook_elems.get(self.cfg['stirrups'].get('hook'))
        poly = st.get('poly') or self.res['stir_poly']
        s0 = st['s0']
        s_last = s0 + (st['n'] - 1) * st['sp']
        run = self.axis.home(self._ft(s0), self._ft(s_last))       # estribos perpendiculares a su viga
        pts = [self.P(s0, x, y, run) for x, y in poly] + [self.P(s0, poly[0][0], poly[0][1], run)]
        host = self.host_for(s0, s_last)
        norm = self.axis.direction(self.axis.runs[run][2])
        if hk is None:
            rb = _create(doc, RebarStyle.StirrupTie, bt, None, None, host, norm, pts, RIGHT, RIGHT)
        elif self.st_orient is not None:
            o1, o2 = self.st_orient
            rb = _create(doc, RebarStyle.StirrupTie, bt, hk, hk, host, norm, pts, o1, o2)
        else:
            rb = None
            for o1, o2 in ((RIGHT, RIGHT), (LEFT, LEFT), (RIGHT, LEFT), (LEFT, RIGHT)):
                try:
                    cand = _create(doc, RebarStyle.StirrupTie, bt, hk, hk, host, norm, pts, o1, o2)
                except Exception:
                    continue
                if self._stir_ok(cand, poly):
                    rb = cand
                    self.st_orient = (o1, o2)
                    break
                doc.Delete(cand.Id)
            if rb is None:
                rb = _create(doc, RebarStyle.StirrupTie, bt, hk, hk, host, norm, pts, RIGHT, RIGHT)
                self.st_orient = (RIGHT, RIGHT)
                self.rep['notes'].append(u'No se pudo verificar hacia dónde quedan los ganchos de los '
                                         u'estribos; revíselos visualmente.')
        if st['n'] > 1 and st['sp'] > 0:
            self._layout_spacing(rb, st['n'], st['sp'], s_last, 's', run)
        self.tag(rb, 'stirrups')
        self.rep['count']['stirrups'] += int(st['n'])

    def _stir_ok(self, rb, poly):
        try:
            cc = _centerline(self.doc, rb)
            tips = [cc[0].GetEndPoint(0), cc[-1].GetEndPoint(1)] if len(cc) < 2 else \
                [_free_end(cc[0], cc[1]), _free_end(cc[-1], cc[-2])]
            for t in tips:
                x, y, _ = self.local(t)
                if not point_in_polygon(x, y, poly):
                    return False
            return True
        except Exception:
            return False

    def prepare_types(self):
        hk = self.hook_elems.get(self.cfg['stirrups'].get('hook'))
        names = set([self.cfg['stir_bt']] + [st.get('bt') for st in self.res.get('stirrups', [])])
        for nm in names:
            bt = self.bt_elems.get(nm)
            if bt is not None and hk is not None:
                try:
                    if not bt.GetHookPermission(hk.Id):
                        bt.SetHookPermission(hk.Id, True)
                except Exception:
                    pass

    # --------------------------------------------------------- vistas
    def make_views(self, templates, label):
        doc = self.doc
        v = self.cfg['views']
        out = []
        if not (v.get('elev') or v.get('cross')):
            return out
        vft = None
        for t in FilteredElementCollector(doc).OfClass(ViewFamilyType):
            try:
                if t.ViewFamily == ViewFamily.Section:
                    vft = t
                    break
            except Exception:
                pass
        if vft is None:
            self.rep['notes'].append(u'No hay un tipo de vista de sección en el modelo; no se crearon vistas.')
            return out
        m = self.model
        fr = self.fr
        if v.get('elev'):
            S0, S1 = m.extents()
            for b in self.res['bars']:
                S0 = min(S0, b['s0'])
                S1 = max(S1, b['s1'])
            mid = (S0 + S1) / 2.0
            half = (S1 - S0) / 2.0 + 40.0
            vs = self._section(vft, self.P(mid, 0, 0), fr.u, XYZ.BasisZ, fr.u.CrossProduct(XYZ.BasisZ),
                               half, m.h / 2.0 + 80.0, -(m.b / 2.0 + 15.0), m.b / 2.0 + 15.0,
                               u'Armado viga %s' % label, templates.get(v.get('elev_tpl')), 25)
            if vs is not None:
                out.append(vs)
        if v.get('cross'):
            for i in range(m.n_spans):
                fa, fb = m.span_faces(i)
                run = self.axis.home(self._ft(fa), self._ft(fb))
                mm = self.axis.runs[run][2]
                vs = self._section(vft, self.P((fa + fb) / 2.0, 0, 0, run), self.axis.normal(mm), XYZ.BasisZ,
                                   self.axis.direction(mm),
                                   m.b / 2.0 + 30.0, m.h / 2.0 + 30.0, -40.0, 0.0,
                                   u'Armado viga %s - T%d' % (label, i + 1), templates.get(v.get('cross_tpl')), 20)
                if vs is not None:
                    out.append(vs)
        return out

    def _existing_view(self, name):
        for v in FilteredElementCollector(self.doc).OfClass(ViewSection):
            try:
                if not v.IsTemplate and el_name(v) == name:
                    return v
            except Exception:
                pass
        return None

    def _section(self, vft, origin, bx, by, bz, hx_cm, hy_cm, z0_cm, z1_cm, name, tpl_id, scale):
        old = self._existing_view(name)
        if old is not None:
            self.rep['views'].append(name + u' (existente)')
            return old
        try:
            tr = Transform.Identity
            tr.Origin = origin
            tr.BasisX = bx
            tr.BasisY = by
            tr.BasisZ = bz
            bb = BoundingBoxXYZ()
            bb.Transform = tr
            bb.Min = XYZ(-hx_cm / FT, -hy_cm / FT, z0_cm / FT)
            bb.Max = XYZ(hx_cm / FT, hy_cm / FT, z1_cm / FT)
            vs = ViewSection.CreateSection(self.doc, vft.Id, bb)
        except Exception as ex:
            self.rep['notes'].append(u'No se pudo crear la vista "%s": %s' % (name, ex))
            return None
        try:
            vs.Scale = scale
        except Exception:
            pass
        if tpl_id is not None:
            try:
                vs.ViewTemplateId = tpl_id
            except Exception as ex:
                self.rep['notes'].append(u'No se pudo aplicar la plantilla a "%s": %s' % (name, ex))
        for k in range(1, 50):
            try:
                vs.Name = name if k == 1 else u'%s (%d)' % (name, k)
                break
            except Exception:
                continue
        self.rep['views'].append(el_name(vs))
        return vs


def build(doc, info, model, cfg, res, bt_elems, hook_elems, templates, replace, view3d, label):
    rep = {'count': {'top_main': 0, 'bot_main': 0, 'top_add': 0, 'bot_add': 0, 'side': 0, 'stirrups': 0},
           'deleted': 0, 'errors': [], 'notes': [], 'views': [], 'fatal': None, 'elements': 0}
    t = Transaction(doc, u'Armado de viga')
    t.Start()
    try:
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_Swallow())
        t.SetFailureHandlingOptions(opts)
    except Exception:
        pass
    try:
        b = Builder(doc, info, model, cfg, res, bt_elems, hook_elems, rep)
        if replace:
            rep['deleted'] = b.delete_previous()
        b.prepare_types()
        for bar in res['bars']:
            try:
                b.make_long(bar)
            except Exception as ex:
                rep['errors'].append(u'%s: %s' % (bar['label'], ex))
        if cfg['stirrups'].get('on'):
            for st in res['stirrups']:
                try:
                    b.make_stirrups(st)
                except Exception as ex:
                    rep['errors'].append(u'Estribos en s=%.2f m: %s' % (st['s0'] / 100.0, ex))
        views = b.make_views(templates, label)
        if view3d is not None:
            views = views + [view3d]
        for rb in b.created:
            for vw in views:
                try:
                    rb.SetUnobscuredInView(vw, True)
                except Exception:
                    pass
        rep['elements'] = len(b.created)
        # la configuracion queda guardada en la viga: al volver a seleccionarla se carga tal cual
        try:
            import bv_leer
            bv_leer.store_cfg(doc, info, cfg, b.created)
        except Exception:
            pass
        t.Commit()
    except Exception:
        rep['fatal'] = traceback.format_exc()
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
    return rep
