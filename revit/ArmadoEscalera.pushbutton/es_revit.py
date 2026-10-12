# -*- coding: utf-8 -*-
"""Lectura del perfil de escaleras / rampas / losas inclinadas y creacion de Rebar
(Revit 2024 - 2026)."""
from __future__ import division
import math
import traceback

import clr
from System.Collections.Generic import List

from Autodesk.Revit.DB import (XYZ, Line, Curve, Options, ViewDetailLevel, Solid, GeometryInstance, PlanarFace,
                               FilteredElementCollector, BuiltInParameter, BuiltInCategory, Transaction, Element,
                               ElementId, IFailuresPreprocessor, FailureSeverity, FailureProcessingResult)
from Autodesk.Revit.DB.Structure import (Rebar, RebarBarType, RebarStyle, RebarHookOrientation, RebarHostData,
                                         MultiplanarOption)
try:
    from Autodesk.Revit.DB import BooleanOperationsUtils, BooleanOperationsType, Plane
except Exception:            # pragma: no cover
    BooleanOperationsUtils = BooleanOperationsType = Plane = None

import es_geom as G
import armado_rebar as AR

FT = 30.48
TAG = u'ArmadoEscalera'
RIGHT = RebarHookOrientation.Right

HOST_ADVICE = (u'Revit no acepta este elemento como anfitrión de armadura.\n\n'
               u'Opciones: modele la escalera/rampa como losa inclinada (Suelo estructural) o como familia in situ '
               u'de categoría Suelos, Armazón estructural o Cimentación estructural, con material de concreto, '
               u'y vuelva a ejecutar la herramienta.')


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


def _mark(el):
    try:
        return el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK).AsString() or u''
    except Exception:
        return u''


def _type_name(doc, el):
    try:
        return el_name(doc.GetElement(el.GetTypeId()))
    except Exception:
        return u''


def is_candidate(el):
    if el is None or isinstance(el, Rebar):
        return False
    return getattr(el, 'Category', None) is not None


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


def _get_solids(el):
    opt = Options()
    opt.ComputeReferences = False
    opt.DetailLevel = ViewDetailLevel.Fine
    out = []
    try:
        geo = el.get_Geometry(opt)
        if geo is not None:
            _solids(geo, out)
    except Exception:
        pass
    return out


def _points(sols):
    pts = []
    for so in sols:
        for e in so.Edges:
            try:
                pts += list(e.Tessellate())
            except Exception:
                pass
    return pts


def _normal(f):
    try:
        return f.FaceNormal
    except Exception:
        return f.Normal


def _horiz(v):
    L = math.hypot(v.X, v.Y)
    return XYZ(v.X / L, v.Y / L, 0.0) if L > 1e-9 else None


class Frame(object):
    """x = subida (ux), y = ancho (uy), z vertical. Origen en (s_min, w_min, z_min). Pies."""

    def __init__(self, ux, uy, ox=0.0, oy=0.0, z0=0.0):
        self.ux, self.uy = ux, uy
        self.ox, self.oy, self.z0 = ox, oy, z0

    def ab(self, p):
        return p.X * self.ux.X + p.Y * self.ux.Y, p.X * self.uy.X + p.Y * self.uy.Y

    def local(self, p):
        a, b = self.ab(p)
        return ((a - self.ox) * FT, (b - self.oy) * FT, (p.Z - self.z0) * FT)

    def world(self, x, y, z):
        a, b = self.ox + x / FT, self.oy + y / FT
        return XYZ(self.ux.X * a + self.uy.X * b, self.ux.Y * a + self.uy.Y * b, self.z0 + z / FT)

    def vec(self, dx, dy, dz):
        return XYZ(self.ux.X * dx + self.uy.X * dy, self.ux.Y * dx + self.uy.Y * dy, dz)


def _run_dir(sols, pts):
    """Direccion horizontal de subida a partir de la cara inclinada mas grande."""
    best = None
    for so in sols:
        for f in so.Faces:
            if not isinstance(f, PlanarFace):
                continue
            n = _normal(f)
            if 0.15 < abs(n.Z) < 0.985:
                h = _horiz(n)
                if h is None:
                    continue
                u = h if n.Z < 0 else XYZ(-h.X, -h.Y, 0.0)
                a = getattr(f, 'Area', 0.0)
                if best is None or a > best[0]:
                    best = (a, u)
    if best is not None:
        return best[1]
    xs = [p.X for p in pts]
    ys = [p.Y for p in pts]
    return XYZ(1.0, 0.0, 0.0) if max(xs) - min(xs) >= max(ys) - min(ys) else XYZ(0.0, 1.0, 0.0)


