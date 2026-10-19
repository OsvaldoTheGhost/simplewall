# -*- coding: utf-8 -*-
"""Armado de trabes desde Excel: lectura de la tabla y conversion a la configuracion de la
herramienta Armado Viga (bv_geom). Sin Revit ni WPF: se puede probar fuera de Revit.

Unidades: cm (como bv_geom). Compatible con IronPython 2.7 y CPython 3."""
from __future__ import division
import copy
import re

try:
    import unicodedata
except ImportError:                     # pragma: no cover
    unicodedata = None

try:
    text_type = unicode                 # IronPython 2.7
except NameError:                       # CPython 3
    text_type = str

# ------------------------------------------------------------------ columnas de la hoja "Armado"
# clave interna, encabezado, ancho, ayuda
COLUMNS = [
    ('niveles', u'Niveles', 12, u'N2, N2-N4, N2,N5 o +7.55'),
    ('trabe', u'Trabe', 18, u'Nombre del tipo en Revit (T EJE-K, T-5 Seccion 1...)'),
    ('tramo', u'Tramo', 7, u'1, 2, 3... o "todos"'),
    ('apoyo_i', u'Apoyo inicial', 13, u'Informativo (lo llena la plantilla)'),
    ('apoyo_f', u'Apoyo final', 13, u'Informativo (lo llena la plantilla)'),
    ('long', u'Long. libre (m)', 10, u'Informativo'),
    ('seccion', u'Sección (cm)', 11, u'Informativo'),
    ('sup', u'Sup. corrido', 12, u'3#4'),
    ('inf', u'Inf. corrido', 12, u'3#4'),
    ('bsup_i', u'Bastón sup. apoyo inicial', 18, u'1#4 100/100 (izq./der. del eje)'),
    ('bsup_f', u'Bastón sup. apoyo final', 18, u'3#6 190/100'),
    ('binf_i', u'Bastón inf. apoyo inicial', 18, u'1#4 100'),
    ('binf_f', u'Bastón inf. apoyo final', 18, u''),
    ('csup', u'Ref. sup. centro', 15, u'2#4 130 (desde el eje de cada apoyo)'),
    ('cinf', u'Ref. inf. centro', 15, u'2#4 130 o 2#4 130/150'),
    ('estribos', u'Estribos', 22, u'E#3 11@10 @20 11@10'),
    ('muro', u'Estribo sobre muro', 12, u'@20 o No'),
    ('piel', u'Piel', 10, u'#3@30 o No'),
    ('notas', u'Notas', 30, u''),
]
COL_KEYS = [c[0] for c in COLUMNS]
INFO_KEYS = ('apoyo_i', 'apoyo_f', 'long', 'seccion', 'notas')

# ------------------------------------------------------------------ hoja "Configuracion"
# clave interna, etiqueta, valor por defecto, ayuda
SETTINGS = [
    ('cover', u'Recubrimiento (cm)', u'2.5', u'Al estribo'),
    ('stir_bt', u'Estribo por defecto', u'#3', u'Si la columna Estribos no dice E#..'),
    ('hook', u'Gancho de estribo', u'', u'Vacío = el de 135°'),
    ('leg', u'Pata de corridos en extremos (cm)', u'', u'Vacío = 12 diámetros'),
    ('add_leg', u'Pata de bastones en extremos (cm)', u'', u'Vacío = 12 diámetros'),
    ('lap_k', u'Traslape (diámetros)', u'40', u''),
    ('stock', u'Longitud comercial (cm)', u'1200', u'Vacío o 0 = sin cortes por longitud comercial'),
    ('first', u'Primer estribo desde el paño (cm)', u'5', u''),
    ('over', u'Estribos sobre muro (por defecto)', u'@20', u'@20 o No'),
    ('axis', u'Cambio de eje en apoyos', u'Doblar', u'Doblar o Anclar'),
    ('side_hook', u'Gancho de la piel', u'Vertical', u'Vertical, Horizontal o Ninguno'),
    ('replace', u'Reemplazar armado previo', u'Sí', u'Sí = borra el armado que hizo la herramienta en esa trabe'),
    ('family', u'Familia de trabes por nivel', u'Trabes N{elev}', u'{elev} = +7.55'),
    ('param', u'Parámetro de nivel en varillas', u'Nivel', u'Vacío = no se llena'),
    ('param_fmt', u'Valor del parámetro de nivel', u'NIVEL {elev}', u'{elev} = +7.55, {nivel} = nombre del nivel'),
    ('base', u'Configuración base (opcional)', u'', u'Nombre de una configuración guardada en Trabes tipo'),
    ('montaje', u'Crear montajes', u'Sí', u'Sí = un montaje por trabe del plano con sus trabes anfitrionas y su acero'),
    ('montaje_fmt', u'Nombre del montaje', u'Trabe {trabe} Nivel {elev}', u'{trabe} = T-4, EJE-K; {elev} = +16.10; {nivel} = nombre del nivel'),
]
SETTING_KEYS = [s[0] for s in SETTINGS]


