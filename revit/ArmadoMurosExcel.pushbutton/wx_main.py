# -*- coding: utf-8 -*-
"""Armado de muros desde Excel (Revit). Usa el motor de Armado Muro (aw_geom / aw_revit) sin abrir su
ventana: cada fila de la tabla (un tipo de muro en uno o varios niveles) se convierte en la misma
configuracion que se capturaria a mano y se arma cada muro de ese tipo cuyo nivel base sea el indicado.

Flujo:
  1. Generar plantilla: lee los muros de los niveles elegidos y escribe una fila por tipo de muro.
  2. Se llena el armado con el plano (Niveles: N2-N4 para repetirlo en esos niveles).
  3. Revisar: calcula todo sin crear nada y muestra el reporte.
  4. Aplicar: crea el armado (todo se deshace con un solo Ctrl+Z), llena el parametro Nivel de las
     varillas y crea un montaje por muro ("Muro M-1 Nivel +16.10")."""
from __future__ import division, print_function

import os
import re
import traceback

from pyrevit import forms, script
from Autodesk.Revit.DB import (FilteredElementCollector, BuiltInCategory, BuiltInParameter, Level, Wall,
                               Transaction, TransactionGroup, StorageType, UnitUtils, UnitTypeId,
                               AssemblyInstance, AssemblyType, ElementId)
from Autodesk.Revit.DB.Structure import RebarHostData
from System.Collections.Generic import List

import aw_geom as W
import aw_colgeom as CG
import aw_revit as R
import armado_leer as AL
import wx_core as C
import vx_xlsx as X

TITLE = u'Armado de muros desde Excel'
SHEET = u'Armado'
SHEET_CFG = u'Configuracion'
SHEET_HELP = u'Instrucciones'


class SafeProgress(object):
    """Barra de progreso que nunca tumba el proceso (si la ventana falla se sigue sin barra)."""

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
        if self.pb is not None and not self.dead:
            try:
                self.pb.__exit__(None, None, None)
            except Exception:
                pass
        return False


# ================================================================== modelo
def _m(ft):
    return UnitUtils.ConvertFromInternalUnits(ft, UnitTypeId.Meters)


def get_levels(doc):
    lv = sorted(FilteredElementCollector(doc).OfClass(Level), key=lambda l: l.Elevation)
    return [(l.Name, round(_m(l.Elevation), 3), l) for l in lv]


def wall_type(w):
    try:
        return R.el_name(w.WallType)
    except Exception:
        return u''


def base_level_id(w):
    try:
        return R.eid_int(w.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT).AsElementId())
    except Exception:
        return None


class WallIndex(object):
    """Muros rectos que pueden llevar armado, por (nivel base, tipo)."""

    def __init__(self, doc):
        self.by = {}
        for w in FilteredElementCollector(doc).OfClass(Wall).WhereElementIsNotElementType():
            if not R.is_wall(w):
                continue
            try:
                if not RebarHostData.IsValidHost(w):
                    continue
            except Exception:
                pass
            tn = wall_type(w)
            lid = base_level_id(w)
            if not tn or lid is None:
                continue
            self.by.setdefault((lid, C.norm_wall(tn)), []).append(w)

    def find(self, level, name):
        return self.by.get((R.eid_int(level.Id), C.norm_wall(name))) or []

    def types_of(self, level):
        out = {}
        lid = R.eid_int(level.Id)
        for (l, _k), ws in self.by.items():
            if l == lid:
                for w in ws:
                    out.setdefault(wall_type(w), []).append(w)
        return out


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s or u'')]


def _f(v, nd=2):
    return (u'%.*f' % (nd, v)).rstrip(u'0').rstrip(u'.')


