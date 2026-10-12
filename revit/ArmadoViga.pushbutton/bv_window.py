# -*- coding: utf-8 -*-
"""Ventana WPF (estilo VBAR) para definir el armado de vigas."""
from __future__ import division
import copy
import math
import os

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System import Double
from System.Windows import (Point, Size, Thickness, GridLength, Visibility,
                            FontWeights, LogicalTreeHelper, DependencyObject, FrameworkElement)
from System.Windows.Controls import (Canvas, TextBlock, Grid, ColumnDefinition, Border, StackPanel,
                                     ScrollViewer, ScrollBarVisibility, DockPanel, Dock, GroupBox,
                                     ContentControl)
from System.Windows.Shapes import Line, Ellipse, Rectangle, Polyline, Polygon
from System.Windows.Media import BrushConverter, DoubleCollection, PointCollection, PenLineJoin

from pyrevit import forms

import bv_geom as G
import armado_modeless as ML


# ================================================================ textos
TEXTS = {
    'title': (u'ARMADO DE VIGA', u'BEAM REINFORCEMENT'),
    'n_cfg': (u'Configuración', u'Settings'),
    'n_main': (u'Barras principales', u'Main bars'),
    'n_top': (u'Refuerzo superior', u'Top reinforcement'),
    'n_bot': (u'Refuerzo inferior', u'Bottom reinforcement'),
    'n_side': (u'Barra central', u'Side bars'),
    'n_stir': (u'Estribos', u'Stirrups'),
    'g_params': (u'PARÁMETROS DE ARMADO', u'REINFORCEMENT PARAMETERS'),
    'g_views': (u'CREACIÓN DE VISTAS', u'VIEW CREATION'),
    'stir_dia': (u'Ø Estribo', u'Stirrup Ø'),
    'cover': (u'Recubrimiento', u'Cover'),
    'partition': (u'Partición', u'Partition'),
    'laps_hdr': (u'TRASLAPES', u'LAPS'), 'lap': (u'Traslape (× db)', u'Lap (× db)'),
    'stock': (u'Long. comercial máx.', u'Max. stock length'), 'lap_style': (u'Unión', u'Joint'),
    'lap_pos_top': (u'Traslapes lecho superior (m desde el inicio, separados por coma)', u'Top layer laps (m from the start, comma separated)'),
    'lap_pos_bot': (u'Traslapes lecho inferior (m desde el inicio)', u'Bottom layer laps (m from the start)'),
    'mk_elev': (u'Crear una vista en elevación', u'Create an elevation view'),
    'mk_cross': (u'Crear cortes transversales (centro de cada tramo)',
                 u'Create cross sections (middle of each span)'),
    'template': (u'Plantilla', u'Template'),
    'replace': (u'Reemplazar el armado creado antes con esta herramienta',
                u'Replace rebar previously created by this tool'),
    'g_top_main': (u'BARRA PRINCIPAL SUPERIOR', u'TOP MAIN BAR'),
    'g_bot_main': (u'BARRA PRINCIPAL INFERIOR', u'BOTTOM MAIN BAR'),
    'top_style': (u'Estilo de barra superior', u'Top bar style'),
    'bot_style': (u'Estilo de barra inferior', u'Bottom bar style'),
    'side_style': (u'Estilo', u'Style'),
    'side_sp': (u'o sep. máx.', u'or max. spacing'),
    'side_hook': (u'Gancho en extremos', u'End hooks'),
    'st_mode': (u'Distribución', u'Layout'),
    'stir_kind': (u'Estribo', u'Stirrup'),
    'joint_L': (u'Cambio de sección: anclaje recto', u'Section change: straight anchorage'),
    'axis_mode': (u'Cambio de eje en apoyos', u'Axis change at supports'),
    'st_left': (u'Desde el apoyo inicial', u'From the start support'),
    'st_left_hint': (u'(n@sep en cm)', u'(n@spacing in cm)'),
    'st_mid': (u'Centro @', u'Middle @'),
    'st_mid_hint': (u'(separación máxima)', u'(maximum spacing)'),
    'st_right': (u'Desde el apoyo final', u'From the end support'),
    'st_right_hint': (u'(vacío = igual)', u'(empty = same)'),
    'top_single_hint': (u'Una sola varilla corrida de extremo a extremo de la viga.',
                        u'One continuous bar from end to end of the beam.'),
    'g_top_cfg': (u'Configuración de barra superior', u'Top bar settings'),
    'g_bot_cfg': (u'Configuración de barra inferior', u'Bottom bar settings'),
    'bar': (u'Barra', u'Bar'),
    'apply_all': (u'Aplicar a todos los tramos', u'Apply to all spans'),
    'g_start': (u'INICIO', u'START'),
    'g_mid': (u'CENTRO', u'CENTER'),
    'g_end': (u'FINAL', u'END'),
    'layer': (u'Capa', u'Layer'),
    'add_layer': (u'Agregar capa', u'Add layer'),
    'del_layer': (u'Borrar capa', u'Delete layer'),
    'support': (u'Apoyo', u'Support'),
    'location': (u'Ubicación', u'Location'),
    'no_inner': (u'Sin apoyos intermedios (viga de un tramo).', u'No intermediate supports (single span).'),
    'g_bot_add': (u'BARRA INFERIOR ADICIONAL', u'ADDITIONAL BOTTOM BAR'),
    'span': (u'Tramo', u'Span'),
    'g_side': (u'BARRA CENTRAL', u'SIDE (SKIN) BARS'),
    'side_on': (u'Añadir barra central', u'Add side bars'),
    'rows': (u'Filas', u'Rows'),
    'g_section': (u'SECCIÓN', u'SECTION'),
    'g_stir': (u'ESTRIBOS', u'STIRRUPS'),
    'st_on': (u'Crear estribos', u'Create stirrups'),
    'hook_type': (u'Tipo de gancho', u'Hook type'),
    'hook_len': (u'Longitud de gancho', u'Hook length'),
    'lc': (u'Zona confinada', u'Confined zone'),
    'lc_hint': (u'(en cada extremo del tramo)', u'(at each end of the span)'),
    'sc': (u'Separación confinada', u'Confined spacing'),
    'sm': (u'Separación al centro', u'Middle spacing'),
    'first': (u'Primer estribo a', u'First stirrup at'),
    'first_hint': (u'(desde el paño del apoyo)', u'(from the support face)'),
    'save': (u'Guardar', u'Save'),
    'import': (u'Importar', u'Import'),
    'reset': (u'Nuevo armado', u'New reinforcement'),
}

TIPS = {
    'tbTopLna': (u'Ln*: prolongación adicional en el extremo inicial (+ alarga, − acorta). '
                 u'0 = hasta el paño exterior del apoyo menos el recubrimiento.',
                 u'Ln*: extra extension at the start end (+ longer, − shorter). '
                 u'0 = to the outer face of the support minus cover.'),
    'tbTopLa': (u'La: longitud del doblez a 90° en el extremo inicial (0 = recta).',
                u'La: 90° bend leg length at the start (0 = straight).'),
    'cbTopStyle': (u'Barra única: una varilla de extremo a extremo. Múltiple: una por tramo, '
                   u'anclada en los apoyos.', u'Single: one bar end to end. Multiple: one per span.'),
    'tbTsL': (u'L: longitud dentro del claro, medida desde el paño del apoyo.',
              u'L: length into the span, measured from the support face.'),
    'tbTmL1': (u'L1 / L2: longitud a cada lado, medida desde el EJE del apoyo o desde el CENTRO del tramo, '
               u'según la ubicación elegida (como en los planos).',
               u'L1 / L2: length on each side, from the support AXIS or the span CENTER (per location).'),
    'tbTmL2': (u'L1 / L2: longitud a cada lado, medida desde el EJE del apoyo o desde el CENTRO del tramo, '
               u'según la ubicación elegida (como en los planos).',
               u'L1 / L2: length on each side, from the support AXIS or the span CENTER (per location).'),
    'cbTmSup': (u'Dónde va el bastón: sobre un apoyo intermedio (o cambio de sección) o en el centro de un tramo.',
                u'Where the bar goes: over an inner support (or section change) or at the middle of a span.'),
    'cbBmSup': (u'Dónde va el bastón: sobre un apoyo intermedio (o cambio de sección) o en el centro de un tramo.',
                u'Where the bar goes: over an inner support (or section change) or at the middle of a span.'),
    'tbBsL': (u'L: longitud dentro del claro, medida desde el paño del apoyo.',
              u'L: length into the span, measured from the support face.'),
    'tbBeL': (u'L: longitud dentro del claro, medida desde el paño del apoyo.',
              u'L: length into the span, measured from the support face.'),
    'tbTopLj0': (u'Anc.*: en un cambio de sección al inicio de este tramo/zona, largo recto que la varilla entra '
                 u'al otro lado si cabe en su sección (medido desde el cambio o el paño del apoyo). Vacío = el de '
                 u'Configuración. Si no cabe, termina con la pata La (0 = 12 db).',
                 u'Anc.*: straight length into the other side at a section change at the start (empty = general).'),
    'tbTopLj1': (u'Ancᵇ: igual, en el cambio de sección al final del tramo/zona. Si no cabe, pata Lb.',
                 u'Ancᵇ: same, at a section change at the end.'),
    'tbBotLj0': (u'Anc.*: en un cambio de sección al inicio de este tramo/zona, largo recto que la varilla entra '
                 u'al otro lado si cabe en su sección (medido desde el cambio o el paño del apoyo). Vacío = el de '
                 u'Configuración. Si no cabe, termina con la pata La (0 = 12 db).',
                 u'Anc.*: straight length into the other side at a section change at the start (empty = general).'),
    'tbBotLj1': (u'Ancᵇ: igual, en el cambio de sección al final del tramo/zona. Si no cabe, pata Lb.',
                 u'Ancᵇ: same, at a section change at the end.'),
    'cbStSpanBt': (u'Diámetro del estribo en este tramo. "General" usa el Ø de Configuración.',
                   u'Stirrup bar in this span. "General" uses the one in Settings.'),
    'cbStDouble': (u'Sencillo: un estribo perimetral. Perimetral + interior: EST.1 perimetral y EST.2 cerrado '
                   u'desde un lado hasta la penúltima varilla del lecho. Dos traslapados: cada uno abarca poco '
                   u'más de la mitad y se traslapan al centro. El EST.2 va un diámetro a un lado del EST.1.',
                   u'Single: one perimeter stirrup. Perimeter + inner: EST.2 closed around the inner bars. '
                   u'Two overlapping: each covers a bit more than half.'),
    'tbJointL': (u'En los apoyos donde cambia la sección (ancho, peralte o nivel), las varillas que caben en la '
                 u'sección del otro tramo siguen rectas esta longitud desde el paño; las que no caben terminan '
                 u'en el paño lejano del apoyo con gancho a 90° (12 db).',
                 u'At supports where the section changes, bars that fit in the other span continue straight this '
                 u'length from the face; the rest end at the far face with a 90° hook.'),
    'tbBmL1': (u'L1 / L2: longitud a cada lado, medida desde el EJE del apoyo o desde el CENTRO del tramo, '
               u'según la ubicación elegida.',
               u'L1 / L2: length on each side, from the support AXIS or the span CENTER (per location).'),
    'tbBmL2': (u'L1 / L2: longitud a cada lado, medida desde el EJE del apoyo o desde el CENTRO del tramo, '
               u'según la ubicación elegida.',
               u'L1 / L2: distance from each support face to the start/end of the bar.'),
    'tbLapPosTop': (u'Posición (m, desde el inicio de la viga) donde empieza cada traslape de las varillas superiores. '
                    u'Lo usual: al centro del claro.', u'Where each top-bar lap starts (m from the beam start). Usually at mid-span.'),
    'tbLapPosBot': (u'Posición (m) donde empieza cada traslape de las varillas inferiores. Lo usual: cerca de los apoyos.',
                    u'Where each bottom-bar lap starts (m). Usually near the supports.'),
    'cbLapStyle': G.LP.STYLE_TIP,
    'cbAxisMode': (u'Contratrabes desplazadas o giradas (como en el plano): en el apoyo donde cambia el eje, '
                   u'"Doblar en planta" deja las varillas corridas y las dobla dentro del apoyo para pasar al eje '
                   u'de la siguiente (una pieza con gancho que cruce el doblez se traslapa en el apoyo); '
                   u'"Anclar recta en el apoyo" corta cada lado recto hasta el paño lejano del apoyo (gancho a '
                   u'90° si el apoyo es más corto que el anclaje recto). Solo se activa si la viga cambia de eje.',
                   u'Offset or rotated beams: at the support where the axis changes, "Bend in plan" keeps the bars '
                   u'continuous and bends them inside the support; "Anchor straight" stops each side straight at '
                   u'the far face of the support (90° hook if the support is shorter than the anchorage).'),
    'cbBotStyle': (u'Barra única: una varilla corrida de extremo a extremo (con traslapes sobre los apoyos '
                   u'si pasa de la longitud comercial). Múltiple: una por tramo, anclada en los apoyos.',
                   u'Single: one continuous bar end to end (lapped over supports if longer than stock). '
                   u'Multiple: one per span.'),
    'cbSideStyle': (u'Barra única: acero de temperatura corrido de extremo a extremo, con traslapes centrados '
                    u'en los apoyos intermedios y gancho en los extremos. Múltiple: una por tramo.',
                    u'Single: continuous skin bars end to end, lapped over inner supports, hooked at the ends. '
                    u'Multiple: one per span.'),
    'tbSideSp': (u'Separación máxima entre filas (m). Si se escribe, el número de filas se calcula solo '
                 u'(p. ej. 0.30 para acero de temperatura @30). Vacío = usar Filas.',
                 u'Maximum spacing between rows (m). If set, the number of rows is computed. Empty = use Rows.'),
    'cbSideHook': (u'Gancho a 90° en los dos extremos de la viga: vertical hacia abajo (en el plano de la cara) '
                   u'u horizontal hacia el centro de la viga.',
                   u'90° hook at both beam ends: vertical downwards or horizontal toward the beam axis.'),
    'tbSideHookL': (u'Longitud de la pata del gancho (m).', u'Hook leg length (m).'),
    'cbStMode': (u'Zona confinada: separación uniforme en cada extremo y otra al centro. Por grupos: como en los '
                 u'planos (10@10,5@15,5@20 y @30 al centro), en cada tramo.',
                 u'Confined zone: uniform spacing at each end and another in the middle. Groups: as on drawings.'),
    'tbStLeft': (u'Grupos desde el paño del apoyo inicial del tramo: número@separación en cm, separados por '
                 u'coma. El primer estribo va a "Primer estribo a".',
                 u'Groups from the start support face: count@spacing in cm, comma separated.'),
    'tbStMid': (u'Separación máxima al centro (cm); el hueco central se reparte en partes iguales.',
                u'Maximum middle spacing (cm); the middle gap is split evenly.'),
    'tbStRight': (u'Grupos desde el paño del apoyo final. Vacío = igual que el inicial; 0 = sin grupos.',
                  u'Groups from the end support face. Empty = same as start; 0 = none.'),
    'tbTmin': (u'Separación libre mínima entre varillas y entre lechos.',
               u'Minimum clear spacing between bars and layers.'),
    'tbSideExl': (u'Prolongación dentro del apoyo izquierdo (0 = desde el paño).',
                  u'Extension into the left support (0 = from the face).'),
    'btnTsAdd': (u'Capa 1 va entre las varillas corridas si caben; las siguientes en lechos interiores '
                 u'separados t min.', u'Layer 1 goes between main bars if it fits; next layers in inner rows.'),
    'elevCanvas': (u'Clic sobre un tramo para seleccionarlo', u'Click a span to select it'),
    'btnSave': (u'Guardar esta configuración con un nombre para reutilizarla',
                u'Save these settings with a name to reuse them'),
    'btnImport': (u'Cargar una configuración guardada', u'Load saved settings'),
    'btnReset': (u'Borra la configuración cargada (la del armado existente o la capturada) y empieza con los '
                 u'valores por defecto. El armado del modelo no cambia hasta pulsar OK; con «Reemplazar» se borra '
                 u'el anterior.',
                 u'Clears the loaded settings (from the existing rebar or typed in) and starts from the defaults. '
                 u'The model rebar does not change until OK; with "Replace" the previous one is deleted.'),
}

