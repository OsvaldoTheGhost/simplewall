# -*- coding: utf-8 -*-
"""Armado de trabes desde Excel (Revit). Usa el motor de Armado Viga (bv_geom / bv_revit) sin abrir su
ventana: cada fila de la tabla se convierte en la misma configuracion que se capturaria a mano y se crea
el armado en cada nivel indicado.

Flujo:
  1. Generar plantilla: lee las trabes de un nivel (familia 'Trabes N+x.xx'), las analiza como lo hace
     Armado Viga (apoyos, tramos, secciones) y escribe un Excel con una fila por tramo.
  2. Se llena el armado con el plano (Niveles: N2-N4 para repetirlo en esos niveles).
  3. Revisar: calcula todo sin crear nada y muestra el reporte.
  4. Aplicar: crea el armado de todas las filas (todo se puede deshacer con un solo Ctrl+Z)."""
from __future__ import division, print_function

import os
import re
import traceback

from pyrevit import forms, script
from Autodesk.Revit.DB import (FilteredElementCollector, BuiltInCategory, BuiltInParameter, Level,
                               Transaction, TransactionGroup, StorageType, UnitUtils, UnitTypeId,
                               AssemblyInstance, AssemblyType, ElementId, Grid, Line)
from System.Collections.Generic import List

import bv_geom as G
import bv_revit as R
import armado_leer as AL
import vx_core as C
import vx_xlsx as X

TITLE = u'Armado de trabes desde Excel'


class SafeProgress(object):
    """Barra de progreso de pyRevit que nunca tumba el proceso.
    Si la ventana falla (p. ej. ZeroDivisionError en proc_screen_scalefactor
    con Revit minimizado o al cambiar de pantalla) se sigue sin barra."""

    def __init__(self, title):
        self.title = title
        self.pb = None
        self.dead = False

    def __enter__(self):
        try:
            self.pb = forms.ProgressBar(title=self.title, cancellable=True)
            self.pb.__enter__()
        except Exception:
            self.pb = None
            self.dead = True
        return self

    @property
    def cancelled(self):
        if self.dead or self.pb is None:
            return False
        try:
            return bool(self.pb.cancelled)
        except Exception:
            return False

    def update_progress(self, value, max_value):
        if self.dead or self.pb is None:
            return
        try:
            self.pb.update_progress(value, max_value)
        except Exception:
            self.dead = True
            try:
                self.pb.__exit__(None, None, None)
            except Exception:
                pass

    def __exit__(self, *exc):
        if self.pb is not None:
            try:
                self.pb.__exit__(None, None, None)
            except Exception:
                pass
        return False
SHEET = u'Armado'
SHEET_CFG = u'Configuracion'
SHEET_HELP = u'Instrucciones'


# ================================================================== modelo
def _m(ft):
    return UnitUtils.ConvertFromInternalUnits(ft, UnitTypeId.Meters)


def get_levels(doc):
    lv = sorted(FilteredElementCollector(doc).OfClass(Level), key=lambda l: l.Elevation)
    return [(l.Name, round(_m(l.Elevation), 3), l) for l in lv]


def type_name(el):
    try:
        return R.el_name(el.Symbol)
    except Exception:
        return u''


def family_name(el):
    try:
        return el.Symbol.FamilyName
    except Exception:
        return u''


class BeamIndex(object):
    """Trabes del modelo por (familia, tipo) y por (nivel de referencia, tipo)."""

    def __init__(self, doc):
        self.by_fam = {}
        self.by_lvl = {}
        self.by_mark = {}            # (familia, trabe del plano) -> trabes de todas sus secciones
        self.by_mark_lvl = {}
        self.families = {}
        col = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StructuralFraming) \
            .WhereElementIsNotElementType()
        for b in col:
            if not R.is_beam(b):
                continue
            fam, tn = family_name(b), type_name(b)
            if not tn:
                continue
            self.families.setdefault(C.fold(fam), fam)
            self.by_fam.setdefault((C.fold(fam), C.norm_beam(tn)), []).append(b)
            mk = C.norm_beam(C.beam_mark(tn))
            self.by_mark.setdefault((C.fold(fam), mk), []).append(b)
            try:
                lid = R.eid_int(b.get_Parameter(BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM).AsElementId())
            except Exception:
                lid = None
            if lid is not None:
                self.by_lvl.setdefault((lid, C.norm_beam(tn)), []).append(b)
                self.by_mark_lvl.setdefault((lid, mk), []).append(b)

    def find(self, fam, level, beam):
        """Trabes de un tipo ('T-5 Seccion 1') o, si el nombre es la trabe del plano sin seccion ('T-5', 'EJE-B'),
        de todas sus secciones: se arman como una sola viga (los bastones pasan el cambio de seccion)."""
        k = C.norm_beam(beam)
        if C.is_mark(beam):
            hit = self.by_mark.get((C.fold(fam), k))
            if hit:
                return hit, u'familia'
            if C.fold(fam) not in self.families:
                hit = self.by_mark_lvl.get((R.eid_int(level.Id), k))
                if hit:
                    return hit, u'nivel'
        hit = self.by_fam.get((C.fold(fam), k))
        if hit:
            return hit, u'familia'
        if C.fold(fam) not in self.families:
            hit = self.by_lvl.get((R.eid_int(level.Id), k))
            if hit:
                return hit, u'nivel'
        return [], None

    def marks_of(self, fam):
        """{trabe del plano: [trabes de todas sus secciones]} de una familia."""
        out = {}
        for tn, els in self.types_of(fam).items():
            out.setdefault(C.beam_mark(tn), []).extend(els)
        return out

    def types_of(self, fam):
        """{tipo: [trabes]} de una familia."""
        out = {}
        f = C.fold(fam)
        for (ff, _k), els in self.by_fam.items():
            if ff != f:
                continue
            for e in els:
                out.setdefault(type_name(e), []).append(e)
        return out


GAP_FT = 1.5 / 0.3048            # dos trabes del mismo tipo a mas de 1.5 m son vigas distintas


def _ends(b):
    c = b.Location.Curve
    return c.GetEndPoint(0), c.GetEndPoint(1)