# ================================================================== plantilla
def _help_rows():
    return [
        [u'Cómo llenar la hoja "Armado"', u''],
        [u'1', u'Cada fila es un tipo de muro (M-1, M-2...) en uno o varios niveles. El armado se aplica a TODOS los '
               u'muros de ese tipo cuyo nivel base sea el indicado. La plantilla trae espesor, número de muros, '
               u'longitudes y alto (columnas grises, solo informativas).'],
        [u'2', u'Niveles: N2 = "Nivel 2". Para repetir el mismo armado en varios niveles escriba N2-N4 (o N2,N3,N5). '
               u'También acepta la elevación: +7.55. Si un muro cambia de armado al subir, haga una fila por cada grupo '
               u'de niveles (M-1 en N2-N4 y M-1 en N5-N9).'],
        [u'3', u'Parrillas: 1 o 2 (vacío = 2 si el muro mide 15 cm o más).'],
        [u'4', u'Verticales / Horizontales: "#4@20". "No" = sin esa varilla.'],
        [u'5', u'Bordes (como en el plano): "8#6+4#4 60 2E@15" = 8 varillas #6 y 4 #4 en Lb = 60 cm, 2 estribos cerrados '
               u'por juego @15. "1E+1G@15" = 1 estribo + 1 grapa; también acepta "6EST.#3+1GR#3@15cm". Las varillas se '
               u'acomodan solas (las más gruesas en los extremos, en paquetes de 2 si no caben); el número, diámetro y '
               u'longitud son los del plano. "No" = sin borde. El botón ve qué es cada extremo:'],
        [u'', u'· Borde extremo libre: extremo que no llega a otro muro. Lb desde el extremo.'],
        [u'', u'· Borde en intersección: extremo que llega a otro muro (T). Lb desde el paño lejano del otro muro. '
              u'Vacío = igual al extremo libre. Los extremos que llegan a una columna no llevan borde (ver armado de columna).'],
        [u'', u'· Borde en esquina L: el elemento en L completo, una vez: "20#6+2#4 120/60 2E+2G@15" = Lb 120 en el muro '
              u'más largo y 60 en el más corto, medidos desde la esquina exterior (si el muro es más corto, toma todo el muro). '
              u'Las varillas y los estribos se reparten entre los dos brazos. Vacío = igual a intersección.'],
        [u'', u'· "A / B": A en el muro más largo de la L y B en el más corto (p. ej. extremos libres distintos). '
              u'"A ; B": esquinas distintas del mismo tipo, en orden de planta (izquierda a derecha, luego abajo hacia arriba).'],
        [u'6', u'Estribos borde: tipo y separación por omisión (E#3 @15) si la celda del borde no los trae.'],
        [u'7', u'Refuerzo huecos: "2#5 60" = 2 varillas #5 por lado anclando 60 cm. "No" = sin refuerzo.'],
        [u'8', u'Filas sin ningún dato de armado se ignoran. Las celdas de Niveles y Muro vacías toman las de la fila anterior.'],
        [u'9', u'Primero use "Revisar" (no crea nada); luego "Aplicar". Todo lo que crea "Aplicar" se deshace con un solo Ctrl+Z.'],
        [u'10', u'Montajes: al aplicar se crea un montaje por muro con su acero ("Muro M-1 Nivel +16.10"). Muros del mismo tipo '
                u'en otro lugar: "Muro M-1.1 Nivel +16.10". Los montajes que ya tenían esos muros o varillas se desensamblan antes. '
                u'Los muros del mismo tipo unidos en esquina van en un solo montaje. Montajes idénticos en otros niveles '
                u'(+10.40 y +27.50) llevan cada uno su propio tipo y nombre; el nombre también queda en Comentarios '
                u'del montaje. Se apaga en Configuracion › Crear montajes = No.'],
        [u'11', u'La columna Notas de la plantilla dice cuántos muros de cada tipo están en esquina en L. Para armarlos '
                u'sueltos: Configuracion › Esquinas en L = No.'],
    ]


def generate_template(doc):
    levels = get_levels(doc)
    idx = WallIndex(doc)
    opts = []
    for name, z, lv in levels:
        tps = idx.types_of(lv)
        if tps:
            opts.append((u'%s  (%s)  ·  %d tipos · %d muros' % (name, C.elev_txt(z), len(tps),
                                                               sum(len(v) for v in tps.values())), name, z, lv))
    if not opts:
        forms.alert(u'No encontré muros rectos que puedan llevar armado.', title=TITLE)
        return
    sel = forms.SelectFromList.show([o[0] for o in opts], title=u'Niveles para la plantilla',
                                    button_name=u'Generar plantilla', multiselect=True)
    if not sel:
        return
    chosen = [o for o in opts if o[0] in sel]
    path = forms.save_file(file_ext='xlsx', default_name=u'Armado muros %s' %
                           u' '.join(C.level_label(o[1]) for o in chosen))
    if not path:
        return
    rows = [[c[1] for c in C.COLUMNS]]
    problems = []
    work = []
    for o in chosen:
        for tn, ws in sorted(idx.types_of(o[3]).items(), key=lambda kv: natural_key(kv[0])):
            work.append((o, tn, ws))
    analyzed = {}
    with SafeProgress(u'Analizando muros ({value} de {max_value})') as pb:
        for k, (o, tn, ws) in enumerate(work):
            if pb.cancelled:
                return
            pb.update_progress(k + 1, len(work))
            infos, bad = [], []
            for w in ws:
                try:
                    info, err = R.analyze_wall(doc, w)
                except Exception as ex:
                    info, err = None, u'%s' % ex
                if info is None:
                    bad.append(u'Id %d: %s' % (R.eid_int(w.Id), err))
                else:
                    infos.append(info)
            analyzed[(o[1], tn)] = (infos, bad)
    # muros en esquina en L, por nivel
    in_corner = set()
    for o in chosen:
        items = [(i,) for (lv, tn), (infos, bad) in analyzed.items() if lv == o[1] for i in infos]
        for g in find_chains(items):
            if len(g) > 1:
                in_corner.update(items[i][0]['id'] for i in g)
    for o, tn, ws in work:
        infos, bad = analyzed.get((o[1], tn), ([], []))
        notes = []
        if bad:
            notes.append(u'%d muro(s) no se pudieron analizar: %s' % (len(bad), u'; '.join(bad[:3])))
            problems.append(u'%s %s: %s' % (C.level_label(o[1]), tn, u'; '.join(bad[:3])))
        ts = sorted(set(round(i['t'], 1) for i in infos))
        Ls = sorted(i['L'] / 100.0 for i in infos)
        Hs = sorted(set(round(i['H'] / 100.0, 2) for i in infos))
        nop = sum(len(R.make_model(i).openings) for i in infos)
        nl = sum(1 for i in infos if i['id'] in in_corner)
        nt = sum(1 for i in infos for sd in ('L', 'R') if (i.get('ends') or {}).get(sd) and i['id'] not in in_corner)
        if nl:
            notes.append(u'%d en esquina L' % nl)
        if nt:
            notes.append(u'%d extremo(s) contra otro muro' % nt)
        if len(ts) > 1:
            notes.append(u'OJO: espesores distintos %s' % u', '.join(_f(t, 1) for t in ts))
        if nop:
            notes.append(u'%d hueco(s)' % nop)
        longs = u', '.join(_f(x) for x in Ls[:8]) + (u'…' if len(Ls) > 8 else u'')
        row = [u''] * len(C.COLUMNS)
        row[0], row[1], row[2], row[3], row[4], row[5] = (C.level_label(o[1]), tn, u'/'.join(_f(t, 1) for t in ts),
                                                          len(ws), longs, u'/'.join(_f(h) for h in Hs))
        row[-1] = u'; '.join(notes)
        rows.append(row)
    cfg_rows = [[u'Parámetro', u'Valor', u'Notas']] + [[s[1], s[2], s[3]] for s in C.SETTINGS]
    X.write_book(path, [
        (SHEET, rows, {'widths': [c[2] for c in C.COLUMNS], 'grey_cols': [2, 3, 4, 5], 'filter': True}),
        (SHEET_CFG, cfg_rows, {'widths': [36, 18, 70]}),
        (SHEET_HELP, _help_rows(), {'widths': [6, 120], 'note_style': True, 'freeze': False}),
    ])
    msg = u'Plantilla guardada con %d filas:\n%s' % (len(rows) - 1, path)
    if problems:
        msg += u'\n\nMuros que no se pudieron analizar:\n- ' + u'\n- '.join(problems[:12])
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
        if 'muro' in m and 'niveles' in m:
            hdr_row, cmap = r, m
            break
    if hdr_row is None:
        raise ValueError(u'No encontré los encabezados "Niveles" y "Muro" en la hoja "%s".' % name)
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


