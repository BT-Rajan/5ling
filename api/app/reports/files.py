"""Spreadsheet (XLSX) and PDF files for report exports (BRD FR-RP-03), with no extra libraries.

Both are deliberately simple: one sheet or a plain table, text and numbers only.

Spreadsheet cells are written as inline strings or plain numbers, never formulas, so a value
such as "=HYPERLINK(...)" from a client name stays text in Excel (no formula injection).
Characters XML 1.0 cannot carry are dropped. The PDF uses the standard Helvetica font, so text is
written in Windows-1252: the rupee sign becomes "Rs." and anything else it cannot show becomes "?".
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

Cell = str | int | float | Decimal | date | None

_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_FIXED = datetime(2000, 1, 1)  # zip entry times: the same bytes for the same report


def escape(text: str) -> str:
    """Text for an XML element or attribute (writing only; nothing here parses XML)."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _text(value: Cell) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def xlsx(
    title: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Cell]],
    note: str | None = None,
) -> bytes:
    """A one-sheet workbook. `note`, if given, is a line above the header (e.g. when generated)."""

    def cell(ref: str, value: Cell, style: int = 0) -> str:
        s = f' s="{style}"' if style else ""
        if isinstance(value, bool) or value is None:
            value = _text(value) if value is not None else ""
        if isinstance(value, int | float | Decimal):
            return f'<c r="{ref}"{s}><v>{value}</v></c>'
        text = escape(_BAD_XML.sub("", _text(value)))
        return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'

    lines = []
    head = 2 if note else 1  # the header's row number
    table: list[Sequence[Cell]] = [[note], list(columns), *rows] if note else [list(columns), *rows]
    for r, row in enumerate(table, start=1):
        style = 1 if r <= head else 0
        cells = "".join(cell(f"{_column(c)}{r}", v, style) for c, v in enumerate(row))
        lines.append(f'<row r="{r}">{cells}</row>')
    widths = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{min(60, max(10, len(c) + 4))}" customWidth="1"/>'
        for i, c in enumerate(columns)
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0">'
        f'<pane ySplit="{head}" topLeftCell="A{head + 1}" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{widths}</cols><sheetData>{''.join(lines)}</sheetData></worksheet>"
    )
    sheet_name = escape(re.sub(r"[\[\]:*?/\\]", " ", title)[:31])
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border/></borders>'
            '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
            '<cellXfs count="2"><xf fontId="0"/><xf fontId="1" applyFont="1"/></cellXfs>'
            "</styleSheet>"
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts.items():
            info = zipfile.ZipInfo(name, date_time=_FIXED.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, body.encode("utf-8"))
    return out.getvalue()


# --- PDF -----------------------------------------------------------------------------------

_W, _H = 842, 595  # A4 landscape, points
_MARGIN = 36
_SIZE = 8
_LEAD = 11


def _pdf_text(value: str) -> bytes:
    raw = value.replace("₹", "Rs.").encode("cp1252", errors="replace")
    return (
        raw.replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
        .replace(b"\r", b" ")
        .replace(b"\n", b" ")
    )


def _fit(text: str, width: float) -> str:
    """Truncate to roughly fit `width` points (Helvetica averages about half the size)."""
    chars = max(3, int(width / (_SIZE * 0.5)))
    return text if len(text) <= chars else text[: chars - 3] + "..."


def pdf(title: str, subtitle: str, columns: Sequence[str], rows: Sequence[Sequence[Cell]]) -> bytes:
    usable = _W - 2 * _MARGIN
    lengths = [
        max([len(c), *(len(_text(r[i])) for r in rows[:500])]) for i, c in enumerate(columns)
    ]
    capped = [min(max(n, 4), 40) for n in lengths]
    total = sum(capped) or 1
    widths = [usable * n / total for n in capped]
    xs = [_MARGIN + sum(widths[:i]) for i in range(len(widths))]
    per_page = int((_H - 2 * _MARGIN - 4 * _LEAD) / _LEAD)
    chunks = [rows[i : i + per_page] for i in range(0, len(rows), per_page)] or [[]]

    def line(y: float, cells: Sequence[str], font: str) -> bytes:
        out = b""
        for x, w, text in zip(xs, widths, cells, strict=False):
            out += b"BT /%s %d Tf %.1f %.1f Td (%s) Tj ET\n" % (
                font.encode(),
                _SIZE,
                x,
                y,
                _pdf_text(_fit(text, w - 4)),
            )
        return out

    pages: list[bytes] = []
    for n, chunk in enumerate(chunks, start=1):
        y = _H - _MARGIN
        body = b"BT /F2 12 Tf %.1f %.1f Td (%s) Tj ET\n" % (_MARGIN, y, _pdf_text(title))
        y -= 14
        body += b"BT /F1 8 Tf %.1f %.1f Td (%s) Tj ET\n" % (
            _MARGIN,
            y,
            _pdf_text(f"{subtitle} - page {n} of {len(chunks)}"),
        )
        y -= _LEAD * 2
        body += line(y, list(columns), "F2")
        y -= 3
        body += b"%.1f %.1f m %.1f %.1f l S\n" % (_MARGIN, y, _W - _MARGIN, y)
        for row in chunk:
            y -= _LEAD
            body += line(y, [_text(v) for v in row], "F1")
        pages.append(body)

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # pages, filled in below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for body in pages:
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(body), body))
        content = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (_W, _H, content)
        )
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (i, obj))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )
    return out.getvalue()