def _chain(segs):
    segs = [list(s) for s in segs if len(s) >= 2]
    if not segs:
        return []
    out = segs.pop(0)
    while segs:
        end = out[-1]
        best = None
        for i, s in enumerate(segs):
            d0, d1 = s[0].DistanceTo(end), s[-1].DistanceTo(end)
            d = min(d0, d1)
            if best is None or d < best[0]:
                best = (d, i, d1 < d0)
        _, i, rev = best
        s = segs.pop(i)
        if rev:
            s = s[::-1]
        out += s[1:]
    return out


def _face_loops(face):
    loops = []
    for loop in face.EdgeLoops:
        segs = []
        for e in loop:
            try:
                segs.append(list(e.AsCurve().Tessellate()))
            except Exception:
                try:
                    segs.append(list(e.Tessellate()))
                except Exception:
                    pass
        pts = _chain(segs)
        if len(pts) >= 3:
            loops.append(pts)
    return loops


def _profile_by_cut(sols, fr, w_mid):
    if BooleanOperationsUtils is None:
        return None
    solids = [sols[0]]
    for so in sols[1:]:
        try:
            solids[0] = BooleanOperationsUtils.ExecuteBooleanOperation(solids[0], so, BooleanOperationsType.Union)
        except Exception:
            solids.append(so)
    origin = XYZ(fr.uy.X * w_mid, fr.uy.Y * w_mid, 0.0)
    try:
        plane = Plane.CreateByNormalAndOrigin(fr.uy, origin)
    except Exception:
        return None
    best = None
    for so in solids:
        try:
            half = BooleanOperationsUtils.CutWithHalfSpace(so, plane)
        except Exception:
            continue
        if half is None:
            continue
        for f in half.Faces:
            if not isinstance(f, PlanarFace):
                continue
            n = _normal(f)
            if n.X * fr.uy.X + n.Y * fr.uy.Y > -0.99:
                continue
            for lp in _face_loops(f):
                prof = [(fr.local(p)[0], fr.local(p)[2]) for p in lp]
                a = abs(G.signed_area(prof))
                if best is None or a > best[0]:
                    best = (a, prof)
    return best[1] if best else None


def _profile_by_face(sols, fr):
    best = None
    for so in sols:
        for f in so.Faces:
            if not isinstance(f, PlanarFace):
                continue
            n = _normal(f)
            if abs(n.X * fr.uy.X + n.Y * fr.uy.Y) < 0.99:
                continue
            for lp in _face_loops(f):
                prof = [(fr.local(p)[0], fr.local(p)[2]) for p in lp]
                a = abs(G.signed_area(prof))
                if best is None or a > best[0]:
                    best = (a, prof)
    return best[1] if best else None


def analyze(doc, el, rot=0, _flip=False):
    """Devuelve (info, None) o (None, error)."""
    try:
        if not RebarHostData.IsValidHost(el):
            return None, HOST_ADVICE
    except Exception:
        pass
    sols = _get_solids(el)
    if not sols:
        return None, u'el elemento no tiene geometría sólida'
    pts = _points(sols)
    if not pts:
        return None, u'sin geometría'
    u = _run_dir(sols, pts)
    if rot:
        u = XYZ(-u.Y, u.X, 0.0)
    if _flip:
        u = XYZ(-u.X, -u.Y, 0.0)
    fr = Frame(u, XYZ(-u.Y, u.X, 0.0))
    ab = [fr.ab(p) for p in pts]
    fr.ox = min(q[0] for q in ab)
    fr.oy = min(q[1] for q in ab)
    fr.z0 = min(p.Z for p in pts)
    w_max = max(q[1] for q in ab)
    W = (w_max - fr.oy) * FT
    if W < 10.0:
        return None, u'ancho insuficiente'
    source = 'cut'
    prof = _profile_by_cut(sols, fr, (fr.oy + w_max) / 2.0)
    if not prof:
        source = 'face'
        prof = _profile_by_face(sols, fr)
    if not prof:
        return None, u'no se pudo obtener el perfil longitudinal'
    try:
        model = G.StairModel(prof, round(W, 1), _mark(el))
    except Exception as ex:
        return None, u'perfil no válido (%s)' % ex
    # la subida debe ir hacia +x
    L = model.length
    left = [p[1] for p in model.poly if p[0] < L / 3.0]
    right = [p[1] for p in model.poly if p[0] > 2.0 * L / 3.0]
    if left and right and max(left) > max(right) + 1.0 and not _flip:
        return analyze(doc, el, rot, True)
    info = {'elem': el, 'model': model, 'frame': fr, 'mark': _mark(el), 'type_name': _type_name(doc, el),
            'id': eid_int(el.Id), 'source': source, 'rot': rot}
    return info, None