DYN = {
    'single': (u'Barra única', u'Single bar'),
    'multi': (u'Múltiple (por tramo)', u'Multiple (per span)'),
    'none': (u'<Ninguna>', u'<None>'),
    'span_n': (u'Tramo %d', u'Span %d'),
    'yes': (u'Sí', u'Yes'),
    'no': (u'No', u'No'),
    'h_main': ([u'Barra Nº', u'Barra', u'No', u'La', u'Ln*', u'Lb', u'Lnᵇ'],
               [u'Bar Nº', u'Bar', u'No', u'La', u'Ln*', u'Lb', u'Lnᵇ']),
    'h_end': ([u'Capa', u'Barra', u'n', u'La', u'L', u'Ln'], [u'Layer', u'Bar', u'n', u'La', u'L', u'Ln']),
    'h_mid': ([u'Capa', u'Barra', u'n', u'L1', u'L2'], [u'Layer', u'Bar', u'n', u'L1', u'L2']),
    'h_side': ([u'Tramo', u'Añade', u'Barra', u'Filas', u'ExL', u'ExR'],
               [u'Span', u'Add', u'Bar', u'Rows', u'ExL', u'ExR']),
    'h_stg': ([u'Tramo', u'Ø', u'Estribo', u'Desde el inicio', u'Centro', u'Desde el final'],
              [u'Span', u'Ø', u'Stirrup', u'From start', u'Middle', u'From end']),
    'stir_kinds': ([u'Sencillo', u'Doble: perimetral + interior', u'Doble: dos traslapados'],
                   [u'Single', u'Double: perimeter + inner', u'Double: two overlapping']),
    'stir_kinds_s': ([u'Sencillo', u'Per. + int.', u'Traslapados'], [u'Single', u'Per. + inner', u'Overlap']),
    'general': (u'General (%s)', u'General (%s)'),
    'st_span_lbl': (u'Ø estribo — tramo %d', u'Stirrup Ø — span %d'),
    'axis_modes': ([u'Doblar en planta', u'Anclar recta en el apoyo'],
                   [u'Bend in plan', u'Anchor straight in the support']),
    'side_hooks': ([u'Sin gancho', u'Vertical, hacia abajo', u'Horizontal, hacia el centro'],
                   [u'No hook', u'Vertical, downwards', u'Horizontal, toward the axis']),
    'st_modes': ([u'Zona confinada (uniforme)', u'Por grupos desde cada apoyo'],
                 [u'Confined zone (uniform)', u'Groups from each support']),
    'same': (u'(igual)', u'(same)'),
    'side_info': (u'%d fila(s) a %s m entre el lecho superior y el inferior · %s',
                  u'%d row(s) at %s m between top and bottom layers · %s'),
    'side_laps': (u'%d traslape(s) de %s m centrados en apoyos', u'%d lap(s) of %s m centered on supports'),
    'side_nolap': (u'sin traslapes', u'no laps'),
    'st_info_g': (u'%d estribos en %d tramo(s): grupos desde cada apoyo y el centro repartido a la separación '
                  u'máxima indicada.', u'%d stirrups in %d span(s): groups from each support, middle evenly split.'),
    'cfg_span': (u' — tramo %d', u' — span %d'),
    'cfg_zone': (u' — zona %d', u' — zone %d'),
    'zone_hint': (u'Una varilla corrida por zona de sección (Z1, Z2…). Donde la zona siguiente usa la misma '
                  u'varilla y queda en el mismo lugar, sigue corrida. Una zona con "=" usa la de Z1.',
                  u'One continuous bar per section zone. It stays continuous where the next zone uses the same bar '
                  u'at the same place. "=" zones use Z1.'),
    'change': (u'cambio', u'change'),
    'ba_span': (u'Claro libre %s m · apoyos %s → %s', u'Clear span %s m · supports %s → %s'),
    'loc_sup': (u'Apoyo %s', u'Support %s'),
    'loc_chg': (u'Cambio %s', u'Change %s'),
    'loc_span': (u'Centro tramo %d', u'Middle of span %d'),
    'axis': (u'eje', u'axis'),
    'center': (u'centro', u'center'),
    'free': (u'libre', u'free'),
    'elev_hint': (u'Alzado · clic en un tramo para seleccionarlo', u'Elevation · click a span to select it'),
    'sec_title': (u'Tramo %d · centro', u'Span %d · middle'),
    'w_title': (u'Peso aprox. (kg): ', u'Approx. weight (kg): '),
    'w_parts': (u'corridos %.1f · bastones sup. %.1f · bastones inf. %.1f · piel %.1f · estribos %.1f (%d pzas) · total %.1f',
                u'main %.1f · top extra %.1f · bottom extra %.1f · skin %.1f · stirrups %.1f (%d pcs) · total %.1f'),
    'st_info': (u'%d estribos en %d tramo(s). Se crean por zonas (confinada / centro / confinada) '
                u'entre los paños de los apoyos.',
                u'%d stirrups in %d span(s), created by zones between support faces.'),
    'bad': (u'Revise los valores marcados en rojo.', u'Check the values marked in red.'),
    'empty': (u'No hay nada que crear.', u'Nothing to create.'),
    'saved': (u'Configuración guardada: %s', u'Settings saved: %s'),
    'loaded': (u'Configuración importada: %s', u'Settings imported: %s'),
    'remap': (u' · tipos sustituidos: %s', u' · types replaced: %s'),
    'save_prompt': (u'Nombre de la configuración', u'Settings name'),
    'overwrite': (u'Ya existe "%s". ¿Reemplazarla?', u'"%s" already exists. Replace it?'),
    'last': (u'(última usada)', u'(last used)'),
    'browse': (u'Buscar archivo…', u'Browse file…'),
    'imp_title': (u'Importar configuración', u'Import settings'),
    'no_saved': (u'No hay configuraciones guardadas.', u'No saved settings.'),
    'warn_n': (u'%s  (+%d avisos más)', u'%s  (+%d more warnings)'),
    'no_layer_sel': (u'Seleccione una capa en la tabla para borrarla (o se borra la última).',
                     u'Select a layer in the table to delete it (or the last one is deleted).'),
    'err_load': (u'No se pudo importar: %s', u'Could not import: %s'),
}

KIND_COLOR = {'top_main': '#3DA5F5', 'bot_main': '#3DA5F5', 'top_add': '#F0A030',
              'bot_add': '#C77DFF', 'side': '#4DD0E1'}
C = {'concrete': '#3A3A3E', 'conc_line': '#7A7A7E', 'support': '#2C2C30', 'sup_line': '#8A8A8A',
     'st': '#7CC576', 'st_elev': '#56704F', 'text': '#A8A8A8', 'dim': '#8C8C8C', 'sel': '#2A3E55',
     'red': '#D9534F', 'tb_border': '#555558', 'bright': '#E6E6E6', 'row_sel': '#24527D',
     'row_alt': '#232327', 'hdr': '#34343A'}

COVERS = [2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.5]
KG_LAST = u'_ultima'

_BC = BrushConverter()
_BR = {}


def brush(h):
    b = _BR.get(h)
    if b is None:
        b = _BC.ConvertFromString(h)
        try:
            b.Freeze()
        except Exception:
            pass
        _BR[h] = b
    return b


