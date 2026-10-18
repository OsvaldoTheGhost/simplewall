# -*- coding: utf-8 -*-
"""Armado de muros desde Excel: lectura de la tabla y conversion a la configuracion de la
herramienta Armado Muro (aw_geom). Sin Revit ni WPF: se puede probar fuera de Revit.

Una fila = un tipo de muro (M-1, M-2...) en uno o varios niveles. El armado se aplica a todos los
muros de ese tipo cuyo nivel base sea el indicado. Unidades: cm. IronPython 2.7 y CPython 3."""
from __future__ import division
import copy
import math
import re

import vx_core as V                      # utilidades del boton Armado Trabes Excel (niveles, varillas...)

clean, fold, num, is_no, is_yes = V.clean, V.fold, V.num, V.is_no, V.is_yes
resolve_levels, level_label, elev_txt = V.resolve_levels, V.level_label, V.elev_txt
resolve_bt, parse_bar, numbered, SpecError = V.resolve_bt, V.parse_bar, V.numbered, V.SpecError

# ------------------------------------------------------------------ columnas de la hoja "Armado"
COLUMNS = [
    ('niveles', u'Niveles', 12, u'N2, N2-N4, N2,N5 o +7.55'),
    ('muro', u'Muro', 14, u'Nombre del tipo en Revit (M-1, M-2...)'),
    ('espesor', u'Espesor (cm)', 9, u'Informativo'),
    ('cant', u'Muros', 7, u'Informativo: cuántos muros de ese tipo hay en el nivel'),
    ('long', u'Longitudes (m)', 16, u'Informativo'),
    ('alto', u'Alto (m)', 8, u'Informativo'),
    ('cortinas', u'Parrillas', 9, u'1 o 2 (vacío = 2 si t >= 15 cm)'),
    ('vert', u'Verticales', 12, u'#4@20'),
    ('horiz', u'Horizontales', 12, u'#3@20'),
    ('borde_ext', u'Borde extremo libre', 24, u'8#6+4#4 60 2E@15 (varillas, Lb cm, estribos y grapas) o No'),
    ('borde_int', u'Borde en intersección', 24, u'Extremo que llega a otro muro (T). Vacío = igual al extremo libre'),
    ('borde_esq', u'Borde en esquina L', 28, u'Toda la L: 20#6+2#4 120/60 2E+2G@15 (Lb en el muro largo / en el corto)'),
    ('est_borde', u'Estribos borde', 14, u'Por omisión: E#3 @15'),
    ('huecos', u'Refuerzo huecos', 14, u'2#5 60 o No'),
    ('notas', u'Notas', 34, u''),
]
COL_KEYS = [c[0] for c in COLUMNS]
INFO_KEYS = ('espesor', 'cant', 'long', 'alto', 'notas')
DATA_KEYS = [k for k in COL_KEYS if k not in INFO_KEYS + ('niveles', 'muro')]

# ------------------------------------------------------------------ hoja "Configuracion"
SETTINGS = [
    ('cover', u'Recubrimiento (cm)', u'2.5', u'Al paño del muro (a la varilla exterior)'),
    ('outer', u'Varilla exterior', u'Horizontal', u'Horizontal o Vertical (la que va más cerca del paño)'),
    ('lap_k', u'Traslape (diámetros)', u'40', u''),
    ('stock', u'Longitud comercial (cm)', u'1200', u'Vacío o 0 = sin cortes por longitud comercial'),
    ('hook_bot', u'Pata en la base', u'Sí', u'Sí = verticales con pata en el arranque'),
    ('hook_len', u'Longitud de pata (cm)', u'30', u''),
    ('top_lap', u'Espera arriba', u'Sí', u'Sí = las verticales salen arriba el traslape para el siguiente nivel'),
    ('h_end', u'Remate de horizontales', u'Gancho 90', u'Gancho 90, Recto o U (horquilla, 2 parrillas)'),
    ('h_leg', u'Pata de horizontales (cm)', u'15', u''),
    ('h_first', u'Primera horizontal (cm)', u'5', u'Desde la base'),
    ('lb_into', u'Borde entra al muro transversal', u'Sí', u'Sí = en intersecciones Lb se mide desde el paño lejano del otro muro'),
    ('corners', u'Esquinas en L', u'Sí', u'Sí = los muros que forman esquinas en L (L, U, Z...) se arman juntos con el borde en L'),
    ('lc', u'Longitud confinada de estribos (cm)', u'60', u'Para "E#3 @10 @20" sin L'),
    ('first', u'Primer estribo (cm)', u'5', u'Desde la base / la corona'),
    ('op_ext', u'Anclaje refuerzo de huecos (cm)', u'60', u'Para "2#5" sin longitud'),
    ('replace', u'Reemplazar armado previo', u'Sí', u'Sí = borra el armado que hizo la herramienta en ese muro'),
    ('param', u'Parámetro de nivel en varillas', u'Nivel', u'Vacío = no se llena'),
    ('param_fmt', u'Valor del parámetro de nivel', u'NIVEL {elev}', u'{elev} = +7.55, {nivel} = nombre del nivel'),
    ('montaje', u'Crear montajes', u'Sí', u'Sí = un montaje por muro con su acero'),
    ('montaje_fmt', u'Nombre del montaje', u'Muro {muro} Nivel {elev}', u'{muro} = M-1; {elev} = +16.10; {nivel} = nombre del nivel'),
]
SETTING_KEYS = [s[0] for s in SETTINGS]


