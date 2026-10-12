# -*- coding: utf-8 -*-
"""Armado de vigas con interfaz tipo VBAR: corridos, bastones superiores e
inferiores por capas, barra central (piel), estribos por zonas y vistas.

Seleccione una viga, o varias vigas seguidas (viga continua), y ejecute."""
from __future__ import division, print_function

__title__ = u'Armado\nViga'
__author__ = u'Osvaldo'
__persistentengine__ = True            # ventana no modal con ExternalEvent

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _find_lib(start):
    """Carpeta lib de la extension (sirve tambien dentro de pulldowns/stacks)."""
    d = start
    for _ in range(6):
        d = os.path.dirname(d)
        if os.path.isfile(os.path.join(d, 'lib', 'armado_modeless.py')):
            return os.path.join(d, 'lib')
    return None


_LIB = _find_lib(_HERE)
if _LIB and _LIB not in sys.path:
    sys.path.append(_LIB)

from pyrevit import revit
import bv_main

# Toda la logica esta en bv_main.py: pyRevit limpia las globales de este archivo
# al terminar y la ventana no modal las necesita despues.
bv_main.main(revit.doc, revit.uidoc, os.path.join(_HERE, 'ui.xaml'))
