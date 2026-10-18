# -*- coding: utf-8 -*-
"""Lectura de muros (contorno con huecos) y creacion de Rebar (Revit 2024 - 2026)."""
from __future__ import division
import math
import traceback

import clr
from System.Collections.Generic import List

from Autodesk.Revit.DB import (XYZ, Line, Curve, Options, ViewDetailLevel, Solid, GeometryInstance, PlanarFace,
                               FilteredElementCollector, BuiltInParameter, BuiltInCategory, Transaction, Element,
                               ElementId, IFailuresPreprocessor, FailureSeverity, FailureProcessingResult, Level,
                               Wall, ViewFamilyType, ViewFamily, ViewSection, View, ViewType, BoundingBoxXYZ,
                               Transform)
from Autodesk.Revit.DB.Structure import (Rebar, RebarBarType, RebarHookType, RebarStyle, RebarHookOrientation,
                                         RebarHostData, MultiplanarOption)

import aw_geom as W
import armado_rebar as AR
import armado_leer as AL
import armado_continuidad as CT

FT = 30.48
TAG = u'ArmadoMuro'
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


def is_wall(el):
    if el is None or not isinstance(el, Wall):
        return False
    return isinstance(getattr(getattr(el, 'Location', None), 'Curve', None), Line)


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
    out, seen = [], set()
    for bt in FilteredElementCollector(doc).OfClass(RebarBarType):
        n = el_name(bt)
        if n in seen:
            continue
        seen.add(n)
        dm, dn = _bt_diams(bt)
        out.append({'name': n, 'dm': dm, 'dn': dn, 'elem': bt})
    out.sort(key=lambda d: (d['dn'], d['name']))
    return out


def get_stirrup_hooks(doc):
    out = []
    for hk in FilteredElementCollector(doc).OfClass(RebarHookType):
        try:
            if hk.Style != RebarStyle.StirrupTie:
                continue
            ang = math.degrees(hk.HookAngle)
        except Exception:
            continue
        out.append({'name': el_name(hk), 'angle': round(ang, 1), 'elem': hk})
    out.sort(key=lambda d: (0 if abs(d['angle'] - 135.0) < 1.0 else 1, d['angle'], d['name']))
    return out


def hook_length_table(bar_types, hooks):
    tab = {}
    for bt in bar_types:
        for hk in hooks:
            try:
                v = bt['elem'].GetHookLength(hk['elem'].Id) * FT
            except Exception:
                v = None
            tab[(bt['name'], hk['name'])] = round(v if v and v > 0.01 else max(6 * bt['dn'], 7.5), 1)
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


def _wall_solids(el):
    opt = Options()
    opt.ComputeReferences = False
    opt.DetailLevel = ViewDetailLevel.Fine
    geo = el.get_Geometry(opt)
    sols = []
    if geo is not None:
        _solids(geo, sols)
    return sols


class Frame(object):
    def __init__(self, O, u):
        self.O = O
        self.u = u
        self.v = XYZ.BasisZ.CrossProduct(u).Normalize()

    def s(self, p):
        return (p.X - self.O.X) * self.u.X + (p.Y - self.O.Y) * self.u.Y

    def l(self, p):
        return (p.X - self.O.X) * self.v.X + (p.Y - self.O.Y) * self.v.Y