class SpecError(Exception):
    pass


# ================================================================== texto
def u_(v):
    if v is None:
        return u''
    if isinstance(v, float):
        if abs(v - round(v)) < 1e-9:
            return u'%d' % int(round(v))
        return (u'%.4f' % v).rstrip(u'0').rstrip(u'.')
    try:
        return text_type(v)
    except Exception:
        return u'%s' % v


def clean(v):
    return re.sub(r'\s+', u' ', u_(v).replace(u' ', u' ')).strip()


def fold(s):
    """Minusculas sin acentos ni espacios repetidos (para comparar nombres)."""
    s = clean(s)
    try:
        s = u''.join(ch for ch in unicodedata.normalize('NFD', s) if unicodedata.category(ch) != 'Mn')
    except Exception:
        for a, b in ((u'á', u'a'), (u'é', u'e'), (u'í', u'i'), (u'ó', u'o'), (u'ú', u'u'), (u'Á', u'a'),
                     (u'É', u'e'), (u'Í', u'i'), (u'Ó', u'o'), (u'Ú', u'u'), (u'ü', u'u'), (u'ñ', u'n'), (u'Ñ', u'n')):
            s = s.replace(a, b)
    return s.lower()


def num(text, default=None):
    t = clean(text).replace(u',', u'.')
    if not t:
        return default
    try:
        return float(t)
    except ValueError:
        return default


def fmt_cm(v):
    return (u'%.1f' % v).rstrip(u'0').rstrip(u'.')


def is_no(text):
    return fold(text) in (u'no', u'n', u'0', u'-', u'ninguno', u'ninguna', u'sin', u'none')


def is_yes(text):
    return fold(text) in (u'si', u's', u'1', u'yes', u'y', u'true', u'verdadero', u'x')


# ================================================================== nombres de trabe
def norm_beam(name):
    """'TRABE EJE-K' == 'T EJE-K' == 'eje-k'; 'T-5 Seccion 1' == 'T-5 S1' == 'TRABE T-5 SEC. 1'."""
    s = fold(name).replace(u'–', u'-').replace(u'—', u'-')
    s = re.sub(r'^(trabe|trabes|tr\.?)\s+', u'', s)
    s = re.sub(r'^t\s+(?=eje)', u'', s)
    s = re.sub(r'\b(seccion|secc?\.?|s)\s*(\d+)', r's\2', s)
    s = re.sub(r'\s*-\s*', u'-', s)
    s = re.sub(r'\s+', u' ', s).strip()
    return s


_SEL = re.compile(r'^(.*?)\s*\(\s*([^()]*?)\s*\)\s*$')
_SECS = re.compile(r'^(.*?)\s*\[\s*([^\[\]]*?)\s*\]\s*$')


def split_sections(name):
    """'T-5 (viga 2) [S1, S2]' -> ('T-5 (viga 2)', [1, 2]); sin corchetes -> (nombre, []).
    Los corchetes arman solo esas secciones de la viga, como una viga aparte (lo que en el plano esta separado)."""
    t = clean(name)
    m = _SECS.match(t)
    if not m or not m.group(1):
        return t, []
    nums = [int(x) for x in re.findall(r'\d+', m.group(2))]
    return m.group(1).strip(), nums


def section_no(type_name):
    """'T-5 Seccion 3' -> 3; 'T-5' -> 0."""
    m = re.search(r'(?:secci[oó]n|secc?\.?|\bs)\s*(\d+)\s*$', fold(type_name))
    return int(m.group(1)) if m else 0


def split_selector(name):
    """'EJE-10 (K-H)' -> ('EJE-10', 'K-H'); 'T-2 (viga 2)' -> ('T-2', 'viga 2'); 'T-4' -> ('T-4', u'').
    El parentesis elige una de las vigas separadas de esa trabe (por los ejes que cruza o por su numero)."""
    t = clean(name)
    m = _SEL.match(t)
    if not m or not m.group(1):
        return t, u''
    return m.group(1).strip(), m.group(2).strip()


def is_mark(name):
    """True si el nombre es la trabe del plano (sin 'Seccion n'): se arman juntas todas sus secciones."""
    t = clean(name)
    return not re.search(r'\s+(secci[oó]n|secc?\.?|s)\s*\d+\s*$', t, flags=re.I)