def _near(a, b):
    """Las dos trabes se tocan o quedan a menos de GAP_FT (extremo con extremo o encimadas en planta)."""
    try:
        pa, pb = _ends(a), _ends(b)
    except Exception:
        return True
    for p in pa:
        for q in pb:
            if ((p.X - q.X) ** 2 + (p.Y - q.Y) ** 2) ** 0.5 <= GAP_FT and abs(p.Z - q.Z) < 3.0:
                return True
    # una dentro de la otra (colineales encimadas)
    for (p0, p1), (q0, q1) in ((pa, pb), (pb, pa)):
        dx, dy = p1.X - p0.X, p1.Y - p0.Y
        L2 = dx * dx + dy * dy
        if L2 < 1e-9:
            continue
        for q in (q0, q1):
            t = ((q.X - p0.X) * dx + (q.Y - p0.Y) * dy) / L2
            if 0.0 <= t <= 1.0:
                d = abs((q.X - p0.X) * dy - (q.Y - p0.Y) * dx) / L2 ** 0.5
                if d < 0.2:
                    return True
    return False


def chains(doc, beams):
    """Separa las trabes de un tipo en vigas continuas: [(info, errores)] (info None si no se pudo).
    Primero agrupa las que se tocan (las de un mismo tipo pueden estar en lugares distintos de la planta) y
    luego deja que Armado Viga arme cada grupo; lo que no entra en una cadena se analiza aparte."""
    groups = []
    for b in beams:
        hit = [g for g in groups if any(_near(b, x) for x in g)]
        merged_g = [b]
        for g in hit:
            merged_g += g
            groups.remove(g)
        groups.append(merged_g)
    out = []
    for g in groups:
        rest = list(g)
        while rest:
            try:
                info, errs = R.analyze_chain(doc, rest)
            except Exception as ex:
                info, errs = None, [(rest[0], u'%s' % ex)]
            if info is None:
                out.append((None, errs))
                break
            ids = set(R.eid_int(c['elem'].Id) for c in info['chain'])
            out.append((info, errs))
            nxt = [b for b in rest if R.eid_int(b.Id) not in ids]
            if len(nxt) == len(rest):
                break
            rest = nxt
    # orden estable: por la posicion del primer elemento
    def key(ie):
        info = ie[0]
        if info is None:
            return (1, 0.0, 0.0)
        p = _ends(info['chain'][0]['elem'])[0]
        return (0, round(p.X, 1), round(p.Y, 1))
    out.sort(key=key)
    return out


def grid_lines(doc):
    """[(clave, nombre, p (XY), d (XY unitario))] de las reticulas rectas del modelo."""
    out = []
    for g in FilteredElementCollector(doc).OfClass(Grid):
        try:
            c = g.Curve
            if not isinstance(c, Line):
                continue
            a, b = c.GetEndPoint(0), c.GetEndPoint(1)
            dx, dy = b.X - a.X, b.Y - a.Y
            n = (dx * dx + dy * dy) ** 0.5
            if n < 1e-6:
                continue
            nm = R.el_name(g)
            out.append((C.grid_key(nm), nm, (a.X, a.Y), (dx / n, dy / n)))
        except Exception:
            pass
    return out


def chain_grids(info, grids, tol_ft=1.0):
    """Ejes que cruza la viga (en orden a lo largo de ella): [(posicion, clave, nombre)].
    Ejes de inicio y fin = los mas cercanos a cada extremo."""
    pts = []
    for c in info['chain']:
        try:
            a, b = _ends(c['elem'])
            pts += [(a.X, a.Y), (b.X, b.Y)]
        except Exception:
            pass
    if len(pts) < 2:
        return [], None, None
    best = None
    for p in pts:
        for q in pts:
            d = ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5
            if best is None or d > best[0]:
                best = (d, p, q)
    L, P, Q = best
    if L < 1e-6:
        return [], None, None
    ux, uy = (Q[0] - P[0]) / L, (Q[1] - P[1]) / L
    hits = []
    for key, nm, g0, gd in grids:
        cr = ux * gd[1] - uy * gd[0]
        if abs(cr) < 0.5:                           # paralelo a la viga
            continue
        # interseccion de la recta de la viga con la reticula: P + t u
        t = ((g0[0] - P[0]) * gd[1] - (g0[1] - P[1]) * gd[0]) / cr
        if -tol_ft <= t <= L + tol_ft:
            hits.append((t, key, nm))
    hits.sort()
    s_ = min(hits, key=lambda h: abs(h[0])) if hits else None
    e_ = min(hits, key=lambda h: abs(h[0] - L)) if hits else None
    return hits, s_, e_


def chain_label(info, grids):
    """'K-H': ejes de los extremos de la viga (para elegirla en el Excel)."""
    hits, s_, e_ = chain_grids(info, grids)
    if not s_ or not e_ or s_[1] == e_[1]:
        return u''
    return u'%s-%s' % (C.grid_key(s_[2]).upper(), C.grid_key(e_[2]).upper())


def pick_chains(chs, sel, grids, claimed=()):
    """Vigas separadas de una trabe que corresponden al selector del Excel ('K-H', 'viga 2' o vacio).
    Sin selector: las que ningun otro renglon de la misma trabe eligio. Devuelve ([(indice, (info, errs))], error)."""
    idx = list(enumerate(chs))
    if not sel:
        return [x for x in idx if x[0] not in claimed], None
    tok = C.selector_tokens(sel)
    if isinstance(tok, int):
        if 1 <= tok <= len(chs):
            return [idx[tok - 1]], None
        return [], u'la trabe tiene %d viga(s); no hay "viga %d"' % (len(chs), tok)
    scored = []
    for i, (info, errs) in idx:
        if info is None:
            continue
        hits, s_, e_ = chain_grids(info, grids)
        keys = set(h[1] for h in hits)
        if not all(t in keys for t in tok):
            continue
        ends = set(x[1] for x in (s_, e_) if x)
        scored.append((0 if ends == set(tok) else 1, i))
    if not scored:
        labels = [chain_label(info, grids) for info, _ in chs if info is not None]
        return [], u'ninguna viga de esta trabe cruza los ejes "%s" (vigas: %s)' % (sel, u', '.join(l or u'?' for l in labels))
    best = min(sc for sc, _ in scored)
    return [idx[i] for sc, i in scored if sc == best], None