def _loop_area(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        a += pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
    return abs(a) / 2.0


def _face_loops(face, fr, s_min, z_min):
    loops = []
    for loop in face.EdgeLoops:
        pts = []
        for e in loop:
            try:
                crv = e.AsCurveFollowingFace(face)
                tess = list(crv.Tessellate())
            except Exception:
                tess = list(e.Tessellate())
            for p in tess[:-1]:
                q = ((fr.s(p) - s_min) * FT, (p.Z - z_min) * FT)
                if not pts or abs(pts[-1][0] - q[0]) + abs(pts[-1][1] - q[1]) > 0.05:
                    pts.append(q)
        if len(pts) >= 3:
            loops.append(pts)
    loops.sort(key=lambda lp: -_loop_area(lp))
    return loops


def _level_name(doc, wall, bip):
    try:
        lv = doc.GetElement(wall.get_Parameter(bip).AsElementId())
        return el_name(lv) if lv is not None else u''
    except Exception:
        return u''


def _measure(sols, fr):
    """Extension (s, l, z) y contorno en alzado de los solidos del muro (datos simples, sin objetos de Revit)."""
    pts = []
    faces = []
    for so in sols:
        for e in so.Edges:
            try:
                pts += list(e.Tessellate())
            except Exception:
                pass
        for f in so.Faces:
            if isinstance(f, PlanarFace):
                try:
                    n = f.FaceNormal
                except Exception:
                    n = f.Normal
                if abs(n.X * fr.v.X + n.Y * fr.v.Y) > 0.99:
                    faces.append(f)
    if not pts:
        return None
    ss = [fr.s(p) for p in pts]
    ll = [fr.l(p) for p in pts]
    zz = [p.Z for p in pts]
    m = {'s_min': min(ss), 's_max': max(ss), 'l_min': min(ll), 'l_max': max(ll), 'z_min': min(zz), 'z_max': max(zz),
         'loops': None}
    if faces:
        face = max(faces, key=lambda f: f.Area)
        m['loops'] = _face_loops(face, fr, m['s_min'], m['z_min']) or None
    return m


def _measure_unjoined(doc, wall, fr):
    """Muro sin solido propio (embebido en otro elemento que lo corta por completo): se separa de los
    elementos unidos en una transaccion que se deshace, y se mide su geometria completa."""
    try:
        from Autodesk.Revit.DB import JoinGeometryUtils
        ids = list(JoinGeometryUtils.GetJoinedElements(doc, wall))
    except Exception:
        return None
    if not ids:
        return None
    sub = None
    try:
        if getattr(doc, 'IsModifiable', False):
            from Autodesk.Revit.DB import SubTransaction
            sub = SubTransaction(doc)
        else:
            sub = Transaction(doc, u'ArmadoMuro: medir muro embebido')
        sub.Start()
    except Exception:
        return None
    try:
        for i in ids:
            try:
                JoinGeometryUtils.UnjoinGeometry(doc, wall, doc.GetElement(i))
            except Exception:
                pass
        doc.Regenerate()
        return _measure(_wall_solids(wall), fr)
    except Exception:
        return None
    finally:
        try:
            sub.RollBack()
        except Exception:
            pass


def _bip(name):
    return getattr(BuiltInParameter, name, None)


def _pdouble(el, name):
    try:
        p = el.get_Parameter(_bip(name))
        return p.AsDouble() if p is not None else None
    except Exception:
        return None


def _measure_params(doc, wall, fr, crv):
    """Ultimo recurso: rectangulo con la linea de ubicacion, el espesor y las alturas del muro (sin huecos)."""
    try:
        lv = doc.GetElement(wall.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT).AsElementId())
        z0 = lv.ProjectElevation
    except Exception:
        z0 = min(crv.GetEndPoint(0).Z, crv.GetEndPoint(1).Z)
    z0 += _pdouble(wall, 'WALL_BASE_OFFSET') or 0.0
    h = _pdouble(wall, 'WALL_USER_HEIGHT_PARAM')
    if not h or h <= 0:
        return None
    t = wall.Width
    # linea de ubicacion -> eje del muro (0 eje, 1 eje del nucleo; 2 / 4 cara exterior; 3 / 5 cara interior)
    off = 0.0
    try:
        key = wall.get_Parameter(_bip('WALL_KEY_REF_PARAM')).AsInteger()
        o = wall.Orientation
        side = o.X * fr.v.X + o.Y * fr.v.Y
        if key in (2, 4):
            off = -side * t / 2.0
        elif key in (3, 5):
            off = side * t / 2.0
    except Exception:
        pass
    a, b = crv.GetEndPoint(0), crv.GetEndPoint(1)
    s0, s1 = sorted((fr.s(a), fr.s(b)))
    lc = fr.l(a) + off
    L = (s1 - s0) * FT
    H = h * FT
    return {'s_min': s0, 's_max': s1, 'l_min': lc - t / 2.0, 'l_max': lc + t / 2.0, 'z_min': z0, 'z_max': z0 + h,
            'loops': [[(0.0, 0.0), (L, 0.0), (L, H), (0.0, H)]]}


def _wall_measure(doc, w):
    """(marco, medidas) de otro muro recto, para ver si es transversal a un extremo."""
    crv = getattr(getattr(w, 'Location', None), 'Curve', None)
    if not isinstance(crv, Line):
        return None, None
    d = crv.Direction
    u = XYZ(d.X, d.Y, 0.0).Normalize()
    p0 = crv.GetEndPoint(0)
    fr = Frame(XYZ(p0.X, p0.Y, 0.0), u)
    m = None
    try:
        m = _measure(_wall_solids(w), fr)
    except Exception:
        m = None
    if m is None:
        m = _measure_params(doc, w, fr, crv)
    return fr, m