def settings_from(pairs):
    lab = dict((fold(s[1]), s[0]) for s in SETTINGS)
    out = dict((s[0], s[2]) for s in SETTINGS)
    for k, v in pairs:
        key = lab.get(fold(k))
        if key:
            out[key] = clean(v)
    return out


# ================================================================== nombres
def norm_wall(name):
    """'MURO M-1' == 'M-1' == 'm 1' == 'M1'."""
    s = fold(name).replace(u'–', u'-').replace(u'—', u'-')
    s = re.sub(r'^(muros?|mu?\.)\s+', u'', s)
    s = re.sub(r'\s*-\s*', u'-', s)
    s = re.sub(r'^([a-z]+)\s*-?\s*(\d)', r'\1-\2', s)
    return re.sub(r'\s+', u' ', s).strip()


def wall_mark(type_name):
    """Muro del plano a partir del tipo de Revit: 'Muro M-1' -> 'M-1'; 'Muro M-2 Nivel -2.65' -> 'M-2'."""
    t = re.sub(r'^(muros?)\s+', u'', clean(type_name), flags=re.I)
    t = re.sub(r'\s+(nivel|niv\.?|n)\s*[+-]?\s*\d+(?:[.,]\d+)?\s*$', u'', t, flags=re.I)
    return t.strip()


def assembly_name(fmt, mark, level_name, elev_m):
    return (fmt or u'Muro {muro} Nivel {elev}').replace(u'{muro}', mark).replace(
        u'{elev}', elev_txt(elev_m)).replace(u'{nivel}', level_name)


def group_rows(rows):
    """[(niveles, muro, fila)] en el orden de la hoja; celdas vacias de Niveles / Muro heredan la anterior."""
    out, errs = [], []
    seen = {}
    last_lv = last_mu = u''
    for r in rows:
        lv = clean(r.get('niveles')) or last_lv
        mu = clean(r.get('muro')) or last_mu
        last_lv, last_mu = lv, mu
        if not any(clean(r.get(k)) for k in DATA_KEYS):
            continue
        key = (fold(lv), norm_wall(mu))
        if key in seen:
            errs.append(u'fila %s: el muro %s ya tiene fila para %s (fila %s); se usa la primera'
                        % (r.get('_row'), mu, lv, seen[key]))
            continue
        seen[key] = r.get('_row')
        out.append((lv, mu, r))
    return out, errs


# ================================================================== lectura de celdas
_GRID = re.compile(r'^(?:(\d+)\s*)?(#\s*\d+(?:\.\d+)?|[^\s@#]+)\s*@\s*(\d+(?:[.,]\d+)?)$')


def parse_grid(text, bts_list):
    """'#4@20' -> ('#4', 20.0); 'No' -> None."""
    t = clean(text).replace(u'Ø', u'#').replace(u'ø', u'#').replace(u' ', u'')
    if not t:
        return u''
    if is_no(t):
        return None
    m = _GRID.match(t)
    if not m:
        raise SpecError(u'no se entiende "%s" (use p. ej. #4@20)' % clean(text))
    sp = float(m.group(3).replace(u',', u'.'))
    if sp < 5:
        raise SpecError(u'separación muy chica en "%s"' % clean(text))
    return resolve_bt(m.group(2), bts_list), sp