def clean_tool_rebar(doc, elems):
    """Borra el acero que hizo la herramienta en esas trabes (todas las de la viga completa). Se usa antes de
    armar una viga por partes ([S..] / [T..]): asi no queda acero de corridas anteriores (p. ej. la viga armada
    unida) en trabes que ninguna parte vuelve a armar o que cambiaron de parte."""
    ids = List[ElementId]()
    try:
        for rb in AL.hosted(elems):
            if AL.comment(rb).startswith(R.TAG):
                ids.Add(rb.Id)
    except Exception:
        return 0
    if not ids.Count:
        return 0
    t = Transaction(doc, u'Limpiar armado previo de la viga')
    t.Start()
    try:
        doc.Delete(ids)
        t.Commit()
    except Exception:
        t.RollBack()
        return 0
    return ids.Count


def make_model(info):
    """Igual que la ventana de Armado Viga (bv_main.Session.load)."""
    model = G.BeamModel(info['b'], info['h'], info['L'], info['supports'], parts=info.get('parts'),
                        bends=info.get('bends'), end_parts=info.get('end_parts'), vkinks=info.get('vkinks'))
    fr = info.get('frame')
    model.slope = abs(fr.angle) if getattr(fr, 'sloped', False) else 0.0
    model.slopes = [round(a, 1) for a in fr.angles] if getattr(fr, 'sloped', False) else []
    model.profile = None
    return model


def node_txt(model, ni):
    n = model.nodes[ni]
    if n.free:
        return u'extremo libre'
    if n.kind == G.WALL_UNDER:
        return u'%s (muro bajo la viga)' % n.name
    if n.kind == u'cambio':
        return u'%s (cambio de sección)' % n.name
    return u'%s (%s)' % (n.name, n.kind)


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s or u'')]


# ================================================================== plantilla
def _help_rows():
    rows = [[u'Cómo llenar la hoja "Armado"', u'']]
    rows += [
        [u'1', u'Cada fila es un tramo (entre dos apoyos) de una trabe. La plantilla ya trae los tramos que el '
               u'botón detecta en el modelo: apoyos, longitud libre y sección (columnas grises, solo informativas).'],
        [u'2', u'Niveles: N2 = "Nivel 2". Para repetir el mismo armado en varios niveles escriba N2-N4 (o N2,N3,N5) '
               u'en todas las filas de esa trabe. También acepta la elevación: +7.55.'],
        [u'3', u'Trabe: la trabe del plano (EJE-K, T-5): se arman juntas todas sus secciones (T-5 Seccion 1, 2...) '
               u'como una sola viga y los bastones pasan el cambio de sección. Si hay varias vigas separadas con la '
               u'misma trabe, cada una lleva entre paréntesis los ejes de sus extremos: "EJE-10 (K-H)", "T-2 (1\'-2)"; '
               u'también "T-2 (viga 2)". Sin paréntesis = todas las vigas que no tengan su propia fila. '
               u'Si en el plano las secciones de una viga van separadas, arme cada parte aparte con corchetes: '
               u'"T-5 (viga 2) [S1, S2]" y "T-5 (viga 2) [S3]" (tramos numerados desde 1 en cada parte). '
               u'Si es un solo tipo, por tramos de la plantilla: "EJE-J [T1-2]" y "EJE-J [T3]". '
               u'También acepta el tipo de Revit (T-5 Seccion 1) para armar solo esa sección.'],
        [u'4', u'Tramo: 1, 2, 3... en el sentido en que la plantilla los lista. Una fila con Tramo = "todos" '
               u'sirve de valor por defecto para los tramos que dejen la celda vacía.'],
        [u'5', u'Sup. / Inf. corrido: 3#4. Si es igual en todos los tramos queda corrido de extremo a extremo con '
               u'traslapes; si cambia por tramo, cada tramo lleva su corrido anclado en los apoyos.'],
        [u'6', u'Bastones en apoyo: "1#4 100/100" = 1 varilla #4, 100 cm a la izquierda y 100 cm a la derecha del '
               u'EJE del apoyo (como las cotas del plano; en un muro bajo la viga, desde el paño del muro). "3#6 190/100". Un solo número = igual a los dos lados. '
               u'Varias capas: "3#6 190/100 + 2#6 150". El mismo apoyo puede escribirse en el tramo de cada lado; '
               u'si es igual cuenta una vez.'],
        [u'7', u'Ref. centro: "2#4 130" = arranca y termina a 130 cm del eje de cada apoyo (del paño si es muro bajo la viga). "2#4 130/150" para '
               u'distancias distintas. Un valor negativo entra en el muro: "2#4 -100/-100" = desde 100 cm dentro del muro de un lado hasta 100 cm dentro del otro.'],
        [u'8', u'Estribos: "E#3 11@10 @20 11@10" = 11 estribos @10 desde el apoyo inicial, @20 al centro y '
               u'11@10 desde el final. Si no pone los del final se repiten los del inicio; "-" = sin grupo de ese lado (p. ej. "E#3 11@10 @20 -" junto a una viga que cruza). "E#3 @20" = @20 '
               u'en todo el tramo. "No" = tramo sin estribos.'],
        [u'9', u'Estribo sobre muro (tramos achurados del plano, muro bajo la viga): "@20" o "No". Si se deja '
               u'vacío se usa el de la hoja Configuracion.'],
        [u'10', u'Piel: "#3@30" (separación máxima vertical) o "1#3" (filas por cara). Vacío o "No" = sin piel.'],
        [u'11', u'Filas sin ningún dato de armado se ignoran. Las celdas de Niveles y Trabe vacías toman las de la '
                u'fila anterior.'],
        [u'12', u'Primero use "Revisar" (no crea nada); luego "Aplicar". Todo lo que crea "Aplicar" se deshace con '
                u'un solo Ctrl+Z.'],
        [u'13', u'Montajes: al aplicar se crea un montaje por trabe del plano ("Trabe T-4 Nivel +16.10") con sus trabes '
                u'anfitrionas (todas sus secciones) y el acero creado. Trabes iguales en otra línea: "Trabe T-2.1 Nivel +16.10". '
                u'Los montajes que ya tenían esas trabes se desensamblan antes. Se apaga en Configuracion › Crear montajes = No.'],
    ]
    return rows