def end_walls(doc, wall, fr, m):
    """Muro transversal en cada extremo (T o L): {'L' / 'R': {'e': cm del extremo al pano lejano del otro muro,
    't', 'name', 'id'}}. El elemento de borde puede medirse desde ese pano lejano (detalle tipico)."""
    out = {}
    try:
        walls = [w for w in FilteredElementCollector(doc).OfClass(Wall)]
    except Exception:
        return out
    lc = (m['l_min'] + m['l_max']) / 2.0
    tol = 2.0 / FT
    for side, s_end, sg in (('L', m['s_min'], -1.0), ('R', m['s_max'], 1.0)):
        A0 = XYZ(fr.O.X + fr.u.X * s_end + fr.v.X * lc, fr.O.Y + fr.u.Y * s_end + fr.v.Y * lc, 0.0)
        d = XYZ(fr.u.X * sg, fr.u.Y * sg, 0.0)
        best = None
        for w in walls:
            try:
                if w is wall or eid_int(w.Id) == eid_int(wall.Id):
                    continue
            except Exception:
                continue
            crv = getattr(getattr(w, 'Location', None), 'Curve', None)
            if not isinstance(crv, Line):
                continue
            dd = crv.Direction
            n = math.hypot(dd.X, dd.Y)
            if n < 1e-9 or abs((dd.X * fr.u.X + dd.Y * fr.u.Y) / n) > 0.1:      # no perpendicular
                continue
            # descarte rapido: la linea del otro muro cerca del extremo
            a, b = crv.GetEndPoint(0), crv.GetEndPoint(1)
            ux, uy = (b.X - a.X), (b.Y - a.Y)
            L2 = ux * ux + uy * uy
            tt = max(0.0, min(1.0, ((A0.X - a.X) * ux + (A0.Y - a.Y) * uy) / L2)) if L2 > 1e-12 else 0.0
            if math.hypot(A0.X - (a.X + ux * tt), A0.Y - (a.Y + uy * tt)) > 4.0:
                continue
            fr2, m2 = _wall_measure(doc, w)
            if m2 is None:
                continue
            if min(m['z_max'], m2['z_max']) - max(m['z_min'], m2['z_min']) < 10.0 / FT:
                continue
            dv = d.X * fr2.v.X + d.Y * fr2.v.Y
            if abs(dv) < 0.9:
                continue
            l0 = fr2.l(A0)
            sig = sorted(((m2['l_min'] - l0) / dv, (m2['l_max'] - l0) / dv))
            s_near, s_far = sig
            if s_far <= tol / 4.0 or s_near > tol or s_near < -(m2['l_max'] - m2['l_min']) - tol:
                continue
            mid = XYZ(A0.X + d.X * (s_near + s_far) / 2.0, A0.Y + d.Y * (s_near + s_far) / 2.0, 0.0)
            s2 = fr2.s(mid)
            if not (m2['s_min'] - tol <= s2 <= m2['s_max'] + tol):
                continue
            if best is None or abs(s_near) < abs(best[0]):
                mk = u''
                try:
                    mk = w.get_Parameter(BuiltInParameter.ALL_MODEL_MARK).AsString() or u''
                except Exception:
                    pass
                best = (s_near, {'e': round(s_far * FT, 2), 't': round((m2['l_max'] - m2['l_min']) * FT, 2),
                                 'name': mk or u'Id %d' % eid_int(w.Id), 'id': eid_int(w.Id)})
        if best is not None:
            out[side] = best[1]
    return out


def above_walls(doc, wall, fr, m):
    """Muros apoyados sobre este (paralelos, con su base en la corona): su extension en las coordenadas (s, x) de
    este muro, en cm. Sirve para rematar las esperas que no caben en un muro de arriba mas delgado."""
    out = []
    try:
        walls = [w for w in FilteredElementCollector(doc).OfClass(Wall)]
    except Exception:
        return out
    lc = (m['l_min'] + m['l_max']) / 2.0
    tol_z = 5.0 / FT
    for w in walls:
        try:
            if w is wall or eid_int(w.Id) == eid_int(wall.Id):
                continue
        except Exception:
            continue
        crv = getattr(getattr(w, 'Location', None), 'Curve', None)
        if not isinstance(crv, Line):
            continue
        dd = crv.Direction
        n = math.hypot(dd.X, dd.Y)
        if n < 1e-9 or abs((dd.X * fr.u.X + dd.Y * fr.u.Y) / n) < 0.99:       # paralelo
            continue
        a, b = crv.GetEndPoint(0), crv.GetEndPoint(1)
        if abs(fr.l(a) - lc) > 3.0:                                           # cerca del eje en planta
            continue
        sa, sb = sorted((fr.s(a), fr.s(b)))
        if sb < m['s_min'] or sa > m['s_max']:
            continue
        try:
            m2 = _measure(_wall_solids(w), fr)
        except Exception:
            m2 = None
        if m2 is None:
            m2 = _measure_params(doc, w, fr, crv)
        if m2 is None or abs(m2['z_min'] - m['z_max']) > tol_z:
            continue
        s0, s1 = max(m2['s_min'], m['s_min']), min(m2['s_max'], m['s_max'])
        if (s1 - s0) * FT < 10.0:
            continue
        mk = u''
        try:
            mk = w.get_Parameter(BuiltInParameter.ALL_MODEL_MARK).AsString() or u''
        except Exception:
            pass
        out.append({'s0': round((s0 - m['s_min']) * FT, 2), 's1': round((s1 - m['s_min']) * FT, 2),
                    'x0': round((m2['l_min'] - lc) * FT, 2), 'x1': round((m2['l_max'] - lc) * FT, 2),
                    't': round((m2['l_max'] - m2['l_min']) * FT, 2), 'name': mk or u'Id %d' % eid_int(w.Id),
                    'id': eid_int(w.Id)})
    return out


