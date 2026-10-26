"""WS-61 KC-5a — the three chunk templates of a connected document, hermetic.

Spec: ``project-docs/specs/knowledge_curation.md`` §7.3 and the KC-5a row of
§22. No database and no network. The module under test is
``gateway/kb_index/chunk_doc.py``, and its input is the REAL output of
``acb_skills.attachment_text.extract_text``, so a change in the shape of that
text turns a test red here.

* **Deck** — one chunk for each ``## Slide N`` with its notes, the path ends
  ``Slide N``, the metadata holds ``slide`` (acceptance A3), and a long slide
  splits with the path repeated.
* **Sheet** — the first row is the header, groups of 40 rows repeat it, the path
  names the rows, and the metadata holds ``sheet``, ``row_from`` and ``row_to``.
  A csv or tsv file is ONE sheet.
* **Prose** — the path is the file, or ``<file> > Page N`` for a PDF. Only md and
  html split by a heading.
* **The sha** holds the path text, and the cap is 512 chunks (``truncated``).
* **No stored form is stripped.** The module never runs ``chunk_page`` or
  ``strip_stored_forms`` on file text (a static read, and a behaviour check).

Mutations this file catches (R7): the header is not repeated, the path leaves
the sha, the cap is lifted, a stored form is stripped from file text, the
slide metadata is lost -> each fails here.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("gateway.kb_index.chunk_doc", reason="gateway not installed")

from acb_skills import attachment_text as at
from gateway.kb_index import chunk_doc
from gateway.kb_index.chunk import CHARS_PER_TOKEN, MAX_CHUNKS, MAX_TABLE_ROWS, MAX_TOKENS
from gateway.kb_index.chunk_doc import chunk_document

from tests.unit import _xlsx_build as xb
from tests.unit.test_attachment_formats import _deck, _paras, _pptx, _slide

_SRC = Path(chunk_doc.__file__)


def _read(data: bytes, suffix: str) -> tuple[str, str]:
    got = at.extract_text(data, suffix)
    return got.text, got.kind


# ── Deck ────────────────────────────────────────────────────────────────────

def test_a_deck_gives_one_chunk_for_each_slide_with_its_slide_in_the_metadata() -> None:
    text, kind = _read(_pptx("Launch plan", "Pricing is flat", "Risks and owners"), ".pptx")
    out = chunk_document("Pitch.pptx", text, kind)
    assert [c.heading_path for c in out.chunks] == [
        "Pitch.pptx > Slide 1", "Pitch.pptx > Slide 2", "Pitch.pptx > Slide 3"]
    assert [c.metadata for c in out.chunks] == [{"slide": 1}, {"slide": 2}, {"slide": 3}]
    assert [c.raw for c in out.chunks] == ["Launch plan", "Pricing is flat", "Risks and owners"]
    assert [c.ordinal for c in out.chunks] == [0, 1, 2] and not out.truncated


def test_the_notes_of_a_slide_stay_in_its_chunk() -> None:
    notes = (b'<?xml version="1.0"?><p:notes xmlns:a="http://schemas.openxmlformats.org/'
             b'drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/'
             b'2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Say the price '
             b'twice</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>')
    deck = _deck([_slide(_paras("Pricing")), _slide(_paras("Close"))], notes={0: notes})
    text, kind = _read(deck, ".pptx")
    assert "### Notes" in text, text
    out = chunk_document("Pitch.pptx", text, kind)
    assert len(out.chunks) == 2
    assert "Pricing" in out.chunks[0].raw and "Say the price twice" in out.chunks[0].raw
    assert out.chunks[1].raw == "Close"


def test_a_long_slide_splits_and_each_part_repeats_the_path() -> None:
    lines = "\n".join(f"Point {n}: " + "word " * 60 for n in range(40))
    text = f"## Slide 1\n{lines}\n## Slide 2\nShort"
    out = chunk_document("Big.pptx", text, "pptx")
    parts = [c for c in out.chunks if c.metadata == {"slide": 1}]
    assert len(parts) > 1
    assert {c.heading_path for c in parts} == {"Big.pptx > Slide 1"}
    assert all(len(c.raw) <= MAX_TOKENS * CHARS_PER_TOKEN for c in parts)
    assert len({c.content_sha for c in parts}) == len(parts), "two parts share one sha"
    assert out.chunks[-1].heading_path == "Big.pptx > Slide 2"


def test_a_slide_with_no_text_gives_no_chunk() -> None:
    out = chunk_document("D.pptx", "## Slide 1\n## Slide 2\nHello", "pptx")
    assert [c.heading_path for c in out.chunks] == ["D.pptx > Slide 2"]


# ── Sheet ───────────────────────────────────────────────────────────────────

def _rows(count: int) -> str:
    out = xb.row(1, xb.inline("A1", "Name") + xb.inline("B1", "Hours"))
    for n in range(2, count + 2):
        out += xb.row(n, xb.inline(f"A{n}", f"person{n}") + xb.num(f"B{n}", n))
    return out


def test_a_sheet_repeats_its_header_in_groups_of_forty_rows() -> None:
    data = xb.workbook([("Plan", xb.sheet(_rows(100)))])
    text, kind = _read(data, ".xlsx")
    out = chunk_document("Staff.xlsx", text, kind)
    assert MAX_TABLE_ROWS == 40
    assert [c.heading_path for c in out.chunks] == [
        "Staff.xlsx > Sheet: Plan > Rows 2-41",
        "Staff.xlsx > Sheet: Plan > Rows 42-81",
        "Staff.xlsx > Sheet: Plan > Rows 82-101"]
    assert [c.metadata for c in out.chunks] == [
        {"sheet": "Plan", "row_from": 2, "row_to": 41},
        {"sheet": "Plan", "row_from": 42, "row_to": 81},
        {"sheet": "Plan", "row_from": 82, "row_to": 101}]
    for c in out.chunks:
        lines = c.raw.split("\n")
        assert lines[0] == "Name | Hours", "the header is not repeated"
    assert out.chunks[0].raw.split("\n")[1] == "person2 | 2"
    assert len(out.chunks[0].raw.split("\n")) == 41 and len(out.chunks[2].raw.split("\n")) == 21


def test_each_sheet_of_a_workbook_has_its_own_chunks() -> None:
    data = xb.workbook([("One", xb.sheet(_rows(3))), ("Two", xb.sheet(_rows(2)))])
    text, kind = _read(data, ".xlsx")
    out = chunk_document("Book.xlsx", text, kind)
    assert [c.metadata["sheet"] for c in out.chunks] == ["One", "Two"]
    assert out.chunks[1].heading_path == "Book.xlsx > Sheet: Two > Rows 2-3"


def test_a_sheet_with_a_header_only_gives_one_chunk() -> None:
    data = xb.workbook([("Only", xb.sheet(xb.row(1, xb.inline("A1", "Name"))))])
    text, kind = _read(data, ".xlsx")
    [only] = chunk_document("H.xlsx", text, kind).chunks
    assert only.raw == "Name" and only.metadata == {"sheet": "Only", "row_from": 1, "row_to": 1}


@pytest.mark.parametrize(("suffix", "sep"), [(".csv", ","), (".tsv", "\t")])
def test_a_csv_or_a_tsv_is_one_sheet(suffix: str, sep: str) -> None:
    body = "\n".join(sep.join([f"k{n}", f"v{n}"]) for n in range(60))
    text, kind = _read(body.encode(), suffix)
    out = chunk_document(f"t{suffix}", text, kind)
    assert [c.metadata["sheet"] for c in out.chunks] == ["Sheet1", "Sheet1"]
    assert [(c.metadata["row_from"], c.metadata["row_to"]) for c in out.chunks] == [
        (2, 41), (42, 60)]
    # The header is the first line of the file, and the second group repeats it.
    shown = " | " if sep == "\t" else sep  # a tab shows as a bar, as in a workbook
    assert out.chunks[1].raw.split("\n")[0] == f"k0{shown}v0"
    assert out.chunks[1].raw.split("\n")[1] == f"k41{shown}v41"


# ── Prose ───────────────────────────────────────────────────────────────────

def test_a_text_file_packs_paragraphs_under_the_file_path() -> None:
    text, kind = _read(b"First paragraph.\n\nSecond paragraph.\n\nThird.", ".txt")
    [only] = chunk_document("notes.txt", text, kind).chunks
    assert only.heading_path == "notes.txt" and only.raw.count("\n\n") == 2
    assert only.metadata == {}


def test_a_long_text_splits_and_each_part_repeats_the_file_path() -> None:
    body = "\n\n".join(f"sentence {n} " * 80 for n in range(30))
    out = chunk_document("long.txt", body, "txt")
    assert len(out.chunks) > 1
    assert {c.heading_path for c in out.chunks} == {"long.txt"}
    assert all(len(c.raw) <= MAX_TOKENS * CHARS_PER_TOKEN for c in out.chunks)


def test_a_pdf_has_one_run_of_chunks_for_each_page() -> None:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for page in ("Alpha clause", "Beta clause"):
        doc.new_page().insert_text((72, 72), page)
    text, kind = _read(doc.tobytes(), ".pdf")
    out = chunk_document("MSA.pdf", text, kind)
    assert [c.heading_path for c in out.chunks] == ["MSA.pdf > Page 1", "MSA.pdf > Page 2"]
    assert [c.metadata for c in out.chunks] == [{"page": 1}, {"page": 2}]
    assert out.chunks[0].raw == "Alpha clause"


def test_markdown_splits_by_heading_and_a_docx_does_not() -> None:
    md = "# Guide\n\nIntro text.\n\n## Setup\n\nInstall it.\n\n### Deep\n\nDetails here."
    out = chunk_document("guide.md", md, "md")
    assert [c.heading_path for c in out.chunks] == [
        "guide.md > Guide", "guide.md > Guide > Setup", "guide.md > Guide > Setup > Deep"]
    html = chunk_document("page.html", "# Top\n\nAbove.\n\n## Part\n\nBelow.", "html")
    assert [c.heading_path for c in html.chunks] == ["page.html > Top", "page.html > Top > Part"]
    # A Word file has no heading detection (out of scope): one path, the file.
    word = chunk_document("brief.docx", "# Not a heading\n\n## Nor this\n\nBody", "docx")
    assert {c.heading_path for c in word.chunks} == {"brief.docx"}


def test_the_stored_forms_of_a_page_are_not_stripped_from_a_file() -> None:
    """The mutation: ``strip_stored_forms`` run on file text would drop the tags."""
    raw = 'See <span data-color="red">the clause</span> and <sup>2</sup> now.'
    [only] = chunk_document("a.txt", raw, "txt").chunks
    assert only.raw == raw


def test_the_module_never_runs_the_page_chunker() -> None:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Call):
            fn = node.func
            names.add(fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", ""))
    assert not {"chunk_page", "strip_stored_forms"} & names, names


# ── The sha and the cap ─────────────────────────────────────────────────────

def test_the_path_is_part_of_the_sha() -> None:
    a = chunk_document("a.pptx", "## Slide 1\nSame words", "pptx").chunks[0]
    b = chunk_document("b.pptx", "## Slide 1\nSame words", "pptx").chunks[0]
    c = chunk_document("a.pptx", "## Slide 2\nSame words", "pptx").chunks[0]
    assert a.raw == b.raw == c.raw
    assert len({a.content_sha, b.content_sha, c.content_sha}) == 3


def test_the_same_text_gives_the_same_shas() -> None:
    text = "## Slide 1\nOne\n## Slide 2\nTwo"
    first = chunk_document("d.pptx", text, "pptx")
    second = chunk_document("d.pptx", text, "pptx")
    assert [c.content_sha for c in first.chunks] == [c.content_sha for c in second.chunks]


def test_a_changed_slide_changes_only_its_sha() -> None:
    old = chunk_document("d.pptx", "## Slide 1\nOne\n## Slide 2\nTwo", "pptx")
    new = chunk_document("d.pptx", "## Slide 1\nOne\n## Slide 2\nTwo changed", "pptx")
    assert old.chunks[0].content_sha == new.chunks[0].content_sha
    assert old.chunks[1].content_sha != new.chunks[1].content_sha


def test_a_document_keeps_at_most_512_chunks_and_says_it_was_cut() -> None:
    slides = "\n".join(f"## Slide {n}\nPoint number {n}" for n in range(1, MAX_CHUNKS + 40))
    out = chunk_document("huge.pptx", slides, "pptx")
    assert len(out.chunks) == MAX_CHUNKS == 512 and out.truncated is True
    exact = "\n".join(f"## Slide {n}\nPoint number {n}" for n in range(1, MAX_CHUNKS + 1))
    assert chunk_document("exact.pptx", exact, "pptx").truncated is False


def test_a_document_with_no_text_gives_one_empty_chunk_so_its_name_is_found() -> None:
    for kind in ("txt", "pdf", "pptx", "xlsx", "md"):
        [only] = chunk_document("empty.file", "", kind).chunks
        assert (only.heading_path, only.raw) == ("empty.file", "")


@pytest.mark.parametrize(("kind", "template"), [
    ("pptx", "deck"), ("odp", "deck"), ("xlsx", "sheet"), ("ods", "sheet"),
    ("csv", "sheet"), ("tsv", "sheet"), ("pdf", "prose"), ("docx", "prose"),
    ("md", "prose"), ("html", "prose"), ("txt", "prose"), ("rtf", "prose"),
])
def test_each_kind_reads_with_its_template(kind: str, template: str) -> None:
    assert chunk_doc.template_of(kind) == template