def grid_key(name):
    """'EJE K' == 'Eje-K' == 'k'; "1'" == "1'"."""
    s = fold(name).replace(u'’', u"'").replace(u'´', u"'")
    s = re.sub(r'^(eje|ejes)\s*-?\s*', u'', s)
    return re.sub(r'[\s\-]+', u'', s)


def selector_tokens(sel):
    """'K-H' / 'K a H' / 'ejes K, H' -> ['k', 'h']; 'viga 2' / '2' -> 2 (indice de viga, 1..)."""
    t = fold(sel)
    m = re.match(r'^(?:viga|v|#)?\s*(\d+)$', t)
    if m and not re.match(r'^\d+$', t):
        return int(m.group(1))
    t = re.sub(r'^(ejes?)\s+', u'', t)
    parts = [grid_key(x) for x in re.split(r'\s*(?:-|–|,|;|/|\s+a\s+|\s+al\s+|\s+y\s+)\s*', t) if x.strip()]
    return [x for x in parts if x]


# ================================================================== niveles
_LV_RANGE = re.compile(r'^(?:n(?:ivel)?\s*)?(\d+)\s*(?:-|a|al|–)\s*(?:n(?:ivel)?\s*)?(\d+)$')
_LV_ONE = re.compile(r'^(?:n(?:ivel)?\s*)?(\d+)$')
_LV_ELEV = re.compile(r'^(?:n(?:ivel)?\s*)?([+-]\s*\d+(?:[.,]\d+)?)$')


def resolve_levels(text, levels):
    """text: 'N2', 'N2-N4', 'N2 a N4', 'N2,N5', '+7.55', 'Nivel Roof Garden'.
    levels: [(nombre, elevacion_m)]. Devuelve (lista de (nombre, elev), errores)."""
    out, errs = [], []
    by_name = dict((fold(n), (n, e)) for n, e in levels)

    def by_index(k):
        lv = by_name.get(u'nivel %d' % k) or by_name.get(u'n%d' % k)
        if lv is None:
            errs.append(u'no hay un nivel llamado "Nivel %d"' % k)
        return lv

    t = clean(text)
    if not t:
        return [], [u'la columna Niveles está vacía']
    for tok in re.split(r'[,;/]+|\s+y\s+', t):
        tok = tok.strip()
        if not tok:
            continue
        f = fold(tok)
        if f in by_name:
            out.append(by_name[f])
            continue
        m = _LV_RANGE.match(f)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            step = 1 if b >= a else -1
            for k in range(a, b + step, step):
                lv = by_index(k)
                if lv:
                    out.append(lv)
            continue
        m = _LV_ONE.match(f)
        if m:
            lv = by_index(int(m.group(1)))
            if lv:
                out.append(lv)
            continue
        m = _LV_ELEV.match(f)
        if m:
            z = float(m.group(1).replace(u' ', u'').replace(u',', u'.'))
            cand = [lv for lv in levels if abs(lv[1] - z) < 0.006]
            if cand:
                out.append(cand[0])
            else:
                errs.append(u'no hay un nivel a %+.2f m' % z)
            continue
        errs.append(u'no se entiende el nivel "%s"' % tok)
    seen, uniq = set(), []
    for lv in out:
        if lv[0] not in seen:
            seen.add(lv[0])
            uniq.append(lv)
    return uniq, errs


def elev_txt(elev_m):
    return u'%+.2f' % elev_m


def family_for(pattern, level_name, elev_m):
    return (pattern or u'Trabes N{elev}').replace(u'{elev}', elev_txt(elev_m)).replace(u'{nivel}', level_name)


def beam_mark(type_name):
    """Trabe del plano a partir del tipo de Revit: 'T-4 Seccion 2' -> 'T-4', 'T EJE-K' -> 'EJE-K',
    "T EJE-1' Seccion 3" -> "EJE-1'"."""
    t = clean(type_name)
    t = re.sub(r'\s+(secci[oó]n|secc?\.?|s)\s*\d+\s*$', u'', t, flags=re.I)
    t = re.sub(r'^(trabe|trabes)\s+', u'', t, flags=re.I)
    t = re.sub(r'^[Tt]\s+(?=[Ee][Jj][Ee])', u'', t)
    return t.strip()


def assembly_name(fmt, mark, level_name, elev_m):
    return (fmt or u'Trabe {trabe} Nivel {elev}').replace(u'{trabe}', mark).replace(
        u'{elev}', elev_txt(elev_m)).replace(u'{nivel}', level_name)