_BORDER_BARS = re.compile(r'^\d+\s*[xX×]?\s*#?\s*\d+(?:\.\d+)?(?:\s*\+\s*\d+\s*[xX×]?\s*#?\s*\d+(?:\.\d+)?)*$')
_ST = re.compile(r'^(?:(\d+)\s*(?:EST\.?|E)\s*(#\s*\d+)?)?(?:\s*\+?\s*(\d+)\s*(?:GR\.?|GP\.?|G)\s*(#\s*\d+)?)?\s*(?:@\s*(\d+(?:[.,]\d+)?))?\s*(?:cm)?$', re.I)


def _one_border(t, bts_list):
    """'8#6+4#4 60 2E@15' / '22#8+8#6 200/60 6E#3+1GR#3@15cm' / 'Columna' -> dict."""
    t = clean(t).replace(u'VARS', u'').replace(u'VAR', u'').replace(u'Ø', u'#')
    if fold(t) in (u'col', u'columna', u'castillo', u'ver columna'):
        return 'col'
    t = re.sub(r'\s*\+\s*', u'+', t)
    toks = t.split(u' ')
    # varillas: el primer bloque con '#'
    bars_txt = toks[0]
    if not _BORDER_BARS.match(bars_txt):
        raise SpecError(u'no se entiende "%s" (use p. ej. 8#6+4#4 60 2E@15)' % t)
    bars = []
    for part in bars_txt.split(u'+'):
        n, bt = parse_bar(part, bts_list)
        bars.append((n, bt))
    rest = u' '.join(toks[1:]).strip()
    lb = lb2 = None
    m = re.match(r'^(?:L\s*=?\s*)?(\d+(?:[.,]\d+)?)(?:\s*/\s*(\d+(?:[.,]\d+)?))?(?:\s*cm)?(?:\s+|$)(.*)$', rest, re.I)
    if m:
        lb = float(m.group(1).replace(u',', u'.'))
        lb2 = float(m.group(2).replace(u',', u'.')) if m.group(2) else None
        rest = m.group(3).strip()
    out = {'bars': bars, 'lb': lb, 'lb2': lb2, 'ne': None, 'ng': 0, 'st_bt': None, 'sp': None}
    if rest:
        r2 = rest.replace(u' ', u'')
        m = _ST.match(r2)
        if not m or not (m.group(1) or m.group(3) or m.group(5)):
            raise SpecError(u'no se entienden los estribos "%s" (use p. ej. 2E@15, 1E+1G@15, 6EST#3+1GR#3@15cm)' % rest)
        if m.group(1):
            out['ne'] = int(m.group(1))
        if m.group(3):
            out['ng'] = int(m.group(3))
        bt = m.group(2) or m.group(4)
        if bt:
            out['st_bt'] = resolve_bt(bt.replace(u' ', u''), bts_list)
        if m.group(5):
            out['sp'] = float(m.group(5).replace(u',', u'.'))
    return out


def parse_border(text, bts_list):
    """Celda de borde -> lista de alternativas [(spec del muro largo, spec del muro corto)] separadas por ';'
    (una por esquina, en orden de planta). 'A / B' = A en el muro mas largo de la L y B en el mas corto.
    'No' -> None; '' -> ''."""
    t = clean(text)
    if not t:
        return u''
    if is_no(t):
        return None
    out = []
    for alt in [a for a in t.split(u';') if a.strip()]:
        parts = [p for p in re.split(r'\s+/\s+', alt.strip()) if p.strip()]
        specs = []
        for p in parts[:2]:
            specs.append(None if is_no(p) else _one_border(p, bts_list))
        if len(specs) == 1:
            specs.append(specs[0])
        out.append(tuple(specs))
    return out


def pick(alts, k=0, short=False):
    """Spec de la alternativa k (se repite la ultima) para el muro largo o el corto."""
    if not alts:
        return alts
    a = alts[min(k, len(alts) - 1)]
    return a[1] if short else a[0]