def describe(info):
    m = info['model']
    parts = [u'%s · %s' % (info['mark'] or u'Id %d' % info['id'], info['type_name']),
             u'ancho %s cm · L %s × H %s cm · garganta %s cm' % (G.fmt(m.W), G.fmt(m.length), G.fmt(m.height),
                                                                G.fmt(m.throat()))]
    if len(m.noses) >= 2:
        parts.append(u'%d escalones (huella %s, peralte %s)' % (len(m.noses), G.fmt(m.tread), G.fmt(m.riser)))
    if info.get('source') == 'face':
        parts.append(u'perfil tomado de la cara lateral')
    return u'\n'.join(parts)


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


KIND_REP = {'long_bot': 'inf', 'long_top': 'sup', 'tr_bot': 'rep', 'tr_top': 'rep', 'nose': 'nariz'}


class Builder(object):
    def __init__(self, doc, info, cfg, bt_elems, rep):
        self.doc = doc
        self.info = info
        self.fr = info['frame']
        self.cfg = cfg
        self.bt_elems = bt_elems
        self.rep = rep
        self.host = info['elem']
        self.created = []

    def tag(self, rb, count, kind):
        try:
            p = rb.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if p is not None and not p.IsReadOnly:
                p.Set(TAG)
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
        k = KIND_REP.get(kind, kind)
        self.rep['count'][k] = self.rep['count'].get(k, 0) + count

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

    def make_bar(self, b):
        bt = self.bt_elems[b['bt']['name']]
        pts = AR.clean_pts([self.fr.world(*q) for q in b['pts']])
        norm = self.fr.vec(*b['plane']).Normalize()
        rb = Rebar.CreateFromCurves(self.doc, RebarStyle.Standard, bt, None, None, self.host, norm, _curves(pts),
                                    RIGHT, RIGHT, True, True)
        n = int(b['n'])
        if n > 1:
            st = b['step']
            L = math.sqrt(st[0] ** 2 + st[1] ** 2 + st[2] ** 2) * (n - 1)
            disp = self.fr.vec(st[0] * (n - 1) / FT, st[1] * (n - 1) / FT, st[2] * (n - 1) / FT)
            if AR.set_layout(self.doc, rb, n, L / FT, disp) == AR.UNVERIFIED:
                self.rep['notes'].add(u'No se pudo verificar el sentido de algún conjunto; revíselo visualmente.')
        self.tag(rb, n, b['kind'])


def build(doc, info, cfg, res, bt_elems, replace, view3d):
    rep = {'count': {}, 'notes': set(), 'errors': [], 'deleted': 0, 'elements': 0, 'fatal': None}
    t = Transaction(doc, u'Armado de escalera / rampa')
    t.Start()
    try:
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_Swallow())
        t.SetFailureHandlingOptions(opts)
    except Exception:
        pass
    try:
        b = Builder(doc, info, cfg, bt_elems, rep)
        if replace:
            rep['deleted'] = b.delete_previous()
        for bar in res['bars']:
            try:
                b.make_bar(bar)
            except Exception as ex:
                rep['errors'].append(u'%s: %s' % (bar['kind'], ex))
        if view3d is not None:
            for rb in b.created:
                try:
                    rb.SetUnobscuredInView(view3d, True)
                except Exception:
                    pass
        for w in res['warnings']:
            rep['notes'].add(w)
        rep['elements'] = len(b.created)
        t.Commit()
    except Exception:
        rep['fatal'] = traceback.format_exc()
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
    return rep