def analyze_wall(doc, wall):
    """Devuelve (info, None) o (None, motivo)."""
    crv = getattr(getattr(wall, 'Location', None), 'Curve', None)
    if not isinstance(crv, Line):
        return None, u'muro curvo (no soportado)'
    try:
        if not RebarHostData.IsValidHost(wall):
            return None, u'no es un anfitrión válido de armadura (¿material de concreto / uso estructural?)'
    except Exception:
        pass
    d = crv.Direction
    u = XYZ(d.X, d.Y, 0.0).Normalize()
    if u.X < -1e-6 or (abs(u.X) < 1e-6 and u.Y < 0):
        u = u.Negate()
    p0 = crv.GetEndPoint(0)
    fr = Frame(XYZ(p0.X, p0.Y, 0.0), u)
    warn = []
    m = _measure(_wall_solids(wall), fr)
    if m is None:
        # sin solido propio (embebido / cortado por completo por otro elemento): el acero si se considera
        m = _measure_unjoined(doc, wall, fr)
        if m is not None:
            warn.append(u'sin geometría sólida propia (embebido en otro elemento): se midió separado de los '
                        u'elementos unidos')
        else:
            m = _measure_params(doc, wall, fr, crv)
            if m is not None:
                warn.append(u'sin geometría sólida propia (embebido en otro elemento): se usó la línea de '
                            u'ubicación, el espesor y las alturas del muro, sin huecos')
    if m is None:
        return None, u'sin geometría sólida (y no se pudieron leer línea de ubicación y alturas)'
    s_min, s_max, z_min, z_max = m['s_min'], m['s_max'], m['z_min'], m['z_max']
    try:
        t = wall.Width * FT
    except Exception:
        t = (m['l_max'] - m['l_min']) * FT
    L = (s_max - s_min) * FT
    H = (z_max - z_min) * FT
    loops = m['loops']
    levels = []
    for lv in FilteredElementCollector(doc).OfClass(Level):
        levels.append((el_name(lv), lv.ProjectElevation))
    levels.sort(key=lambda q: q[1])
    inner = [(z - z_min) * FT for n, z in levels if z_min + 0.01 < z < z_max - 0.01]
    view = [(n, (z - z_min) * FT) for n, z in levels if z_min - 4.0 < z < z_max + 260.0 / FT]
    mark = u''
    try:
        mark = wall.get_Parameter(BuiltInParameter.ALL_MODEL_MARK).AsString() or u''
    except Exception:
        pass
    try:
        tname = el_name(wall.WallType)
    except Exception:
        tname = u''
    info = {'elem': wall, 'frame': fr, 's_min': s_min, 'z_min': z_min, 'l_c': (m['l_min'] + m['l_max']) / 2.0,
            't': round(t, 2), 'L': round(L, 2), 'H': round(H, 2), 'loops': loops, 'inner': inner, 'levels_view': view,
            'base_name': _level_name(doc, wall, BuiltInParameter.WALL_BASE_CONSTRAINT),
            'top_name': _level_name(doc, wall, BuiltInParameter.WALL_HEIGHT_TYPE), 'mark': mark, 'type_name': tname,
            'id': eid_int(wall.Id), 'warn': warn}
    try:
        info['ends'] = end_walls(doc, wall, fr, m)
    except Exception:
        info['ends'] = {}
    try:
        info['above'] = above_walls(doc, wall, fr, m)
    except Exception:
        info['above'] = []
    # elementos de concreto arriba de la corona (continuidad de las esperas)
    try:
        lc_ = info['l_c']

        def to_local(p):
            return ((fr.s(p) - s_min) * FT, (fr.l(p) - lc_) * FT)

        info['top_parts'] = CT.parts_above(doc, wall, to_local, z_min, z_max)
    except Exception:
        info['top_parts'] = None
    return info, None


def corner_of(a, b, label_a=u'A', label_b=u'B'):
    """Esquina en L entre dos muros rectos: (cn, None) o (None, motivo). cn lleva de la planta de la
    esquina (origen en la esquina exterior, pa a lo largo de A, pb a lo largo de B; cm) a las
    coordenadas (s, x) de cada muro (ver aw_geom)."""
    fa, fb = a['frame'], b['frame']

    def axis(info):
        fr = info['frame']
        return XYZ(fr.O.X + fr.v.X * info['l_c'], fr.O.Y + fr.v.Y * info['l_c'], 0.0), fr.u

    pa_, ua = axis(a)
    pb_, ub = axis(b)
    cross = ua.X * ub.Y - ua.Y * ub.X
    if abs(cross) < 0.999:
        return None, u'los muros no son perpendiculares'
    dx, dy = pb_.X - pa_.X, pb_.Y - pa_.Y
    t = (dx * ub.Y - dy * ub.X) / cross
    I = XYZ(pa_.X + ua.X * t, pa_.Y + ua.Y * t, 0.0)

    def end(info, other):
        sI = (info['frame'].s(I) - info['s_min']) * FT
        tol = other['t'] + 15.0
        if abs(sI) <= tol:
            return 1.0
        if abs(sI - info['L']) <= tol:
            return -1.0
        return None

    dsa, dsb = end(a, b), end(b, a)
    if dsa is None or dsb is None:
        return None, u'no forman una esquina en L (los dos muros deben terminar en la esquina)'
    ad = XYZ(ua.X * dsa, ua.Y * dsa, 0.0)          # a lo largo de A, alejandose de la esquina
    bd = XYZ(ub.X * dsb, ub.Y * dsb, 0.0)
    O = XYZ(I.X - ad.X * b['t'] / 2.0 / FT - bd.X * a['t'] / 2.0 / FT,
            I.Y - ad.Y * b['t'] / 2.0 / FT - bd.Y * a['t'] / 2.0 / FT, 0.0)

    def sgn(v):
        return 1.0 if v >= 0 else -1.0

    cn = {'A': {'L': a['L'], 't': a['t'], 's0': (fa.s(O) - a['s_min']) * FT, 'ds': dsa,
                'xs': sgn(bd.X * fa.v.X + bd.Y * fa.v.Y), 'name': label_a},
          'B': {'L': b['L'], 't': b['t'], 's0': (fb.s(O) - b['s_min']) * FT, 'ds': dsb,
                'xs': sgn(ad.X * fb.v.X + ad.Y * fb.v.Y), 'name': label_b},
          # direcciones de A y B en planta (X, Y del modelo): la ventana dibuja la L como se ve en planta
          'plan': (ad.X, ad.Y, bd.X, bd.Y),
          # esquina exterior en planta (cm): para dibujar juntas las esquinas de una cadena de muros
          'O': (O.X * FT, O.Y * FT)}
    return cn, None


