# -*- coding: utf-8 -*-
"""Lógica del botón (sesión de la ventana no modal).

Vive en un módulo y no en script.py porque pyRevit borra las variables
globales de script.py al terminar, y la ventana no modal las sigue usando
después, desde el ExternalEvent (Seleccionar, Actualizar, OK)."""
from __future__ import division, print_function

from pyrevit import forms, script
from Autodesk.Revit.DB import View3D
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

import es_revit as R
import es_window as UI
import armado_modeless as ML

TITLE = u'Armado de escalera / rampa'


class _AnyFilter(ISelectionFilter):
    def AllowElement(self, el):
        return R.is_candidate(el)

    def AllowReference(self, ref, pt):
        return False


def _get_element(doc, uidoc):
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    sel = [e for e in sel if R.is_candidate(e)]
    if sel:
        return sel[0], len(sel)
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, _AnyFilter(),
                                         u'Seleccione la escalera, rampa o losa inclinada a armar')
    except OperationCanceledException:
        return None, 0
    return doc.GetElement(ref.ElementId), 1


def _report(rep, label):
    out = script.get_output()
    out.set_title(TITLE)
    if rep['fatal']:
        out.print_md(u'## ❌ Error — no se creó nada (se deshizo la transacción)')
        print(rep['fatal'])
        return
    out.print_md(u'## Armado de %s — resultado' % label)
    names = [('inf', u'Lecho inferior'), ('sup', u'Lecho superior'), ('rep', u'Repartición'),
             ('nariz', u'Narices de escalón')]
    rows = [[t, str(rep['count'].get(k, 0))] for k, t in names if rep['count'].get(k)]
    out.print_table(rows, columns=[u'Elemento', u'Piezas'])
    out.print_md(u'**Elementos de armadura creados:** %d · **armado previo borrado:** %d'
                 % (rep['elements'], rep['deleted']))
    for s in sorted(rep['notes']):
        out.print_md(u'- ' + s)
    if rep['errors']:
        out.print_md(u'### Errores')
        for e in rep['errors'][:60]:
            out.print_md(u'- ' + e)


class Session(object):
    """Estado de la ventana no modal (un elemento a la vez)."""

    def __init__(self, doc):
        self.doc = doc
        self.el = None
        self.info = None
        self.bar_types = R.get_bar_types(doc)

    def load(self, el, nsel=1):
        info, err = R.analyze(self.doc, el, 0)
        if info is None:
            return None, u'No se puede armar: %s' % err
        self.el = el
        self.info = info
        desc = R.describe(info)
        if nsel > 1:
            desc += u'\nAviso: se seleccionaron %d elementos; se arma solo el primero.' % nsel
        ctx = {'model': info['model'], 'bar_types': self.bar_types, 'desc': desc,
               'rebuild': self._rebuild_now, 'rotate': self.rotate}
        return ctx, None

    def _rebuild_now(self, rot):
        inf, e = R.analyze(self.doc, self.el, rot)
        if inf is None:
            raise ML.UserError(e)
        self.info = inf
        return inf['model']

    def rotate(self, uiapp, rot):
        ML.check_doc(uiapp, self.doc)
        return self._rebuild_now(rot)

    def _ctx_or_raise(self, el):
        ctx, err = self.load(el)
        if ctx is None:
            raise ML.UserError(err)
        return ctx

    def pick(self, uiapp):
        ud = ML.check_doc(uiapp, self.doc)
        ref = ud.Selection.PickObject(ObjectType.Element, _AnyFilter(),
                                      u'Seleccione la escalera, rampa o losa inclinada a armar')
        return self._ctx_or_raise(self.doc.GetElement(ref.ElementId))

    def reload(self, uiapp):
        ML.check_doc(uiapp, self.doc)
        els = ML.refetch(self.doc, [self.el])
        if not els:
            raise ML.UserError(u'El elemento ya no existe en el modelo.')
        return self._ctx_or_raise(els[0])

    def apply(self, uiapp, result):
        ud = ML.check_doc(uiapp, self.doc)
        cur = self.info
        view3d = ud.ActiveView if isinstance(ud.ActiveView, View3D) else None
        rep = R.build(self.doc, cur, result['cfg'], result['res'], dict((b['name'], b['elem']) for b in self.bar_types),
                      result['replace'], view3d)
        try:
            _report(rep, cur['mark'] or u'Id %d' % cur['id'])
        except Exception:
            pass
        if rep['fatal']:
            raise ML.UserError(u'No se creó nada; vea el reporte.')
        return ML.summary(rep['elements'], rep['deleted'], len(rep['errors']))


def main(doc, uidoc, xaml):
    el, nsel = _get_element(doc, uidoc)
    if el is None:
        return
    ses = Session(doc)
    if not ses.bar_types:
        forms.alert(u'El modelo no tiene tipos de varilla.', title=TITLE)
        return
    ctx, err = ses.load(el, nsel)
    if ctx is None:
        forms.alert(err, title=TITLE)
        return
    ctx['host'] = ML.Host(TITLE, ses)
    win = UI.StairRebarWindow(xaml, ctx)
    win.show()