def generate_template(doc):
    levels = get_levels(doc)
    idx = BeamIndex(doc)
    pattern = C.SETTINGS[C.SETTING_KEYS.index('family')][2]
    opts = []
    for name, z, lv in levels:
        fam = C.family_for(pattern, name, z)
        n = len(idx.types_of(fam))
        if n:
            opts.append((u'%s  (%s)  ·  %s  ·  %d tipos' % (name, C.elev_txt(z), fam, n), name, z, lv, fam))
    if not opts:
        forms.alert(u'No encontré familias de trabes por nivel ("Trabes N+x.xx").', title=TITLE)
        return
    sel = forms.SelectFromList.show([o[0] for o in opts], title=u'Niveles para la plantilla',
                                    button_name=u'Generar plantilla', multiselect=True)
    if not sel:
        return
    chosen = [o for o in opts if o[0] in sel]
    path = forms.save_file(file_ext='xlsx', default_name=u'Armado trabes %s' %
                           u' '.join(C.level_label(o[1]) for o in chosen))
    if not path:
        return
    rows = [[c[1] for c in C.COLUMNS]]
    grids = grid_lines(doc)
    problems = []
    total = 0
    with SafeProgress(u'Analizando trabes ({value} de {max_value})') as pb:
        work = []
        for o in chosen:
            for tn, els in sorted(idx.marks_of(o[4]).items(), key=lambda kv: natural_key(kv[0])):
                work.append((o, tn, els))
        for k, (o, tn, els) in enumerate(work):
            if pb.cancelled:
                break
            pb.update_progress(k + 1, len(work))
            lab = C.level_label(o[1])
            chs = chains(doc, els)
            good = [(i, e) for i, e in chs if i is not None]
            bad = [e for i, e in chs if i is None]
            if not good:
                errs = [x for e in bad for x in e]
                problems.append(u'%s %s: %s' % (lab, tn, u'; '.join(u'%s' % e for _, e in errs)))
                rows.append([lab, tn, u'', u'', u'', u'', u'', u'', u'', u'', u'', u'', u'', u'', u'', u'', u'',
                             u'', u'No se pudo analizar: ' + u'; '.join(u'%s' % e for _, e in errs)])
                continue
            labels = [chain_label(i, grids) for i, _ in good]
            for gi, (info, _e) in enumerate(good):
                model = make_model(info)
                notes = []
                # nombre de la viga en el Excel: la trabe del plano y, si hay varias, los ejes de sus extremos
                name = tn
                if len(good) > 1:
                    lb = labels[gi]
                    if not lb or labels.count(lb) > 1:
                        lb = u'viga %d' % (gi + 1)
                    name = u'%s (%s)' % (tn, lb)
                    if gi == 0:
                        notes.append(u'%d vigas separadas con esta trabe: cada una con sus filas (el paréntesis dice '
                                     u'los ejes de sus extremos)' % len(good))
                tys = []
                for c in info['chain']:
                    t_ = type_name(c['elem'])
                    if t_ not in tys:
                        tys.append(t_)
                if len(tys) > 1:
                    notes.append(u'secciones: %s (se arman juntas; los bastones pasan el cambio de sección)'
                                 % u' + '.join(tys))
                if bad and gi == 0:
                    notes.append(u'%d trabe(s) no se pudieron analizar' % sum(len(e) for e in bad))
                note = u'; '.join(notes)
                for i in range(model.n_spans):
                    fa, fb = model.span_faces(i)
                    sc = model.sec(i)
                    rows.append([lab, name, i + 1, node_txt(model, i), node_txt(model, i + 1),
                                 round((fb - fa) / 100.0, 2), u'%sx%s' % (G.fcm_(round(sc['b'], 1)), G.fcm_(round(sc['h'], 1))),
                                 u'', u'', u'', u'', u'', u'', u'', u'', u'', u'', u'', note if i == 0 else u''])
                    total += 1
    cfg_rows = [[u'Parámetro', u'Valor', u'Notas']] + [[s[1], s[2], s[3]] for s in C.SETTINGS]
    X.write_book(path, [
        (SHEET, rows, {'widths': [c[2] for c in C.COLUMNS], 'grey_cols': [3, 4, 5, 6], 'filter': True}),
        (SHEET_CFG, cfg_rows, {'widths': [34, 18, 60]}),
        (SHEET_HELP, _help_rows(), {'widths': [6, 120], 'note_style': True, 'freeze': False}),
    ])
    msg = u'Plantilla guardada con %d tramos:\n%s' % (total, path)
    if problems:
        msg += u'\n\nTrabes que no se pudieron analizar (%d):\n- ' % len(problems) + u'\n- '.join(problems[:12])
    if forms.alert(msg + u'\n\n¿Abrir el archivo?', title=TITLE, yes=True, no=True):
        try:
            os.startfile(path)
        except Exception:
            pass


# ================================================================== lectura del Excel
def read_table(path):
    book = X.read_sheets(path)
    order = book.get('__order__') or []
    name = next((n for n in order if C.fold(n) == C.fold(SHEET)), order[0] if order else None)
    if name is None:
        raise ValueError(u'El archivo no tiene hojas.')
    data = book[name]
    heads = dict((C.fold(c[1]), c[0]) for c in C.COLUMNS)
    hdr_row, cmap = None, {}
    for r, row in enumerate(data[:15]):
        m = {}
        for j, v in enumerate(row):
            k = heads.get(C.fold(v))
            if k:
                m[k] = j
        if 'trabe' in m and 'niveles' in m:
            hdr_row, cmap = r, m
            break
    if hdr_row is None:
        raise ValueError(u'No encontré los encabezados "Niveles" y "Trabe" en la hoja "%s".' % name)
    rows = []
    for r in range(hdr_row + 1, len(data)):
        row = data[r]
        d = {'_row': r + 1}
        for k, j in cmap.items():
            d[k] = C.clean(row[j]) if j < len(row) else u''
        if any(d.get(k) for k in C.COL_KEYS):
            rows.append(d)
    pairs = []
    cname = next((n for n in order if C.fold(n) in (C.fold(SHEET_CFG), u'configuración')), None)
    if cname:
        for row in book[cname]:
            if len(row) >= 2 and C.clean(row[0]):
                pairs.append((row[0], row[1]))
    return rows, C.settings_from(pairs), name