def chain_of(infos, labels):
    """Cadena de muros en L seguidos (L con 2, U o Z con 3, ...): (cs, orden, None) o (None, None, motivo).
    cs = {'walls': [...], 'corners': [esquina entre el muro k y el k+1]} (ver aw_geom)."""
    n = len(infos)
    if n < 2:
        return None, None, u'un solo muro'
    adj = dict((i, []) for i in range(n))
    why = u''
    for i in range(n):
        for j in range(i + 1, n):
            cn, w = corner_of(infos[i], infos[j])
            if cn is not None:
                adj[i].append(j)
                adj[j].append(i)
            elif n == 2:
                why = w
    if n == 2 and not adj[0]:
        return None, None, why
    if any(len(v) > 2 for v in adj.values()):
        return None, None, u'un muro llega a más de dos esquinas (solo cadenas en L, U, Z...)'
    ends = [i for i in range(n) if len(adj[i]) == 1]
    if len(ends) != 2:
        return None, None, u'los muros no forman una cadena de esquinas en L'
    # la cadena empieza siempre por el mismo muro (el de menor Id), sin importar en que orden se
    # seleccionaron: los brazos A / B y los extremos libres de la configuracion quedan siempre en el mismo
    # muro (antes, seleccionar en otro orden intercambiaba los brazos)
    order = [min(ends, key=lambda i: (infos[i].get('id', i), i))]
    while len(order) < n:
        nxt = [j for j in adj[order[-1]] if j not in order]
        if not nxt:
            return None, None, u'los muros no forman una sola cadena de esquinas en L'
        order.append(nxt[0])
    corners = []
    for k in range(n - 1):
        a, b = order[k], order[k + 1]
        cn, w = corner_of(infos[a], infos[b], labels[a], labels[b])
        if cn is None:
            return None, None, w
        corners.append(cn)
    # un muro intermedio llega a sus dos esquinas por extremos distintos
    for k in range(1, n - 1):
        if corners[k - 1]['B']['ds'] == corners[k]['A']['ds']:
            return None, None, u'%s llega a sus dos esquinas por el mismo extremo' % labels[order[k]]
    ox, oy = corners[0]['O']
    for cn in corners:
        cn['O'] = (cn['O'][0] - ox, cn['O'][1] - oy)
    walls = [{'L': infos[i]['L'], 't': infos[i]['t'], 'name': labels[i]} for i in order]
    return {'walls': walls, 'corners': corners}, order, None


def make_model(info):
    m = W.WallModel(info['t'], info['L'], info['H'], info['loops'], info['inner'], info['levels_view'],
                    info['base_name'], info['top_name'])
    m.ends = dict(info.get('ends') or {})          # muros transversales en los extremos
    m.above = list(info.get('above') or [])         # muros apoyados encima (esperas que no caben)
    m.top_parts = info.get('top_parts')             # elementos arriba (esperas sin continuidad)
    return m


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


def _create(doc, style, bt, hs, he, host, norm, pts, o1=RIGHT, o2=RIGHT):
    return Rebar.CreateFromCurves(doc, style, bt, hs, he, host, norm, _curves(pts), o1, o2, True, True)


def _uniform(vals, tol=0.1):
    if len(vals) < 3:
        return True
    d = vals[1] - vals[0]
    return all(abs((vals[i + 1] - vals[i]) - d) < tol for i in range(len(vals) - 1))


