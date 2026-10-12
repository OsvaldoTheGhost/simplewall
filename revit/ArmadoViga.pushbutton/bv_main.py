# -*- coding: utf-8 -*-
"""Lógica del botón (sesión de la ventana no modal).

Vive en un módulo y no en script.py porque pyRevit borra las variables
globales de script.py al terminar, y la ventana no modal las sigue usando
después, desde el ExternalEvent (Seleccionar, Actualizar, OK)."""
from __future__ import division, print_function

from pyrevit import forms, script
from Autodesk.Revit.DB import View3D, BuiltInParameter
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

import bv_geom as G
import bv_revit as R
import bv_leer as LR
import bv_window as UI
import armado_modeless as ML

TITLE = u'Armado de viga'
PROMPT = u'Seleccione la viga (o las vigas seguidas de una viga continua) y pulse Finalizar'


class _BeamFilter(ISelectionFilter):
    def AllowElement(self, el):
        return R.is_beam(el)

    def AllowReference(self, ref, pt):
        return False


def _get_beams(doc, uidoc):
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    beams = [b for b in sel if R.is_beam(b)]
    if beams:
        return beams
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, _BeamFilter(), PROMPT)
    except OperationCanceledException:
        return []
    return [doc.GetElement(r.ElementId) for r in refs]


def _label(info):
    first = info['chain'][0]['elem']
    mark = u''
    try:
        p = first.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        mark = p.AsString() or u''
    except Exception:
        pass
    return mark or u'Id %d' % R.eid_int(first.Id)


def _desc(info, model, errors):
    first = info['chain'][0]['elem']
    try:
        tname = u'%s: %s' % (first.Symbol.FamilyName, R.el_name(first.Symbol))
    except Exception:
        tname = u''
    lines = [u'%d viga(s) · %s' % (len(info['chain']), tname)]
    if model.joints:
        y0 = model.sec(0)['yc'] + model.sec(0)['h'] / 2.0
        secs = []
        for i, sc in enumerate(model.sections):
            dz = sc['yc'] + sc['h'] / 2.0 - y0
            secs.append(u'T%d %s × %s%s' % (i + 1, UI.fcm(round(sc['b'], 1)), UI.fcm(round(sc['h'], 1)),
                                            (u' (%+.0f cm)' % dz) if abs(dz) > 1.0 else u''))
        lines.append(u'Secciones: %s cm · longitud %s m · cambio de sección en %s'
                     % (u' · '.join(secs), UI.fm(model.L), u', '.join(model.nodes[ni].name for ni in model.joints)))
    else:
        lines.append(u'Sección %s × %s cm · longitud %s m · %d tramo(s)'
                     % (UI.fcm(round(model.b, 1)), UI.fcm(round(model.h, 1)), UI.fm(model.L), model.n_spans))
    sup = []
    for ni, n in enumerate(model.nodes):
        if n.free:
            sup.append(u'extremo libre')
        elif ni in model.kink_nodes:
            sup.append(u'%s (quiebre en planta, sin apoyo)' % n.name)
        elif ni in model.bend_nodes:
            sup.append(u'%s (%s, %s m, cambio de eje)' % (n.name, n.kind, UI.fm(n.width)))
        elif n.kind == u'cambio':
            sup.append(u'%s (cambio de sección, sin apoyo)' % n.name)
        else:
            sup.append(u'%s (%s, %s m)' % (n.name, n.kind, UI.fm(n.width)))
    lines.append(u'Apoyos: ' + u' · '.join(sup))
    for w in info['warns']:
        lines.append(u'Aviso: ' + w)
    for el, e in errors:
        lines.append(u'Omitida Id %d: %s' % (R.eid_int(el.Id), e))
    return u'\n'.join(lines)


def _report(rep, label):
    out = script.get_output()
    out.set_title(u'Armado de viga')
    if rep['fatal']:
        out.print_md(u'## ❌ Error — no se creó nada (se deshizo la transacción)')
        print(rep['fatal'])
        return
    out.print_md(u'## Armado de viga %s — resultado' % label)
    c = rep['count']
    rows = [[u'Corridos superiores', str(c['top_main'])], [u'Corridos inferiores', str(c['bot_main'])],
            [u'Refuerzo superior (bastones)', str(c['top_add'])],
            [u'Refuerzo inferior (bastones)', str(c['bot_add'])],
            [u'Barra central (piel)', str(c['side'])], [u'Estribos', str(c['stirrups'])]]
    out.print_table(rows, columns=[u'Elemento', u'Piezas'])
    out.print_md(u'**Elementos de armadura creados:** %d · **armado previo borrado:** %d'
                 % (rep['elements'], rep['deleted']))
    if rep['views']:
        out.print_md(u'**Vistas creadas:** ' + u', '.join(rep['views']))
    for n in rep['notes']:
        out.print_md(u'- ' + n)
    if rep['errors']:
        out.print_md(u'### Errores')
        for e in rep['errors'][:60]:
            out.print_md(u'- ' + e)