def load_base_cfg(name, bts_list):
    if not name:
        return {}, None
    root = G.config_dir()
    want = C.fold(name[:-5] if name.lower().endswith('.json') else name)
    for d, _sub, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith('.json') and C.fold(fn[:-5]) == want:
                data, diams = G.load_cfg(os.path.join(d, fn))
                G.remap_bar_names(data, diams, bts_list)
                for k in ('top_add', 'bot_add', 'top_spans', 'bot_spans', 'side', 'top_zones', 'bot_zones'):
                    data.pop(k, None)
                st = data.get('stirrups') or {}
                for k in ('groups', 'spans', 'manual', 'node_over'):
                    st.pop(k, None)
                return data, None
    return {}, u'no encontré la configuración base "%s" en %s' % (name, root)


# ================================================================== proceso
def set_level_param(doc, rebars, pname, value, level):
    n = 0
    for rb in rebars:
        p = rb.LookupParameter(pname)
        if p is None or p.IsReadOnly:
            continue
        try:
            if p.StorageType == StorageType.String:
                p.Set(value)
            elif p.StorageType == StorageType.ElementId:
                p.Set(level.Id)
            else:
                continue
            n += 1
        except Exception:
            pass
    return n


def run(doc, uidoc, apply_mode):
    path = forms.pick_file(file_ext='xlsx')
    if not path:
        return
    try:
        rows, settings, sheet = read_table(path)
    except Exception as ex:
        forms.alert(u'No se pudo leer el Excel:\n%s' % ex, title=TITLE)
        return
    groups = C.group_rows(rows)
    if not groups:
        forms.alert(u'La hoja "%s" no tiene filas con armado.' % sheet, title=TITLE)
        return
    bts_list = R.get_bar_types(doc)
    if not bts_list:
        forms.alert(u'El modelo no tiene tipos de varilla.', title=TITLE)
        return
    bts = dict((b['name'], b) for b in bts_list)
    hooks = R.get_stirrup_hooks(doc)
    hook_len = R.hook_length_table(bts_list, hooks)
    base_cfg, base_err = load_base_cfg(settings.get('base'), bts_list)
    levels = get_levels(doc)
    idx = BeamIndex(doc)
    replace = C.is_yes(settings.get('replace') or u'Sí')
    pname = C.clean(settings.get('param'))
    pfmt = settings.get('param_fmt') or u'NIVEL {elev}'

    # ---------------- plan: (grupo, nivel, trabes)
    plan, report, fatal = [], [], []
    if base_err:
        fatal.append(u'Configuración: %s' % base_err)
    grids = grid_lines(doc)
    # selectores usados por cada trabe ('EJE-10 (K-H)'): las filas sin parentesis arman las demas vigas
    sels = {}
    for lv_txt, tb, grows in groups:
        nm, sel = C.split_selector(C.split_sections(tb)[0])
        if sel:
            sels.setdefault((C.fold(lv_txt), C.norm_beam(nm)), []).append(sel)
    for lv_txt, tb, grows in groups:
        tb_full = tb
        spans_sel = C.split_spans_sel(tb)
        tb, secs = C.split_sections(tb)
        tb, sel = C.split_selector(tb)
        lvs, errs = C.resolve_levels(lv_txt, [(n, z) for n, z, _ in levels])
        r0 = grows[0].get('_row')
        for e in errs:
            report.append({'nivel': lv_txt, 'trabe': tb, 'estado': u'Error', 'errores': [u'fila %s: %s' % (r0, e)]})
        for name, z in lvs:
            level = [l for n, zz, l in levels if n == name][0]
            fam = C.family_for(settings.get('family'), name, z)
            beams, how = idx.find(fam, level, tb)
            if not beams:
                report.append({'nivel': C.level_label(name), 'trabe': tb, 'estado': u'Error',
                               'errores': [u'fila %s: no hay trabes tipo "%s" en la familia "%s"' % (r0, tb, fam)]})
                continue
            others = [] if sel else sels.get((C.fold(lv_txt), C.norm_beam(tb)), [])
            plan.append((tb, grows, name, z, level, fam, beams, sel, others, tb_full, secs, spans_sel))
    if not plan:
        _print_report(report, fatal, apply_mode, path)
        return
    if apply_mode:
        msg = (u'Se armarán %d trabes (%d filas de Excel en %d niveles).\n\n' %
               (len(plan), sum(len(p[1]) for p in plan), len(set(p[2] for p in plan))))
        msg += (u'El armado que la herramienta ya había hecho en esas trabes se reemplaza.' if replace else
                u'El armado previo de esas trabes NO se borra (Reemplazar armado previo = No).')
        msg += u'\n\nTodo se puede deshacer con un solo Ctrl+Z. ¿Continuar?'
        if not forms.alert(msg, title=TITLE, yes=True, no=True):
            return

    bt_elems = dict((d['name'], d['elem']) for d in bts_list)
    hook_elems = dict((d['name'], d['elem']) for d in hooks)
    templates = R.get_section_templates(doc)
    tg = None
    if apply_mode:
        tg = TransactionGroup(doc, TITLE)
        tg.Start()
    cleaned = set()                 # trabes ya limpiadas en esta corrida (vigas armadas por partes)
    try:
        with SafeProgress(u'Armando trabes ({value} de {max_value})') as pb:
            for k, (tb, grows, name, z, level, fam, beams, sel, others, tb_full, secs, spans_sel) in enumerate(plan):
                if pb.cancelled:
                    fatal.append(u'Cancelado por el usuario después de %d trabes.' % k)
                    break
                pb.update_progress(k + 1, len(plan))
                chs = chains(doc, beams)
                claimed = set()
                for o in others:
                    for i, _x in (pick_chains(chs, o, grids)[0] or []):
                        claimed.add(i)
                picked, perr = pick_chains(chs, sel, grids, claimed)
                if perr:
                    report.append({'nivel': C.level_label(name), 'trabe': tb_full, 'estado': u'Error',
                                   'errores': [u'fila %s: %s' % (grows[0].get('_row'), perr)]})
                    continue
                for ci, (info, errs) in picked:
                    n_clean = 0
                    if (secs or spans_sel) and apply_mode and replace and info is not None:
                        full = [c['elem'] for c in info['chain'] if R.eid_int(c['elem'].Id) not in cleaned]
                        if full:
                            n_clean = clean_tool_rebar(doc, full)
                            cleaned.update(R.eid_int(e.Id) for e in full)
                    lb = chain_label(info, grids) if info is not None else u''
                    tag = (u' (%s)' % (lb or u'viga %d' % (ci + 1))) if len(chs) > 1 else u''
                    if secs and info is not None:
                        # solo esas secciones, armadas como una viga aparte
                        sub = [c['elem'] for c in info['chain'] if C.section_no(type_name(c['elem'])) in secs]
                        tag += u' [%s]' % u', '.join(u'S%d' % n for n in secs)
                        if not sub:
                            report.append({'nivel': C.level_label(name), 'trabe': tb_full, 'estado': u'Error',
                                           'errores': [u'fila %s: la viga no tiene secciones %s' %
                                                       (grows[0].get('_row'), u', '.join(u'%d' % n for n in secs))]})
                            continue
                        try:
                            info, errs = R.analyze_chain(doc, sub)
                        except Exception as ex:
                            info, errs = None, [(sub[0], u'%s' % ex)]
                    if spans_sel and info is not None:
                        # solo las trabes de esos tramos (por la posicion de cada trabe en la viga)
                        m0 = make_model(info)
                        ns = m0.n_spans
                        bad_idx = [i + 1 for i in spans_sel if i >= ns]
                        if bad_idx:
                            report.append({'nivel': C.level_label(name), 'trabe': tb_full, 'estado': u'Error',
                                           'errores': [u'fila %s: la viga tiene %d tramos; no hay tramo %s' %
                                                       (grows[0].get('_row'), ns, u', '.join(u'%d' % i for i in bad_idx))]})
                            continue
                        # cada trabe pertenece a UNA sola parte (ver C.part_of_elements)
                        a0, b0 = min(spans_sel), max(spans_sel)
                        parts = info.get('parts') or []
                        rng = [(q[0], q[1]) for q in parts]
                        idx_in, idx_x = C.part_of_elements(rng, [(n.s0, n.s1) for n in m0.nodes], a0, b0)
                        sub = [info['chain'][i]['elem'] for i in idx_in]
                        cross = [R.eid_int(info['chain'][i]['elem'].Id) for i in idx_x]
                        tag += u' [T%s]' % u','.join(u'%d' % (i + 1) for i in spans_sel)
                        if not sub or cross:
                            report.append({'nivel': C.level_label(name), 'trabe': tb_full, 'estado': u'Error',
                                           'errores': [u'fila %s: %s' % (grows[0].get('_row'),
                                                       (u'la trabe Id %s pasa de esos tramos a otros: divídala en '
                                                        u'Revit en el apoyo para armar cada parte aparte'
                                                        % u', '.join(u'%d' % x for x in cross)) if cross else
                                                       u'ninguna trabe en esos tramos')]})
                            continue
                        try:
                            info, errs = R.analyze_chain(doc, sub)
                        except Exception as ex:
                            info, errs = None, [(sub[0], u'%s' % ex)]
                    rec_ = _one(doc, tb, grows, name, z, level, beams, info, errs, tag, settings, base_cfg,
                                bts_list, bts, hooks, hook_len, bt_elems, hook_elems, templates, replace,
                                apply_mode, pname, pfmt)
                    if n_clean:
                        rec_['avisos'].insert(0, u'se borraron %d elementos de armado previo de toda la viga '
                                                 u'(corridas anteriores) antes de armar por partes' % n_clean)
                    report.append(rec_)
        if tg is not None:
            if any(r['estado'] == u'Creado' for r in report):
                if not C.is_no(settings.get('montaje') or u'Sí'):
                    try:
                        make_assemblies(doc, report, settings.get('montaje_fmt'), fatal)
                    except Exception:
                        fatal.append(u'Montajes: ' + traceback.format_exc())
                tg.Assimilate()
            else:
                tg.RollBack()
    except Exception:
        fatal.append(traceback.format_exc())
        if tg is not None and tg.HasStarted() and not tg.HasEnded():
            tg.RollBack()
    _print_report(report, fatal, apply_mode, path)


