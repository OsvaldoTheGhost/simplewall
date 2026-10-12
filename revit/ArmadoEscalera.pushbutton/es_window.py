# -*- coding: utf-8 -*-
"""Ventana WPF (estilo CBAR) para el armado de escaleras y rampas / losas inclinadas."""
from __future__ import division
import copy
import math
import os

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System import Double, Object
from System.Windows import Point, Size, LogicalTreeHelper, DependencyObject, FrameworkElement
from System.Windows.Controls import Canvas, TextBlock, ContentControl, HeaderedContentControl
from System.Windows.Shapes import Line, Ellipse, Rectangle, Polyline, Polygon
from System.Windows.Media import BrushConverter, DoubleCollection, PointCollection, PenLineJoin

from pyrevit import forms

import es_geom as G
import armado_modeless as ML


# ================================================================ textos
TEXTS = {
    'title': (u'ARMADO DE ESCALERA / RAMPA', u'STAIR / RAMP REINFORCEMENT'),
    'tab_gen': (u'General', u'General'), 'tab_long': (u'Longitudinal', u'Longitudinal'),
    'tab_tr': (u'Transversal', u'Transverse'), 'tab_steps': (u'Escalones', u'Steps'),
    'kind': (u'Tipo', u'Type'), 'rot': (u'Dirección', u'Direction'),
    'cov_b': (u'Recubrimiento inferior', u'Bottom cover'), 'cov_t': (u'superior', u'top'),
    'cov_s': (u'Recubrimiento lateral y de extremos', u'Side and end cover'), 'partition': (u'Partición', u'Partition'),
    'replace': (u'Reemplazar el armado creado antes con esta herramienta', u'Replace rebar previously created by this tool'),
    'g_bot': (u'LECHO INFERIOR', u'BOTTOM LAYER'), 'g_top': (u'LECHO SUPERIOR', u'TOP LAYER'),
    'bar': (u'Varilla', u'Bar'), 'ext': (u'Prolongación ini./fin', u'Extension start/end'),
    'leg': (u'Pata ini./fin', u'Leg start/end'), 'split': (u'Cortar y cruzar en quiebres', u'Split and lap at kinks'),
    'ld': (u'Anclaje Ld', u'Anchorage Ld'), 'top_on': (u'Crear lecho superior', u'Create top layer'),
    'mode': (u'Colocación', u'Layout'), 'l_ends': (u'L ini./fin', u'L start/end'),
    'tr_bot': (u'Repartición inferior', u'Bottom distribution'), 'tr_top': (u'Repartición superior', u'Top distribution'),
    'tr_hint': (u'Las varillas de repartición se colocan a lo ancho, por dentro de las longitudinales, y se agrupan por tramo recto.',
                u'Distribution bars run across the width, inside the longitudinal bars, grouped per straight segment.'),
    'nose_on': (u'Varilla en la nariz de cada escalón', u'Bar at each step nosing'),
    'steps_hint': (u'Las narices con el mismo paso se agrupan en un solo conjunto.',
                   u'Nosings with the same spacing are grouped in one set.'),
    'show_bot': (u'Inferior', u'Bottom'), 'show_top': (u'Superior', u'Top'), 'show_tr': (u'Repartición', u'Distribution'),
    'save': (u'Guardar', u'Save'), 'import': (u'Importar', u'Import'),
    'lap': (u'Traslape (× db)', u'Lap (× db)'), 'stock': (u'Longitud comercial máx.', u'Max. stock length'),
    'lap_style': (u'Unión', u'Joint'),
    'lap_pos': (u'Traslapes (cm horizontales desde el inicio, separados por coma)',
                u'Laps (horizontal cm from the start, comma separated)'),
}

TIPS = {
    'tbLapPos': (u'Distancia horizontal desde el inicio de la escalera donde empieza cada traslape de las longitudinales.',
                 u'Horizontal distance from the start where each longitudinal lap starts.'),
    'cbLapStyle': G.LP.STYLE_TIP,
    'chkBotSplit': (u'En los quiebres donde la varilla en tensión tiende a salirse del concreto (inferior: llegada a un '
                    u'descanso superior; superior: arranque desde un descanso inferior) se cortan y se cruzan con Ld.',
                    u'At kinks where the bar in tension would pull out of the concrete the bars are split and lapped with Ld.'),
    'chkTopSplit': (u'Corta y cruza el lecho superior en los quiebres cóncavos de la cara superior.',
                    u'Split and lap the top layer at re-entrant kinks.'),
    'tbBotExt0': (u'Prolongación recta más allá del extremo (hacia el apoyo). 0 = termina con recubrimiento.',
                  u'Straight extension beyond the end (into the support). 0 = ends at cover.'),
    'tbBotLeg0': (u'Pata a 90° hacia el lecho opuesto (se limita al espesor).', u'90° leg towards the opposite layer (limited to thickness).'),
    'cbRot': (u'Automática: la subida se detecta de la cara inclinada. Girada: usa la dirección perpendicular.',
              u'Automatic: rise detected from the sloped face. Rotated: uses the perpendicular direction.'),
    'btnSave': (u'Guardar esta configuración con un nombre', u'Save these settings with a name'),
    'btnImport': (u'Cargar una configuración guardada', u'Load saved settings'),
}

