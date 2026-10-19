# -*- coding: utf-8 -*-
"""Lectura y escritura de .xlsx sin librerias externas (zipfile + XML), para IronPython 2.7 y CPython 3.

- read_sheets(ruta): {nombre de hoja: [[celda, ...], ...]} (texto o numero; filas completas desde A).
  Lee el archivo aunque este abierto en Excel.
- write_book(ruta, hojas): hojas = [(nombre, filas, opciones)], opciones: {'widths': [...], 'header': True,
  'freeze': True}. Las celdas de texto van en linea (inlineStr)."""
from __future__ import division
import io
import re
import zipfile

try:
    text_type = unicode                 # IronPython 2.7
except NameError:
    text_type = str


# ================================================================== lectura
def _open_zip(path):
    """ZipFile del libro aunque Excel lo tenga abierto. En IronPython (pyRevit) se copia a un temporal con .NET
    (FileShare.ReadWrite) y se abre por ruta: zipfile de IronPython no acepta BytesIO con bytes."""
    try:
        import System
        from System.IO import FileStream, FileMode, FileAccess, FileShare, MemoryStream, File, Path
    except ImportError:
        with open(path, 'rb') as f:
            return zipfile.ZipFile(io.BytesIO(f.read())), None
    fs = FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)
    try:
        ms = MemoryStream()
        fs.CopyTo(ms)
        arr = ms.ToArray()
    finally:
        fs.Close()
    tmp = Path.Combine(Path.GetTempPath(), u'armado_trabes_%s.xlsx' % System.Guid.NewGuid().ToString('N'))
    File.WriteAllBytes(tmp, arr)
    return zipfile.ZipFile(tmp, 'r'), tmp


def _cleanup(tmp):
    if not tmp:
        return
    try:
        from System.IO import File
        File.Delete(tmp)
    except Exception:
        pass


_ENT = {'amp': u'&', 'lt': u'<', 'gt': u'>', 'quot': u'"', 'apos': u"'"}


def _unesc(s):
    def rep(m):
        e = m.group(1)
        if e.startswith('#x') or e.startswith('#X'):
            return _chr(int(e[2:], 16))
        if e.startswith('#'):
            return _chr(int(e[1:]))
        return _ENT.get(e, m.group(0))
    s = re.sub(r'_x([0-9A-Fa-f]{4})_', lambda m: _chr(int(m.group(1), 16)), s)
    return re.sub(r'&(#?[xX]?[0-9A-Za-z]+);', rep, s)


def _chr(k):
    try:
        return unichr(k)
    except NameError:
        return chr(k)


def _txt(data):
    """Texto UTF-8 de una parte del zip (bytes en CPython; en IronPython str con los bytes)."""
    if hasattr(data, 'decode'):
        try:
            return data.decode('utf-8')
        except Exception:
            pass
    if isinstance(data, text_type):
        return data
    return data.decode('utf-8', 'replace')


def _texts(xml):
    """Texto de un <si> o <is>: todos los <t>, sin los de fonetica (<rPh>)."""
    xml = re.sub(r'<rPh\b.*?</rPh>', u'', xml, flags=re.S)
    return u''.join(_unesc(t) for t in re.findall(r'<t(?:\s[^>]*)?>(.*?)</t>', xml, flags=re.S))


def _col_index(ref):
    letters = re.match(r'([A-Za-z]+)', ref).group(1).upper()
    k = 0
    for ch in letters:
        k = k * 26 + (ord(ch) - 64)
    return k - 1


def read_sheets(path):
    zf, tmp = _open_zip(path)
    try:
        return _read_zip(zf)
    finally:
        zf.close()
        _cleanup(tmp)


def _read_zip(zf):
    names = zf.namelist()
    shared = []
    if 'xl/sharedStrings.xml' in names:
        sx = _txt(zf.read('xl/sharedStrings.xml'))
        shared = [_texts(si) for si in re.findall(r'<si\b[^>]*>(.*?)</si>', sx, flags=re.S)]
    wb = _txt(zf.read('xl/workbook.xml'))
    rels = _txt(zf.read('xl/_rels/workbook.xml.rels')) if 'xl/_rels/workbook.xml.rels' in names else u''
    rmap = {}
    for m in re.finditer(r'<Relationship\b([^>]*)/?>', rels):
        a = m.group(1)
        rid = re.search(r'\bId="([^"]+)"', a)
        tgt = re.search(r'\bTarget="([^"]+)"', a)
        if rid and tgt:
            t = tgt.group(1)
            t = t[1:] if t.startswith('/') else 'xl/' + t
            rmap[rid.group(1)] = t
    out = {}
    order = []
    for m in re.finditer(r'<sheet\b([^>]*)/?>', wb):
        a = m.group(1)
        nm = re.search(r'\bname="([^"]*)"', a)
        rid = re.search(r'\br:id="([^"]+)"', a) or re.search(r'\b\w+:id="([^"]+)"', a)
        if not nm or not rid:
            continue
        target = rmap.get(rid.group(1))
        if not target or target not in names:
            continue
        name = _unesc(nm.group(1))
        out[name] = _parse_sheet(_txt(zf.read(target)), shared)
        order.append(name)
    out['__order__'] = order
    return out