def parse_bstir(text, bts_list, settings):
    """'E#3 @15' -> uniforme; 'E#3 @10 @20' -> @10 en la longitud confinada (Configuracion) y @20 al centro;
    'E#3 @10 @20 L60' -> confinada de 60 cm. 'No' -> None."""
    t = clean(text)
    if not t:
        return u''
    if is_no(t):
        return None
    t = t.replace(u',', u' ')
    bt = None
    m = re.match(r'^[Ee]\s*(#?\s*\d+(?:\.\d+)?)\s*(.*)$', t)
    if m:
        bt = resolve_bt(m.group(1).replace(u' ', u''), bts_list)
        t = m.group(2)
    seps, lc = [], None
    for tok in re.findall(r'[Ll]\s*=?\s*\d+(?:\.\d+)?|@\s*\d+(?:\.\d+)?|\S+', t):
        tt = tok.replace(u' ', u'')
        if tt.startswith(u'@'):
            seps.append(float(tt[1:]))
        elif tt[:1] in (u'L', u'l'):
            lc = float(tt.lstrip(u'Ll=').replace(u',', u'.'))
        else:
            raise SpecError(u'no se entiende "%s" en los estribos "%s"' % (tok, clean(text)))
    if not seps or len(seps) > 2:
        raise SpecError(u'estribos "%s": use E#3 @15 o E#3 @10 @20' % clean(text))
    if min(seps) < 5:
        raise SpecError(u'separación de estribos muy chica en "%s"' % clean(text))
    sc = seps[0]
    sm = seps[1] if len(seps) > 1 else seps[0]
    if lc is None:
        lc = num(settings.get('lc'), 60.0) if len(seps) > 1 else 0.0
    return {'bt': bt, 'sc': sc, 'sm': sm, 'lc': lc}


def parse_open(text, bts_list, settings):
    """'2#5 60' -> {'n': 2, 'bt': '#5', 'ext': 60}; 'No' -> None."""
    t = clean(text)
    if not t:
        return u''
    if is_no(t):
        return None
    m = re.match(r'^(\d+\s*[xX×]?\s*#?\s*\d+(?:\.\d+)?)\s*(\d+(?:[.,]\d+)?)?\s*(?:cm)?$', t)
    if not m:
        raise SpecError(u'no se entiende el refuerzo de huecos "%s" (use p. ej. 2#5 60)' % t)
    n, bt = parse_bar(m.group(1).replace(u' ', u''), bts_list)
    ext = float(m.group(2).replace(u',', u'.')) if m.group(2) else num(settings.get('op_ext'), 60.0)
    return {'n': n, 'bt': bt, 'ext': ext}


# ================================================================== acomodo de un elemento de borde
def _expand(bars, bts):
    """[(n, '#6'), (n, '#4')] -> lista de tipos de varilla de mayor a menor diametro."""
    out = []
    for n, name in bars:
        out += [bts[name]] * int(n)
    out.sort(key=lambda b: -b['dm'])
    return out