def _one(doc, tb, grows, name, z, level, beams, info, errs, tag, settings, base_cfg, bts_list, bts, hooks,
         hook_len, bt_elems, hook_elems, templates, replace, apply_mode, pname, pfmt):
    lab = C.level_label(name)
    rec = {'nivel': lab, 'trabe': (tb if C.is_mark(tb) else (type_name(beams[0]) or tb)) + tag, 'estado': u'', 'tramos': 0, 'kg': 0.0,
           'errores': [], 'avisos': [], 'creados': 0, 'borrados': 0,
           'montaje': C.assembly_name(settings.get('montaje_fmt'), C.beam_mark(type_name(beams[0]) or tb), name, z)
           if not C.is_no(settings.get('montaje') or u'Sí') else u''}
    if info is None:
        rec['estado'] = u'Error'
        rec['errores'] += [u'Id %d: %s' % (R.eid_int(b.Id), e) for b, e in errs]
        return rec
    if tag:
        rec['avisos'].append(u'viga con Ids %s' % u', '.join(u'%d' % R.eid_int(c['elem'].Id) for c in info['chain']))
    rec['avisos'] += [u'%s' % w for w in info.get('warns') or []]
    model = make_model(info)
    rec['tramos'] = model.n_spans
    base, per, errs = C.split_spans(grows, model.n_spans)
    rec['errores'] += errs
    if not per and base is None:
        rec['errores'].append(u'ninguna fila válida para esta trabe')
    missing = [i + 1 for i in range(model.n_spans) if i not in per]
    if missing and base is None:
        rec['avisos'].append(u'tramo(s) sin fila en el Excel: %s (quedan sin armado propio)'
                             % u', '.join(u'%d' % i for i in missing))
    # los apoyos de la plantilla deben seguir siendo los mismos
    for i, r in per.items():
        for key, ni in (('apoyo_i', i), ('apoyo_f', i + 1)):
            want = C.clean(r.get(key))
            if want and C.fold(want) != C.fold(node_txt(model, ni)):
                rec['avisos'].append(u'fila %s: el %s era "%s" y en el modelo ahora es "%s"; revise que los '
                                     u'tramos coincidan' % (r.get('_row'), u'apoyo inicial' if key == 'apoyo_i'
                                                            else u'apoyo final', want, node_txt(model, ni)))
    if rec['errores']:
        rec['estado'] = u'Error'
        return rec
    try:
        cfg, warns, errs = C.build_cfg(G, model, bts_list, hooks, settings, base_cfg, base, per)
        rec['avisos'] += warns
        rec['errores'] += errs
        if errs:
            rec['estado'] = u'Error'
            return rec
        cfg = G.fit_cfg(cfg, model, bts_list)
        res = G.compute(model, cfg, bts)
        off = set(C.stirrups_off_spans(cfg))
        if off:
            res['stirrups'] = [s for s in res['stirrups'] if not (s.get('span') in off and s.get('zone') in ('conf', 'mid'))]
        rec['avisos'] += [u'%s' % w for w in res.get('warnings') or []]
        hk = cfg['stirrups'].get('hook')
        w, n_st = G.summary(res, bts, dict((nm, hook_len.get((nm, hk)) or 0.0) for nm in bts))
        rec['kg'] = sum(w.values())
        rec['n_bars'] = sum(len(b['xs']) for b in res['bars'])
        rec['n_st'] = n_st
        if not res['bars'] and not res['stirrups']:
            rec['estado'] = u'Error'
            rec['errores'].append(u'la configuración no produce varillas')
            return rec
    except Exception:
        rec['estado'] = u'Error'
        rec['errores'].append(traceback.format_exc())
        return rec
    if not apply_mode:
        rec['estado'] = u'OK'
        return rec
    label = u'%s %s' % (lab, rec['trabe'])
    rep = R.build(doc, info, model, cfg, res, bt_elems, hook_elems, templates, replace, None, label)
    if rep['fatal']:
        rec['estado'] = u'Error'
        rec['errores'].append(u'no se creó nada:\n' + rep['fatal'])
        return rec
    rec['estado'] = u'Creado'
    rec['_chain'] = [c['elem'] for c in info['chain']]
    rec['_level'] = (name, z)
    rec['creados'] = rep['elements']
    rec['borrados'] = rep['deleted']
    rec['errores'] += rep['errors']
    rec['avisos'] += rep['notes']
    if pname:
        try:
            own = [rb for rb in AL.hosted([c['elem'] for c in info['chain']])
                   if AL.comment(rb).startswith(R.TAG)]
            value = pfmt.replace(u'{elev}', C.elev_txt(z)).replace(u'{nivel}', name)
            t = Transaction(doc, u'Parámetro de nivel en varillas')
            t.Start()
            n = set_level_param(doc, own, pname, value, level)
            t.Commit()
            if own and not n:
                rec['avisos'].append(u'las varillas no tienen el parámetro "%s" (o es de solo lectura)' % pname)
        except Exception as ex:
            rec['avisos'].append(u'no se pudo llenar el parámetro "%s": %s' % (pname, ex))
    return rec