# ================================================================== proceso
def set_level_param(rebars, pname, value, level):
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


def own_bars(walls):
    return [rb for rb in AL.hosted(walls) if AL.comment(rb).startswith(R.TAG)]


def _pd(el, bip):
    try:
        p = el.get_Parameter(bip)
        return p.AsDouble() if p is not None else None
    except Exception:
        return None


def ref_level(wall, name, z, level, levels, mode):
    """Nivel al que pertenece el acero del muro: (nombre, elevacion m, Level). 'Superior': el nivel de arriba del
    entrepiso (los planos ven la planta por debajo): la restriccion superior del muro o, si no tiene, el primer
    nivel en / arriba de su corona (hasta 0.60 m abajo, por la losa). 'Base': el nivel de arranque."""
    if C.fold(mode or u'').startswith(u'base'):
        return name, z, level
    try:
        top = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE).AsElementId()
        for n, zz, l in levels:
            if l.Id == top and zz > z + 0.5:
                return n, zz, l
    except Exception:
        pass
    h = _pd(wall, BuiltInParameter.WALL_USER_HEIGHT_PARAM)
    if h:
        zt = z + _m((_pd(wall, BuiltInParameter.WALL_BASE_OFFSET) or 0.0) + h)
        for n, zz, l in levels:                  # ordenados por elevacion
            if zz >= zt - 0.60 and zz > z + 0.5:
                return n, zz, l
    return name, z, level


def _rec(name, z, wall, mu, settings, mk_on, ref=None):
    tn = wall_type(wall) or mu
    rn, rz = (ref[0], ref[1]) if ref else (name, z)
    return {'nivel': C.level_label(name), 'muro': tn, 'id': R.eid_int(wall.Id), 'estado': u'', 'kg': 0.0,
            'errores': [], 'avisos': [], 'creados': 0, 'borrados': 0, 'L': 0.0, 't': 0.0, 'H': 0.0, '_ref': ref,
            'montaje': C.assembly_name(settings.get('montaje_fmt'), C.wall_mark(tn), rn, rz) if mk_on else u''}


def _ends_xy(info):
    """Extremos (X, Y) en planta de la linea del muro (pies), para buscar esquinas rapido."""
    c = info['elem'].Location.Curve
    return [(p.X, p.Y) for p in (c.GetEndPoint(0), c.GetEndPoint(1))]