def layout_zone(CG, sec, cover, ds, bar_list, ne, ng, x0=None):
    """Varillas y estribos de un elemento de borde (seccion sec, rectangular b x t) con las mismas cantidades que
    el plano: las varillas se reparten en las dos caras, las mas gruesas en los extremos y en paquetes de 2 si no
    caben sueltas; ne estribos cerrados (el 1o perimetral, los demas envolviendo grupos de varillas) y ng grapas.
    x0: coordenada (seccion, centro = 0) desde donde se pueden poner varillas (brazo B de una esquina)."""
    if not bar_list:
        return [], []
    dmax = max(b['dm'] for b in bar_list)
    off = cover + ds + dmax / 2.0
    xa = -sec.hx + off if x0 is None else max(x0 + dmax / 2.0 + 1.0, -sec.hx + off)
    xb = sec.hx - off
    if xb < xa:
        xa = xb = (xa + xb) / 2.0
    N = len(bar_list)
    m = int(math.ceil(N / 2.0))
    nb = 1
    if m > 1 and (xb - xa) / (m - 1) < dmax + 2.5:
        nb = 2
    cols = int(math.ceil(m / float(nb)))
    xs = [xa + (xb - xa) * i / (cols - 1.0) for i in range(cols)] if cols > 1 else [(xa + xb) / 2.0]
    order = []
    i, j = 0, cols - 1
    while i <= j:
        order.append(i)
        if j != i:
            order.append(j)
        i += 1
        j -= 1
    pool = list(bar_list)
    slots = {}                                   # (col, cara) -> Bar
    for c in order:
        for face in (1, -1):
            if not pool:
                break
            bt = pool[0]
            k = 1
            if nb == 2 and len(pool) >= 2 and pool[1]['name'] == bt['name']:
                k = 2
            del pool[:k]
            ax, ay = sec.bar_region(cover, ds, bt['dm'])
            x = xs[c]
            y = face * ay
            b = CG.Bar(x / ax if ax > 1e-6 else 0.0, y / ay if ay > 1e-6 else 0.0, bt, k)
            b.dx, b.dy = (1.0 if x < 0 else -1.0), None
            slots[(c, face)] = b
    bars = [slots[k] for k in sorted(slots)]
    used = sorted(set(c for c, _f in slots))
    sts = []
    ne = max(int(ne or 1), 1)

    def wrap(c0, c1):
        a = slots.get((c0, 1)) or slots.get((c0, -1))
        b = slots.get((c1, -1)) or slots.get((c1, 1))
        if a is None or b is None:
            return None
        return CG.Stirrup('rect2', [('bar', a), ('bar', b)], True)

    if x0 is None:
        sts.append(CG.Stirrup('perim', [], True))
    else:
        st = wrap(used[0], used[-1])
        if st is not None:
            sts.append(st)
    extra = ne - 1
    if extra > 0 and len(used) >= 2:
        inner = used[1:-1] if (extra == 1 and len(used) >= 3) else used
        size = max(1, int(math.ceil(len(inner) / float(extra))))
        groups = [inner[i:i + size] for i in range(0, len(inner), size)]
        while len(groups) < extra:
            groups.append(groups[-1])
        for g in groups[:extra]:
            g0, g1 = g[0], g[-1]
            if g0 == g1:                               # un estribo alrededor de una sola columna: con la vecina
                g1 = used[min(used.index(g0) + 1, len(used) - 1)]
                if g1 == g0:
                    g0 = used[max(used.index(g0) - 1, 0)]
            st = wrap(g0, g1)
            if st is not None:
                sts.append(st)
    if ng:
        both = [c for c in used if (c, 1) in slots and (c, -1) in slots]
        cand = [c for c in both[1:-1]] or both or used
        step = len(cand) / float(ng + 1)
        for k in range(int(ng)):
            c = cand[min(int(round((k + 1) * step)) - 1 if len(cand) > ng else k % len(cand), len(cand) - 1)]
            a, b = slots.get((c, 1)), slots.get((c, -1))
            if a is not None and b is not None:
                sts.append(CG.Stirrup('tie', [('bar', a), ('bar', b)], True))
    return bars, sts


def _bt_spacing(spec, cfg, bts):
    """Ajusta los estribos de borde (tipo y separacion) con los del elemento."""
    bst = cfg['bstir']
    if spec.get('st_bt') and spec['st_bt'] in bts:
        bst['bt'] = spec['st_bt']
    if spec.get('sp'):
        bst['sc'] = bst['sm'] = spec['sp']
        bst['lc'] = 0.0


# ================================================================== configuracion
def end_specs(row, bts_list):
    """{'ext', 'int', 'esq'} -> alternativas (parse_border) y errores. Vacios: intersección = extremo libre;
    esquina = intersección."""
    errs = []
    out = {}
    for kind, key, lab in (('ext', 'borde_ext', u'Borde extremo libre'), ('int', 'borde_int', u'Borde en intersección'),
                           ('esq', 'borde_esq', u'Borde en esquina L')):
        try:
            out[kind] = parse_border(row.get(key), bts_list)
        except SpecError as ex:
            errs.append(u'%s: %s' % (lab, ex))
            out[kind] = u''
    out['esq_set'] = out['esq'] != u''
    if out['int'] == u'':
        out['int'] = out['ext']
    if out['esq'] == u'':
        out['esq'] = out['int']
    return out, errs


def end_kind(model, side):
    """'col' si el extremo llega a una columna, 'int' si llega a otro muro (T o esquina sin armar junta),
    'ext' si es libre."""
    if side in (getattr(model, 'col_ends', None) or ()):
        return 'col'
    e = (getattr(model, 'ends', None) or {}).get(side)
    return 'int' if e else 'ext'