def _pip(x, y, pts):
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
        self.host = info['elem']
        self.created = []
        self.orient = {}

    def P(self, s, x, z):
        fr = self.fr
        ss = self.info['s_min'] + s / FT
        ll = self.info['l_c'] + x / FT
        return XYZ(fr.O.X + fr.u.X * ss + fr.v.X * ll, fr.O.Y + fr.u.Y * ss + fr.v.Y * ll,
                   self.info['z_min'] + z / FT)

    def local(self, p):
        return ((self.fr.s(p) - self.info['s_min']) * FT, (self.fr.l(p) - self.info['l_c']) * FT,
                (p.Z - self.info['z_min']) * FT)

    def tag(self, rb, count, kind):
        try:
            p = rb.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if p is not None and not p.IsReadOnly:
                p.Set(TAG + u'|' + kind)          # la funcion sirve para leer el armado despues (aw_leer)
        except Exception:
            pass
        part = self.cfg['gen'].get('partition')
        if part:
            try:
                p = rb.get_Parameter(BuiltInParameter.NUMBER_PARTITION_PARAM)
                if p is not None and not p.IsReadOnly:
                    p.Set(part)
            except Exception:
                pass
        self.created.append(rb)
        self.rep['count'][kind] = self.rep['count'].get(kind, 0) + count

    def delete_previous(self):
        ids = List[ElementId]()
        try:
            for rb in RebarHostData.GetRebarHostData(self.host).GetRebarsInHost():
                p = rb.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                s = p.AsString() if p is not None else None
                if s and s.startswith(TAG):
                    ids.Add(rb.Id)
        except Exception:
            pass
        if ids.Count:
            self.doc.Delete(ids)
        return ids.Count

    # ------------------------------------------------ conjuntos
    def _layout(self, rb, n, length_cm, expect_last, axis, spacing=False):
        """Las posiciones crecen a lo largo de axis ('s' o 'z'); expect_last: coordenada de la ultima."""
        total = length_cm * (int(n) - 1) if spacing else length_cm
        o = self.P(0.0, 0.0, 0.0)
        disp = (self.P(total, 0.0, 0.0) if axis == 's' else self.P(0.0, 0.0, total)).Subtract(o)
        if AR.set_layout(self.doc, rb, n, length_cm / FT, disp, spacing) == AR.UNVERIFIED:
            self.rep['notes'].add(u'No se pudo verificar el sentido de algún conjunto; revíselo visualmente.')

    # ------------------------------------------------ verticales
    def make_vertical(self, v):
        bt = self.bt_elems[v['bt']['name']]
        db = v['bt']['dm']
        x = v['x']
        L = max((v['leg_len'] or 0.0) - db / 2.0, db)
        L1 = max((v.get('leg1_len') or v['leg_len'] or 0.0) - db / 2.0, db)     # pata de arriba (remate)
        leg = v['leg0'] or v['leg1']
        along_s = bool(leg and abs(leg[0]) > 0.5)
        norm = self.fr.v if along_s else self.fr.u
        ss = sorted(v['ss'])

        ck = v.get('crank')
        sh = v.get('shift')
        if sh:
            x += sh[1] * db

        def pts_at(s):
            if sh:
                s += sh[0] * db
            out = []
            if v['leg0']:
                out.append(self.P(s + v['leg0'][0] * L, x + v['leg0'][1] * L, v['z0']))
            out.append(self.P(s, x, v['z0']))
            if ck and v['z0'] < ck[0] < v['z1']:
                # bayoneta de un diametro en la zona de traslape
                zc, (ds_, dx_) = ck
                out.append(self.P(s, x, max(zc - 6.0 * db, v['z0'] + 0.5 * db)))
                out.append(self.P(s + ds_ * db, x + dx_ * db, zc))
                out.append(self.P(s + ds_ * db, x + dx_ * db, v['z1']))
                return out
            out.append(self.P(s, x, v['z1']))
            if v['leg1']:
                out.append(self.P(s + v['leg1'][0] * L1, x + v['leg1'][1] * L1, v['z1']))
            return out

        kind = {'bnd': 'bnd', 'web': 'web_v', 'op': 'op'}[v['kind']]
        if len(ss) > 1 and not along_s and _uniform(ss):
            rb = _create(self.doc, RebarStyle.Standard, bt, None, None, self.host, norm, pts_at(ss[0]))
            self._layout(rb, len(ss), ss[-1] - ss[0], ss[-1], 's')
            self.tag(rb, len(ss), kind)
            return
        for s in ss:
            rb = _create(self.doc, RebarStyle.Standard, bt, None, None, self.host, norm, pts_at(s))
            self.tag(rb, 1, kind)

    # ------------------------------------------------ horizontales
    def make_horizontal(self, h):
        bt = self.bt_elems[h['bt']['name']]
        db = h['bt']['dm']
        x = h['x']
        L = max((h['leg_len'] or 0.0) - db / 2.0, db)
        zs = sorted(h['zs'])

        ck = h.get('crank')
        sh = h.get('shift')

        def pts_at(z):
            if sh:
                z += sh[1] * db
            out = []
            if h['leg0']:
                out.append(self.P(h['s0'], x + h['leg0'][1] * L, z))
            out.append(self.P(h['s0'], x, z))
            if ck and h['s0'] < ck[0] < h['s1']:
                sc, dx_ = ck
                out.append(self.P(max(sc - 6.0 * db, h['s0'] + 0.5 * db), x, z))
                out.append(self.P(sc, x + dx_ * db, z))
                out.append(self.P(h['s1'], x + dx_ * db, z))
                return out
            out.append(self.P(h['s1'], x, z))
            if h['leg1']:
                out.append(self.P(h['s1'], x + h['leg1'][1] * L, z))
            return out

        kind = 'op' if h['kind'] == 'op' else 'web_h'
        rb = _create(self.doc, RebarStyle.Standard, bt, None, None, self.host, XYZ.BasisZ, pts_at(zs[0]))
        if len(zs) > 1:
            self._layout(rb, len(zs), zs[-1] - zs[0], zs[-1], 'z')
        self.tag(rb, len(zs), kind)

    def make_u(self, u):
        bt = self.bt_elems[u['bt']['name']]
        zs = sorted(u['zs'])
        s, d, leg = u['s'], u['dir'], u['leg']
        pts = [self.P(s + d * leg, u['x1'], zs[0]), self.P(s, u['x1'], zs[0]),
               self.P(s, u['x2'], zs[0]), self.P(s + d * leg, u['x2'], zs[0])]
        rb = _create(self.doc, RebarStyle.Standard, bt, None, None, self.host, XYZ.BasisZ, pts)
        if len(zs) > 1:
            self._layout(rb, len(zs), zs[-1] - zs[0], zs[-1], 'z')
        self.tag(rb, len(zs), 'u')

    def make_diag(self, dg):
        bt = self.bt_elems[dg['bt']['name']]
        pts = [self.P(dg['p0'][0], dg['x'], dg['p0'][1]), self.P(dg['p1'][0], dg['x'], dg['p1'][1])]
        rb = _create(self.doc, RebarStyle.Standard, bt, None, None, self.host, self.fr.v, pts)
        self.tag(rb, 1, 'op')

    # ------------------------------------------------ estribos de borde
    def prepare_types(self):
        st = self.cfg['bstir']
        bt = self.bt_elems.get(st['bt'])
        hk = self.hook_elems.get(st.get('hook'))
        if bt is None or hk is None:
            return
        try:
            if not bt.GetHookPermission(hk.Id):
                bt.SetHookPermission(hk.Id, True)
        except Exception:
            pass
        if st.get('hook_custom'):
            try:
                bt.SetAutoCalcHookLengths(hk.Id, False)
                bt.SetHookLength(hk.Id, st['hook_custom'] / FT)
                self.rep['notes'].add(u'Se fijó la longitud del gancho "%s" en %s cm para el tipo "%s" (afecta al modelo).'
                                      % (el_name(hk), st['hook_custom'], el_name(bt)))
            except Exception as ex:
                self.rep['notes'].add(u'No se pudo fijar la longitud del gancho: %s' % ex)

    def _tips_ok(self, rb, st):
        try:
            self.doc.Regenerate()
            cc = list(rb.GetCenterlineCurves(False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, 0))
            tips = [cc[0].GetEndPoint(0), cc[-1].GetEndPoint(1)] if len(cc) < 2 else \
                [_free_end(cc[0], cc[1]), _free_end(cc[-1], cc[-2])]
            loc = [self.local(t)[:2] for t in tips]
            pts = st['pts']
            if st['closed']:
                return all(_pip(s, x, pts) for s, x in loc)
            (a0, b0), (a1, b1) = pts[0], pts[-1]
            side = [(a1 - a0) * (x - b0) - (b1 - b0) * (s - a0) for s, x in loc]
            return side[0] * side[1] > 0
        except Exception:
            return False

    def make_stirrups(self, st):
        doc = self.doc
        bt = self.bt_elems.get(self.cfg['bstir']['bt'])
        hk = self.hook_elems.get(self.cfg['bstir'].get('hook'))
        z0 = st['z0']
        pts = [self.P(s, x, z0) for s, x in st['pts']]
        if st['closed']:
            pts.append(pts[0])
        key = (st['zone'], tuple((round(a, 1), round(b, 1)) for a, b in st['pts']))
        if hk is None:
            rb = _create(doc, RebarStyle.StirrupTie, bt, None, None, self.host, XYZ.BasisZ, pts)
        elif key in self.orient:
            o1, o2 = self.orient[key]
            rb = _create(doc, RebarStyle.StirrupTie, bt, hk, hk, self.host, XYZ.BasisZ, pts, o1, o2)
        else:
            rb = None
            for o1, o2 in ((RIGHT, RIGHT), (LEFT, LEFT), (RIGHT, LEFT), (LEFT, RIGHT)):
                try:
                    cand = _create(doc, RebarStyle.StirrupTie, bt, hk, hk, self.host, XYZ.BasisZ, pts, o1, o2)
                except Exception:
                    continue
                if self._tips_ok(cand, st):
                    rb = cand
                    self.orient[key] = (o1, o2)
                    break
                doc.Delete(cand.Id)
            if rb is None:
                rb = _create(doc, RebarStyle.StirrupTie, bt, hk, hk, self.host, XYZ.BasisZ, pts)
                self.orient[key] = (RIGHT, RIGHT)
                self.rep['notes'].add(u'No se pudo verificar el lado de los ganchos de algún estribo; revíselo.')
        if st['n'] > 1 and st['sp'] > 0:
            self._layout(rb, st['n'], st['sp'], z0 + (st['n'] - 1) * st['sp'], 'z', spacing=True)
        self.tag(rb, st['n'], 'st')

    # ------------------------------------------------ vista
    def make_view(self, templates, label):
        g = self.cfg['gen']
        if not g.get('elev'):
            return None
        name = u'Armado muro %s' % label
        for vw in FilteredElementCollector(self.doc).OfClass(ViewSection):
            try:
                if not vw.IsTemplate and el_name(vw) == name:
                    self.rep['views'].append(name + u' (existente)')
                    return vw
            except Exception:
                pass
        vft = None
        for t in FilteredElementCollector(self.doc).OfClass(ViewFamilyType):
            try:
                if t.ViewFamily == ViewFamily.Section:
                    vft = t
                    break
            except Exception:
                pass
        if vft is None:
            self.rep['notes'].add(u'No hay tipo de vista de sección; no se creó la vista.')
            return None
        m = self.model
        fr = self.fr
        try:
            tr = Transform.Identity
            tr.Origin = self.P(m.L / 2.0, 0.0, m.H / 2.0)
            tr.BasisX = fr.u
            tr.BasisY = XYZ.BasisZ
            tr.BasisZ = fr.u.CrossProduct(XYZ.BasisZ)
            bb = BoundingBoxXYZ()
            bb.Transform = tr
            ext = max(g['ext_bot'], g['ext_top'], 0.0)
            bb.Min = XYZ(-(m.L / 2.0 + 50.0) / FT, -(m.H / 2.0 + 60.0 + ext) / FT, -(m.t / 2.0 + 15.0) / FT)
            bb.Max = XYZ((m.L / 2.0 + 50.0) / FT, (m.H / 2.0 + 60.0 + ext) / FT, (m.t / 2.0 + 15.0) / FT)
            vs = ViewSection.CreateSection(self.doc, vft.Id, bb)
        except Exception as ex:
            self.rep['notes'].add(u'No se pudo crear la vista: %s' % ex)
            return None
        try:
            vs.Scale = 50
        except Exception:
            pass
        tpl = templates.get(g.get('elev_tpl'))
        if tpl is not None:
            try:
                vs.ViewTemplateId = tpl
            except Exception:
                pass
        for k in range(1, 50):
            try:
                vs.Name = name if k == 1 else u'%s (%d)' % (name, k)
                break
            except Exception:
                continue
        self.rep['views'].append(el_name(vs))
        return vs


