"""Chunking the text of a connected document (WS-61 KC-5a, ``knowledge_curation.md`` §7.3).

Pure functions. No database, no network. The text comes from
``acb_skills.attachment_text.extract_text``, and this module reads its shapes:

* a deck: ``## Slide N`` opens each slide, and ``### Notes`` opens its notes;
* a workbook: ``## Sheet: <name>`` opens each sheet, a line is one row, and a
  tab parts two columns. An Excel or OpenDocument line starts with the
  reference of its first value, such as ``A2``;
* a PDF: ``[Page N]`` opens each page.

**Three templates**, chosen by ``Extracted.kind``:

* **Deck** (``pptx``, ``odp``) — one chunk for each slide, notes included.
  The path is ``<file> > Slide N`` and the metadata is ``{"slide": N}``. A long
  slide splits into parts, and each part repeats the path.
* **Sheet** (``xlsx``, ``ods``, and ``csv`` or ``tsv`` as ONE sheet) — for each
  sheet, the first row is the header. The data rows come in groups of
  :data:`~gateway.kb_index.chunk.MAX_TABLE_ROWS`, and each group repeats the
  header. The path is ``<file> > Sheet: X > Rows a-b`` and the metadata is
  ``{"sheet": X, "row_from": a, "row_to": b}``.
* **Prose** (every other kind) — the path is ``<file>``, or ``<file> > Page N``
  for a PDF. The chunks pack paragraphs. Only ``md`` and ``html`` split by a
  Markdown heading.

**What this module never does.** It never runs ``chunk_page`` or
``strip_stored_forms`` on file text. Those two read the stored forms of a
Knowledge page, and a file is not a page.

A chunk's ``content_sha`` is the hash of its path and its text, so the path
text is part of the sha: a file that moves to a new name gets new chunks. A
document keeps at most :data:`~gateway.kb_index.chunk.MAX_CHUNKS` chunks, and
``truncated`` says that it had more. A document with no text still gets ONE
chunk with an empty text, so that its file name is found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from gateway.kb_index.chunk import (
    CHARS_PER_TOKEN,
    MAX_CHUNKS,
    MAX_TABLE_ROWS,
    MAX_TOKENS,
    PATH_SEP,
    Chunk,
    _prose_parts,
    _sections,
    content_sha,
)

#: The kinds of ``Extracted.kind`` that each template reads.
DECK_KINDS = frozenset({"pptx", "odp"})
SHEET_KINDS = frozenset({"xlsx", "ods"})
#: A plain table file is ONE sheet.
TABLE_KINDS = frozenset({"csv", "tsv"})
#: The kinds that split by a Markdown heading.
HEADING_KINDS = frozenset({"md", "html"})

TEMPLATE_DECK = "deck"
TEMPLATE_SHEET = "sheet"
TEMPLATE_PROSE = "prose"

#: The name of the one sheet of a csv or tsv file.
TABLE_SHEET = "Sheet1"

_SLIDE = re.compile(r"^## Slide (\d+)\s*$")
_SHEET = "## Sheet: "
_PAGE = re.compile(r"^\[Page (\d+)\]\s*$")
#: The reference of the first value of a workbook line, then a tab.
_CELL_REF = re.compile(r"^[A-Za-z]{1,3}([0-9]{1,7})\t")
#: ``attachment_text.PAGE_UNREADABLE``, copied: this module imports no skill.
_UNREADABLE = "(The text of this page could not be read.)"


@dataclass(frozen=True)
class DocChunks:
    """The chunks of one document, and whether the cap cut it short."""

    chunks: tuple[Chunk, ...]
    truncated: bool


def template_of(kind: str) -> str:
    """The template that reads a file of *kind* (an ``Extracted.kind``)."""
    if kind in DECK_KINDS:
        return TEMPLATE_DECK
    if kind in SHEET_KINDS or kind in TABLE_KINDS:
        return TEMPLATE_SHEET
    return TEMPLATE_PROSE


def _path(*parts: str) -> str:
    return PATH_SEP.join(p for p in parts if p)


def _paragraphs(lines: list[str]) -> list[str]:
    """The blank-line separated paragraphs of *lines*, each trimmed."""
    out: list[str] = []
    para: list[str] = []
    for line in lines:
        if line.strip():
            para.append(line)
        elif para:
            out.append("\n".join(para).strip())
            para = []
    if para:
        out.append("\n".join(para).strip())
    return [p for p in out if p]


# ── Deck ────────────────────────────────────────────────────────────────────

def _deck(name: str, lines: list[str], max_chars: int) -> list[tuple[str, str, dict[str, Any]]]:
    """One entry for each slide part: ``(path, text, metadata)``."""
    slides: list[tuple[int, list[str]]] = []
    for line in lines:
        head = _SLIDE.match(line)
        if head:
            slides.append((int(head.group(1)), []))
        elif slides:
            slides[-1][1].append(line)
    out: list[tuple[str, str, dict[str, Any]]] = []
    for number, body in slides:
        rows = [ln.strip() for ln in body if ln.strip()]
        if not rows:
            continue  # a slide with no text has nothing to find
        joined = "\n".join(rows)
        # A short slide is ONE chunk. A long slide splits, and each part
        # repeats the path (the lines pack like paragraphs).
        parts = [joined] if len(joined) <= max_chars else _prose_parts(rows, max_chars)
        for part in parts:
            out.append((_path(name, f"Slide {number}"), part, {"slide": number}))
    return out


# ── Sheet ───────────────────────────────────────────────────────────────────

def _row_text(line: str, *, refs: bool) -> str:
    """A workbook line without its leading reference, with ``|`` between columns."""
    if refs:
        line = _CELL_REF.sub("", line, count=1)
    return " | ".join(line.split("\t")).strip()


def _row_number(line: str, fallback: int, *, refs: bool) -> int:
    found = _CELL_REF.match(line) if refs else None
    return int(found.group(1)) if found else fallback


def _sheets(text: str, kind: str) -> list[tuple[str, list[str]]]:
    """``(sheet name, row lines)`` for each sheet of *text*."""
    if kind in TABLE_KINDS:
        rows = [ln for ln in text.splitlines() if ln.strip()]
        return [(TABLE_SHEET, rows)] if rows else []
    sheets: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith(_SHEET):
            sheets.append((line[len(_SHEET):].strip(), []))
        elif sheets and line.strip():
            sheets[-1][1].append(line)
    return sheets


def _sheet(name: str, text: str, kind: str,
           max_rows: int) -> list[tuple[str, str, dict[str, Any]]]:
    refs = kind in SHEET_KINDS
    out: list[tuple[str, str, dict[str, Any]]] = []
    for sheet, rows in _sheets(text, kind):
        if not rows:
            continue
        header = _row_text(rows[0], refs=refs)
        head_no = _row_number(rows[0], 1, refs=refs)
        body = rows[1:]
        if not body:
            out.append((_path(name, f"Sheet: {sheet}", f"Rows {head_no}-{head_no}"), header,
                        {"sheet": sheet, "row_from": head_no, "row_to": head_no}))
            continue
        for start in range(0, len(body), max_rows):
            group = body[start:start + max_rows]
            first = _row_number(group[0], start + 2, refs=refs)
            last = _row_number(group[-1], start + len(group) + 1, refs=refs)
            lines = [header, *(_row_text(r, refs=refs) for r in group)]
            out.append((_path(name, f"Sheet: {sheet}", f"Rows {first}-{last}"),
                        "\n".join(lines),
                        {"sheet": sheet, "row_from": first, "row_to": last}))
    return out


# ── Prose ───────────────────────────────────────────────────────────────────

def _pdf(name: str, lines: list[str], max_chars: int) -> list[tuple[str, str, dict[str, Any]]]:
    pages: list[tuple[int, list[str]]] = []
    for line in lines:
        head = _PAGE.match(line)
        if head:
            pages.append((int(head.group(1)), []))
        elif pages:
            pages[-1][1].append(line)
    out: list[tuple[str, str, dict[str, Any]]] = []
    for number, body in pages:
        paras = [p for p in _paragraphs(body) if p != _UNREADABLE]
        for part in _prose_parts(paras, max_chars):
            out.append((_path(name, f"Page {number}"), part, {"page": number}))
    return out


def _headed(name: str, text: str, max_chars: int) -> list[tuple[str, str, dict[str, Any]]]:
    """A Markdown or HTML file: one run of parts for each heading section."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for section in _sections(name, text):
        for part in _prose_parts(_paragraphs(section.lines), max_chars):
            out.append((PATH_SEP.join(section.path), part, {}))
    return out