def _base_cfg(W, model, bts_list, settings, row, warns, errs):
    bts = dict((b['name'], b) for b in bts_list)
    cfg = W.default_cfg(model, bts_list)
    cfg['cover'] = num(settings.get('cover'), 2.5)
    g = cfg['gen']
    g['lap_f'] = num(settings.get('lap_k'), 40.0)
    stock = num(settings.get('stock'), 1200.0)
    g['stock_on'] = bool(stock and stock > 0)
    if stock:
        g['stock'] = stock
    g['hook_bot'] = not is_no(settings.get('hook_bot') or u'Sí')
    g['hook_len'] = num(settings.get('hook_len'), 30.0)
    g['top_lap'] = not is_no(settings.get('top_lap') or u'Sí')
    g['elev'] = False
    web = cfg['web']
    web['outer'] = 'v' if fold(settings.get('outer')).startswith(u'v') else 'h'
    he = fold(settings.get('h_end'))
    web['h_end'] = 'u' if he in (u'u', u'horquilla', u'horquillas') else ('straight' if he.startswith(u'rect') else 'hook90')
    web['h_leg'] = num(settings.get('h_leg'), 15.0)
    web['h_first'] = num(settings.get('h_first'), 5.0)
    labels = dict((c[0], c[1]) for c in COLUMNS)

    def cell(key, fn, *a):
        try:
            return fn(row.get(key), bts_list, *a)
        except SpecError as ex:
            errs.append(u'%s: %s' % (labels[key], ex))
            return u''

    cur = clean(row.get('cortinas'))
    if cur:
        k = num(cur)
        if k not in (1.0, 2.0):
            errs.append(u'Parrillas: "%s" (use 1 o 2)' % cur)
        else:
            web['curtains'] = int(k)
    v = cell('vert', parse_grid)
    h = cell('horiz', parse_grid)
    if v is None:
        web['v_on'] = False
    elif v:
        web['v_bt'], web['v_sp'] = v
    if h is None:
        web['h_on'] = False
    elif h:
        web['h_bt'], web['h_sp'] = h
    if v == u'' and h == u'':
        warns.append(u'sin Verticales ni Horizontales: se usa la malla por defecto (%s@%s / %s@%s)'
                     % (web['v_bt'], V.fmt_cm(web['v_sp']), web['h_bt'], V.fmt_cm(web['h_sp'])))
    if web['h_end'] == 'u' and web['curtains'] < 2:
        web['h_end'] = 'hook90'
    bst = cfg['bstir']
    bst['first'] = num(settings.get('first'), 5.0)
    bst['sc'] = bst['sm'] = 15.0
    bst['lc'] = 0.0
    st = cell('est_borde', parse_bstir, settings)
    if st is None:
        bst['on'] = False
    elif st:
        if st['bt']:
            bst['bt'] = st['bt']
        bst['sc'], bst['sm'], bst['lc'] = st['sc'], st['sm'], st['lc']
    op = cell('huecos', parse_open, settings)
    o = cfg['open']
    if op is None:
        o['on'] = False
    elif op:
        o['on'] = True
        o['n'], o['bt'], o['ext'] = op['n'], op['bt'], op['ext']
    elif model.openings:
        warns.append(u'el muro tiene %d hueco(s) y no hay "Refuerzo huecos": se usa %d%s %s cm'
                     % (len(model.openings), o['n'], o['bt'], V.fmt_cm(o['ext'])))
    return cfg, bts