def numbered(name, k, elev_m=None):
    """Nombre para la k-esima trabe repetida (k = 0 -> igual): 'Trabe T-2 Nivel +16.10' -> 'Trabe T-2.1 Nivel +16.10'."""
    if k <= 0:
        return name
    m = re.search(r'\s+(nivel|n)\s*[+-]?\d', name, flags=re.I)
    if m:
        return u'%s.%d%s' % (name[:m.start()], k, name[m.start():])
    return u'%s.%d' % (name, k)


def level_label(level_name):
    """'Nivel 2' -> 'N2' (para la plantilla)."""
    m = re.match(r'^\s*nivel\s+(\d+)\s*$', fold(level_name))
    return u'N%s' % m.group(1) if m else level_name


# ================================================================== tipos de varilla
def resolve_bt(name, bts_list):
    """Nombre del tipo de varilla del modelo para '#4', '4', 'Var #4'..."""
    t = clean(name)
    if not t:
        raise SpecError(u'falta el tipo de varilla')
    names = [b['name'] for b in bts_list]
    if t in names:
        return t
    ft = fold(t).replace(u' ', u'')
    for n in names:
        if fold(n).replace(u' ', u'') == ft:
            return n
    m = re.search(r'#?\s*(\d+(?:\.\d+)?)\s*$', t)
    if m:
        k = m.group(1)
        pat = re.compile(r'(^|[^\d.])#?\s*' + re.escape(k) + r'$')
        hits = [n for n in names if pat.search(clean(n))]
        hits = [n for n in hits if u'#' in n] or hits
        if len(hits) == 1 or (hits and all(h == hits[0] for h in hits)):
            return hits[0]
        if hits:
            return sorted(hits, key=len)[0]
    raise SpecError(u'no hay tipo de varilla "%s" en el modelo' % t)


_BAR = re.compile(r'^(\d+)\s*[xX×]?\s*(#\s*\d+(?:\.\d+)?|[^\s#]+)$')


def parse_bar(text, bts_list):
    """'3#4' -> (3, '#4')."""
    t = clean(text).replace(u'Ø', u'#').replace(u'ø', u'#').replace(u'∅', u'#')
    m = _BAR.match(t)
    if not m:
        raise SpecError(u'no se entiende "%s" (use p. ej. 3#4)' % clean(text))
    n = int(m.group(1))
    return n, resolve_bt(m.group(2).replace(u' ', u''), bts_list)


def _split_bar_lengths(part):
    """'3#6 190/100' -> ('3#6', [190, 100]); '1#4 100' -> ('1#4', [100])."""
    p = clean(part).replace(u'Ø', u'#').replace(u'ø', u'#')
    m = re.match(r'^(\d+\s*[xX×]?\s*#\s*\d+(?:\.\d+)?|\d+\s*[xX×]?\s*\S+?)\s*(?:[Ll]\s*=?\s*|a\s*)?'
                 r'(-?\d+(?:[.,]\d+)?(?:\s*/\s*-?\d+(?:[.,]\d+)?)?)?\s*(?:cm)?$', p)
    if not m:
        raise SpecError(u'no se entiende "%s"' % clean(part))
    lens = []
    if m.group(2):
        lens = [float(x.replace(u',', u'.')) for x in re.split(r'\s*/\s*', m.group(2))]
    return m.group(1), lens


def parse_layers(text, bts_list, need_len=True):
    """Bastones: '1#4 100/100', '3#6 190/100 + 2#6 150' -> [{'n','bt','L1','L2'}] (cm desde el eje)."""
    t = clean(text)
    if not t or is_no(t):
        return []
    out = []
    for part in re.split(r'\s*\+\s*', t):
        if not part:
            continue
        bar, lens = _split_bar_lengths(part)
        n, bt = parse_bar(bar, bts_list)
        if not lens:
            if need_len:
                raise SpecError(u'"%s": falta la longitud (p. ej. %s 100/100)' % (clean(part), clean(bar)))
            lens = [0.0]
        L1 = lens[0]
        L2 = lens[1] if len(lens) > 1 else lens[0]
        out.append({'n': n, 'bt': bt, 'L1': L1, 'L2': L2})
    return out