def _dash(v):
    dc = DoubleCollection()
    for x in v:
        dc.Add(float(x))
    return dc


def d_line(cv, x1, y1, x2, y2, col, th=1.0, dash=None):
    ln = Line()
    ln.X1, ln.Y1, ln.X2, ln.Y2 = float(x1), float(y1), float(x2), float(y2)
    ln.Stroke = brush(col)
    ln.StrokeThickness = float(th)
    if dash:
        ln.StrokeDashArray = _dash(dash)
    ln.IsHitTestVisible = False
    cv.Children.Add(ln)


def d_circle(cv, x, y, r, stroke=None, fill=None, th=1.0):
    e = Ellipse()
    e.Width = e.Height = 2.0 * r
    if stroke:
        e.Stroke = brush(stroke)
        e.StrokeThickness = float(th)
    if fill:
        e.Fill = brush(fill)
    e.IsHitTestVisible = False
    Canvas.SetLeft(e, float(x - r))
    Canvas.SetTop(e, float(y - r))
    cv.Children.Add(e)


def d_rect(cv, x, y, w, h, stroke=None, fill=None, th=1.0, dash=None, radius=0.0):
    r = Rectangle()
    r.Width = max(float(w), 0.0)
    r.Height = max(float(h), 0.0)
    if stroke:
        r.Stroke = brush(stroke)
        r.StrokeThickness = float(th)
    if fill:
        r.Fill = brush(fill)
    if dash:
        r.StrokeDashArray = _dash(dash)
    if radius:
        r.RadiusX = r.RadiusY = float(radius)
    r.IsHitTestVisible = False
    Canvas.SetLeft(r, float(x))
    Canvas.SetTop(r, float(y))
    cv.Children.Add(r)


def d_poly(cv, pts, col, th=1.0, closed=False, dash=None):
    pc = PointCollection()
    for p in pts:
        pc.Add(Point(float(p[0]), float(p[1])))
    s = Polygon() if closed else Polyline()
    s.Points = pc
    s.Stroke = brush(col)
    s.StrokeThickness = float(th)
    s.StrokeLineJoin = PenLineJoin.Round
    if dash:
        s.StrokeDashArray = _dash(dash)
    s.IsHitTestVisible = False
    cv.Children.Add(s)


def d_text(cv, x, y, text, col, size=11.0, anchor='lt', bold=False):
    tb = TextBlock()
    tb.Text = text
    tb.Foreground = brush(col)
    tb.FontSize = float(size)
    if bold:
        tb.FontWeight = FontWeights.SemiBold
    tb.IsHitTestVisible = False
    tb.Measure(Size(Double.PositiveInfinity, Double.PositiveInfinity))
    w, h = tb.DesiredSize.Width, tb.DesiredSize.Height
    if anchor[0] == 'c':
        x -= w / 2.0
    elif anchor[0] == 'r':
        x -= w
    if anchor[1] == 'c':
        y -= h / 2.0
    elif anchor[1] == 'b':
        y -= h
    Canvas.SetLeft(tb, float(x))
    Canvas.SetTop(tb, float(y))
    cv.Children.Add(tb)


def fm(cm):
    """cm -> texto en m."""
    if cm is None:
        return u'-'
    return u'%.2f' % (cm / 100.0)


def fcm(v):
    s = u'%.1f' % v
    return s[:-2] if s.endswith(u'.0') else s


# ================================================================ tabla
class Table(object):
    """Tabla simple (encabezado + filas seleccionables) dentro de un Border."""

    def __init__(self, host, widths, on_select=None, toggle=True):
        self.host = host
        self.widths = widths
        self.on_select = on_select
        self.toggle = toggle
        self.sel = -1
        self.n = 0
        root = DockPanel()
        self.header = Border()
        self.header.Background = brush(C['hdr'])
        DockPanel.SetDock(self.header, Dock.Top)
        root.Children.Add(self.header)
        sv = ScrollViewer()
        sv.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        sv.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto
        self.body = StackPanel()
        sv.Content = self.body
        root.Children.Add(sv)
        host.Child = root

    def _grid(self, values, bold=False):
        g = Grid()
        for w in self.widths:
            cd = ColumnDefinition()
            cd.Width = GridLength(float(w))
            g.ColumnDefinitions.Add(cd)
        for i, v in enumerate(values):
            if i >= len(self.widths):
                break
            tb = TextBlock()
            tb.Text = u'%s' % v
            tb.Margin = Thickness(5, 3, 3, 3)
            tb.Foreground = brush(C['bright'])
            if bold:
                tb.FontWeight = FontWeights.SemiBold
                tb.FontSize = 11.0
            Grid.SetColumn(tb, i)
            g.Children.Add(tb)
        return g

    def set_header(self, names):
        self.header.Child = self._grid(names, True)

    def set_rows(self, rows, sel=None):
        self.body.Children.Clear()
        self.n = len(rows)
        for i, r in enumerate(rows):
            bd = Border()
            bd.Child = self._grid(r)
            bd.Tag = i
            bd.MouseLeftButtonDown += self._click
            self.body.Children.Add(bd)
        if sel is not None:
            self.sel = sel
        if self.sel >= self.n:
            self.sel = self.n - 1 if not self.toggle else -1
        self._paint()

    def _paint(self):
        for i, bd in enumerate(self.body.Children):
            if i == self.sel:
                bd.Background = brush(C['row_sel'])
            else:
                bd.Background = brush(C['row_alt'] if i % 2 else '#1F1F22')

    def _click(self, sender, args):
        i = int(sender.Tag)
        if self.toggle and i == self.sel:
            i = -1
        self.select(i, True)

    def select(self, i, fire=True):
        self.sel = i
        self._paint()
        if fire and self.on_select is not None:
            self.on_select(i)


# ================================================================ ventana
LAYER_END = ('Ts', 'Te', 'Bs', 'Be')
LAYER_MID = ('Tm', 'Bm')