def _parse_sheet(xml, shared):
    rows = {}
    for rm in re.finditer(r'<row\b([^>]*?)(?:/>|>(.*?)</row>)', xml, flags=re.S):
        ra = rm.group(1)
        body = rm.group(2) or u''
        rn = re.search(r'\br="(\d+)"', ra)
        r = int(rn.group(1)) if rn else (max(rows.keys()) + 1 if rows else 1)
        cells = {}
        col = -1
        for cm in re.finditer(r'<c\b([^>]*?)(?:/>|>(.*?)</c>)', body, flags=re.S):
            ca = cm.group(1)
            inner = cm.group(2) or u''
            ref = re.search(r'\br="([A-Za-z]+)\d*"', ca)
            col = _col_index(ref.group(1)) if ref else col + 1
            t = re.search(r'\bt="([^"]+)"', ca)
            t = t.group(1) if t else u'n'
            v = re.search(r'<v>(.*?)</v>', inner, flags=re.S)
            v = _unesc(v.group(1)) if v else None
            if t == 's':
                val = shared[int(v)] if v is not None and v.strip().isdigit() and int(v) < len(shared) else u''
            elif t == 'inlineStr':
                val = _texts(inner)
            elif t in ('str', 'e'):
                val = v or u''
            elif t == 'b':
                val = u'Sí' if v == '1' else u'No'
            else:
                if v is None or v == u'':
                    val = u''
                else:
                    try:
                        val = float(v)
                    except ValueError:
                        val = v
            cells[col] = val
        if cells:
            rows[r] = [cells.get(i, u'') for i in range(max(cells.keys()) + 1)]
    if not rows:
        return []
    return [rows.get(i, []) for i in range(1, max(rows.keys()) + 1)]


# ================================================================== escritura
def _esc(s):
    s = u'%s' % s
    s = s.replace(u'&', u'&amp;').replace(u'<', u'&lt;').replace(u'>', u'&gt;').replace(u'"', u'&quot;')
    return re.sub(u'[\x00-\x08\x0b\x0c\x0e-\x1f]', u'', s)


def _col_name(i):
    s = u''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = _chr(65 + r) + s
    return s


_STYLES = u'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="3"><font><sz val="10"/><name val="Calibri"/></font><font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font><font><i/><sz val="9"/><color rgb="FF666666"/><name val="Calibri"/></font></fonts>
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2C3E50"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEEF1F4"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFC8CDD2"/></left><right style="thin"><color rgb="FFC8CDD2"/></right><top style="thin"><color rgb="FFC8CDD2"/></top><bottom style="thin"><color rgb="FFC8CDD2"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment wrapText="1" vertical="center" horizontal="center"/></xf>
<xf numFmtId="49" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="49" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def _sheet_xml(rows, opts):
    widths = opts.get('widths') or []
    header = opts.get('header', True)
    grey = set(opts.get('grey_cols') or [])        # columnas informativas (fondo gris)
    note = opts.get('note_style', False)
    parts = [u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             u'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             u'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">']
    if header and opts.get('freeze', True):
        parts.append(u'<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
                     u'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    if widths:
        parts.append(u'<cols>')
        for i, w in enumerate(widths):
            parts.append(u'<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w))
        parts.append(u'</cols>')
    parts.append(u'<sheetData>')
    for r, row in enumerate(rows):
        attrs = u' ht="30" customHeight="1"' if (header and r == 0) else u''
        parts.append(u'<row r="%d"%s>' % (r + 1, attrs))
        for c, v in enumerate(row):
            ref = u'%s%d' % (_col_name(c), r + 1)
            if header and r == 0:
                st = 1
            elif note:
                st = 4 if c > 0 else 0
            else:
                st = 3 if c in grey else 2
            if v is None or v == u'':
                if st:
                    parts.append(u'<c r="%s" s="%d"/>' % (ref, st))
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                parts.append(u'<c r="%s" s="%d"><v>%s</v></c>' % (ref, 0 if st in (2, 3) else st, repr(float(v))))
            else:
                parts.append(u'<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                             % (ref, st, _esc(v)))
        parts.append(u'</row>')
    parts.append(u'</sheetData>')
    if header and opts.get('filter') and rows:
        parts.append(u'<autoFilter ref="A1:%s%d"/>' % (_col_name(len(rows[0]) - 1), len(rows)))
    parts.append(u'<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>'
                 u'</worksheet>')
    return u''.join(parts)


def write_book(path, sheets):
    ct = [u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          u'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          u'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          u'<Default Extension="xml" ContentType="application/xml"/>'
          u'<Override PartName="/xl/workbook.xml" '
          u'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          u'<Override PartName="/xl/styles.xml" '
          u'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    wb = [u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          u'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          u'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
    rels = [u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    files = []
    for i, (name, rows, opts) in enumerate(sheets):
        k = i + 1
        ct.append(u'<Override PartName="/xl/worksheets/sheet%d.xml" '
                  u'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % k)
        wb.append(u'<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_esc(name[:31]), k, k))
        rels.append(u'<Relationship Id="rId%d" '
                    u'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                    u'Target="worksheets/sheet%d.xml"/>' % (k, k))
        files.append((u'xl/worksheets/sheet%d.xml' % k, _sheet_xml(rows, opts or {})))
    n = len(sheets)
    rels.append(u'<Relationship Id="rId%d" '
                u'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                u'Target="styles.xml"/>' % (n + 1))
    ct.append(u'</Types>')
    wb.append(u'</sheets></workbook>')
    rels.append(u'</Relationships>')
    root = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            u'<Relationship Id="rId1" '
            u'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            u'Target="xl/workbook.xml"/></Relationships>')
    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED)
    for nm, data in [(u'[Content_Types].xml', u''.join(ct)), (u'_rels/.rels', root),
                     (u'xl/workbook.xml', u''.join(wb)), (u'xl/_rels/workbook.xml.rels', u''.join(rels)),
                     (u'xl/styles.xml', _STYLES)] + files:
        zf.writestr(str(nm), data.encode('utf-8'))
    zf.close()
    with open(path, 'wb') as f:
        f.write(buf.getvalue())
