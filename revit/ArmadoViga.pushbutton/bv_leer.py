# -*- coding: utf-8 -*-
"""Lectura del armado que ya tiene la viga (Revit 2024 - 2026).

Revisa todas las varillas alojadas en las vigas de la cadena (las creadas con esta herramienta y las
hechas a mano) y las clasifica por su geometria, sin depender de como se crearon:

- estribos: varillas con estilo Estribo/Atadura (o cerradas y cortas a lo largo de la viga);
- longitudinales: superior / inferior / piel segun su altura dentro de la viga;
  - corridas: las piezas de una misma linea (mismo tipo, misma posicion en la seccion) que juntas
    cubren casi toda la viga (las piezas extra son traslapes);
  - bastones: las demas, ubicadas sobre un apoyo (con su longitud a cada lado) o en un tramo.

Unidades del resultado: cm."""
from __future__ import division
import math

from Autodesk.Revit.DB import BuiltInParameter
from Autodesk.Revit.DB.Structure import RebarHostData, RebarStyle, MultiplanarOption

import bv_revit as R

FT = R.FT
COVER_FULL = 0.85            # una linea que cubre >= 85 % de la viga es corrida
JOIN_TOL = 5.0               # cm: piezas de una misma linea que se tocan o traslapan
POS_TOL = 2.0                # cm: misma posicion en la seccion


# ================================================================ util
def _curves(rb, pos):
    try:
        return list(rb.GetCenterlineCurves(False, False, False, MultiplanarOption.IncludeOnlyPlanarCurves, pos))
    except Exception:
        try:
            return list(rb.GetCenterlineCurves(False, False, False))
        except Exception:
            return []


def _pts(curves):
    out = []
    for c in curves:
        try:
            out.extend(list(c.Tessellate()))
        except Exception:
            try:
                out.extend([c.GetEndPoint(0), c.GetEndPoint(1)])
            except Exception:
                pass
    return out


def _positions(rb):
    try:
        n = int(rb.NumberOfBarPositions)
    except Exception:
        n = 1
    out = []
    for i in range(max(n, 1)):
        try:
            if not rb.DoesIncludeBarAtPosition(i):
                continue
        except Exception:
            pass
        out.append(i)
    return out or [0]


def _quantity(rb, default):
    try:
        return int(rb.Quantity)
    except Exception:
        return default


def _is_stirrup(doc, rb):
    try:
        sh = doc.GetElement(rb.GetShapeId())
        if sh is not None:
            return sh.RebarStyle == RebarStyle.StirrupTie
    except Exception:
        pass
    try:
        p = rb.get_Parameter(BuiltInParameter.REBAR_ELEM_HOOK_STYLE)
        if p is not None:
            return p.AsInteger() == int(RebarStyle.StirrupTie)
    except Exception:
        pass
    return None


def _comment(rb):
    try:
        p = rb.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        return (p.AsString() or u'') if p is not None else u''
    except Exception:
        return u''


def _fmt(v):
    s = u'%.0f' % v if abs(v - round(v)) < 0.05 or abs(v) >= 100 else u'%.1f' % v
    return s


def _merge(iv):
    """Une intervalos [(a, b, ...)] que se tocan o traslapan; devuelve [(a, b, piezas)]."""
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1] + JOIN_TOL:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2] += 1
        else:
            out.append([a, b, 1])
    return out


# ================================================================ lectura
class _Ctx(object):
    def __init__(self, info):
        self.info = info
        self.fr = info['frame']
        ax = info.get('axis') or {}
        self.axis = None
        try:
            self.axis = R.Axis(self.fr, ax.get('runs') or [], ax.get('bends') or [], ax.get('s0', info['s_min']))
        except Exception:
            self.axis = None
        self.L = info['L']
        self.sup = sorted(info.get('supports') or [])
        self.z_of = {}
        for c in info['chain']:
            self.z_of[R.eid_int(c['elem'].Id)] = (c['z0'], c['z1'])

    def s_cm(self, p):
        s = self.fr.s(p)
        if self.axis is not None:
            try:
                return self.axis.to_arc(s) * FT
            except Exception:
                pass
        return (s - self.info['s_min']) * FT

    def l_cm(self, p):
        return (self.fr.l(p) - self.info['l_c']) * FT

    def zrange(self, host_id):
        return self.z_of.get(host_id, (self.info['z0'], self.info['z1']))

    def where(self, a, b):
        """Ubicacion de un baston: (texto, orden)."""
        over = [q for q in self.sup if q[0] < b - 1.0 and q[1] > a + 1.0]
        if over:
            q = max(over, key=lambda q: min(b, q[1]) - max(a, q[0]))
            left, right = q[0] - a, b - q[1]
            txt = u'apoyo %s' % q[2]
            parts = []
            if left > 1.0:
                parts.append(u'%s izq.' % _fmt(left))
            if right > 1.0:
                parts.append(u'%s der.' % _fmt(right))
            if parts:
                txt += u' (%s cm desde el paño)' % u' / '.join(parts)
            return txt, (q[0] + q[1]) / 2.0
        c = (a + b) / 2.0
        k = sum(1 for q in self.sup if (q[0] + q[1]) / 2.0 < c)
        if not self.sup or self.sup[0][0] > 5.0:
            k += 1
        return u'tramo T%d' % max(k, 1), c