def parse_stirrups(text, bts_list):
    """'E#3 11@10 @20 11@10' -> (bt|None, left, mid|None, right).
    Los grupos antes del '@sep' suelto van desde el apoyo inicial; los de despues, desde el final
    (si no hay, se repiten los del inicio)."""
    t = clean(text)
    if not t:
        return None
    if is_no(t):
        return 'off'
    bt = None
    left, right = [], []
    mid = None
    left_none = right_none = False
    t2 = re.sub(r'[,;]+', u' ', t)                  # comas y punto y coma separan grupos (decimales con punto)
    toks = re.findall(r'[Ee]?\s*#\s*\d+(?:\.\d+)?|\d+\s*@\s*\d+(?:\.\d+)?|@\s*\d+(?:\.\d+)?|\S+', t2)
    for tok in toks:
        k = tok.replace(u' ', u'')
        if re.match(r'^[Ee]?#\d+(?:\.\d+)?$', k):
            bt = resolve_bt(k.lstrip(u'Ee'), bts_list)
            continue
        m = re.match(r'^(\d+)@(\d+(?:\.\d+)?)$', k)
        if m:
            g = u'%d@%s' % (int(m.group(1)), fmt_cm(float(m.group(2))))
            (left if mid is None else right).append(g)
            continue
        m = re.match(r'^@(\d+(?:\.\d+)?)$', k)
        if m:
            if mid is not None:
                raise SpecError(u'estribos "%s": dos separaciones al centro' % t)
            mid = float(m.group(1))
            continue
        if k in (u'-', u'0'):                      # lado sin grupos (p. ej. junto a una viga que cruza)
            if mid is None:
                left_none = True
            else:
                right_none = True
            continue
        if k.lower() in (u'e', u'est', u'est.', u'estribos', u'c/', u'a'):
            continue
        raise SpecError(u'estribos: no se entiende "%s" en "%s"' % (tok, t))
    if mid is None and not left:
        raise SpecError(u'estribos "%s": falta la separación' % t)
    lt = u','.join(left) or (u'0' if left_none else u'')
    rt = u','.join(right) or (u'0' if right_none else u'')
    if lt == u'0' and not rt:
        rt = u'0'
    return bt, lt, mid, rt


def parse_over(text):
    """'@20' / '20' -> 20.0; 'No' -> 0.0; vacio -> None."""
    t = clean(text)
    if not t:
        return None
    if is_no(t):
        return 0.0
    m = re.match(r'^(?:[Ee]?#\d+\s*)?@?\s*(\d+(?:[.,]\d+)?)\s*(?:cm)?$', t)
    if not m:
        raise SpecError(u'estribo sobre muro: no se entiende "%s" (use @20 o No)' % t)
    return float(m.group(1).replace(u',', u'.'))


def parse_side(text, bts_list):
    """Piel: '#3@30' (separacion maxima), '2#3' (filas por cara), 'No' -> dict o None (vacio)."""
    t = clean(text).replace(u' ', u'')
    if not t:
        return None
    if is_no(t):
        return {'on': False}
    m = re.match(r'^(\d+)?([Ee]?#\d+(?:\.\d+)?|[^@]+?)(?:@(\d+(?:[.,]\d+)?))?$', t)
    if not m:
        raise SpecError(u'piel: no se entiende "%s" (use #3@30 o 1#3)' % t)
    bt = resolve_bt(m.group(2).lstrip(u'Ee'), bts_list)
    rows = int(m.group(1)) if m.group(1) else 1
    sp = float(m.group(3).replace(u',', u'.')) if m.group(3) else 0.0
    return {'on': True, 'bt': bt, 'rows': max(1, rows), 'sp': sp, 'exl': 0.0, 'exr': 0.0}


# ================================================================== filas
def is_all_tramo(text):
    return fold(text) in (u'', u'todos', u'todo', u'*', u't', u'all')


def group_rows(rows):
    """rows: [dict(clave -> texto, '_row': n)] -> [(niveles, trabe, filas)] en el orden de la hoja.
    Las filas sin nivel o sin trabe heredan los de la fila anterior (celdas combinadas)."""
    groups = []
    index = {}
    last_lv = last_tb = u''
    for r in rows:
        if not any(clean(r.get(k)) for k in COL_KEYS if k not in INFO_KEYS + ('niveles', 'trabe', 'tramo')):
            # fila sin armado: se ignora (p. ej. la plantilla sin llenar)
            if clean(r.get('niveles')):
                last_lv = clean(r.get('niveles'))
            if clean(r.get('trabe')):
                last_tb = clean(r.get('trabe'))
            continue
        lv = clean(r.get('niveles')) or last_lv
        tb = clean(r.get('trabe')) or last_tb
        last_lv, last_tb = lv, tb
        key = (fold(lv), norm_beam(tb))
        if key not in index:
            index[key] = len(groups)
            groups.append((lv, tb, []))
        groups[index[key]][2].append(r)
    return groups