def build_cfg(W, CG, model, bts_list, settings, row):
    """Muro suelto -> (cfg, zones, avisos, errores)."""
    warns, errs = [], []
    cfg, bts = _base_cfg(W, model, bts_list, settings, row, warns, errs)
    specs, e = end_specs(row, bts_list)
    errs += e
    b = cfg['bnd']
    b['mirror'] = False
    into = not is_no(settings.get('lb_into') or u'Sí')
    b['intoL'] = b['intoR'] = into
    zones = {'L': ([], []), 'R': ([], [])}
    sbt = bts.get(cfg['bstir']['bt'])
    for key in ('L', 'R'):
        b['len' + key] = 0.0
        b[key] = {'bars': [], 'stirrups': []}
    sides = []
    for key in ('L', 'R'):
        kind = end_kind(model, key)
        spec = None if kind == 'col' else pick(specs[kind])
        if spec == 'col':
            spec = None
        sides.append((key, spec))
    for key, spec in sides:
        if not spec:
            continue
        lb = spec['lb'] or 60.0
        if not spec['lb']:
            warns.append(u'borde sin longitud: Lb = 60 cm')
        ext = W.side_ext(model, cfg, key, into)
        if lb - ext >= model.L - 10.0:
            # el elemento ocupa todo el muro: uno solo, del inicio al final
            lb = model.L + ext + W.side_ext(model, cfg, 'R' if key == 'L' else 'L', into)
            b['len' + key] = lb
            _bt_spacing(spec, cfg, bts)
            sbt = bts.get(cfg['bstir']['bt'])
            sec = CG.Section('rect', lb, model.t)
            bars, sts = layout_zone(CG, sec, cfg['cover'], sbt['dm'] if sbt else 0.95, _expand(spec['bars'], bts),
                                    spec['ne'] or 1, spec['ng'])
            zones[key] = (bars, sts)
            b[key] = W.serialize_zone(bars, sts)
            other = 'R' if key == 'L' else 'L'
            b['len' + other] = 0.0
            zones[other] = ([], [])
            b[other] = {'bars': [], 'stirrups': []}
            cfg['web']['v_on'] = False
            cfg['_full'] = [0]
            warns.append(u'el borde ocupa todo el muro (%s cm): un solo elemento' % V.fmt_cm(model.L))
            break
        b['len' + key] = lb
        _bt_spacing(spec, cfg, bts)
        sbt = bts.get(cfg['bstir']['bt'])
        sec = CG.Section('rect', lb, model.t)
        bars, sts = layout_zone(CG, sec, cfg['cover'], sbt['dm'] if sbt else 0.95, _expand(spec['bars'], bts),
                                spec['ne'] or 1, spec['ng'])
        zones[key] = (bars, sts)
        b[key] = W.serialize_zone(bars, sts)
    return cfg, zones, warns, errs


# ================================================================== cadenas de muros en L (L, U, Z...)
def _split_counts(bar_list, fa):
    """Reparte la lista de varillas entre dos brazos (fraccion fa para el A) conservando la mezcla de diametros."""
    na = max(min(int(round(len(bar_list) * fa)), len(bar_list)), 0)
    nb = len(bar_list) - na
    a, b = [], []
    for bt in bar_list:
        if len(a) < na and (len(b) >= nb or len(a) * (1.0 - fa) <= len(b) * fa):
            a.append(bt)
        else:
            b.append(bt)
    return a, b