class BeamRebarWindow(forms.WPFWindow):
    """ctx: model (G.BeamModel), bar_types [{name,dm,dn,...}], hooks_stir [{name,angle}],
    hook_len {(bar_name, hook_name): cm}, templates [nombres], beam_desc (texto)."""

    def __init__(self, xaml_file, ctx):
        try:
            forms.WPFWindow.__init__(self, xaml_file, handle_esc=False)
        except TypeError:
            forms.WPFWindow.__init__(self, xaml_file)
        self._ready = False
        self._mute = True
        self.ctx = ctx
        self.model = ctx['model']
        self.bts_list = ctx['bar_types']
        self.bts = dict((b['name'], b) for b in self.bts_list)
        self.names = [b['name'] for b in self.bts_list]
        self.hooks = ctx['hooks_stir']
        self.hook_len = ctx['hook_len']
        self.templates = ctx.get('templates', [])
        self.lang = 0
        self.page = 0
        self.cur_span = 0
        self.cur_loc = {'T': 0, 'B': 0}
        self.result = None
        self._bad = set()
        self.res = None
        self.cfg = G.fit_cfg(ctx.get('cfg') or {}, self.model, self.bts_list)
        if not self.cfg['stirrups'].get('hook') and self.hooks:
            h = [x for x in self.hooks if abs(x['angle'] - 135.0) < 1.0]
            self.cfg['stirrups']['hook'] = (h[0] if h else self.hooks[0])['name']

        self._build_tables()
        self._init_controls()
        self._wire()
        ML.setup(self)
        self._apply_language()
        self._load_all_editors()
        self._ready = True
        self._mute = False
        self._refresh_all()
        self.nav0.IsChecked = True

    # ------------------------------------------------------------ util
    def T(self, key):
        return DYN[key][self.lang]

    def _cb_fill(self, cb, items, sel=0):
        cb.Items.Clear()
        for it in items:
            cb.Items.Add(it)
        if items:
            cb.SelectedIndex = max(0, min(sel, len(items) - 1))

    def _bt_idx(self, name):
        try:
            return self.names.index(name)
        except ValueError:
            return 0

    def _bt_of(self, cb):
        i = cb.SelectedIndex
        return self.names[i] if 0 <= i < len(self.names) else (self.names[0] if self.names else u'')

    def _mark(self, tb, ok):
        nm = tb.Name
        if ok:
            if nm in self._bad:
                self._bad.discard(nm)
                tb.BorderBrush = brush(C['tb_border'])
        elif nm not in self._bad:
            self._bad.add(nm)
            tb.BorderBrush = brush(C['red'])

    def _m(self, tb, minv=None, maxv=None):
        """Lee un campo en m y devuelve cm (None si invalido)."""
        v = G.parse_num(tb.Text, None)
        ok = v is not None and (minv is None or v >= minv) and (maxv is None or v <= maxv)
        self._mark(tb, ok)
        return v * 100.0 if ok else None

    def _setm(self, tb, cm):
        tb.Text = fm(cm)
        self._mark(tb, True)

    def _span_len(self, i):
        a, b = self.model.span_faces(i)
        return max(b - a, 0.0)

    def _span_names(self):
        return [self.T('span_n') % (i + 1) for i in range(self.model.n_spans)]

    def _node_name(self, n):
        return n.name if not n.free else self.T('free')

    # ------------------------------------------------------------ init
    def _build_tables(self):
        self.tb_top = Table(self.tblTopMain, [62, 120, 36, 52, 52, 52, 52], self._on_top_row, toggle=False)
        self.tb_bot = Table(self.tblBotMain, [62, 120, 36, 52, 52, 52, 52], self._on_bot_row, toggle=False)
        self.tb_side = Table(self.tblSide, [52, 52, 120, 44, 52, 52], self._on_side_row, toggle=False)
        self.tb_stg = Table(self.tblStGroups, [40, 44, 92, 124, 46, 104], self._on_side_row, toggle=False)
        self.tb_layers = {}
        for p in LAYER_END:
            self.tb_layers[p] = Table(getattr(self, 'tbl' + p), [40, 96, 26, 42, 42, 42],
                                      self._mk_layer_sel(p))
        for p in LAYER_MID:
            self.tb_layers[p] = Table(getattr(self, 'tbl' + p), [40, 96, 26, 46, 46],
                                      self._mk_layer_sel(p))
        self.tb_top.sel = 0
        self.tb_bot.sel = 0
        self.tb_side.sel = 0
        self.tb_stg.sel = 0

    def _init_controls(self):
        self._cb_fill(self.cbLang, [u'ESP', u'ENG'], 0)
        ns = [u'%d' % k for k in range(1, 13)]
        for cb in (self.cbTopBt, self.cbBotBt, self.cbSideBt, self.cbStirBt,
                   self.cbTsBt, self.cbTmBt, self.cbTeBt, self.cbBsBt, self.cbBmBt, self.cbBeBt):
            self._cb_fill(cb, self.names, 0)
        for cb in (self.cbTopN, self.cbBotN, self.cbTsN, self.cbTmN, self.cbTeN,
                   self.cbBsN, self.cbBmN, self.cbBeN):
            self._cb_fill(cb, ns, 0)
        self._cb_fill(self.cbSideRows, [u'%d' % k for k in range(1, G.SIDE_ROWS_MAX + 1)], 0)
        self._cb_fill(self.cbStHook, [h['name'] for h in self.hooks], 0)
        self._fill_cover()
        self._fill_templates()
        self._fill_span_combos()
        # valores por defecto de los editores de capas
        main_bt = self.cfg['top_single']['bt']
        db = self.bts[main_bt]['dn'] if main_bt in self.bts else 1.6
        leg = G.round_up(12 * db, 5.0)
        L0 = self._span_len(0)
        for p in LAYER_END:
            getattr(self, 'cb%sBt' % p).SelectedIndex = self._bt_idx(main_bt)
            self._setm(getattr(self, 'tb%sLa' % p), leg)
            self._setm(getattr(self, 'tb%sL' % p), G.round_up(L0 * (0.3 if p[0] == 'T' else 0.25), 5.0))
            self._setm(getattr(self, 'tb%sLn' % p), 0.0)
        for p in LAYER_MID:
            getattr(self, 'cb%sBt' % p).SelectedIndex = self._bt_idx(main_bt)
            v = self._loc_default(p[0])
            self._setm(getattr(self, 'tb%sL1' % p), v)
            self._setm(getattr(self, 'tb%sL2' % p), v)
        self.layer_sel = dict((p, -1) for p in LAYER_END + LAYER_MID)
        self.txtBeamInfo.Text = self.ctx.get('beam_desc', u'')

    def _fill_cover(self):
        vals = list(COVERS)
        c = float(self.cfg['cover'])
        if all(abs(c - v) > 1e-6 for v in vals):
            vals.append(c)
            vals.sort()
        self.cover_vals = vals
        self._cb_fill(self.cbCover, [u'R=%scm' % fcm(v) for v in vals],
                      [i for i, v in enumerate(vals) if abs(v - c) < 1e-6][0])

    def _fill_templates(self):
        items = [self.T('none')] + list(self.templates)
        v = self.cfg['views']
        for cb, key in ((self.cbElevTpl, 'elev_tpl'), (self.cbCrossTpl, 'cross_tpl')):
            name = v.get(key) or u''
            idx = self.templates.index(name) + 1 if name in self.templates else 0
            self._cb_fill(cb, items, idx)

    def _locations(self):
        """Ubicaciones de CENTRO: apoyos intermedios (y cambios de seccion) y centros de tramo."""
        out = []
        for j, ni in enumerate(self.model.inner_supports):
            nd = self.model.nodes[ni]
            out.append(('mid', j, (self.T('loc_chg') if nd.kind == u'cambio' else self.T('loc_sup')) % nd.name))
        for i in range(self.model.n_spans):
            out.append(('span', i, self.T('loc_span') % (i + 1)))
        return out

    def _loc(self, g):
        locs = self._locations()
        k = max(0, min(self.cur_loc.get(g, 0), len(locs) - 1))
        return locs[k]

    def _loc_default(self, g):
        """L1 = L2 inicial segun la ubicacion: sobre apoyo ~0.3 del claro + medio apoyo; en centro de
        tramo ~0.15 del claro."""
        kind, idx, _ = self._loc(g)
        if kind == 'mid':
            ni = self.model.inner_supports[idx]
            L = max(self._span_len(ni - 1), self._span_len(ni))
            return G.round_up(L * (0.3 if g == 'T' else 0.2) + self.model.nodes[ni].width / 2.0, 5.0)
        return G.round_up(self._span_len(idx) * 0.15, 5.0)

    def _fill_span_combos(self):
        items = [t for _, _, t in self._locations()]
        for g in ('T', 'B'):
            self.cur_loc[g] = max(0, min(self.cur_loc.get(g, 0), len(items) - 1))
            self._cb_fill(getattr(self, 'cb%smSup' % g), items, self.cur_loc[g])
            getattr(self, 'txt%smNone' % g).Visibility = Visibility.Collapsed
            self._loc_drawing(g)

    def _loc_drawing(self, g):
        kind = self._loc(g)[0]
        getattr(self, 'shp%smSup' % g).Visibility = Visibility.Visible if kind == 'mid' else Visibility.Collapsed
        getattr(self, 'txt%smAxis' % g).Text = self.T('axis') if kind == 'mid' else self.T('center')

    def _wire(self):
        self.titleBar.MouseLeftButtonDown += self._on_drag
        self.btnClose.Click += self._on_close
        self.btnOK.Click += self._on_ok
        self.btnSave.Click += self._on_save
        self.btnImport.Click += self._on_import
        self.btnReset.Click += self._on_reset
        self.cbLang.SelectionChanged += self._on_lang
        for i in range(6):
            getattr(self, 'nav%d' % i).Checked += self._mk_nav(i)

        def ev(group):
            return lambda s, a: self._on_edit(group)

        # configuracion
        for c in (self.cbStirBt, self.cbCover, self.cbElevTpl, self.cbCrossTpl):
            c.SelectionChanged += ev('Cfg')
        for c in (self.tbTmin, self.tbPartition, self.tbLap, self.tbStock, self.tbLapPosTop, self.tbLapPosBot,
                  self.tbJointL):
            c.TextChanged += ev('Cfg')
        for c in (self.chkElev, self.chkCross, self.chkStock):
            c.Click += ev('Cfg')
        self.cbLapStyle.SelectionChanged += ev('Cfg')
        self.cbAxisMode.SelectionChanged += self._on_axis_mode
        # corridos
        self.cbTopStyle.SelectionChanged += self._on_top_style
        self.cbBotStyle.SelectionChanged += self._on_bot_style
        self.cbSideStyle.SelectionChanged += self._on_side_style
        for g in ('Top', 'Bot'):
            for n in ('Bt', 'N'):
                getattr(self, 'cb%s%s' % (g, n)).SelectionChanged += ev(g)
            for n in ('La', 'Lb', 'Lna', 'Lnb', 'Lj0', 'Lj1'):
                getattr(self, 'tb%s%s' % (g, n)).TextChanged += ev(g)
        self.btnTopAll.Click += lambda s, a: self._apply_all('Top')
        self.btnBotAll.Click += lambda s, a: self._apply_all('Bot')
        # capas
        for p in LAYER_END + LAYER_MID:
            getattr(self, 'cb%sBt' % p).SelectionChanged += ev(p)
            getattr(self, 'cb%sN' % p).SelectionChanged += ev(p)
            fields = ('La', 'L', 'Ln') if p in LAYER_END else ('L1', 'L2')
            for f in fields:
                getattr(self, 'tb%s%s' % (p, f)).TextChanged += ev(p)
            getattr(self, 'btn%sAdd' % p).Click += self._mk_add(p)
            getattr(self, 'btn%sDel' % p).Click += self._mk_del(p)
        self.cbTmSup.SelectionChanged += lambda s, a: self._on_loc('T')
        self.cbBmSup.SelectionChanged += lambda s, a: self._on_loc('B')
        # piel
        self.chkSideOn.Click += ev('Side')
        for c in (self.cbSideBt, self.cbSideRows, self.cbSideHook):
            c.SelectionChanged += ev('Side')
        for c in (self.tbSideExl, self.tbSideExr, self.tbSideSp, self.tbSideHookL):
            c.TextChanged += ev('Side')
        self.btnSideAll.Click += lambda s, a: self._apply_all('Side')
        # estribos
        self.chkStOn.Click += ev('St')
        self.cbStHook.SelectionChanged += ev('St')
        self.cbStMode.SelectionChanged += ev('St')
        for c in (self.cbStSpanBt, self.cbStDouble):
            c.SelectionChanged += ev('StS')
        for c in (self.tbStLc, self.tbStSc, self.tbStSm, self.tbStFirst):
            c.TextChanged += ev('St')
        for c in (self.tbStLeft, self.tbStMid, self.tbStRight):
            c.TextChanged += ev('StG')
        self.btnStAll.Click += lambda s, a: self._apply_all('StG')
        # dibujo
        for cv in (self.secCanvas0, self.secCanvas4, self.secCanvas5, self.elevCanvas):
            cv.SizeChanged += self._on_resize
        self.elevCanvas.MouseLeftButtonDown += self._on_elev_click

    def _mk_nav(self, i):
        def h(s, a):
            self._show_page(i)
        return h

    def _mk_layer_sel(self, p):
        def h(i):
            self.layer_sel[p] = i
            if i >= 0:
                lst = self._layer_list(p)
                if lst is not None and i < len(lst):
                    self._load_layer_editor(p, lst[i])
        return h

    def _mk_add(self, p):
        return lambda s, a: self._add_layer(p)

    def _mk_del(self, p):
        return lambda s, a: self._del_layer(p)

    # ------------------------------------------------------------ idioma
    def _tagged(self):
        out = []
        stack = [self.Content]
        seen = 0
        while stack and seen < 20000:
            e = stack.pop()
            seen += 1
            if isinstance(e, FrameworkElement):
                tg = e.Tag
                if isinstance(tg, str) and tg.startswith('t:'):
                    out.append((e, tg[2:]))
            if isinstance(e, DependencyObject):
                for ch in LogicalTreeHelper.GetChildren(e):
                    stack.append(ch)
        return out

    def _apply_language(self):
        L = self.lang
        if not hasattr(self, '_tag_cache'):
            self._tag_cache = self._tagged()
        for el, key in self._tag_cache:
            pair = TEXTS.get(key)
            if pair is None:
                continue
            if isinstance(el, GroupBox):
                el.Header = pair[L]
            elif isinstance(el, TextBlock):
                el.Text = pair[L]
            elif isinstance(el, ContentControl):
                el.Content = pair[L]
        for name, pair in TIPS.items():
            el = getattr(self, name, None)
            if el is not None:
                el.ToolTip = pair[L]
        self.tb_top.set_header(self.T('h_main'))
        self.tb_bot.set_header(self.T('h_main'))
        self.tb_side.set_header(self.T('h_side'))
        self.tb_stg.set_header(self.T('h_stg'))
        for p in LAYER_END:
            self.tb_layers[p].set_header(self.T('h_end'))
        for p in LAYER_MID:
            self.tb_layers[p].set_header(self.T('h_mid'))
        mute = self._mute
        self._mute = True
        self._cb_fill(self.cbTopStyle, [self.T('single'), self.T('multi')],
                      0 if self.cfg['top_style'] == 'single' else 1)
        self._cb_fill(self.cbBotStyle, [self.T('single'), self.T('multi')],
                      0 if self.cfg.get('bot_style') == 'single' else 1)
        self._cb_fill(self.cbSideStyle, [self.T('single'), self.T('multi')],
                      0 if self.cfg.get('side_style') == 'single' else 1)
        self._cb_fill(self.cbSideHook, self.T('side_hooks'),
                      G.SIDE_HOOKS.index(self.cfg['side_hook'].get('dir', 'none')))
        self._cb_fill(self.cbStMode, self.T('st_modes'),
                      1 if self.cfg['stirrups'].get('mode') == 'groups' else 0)
        self._fill_axis_mode()
        self._fill_stir_span()
        self._fill_templates()
        self._fill_span_combos()
        self._cb_fill(self.cbLapStyle, G.LP.style_items(self.lang), G.LP.STYLES.index(G.LP.lap_style(self.cfg['lap'])))
        self._mute = mute
        self.txtElevHint.Text = self.T('elev_hint')
        ML.translate(self)
        if self._ready:
            self._refresh_all()

    def _on_lang(self, s, a):
        self.lang = 1 if self.cbLang.SelectedIndex == 1 else 0
        self._apply_language()

    # ------------------------------------------------------------ paginas
    def _show_page(self, i):
        self.page = i
        for k in range(6):
            getattr(self, 'pg%d' % k).Visibility = Visibility.Visible if k == i else Visibility.Collapsed
        try:
            self.UpdateLayout()
        except Exception:
            pass
        if self._ready:
            self._redraw()

    # ------------------------------------------------------------ editores
    def _zone_mode(self, g):
        """'Barra unica' en una viga con cambios de seccion: una configuracion por zona."""
        st = self.cfg['top_style'] if g == 'Top' else self.cfg.get('bot_style')
        return st == 'single' and len(self.model.segments()) > 1

    def _zone_of(self, span):
        for z, (a, b) in enumerate(self.model.segments()):
            if a <= span <= b:
                return z
        return 0

    def _zone_cfg(self, g, z, create=False):
        key = 'top' if g == 'Top' else 'bot'
        if z <= 0:
            return self.cfg[key + '_single']
        lst = self.cfg[key + '_zones']
        if z - 1 >= len(lst) or lst[z - 1] is None:
            if not create or z - 1 >= len(lst):
                return self.cfg[key + '_single']
            lst[z - 1] = copy.deepcopy(self.cfg[key + '_single'])
        return lst[z - 1]

    def _main_target(self, g, create=False):
        if g == 'Top':
            if self.cfg['top_style'] == 'single':
                return self._zone_cfg('Top', self._zone_of(self.cur_span), create)
            i = self.tb_top.sel if self.tb_top.sel >= 0 else 0
            return self.cfg['top_spans'][i]
        if self.cfg.get('bot_style') == 'single':
            return self._zone_cfg('Bot', self._zone_of(self.cur_span), create)
        i = self.tb_bot.sel if self.tb_bot.sel >= 0 else 0
        return self.cfg['bot_spans'][i]

    def _joint_ends(self, g):
        """(inicio, fin) del tramo/zona del editor en un cambio de seccion."""
        m = self.model
        st = self.cfg['top_style'] if g == 'Top' else self.cfg.get('bot_style')
        if st == 'single':
            segs = m.segments()
            z = self._zone_of(self.cur_span)
            return z > 0, z < len(segs) - 1
        tb = self.tb_top if g == 'Top' else self.tb_bot
        i = tb.sel if tb.sel >= 0 else 0
        return m.is_joint(i), m.is_joint(i + 1)

    def _side_target(self):
        if self.cfg.get('side_style') == 'single':
            return self.cfg['side_single']
        return self.cfg['side'][max(self.tb_side.sel, 0)]

    def _load_main_editor(self, g):
        c = self._main_target(g)
        m = self._mute
        self._mute = True
        getattr(self, 'cb%sBt' % g).SelectedIndex = self._bt_idx(c['bt'])
        getattr(self, 'cb%sN' % g).SelectedIndex = max(0, min(11, int(c['n']) - 1))
        for f in ('La', 'Lb', 'Lna', 'Lnb'):
            self._setm(getattr(self, 'tb%s%s' % (g, f)), c[f])
        j0, j1 = self._joint_ends(g)
        for f, on in (('Lj0', j0), ('Lj1', j1)):
            tb = getattr(self, 'tb%s%s' % (g, f))
            v = c.get(f)
            tb.Text = fm(v) if (on and v) else u''
            tb.IsEnabled = on
            self._mark(tb, True)
        self._mute = m
        self._update_cfg_headers()

    def _read_main_editor(self, g):
        c = self._main_target(g, True)
        c['bt'] = self._bt_of(getattr(self, 'cb%sBt' % g))
        c['n'] = getattr(self, 'cb%sN' % g).SelectedIndex + 1
        for f, mn in (('La', 0.0), ('Lb', 0.0), ('Lna', -50.0), ('Lnb', -50.0)):
            v = self._m(getattr(self, 'tb%s%s' % (g, f)), mn, 50.0)
            if v is not None:
                c[f] = v
        for f in ('Lj0', 'Lj1'):
            tb = getattr(self, 'tb%s%s' % (g, f))
            if not tb.IsEnabled:
                continue
            if not (tb.Text or u'').strip():
                self._mark(tb, True)
                c[f] = None
                continue
            v = self._m(tb, 0.0, 10.0)
            if v is not None:
                c[f] = v if v > 0 else None

    def _layer_list(self, p):
        add = self.cfg['top_add' if p[0] == 'T' else 'bot_add']
        if p[1] == 's':
            return add['start']
        if p[1] == 'e':
            return add['end']
        kind, idx, _ = self._loc(p[0])
        lst = add.get(kind) or []
        return lst[idx] if 0 <= idx < len(lst) else None

    def _read_layer_editor(self, p):
        ly = {'bt': self._bt_of(getattr(self, 'cb%sBt' % p)),
              'n': getattr(self, 'cb%sN' % p).SelectedIndex + 1}
        ok = True
        if p in LAYER_END:
            for f, mn in (('La', 0.0), ('L', 0.0), ('Ln', -50.0)):
                v = self._m(getattr(self, 'tb%s%s' % (p, f)), mn, 100.0)
                ok = ok and v is not None
                ly[f] = v
        else:
            for f in ('L1', 'L2'):
                v = self._m(getattr(self, 'tb%s%s' % (p, f)), 0.0, 100.0)
                ok = ok and v is not None
                ly[f] = v
        return ly if ok else None

    def _load_layer_editor(self, p, ly):
        m = self._mute
        self._mute = True
        getattr(self, 'cb%sBt' % p).SelectedIndex = self._bt_idx(ly['bt'])
        getattr(self, 'cb%sN' % p).SelectedIndex = max(0, min(11, int(ly['n']) - 1))
        for f in (('La', 'L', 'Ln') if p in LAYER_END else ('L1', 'L2')):
            self._setm(getattr(self, 'tb%s%s' % (p, f)), ly[f])
        self._mute = m

    def _load_side_editor(self):
        c = self._side_target()
        m = self._mute
        self._mute = True
        self.chkSideOn.IsChecked = bool(c.get('on'))
        self.cbSideBt.SelectedIndex = self._bt_idx(c['bt'])
        self.cbSideRows.SelectedIndex = max(0, min(G.SIDE_ROWS_MAX - 1, int(c['rows']) - 1))
        sp = float(c.get('sp') or 0.0)
        self.tbSideSp.Text = fm(sp) if sp > 0 else u''
        self._mark(self.tbSideSp, True)
        self.cbSideRows.IsEnabled = not sp > 0
        self._setm(self.tbSideExl, c['exl'])
        self._setm(self.tbSideExr, c['exr'])
        sh = self.cfg['side_hook']
        self.cbSideHook.SelectedIndex = G.SIDE_HOOKS.index(sh.get('dir', 'none'))
        self._setm(self.tbSideHookL, sh.get('L') or 0.0)
        self.tbSideHookL.IsEnabled = sh.get('dir', 'none') != 'none'
        self._mute = m

    def _fill_stir_span(self):
        """Combos de Ø y forma del estribo del tramo actual."""
        sp = self.cfg['stirrups']['spans'][self.cur_span]
        m = self._mute
        self._mute = True
        items = [self.T('general') % self.cfg['stir_bt']] + list(self.names)
        idx = self.names.index(sp['bt']) + 1 if sp.get('bt') in self.names else 0
        self._cb_fill(self.cbStSpanBt, items, idx)
        self._cb_fill(self.cbStDouble, self.T('stir_kinds'), G.STIR_KINDS.index(sp.get('double', 'none')))
        self.lblStSpan.Text = self.T('st_span_lbl') % (self.cur_span + 1)
        self._mute = m

    def _span_stir_bt(self, i):
        sp = self.cfg['stirrups']['spans'][i]
        return sp.get('bt') or self.cfg['stir_bt']

    def _load_stg_editor(self):
        self._fill_stir_span()
        g = self.cfg['stirrups']['groups'][self.cur_span]
        m = self._mute
        self._mute = True
        self.tbStLeft.Text = g.get('left') or u''
        self.tbStMid.Text = fcm(float(g.get('mid') or 0.0))
        self.tbStRight.Text = g.get('right') or u''
        for tb in (self.tbStLeft, self.tbStMid, self.tbStRight):
            self._mark(tb, True)
        self._mute = m

    def _load_cfg_page(self):
        m = self._mute
        self._mute = True
        c = self.cfg
        self.cbStirBt.SelectedIndex = self._bt_idx(c['stir_bt'])
        self._fill_cover()
        self._setm(self.tbTmin, c['tmin'])
        self.tbPartition.Text = c.get('partition') or u''
        self.chkElev.IsChecked = bool(c['views'].get('elev'))
        self.chkCross.IsChecked = bool(c['views'].get('cross'))
        lc = c['lap']
        self.tbLap.Text = G.LP.fmt_num(float(lc.get('k', 40.0)))
        self.chkStock.IsChecked = bool(lc.get('stock_on', True))
        self.tbStock.Text = G.LP.fmt_num(float(lc.get('stock', 1200.0)) / 100.0)
        self.tbStock.IsEnabled = bool(lc.get('stock_on', True))
        self._cb_fill(self.cbLapStyle, G.LP.style_items(self.lang), G.LP.STYLES.index(G.LP.lap_style(lc)))
        self.tbLapPosTop.Text = lc.get('pos_top') or u''
        self.tbLapPosBot.Text = lc.get('pos_bot') or u''
        self._setm(self.tbJointL, float((c.get('joint') or {}).get('L', 100.0)))
        self._fill_axis_mode()
        self._fill_templates()
        st = c['stirrups']
        self.chkStOn.IsChecked = bool(st.get('on'))
        names = [h['name'] for h in self.hooks]
        self.cbStHook.SelectedIndex = names.index(st['hook']) if st.get('hook') in names else 0
        self._setm(self.tbStLc, st['lc'])
        self._setm(self.tbStSc, st['sc'])
        self._setm(self.tbStSm, st['sm'])
        self._setm(self.tbStFirst, st['first'])
        self.cbStMode.SelectedIndex = 1 if st.get('mode') == 'groups' else 0
        self.cbTopStyle.SelectedIndex = 0 if c['top_style'] == 'single' else 1
        self.cbBotStyle.SelectedIndex = 0 if c.get('bot_style') == 'single' else 1
        self.cbSideStyle.SelectedIndex = 0 if c.get('side_style') == 'single' else 1
        self._mute = m

    def _fill_axis_mode(self):
        m = self._mute
        self._mute = True
        mode = self.cfg.get('axis_mode', 'bend')
        self._cb_fill(self.cbAxisMode, self.T('axis_modes'), G.AXIS_MODES.index(mode) if mode in G.AXIS_MODES else 0)
        self.cbAxisMode.IsEnabled = bool(self.model.bend_nodes)
        self._mute = m

    def _on_axis_mode(self, s, a):
        if self._mute or not self._ready:
            return
        i = self.cbAxisMode.SelectedIndex
        mode = G.AXIS_MODES[i] if 0 <= i < len(G.AXIS_MODES) else 'bend'
        if mode == self.cfg.get('axis_mode', 'bend'):
            return
        self.cfg['axis_mode'] = mode
        # anclar en el apoyo divide las zonas de Barra unica en los cambios de eje
        self.cfg = G.fit_cfg(self.cfg, self.model, self.bts_list)
        self._load_all_editors()
        self._refresh_all()

    def _load_all_editors(self):
        self._load_cfg_page()
        self._load_main_editor('Top')
        self._load_main_editor('Bot')
        self._load_side_editor()
        self._load_stg_editor()
        self._update_top_style_ui()

    def _on_edit(self, g):
        if self._mute or not self._ready:
            return
        c = self.cfg
        if g == 'Cfg':
            c['stir_bt'] = self._bt_of(self.cbStirBt)
            self._fill_stir_span()
            i = self.cbCover.SelectedIndex
            if 0 <= i < len(self.cover_vals):
                c['cover'] = self.cover_vals[i]
            v = self._m(self.tbTmin, 0.0, 0.5)
            if v is not None:
                c['tmin'] = v
            c['partition'] = (self.tbPartition.Text or u'').strip()
            c['views']['elev'] = bool(self.chkElev.IsChecked)
            c['views']['cross'] = bool(self.chkCross.IsChecked)
            ie = self.cbElevTpl.SelectedIndex
            ic = self.cbCrossTpl.SelectedIndex
            c['views']['elev_tpl'] = self.templates[ie - 1] if ie >= 1 else u''
            c['views']['cross_tpl'] = self.templates[ic - 1] if ic >= 1 else u''
            lc = c['lap']
            for tb, key, mn, mx, f in ((self.tbLap, 'k', 1.0, 200.0, 1.0), (self.tbStock, 'stock', 1.0, 50.0, 100.0)):
                v = G.parse_num(tb.Text, None)
                ok = v is not None and mn <= v <= mx
                self._mark(tb, ok)
                if ok:
                    lc[key] = v * f
            lc['stock_on'] = bool(self.chkStock.IsChecked)
            v = self._m(self.tbJointL, 0.0, 5.0)
            if v is not None:
                c.setdefault('joint', {})['L'] = v
            i_ls = self.cbLapStyle.SelectedIndex
            G.LP.set_style(lc, G.LP.STYLES[i_ls] if 0 <= i_ls < len(G.LP.STYLES) else 'offset')
            self.tbStock.IsEnabled = lc['stock_on']
            for tb, key in ((self.tbLapPosTop, 'pos_top'), (self.tbLapPosBot, 'pos_bot')):
                vals = G.LP.parse_positions(tb.Text)
                self._mark(tb, vals is not None)
                if vals is not None:
                    lc[key] = G.LP.fmt_positions(vals)
        elif g in ('Top', 'Bot'):
            self._read_main_editor(g)
        elif g in LAYER_END + LAYER_MID:
            sel = self.layer_sel.get(g, -1)
            lst = self._layer_list(g)
            if sel >= 0 and lst is not None and sel < len(lst):
                ly = self._read_layer_editor(g)
                if ly is not None:
                    lst[sel] = ly
            else:
                self._read_layer_editor(g)          # solo valida
        elif g == 'Side':
            sd = self._side_target()
            sd['on'] = bool(self.chkSideOn.IsChecked)
            sd['bt'] = self._bt_of(self.cbSideBt)
            sd['rows'] = self.cbSideRows.SelectedIndex + 1
            for f, tb in (('exl', self.tbSideExl), ('exr', self.tbSideExr)):
                v = self._m(tb, -2.0, 5.0)
                if v is not None:
                    sd[f] = v
            if (self.tbSideSp.Text or u'').strip():
                v = self._m(self.tbSideSp, 0.05, 2.0)
                if v is not None:
                    sd['sp'] = v
            else:
                self._mark(self.tbSideSp, True)
                sd['sp'] = 0.0
            self.cbSideRows.IsEnabled = not float(sd.get('sp') or 0.0) > 0
            sh = c['side_hook']
            ih = self.cbSideHook.SelectedIndex
            sh['dir'] = G.SIDE_HOOKS[ih] if 0 <= ih < len(G.SIDE_HOOKS) else 'none'
            self.tbSideHookL.IsEnabled = sh['dir'] != 'none'
            v = self._m(self.tbSideHookL, 0.0, 1.0)
            if v is not None:
                sh['L'] = v
        elif g == 'StS':
            sp = c['stirrups']['spans'][self.cur_span]
            ib = self.cbStSpanBt.SelectedIndex
            sp['bt'] = self.names[ib - 1] if 1 <= ib <= len(self.names) else u''
            ik = self.cbStDouble.SelectedIndex
            sp['double'] = G.STIR_KINDS[ik] if 0 <= ik < len(G.STIR_KINDS) else 'none'
        elif g == 'StG':
            gr = c['stirrups']['groups'][self.cur_span]
            left = G.parse_groups(self.tbStLeft.Text)
            self._mark(self.tbStLeft, left is not None)
            right = G.parse_groups(self.tbStRight.Text, left or [])
            self._mark(self.tbStRight, right is not None)
            mid = G.parse_num(self.tbStMid.Text, None)
            self._mark(self.tbStMid, mid is not None and 1.0 <= mid <= 200.0)
            if left is not None:
                gr['left'] = (self.tbStLeft.Text or u'').strip()
            if right is not None:
                gr['right'] = (self.tbStRight.Text or u'').strip()
            if mid is not None and 1.0 <= mid <= 200.0:
                gr['mid'] = mid
        elif g == 'St':
            st = c['stirrups']
            st['on'] = bool(self.chkStOn.IsChecked)
            i = self.cbStHook.SelectedIndex
            if 0 <= i < len(self.hooks):
                st['hook'] = self.hooks[i]['name']
            for f, tb, mn in (('lc', self.tbStLc, 0.0), ('sc', self.tbStSc, 0.01),
                              ('sm', self.tbStSm, 0.01), ('first', self.tbStFirst, 0.0)):
                v = self._m(tb, mn, 20.0)
                if v is not None:
                    st[f] = v
            st['mode'] = 'groups' if self.cbStMode.SelectedIndex == 1 else 'zones'
            self._update_st_mode_ui()
        self._refresh_all()

    def _on_top_style(self, s, a):
        if self._mute or not self._ready:
            return
        multi = self.cbTopStyle.SelectedIndex == 1
        old = self.cfg['top_style']
        self.cfg['top_style'] = 'multi' if multi else 'single'
        self._convert_style('Top', multi, old)
        self._update_top_style_ui()
        self._load_main_editor('Top')
        self._refresh_all()

    def _convert_style(self, g, multi, old):
        """Pasa la configuracion entre 'Barra unica' (por zona) y 'Multiple' (por tramo)."""
        key = 'top' if g == 'Top' else 'bot'
        ns = self.model.n_spans
        segs = self.model.segments()
        if multi and old == 'single':
            for i in range(ns):
                c = copy.deepcopy(self._zone_cfg(g, self._zone_of(i)))
                if i > 0 and not self.model.is_joint(i):
                    c['La'], c['Lna'] = 0.0, 0.0      # sin doblez en apoyos intermedios
                if i < ns - 1 and not self.model.is_joint(i + 1):
                    c['Lb'], c['Lnb'] = 0.0, 0.0
                self.cfg[key + '_spans'][i] = c
        elif not multi and old == 'multi':
            spans = self.cfg[key + '_spans']
            c = copy.deepcopy(spans[0])
            c['Lb'] = spans[segs[0][1]]['Lb']
            c['Lnb'] = spans[segs[0][1]]['Lnb']
            c['Lj1'] = spans[segs[0][1]].get('Lj1')
            self.cfg[key + '_single'] = c
            zones = []
            for a, b in segs[1:]:
                z = copy.deepcopy(spans[a])
                z['Lb'], z['Lnb'], z['Lj1'] = spans[b]['Lb'], spans[b]['Lnb'], spans[b].get('Lj1')
                zones.append(z)
            self.cfg[key + '_zones'] = zones

    def _on_bot_style(self, s, a):
        if self._mute or not self._ready:
            return
        multi = self.cbBotStyle.SelectedIndex == 1
        old = self.cfg.get('bot_style', 'multi')
        self.cfg['bot_style'] = 'multi' if multi else 'single'
        self._convert_style('Bot', multi, old)
        self._update_top_style_ui()
        self._load_main_editor('Bot')
        self._refresh_all()

    def _on_side_style(self, s, a):
        if self._mute or not self._ready:
            return
        multi = self.cbSideStyle.SelectedIndex == 1
        old = self.cfg.get('side_style', 'multi')
        self.cfg['side_style'] = 'multi' if multi else 'single'
        if multi and old == 'single':
            self.cfg['side'] = [copy.deepcopy(self.cfg['side_single']) for _ in range(self.model.n_spans)]
        elif not multi and old == 'multi':
            self.cfg['side_single'] = copy.deepcopy(self.cfg['side'][max(self.tb_side.sel, 0)])
        self._update_top_style_ui()
        self._load_side_editor()
        self._refresh_all()

    def _update_top_style_ui(self):
        V, Cl = Visibility.Visible, Visibility.Collapsed
        for g, st in (('Top', self.cfg['top_style']), ('Bot', self.cfg.get('bot_style'))):
            multi = st != 'single'
            zones = self._zone_mode(g)
            getattr(self, 'tbl%sMain' % g).Visibility = V if (multi or zones) else Cl
            getattr(self, 'btn%sAll' % g).Visibility = V if (multi or zones) else Cl
            txt = getattr(self, 'txt%sSingle' % g)
            txt.Visibility = Cl if multi else V
            txt.Text = self.T('zone_hint') if zones else TEXTS['top_single_hint'][self.lang]
        smulti = self.cfg.get('side_style') != 'single'
        self.tblSide.Visibility = V if smulti else Cl
        self.btnSideAll.Visibility = V if smulti else Cl
        self._update_st_mode_ui()
        self._update_cfg_headers()

    def _update_st_mode_ui(self):
        V, Cl = Visibility.Visible, Visibility.Collapsed
        grp = self.cfg['stirrups'].get('mode') == 'groups'
        self.pnlStZones.Visibility = Cl if grp else V
        self.pnlStGroups.Visibility = V if grp else Cl

    def _update_cfg_headers(self):
        for g, gb, key in (('Top', self.gbTopCfg, 'g_top_cfg'), ('Bot', self.gbBotCfg, 'g_bot_cfg')):
            st = self.cfg['top_style'] if g == 'Top' else self.cfg.get('bot_style')
            tb = self.tb_top if g == 'Top' else self.tb_bot
            if st != 'single':
                suf = self.T('cfg_span') % (max(tb.sel, 0) + 1)
            elif self._zone_mode(g):
                suf = self.T('cfg_zone') % (self._zone_of(self.cur_span) + 1)
            else:
                suf = u''
            gb.Header = TEXTS[key][self.lang] + suf

    def _on_top_row(self, i):
        self._set_span(self._row_span('Top', max(i, 0)))

    def _on_bot_row(self, i):
        self._set_span(self._row_span('Bot', max(i, 0)))

    def _row_span(self, g, i):
        """Fila de la tabla de barras principales -> tramo (en modo zonas, la fila es la zona)."""
        if self._zone_mode(g):
            segs = self.model.segments()
            return segs[min(i, len(segs) - 1)][0]
        return i

    def _span_row(self, g, i):
        return self._zone_of(i) if self._zone_mode(g) else i

    def _on_side_row(self, i):
        self._set_span(max(i, 0))

    def _apply_all(self, g):
        if g in ('Top', 'Bot') and self._zone_mode(g):
            key = 'top' if g == 'Top' else 'bot'
            src = self._main_target(g)
            one = self.cfg[key + '_single']
            one['bt'], one['n'] = src['bt'], src['n']
            self.cfg[key + '_zones'] = [None] * len(self.cfg[key + '_zones'])     # todas = Z1
        elif g == 'Top':
            src = self._main_target('Top')
            self.cfg['top_spans'] = [copy.deepcopy(src) for _ in range(self.model.n_spans)]
        elif g == 'Bot':
            src = self._main_target('Bot')
            self.cfg['bot_spans'] = [copy.deepcopy(src) for _ in range(self.model.n_spans)]
        elif g == 'StG':
            st = self.cfg['stirrups']
            src = st['groups'][self.cur_span]
            st['groups'] = [copy.deepcopy(src) for _ in range(self.model.n_spans)]
            src = st['spans'][self.cur_span]
            st['spans'] = [copy.deepcopy(src) for _ in range(self.model.n_spans)]
        else:
            src = self.cfg['side'][max(self.tb_side.sel, 0)]
            self.cfg['side'] = [copy.deepcopy(src) for _ in range(self.model.n_spans)]
        self._refresh_all()

    def _add_layer(self, p):
        lst = self._layer_list(p)
        if lst is None:
            return
        ly = self._read_layer_editor(p)
        if ly is None:
            self.txtStatus.Text = self.T('bad')
            return
        lst.append(ly)
        self.layer_sel[p] = -1
        self.tb_layers[p].sel = -1
        self._refresh_all()

    def _del_layer(self, p):
        lst = self._layer_list(p)
        if not lst:
            return
        i = self.layer_sel.get(p, -1)
        if not (0 <= i < len(lst)):
            i = len(lst) - 1
        lst.pop(i)
        self.layer_sel[p] = -1
        self.tb_layers[p].sel = -1
        self._refresh_all()

    def _on_loc(self, g):
        if self._mute or not self._ready:
            return
        self.cur_loc[g] = max(getattr(self, 'cb%smSup' % g).SelectedIndex, 0)
        p = g + 'm'
        self.layer_sel[p] = -1
        self.tb_layers[p].sel = -1
        self._loc_drawing(g)
        m = self._mute
        self._mute = True
        v = self._loc_default(g)
        self._setm(getattr(self, 'tb%sL1' % p), v)
        self._setm(getattr(self, 'tb%sL2' % p), v)
        self._mute = m
        self._refresh_all()

    def _set_span(self, i):
        i = max(0, min(i, self.model.n_spans - 1))
        changed = i != self.cur_span
        self.cur_span = i
        m = self._mute
        self._mute = True
        self.tb_top.select(self._span_row('Top', i), False)
        self.tb_bot.select(self._span_row('Bot', i), False)
        self.tb_side.select(i, False)
        self.tb_stg.select(i, False)
        self._mute = m
        self._load_main_editor('Top')
        self._load_main_editor('Bot')
        self._load_side_editor()
        self._load_stg_editor()
        self._refresh_all()

    # ------------------------------------------------------------ refresco
    def _refresh_all(self):
        if not self._ready:
            return
        try:
            self.res = G.compute(self.model, self.cfg, self.bts)
        except Exception as ex:
            self.res = None
            self.txtStatus.Text = u'Error: %s' % ex
            return
        self._refresh_tables()
        self._refresh_texts()
        self._redraw()

    def _refresh_tables(self):
        c = self.cfg
        ns = self.model.n_spans

        def main_rows(lst):
            return [[u'%d' % (i + 1), x['bt'], u'%d' % x['n'], fm(x['La']), fm(x['Lna']),
                     fm(x['Lb']), fm(x['Lnb'])] for i, x in enumerate(lst[:ns])]

        def zone_rows(g):
            key = 'top' if g == 'Top' else 'bot'
            out = []
            for z, (a, b) in enumerate(self.model.segments()):
                x = self._zone_cfg(g, z)
                inh = z > 0 and (z - 1 >= len(c[key + '_zones']) or c[key + '_zones'][z - 1] is None)
                lbl = (u'Z%d T%d' % (z + 1, a + 1)) if a == b else (u'Z%d T%d-%d' % (z + 1, a + 1, b + 1))
                out.append([lbl + (u' =' if inh else u''), x['bt'], u'%d' % x['n'], fm(x['La']), fm(x['Lna']),
                            fm(x['Lb']), fm(x['Lnb'])])
            return out

        self.tb_top.set_rows(zone_rows('Top') if self._zone_mode('Top') else main_rows(c['top_spans']),
                             self._span_row('Top', self.cur_span))
        self.tb_bot.set_rows(zone_rows('Bot') if self._zone_mode('Bot') else main_rows(c['bot_spans']),
                             self._span_row('Bot', self.cur_span))
        yes, no = self.T('yes'), self.T('no')
        self.tb_side.set_rows([[u'%d' % (i + 1), yes if x.get('on') else no, x['bt'],
                                (u'@' + fm(x['sp'])) if float(x.get('sp') or 0) > 0 else u'%d' % x['rows'],
                                fm(x['exl']), fm(x['exr'])] for i, x in enumerate(c['side'][:ns])])
        same = self.T('same')
        grp = c['stirrups'].get('mode') == 'groups'
        kinds = self.T('stir_kinds_s')
        rows = []
        for i in range(ns):
            g = c['stirrups']['groups'][i]
            sp = c['stirrups']['spans'][i]
            kd = kinds[G.STIR_KINDS.index(sp.get('double', 'none'))]
            if grp:
                rows.append([u'%d' % (i + 1), self._span_stir_bt(i), kd, g.get('left') or u'0',
                             u'@' + fcm(float(g.get('mid') or 0)), g.get('right') or same])
            else:
                rows.append([u'%d' % (i + 1), self._span_stir_bt(i), kd, u'-', u'-', u'-'])
        self.tb_stg.set_rows(rows)
        for p in LAYER_END + LAYER_MID:
            lst = self._layer_list(p) or []
            if p in LAYER_END:
                rows = [[u'%d' % (k + 1), ly['bt'], u'%d' % ly['n'], fm(ly['La']), fm(ly['L']), fm(ly['Ln'])]
                        for k, ly in enumerate(lst)]
            else:
                rows = [[u'%d' % (k + 1), ly['bt'], u'%d' % ly['n'], fm(ly['L1']), fm(ly['L2'])]
                        for k, ly in enumerate(lst)]
            self.tb_layers[p].set_rows(rows, self.layer_sel.get(p, -1))
        self._update_cfg_headers()

    def _side_info(self):
        side = [b for b in (self.res or {}).get('bars', []) if b['kind'] == 'side']
        if not side:
            return u''
        if self.cfg.get('side_style') == 'single':
            span = [b for b in side if b.get('lap_piece', 0) == 0]
        else:
            i = max(self.tb_side.sel, 0)
            span = [b for b in side if b.get('span') == i and b.get('lap_piece', 0) == 0]
        if not span:
            return u''
        ys = sorted(set(round(b['y'], 2) for b in span))
        if len(ys) > 1:
            sp = ys[1] - ys[0]
        else:
            top = self.res['top_rows'][self.cur_span] or {'y': self.model.h / 2.0}
            sp = top['y'] - ys[0]
        y0 = span[0]['y']
        pieces = max([b.get('lap_piece', 0) for b in side if abs(b['y'] - y0) < 1e-6] or [0])
        lap = self.cfg['lap'].get('k', 40.0) * span[0]['bt']['dn']
        laps = self.T('side_laps') % (pieces, fm(lap)) if pieces else self.T('side_nolap')
        return self.T('side_info') % (len(ys), fm(sp), laps)

    def _refresh_texts(self):
        res = self.res
        i = self.cur_span
        hk = self.cfg['stirrups'].get('hook')
        a = self.hook_len.get((self._span_stir_bt(i), hk))
        self.tbStHookLen.Text = fcm(a) if a else u'-'
        hooks = dict((nm, self.hook_len.get((nm, hk)) or 0.0) for nm in self.names)
        w, n_st = G.summary(res, self.bts, hooks)
        tot = sum(w.values())
        self.txtSummary.Text = self.T('w_title') + self.T('w_parts') % (
            w['top_main'] + w['bot_main'], w['top_add'], w['bot_add'], w['side'], w['stirrups'], n_st, tot)
        if self.cfg['stirrups'].get('mode') == 'groups':
            self.txtStInfo.Text = self.T('st_info_g') % (n_st, self.model.n_spans)
        else:
            self.txtStInfo.Text = self.T('st_info') % (n_st, self.model.n_spans)
        self.txtSideInfo.Text = self._side_info()
        if self._bad:
            self.txtStatus.Text = self.T('bad')
        elif res['warnings']:
            wr = res['warnings']
            self.txtStatus.Text = wr[0] if len(wr) == 1 else self.T('warn_n') % (wr[0], len(wr) - 1)
        else:
            self.txtStatus.Text = u''

    # ------------------------------------------------------------ dibujo
    def _on_resize(self, s, a):
        if self._ready:
            self._redraw()

    def _redraw(self):
        if self.res is None:
            return
        try:
            self._draw_elev()
            if self.page == 0:
                self._draw_section(self.secCanvas0, 'all')
            elif self.page == 4:
                self._draw_section(self.secCanvas4, 'side')
            elif self.page == 5:
                self._draw_section(self.secCanvas5, 'st')
        except Exception as ex:
            self.txtStatus.Text = u'Error de dibujo: %s' % ex

    def _draw_section(self, cv, focus):
        cv.Children.Clear()
        W, H = cv.ActualWidth, cv.ActualHeight
        if W < 40 or H < 40:
            return
        m = self.model
        res = self.res
        i = self.cur_span
        sc = m.sec(i)
        mg = 28.0
        s = min((W - 2 * mg) / sc['b'], (H - 2 * mg - 14) / sc['h'])
        cx, cy = W / 2.0, H / 2.0 + 7

        def P(x, y):
            return (cx - (x - sc['xc']) * s, cy - (y - sc['yc']) * s)     # x local + hacia la izquierda

        x0, y0 = P(sc['xc'] + sc['b'] / 2.0, sc['yc'] + sc['h'] / 2.0)
        d_rect(cv, x0, y0, sc['b'] * s, sc['h'] * s, C['conc_line'], C['concrete'], 1.2)
        d_text(cv, W / 2.0, y0 - 4, u'%s' % fm(sc['b']), C['text'], 10.5, 'cb')
        d_text(cv, x0 + sc['b'] * s + 6, cy, u'%s' % fm(sc['h']), C['text'], 10.5, 'lc')
        d_text(cv, 6, 4, self.T('sec_title') % (i + 1), C['dim'], 10.5)
        polys = (res.get('stir_polys') or [])
        polys = polys[i] if i < len(polys) else []
        ds = (res.get('stir_ds') or [res.get('ds', 0.0)] * (i + 1))[i]
        for k, poly in enumerate(polys):
            pts = [P(x, y) for x, y in poly]
            th = max(1.4, ds * s)
            col = (C['st'] if k == 0 else '#B6F09C') if focus in ('st', 'all') else '#4E6E4A'
            d_poly(cv, pts, col, th, True)
            px, py = pts[0]
            qx = sum(p[0] for p in pts) / len(pts)
            qy = sum(p[1] for p in pts) / len(pts)
            ux, uy = (qx - px), (qy - py)
            ul = math.hypot(ux, uy) or 1.0
            ux, uy = ux / ul, uy / ul
            for off in (-2.0, 2.0):
                d_line(cv, px - uy * off, py + ux * off, px + ux * 13 - uy * off, py + uy * 13 + ux * off,
                       col, th * 0.8)
        # varillas en el centro del tramo seleccionado
        fa, fb = m.span_faces(i)
        smid = (fa + fb) / 2.0
        for b in res['bars']:
            if not (b['s0'] - 1 <= smid <= b['s1'] + 1):
                continue
            if b['kind'] in ('top_add', 'bot_add') and focus != 'all':
                continue
            col = KIND_COLOR[b['kind']]
            if focus == 'side' and b['kind'] != 'side':
                col = '#2F5F8A'
            r = max(3.0, b['bt']['dm'] / 2.0 * s)
            for x in b['xs']:
                px, py = P(x, b['y'])
                d_circle(cv, px, py, r, '#CDE8FF', col, 0.8)

    def _draw_elev(self):
        cv = self.elevCanvas
        cv.Children.Clear()
        W, H = cv.ActualWidth, cv.ActualHeight
        if W < 100 or H < 80:
            return
        m = self.model
        res = self.res
        S0, S1 = m.extents()
        for b in res['bars']:
            S0 = min(S0, b['s0'])
            S1 = max(S1, b['s1'])
        pad = 34.0
        sx = (W - 2 * pad) / max(S1 - S0, 1.0)
        y_top = H * 0.27
        bh = H * 0.36
        sy = bh / m.h

        def X(s):
            return pad + (s - S0) * sx

        def Y(y):
            return y_top + (m.h / 2.0 - y) * sy

        self._elev_map = (S0, sx, pad)
        if getattr(m, 'slope', 0.0) > 0.05:
            sl = getattr(m, 'slopes', None) or [m.slope]
            txt = u' → '.join(u'%.1f°' % a for a in sl)
            d_text(cv, W - 8, 4, (u'Pendiente %s · alzado desarrollado a lo largo de la viga' if self.lang == 0
                                  else u'Slope %s · elevation unrolled along the beam') % txt, '#F0A030', 10.5, 'rt')
        # apoyos
        for n in m.nodes:
            if n.free:
                d_line(cv, X(n.s0), y_top - 10, X(n.s0), y_top + bh + 10, C['dim'], 1.0, (2, 3))
                continue
            if n.kind == u'cambio':
                d_line(cv, X(n.s0), y_top - 18, X(n.s0), y_top + bh + 18, '#F0A030', 1.0, (4, 3))
                d_text(cv, X(n.s0), y_top - 20, n.name, '#F0A030', 10.0, 'cb')
                continue
            d_rect(cv, X(n.s0), y_top - 30, (n.s1 - n.s0) * sx, bh + 60, C['sup_line'], C['support'], 1.0, (5, 3))
            xm = X(n.mid)
            d_line(cv, xm, 26, xm, y_top + bh + 40, '#6A6A6A', 0.8, (8, 3, 2, 3))
            d_circle(cv, xm, 15, 11, '#BDBDBD', '#27272A', 1.2)
            d_text(cv, xm, 15, n.name, C['bright'], 10.5, 'cc')
        # viga (cada pieza con su seccion: cambios de peralte o de nivel)
        parts = m.parts or [{'s0': 0.0, 's1': m.L, 'yc': 0.0, 'h': m.h}]
        for p in parts:
            d_rect(cv, X(p['s0']), Y(p['yc'] + p['h'] / 2.0), (p['s1'] - p['s0']) * sx, p['h'] * sy,
                   C['conc_line'], C['concrete'], 1.0)
        # tramo seleccionado
        fa, fb = m.span_faces(self.cur_span)
        sc = m.sec(self.cur_span)
        d_rect(cv, X(fa), Y(sc['yc'] + sc['h'] / 2.0), (fb - fa) * sx, sc['h'] * sy, '#3DA5F5', '#20304A', 1.0,
               (4, 3))
        # estribos
        sets = res['stirrups']
        total = sum(t['n'] for t in sets)
        if sets and total <= 2000:
            col = C['st'] if self.page == 5 else C['st_elev']
            for t in sets:
                poly = t.get('poly') or res.get('stir_poly')
                if not poly:
                    continue
                y1 = max(q[1] for q in poly)
                y0 = min(q[1] for q in poly)
                for k in range(t['n']):
                    xx = X(t['s0'] + k * t['sp'])
                    d_line(cv, xx, Y(y1), xx, Y(y0), col, 1.0)
        # varillas
        focus = {1: ('top_main', 'bot_main'), 2: ('top_add',), 3: ('bot_add',), 4: ('side',)}.get(self.page)
        for b in sorted(res['bars'], key=lambda q: 1 if (focus and q['kind'] in focus) else 0):
            col = KIND_COLOR[b['kind']]
            th = 2.2 if (focus is None or b['kind'] in focus) else 1.2
            if focus and b['kind'] not in focus:
                col = '#44607A'
            pts = []
            yb = b['y']
            dys = (4.0 if b['shift'] < 0 else -4.0) if b.get('shift') else 0.0   # desfasado (en pixeles)
            if abs(b['leg0']) > 0.01:
                pts.append((X(b['s0']), Y(yb + b['leg0']) + dys))
            pts.append((X(b['s0']), Y(yb) + dys))
            ck = b.get('crank')
            if ck:
                # bayoneta: se dibuja desplazada unos pixeles para que se vea el traslape
                run = max(6.0 * b['bt']['dm'] * sx, 6.0)
                dyp = 4.0 if ck[1] < 0 else -4.0
                pts += [(X(ck[0]) - run, Y(b['y'])), (X(ck[0]), Y(b['y']) + dyp), (X(b['s1']), Y(b['y']) + dyp)]
            else:
                pts.append((X(b['s1']), Y(yb) + dys))
            if abs(b['leg1']) > 0.01:
                pts.append((X(b['s1']), Y(yb + b['leg1']) + dys))
            d_poly(cv, pts, col, th)
            # gancho horizontal (hacia el centro de la viga): se ve de punta en el alzado
            hin = b.get('hin') or (0.0, 0.0)
            for s_end, L in ((b['s0'], hin[0]), (b['s1'], hin[1])):
                if L > 0.01:
                    d_circle(cv, X(s_end), Y(yb) + dys, 3.2, col, col, 1.0)
        # cotas
        yd = H - 24
        xa, xb = X(m.nodes[0].s0), X(m.nodes[-1].s1)
        d_line(cv, xa, yd, xb, yd, C['dim'], 0.8)
        marks = []
        for n in m.nodes:
            marks += [n.s0, n.s1]
        for s in marks:
            d_line(cv, X(s), yd - 5, X(s), yd + 5, C['dim'], 0.8)
        for n in m.nodes:
            if not n.free and n.kind != u'cambio':
                d_text(cv, X(n.mid), yd + 3, fm(n.width), C['text'], 10.5, 'ct')
        for k in range(m.n_spans):
            a, b = m.span_faces(k)
            d_text(cv, X((a + b) / 2.0), yd - 3, fm(b - a), C['bright'] if k == self.cur_span else C['text'],
                   11, 'cb', k == self.cur_span)

    def _on_elev_click(self, s, a):
        if not getattr(self, '_elev_map', None):
            return
        S0, sx, pad = self._elev_map
        p = a.GetPosition(self.elevCanvas)
        sv = S0 + (p.X - pad) / sx
        ns = self.model.n_spans
        for i in range(ns):
            lo = self.model.nodes[i].mid if i > 0 else -1e12
            hi = self.model.nodes[i + 1].mid if i < ns - 1 else 1e12
            if lo <= sv <= hi:
                self._set_span(i)
                return

    # ------------------------------------------------------------ guardar / importar
    def _ask_name(self):
        return forms.ask_for_string(default=u'viga', prompt=self.T('save_prompt'), title=TEXTS['save'][self.lang])

    def _confirm(self, msg):
        return forms.alert(msg, yes=True, no=True)

    def _pick_saved(self, names, last):
        items = []
        if last:
            items.append(self.T('last'))
        items += names
        items.append(self.T('browse'))
        return forms.SelectFromList.show(items, title=self.T('imp_title'), button_name=TEXTS['import'][self.lang],
                                         multiselect=False)

    def _pick_file(self, folder):
        return forms.pick_file(file_ext='json', init_dir=folder)

    def _on_save(self, s, a):
        name = self._ask_name()
        if not name:
            return
        folder = G.config_dir()
        name = G.safe_name(name)
        path = os.path.join(folder, name + u'.json')
        if os.path.exists(path) and not self._confirm(self.T('overwrite') % name):
            return
        try:
            G.save_cfg(path, self.cfg, self.bts)
            self.txtStatus.Text = self.T('saved') % path
        except Exception as ex:
            self.txtStatus.Text = u'Error: %s' % ex

    def _on_import(self, s, a):
        folder = G.config_dir()
        names = [n for n in G.list_saved(folder) if n != KG_LAST]
        last = os.path.exists(os.path.join(folder, KG_LAST + u'.json'))
        pick = self._pick_saved(names, last)
        if not pick:
            return
        if pick == self.T('browse'):
            path = self._pick_file(folder)
        elif pick == self.T('last'):
            path = os.path.join(folder, KG_LAST + u'.json')
        else:
            path = os.path.join(folder, pick + u'.json')
        if not path:
            return
        self.import_file(path)

    def import_file(self, path):
        try:
            data, diams = G.load_cfg(path)
            cfg = G.fit_cfg(data, self.model, self.bts_list)
            changes = G.remap_bar_names(cfg, diams, self.bts_list)
            hk_names = [h['name'] for h in self.hooks]
            if cfg['stirrups'].get('hook') not in hk_names and self.hooks:
                cfg['stirrups']['hook'] = hk_names[0]
            views = cfg['views']
            for k in ('elev_tpl', 'cross_tpl'):
                if views.get(k) and views[k] not in self.templates:
                    views[k] = u''
        except Exception as ex:
            self.txtStatus.Text = self.T('err_load') % ex
            return False
        self.cfg = cfg
        self.cur_loc = {'T': 0, 'B': 0}
        for p in self.layer_sel:
            self.layer_sel[p] = -1
            self.tb_layers[p].sel = -1
        self._mute = True
        self._fill_span_combos()
        self._mute = False
        self._load_all_editors()
        self._refresh_all()
        msg = self.T('loaded') % os.path.basename(path)
        if changes:
            msg += self.T('remap') % u', '.join(u'%s→%s' % kv for kv in changes.items())
        self.txtStatus.Text = msg
        return True

    # ------------------------------------------------------------ ventana
    def _on_drag(self, s, a):
        try:
            self.DragMove()
        except Exception:
            pass

    def _on_close(self, s, a):
        self.result = None
        self.Close()

    def _on_ok(self, s, a):
        if self._bad:
            self.txtStatus.Text = self.T('bad')
            return
        if self.res is None or (not self.res['bars'] and not self.res['stirrups']):
            self.txtStatus.Text = self.T('empty')
            return
        try:
            G.save_cfg(os.path.join(G.config_dir(), KG_LAST + u'.json'), self.cfg, self.bts)
        except Exception:
            pass
        result = {'cfg': copy.deepcopy(self.cfg), 'res': self.res,
                  'replace': bool(self.chkReplace.IsChecked), 'model': self.model}
        if ML.apply(self, result):
            return
        self.result = result
        self.Close()

    # ------------------------------------------------------------ nuevo armado
    def _default_cfg(self):
        cfg = G.fit_cfg({}, self.model, self.bts_list)
        if self.hooks:
            h = [x for x in self.hooks if abs(x['angle'] - 135.0) < 1.0]
            cfg['stirrups']['hook'] = (h[0] if h else self.hooks[0])['name']
        return cfg

    def _on_reset(self, s, a):
        msg = (u'¿Borrar la configuración actual y empezar un armado nuevo con los valores por defecto?\n\n'
               u'El armado del modelo no cambia hasta pulsar OK.') if self.lang == 0 else \
              (u'Clear the current settings and start a new reinforcement from the defaults?\n\n'
               u'The model rebar does not change until OK.')
        if not self._confirm(msg):
            return
        self.cfg = self._default_cfg()
        self.cur_span = 0
        self.cur_loc = {'T': 0, 'B': 0}
        for p in self.layer_sel:
            self.layer_sel[p] = -1
            self.tb_layers[p].sel = -1
        self._load_all_editors()
        self._set_span(0)
        self._refresh_all()
        self.txtStatus.Text = u'Configuración reiniciada: capture el armado y pulse OK (marque «Reemplazar» para ' \
                              u'borrar el anterior).' if self.lang == 0 else \
            u'Settings cleared: enter the reinforcement and press OK (check "Replace" to delete the previous one).'

    # ------------------------------------------------------------ no modal
    def reload(self, ctx):
        """Carga otra viga (o la misma releida) conservando la configuracion."""
        self.ctx = ctx
        self.model = ctx['model']
        self.templates = ctx.get('templates', self.templates)
        # la viga ya tiene armado: se carga el suyo; si no, se conserva la configuracion actual
        self.cfg = G.fit_cfg(ctx.get('cfg') or self.cfg, self.model, self.bts_list)
        if not self.cfg['stirrups'].get('hook') and self.hooks:
            h = [x for x in self.hooks if abs(x['angle'] - 135.0) < 1.0]
            self.cfg['stirrups']['hook'] = (h[0] if h else self.hooks[0])['name']
        self.cur_span = 0
        self.cur_loc = {'T': 0, 'B': 0}
        for p in self.layer_sel:
            self.layer_sel[p] = -1
            self.tb_layers[p].sel = -1
        self._mute = True
        self._fill_span_combos()
        self._fill_templates()
        self.txtBeamInfo.Text = ctx.get('beam_desc', u'')
        self._mute = False
        self._load_all_editors()
        self._set_span(0)
        self._refresh_all()