def split_spans(rows, n_spans):
    """-> (fila 'todos' o None, {indice 0..: fila}, errores)."""
    base, per, errs = None, {}, []
    for r in rows:
        t = clean(r.get('tramo'))
        if is_all_tramo(t):
            if base is not None:
                errs.append(u'fila %s: hay dos filas "todos" para la misma trabe' % r.get('_row'))
            base = r
            continue
        m = re.match(r'^[Tt]?\s*(\d+)$', t)
        if not m:
            errs.append(u'fila %s: tramo "%s" no válido (1, 2, 3... o todos)' % (r.get('_row'), t))
            continue
        i = int(m.group(1)) - 1
        if i < 0 or i >= n_spans:
            errs.append(u'fila %s: el tramo %d no existe (la trabe tiene %d tramo%s en el modelo)'
                        % (r.get('_row'), i + 1, n_spans, u'' if n_spans == 1 else u's'))
            continue
        if i in per:
            errs.append(u'fila %s: el tramo %d está repetido' % (r.get('_row'), i + 1))
            continue
        per[i] = r
    return base, per, errs


def merged(base, row, key):
    """Valor de la columna para un tramo: el de su fila o, si esta vacio, el de la fila 'todos'."""
    v = clean(row.get(key)) if row is not None else u''
    if not v and base is not None:
        v = clean(base.get(key))
    return v


# ================================================================== configuracion
def settings_from(pairs):
    """pairs: [(etiqueta, valor)] de la hoja Configuracion -> {clave: texto}."""
    lab = dict((fold(s[1]), s[0]) for s in SETTINGS)
    out = dict((s[0], s[2]) for s in SETTINGS)
    for k, v in pairs:
        key = lab.get(fold(k))
        if key:
            out[key] = clean(v)
    return out


def round_up(v, step):
    import math
    return math.ceil(v / step - 1e-9) * step