def build_chain_cfg(W, CG, cs, models, rows, bts_list, settings, corner_rank=None):
    """Cadena de muros en esquina (orden de cs). rows[k] = fila del Excel del muro k. corner_rank[k] = orden en
    planta de la esquina k (para las alternativas ';'). -> (cfg, zones, avisos[k], errores[k])."""
    n = len(models)
    warns = [[] for _ in range(n)]
    errs = [[] for _ in range(n)]
    cfgs = []
    for k in range(n):
        c, bts = _base_cfg(W, models[k], bts_list, settings, rows[k], warns[k], errs[k])
        cfgs.append(c)
    cfg = copy.deepcopy(cfgs[0])
    cfg['bnd'] = {'mirror': False, 'lenL': 0.0, 'lenR': 0.0, 'L': {'bars': [], 'stirrups': []},
                  'R': {'bars': [], 'stirrups': []}, 'intoL': True, 'intoR': True}
    for k in range(1, n):
        if cfgs[k]['open'] != cfgs[0]['open']:
            warns[k].append(u'en la esquina se usa el refuerzo de huecos de %s' % cs['walls'][0]['name'])
    c = cfg.setdefault('corner', {})
    c['webs'] = dict((str(k), copy.deepcopy(cfgs[k]['web'])) for k in range(1, n))
    c['web_same'] = all(cfgs[k]['web'] == cfgs[0]['web'] for k in range(1, n))
    c['len'], c['zones'], c['into'], c['rot'] = {}, {}, {}, 0
    specs = []
    for k in range(n):
        sp, e = end_specs(rows[k], bts_list)
        errs[k] += e
        specs.append(sp)
    Ls = [m.L for m in models]
    into = not is_no(settings.get('lb_into') or u'Sí')
    zones = {}
    spacing_set = [False]

    def apply_sp(spec):
        if not spacing_set[0] and (spec.get('sp') or spec.get('st_bt')):
            _bt_spacing(spec, cfg, bts)
            spacing_set[0] = True

    # ---- esquinas: un elemento en L por esquina (de la fila de cualquiera de sus dos muros)
    for k in range(len(cs['corners'])):
        cn = cs['corners'][k]
        ka, kb = k, k + 1
        rank = corner_rank[k] if corner_rank else k
        la = Ls[ka] >= Ls[kb]                         # el muro A es el largo
        sa = pick(specs[ka]['esq'], rank) if specs[ka]['esq_set'] else None
        sb = pick(specs[kb]['esq'], rank) if specs[kb]['esq_set'] else None
        spec = sa or sb or pick(specs[ka]['esq'], rank) or pick(specs[kb]['esq'], rank)
        if sa and sb and sa != 'col' and sb != 'col' and (sa['bars'] != sb['bars']):
            warns[kb].append(u'la esquina con %s trae otro armado en su fila; se usa el de %s'
                             % (cs['walls'][ka]['name'], cs['walls'][ka]['name']))
        keyA, keyB = 'C%dA' % k, 'C%dB' % k
        if not spec or spec == 'col':
            for key in (keyA, keyB):
                c['len'][key] = 0.0
                zones[key] = ([], [])
                c['zones'][key] = {'bars': [], 'stirrups': []}
            continue
        apply_sp(spec)
        sbt = bts.get(cfg['bstir']['bt'])
        ds = sbt['dm'] if sbt else 0.95
        cover = float(cfg['cover'])
        lb_long = spec['lb'] or 60.0
        lb_short = spec['lb2'] or lb_long
        lbA, lbB = (lb_long, lb_short) if la else (lb_short, lb_long)
        tA, tB = cn['A']['t'], cn['B']['t']
        lbA = min(max(lbA, tB + 5.0), Ls[ka] + 0.0)
        lbB = min(max(lbB, tA + 5.0), Ls[kb] + 0.0)
        c['len'][keyA], c['len'][keyB] = lbA, lbB
        for kk, lb_, L_ in ((ka, lbA, Ls[ka]), (kb, lbB, Ls[kb])):
            if lb_ >= L_ - 10.0:
                cfg.setdefault('_full', []).append(kk)
        allb = _expand(spec['bars'], bts)
        lenA, lenB = lbA, max(lbB - tA, 1.0)
        bA, bB = _split_counts(allb, lenA / (lenA + lenB))
        ne = spec['ne'] or 2
        ng = spec['ng'] or 0
        eA = max(1, int(round(ne * lenA / (lenA + lenB))))
        eB = max(1, ne - eA) if ne >= 2 else 1
        gA = int(round(ng * lenA / (lenA + lenB)))
        gB = ng - gA
        secA = W.leg_section(cs, cfg, keyA)
        secB = W.leg_section(cs, cfg, keyB)
        barsA, stsA = layout_zone(CG, secA, cover, ds, bA, eA, gA)
        # brazo B: sin el cuadro de la esquina (ya lo tiene el A); x de la seccion B: pb - lb/2
        barsB, stsB = layout_zone(CG, secB, cover, ds, bB, eB, gB, x0=tA - lbB / 2.0)
        if not barsB:
            stsB = [CG.Stirrup('perim', [], True)]
        else:
            stsB = [CG.Stirrup('perim', [], True)] + stsB[1:]
        zones[keyA] = (barsA, stsA)
        zones[keyB] = (barsB, stsB)
        c['zones'][keyA] = W.serialize_zone(barsA, stsA)
        c['zones'][keyB] = W.serialize_zone(barsB, stsB)
    # ---- extremos libres de la cadena
    for key in W.free_keys(cs):
        wk = W.key_wall(key)
        side = W.free_side(cs, key)
        kind = end_kind(models[wk], side)
        short = Ls[wk] < max(Ls) - 1e-6
        spec = None if kind == 'col' else pick(specs[wk][kind], 0, short)
        c['into'][key] = into
        if not spec or spec == 'col':
            c['len'][key] = 0.0
            zones[key] = ([], [])
            c['zones'][key] = {'bars': [], 'stirrups': []}
            continue
        apply_sp(spec)
        sbt = bts.get(cfg['bstir']['bt'])
        lb = spec['lb'] or 60.0
        c['len'][key] = lb
        sec = CG.Section('rect', lb, models[wk].t)
        bars, sts = layout_zone(CG, sec, float(cfg['cover']), sbt['dm'] if sbt else 0.95, _expand(spec['bars'], bts),
                                spec['ne'] or 1, spec['ng'])
        zones[key] = (bars, sts)
        c['zones'][key] = W.serialize_zone(bars, sts)
    W.corner_cfg(cfg, cs)
    return cfg, zones, warns, errs
