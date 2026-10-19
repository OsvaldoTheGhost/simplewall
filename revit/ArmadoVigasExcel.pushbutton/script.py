# -*- coding: utf-8 -*-
"""Armado de trabes desde Excel: genera una plantilla con los tramos de las trabes de un nivel, lee el
armado capturado del plano (N2-N4, 3#4, 1#4 100/100, E#3 11@10 @20 11@10...) y lo crea con el motor
de Armado Viga en todos los niveles indicados."""
from __future__ import division, print_function

__title__ = u'Armado\nTrabes Excel'
__author__ = u'Osvaldo'

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_VIGA = os.path.join(os.path.dirname(_HERE), 'ArmadoViga.pushbutton')   # motor de Armado Viga
for p in (_HERE, _VIGA):
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

if not os.path.isfile(os.path.join(_VIGA, 'bv_geom.py')):
    forms.alert(u'No encontré la herramienta Armado Viga junto a este botón:\n%s' % _VIGA,
                title=u'Armado de trabes desde Excel', exitscript=True)

import vx_main

vx_main.main(revit.doc, revit.uidoc)