def build(doc, items, cfg, bt_elems, hook_elems, templates, replace, view3d, chain=False):
    """items: [(info, model, res, label)]. Todo en una transaccion.
    replace: False, True (borra el armado creado antes con esta herramienta) o 'all' (borra todas las varillas
    del muro: tambien las hechas a mano o con otra herramienta). chain: los muros forman una cadena en esquina
    (la configuracion se guarda en todos ellos como un conjunto)."""
    rep = {'walls': [], 'notes': set(), 'views': [], 'fatal': None, 'count': {}}
    created = []
    t = Transaction(doc, u'Armado de muro')
    t.Start()
    try:
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_Swallow())
        t.SetFailureHandlingOptions(opts)
    except Exception:
        pass
    try:
        for info, model, res, label in items:
            wrep = {'id': info['id'], 'eid': info['elem'].Id, 'label': label, 'deleted': 0, 'errors': [],
                    'count': {}, 'elements': 0}
            sub = {'notes': rep['notes'], 'views': rep['views'], 'count': wrep['count']}
            b = Builder(doc, info, model, cfg, res, bt_elems, hook_elems, sub)
            if replace == 'all':
                wrep['deleted'] = AL.delete_all(doc, [info['elem']])
            elif replace:
                wrep['deleted'] = b.delete_previous()
            b.prepare_types()
            for v in res['v']:
                try:
                    b.make_vertical(v)
                except Exception as ex:
                    wrep['errors'].append(u'Vertical %s en s=%s: %s' % (v['kind'], [round(s) for s in v['ss']][:3], ex))
            for h in res['h']:
                try:
                    b.make_horizontal(h)
                except Exception as ex:
                    wrep['errors'].append(u'Horizontal en z=%s: %s' % ([round(z) for z in h['zs']][:3], ex))
            for u in res['u']:
                try:
                    b.make_u(u)
                except Exception as ex:
                    wrep['errors'].append(u'Horquilla en s=%.0f: %s' % (u['s'], ex))
            for dg in res['diag']:
                try:
                    b.make_diag(dg)
                except Exception as ex:
                    wrep['errors'].append(u'Diagonal: %s' % ex)
            if cfg['bstir'].get('on'):
                for st in res['st']:
                    try:
                        b.make_stirrups(st)
                    except Exception as ex:
                        wrep['errors'].append(u'Estribo %s z=%.0f: %s' % (st['zone'], st['z0'], ex))
            vw = b.make_view(templates, label)
            views = [x for x in (vw, view3d) if x is not None]
            for rb in b.created:
                for x in views:
                    try:
                        rb.SetUnobscuredInView(x, True)
                    except Exception:
                        pass
            wrep['elements'] = len(b.created)
            created.append((info, b.created))
            for w in res['warnings']:
                rep['notes'].add(u'%s: %s' % (label, w))
            rep['walls'].append(wrep)
        # configuracion con que se creo, para leerla tal cual despues (aw_leer)
        try:
            import aw_leer
            if chain:
                aw_leer.store_cfg([i for i, c in created], cfg, [rb for i, c in created for rb in c])
            else:
                for i, c in created:
                    aw_leer.store_cfg([i], cfg, c)
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