def read_existing(doc, info):
    """Devuelve un dict con el armado existente o None si la viga no tiene varillas."""
    ctx = _Ctx(info)
    seen = set()
    rebars = []
    for c in info['chain']:
        try:
            for rb in RebarHostData.GetRebarHostData(c['elem']).GetRebarsInHost():
                k = R.eid_int(rb.Id)
                if k not in seen:
                    seen.add(k)
                    rebars.append(rb)
        except Exception:
            pass
    if not rebars:
        return None
    longs = []           # una entrada por varilla (posicion) longitudinal
    stirrups = []        # una entrada por conjunto de estribos
    own = 0
    for rb in rebars:
        if _comment(rb).startswith(R.TAG):
            own += 1
        bt = doc.GetElement(rb.GetTypeId())
        bname = R.el_name(bt) if bt is not None else u'?'
        try:
            dn = R._bt_diams(bt)[1]
        except Exception:
            dn = 0.0
        try:
            host_id = R.eid_int(rb.GetHostId())
        except Exception:
            host_id = None
        z0, z1 = ctx.zrange(host_id)
        h = max((z1 - z0) * FT, 1.0)
        poss = _positions(rb)
        first = _pts(_curves(rb, poss[0]))
        if not first:
            continue
        ss = [ctx.s_cm(p) for p in first]
        ext_s = max(ss) - min(ss)
        st = _is_stirrup(doc, rb)
        if st is None:
            st = ext_s < 0.5 * h
        if st:
            last = _pts(_curves(rb, poss[-1])) if len(poss) > 1 else first
            s_a = sum(ss) / len(ss)
            sl = [ctx.s_cm(p) for p in last]
            s_b = sum(sl) / len(sl)
            n = _quantity(rb, len(poss))
            ls = [ctx.l_cm(p) for p in first]
            zs = [p.Z * FT for p in first]
            stirrups.append({'bt': bname, 'dn': dn, 'n': n, 's0': min(s_a, s_b), 's1': max(s_a, s_b),
                             'sp': abs(s_b - s_a) / (n - 1) if n > 1 else 0.0,
                             'b': max(ls) - min(ls) + dn, 'h': max(zs) - min(zs) + dn, 'id': R.eid_int(rb.Id)})
            continue
        for i in poss:
            pts = first if i == poss[0] else _pts(_curves(rb, i))
            if not pts:
                continue
            ss = [ctx.s_cm(p) for p in pts]
            a, b = min(ss), max(ss)
            # la posicion en la seccion se toma en el tramo largo (sin las patas)
            mid = [p for p, s in zip(pts, ss) if a + 0.25 * (b - a) <= s <= b - 0.25 * (b - a)] or pts
            zc = sum(p.Z for p in mid) / len(mid)
            lc = sum(ctx.l_cm(p) for p in mid) / len(mid)
            rel = (zc - z0) / max(z1 - z0, 1e-9)
            layer = 'sup' if rel >= 0.6 else ('inf' if rel <= 0.4 else 'piel')
            legs = any(abs(q.Z - p.Z) * FT > 3.0 and abs(ctx.s_cm(q) - ctx.s_cm(p)) < 1.0
                       for p, q in zip(pts, pts[1:]))
            longs.append({'bt': bname, 'dn': dn, 'layer': layer, 'l': lc, 'z': zc * FT, 'a': a, 'b': b,
                          'legs': legs, 'id': R.eid_int(rb.Id)})
    # ---- lineas: mismo lecho, tipo y posicion en la seccion
    lines = []
    for bar in longs:
        for ln in lines:
            if ln['layer'] == bar['layer'] and ln['bt'] == bar['bt'] and abs(ln['l'] - bar['l']) <= POS_TOL \
                    and abs(ln['z'] - bar['z']) <= POS_TOL:
                ln['bars'].append(bar)
                break
        else:
            lines.append({'layer': bar['layer'], 'bt': bar['bt'], 'dn': bar['dn'], 'l': bar['l'], 'z': bar['z'],
                          'bars': [bar]})
    out = {'own': own, 'total': len(rebars), 'L': ctx.L, 'corr': {}, 'bast': [], 'stir': [], 'lines': lines}
    for ln in lines:
        runs = _merge([(b['a'], b['b']) for b in ln['bars']])
        for a, b, npc in runs:
            if b - a >= COVER_FULL * ctx.L:
                key = (ln['layer'], ln['bt'])
                d = out['corr'].setdefault(key, {'n': 0, 'laps': [], 'dn': ln['dn'], 'legs': False})
                d['n'] += 1
                d['laps'].append(npc - 1)
                d['legs'] = d['legs'] or any(x['legs'] for x in ln['bars'])
            else:
                for x in ln['bars']:
                    if x['a'] >= a - 0.1 and x['b'] <= b + 0.1:
                        txt, order = ctx.where(x['a'], x['b'])
                        out['bast'].append({'layer': ln['layer'], 'bt': ln['bt'], 'dn': ln['dn'], 'where': txt,
                                            'order': order, 'len': x['b'] - x['a'], 'a': x['a'], 'b': x['b'],
                                            'legs': x['legs']})
    # bastones iguales en el mismo lugar se cuentan juntos
    groups = {}
    for x in out['bast']:
        k = (x['layer'], x['bt'], x['where'], int(round(x['len'] / 5.0)))
        g = groups.setdefault(k, dict(x, n=0))
        g['n'] += 1
    out['bast'] = sorted(groups.values(), key=lambda g: (g['layer'], g['order'], -g['dn']))
    out['stir'] = sorted(stirrups, key=lambda s: s['s0'])
    return out


