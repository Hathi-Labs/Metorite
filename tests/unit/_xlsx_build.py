"""Excel workbooks for the fences of WS-17 EM-T11b, built with :mod:`zipfile`.

The parts follow the layout that Excel writes: ``_rels/.rels`` names
``xl/workbook.xml``, and ``xl/_rels/workbook.xml.rels`` names each sheet and
the shared strings. A test can replace any part, or add one, to build a
hostile file.
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape, quoteattr

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
WORKSHEET = f"{REL}/worksheet"
STRINGS = f"{REL}/sharedStrings"
OFFICE_DOCUMENT = f"{REL}/officeDocument"


def sheet(rows: str, *, before: str = "", after: str = "") -> bytes:
    """A worksheet part whose ``<sheetData>`` holds *rows*, raw XML."""
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{MAIN}" xmlns:r="{REL}">{before}<sheetData>{rows}</sheetData>'
        f"{after}</worksheet>"
    ).encode()


def strings(items: list[str], *, raw: bool = False) -> bytes:
    """A shared strings part. Each item is plain text, or raw ``<si>`` XML."""
    body = "".join(i if raw else f"<si><t>{escape(i)}</t></si>" for i in items)
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="{MAIN}" count="{len(items)}" uniqueCount="{len(items)}">{body}</sst>'
    ).encode()


def rels(entries: list[tuple[str, str, str]] | list[tuple[str, str, str, str]]) -> bytes:
    """A rels part. Each entry is ``(Id, Type, Target)`` and an optional mode."""
    out = []
    for entry in entries:
        rid, kind, target = entry[:3]
        mode = f" TargetMode={quoteattr(entry[3])}" if len(entry) > 3 else ""
        out.append(
            f"<Relationship Id={quoteattr(rid)} Type={quoteattr(kind)} "
            f"Target={quoteattr(target)}{mode}/>"
        )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PKG}">{"".join(out)}</Relationships>'
    ).encode()


def workbook_xml(entries: list[tuple[str, str, str]]) -> bytes:
    """``xl/workbook.xml``. Each entry is ``(name, rel id, state)``."""
    body = "".join(
        f"<sheet name={quoteattr(name)} sheetId=\"{n}\" r:id={quoteattr(rid)}"
        + (f" state={quoteattr(state)}" if state else "") + "/>"
        for n, (name, rid, state) in enumerate(entries, start=1)
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>{body}</sheets></workbook>'
    ).encode()


def parts(
    sheets: list[tuple[str, bytes]],
    *,
    shared: bytes | None = None,
    hidden: frozenset[str] = frozenset(),
) -> dict[str, bytes]:
    """The parts of a workbook: one worksheet for each ``(name, xml)``."""
    out: dict[str, bytes] = {
        "[Content_Types].xml": (
            b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.'
            b'openxmlformats.org/package/2006/content-types"/>'
        ),
        "_rels/.rels": rels([("rId1", OFFICE_DOCUMENT, "xl/workbook.xml")]),
    }
    entries: list[tuple[str, str, str]] = []
    links: list[tuple[str, str, str]] = []
    for n, (name, xml) in enumerate(sheets, start=1):
        out[f"xl/worksheets/sheet{n}.xml"] = xml
        entries.append((name, f"rId{n}", "hidden" if name in hidden else ""))
        links.append((f"rId{n}", WORKSHEET, f"worksheets/sheet{n}.xml"))
    if shared is not None:
        out["xl/sharedStrings.xml"] = shared
        links.append((f"rId{len(sheets) + 1}", STRINGS, "sharedStrings.xml"))
    out["xl/workbook.xml"] = workbook_xml(entries)
    out["xl/_rels/workbook.xml.rels"] = rels(links)
    return out


def package(files: dict[str, bytes]) -> bytes:
    """A zip of *files*, deflated, in the order given."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def workbook(
    sheets: list[tuple[str, bytes]],
    *,
    shared: bytes | None = None,
    hidden: frozenset[str] = frozenset(),
) -> bytes:
    """A whole ``.xlsx``: :func:`parts` in a zip."""
    return package(parts(sheets, shared=shared, hidden=hidden))


def row(n: int, cells: str) -> str:
    return f'<row r="{n}">{cells}</row>'


def num(ref: str, value: object) -> str:
    return f'<c r="{ref}"><v>{value}</v></c>'


def shared_ref(ref: str, index: int) -> str:
    return f'<c r="{ref}" t="s"><v>{index}</v></c>'


def inline(ref: str, text: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'