def _plain(name: str, text: str, max_chars: int) -> list[tuple[str, str, dict[str, Any]]]:
    return [(name, part, {})
            for part in _prose_parts(_paragraphs(text.splitlines()), max_chars)]


# ── The document ────────────────────────────────────────────────────────────

def chunk_document(
    filename: str,
    text: str,
    kind: str,
    *,
    max_tokens: int = MAX_TOKENS,
    max_rows: int = MAX_TABLE_ROWS,
    max_chunks: int = MAX_CHUNKS,
) -> DocChunks:
    """The chunks of one document, in reading order, each sha once.

    *filename* is the node name, the root of each path. *text* is
    ``Extracted.text`` and *kind* is ``Extracted.kind``. At most *max_chunks*
    chunks come back. ``truncated`` is true when more existed.
    """
    name = (filename or "").strip()
    body = text or ""
    max_chars = max(1, max_tokens * CHARS_PER_TOKEN)
    template = template_of(kind)
    if template == TEMPLATE_DECK:
        items = _deck(name, body.splitlines(), max_chars)
    elif template == TEMPLATE_SHEET:
        items = _sheet(name, body, kind, max(1, max_rows))
    elif kind == "pdf":
        items = _pdf(name, body.splitlines(), max_chars)
    elif kind in HEADING_KINDS:
        items = _headed(name, body, max_chars)
    else:
        items = _plain(name, body, max_chars)
    if not items:
        # No text: ONE chunk, so that the file name is found.
        items = [(name, "", {})]
    chunks: list[Chunk] = []
    seen: set[str] = set()
    truncated = False
    for path, raw, meta in items:
        sha = content_sha(path, raw)
        if sha in seen:
            continue
        if len(chunks) >= max_chunks:
            truncated = True
            break
        seen.add(sha)
        chunks.append(Chunk(ordinal=len(chunks), heading_path=path, raw=raw,
                            content_sha=sha, metadata=meta))
    return DocChunks(chunks=tuple(chunks), truncated=truncated)


__all__ = [
    "DECK_KINDS",
    "HEADING_KINDS",
    "SHEET_KINDS",
    "TABLE_KINDS",
    "TABLE_SHEET",
    "TEMPLATE_DECK",
    "TEMPLATE_PROSE",
    "TEMPLATE_SHEET",
    "DocChunks",
    "chunk_document",
    "template_of",
]