def find_chains(items):
    """items: [(info, ...)] de un nivel. Devuelve grupos de indices: cadenas de muros en esquina en L
    (2 o mas) y muros sueltos (1). Un muro con mas de dos esquinas o un anillo cerrado se parten."""
    n = len(items)
    ends = [_ends_xy(it[0]) for it in items]
    adj = dict((i, []) for i in range(n))
    near = 3.5                                     # pies (~1 m) entre extremos para probar la esquina
    for i in range(n):
        for j in range(i + 1, n):
            if not any(((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 <= near for p in ends[i] for q in ends[j]):
                continue
            try:
                cn, _w = R.corner_of(items[i][0], items[j][0])
            except Exception:
                cn = None
            if cn is not None and len(adj[i]) < 2 and len(adj[j]) < 2:
                adj[i].append(j)
                adj[j].append(i)
    seen, groups = set(), []
    for i in range(n):
        if i in seen:
            continue
        comp, stack = [], [i]
        while stack:
            a = stack.pop()
            if a in seen:
                continue
            seen.add(a)
            comp.append(a)
            stack += adj[a]
        if len(comp) > 2 and all(len(adj[a]) == 2 for a in comp):
            # anillo cerrado (caja): se abre en la esquina entre los dos muros de mayor Id
            a = max(comp, key=lambda k: items[k][0]['id'])
            b = max(adj[a], key=lambda k: items[k][0]['id'])
            adj[a].remove(b)
            adj[b].remove(a)
        groups.append(sorted(comp))
    return groups


def run(doc, uidoc, apply_mode):
    path = forms.pick_file(file_ext='xlsx')
    if not path:
        return
    try:
        rows, settings, sheet = read_table(path)
    except Exception as ex:
        forms.alert(u'No se pudo leer el Excel:\n%s' % ex, title=TITLE)
        return
    groups, gerrs = C.group_rows(rows)
    if not groups:
        forms.alert(u'La hoja "%s" no tiene filas con armado.' % sheet, title=TITLE)
        return
    bts_list = R.get_bar_types(doc)
    if not bts_list:
        forms.alert(u'El modelo no tiene tipos de varilla.', title=TITLE)
        return
    hooks = R.get_stirrup_hooks(doc)
    ctx = {'settings': settings, 'bts_list': bts_list, 'bts': dict((b['name'], b) for b in bts_list),
           'hook_len': R.hook_length_table(bts_list, hooks), 'hook': hooks[0]['name'] if hooks else u'',
           'bt_elems': dict((d['name'], d['elem']) for d in bts_list),
           'hook_elems': dict((d['name'], d['elem']) for d in hooks), 'templates': R.get_section_templates(doc),
           'replace': C.is_yes(settings.get('replace') or u'Sí'), 'pname': C.clean(settings.get('param')),
           'pfmt': settings.get('param_fmt') or u'NIVEL {elev}', 'apply': apply_mode,
           'mk_on': not C.is_no(settings.get('montaje') or u'Sí'), 'col_boxes': column_boxes(doc)}
    corners_on = not C.is_no(settings.get('corners') or u'Sí')
    levels = get_levels(doc)
    idx = WallIndex(doc)

    # ---------------- muros por nivel: (fila, muro)
    by_level, report, fatal = {}, [], list(gerrs)
    owner = {}
    for lv_txt, mu, row in groups:
        lvs, errs = C.resolve_levels(lv_txt, [(n, z) for n, z, _ in levels])
        for e in errs:
            report.append({'nivel': lv_txt, 'muro': mu, 'estado': u'Error', 'errores': [u'fila %s: %s' % (row['_row'], e)]})
        for name, z in lvs:
            level = [l for n, zz, l in levels if n == name][0]
            walls = idx.find(level, mu)
            if not walls:
                report.append({'nivel': C.level_label(name), 'muro': mu, 'estado': u'Error',
                               'errores': [u'fila %s: no hay muros tipo "%s" con nivel base %s' % (row['_row'], mu, name)]})
                continue
            for w in walls:
                k = R.eid_int(w.Id)
                if k in owner:
                    fatal.append(u'El muro Id %d (%s, %s) está en las filas %s y %s; se usa la primera'
                                 % (k, mu, name, owner[k], row['_row']))
                    continue
                owner[k] = row['_row']
                by_level.setdefault((name, z), (level, []))[1].append((mu, row, w))
    n_walls = sum(len(v[1]) for v in by_level.values())
    if not n_walls:
        _print_report(report, fatal, apply_mode, path)
        return
    if apply_mode:
        msg = (u'Se armarán %d muros (%d filas de Excel en %d niveles).\n\n' % (n_walls, len(groups), len(by_level)))
        msg += (u'El armado que la herramienta ya había hecho en esos muros se reemplaza.' if ctx['replace'] else
                u'El armado previo de esos muros NO se borra (Reemplazar armado previo = No).')
        if corners_on:
            msg += u'\nLos muros en esquina en L (L, U, Z...) se arman juntos.'
        msg += u'\n\nTodo se puede deshacer con un solo Ctrl+Z. ¿Continuar?'
        if not forms.alert(msg, title=TITLE, yes=True, no=True):
            return
    tg = None
    if apply_mode:
        tg = TransactionGroup(doc, TITLE)
        tg.Start()
    try:
        with SafeProgress(u'Armando muros ({value} de {max_value})') as pb:
            done = 0
            stop = False
            for (name, z), (level, lst) in sorted(by_level.items(), key=lambda kv: kv[0][1]):
                # analisis de todos los muros del nivel
                items = []
                for mu, row, w in sorted(lst, key=lambda t: R.eid_int(t[2].Id)):
                    ref = ref_level(w, name, z, level, levels, settings.get('nivel_ref'))
                    rec = _rec(name, z, w, mu, settings, ctx['mk_on'], ref)
                    try:
                        info, err = R.analyze_wall(doc, w)
                    except Exception as ex:
                        info, err = None, u'%s' % ex
                    if info is None:
                        rec['estado'] = u'Error'
                        rec['errores'].append(err)
                        report.append(rec)
                        done += 1
                        continue
                    rec['L'], rec['t'], rec['H'] = info['L'], info['t'], info['H']
                    rec['avisos'] += [u'%s' % x for x in info.get('warn') or []]
                    items.append((info, row, rec))
                units = find_chains(items) if corners_on else [[i] for i in range(len(items))]
                for u in units:
                    if pb.cancelled:
                        fatal.append(u'Cancelado por el usuario después de %d muros.' % done)
                        stop = True
                        break
                    done += len(u)
                    pb.update_progress(done, n_walls)
                    sub = [items[i] for i in u]
                    if len(sub) > 1:
                        recs = _chain(doc, sub, name, z, level, ctx)
                    else:
                        recs = [_single(doc, sub[0], name, z, level, ctx)]
                    report += recs
                if stop:
                    break
        if tg is not None:
            if any(r['estado'] == u'Creado' for r in report):
                if ctx['mk_on']:
                    try:
                        make_assemblies(doc, report, settings.get('montaje_fmt'), fatal)
                    except Exception:
                        fatal.append(u'Montajes: ' + traceback.format_exc())
                tg.Assimilate()
            else:
                tg.RollBack()
    except Exception:
        fatal.append(u'No se creó nada (se deshizo todo):\n' + traceback.format_exc())
        if tg is not None and tg.HasStarted() and not tg.HasEnded():
            tg.RollBack()
        for r in report:
            if r.get('estado') == u'Creado':
                r['estado'] = u'Deshecho'
    _print_report(report, fatal, apply_mode, path)


def column_boxes(doc):
    """Cajas (xmin, ymin, zmin, xmax, ymax, zmax) en pies de las columnas estructurales."""
    out = []
    for c in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StructuralColumns) \
            .WhereElementIsNotElementType():
        try:
            bb = c.get_BoundingBox(None)
        except Exception:
            bb = None
        if bb is not None:
            out.append((bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z))
    return out


def col_ends(info, boxes, tol=0.25):
    """Lados ('L' inicio / 'R' fin) del muro que llegan a una columna (su extremo toca la caja de la columna)."""
    out = set()
    try:
        fr = info['frame']
        z0 = info['z_min']
        z1 = z0 + info['H'] / 30.48
        for side, s in (('L', info['s_min']), ('R', info['s_min'] + info['L'] / 30.48)):
            for dl in (-tol, 0.0, tol):
                x = fr.O.X + fr.u.X * s + fr.v.X * (info['l_c'] + dl)
                y = fr.O.Y + fr.u.Y * s + fr.v.Y * (info['l_c'] + dl)
                if any(b[0] - tol <= x <= b[3] + tol and b[1] - tol <= y <= b[4] + tol and b[2] < z1 and b[5] > z0
                       for b in boxes):
                    out.add(side)
                    break
    except Exception:
        pass
    return out


def make_model(info, ctx):
    m = R.make_model(info)
    m.col_ends = col_ends(info, ctx.get('col_boxes') or [])
    for sd in m.col_ends:
        (m.ends or {}).pop(sd, None)
    return m


def _summ(rec, res, cfg, ctx, full=False):
    rec['avisos'] += [u'%s' % x for x in res.get('warnings') or []
                      if not (full and u'se traslapan' in (u'%s' % x))]
    hk = ctx['hook_len'].get((cfg['bstir']['bt'], cfg['bstir'].get('hook') or ctx['hook'])) or 0.0
    wsum, n_st = W.summary(res, hk)
    rec['kg'] = sum(wsum.values())
    rec['n_v'] = sum(len(v['ss']) for v in res['v'])
    rec['n_h'] = sum(len(h['zs']) for h in res['h'])
    rec['n_st'] = n_st


def _after_build(doc, rec, wall, wr, name, z, level, ctx):
    rec['estado'] = u'Creado'
    rec['_wall'] = wall
    rec['_level'] = (name, z)
    rec['creados'] = wr.get('elements', 0)
    rec['borrados'] = wr.get('deleted', 0)
    rec['errores'] += wr.get('errors') or []
    if ctx['pname']:
        try:
            own = own_bars([wall])
            rn, rz, rl = rec.get('_ref') or (name, z, level)      # nivel al que pertenece el acero
            value = ctx['pfmt'].replace(u'{elev}', C.elev_txt(rz)).replace(u'{nivel}', rn)
            t = Transaction(doc, u'Parámetro de nivel en varillas')
            t.Start()
            n = set_level_param(own, ctx['pname'], value, rl)
            t.Commit()
            if own and not n:
                rec['avisos'].append(u'las varillas no tienen el parámetro "%s" (o es de solo lectura)' % ctx['pname'])
        except Exception as ex:
            rec['avisos'].append(u'no se pudo llenar el parámetro "%s": %s' % (ctx['pname'], ex))


def _single(doc, item, name, z, level, ctx):
    info, row, rec = item
    bts_list, settings = ctx['bts_list'], ctx['settings']
    try:
        model = make_model(info, ctx)
        cfg, zones, warns, errs = C.build_cfg(W, CG, model, bts_list, settings, row)
        rec['avisos'] += warns
        if errs:
            rec['estado'] = u'Error'
            rec['errores'] += [u'fila %s: %s' % (row['_row'], e) for e in errs]
            return rec
        cfg = W.fit_cfg(cfg, model, bts_list)
        res = W.compute(model, cfg, zones, ctx['bts'])
        _summ(rec, res, cfg, ctx, bool(cfg.get('_full')))
        if not res['v'] and not res['h']:
            rec['estado'] = u'Error'
            rec['errores'].append(u'la configuración no produce varillas')
            return rec
    except Exception:
        rec['estado'] = u'Error'
        rec['errores'].append(traceback.format_exc())
        return rec
    if not ctx['apply']:
        rec['estado'] = u'OK'
        return rec
    label = u'%s %s Id %d' % (rec['nivel'], rec['muro'], rec['id'])
    rep = R.build(doc, [(info, model, res, label)], cfg, ctx['bt_elems'], ctx['hook_elems'], ctx['templates'],
                  ctx['replace'], None)
    if rep['fatal']:
        rec['estado'] = u'Error'
        rec['errores'].append(u'no se creó nada:\n' + rep['fatal'])
        return rec
    rec['avisos'] += sorted(rep['notes'])
    _after_build(doc, rec, info['elem'], rep['walls'][0] if rep['walls'] else {}, name, z, level, ctx)
    return rec


def _chain(doc, sub, name, z, level, ctx):
    """Muros en esquina en L (L, U, Z...): se arman juntos como en Armado Muro (una sola configuracion,
    alma de cada muro segun su fila, brazos de la L con su borde)."""
    bts_list, settings = ctx['bts_list'], ctx['settings']
    infos = [s[0] for s in sub]
    labels = [u'%s (Id %d)' % (s[2]['muro'], s[2]['id']) for s in sub]
    try:
        cs, order, why = R.chain_of(infos, labels)
    except Exception as ex:
        cs, order, why = None, None, u'%s' % ex
    if cs is None:
        out = []
        for s in sub:
            r = _single(doc, s, name, z, level, ctx)
            r['avisos'].append(u'no se pudo armar con su esquina (%s): se armó solo' % why)
            out.append(r)
        return out
    sub = [sub[i] for i in order]
    recs = [s[2] for s in sub]
    names = u' → '.join(labels[i] for i in order)
    for r in recs:
        r['avisos'].append(u'esquina en L: %s' % names)
        r['_chain'] = names
    try:
        models = [make_model(s[0], ctx) for s in sub]
        # orden en planta de las esquinas (izquierda a derecha, luego abajo hacia arriba) para 'A ; B'
        pos = [(round(cn['O'][0] / 30.0), cn['O'][1], k) for k, cn in enumerate(cs['corners'])]
        rank = dict((k, i) for i, (_x, _y, k) in enumerate(sorted(pos)))
        cfg, zones, warns, errs = C.build_chain_cfg(W, CG, cs, models, [s[1] for s in sub], bts_list, settings,
                                                    [rank[k] for k in range(len(cs['corners']))])
        bad = False
        for r, s, w, e in zip(recs, sub, warns, errs):
            r['avisos'] += w
            if e:
                bad = True
                r['estado'] = u'Error'
                r['errores'] += [u'fila %s: %s' % (s[1]['_row'], x) for x in e]
        if bad:
            for r in recs:
                if r['estado'] != u'Error':
                    r['estado'] = u'Error'
                    r['errores'].append(u'otro muro de su esquina tiene error')
            return recs
        cfg = W.fit_cfg(cfg, models[0], bts_list)
        W.corner_cfg(cfg, cs)
        sbt = ctx['bts'].get(cfg['bstir']['bt'])
        split = W.corner_split(cs, cfg, zones, float(cfg['cover']), sbt['dm'] if sbt else 0.0)
        items = []
        for k, (s, m) in enumerate(zip(sub, models)):
            res = W.compute(m, W.wall_cfg(cfg, k), zones, ctx['bts'], split[k])
            _summ(recs[k], res, cfg, ctx, k in (cfg.get('_full') or ()))
            items.append((s[0], m, res, u'%s %s' % (recs[k]['nivel'], labels[order[k]])))
    except Exception:
        for r in recs:
            r['estado'] = u'Error'
            r['errores'].append(traceback.format_exc())
        return recs
    if not ctx['apply']:
        for r in recs:
            r['estado'] = u'OK'
        return recs
    rep = R.build(doc, items, cfg, ctx['bt_elems'], ctx['hook_elems'], ctx['templates'], ctx['replace'], None,
                  chain=True)
    if rep['fatal']:
        for r in recs:
            r['estado'] = u'Error'
            r['errores'].append(u'no se creó nada:\n' + rep['fatal'])
        return recs
    by_id = dict((w['id'], w) for w in rep['walls'])
    for r, s in zip(recs, sub):
        r['avisos'] += sorted(n for n in rep['notes'] if n.startswith(u'%s ' % r['nivel']) and (u'Id %d)' % r['id']) in n)
        _after_build(doc, r, s[0]['elem'], by_id.get(r['id'], {}), name, z, level, ctx)
    return recs


# ================================================================== montajes
def _line(w):
    c = w.Location.Curve
    p, q = c.GetEndPoint(0), c.GetEndPoint(1)
    d = ((q.X - p.X) ** 2 + (q.Y - p.Y) ** 2) ** 0.5
    if d < 1e-6:
        return None
    return (p.X, p.Y, q.X, q.Y, (q.X - p.X) / d, (q.Y - p.Y) / d)


def _joined(l1, l2, tol=0.5):
    """Dos muros del mismo tipo en la misma linea y tocandose (un muro partido en piezas)."""
    if l1 is None or l2 is None:
        return False
    x, y, _a, _b, ux, uy = l1
    x2, y2, _c, _d, vx, vy = l2
    if abs(ux * vy - uy * vx) > 0.03 or abs((x2 - x) * uy - (y2 - y) * ux) > tol:
        return False
    for p in ((l1[0], l1[1]), (l1[2], l1[3])):
        for q in ((l2[0], l2[1]), (l2[2], l2[3])):
            if ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 <= tol:
                return True
    return False


def make_assemblies(doc, report, fmt, fatal):
    """Un montaje por muro (las piezas del mismo tipo seguidas en la misma linea van juntas) con su acero.
    Muros iguales en otro lugar llevan numero: 'Muro M-1 Nivel +16.10', 'Muro M-1.1 Nivel +16.10'."""
    groups = {}
    for r in report:
        if r.get('estado') != u'Creado' or not r.get('_wall') or not r.get('montaje'):
            continue
        r['_ln'] = _line(r['_wall'])
        lst = groups.setdefault((r['_level'][0], r['montaje']), [])
        hit = [g for g in lst if any(_joined(x['_ln'], r['_ln']) or (r.get('_chain') and x.get('_chain') == r.get('_chain'))
                                      for x in g)]
        merged_g = [r]
        for g in hit:
            merged_g += g
            lst.remove(g)
        lst.append(merged_g)
    plans = []
    for (lv, name), lst in sorted(groups.items()):
        lst.sort(key=lambda g: min((round(x['_ln'][0], 1), round(x['_ln'][1], 1)) if x['_ln'] else (0, 0) for x in g))
        for k, g in enumerate(lst):
            hosts = [x['_wall'] for x in g if x['_wall'].IsValidObject]
            plans.append((C.numbered(name, k), g, hosts, own_bars(hosts)))
    if not plans:
        return
    old = {}
    for nm, g, hosts, bars in plans:
        for e in hosts + bars:
            try:
                aid = e.AssemblyInstanceId
            except Exception:
                continue
            if aid is not None and aid != ElementId.InvalidElementId:
                old[R.eid_int(aid)] = aid
    t = Transaction(doc, u'Deshacer montajes previos de muros')
    t.Start()
    for aid in old.values():
        a = doc.GetElement(aid)
        if a is not None:
            try:
                a.Disassemble()
            except Exception as ex:
                fatal.append(u'Montajes: no se pudo desensamblar Id %d: %s' % (R.eid_int(aid), ex))
    t.Commit()
    cat = ElementId(BuiltInCategory.OST_Walls)
    made = []
    t = Transaction(doc, u'Crear montajes de muros')
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
    # Revit da el MISMO tipo de montaje a montajes identicos (el mismo muro con el mismo acero en +10.40 y en
    # +27.50), y el nombre del montaje es el nombre de ese tipo: renombrar el tipo renombraba los dos. Cada
    # montaje se nombra por su instancia (AssemblyTypeName), que lo pasa a un tipo propio con ese nombre; el
    # tipo compartido solo se renombra si nadie mas lo usa. Ademas, el nombre se escribe en Comentarios del
    # montaje (instancia) para poder filtrar las tablas aunque Revit no permitiera separar el tipo.
    t = Transaction(doc, u'Nombrar montajes de muros')
    t.Start()
    for aid, nm, g in made:
        ai = doc.GetElement(aid)
        if ai is None:
            continue
        final, err = _name_assembly(doc, ai, nm)
        if final is None:
            for r in g:
                r['avisos'].append(u'montaje creado pero no se pudo nombrar "%s": %s' % (nm, err))
            continue
        _set_comment(ai, nm)
        for r in g:
            r['montaje_final'] = final
            if final != nm:
                r['avisos'].append(u'el montaje quedó como "%s": Revit no permitió un tipo propio con el nombre "%s" '
                                   u'(use Comentarios del montaje para filtrar)' % (final, nm))
    t.Commit()


def _type_users(doc, type_id):
    """Instancias de montaje que usan el tipo type_id."""
    out = []
    for a in FilteredElementCollector(doc).OfClass(AssemblyInstance):
        try:
            if a.GetTypeId() == type_id:
                out.append(a)
        except Exception:
            pass
    return out


def _types_by_name(doc):
    out = {}
    for at in FilteredElementCollector(doc).OfClass(AssemblyType):
        out[R.el_name(at)] = at
    return out


def _name_assembly(doc, ai, nm):
    """Nombra el montaje ai como nm sin cambiar el nombre de otros montajes. Devuelve (nombre final, error)."""
    err = u''
    for n in range(0, 50):
        final = nm if n == 0 else u'%s (%d)' % (nm, n)
        types = _types_by_name(doc)
        tid = ai.GetTypeId()
        cur = doc.GetElement(tid)
        if cur is not None and R.el_name(cur) == final:
            return final, None
        other = types.get(final)
        if other is not None:
            users = [a for a in _type_users(doc, other.Id) if a.Id != ai.Id]
            if not users:
                # tipo sin montajes (de una corrida anterior): se borra para liberar el nombre
                try:
                    doc.Delete(other.Id)
                except Exception:
                    continue
            else:
                continue          # el nombre lo usa otro montaje (otro nivel / otro muro): siguiente numero
        shared = [a for a in _type_users(doc, tid) if a.Id != ai.Id]
        if not shared:
            try:
                cur.Name = final              # el tipo es solo de este montaje
                return final, None
            except Exception as ex:
                err = u'%s' % ex
                continue
        # tipo compartido con otro montaje identico: este montaje pasa a un tipo nuevo con su nombre
        names = [(a.Id, R.el_name(doc.GetElement(a.GetTypeId()))) for a in shared]
        try:
            ai.AssemblyTypeName = final
        except Exception as ex:
            err = u'%s' % ex
            continue
        changed = [i for i, old in names if R.el_name(doc.GetElement(doc.GetElement(i).GetTypeId())) != old]
        if R.el_name(doc.GetElement(ai.GetTypeId())) == final and not changed:
            return final, None
        # Revit renombro el tipo compartido (tambien el otro montaje): se regresa su nombre
        if changed:
            try:
                doc.GetElement(doc.GetElement(changed[0]).GetTypeId()).Name = dict(names)[changed[0]]
            except Exception:
                pass
        return R.el_name(doc.GetElement(ai.GetTypeId())), None
    return None, err or u'sin nombre disponible'


def _set_comment(ai, text):
    """Nombre del montaje (con su nivel) en Comentarios de la instancia: filtro seguro en las tablas."""
    try:
        p = ai.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p is not None and not p.IsReadOnly:
            p.Set(text)
    except Exception:
        pass


# ================================================================== reporte
def _print_report(report, fatal, apply_mode, path):
    out = script.get_output()
    out.set_title(TITLE)
    out.print_md(u'## %s — %s' % (TITLE, u'aplicado' if apply_mode else u'revisión (no se creó nada)'))
    out.print_md(u'Archivo: `%s`' % path)
    for f in fatal:
        out.print_md(u'### ❌ Aviso / error')
        out.print_code(f)
    if not report:
        return
    ok = [r for r in report if r['estado'] in (u'OK', u'Creado')]
    bad = [r for r in report if r['estado'] == u'Error']
    out.print_md(u'**%d muros correctos · %d con error · %.0f kg de acero%s**'
                 % (len(ok), len(bad), sum(r.get('kg', 0.0) for r in ok), u'' if apply_mode else u' (estimado)'))
    rows = []
    for r in report:
        try:
            link = out.linkify(ElementId(r['id'])) if r.get('id') else u''
        except Exception:
            link = u'%s' % r.get('id', u'')
        rows.append([r['nivel'], r['muro'], link, r['estado'],
                     _f(r['L'] / 100.0) if r.get('L') else u'', _f(r['t'], 1) if r.get('t') else u'',
                     u'%s' % (r.get('n_v') or u''), u'%s' % (r.get('n_h') or u''), u'%s' % (r.get('n_st') or u''),
                     u'%.1f' % r['kg'] if r.get('kg') else u'',
                     u'%s' % r.get('creados', u'') if apply_mode else u'',
                     r.get('montaje_final') or r.get('montaje') or u'', u'%d' % len(r.get('avisos') or [])])
    out.print_table(rows, columns=[u'Nivel', u'Muro', u'Id', u'Estado', u'L (m)', u't (cm)', u'Verticales',
                                   u'Horizontales', u'Estribos', u'kg', u'Elementos creados', u'Montaje', u'Avisos'])
    errs = [r for r in report if r.get('errores')]
    if errs:
        out.print_md(u'### Errores')
        for r in errs:
            out.print_md(u'**%s %s%s**' % (r['nivel'], r['muro'], (u' (Id %s)' % r['id']) if r.get('id') else u''))
            for e in r['errores']:
                out.print_md(u'- %s' % e.replace(u'\n', u'  \n'))
    av = [r for r in report if r.get('avisos')]
    if av:
        out.print_md(u'### Avisos')
        seen = set()
        for r in av:
            txt = u'; '.join(r['avisos'])
            key = (r['nivel'], r['muro'], txt)
            if key in seen:
                continue
            seen.add(key)
            out.print_md(u'**%s %s (Id %s)**' % (r['nivel'], r['muro'], r.get('id')))
            for a in r['avisos']:
                out.print_md(u'- %s' % a)


# ================================================================== menu
def main(doc, uidoc):
    ops = [u'1. Generar plantilla (muros de uno o varios niveles)',
           u'2. Revisar Excel (no crea nada)',
           u'3. Aplicar Excel (crea el armado)']
    sel = forms.CommandSwitchWindow.show(ops, message=u'Armado de muros desde Excel')
    if not sel:
        return
    try:
        if sel == ops[0]:
            generate_template(doc)
        elif sel == ops[1]:
            run(doc, uidoc, False)
        else:
            run(doc, uidoc, True)
    except Exception:
        forms.alert(traceback.format_exc(), title=TITLE)