# ================================================================== montajes
def _line(elems):
    """(punto, direccion) en planta de una viga (sus trabes anfitrionas)."""
    pts = []
    for e in elems:
        try:
            a, b = _ends(e)
            pts += [a, b]
        except Exception:
            pass
    if len(pts) < 2:
        return None
    best = None
    for p in pts:
        for q in pts:
            d = ((p.X - q.X) ** 2 + (p.Y - q.Y) ** 2) ** 0.5
            if best is None or d > best[0]:
                best = (d, p, q)
    d, p, q = best
    if d < 1e-6:
        return None
    return (p.X, p.Y, (q.X - p.X) / d, (q.Y - p.Y) / d)


def _same_line(l1, l2, tol=1.6):
    """Misma linea en planta (paralelas y a menos de ~50 cm una de otra)."""
    if l1 is None or l2 is None:
        return False
    x, y, ux, uy = l1
    x2, y2, vx, vy = l2
    if abs(ux * vy - uy * vx) > 0.03:
        return False
    return abs((x2 - x) * uy - (y2 - y) * ux) <= tol


def make_assemblies(doc, report, fmt, fatal):
    """Un montaje por trabe del plano: junta las secciones (tipos 'T-4 Seccion 1', 'Seccion 2'...) que estan en la
    misma linea con sus trabes anfitrionas y el acero que creo la herramienta. Trabes iguales en otra linea llevan
    numero: 'Trabe T-2 Nivel +16.10', 'Trabe T-2.1 Nivel +16.10'. Antes desensambla los montajes que ya tenian
    esas trabes o varillas."""
    groups = {}                                   # (nivel, nombre) -> [[recs de una linea]]
    for r in report:
        if r.get('estado') != u'Creado' or not r.get('_chain') or not r.get('montaje'):
            continue
        key = (r['_level'][0], r['montaje'])
        r['_line'] = _line(r['_chain'])
        lines = groups.setdefault(key, [])
        for g in lines:
            if any(_same_line(x['_line'], r['_line']) for x in g):
                g.append(r)
                break
        else:
            lines.append([r])
    plans = []
    for (lv, name), lines in sorted(groups.items()):
        lines.sort(key=lambda g: (round(g[0]['_line'][0], 1), round(g[0]['_line'][1], 1)) if g[0]['_line'] else (0, 0))
        for k, g in enumerate(lines):
            hosts = []
            for r in g:
                hosts += [e for e in r['_chain'] if ML_valid(e)]
            bars = [rb for rb in AL.hosted(hosts) if AL.comment(rb).startswith(R.TAG)]
            plans.append((C.numbered(name, k), g, hosts, bars))
    if not plans:
        return
    # 1) desensamblar los montajes que ya contienen esas trabes o varillas
    old = {}
    for nm, g, hosts, bars in plans:
        for e in hosts + bars:
            try:
                aid = e.AssemblyInstanceId
            except Exception:
                continue
            if aid is not None and aid != ElementId.InvalidElementId:
                old[R.eid_int(aid)] = aid
    t = Transaction(doc, u'Deshacer montajes previos de trabes')
    t.Start()
    for aid in old.values():
        a = doc.GetElement(aid)
        if a is not None:
            try:
                a.Disassemble()
            except Exception as ex:
                fatal.append(u'Montajes: no se pudo desensamblar Id %d: %s' % (R.eid_int(aid), ex))
    t.Commit()
    # 2) crear
    cat = ElementId(BuiltInCategory.OST_StructuralFraming)
    made = []
    t = Transaction(doc, u'Crear montajes de trabes')
    t.Start()
    for nm, g, hosts, bars in plans:
        ids = List[ElementId]([e.Id for e in hosts + bars])
        try:
            c = cat
            if not AssemblyInstance.IsValidNamingCategory(doc, c, ids):
                c = ElementId(BuiltInCategory.OST_Rebar)
            ai = AssemblyInstance.Create(doc, ids, c)
            made.append((ai.Id, nm, g))
        except Exception as ex:
            for r in g:
                r['avisos'].append(u'no se pudo crear el montaje "%s": %s' % (nm, ex))
    t.Commit()
    # 3) nombrar (en otra transaccion, el tipo ya existe). Si queda un tipo de montaje sin uso con ese nombre
    # (de un montaje desensamblado), se borra para reutilizar el nombre.
    used = {}
    for at in FilteredElementCollector(doc).OfClass(AssemblyType):
        used[R.el_name(at)] = at
    inst_types = set(R.eid_int(a.GetTypeId()) for a in FilteredElementCollector(doc).OfClass(AssemblyInstance))
    t = Transaction(doc, u'Nombrar montajes de trabes')
    t.Start()
    for aid, nm, g in made:
        ai = doc.GetElement(aid)
        final = nm
        n = 0
        while final in used and R.eid_int(used[final].Id) in inst_types and \
                R.eid_int(used[final].Id) != R.eid_int(ai.GetTypeId()):
            n += 1
            final = u'%s (%d)' % (nm, n)
        if final in used and R.eid_int(used[final].Id) not in inst_types:
            try:
                doc.Delete(used[final].Id)
            except Exception:
                pass
        try:
            doc.GetElement(ai.GetTypeId()).Name = final
            used[final] = doc.GetElement(ai.GetTypeId())
            inst_types.add(R.eid_int(ai.GetTypeId()))
        except Exception as ex:
            for r in g:
                r['avisos'].append(u'montaje creado pero no se pudo nombrar "%s": %s' % (final, ex))
            continue
        for r in g:
            r['montaje_final'] = final
    t.Commit()