DYN = {
    'k_esc': (u'Escalera', u'Stair'), 'k_ramp': (u'Rampa / losa inclinada', u'Ramp / sloped slab'),
    'r_auto': (u'Automática (subida)', u'Automatic (rise)'), 'r_90': (u'Girada 90°', u'Rotated 90°'),
    'm_full': (u'Corrido', u'Continuous'), 'm_ends': (u'Solo en extremos', u'Only at ends'),
    'prof': (u'Perfil · L = %s cm · H = %s cm · garganta %s cm', u'Profile · L = %s cm · H = %s cm · waist %s cm'),
    'sec': (u'Corte transversal · ancho %s cm · t = %s cm', u'Cross section · width %s cm · t = %s cm'),
    'foot': (u'Inf.: %s @ %s · Sup.: %s · Rep.: %s', u'Bottom: %s @ %s · Top: %s · Dist.: %s'),
    'none': (u'no', u'no'),
    'steps': (u'%d escalones · huella %s cm · peralte %s cm', u'%d steps · tread %s cm · riser %s cm'),
    'no_steps': (u'No se detectaron escalones (perfil de rampa).', u'No steps detected (ramp profile).'),
    'sum': (u'Peso aprox. (kg): %s · total %.1f', u'Approx. weight (kg): %s · total %.1f'),
    'wnames': ({'long_bot': u'inferior', 'long_top': u'superior', 'tr_bot': u'rep. inf.', 'tr_top': u'rep. sup.',
                'nose': u'narices'},
               {'long_bot': u'bottom', 'long_top': u'top', 'tr_bot': u'bottom dist.', 'tr_top': u'top dist.',
                'nose': u'nosings'}),
    'up': (u'sube', u'up'),
    'bad': (u'Revise los valores marcados en rojo.', u'Check the values marked in red.'),
    'empty': (u'No hay nada que crear.', u'Nothing to create.'),
    'saved': (u'Configuración guardada: %s', u'Settings saved: %s'),
    'loaded': (u'Configuración importada: %s', u'Settings imported: %s'),
    'remap': (u' · tipos sustituidos: %s', u' · types replaced: %s'),
    'save_prompt': (u'Nombre de la configuración', u'Settings name'),
    'overwrite': (u'Ya existe "%s". ¿Reemplazarla?', u'"%s" already exists. Replace it?'),
    'last': (u'(última usada)', u'(last used)'), 'browse': (u'Buscar archivo…', u'Browse file…'),
    'imp_title': (u'Importar configuración', u'Import settings'),
    'err_load': (u'No se pudo importar: %s', u'Could not import: %s'),
    'warn_n': (u'%s  (+%d avisos más)', u'%s  (+%d more warnings)'),
}

KINDS = ('escalera', 'rampa')
KC = {'long_bot': '#3DA5F5', 'long_top': '#F0A030', 'tr_bot': '#8FD3FF', 'tr_top': '#FFD08A', 'nose': '#7CC576'}
C = {'conc': '#343437', 'conc_line': '#D08A4A', 'bg': '#262628', 'text': '#A8A8A8', 'red': '#D9534F',
     'tb_border': '#555558', 'bright': '#E6E6E6'}
KEY_LAST = u'_ultima'

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


def d_rect(cv, x, y, w, h, stroke=None, fill=None, th=1.0, dash=None):
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
    r.IsHitTestVisible = False
    Canvas.SetLeft(r, float(x))
    Canvas.SetTop(r, float(y))
    cv.Children.Add(r)


def d_poly(cv, pts, col, th=1.0, closed=False, dash=None, fill=None):
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
    if fill and closed:
        s.Fill = brush(fill)
    s.IsHitTestVisible = False
    cv.Children.Add(s)


def d_text(cv, x, y, text, col, size=11.0, anchor='lt'):
    tb = TextBlock()
    tb.Text = text
    tb.Foreground = brush(col)
    tb.FontSize = float(size)
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


def fmt(v):
    if v is None:
        return u'-'
    s = u'%.1f' % v
    return s[:-2] if s.endswith(u'.0') else s