# ================================================================ texto
LAYERS = (('sup', u'Superior'), ('inf', u'Inferior'), ('piel', u'Piel / lateral'))


def _laps_txt(laps):
    if not any(laps):
        return u''
    if len(set(laps)) == 1:
        return u'%d traslape%s c/u' % (laps[0], u's' if laps[0] > 1 else u'')
    return u'%d traslapes en total' % sum(laps)


def _corr_txt(key, d):
    t = u'%d %s corrida%s' % (d['n'], key[1], u's' if d['n'] > 1 else u'')
    extra = []
    if any(d['laps']):
        extra.append(_laps_txt(d['laps']))
    if d['legs']:
        extra.append(u'con patas')
    return t + (u' (%s)' % u', '.join(extra) if extra else u'')


def _bast_txt(g):
    return u'%d %s en %s, L = %s cm%s' % (g['n'], g['bt'], g['where'], _fmt(g['len']),
                                         u' con patas' if g['legs'] else u'')


def _stir_txt(s):
    t = u'%d E%s' % (s['n'], s['bt'] if s['bt'].startswith(u'#') else u' ' + s['bt'])
    if s['n'] > 1:
        t += u' @ %s' % _fmt(s['sp'])
    t += u' de %s a %s cm (%s × %s)' % (_fmt(s['s0']), _fmt(s['s1']), _fmt(s['b']), _fmt(s['h']))
    return t


def short_text(ex):
    """Resumen corto para la ventana."""
    if not ex:
        return u'Armado existente: ninguno.'
    lines = [u'Armado existente (%d elemento(s) de armadura, %d de esta herramienta):' % (ex['total'], ex['own'])]
    for lay, name in LAYERS:
        items = [_corr_txt(k, d) for k, d in sorted(ex['corr'].items(), key=lambda kv: -kv[1]['dn'])
                 if k[0] == lay]
        items += [_bast_txt(g) for g in ex['bast'] if g['layer'] == lay]
        if items:
            lines.append(u'  %s: %s' % (name, u' · '.join(items)))
    if ex['stir']:
        lines.append(u'  Estribos: %s' % u' · '.join(_stir_txt(s) for s in ex['stir']))
    return u'\n'.join(lines)


def print_report(ex, label, out):
    """Reporte detallado en la ventana de salida de pyRevit."""
    out.print_md(u'## Armado existente de la viga %s' % label)
    if not ex:
        out.print_md(u'La viga no tiene varillas alojadas.')
        return
    out.print_md(u'**Longitud de la viga:** %s cm · **elementos de armadura:** %d (%d creados con esta herramienta)'
                 % (_fmt(ex['L']), ex['total'], ex['own']))
    rows = []
    for lay, name in LAYERS:
        for k, d in sorted(ex['corr'].items(), key=lambda kv: -kv[1]['dn']):
            if k[0] == lay:
                rows.append([name, u'Corrida', k[1], str(d['n']), u'toda la viga',
                             _laps_txt(d['laps']) or u'-',
                             u'sí' if d['legs'] else u'no'])
        for g in ex['bast']:
            if g['layer'] == lay:
                rows.append([name, u'Bastón', g['bt'], str(g['n']), g['where'],
                             u'L = %s (de %s a %s)' % (_fmt(g['len']), _fmt(g['a']), _fmt(g['b'])),
                             u'sí' if g['legs'] else u'no'])
    if rows:
        out.print_md(u'### Varillas longitudinales')
        out.print_table(rows, columns=[u'Lecho', u'Tipo', u'Varilla', u'Cant.', u'Ubicación', u'Longitud (cm)',
                                       u'Patas'])
    if ex['stir']:
        out.print_md(u'### Estribos')
        out.print_table([[s['bt'], str(s['n']), _fmt(s['sp']) if s['n'] > 1 else u'-', _fmt(s['s0']), _fmt(s['s1']),
                          u'%s × %s' % (_fmt(s['b']), _fmt(s['h']))] for s in ex['stir']],
                        columns=[u'Varilla', u'Cant.', u'Separación (cm)', u'Desde (cm)', u'Hasta (cm)',
                                 u'Tamaño ext. b × h (cm)'])
    out.print_md(u'_Posiciones medidas a lo largo de la viga desde su extremo inicial. Las corridas se '
                 u'reconocen por cubrir al menos el %d %% de la viga; las demás piezas se reportan como '
                 u'bastones._' % int(COVER_FULL * 100))