class Session(object):
    """Estado de la ventana no modal (una viga o viga continua a la vez)."""

    def __init__(self, doc):
        self.doc = doc
        self.beams = []
        self.info = None
        self.bar_types = R.get_bar_types(doc)
        self.hooks = R.get_stirrup_hooks(doc)
        self.hook_len = R.hook_length_table(self.bar_types, self.hooks)

    def load(self, beams):
        info, errors = R.analyze_chain(self.doc, beams)
        if info is None:
            return None, (u'Ninguna viga válida:\n' +
                          u'\n'.join(u'Id %d: %s' % (R.eid_int(b.Id), e) for b, e in errors))
        self.beams = list(beams)
        self.info = info
        self.templates = R.get_section_templates(self.doc)
        model = G.BeamModel(info['b'], info['h'], info['L'], info['supports'], parts=info.get('parts'),
                            bends=info.get('bends'), end_parts=info.get('end_parts'))
        ctx = {
            'model': model,
            'bar_types': self.bar_types,
            'hooks_stir': self.hooks,
            'hook_len': self.hook_len,
            'templates': sorted(self.templates.keys()),
            'beam_desc': _desc(info, model, errors),
        }
        # armado que ya tiene la viga (corridas, bastones, estribos): en la ventana y en el reporte
        try:
            ex = LR.read_existing(self.doc, info)
            ctx['beam_desc'] += u'\n\n' + LR.short_text(ex)
            ctx['existing'] = ex
            if ex:
                out = script.get_output()
                out.set_title(TITLE)
                LR.print_report(ex, _label(info), out)
        except Exception as e:
            ctx['beam_desc'] += u'\n\nNo se pudo leer el armado existente: %s' % e
        return ctx, None

    def _ctx_or_raise(self, beams):
        ctx, err = self.load(beams)
        if ctx is None:
            if err is None:
                raise ML.Cancelled()
            raise ML.UserError(err)
        return ctx

    def pick(self, uiapp):
        ud = ML.check_doc(uiapp, self.doc)
        refs = ud.Selection.PickObjects(ObjectType.Element, _BeamFilter(), PROMPT)
        beams = [self.doc.GetElement(r.ElementId) for r in refs]
        if not beams:
            raise ML.Cancelled()
        return self._ctx_or_raise(beams)

    def reload(self, uiapp):
        ML.check_doc(uiapp, self.doc)
        beams = ML.refetch(self.doc, self.beams)
        if not beams:
            raise ML.UserError(u'Las vigas ya no existen en el modelo.')
        return self._ctx_or_raise(beams)

    def apply(self, uiapp, result):
        ud = ML.check_doc(uiapp, self.doc)
        info = self.info
        label = _label(info)
        bt_elems = dict((d['name'], d['elem']) for d in self.bar_types)
        hook_elems = dict((d['name'], d['elem']) for d in self.hooks)
        view3d = ud.ActiveView if isinstance(ud.ActiveView, View3D) else None
        rep = R.build(self.doc, info, result['model'], result['cfg'], result['res'], bt_elems, hook_elems,
                      self.templates, result['replace'], view3d, label)
        try:
            _report(rep, label)
        except Exception:
            pass
        if rep['fatal']:
            raise ML.UserError(u'No se creó nada; vea el reporte.')
        return ML.summary(rep['elements'], rep['deleted'], len(rep['errors']))


def main(doc, uidoc, xaml):
    beams = _get_beams(doc, uidoc)
    if not beams:
        return
    ses = Session(doc)
    if not ses.bar_types:
        forms.alert(u'El modelo no tiene tipos de varilla.', title=TITLE)
        return
    ctx, err = ses.load(beams)
    if ctx is None:
        if err:
            forms.alert(err, title=TITLE)
        return
    ctx['host'] = ML.Host(TITLE, ses)
    win = UI.BeamRebarWindow(xaml, ctx)
    win.show()