# ================================================================ ventana
class StairRebarWindow(forms.WPFWindow):
    def __init__(self, xaml_file, ctx):
        try:
            forms.WPFWindow.__init__(self, xaml_file, handle_esc=False)
        except TypeError:
            forms.WPFWindow.__init__(self, xaml_file)
        self._ready = False
        self._mute = True
        self.ctx = ctx
        self.model = ctx['model']
        self.bl = ctx['bar_types']
        self.bts = dict((b['name'], b) for b in self.bl)
        self.names = [b['name'] for b in self.bl]
        self.lang = 0
        self._bad = set()
        self.res = None
        self.result = None
        self.cfg = G.fit_cfg(ctx.get('cfg') or {}, self.model, self.bl)
        self._init_controls()
        self._wire()
        ML.setup(self)
        self._apply_language()
        self._load_editors()
        self._ready = True
        self._mute = False
        self._refresh()

    # ------------------------------------------------------------ util
    def T(self, k):
        return DYN[k][self.lang]

    def _cb_fill(self, cb, items, sel=0):
        cb.Items.Clear()
        for it in items:
            cb.Items.Add(it)
        if items:
            cb.SelectedIndex = max(0, min(sel, len(items) - 1))

    def _bt_idx(self, n):
        try:
            return self.names.index(n)
        except ValueError:
            return 0

    def _bt_of(self, cb):
        i = cb.SelectedIndex
        return self.names[i] if 0 <= i < len(self.names) else (self.names[0] if self.names else u'')

    def _mark(self, tb, ok):
        n = tb.Name
        if ok:
            if n in self._bad:
                self._bad.discard(n)
                tb.BorderBrush = brush(C['tb_border'])
        elif n not in self._bad:
            self._bad.add(n)
            tb.BorderBrush = brush(C['red'])

    def _num(self, tb, mn=None, mx=None):
        v = G.parse_num(tb.Text, None)
        ok = v is not None and (mn is None or v >= mn) and (mx is None or v <= mx)
        self._mark(tb, ok)
        return v if ok else None

    def _set(self, tb, v):
        tb.Text = fmt(v)
        self._mark(tb, True)

    def _tab(self):
        return self.tabs.SelectedIndex

    # ------------------------------------------------------------ init
    BT_COMBOS = ('cbBotBt', 'cbTopBt', 'cbTrBotBt', 'cbTrTopBt', 'cbNoseBt')

    def _init_controls(self):
        self._cb_fill(self.cbLang, [u'ESP', u'ENG'], 0)
        for n in self.BT_COMBOS:
            self._cb_fill(getattr(self, n), self.names, 0)

    def _fill_dyn(self):
        m = self._mute
        self._mute = True
        c = self.cfg
        self._cb_fill(self.cbKind, [self.T('k_esc'), self.T('k_ramp')], 1 if c.get('kind') == 'rampa' else 0)
        self._cb_fill(self.cbRot, [self.T('r_auto'), self.T('r_90')], 1 if int(c.get('rot', 0)) == 1 else 0)
        self._cb_fill(self.cbTopMode, [self.T('m_full'), self.T('m_ends')], 1 if c['top'].get('mode') == 'ends' else 0)
        self._cb_fill(self.cbLapStyle, G.LP.style_items(self.lang), G.LP.STYLES.index(G.LP.lap_style(c['lap'])))
        self._mute = m

    def _wire(self):
        self.titleBar.MouseLeftButtonDown += self._on_drag
        self.btnClose.Click += self._on_close
        self.btnOK.Click += self._on_ok
        self.btnSave.Click += self._on_save
        self.btnImport.Click += self._on_import
        self.cbLang.SelectionChanged += self._on_lang
        self.tabs.SelectionChanged += self._on_tab
        self.cbRot.SelectionChanged += self._on_rot

        def ev(g):
            return lambda s, a: self._on_edit(g)

        groups = {
            'Gen': (['tbCovB', 'tbCovT', 'tbCovS', 'tbPartition', 'tbLap', 'tbStock', 'tbLapPos'], ['cbKind', 'cbLapStyle'],
                    ['chkStock']),
            'Bot': (['tbBotSp', 'tbBotExt0', 'tbBotExt1', 'tbBotLeg0', 'tbBotLeg1', 'tbBotLd'], ['cbBotBt'], ['chkBotSplit']),
            'Top': (['tbTopSp', 'tbTopL0', 'tbTopL1', 'tbTopLeg0', 'tbTopLeg1', 'tbTopLd'], ['cbTopBt', 'cbTopMode'],
                    ['chkTopOn', 'chkTopSplit']),
            'Tr': (['tbTrBotSp', 'tbTrTopSp'], ['cbTrBotBt', 'cbTrTopBt'], ['chkTrBot', 'chkTrTop']),
            'Nose': ([], ['cbNoseBt'], ['chkNose']),
        }
        for g, (tbs, cbs, chks) in groups.items():
            for n in tbs:
                getattr(self, n).TextChanged += ev(g)
            for n in cbs:
                getattr(self, n).SelectionChanged += ev(g)
            for n in chks:
                getattr(self, n).Click += ev(g)
        for b in (self.btnShowBot, self.btnShowTop, self.btnShowTr):
            b.Click += lambda s, a: self._redraw()
        self.profCanvas.SizeChanged += self._on_resize
        self.secCanvas.SizeChanged += self._on_resize
        self.Loaded += self._on_resize

    # ------------------------------------------------------------ idioma
    def _tagged(self):
        out = []
        stack = [self.Content]
        n = 0
        while stack and n < 20000:
            e = stack.pop()
            n += 1
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
            p = TEXTS.get(key)
            if p is None:
                continue
            if isinstance(el, HeaderedContentControl):
                el.Header = p[L]
            elif isinstance(el, TextBlock):
                el.Text = p[L]
            elif isinstance(el, ContentControl):
                el.Content = p[L]
        for name, p in TIPS.items():
            el = getattr(self, name, None)
            if el is not None:
                el.ToolTip = p[L]
        self._fill_dyn()
        ML.translate(self)
        if self._ready:
            m = self._mute
            self._mute = True
            self._update_ui()
            self._mute = m
            self._refresh()

    def _on_lang(self, s, a):
        self.lang = 1 if self.cbLang.SelectedIndex == 1 else 0
        self._apply_language()

    # ------------------------------------------------------------ editores
    def _load_editors(self):
        m = self._mute
        self._mute = True
        c = self.cfg
        self._set(self.tbCovB, c['cover_b'])
        self._set(self.tbCovT, c['cover_t'])
        self._set(self.tbCovS, c['cover_s'])
        self.tbPartition.Text = c.get('partition') or u''
        lc = c['lap']
        self._set(self.tbLap, lc.get('k', 40.0))
        self.chkStock.IsChecked = bool(lc.get('stock_on', True))
        self._set(self.tbStock, lc.get('stock', 1200.0))
        self.tbStock.IsEnabled = bool(lc.get('stock_on', True))
        self._cb_fill(self.cbLapStyle, G.LP.style_items(self.lang), G.LP.STYLES.index(G.LP.lap_style(lc)))
        self.tbLapPos.Text = lc.get('pos') or u''
        b, t, tr, no = c['bot'], c['top'], c['tr'], c['nose']
        self.cbBotBt.SelectedIndex = self._bt_idx(b['bt'])
        for tbn, k in (('tbBotSp', 'sp'), ('tbBotExt0', 'ext0'), ('tbBotExt1', 'ext1'), ('tbBotLeg0', 'leg0'),
                       ('tbBotLeg1', 'leg1'), ('tbBotLd', 'ld')):
            self._set(getattr(self, tbn), b[k])
        self.chkBotSplit.IsChecked = bool(b['split'])
        self.chkTopOn.IsChecked = bool(t['on'])
        self.cbTopBt.SelectedIndex = self._bt_idx(t['bt'])
        for tbn, k in (('tbTopSp', 'sp'), ('tbTopL0', 'L0'), ('tbTopL1', 'L1'), ('tbTopLeg0', 'leg0'),
                       ('tbTopLeg1', 'leg1'), ('tbTopLd', 'ld')):
            self._set(getattr(self, tbn), t[k])
        self.chkTopSplit.IsChecked = bool(t['split'])
        self.chkTrBot.IsChecked = bool(tr['bot_on'])
        self.cbTrBotBt.SelectedIndex = self._bt_idx(tr['bot_bt'])
        self._set(self.tbTrBotSp, tr['bot_sp'])
        self.chkTrTop.IsChecked = bool(tr['top_on'])
        self.cbTrTopBt.SelectedIndex = self._bt_idx(tr['top_bt'])
        self._set(self.tbTrTopSp, tr['top_sp'])
        self.chkNose.IsChecked = bool(no['on'])
        self.cbNoseBt.SelectedIndex = self._bt_idx(no['bt'])
        self._fill_dyn()
        self._update_ui()
        self._mute = m

    def _update_ui(self):
        c = self.cfg
        esc = c.get('kind') == 'escalera'
        self.tabSteps.IsEnabled = esc
        if not esc and self._tab() == 3:
            self.tabs.SelectedIndex = 0
        ends = c['top'].get('mode') == 'ends'
        self.tbTopL0.IsEnabled = self.tbTopL1.IsEnabled = ends
        self.chkTopSplit.IsEnabled = not ends
        on = bool(c['top']['on'])
        for n in ('cbTopBt', 'tbTopSp', 'cbTopMode', 'tbTopLeg0', 'tbTopLeg1', 'tbTopLd'):
            getattr(self, n).IsEnabled = on
        self.chkTrTop.IsEnabled = on
        self.tbBotLd.IsEnabled = bool(c['bot']['split'])
        m = self.model
        if len(m.noses) >= 2:
            self.txtSteps.Text = self.T('steps') % (len(m.noses), fmt(m.tread), fmt(m.riser))
        else:
            self.txtSteps.Text = self.T('no_steps')

    def _on_edit(self, g):
        if self._mute or not self._ready:
            return
        c = self.cfg
        if g == 'Gen':
            for tb, k in ((self.tbCovB, 'cover_b'), (self.tbCovT, 'cover_t'), (self.tbCovS, 'cover_s')):
                v = self._num(tb, 1.0, 10.0)
                if v is not None:
                    c[k] = v
            c['partition'] = (self.tbPartition.Text or u'').strip()
            c['kind'] = 'rampa' if self.cbKind.SelectedIndex == 1 else 'escalera'
            lc = c['lap']
            v = self._num(self.tbLap, 1.0, 200.0)
            if v is not None:
                lc['k'] = v
            v = self._num(self.tbStock, 100.0, 5000.0)
            if v is not None:
                lc['stock'] = v
            lc['stock_on'] = bool(self.chkStock.IsChecked)
            i_ls = self.cbLapStyle.SelectedIndex
            G.LP.set_style(lc, G.LP.STYLES[i_ls] if 0 <= i_ls < len(G.LP.STYLES) else 'offset')
            self.tbStock.IsEnabled = lc['stock_on']
            vals = G.LP.parse_positions(self.tbLapPos.Text)
            self._mark(self.tbLapPos, vals is not None)
            if vals is not None:
                lc['pos'] = G.LP.fmt_positions(vals)
        elif g == 'Bot':
            b = c['bot']
            b['bt'] = self._bt_of(self.cbBotBt)
            b['split'] = bool(self.chkBotSplit.IsChecked)
            for tb, key, mn, mx in ((self.tbBotSp, 'sp', 5.0, 60.0), (self.tbBotExt0, 'ext0', 0.0, 300.0),
                                    (self.tbBotExt1, 'ext1', 0.0, 300.0), (self.tbBotLeg0, 'leg0', 0.0, 100.0),
                                    (self.tbBotLeg1, 'leg1', 0.0, 100.0), (self.tbBotLd, 'ld', 10.0, 300.0)):
                v = self._num(tb, mn, mx)
                if v is not None:
                    b[key] = v
        elif g == 'Top':
            t = c['top']
            t['on'] = bool(self.chkTopOn.IsChecked)
            t['bt'] = self._bt_of(self.cbTopBt)
            t['mode'] = 'ends' if self.cbTopMode.SelectedIndex == 1 else 'full'
            t['split'] = bool(self.chkTopSplit.IsChecked)
            for tb, key, mn, mx in ((self.tbTopSp, 'sp', 5.0, 60.0), (self.tbTopL0, 'L0', 20.0, 2000.0),
                                    (self.tbTopL1, 'L1', 20.0, 2000.0), (self.tbTopLeg0, 'leg0', 0.0, 100.0),
                                    (self.tbTopLeg1, 'leg1', 0.0, 100.0), (self.tbTopLd, 'ld', 10.0, 300.0)):
                v = self._num(tb, mn, mx)
                if v is not None:
                    t[key] = v
        elif g == 'Tr':
            tr = c['tr']
            tr['bot_on'] = bool(self.chkTrBot.IsChecked)
            tr['top_on'] = bool(self.chkTrTop.IsChecked)
            tr['bot_bt'] = self._bt_of(self.cbTrBotBt)
            tr['top_bt'] = self._bt_of(self.cbTrTopBt)
            for tb, key in ((self.tbTrBotSp, 'bot_sp'), (self.tbTrTopSp, 'top_sp')):
                v = self._num(tb, 5.0, 60.0)
                if v is not None:
                    tr[key] = v
        elif g == 'Nose':
            c['nose']['on'] = bool(self.chkNose.IsChecked)
            c['nose']['bt'] = self._bt_of(self.cbNoseBt)
        self._mute = True
        self._update_ui()
        self._mute = False
        self._refresh()

    def _on_rot(self, s, a):
        if self._mute or not self._ready:
            return
        rot = 1 if self.cbRot.SelectedIndex == 1 else 0
        host = ML.host_of(self)
        if host is not None and self.ctx.get('rotate') is not None:
            # no modal: la geometria se relee en Revit con ExternalEvent
            rotate = self.ctx['rotate']

            def done(m, err):
                if err:
                    self.txtStatus.Text = u'Error: %s' % ML._short(err)
                self._rot_done(rot, None if err else m)

            if not host.call(lambda uiapp: rotate(uiapp, rot), done):
                self._rot_done(rot, None)
            return
        fn = self.ctx.get('rebuild')
        if fn is None:
            return
        try:
            m = fn(rot)
        except Exception as ex:
            m = None
            self.txtStatus.Text = u'Error: %s' % ex
        self._rot_done(rot, m)

    def _rot_done(self, rot, m):
        if m is None:
            self._mute = True
            self.cbRot.SelectedIndex = int(self.cfg.get('rot', 0))
            self._mute = False
            return
        self.cfg['rot'] = rot
        self.model = m
        self.cfg['kind'] = m.guess_kind()
        self._mute = True
        self._fill_dyn()
        self._update_ui()
        self._mute = False
        self._refresh()

    # ------------------------------------------------------------ vista
    def _on_tab(self, s, a):
        if not Object.ReferenceEquals(a.OriginalSource, self.tabs):
            return
        if self._ready:
            self._redraw()

    def _on_resize(self, s, a):
        if self._ready:
            self._redraw()

    def _refresh(self):
        if not self._ready:
            return
        try:
            self.res = G.compute(self.model, self.cfg, self.bts)
        except Exception as ex:
            self.res = None
            self.txtStatus.Text = u'Error: %s' % ex
            return
        self._redraw()
        self._update_texts()

    def _redraw(self):
        if self.res is None:
            return
        try:
            self._draw_profile()
            self._draw_section()
        except Exception as ex:
            self.txtStatus.Text = u'Error de dibujo: %s' % ex

    def _draw_profile(self):
        cv = self.profCanvas
        cv.Children.Clear()
        W, H = cv.ActualWidth, cv.ActualHeight
        if W < 60 or H < 60:
            return
        m = self.model
        dr = self.res['draw']
        xs = [p[0] for p in m.poly]
        zs = [p[1] for p in m.poly]
        x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
        pad = 36.0
        k = min((W - 2 * pad) / max(x1 - x0, 1.0), (H - 2 * pad) / max(z1 - z0, 1.0))
        ox = (W - (x1 - x0) * k) / 2.0
        oy = H - (H - (z1 - z0) * k) / 2.0

        def P(x, z):
            return (ox + (x - x0) * k, oy - (z - z0) * k)

        d_poly(cv, [P(*p) for p in m.poly], C['conc_line'], 1.4, True, None, C['conc'])
        sb, st, sr = bool(self.btnShowBot.IsChecked), bool(self.btnShowTop.IsChecked), bool(self.btnShowTr.IsChecked)
        bt = self.bts.get(self.cfg['bot']['bt'])
        tt = self.bts.get(self.cfg['top']['bt'])
        # longitudinales: se dibujan las piezas reales (con traslapes y bayonetas exageradas para verse)
        for kind, show, bb in (('long_bot', sb, bt), ('long_top', st, tt)):
            if not show:
                continue
            thk = max(1.5, (bb['dm'] if bb else 1.0) * k * 0.7)
            for i, b in enumerate([x for x in self.res['bars'] if x['kind'] == kind]):
                pts2 = [(q[0], q[2]) for q in b['pts']]
                if b.get('lap_style') == 'offset' and b.get('lap_piece', 0) % 2:
                    # desfasado (a lo ancho, no se ve en el perfil): se dibuja 3 px arriba
                    pts2 = [(q[0], q[1] + 3.0 / k) for q in pts2]
                elif b.get('lap_style') == 'crank' and b.get('lap_n') and b.get('lap_piece', 0) < b['lap_n'] - 1 \
                        and len(pts2) >= 4:
                    # exagera la bayoneta a 4 px
                    ex = 4.0 / k
                    p4, p3, p2 = pts2[-4], pts2[-3], pts2[-2]
                    dx, dz = p3[0] - p4[0], p3[1] - p4[1]
                    L = math.hypot(dx, dz) or 1.0
                    nx, nz = -dz / L, dx / L
                    sg = 1.0 if (p2[0] - p3[0]) * nx + (p2[1] - p3[1]) * nz >= 0 else -1.0
                    sh = (nx * ex * sg, nz * ex * sg)
                    pts2 = pts2[:-2] + [(pts2[-2][0] + sh[0], pts2[-2][1] + sh[1]), (pts2[-1][0] + sh[0], pts2[-1][1] + sh[1])]
                d_poly(cv, [P(*q) for q in pts2], KC[kind], thk, False, (6, 2) if i % 2 else None)
        if sr:
            for key, col in (('tr_bot', KC['tr_bot']), ('tr_top', KC['tr_top'])):
                if (key == 'tr_bot' and not sb) or (key == 'tr_top' and not st):
                    continue
                for q in dr.get(key, []):
                    x, y = P(*q)
                    d_circle(cv, x, y, 2.4, None, col)
        for q in dr.get('nose', []):
            x, y = P(*q)
            d_circle(cv, x, y, 3.2, '#D8F5D0', KC['nose'], 0.6)
        # cotas
        a = P(x0, z0)
        b = P(x1, z0)
        d_text(cv, (a[0] + b[0]) / 2.0, a[1] + 6, u'L = %s   ·   H = %s' % (fmt(x1 - x0), fmt(z1 - z0)), C['text'], 10.5, 'ct')
        # flecha de subida
        s0 = P(x0 + (x1 - x0) * 0.08, z1 - (z1 - z0) * 0.05)
        d_line(cv, s0[0], s0[1], s0[0] + 40, s0[1], C['bright'], 1.3)
        d_line(cv, s0[0] + 40, s0[1], s0[0] + 34, s0[1] - 4, C['bright'], 1.3)
        d_line(cv, s0[0] + 40, s0[1], s0[0] + 34, s0[1] + 4, C['bright'], 1.3)
        d_text(cv, s0[0] + 44, s0[1], self.T('up'), C['bright'], 10.5, 'lc')

    def _main_seg(self):
        m = self.model
        best = None
        for i, (a, b) in enumerate(zip(m.lower, m.lower[1:])):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            incl = abs(b[1] - a[1]) > 0.1 * L
            key = (1 if incl else 0, L)
            if best is None or key > best[0]:
                best = (key, i)
        return best[1] if best else 0

    def _draw_section(self):
        cv = self.secCanvas
        cv.Children.Clear()
        W, H = cv.ActualWidth, cv.ActualHeight
        if W < 60 or H < 40:
            return
        m = self.model
        c = self.cfg
        i = self._main_seg()
        T = m.seg_t[i] if m.seg_t else 15.0
        self._sec_t = T
        pad = 20.0
        k = min((W - 2 * pad) / max(m.W, 1.0), (H - 2 * pad) / max(T * 1.8, 1.0))
        ox = (W - m.W * k) / 2.0
        oy = (H + T * k) / 2.0

        def P(y, z):
            return (ox + y * k, oy - z * k)

        a, b = P(0, T), P(m.W, 0)
        d_rect(cv, a[0], a[1], b[0] - a[0], b[1] - a[1], C['conc_line'], C['conc'], 1.2)
        cb, ct, cs = c['cover_b'], c['cover_t'], c['cover_s']
        bb = self.bts.get(c['bot']['bt'])
        tb = self.bts.get(c['top']['bt'])
        rb = self.bts.get(c['tr']['bot_bt'])
        rt = self.bts.get(c['tr']['top_bt'])
        top_mid = c['top']['on'] and tb is not None and c['top'].get('mode') != 'ends'
        if bb and self.btnShowBot.IsChecked:
            if rb and c['tr']['bot_on'] and self.btnShowTr.IsChecked:
                z = cb + bb['dm'] + rb['dm'] / 2.0
                p0, p1 = P(cs + rb['dm'] / 2.0, z), P(m.W - cs - rb['dm'] / 2.0, z)
                d_line(cv, p0[0], p0[1], p1[0], p1[1], KC['tr_bot'], max(1.5, rb['dm'] * k))
            y0, dy, n = G._across(m.W, cs, bb['dm'], c['bot']['sp'])
            for j in range(n):
                x, y = P(y0 + dy * j, cb + bb['dm'] / 2.0)
                d_circle(cv, x, y, max(2.4, bb['dm'] / 2.0 * k), '#CDE8FF', KC['long_bot'], 0.6)
        if top_mid and self.btnShowTop.IsChecked:
            zt = T - ct - tb['dm'] / 2.0
            if rt and c['tr']['top_on'] and self.btnShowTr.IsChecked:
                z = T - ct - tb['dm'] - rt['dm'] / 2.0
                p0, p1 = P(cs + rt['dm'] / 2.0, z), P(m.W - cs - rt['dm'] / 2.0, z)
                d_line(cv, p0[0], p0[1], p1[0], p1[1], KC['tr_top'], max(1.5, rt['dm'] * k))
            y0, dy, n = G._across(m.W, cs, tb['dm'], c['top']['sp'])
            for j in range(n):
                x, y = P(y0 + dy * j, zt)
                d_circle(cv, x, y, max(2.4, tb['dm'] / 2.0 * k), '#FFE8C8', KC['long_top'], 0.6)
        d_text(cv, 6, 4, u't = %s cm' % fmt(T), C['text'], 10.5)

    # ------------------------------------------------------------ textos
    def _update_texts(self):
        m = self.model
        c = self.cfg
        xs = [p[0] for p in m.poly]
        zs = [p[1] for p in m.poly]
        self.txtProfHeader.Text = self.T('prof') % (fmt(max(xs) - min(xs)), fmt(max(zs) - min(zs)), fmt(m.throat()))
        self.txtSecHeader.Text = self.T('sec') % (fmt(m.W), fmt(getattr(self, '_sec_t', m.throat())))
        b, t, tr = c['bot'], c['top'], c['tr']
        top_txt = (u'%s @ %s%s' % (t['bt'], fmt(t['sp']), u'' if t.get('mode') != 'ends' else u' (L %s / %s)'
                                   % (fmt(t['L0']), fmt(t['L1'])))) if t['on'] else self.T('none')
        rep = u' / '.join(u'%s @ %s' % (tr[k + '_bt'], fmt(tr[k + '_sp'])) for k in ('bot', 'top') if tr[k + '_on'])
        self.txtSecFooter.Text = self.T('foot') % (b['bt'], fmt(b['sp']), top_txt, rep or self.T('none'))
        self.txtInfo.Text = self.ctx.get('desc', u'')
        w = G.summary(self.res)
        names = self.T('wnames')
        parts = u' · '.join(u'%s %.1f' % (names.get(key, key), v) for key, v in sorted(w.items()) if v > 0)
        self.txtSummary.Text = self.T('sum') % (parts, sum(w.values()))
        self.txtHint.Text = u''
        if self._bad:
            self.txtStatus.Text = self.T('bad')
        elif self.res['warnings']:
            wr = self.res['warnings']
            self.txtStatus.Text = wr[0] if len(wr) == 1 else self.T('warn_n') % (wr[0], len(wr) - 1)
        else:
            self.txtStatus.Text = u''

    # ------------------------------------------------------------ guardar / importar
    def _ask_name(self):
        return forms.ask_for_string(default=u'escalera', prompt=self.T('save_prompt'), title=TEXTS['save'][self.lang])

    def _confirm(self, msg):
        return forms.alert(msg, yes=True, no=True)

    def _pick_saved(self, names, last):
        items = ([self.T('last')] if last else []) + names + [self.T('browse')]
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
        names = [n for n in G.list_saved(folder) if n != KEY_LAST]
        last = os.path.exists(os.path.join(folder, KEY_LAST + u'.json'))
        pick = self._pick_saved(names, last)
        if not pick:
            return
        if pick == self.T('browse'):
            path = self._pick_file(folder)
        elif pick == self.T('last'):
            path = os.path.join(folder, KEY_LAST + u'.json')
        else:
            path = os.path.join(folder, pick + u'.json')
        if path:
            self.import_file(path)

    def import_file(self, path):
        try:
            data, diams = G.load_cfg(path)
            cfg = G.fit_cfg(data, self.model, self.bl)
            changes = G.remap_bar_names(cfg, diams, self.bl)
            cfg['kind'] = self.cfg.get('kind')        # el tipo y la direccion los define el elemento
            cfg['rot'] = self.cfg.get('rot', 0)
        except Exception as ex:
            self.txtStatus.Text = self.T('err_load') % ex
            return False
        self.cfg = cfg
        self._mute = True
        self._load_editors()
        self._mute = False
        self._refresh()
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
        if self.res is None or not self.res['bars']:
            self.txtStatus.Text = self.T('empty')
            return
        try:
            G.save_cfg(os.path.join(G.config_dir(), KEY_LAST + u'.json'), self.cfg, self.bts)
        except Exception:
            pass
        result = {'cfg': copy.deepcopy(self.cfg), 'res': self.res, 'replace': bool(self.chkReplace.IsChecked),
                  'model': self.model}
        if ML.apply(self, result):          # ventana no modal: crea el armado y sigue abierta
            return
        self.result = result
        self.Close()

    # ------------------------------------------------------------ otro elemento (no modal)
    def reload(self, ctx):
        """Carga otro elemento (o el mismo releido) conservando la configuracion."""
        self.ctx = ctx
        self.model = ctx['model']
        cfg = G.fit_cfg(self.cfg, self.model, self.bl)
        cfg['rot'] = 0
        cfg['kind'] = self.model.guess_kind()
        self.cfg = cfg
        self._mute = True
        self._load_editors()
        self._mute = False
        self._refresh()