def build_cfg(G, model, bts_list, hooks, settings, base_cfg, base, per, row_label=u''):
    """Configuracion de Armado Viga para una cadena (modelo ya analizado) a partir de las filas de Excel.
    G: modulo bv_geom. hooks: [{'name','angle'}]. base_cfg: configuracion guardada (o {}).
    Devuelve (cfg, avisos, errores)."""
    warns, errs = [], []
    bts = dict((b['name'], b) for b in bts_list)
    ns = model.n_spans
    inner = list(model.inner_supports)
    cfg = G.fit_cfg(copy.deepcopy(base_cfg or {}), model, bts_list)

    def fail(i, key, ex):
        lbl = u'tramo %d' % (i + 1) if i is not None else u'trabe'
        errs.append(u'%s, %s: %s' % (lbl, dict((c[0], c[1]) for c in COLUMNS).get(key, key), ex))

    def leg_of(bt_name, key):
        v = num(settings.get(key))
        if v is not None:
            return v
        return round_up(12.0 * bts[bt_name]['dn'], 5.0)

    rows = [per.get(i) for i in range(ns)]

    def val(i, key):
        return merged(base, rows[i], key)

    # -------- generales
    cover = num(settings.get('cover'))
    if cover is not None:
        cfg['cover'] = cover
    try:
        cfg['stir_bt'] = resolve_bt(settings.get('stir_bt') or u'#3', bts_list)
    except SpecError as ex:
        errs.append(u'Configuración, estribo por defecto: %s' % ex)
    hk = clean(settings.get('hook'))
    hnames = [h['name'] for h in hooks]
    if hk:
        hit = [n for n in hnames if fold(n) == fold(hk)] or [n for n in hnames if fold(hk) in fold(n)]
        if hit:
            cfg['stirrups']['hook'] = hit[0]
        else:
            errs.append(u'Configuración: no hay gancho de estribo "%s"' % hk)
    elif not cfg['stirrups'].get('hook') or cfg['stirrups']['hook'] not in hnames:
        h135 = [h for h in hooks if abs(h['angle'] - 135.0) < 1.0]
        cfg['stirrups']['hook'] = (h135[0] if h135 else (hooks[0] if hooks else {'name': u''}))['name']
    k = num(settings.get('lap_k'))
    if k is not None:
        cfg['lap']['k'] = k
    st = num(settings.get('stock'))
    if st is not None:
        cfg['lap']['stock_on'] = st > 0
        if st > 0:
            cfg['lap']['stock'] = st
    fs = num(settings.get('first'))
    if fs is not None:
        cfg['stirrups']['first'] = fs
    ax = fold(settings.get('axis'))
    if ax:
        cfg['axis_mode'] = 'anchor' if ax.startswith(u'anc') else 'bend'
        model.set_bend_mode(cfg['axis_mode'])
    sh = fold(settings.get('side_hook'))
    if sh:
        cfg['side_hook']['dir'] = 'in' if sh.startswith(u'hor') else ('none' if sh.startswith(u'n') or sh.startswith(u'sin') else 'down')
    cfg['views'] = {'elev': False, 'elev_tpl': u'', 'cross': False, 'cross_tpl': u''}

    # -------- corridos superior e inferior
    for g, key in (('top', 'sup'), ('bot', 'inf')):
        specs = []
        for i in range(ns):
            v = val(i, key)
            if not v or is_no(v):
                specs.append((0, None))
                continue
            try:
                specs.append(parse_bar(v, bts_list))
            except SpecError as ex:
                fail(i, key, ex)
                specs.append((0, None))
        used = [s for s in specs if s[0] > 0]
        if not used:
            cfg['%s_style' % g] = 'single'
            sg = cfg['%s_single' % g]
            sg['n'] = 0
            cfg['%s_zones' % g] = [None] * len(cfg.get('%s_zones' % g) or [])
            if not any(val(i, key) for i in range(ns)):
                warns.append(u'sin %s corrido' % (u'superior' if g == 'top' else u'inferior'))
            continue
        if all(s == specs[0] for s in specs):
            n, bt = specs[0]
            leg = leg_of(bt, 'leg')
            cfg['%s_style' % g] = 'single'
            sg = G.main_cfg(bt, n, leg, leg)
            cfg['%s_single' % g] = sg
            cfg['%s_zones' % g] = [None] * len(cfg.get('%s_zones' % g) or [])
        else:
            cfg['%s_style' % g] = 'multi'
            lst = []
            for i, (n, bt) in enumerate(specs):
                bt = bt or used[0][1]
                leg = leg_of(bt, 'leg')
                lst.append(G.main_cfg(bt, n, leg if i == 0 else 0.0, leg if i == ns - 1 else 0.0))
            cfg['%s_spans' % g] = lst
            first = [s for s in specs if s[0] > 0][0]
            cfg['%s_single' % g] = G.main_cfg(first[1], first[0], leg_of(first[1], 'leg'), leg_of(first[1], 'leg'))
            warns.append(u'%s corrido distinto por tramo: cada tramo se arma con su corrido anclado en los apoyos'
                         % (u'superior' if g == 'top' else u'inferior'))

    # -------- bastones en apoyos (INICIO / CENTRO sobre apoyo / FINAL)
    for g, ki, kf in (('top', 'bsup_i', 'bsup_f'), ('bot', 'binf_i', 'binf_f')):
        add = {'start': [], 'end': [], 'mid': [[] for _ in inner], 'span': [[] for _ in range(ns)]}
        per_node = {}                                      # nodo -> [(capas, fila)]
        for i in range(ns):
            for key, ni in ((ki, i), (kf, i + 1)):
                v = val(i, key)
                if not v:
                    continue
                try:
                    lys = parse_layers(v, bts_list)
                except SpecError as ex:
                    fail(i, key, ex)
                    continue
                if lys:
                    per_node.setdefault(ni, []).append((lys, i))
        for ni, lst in sorted(per_node.items()):
            # el mismo apoyo descrito en el tramo de cada lado: si es igual cuenta una vez
            uniq = []
            for lys, i in lst:
                if lys not in [q[0] for q in uniq]:
                    uniq.append((lys, i))
            if len(uniq) > 1:
                warns.append(u'apoyo %s: bastones %s distintos en los tramos %s; se colocan todos'
                             % (model.nodes[ni].name or ni, u'sup.' if g == 'top' else u'inf.',
                                u' y '.join(u'%d' % (i + 1) for _, i in uniq)))
            layers = [ly for lys, _ in uniq for ly in lys]
            nd = model.nodes[ni]
            wall = nd.kind == G.WALL_UNDER          # muro bajo la viga: las cotas van desde su pano
            if ni in inner:
                dl = (nd.mid - nd.s0) if wall else 0.0
                dr = (nd.s1 - nd.mid) if wall else 0.0
                add['mid'][inner.index(ni)] = [dict(ly, L1=ly['L1'] + dl, L2=ly['L2'] + dr) for ly in layers]
                continue
            # apoyo extremo (o extremo libre): L medida desde el eje, hacia dentro del claro
            start = (ni == 0)
            for ly in layers:
                Lin = ly['L2'] if start else ly['L1']
                half = 0.0 if wall else ((nd.s1 - nd.mid) if start else (nd.mid - nd.s0))
                L = max(Lin - half, 0.0)
                leg = 0.0 if nd.free else leg_of(ly['bt'], 'add_leg')
                (add['start'] if start else add['end']).append(
                    {'n': ly['n'], 'bt': ly['bt'], 'L': L, 'La': leg, 'Ln': 0.0})
        # refuerzo al centro del claro: distancias desde el eje de cada apoyo -> L1 / L2 desde el centro
        key = 'csup' if g == 'top' else 'cinf'
        for i in range(ns):
            v = val(i, key)
            if not v:
                continue
            try:
                lys = parse_layers(v, bts_list, need_len=False)
            except SpecError as ex:
                fail(i, key, ex)
                continue
            fa, fb = model.span_faces(i)
            c = (fa + fb) / 2.0
            n0, n1 = model.nodes[i], model.nodes[i + 1]
            a0 = n0.s1 if n0.kind == G.WALL_UNDER else n0.mid      # eje del apoyo (pano si es muro)
            a1 = n1.s0 if n1.kind == G.WALL_UNDER else n1.mid
            for ly in lys:
                s0 = a0 + ly['L1']
                s1 = a1 - ly['L2']
                if s1 - s0 < 10.0:
                    fail(i, key, u'con %s cm desde los apoyos no queda varilla' % fmt_cm(ly['L1']))
                    continue
                add['span'][i].append({'n': ly['n'], 'bt': ly['bt'], 'L1': c - s0, 'L2': s1 - c})
        cfg['%s_add' % g] = add

    # -------- estribos (por grupos desde cada apoyo)
    stc = cfg['stirrups']
    stc['mode'] = 'groups'
    stc['on'] = True
    stc['manual'] = []
    groups, spans = [], []
    any_st = False
    for i in range(ns):
        v = val(i, 'estribos')
        g = {'left': u'', 'mid': 0.0, 'right': u''}
        sp = {'bt': u'', 'double': 'none'}
        try:
            p = parse_stirrups(v, bts_list) if v else None
        except SpecError as ex:
            fail(i, 'estribos', ex)
            p = None
        if p == 'off':
            g = {'left': u'0', 'mid': 0.0, 'right': u'0', 'off': True}
            sp['off'] = True
        elif p:
            any_st = True
            bt, left, mid, right = p
            g['left'] = left
            g['right'] = right
            g['mid'] = mid if mid is not None else 0.0
            if bt and bt != cfg.get('stir_bt'):
                sp['bt'] = bt
        else:
            warns.append(u'tramo %d sin estribos en la tabla' % (i + 1))
        groups.append(g)
        spans.append(sp)
    stc['groups'] = groups
    stc['spans'] = [dict((k, v) for k, v in s.items() if k != 'off') for s in spans]
    if not any_st:
        stc['on'] = False
    # tramos marcados "No": sin estribos (solo de ese tramo)
    off = [i for i, s in enumerate(spans) if s.get('off')]
    if off:
        warns.append(u'tramo(s) %s sin estribos (No)' % u', '.join(u'%d' % (i + 1) for i in off))

    ov = None
    for i in range(ns):
        try:
            v = parse_over(val(i, 'muro'))
        except SpecError as ex:
            fail(i, 'muro', ex)
            v = None
        if v is not None:
            ov = v
            break
    if ov is None:
        try:
            ov = parse_over(settings.get('over'))
        except SpecError as ex:
            errs.append(u'Configuración: %s' % ex)
            ov = None
    if ov is not None:
        stc['over'] = {'mode': 'walls' if ov > 0 else 'none', 'sp': ov if ov > 0 else 20.0}

    # -------- piel
    sides = []
    for i in range(ns):
        try:
            sides.append(parse_side(val(i, 'piel'), bts_list))
        except SpecError as ex:
            fail(i, 'piel', ex)
            sides.append(None)
    t3 = cfg.get('stir_bt')
    off_side = {'on': False, 'bt': t3, 'rows': 1, 'sp': 0.0, 'exl': 0.0, 'exr': 0.0}
    if all(s is None for s in sides):
        cfg['side_style'] = 'single'
        cfg['side_single'] = dict(off_side)
    elif all(s == sides[0] for s in sides):
        cfg['side_style'] = 'single'
        cfg['side_single'] = dict(off_side, **sides[0]) if sides[0] else dict(off_side)
    else:
        cfg['side_style'] = 'multi'
        cfg['side'] = [dict(off_side, **s) if s else dict(off_side) for s in sides]
    return cfg, warns, errs


def stirrups_off_spans(cfg):
    """Tramos con estribos "No" (grupos '0'): bv_geom los rellena con @mid si quedan vacios, asi que se
    eliminan del resultado despues de calcular."""
    return [i for i, g in enumerate(cfg['stirrups'].get('groups') or []) if g.get('off')]