def ML_valid(e):
    try:
        return e is not None and e.IsValidObject
    except Exception:
        return e is not None


def _print_report(report, fatal, apply_mode, path):
    out = script.get_output()
    out.set_title(TITLE)
    out.print_md(u'## %s — %s' % (TITLE, u'aplicado' if apply_mode else u'revisión (no se creó nada)'))
    out.print_md(u'Archivo: `%s`' % path)
    for f in fatal:
        out.print_md(u'### ❌ Error')
        out.print_code(f)
    if not report:
        return
    ok = [r for r in report if r['estado'] in (u'OK', u'Creado')]
    bad = [r for r in report if r['estado'] == u'Error']
    out.print_md(u'**%d trabes correctas · %d con error · %.0f kg de acero%s**'
                 % (len(ok), len(bad), sum(r.get('kg', 0.0) for r in ok),
                    u'' if apply_mode else u' (estimado)'))
    rows = []
    for r in report:
        rows.append([r['nivel'], r['trabe'], r['estado'], u'%s' % (r.get('tramos') or u''),
                     u'%s' % (r.get('n_bars') or u''), u'%s' % (r.get('n_st') or u''),
                     u'%.1f' % r['kg'] if r.get('kg') else u'',
                     u'%s' % r.get('creados', u'') if apply_mode else u'',
                     r.get('montaje_final') or r.get('montaje') or u'',
                     u'%d' % len(r.get('avisos') or [])])
    out.print_table(rows, columns=[u'Nivel', u'Trabe', u'Estado', u'Tramos', u'Varillas', u'Estribos', u'kg',
                                   u'Elementos creados', u'Montaje', u'Avisos'])
    if bad:
        out.print_md(u'### Errores')
        for r in bad:
            for e in r['errores']:
                out.print_md(u'- **%s %s**: %s' % (r['nivel'], r['trabe'], e))
    warn = [r for r in report if r.get('avisos')]
    if warn:
        out.print_md(u'### Avisos')
        for r in warn:
            out.print_md(u'**%s %s**' % (r['nivel'], r['trabe']))
            for w in r['avisos'][:25]:
                out.print_md(u'- %s' % w)


# ================================================================== entrada
def main(doc, uidoc):
    ops = [u'1. Generar plantilla de Excel desde el modelo',
           u'2. Revisar Excel (no crea nada)',
           u'3. Aplicar armado desde Excel']
    op = forms.CommandSwitchWindow.show(ops, message=u'Armado de trabes desde Excel')
    if not op:
        return
    if op == ops[0]:
        generate_template(doc)
    elif op == ops[1]:
        run(doc, uidoc, False)
    else:
        run(doc, uidoc, True)
