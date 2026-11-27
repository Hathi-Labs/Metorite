"""Makes the file fixtures of WEB-3 (tests/unit/test_web_files.py).

Run once: ``uv run python tests/unit/fixtures/web/files/make_files.py``. The text is
our own: a made-up printer, never a real customer. Each file stays under 50 KB.
The output is committed, so a test never needs this script.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINES = [
    "Julia Pro data sheet",
    "Build volume is 400 by 400 by 450 millimetres.",
    "The hot end reaches 300 degrees Celsius.",
]


def pdf(lines: list[str], *, matrix: str = "1 0 0 1 20 160") -> bytes:
    """A one-page PDF with the xref offsets of its own objects."""
    ops = [f"BT /F1 12 Tf {matrix} Tm 14 TL"]
    ops += [f"({line}) Tj T*" for line in lines]
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, start)
    return bytes(out)


def docx() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading(LINES[0], 1)
    for line in LINES[1:]:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def pptx() -> bytes:
    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = LINES[0]
    slide.placeholders[1].text = "\n".join(LINES[1:])
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def xlsx() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Specs"
    sheet.append(["Item", "Value"])
    sheet.append(["Build volume", "400 by 400 by 450 millimetres"])
    sheet.append(["Hot end", "300 degrees Celsius"])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def csv() -> bytes:
    rows = ["part,qty,note"] + [f"nozzle-{n:03d},{n},stock row {n} of the Julia Pro list"
                                for n in range(1, 61)]
    return ("\n".join(rows) + "\n").encode("utf-8")


def epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:title>Julia Pro handbook</dc:title><dc:identifier id="id">julia-1'
                   '</dc:identifier><dc:language>en</dc:language></metadata><manifest>'
                   '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("ch1.xhtml",
                   '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title></head>'
                   "<body><h1>Setting up</h1>"
                   + "".join(f"<p>{line}</p>" for line in LINES[1:]) + "</body></html>")
    return buf.getvalue()


def main() -> None:
    files = {
        "julia.pdf": pdf(LINES), "julia.docx": docx(), "julia.pptx": pptx(),
        "julia.xlsx": xlsx(), "julia.csv": csv(), "julia.epub": epub(),
    }
    for name, data in files.items():
        (HERE / name).write_bytes(data)
        print(name, len(data))


if __name__ == "__main__":
    main()
