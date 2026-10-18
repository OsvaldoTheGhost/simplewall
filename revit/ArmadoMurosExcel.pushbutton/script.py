# -*- coding: utf-8 -*-
"""Armado de muros desde Excel: genera una plantilla con los tipos de muro de uno o varios niveles, lee el
armado capturado del plano (N2-N4, #4@20, borde 4#5 40, E#3 @10 @20...) y lo crea con el motor de
Armado Muro en todos los niveles indicados."""
from __future__ import division, print_function

__title__ = u'Armado\nMuros Excel'
__author__ = u'Osvaldo'

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PULL = os.path.dirname(_HERE)
_MURO = os.path.join(_PULL, 'ArmadoMuro.pushbutton')            # motor de Armado Muro
_VX = os.path.join(_PULL, 'ArmadoVigasExcel.pushbutton')        # utilidades de Excel / niveles
for p in (_HERE, _MURO, _VX):
    if p not in sys.path:
        sys.path.insert(0, p)


def _find_lib(start):
    d = start
    for _ in range(6):
        d = os.path.dirname(d)
        if os.path.isfile(os.path.join(d, 'lib', 'armado_modeless.py')):
            return os.path.join(d, 'lib')
    return None


_LIB = _find_lib(_HERE)
if _LIB and _LIB not in sys.path:
    sys.path.append(_LIB)

from pyrevit import revit, forms

for _d, _f, _n in ((_MURO, 'aw_geom.py', u'Armado Muro'), (_VX, 'vx_xlsx.py', u'Armado Trabes Excel')):
    if not os.path.isfile(os.path.join(_d, _f)):
        forms.alert(u'No encontré la herramienta %s junto a este botón:\n%s' % (_n, _d),
                    title=u'Armado de muros desde Excel', exitscript=True)

import wx_main

wx_main.main(revit.doc, revit.uidoc)
