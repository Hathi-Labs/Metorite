"""Chunking a page (WS-61 KB-3, ``knowledge_base.md`` §6.2).

Pure functions. No database, no network, no import from the gateway.

The rule for a page:

* Split on headings (``#`` to ``######``, outside a code fence). Each chunk
  carries the full heading path, rooted at the page title:
  ``"Returns policy > Refund window"``.
* A section longer than about :data:`MAX_TOKENS` tokens splits on paragraphs,
  and each part repeats the heading path. A single paragraph longer than the
  limit splits on words.
* A Markdown table is one chunk, with its header row repeated, at most
  :data:`MAX_TABLE_ROWS` rows to a chunk.

A chunk's ``content_sha`` is the hash of its heading path and its text. The
indexer keeps a row whose sha is still there, and so a one-word edit costs
one embedding call, not thirty. Two parts of one page with the same sha are
one chunk.

The stored forms of KB-2p (``knowledge_base.md`` §21.7) are HTML fragments
in the Markdown body: a text colour, a highlight, ``sup`` and ``sub``, a
status badge, a date, and the ``data-type`` blocks (columns, subpages, a
page break). :func:`strip_stored_forms` takes their tags off before the
split and keeps their text. It strips nothing else, so a ``<details>``
toggle, an aligned paragraph, an HTML table and every other raw HTML keep
their bytes and their ``content_sha``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

#: About this many tokens make one chunk (§6.2).
MAX_TOKENS = 600

#: The rough size of a token in characters, for the estimate.
CHARS_PER_TOKEN = 4

#: The most body rows of a table in one chunk (§6.2).
MAX_TABLE_ROWS = 40

#: The most chunks one page indexes. A body of many tiny headed sections
#: would otherwise write thousands of rows in one transaction, and a title
#: edit would re-embed all of them. The rest of the page is not indexed.
MAX_CHUNKS = 512

#: Between two levels of a heading path.
PATH_SEP = " > "

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


@dataclass(frozen=True)
class Chunk:
    """One part of a page, as the index stores it."""

    ordinal: int
    heading_path: str
    raw: str
    content_sha: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def embed_text(self) -> str:
        """The text the embed door reads: the heading path, then the text."""
        return embed_text(self.heading_path, self.raw)

    def metadata_json(self) -> str:
        return json.dumps(self.metadata, sort_keys=True)


def estimate_tokens(text: str) -> int:
    """A rough token count: the characters over :data:`CHARS_PER_TOKEN`."""
    return math.ceil(len(text or "") / CHARS_PER_TOKEN)


def embed_text(heading_path: str | None, raw: str) -> str:
    """The text of a chunk that gets a vector. One rule, used by the indexer."""
    path = (heading_path or "").strip()
    body = (raw or "").strip()
    if path and body:
        return f"{path}\n\n{body}"
    return path or body


def content_sha(heading_path: str | None, raw: str) -> str:
    """The hash of a chunk: its heading path and its text."""
    payload = f"{heading_path or ''}\n{raw}".encode("utf-8", "replace")
    return hashlib.sha256(payload).hexdigest()


# ── The stored forms of KB-2p ───────────────────────────────────────────────

#: A colour value the editor writes: a name of its list, or a hex colour.
_COLOR = r'(?:[a-z]{3,10}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})'

#: The inline open tags of the stored forms, each one exact (§21.7).
_INLINE_OPEN = re.compile(
    r'<span data-color="' + _COLOR + r'">'
    r'|<mark data-color="' + _COLOR + r'">'
    r'|<sup>|<sub>'
    r'|<span data-type="status" data-color="[a-z]{3,10}">'
    r'|<time data-type="date" datetime="\d{4}-\d{2}-\d{2}">'
)

#: Any open or close tag of the four inline names. A raw tag of one of these
#: names still counts, so that its close tag pairs with it and not with ours.
_INLINE_TAG = re.compile(r"<(/?)(span|mark|sup|sub|time)\b[^<>]*>", re.I)

#: The quote marks and the indent before a block in a callout, a quote or a
#: list item, then the rest of the line.
_PREFIX = re.compile(r"^((?:[ \t]*>)*[ \t]*)(.*)$")

#: The block forms on one line: a subpages list and a page break.
_BLOCK_LINE = re.compile(
    r'(?:<div data-type="subpages" data-order="(?:manual|title|recent)"></div>'
    r'|<div data-type="page-break"></div>)[ \t]*'
)

#: The open line of columns or of one column, and a close line.
_DIV_OPEN = re.compile(r'<div data-type="(?:columns" data-count="[2-5]|column)">[ \t]*')
_DIV_CLOSE = re.compile(r"</div>[ \t]*")
_DIV_TAG = re.compile(r"<(/?)div\b[^<>]*>", re.I)
_CODE_RUN = re.compile(r"`+")


def _bare(line: str) -> str:
    """A line with its block tag taken off: the quote marks stay."""
    match = _PREFIX.match(line)
    return match.group(1).rstrip() if match else ""


def _rest(line: str) -> str:
    """A line after its quote marks and its indent."""
    match = _PREFIX.match(line)
    return match.group(2) if match else line


def _code_spans(text: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` of each code span in *text*, as CommonMark reads them."""
    spans: list[tuple[int, int]] = []
    pos = 0
    while True:
        opened = _CODE_RUN.search(text, pos)
        if not opened:
            return spans
        fence = opened.group(0)
        close = re.compile(r"(?<!`)" + fence + r"(?!`)").search(text, opened.end())
        if not close:
            pos = opened.end()
            continue
        spans.append((opened.start(), close.end()))
        pos = close.end()


def _strip_inline(text: str) -> str:
    """The inline stored forms of one paragraph, without their tags.

    Each tag pairs with the close tag of its own name, past any raw tag of
    that name. Only a pair whose open tag is an exact stored form loses its
    tags. A tag inside a code span is code, and it stays.
    """
    if "<" not in text:
        return text
    spans = _code_spans(text)
    stacks: dict[str, list[tuple[int, int, bool]]] = {}
    cut: list[tuple[int, int]] = []
    for m in _INLINE_TAG.finditer(text):
        if any(start <= m.start() < end for start, end in spans):
            continue
        name = m.group(2).lower()
        stack = stacks.setdefault(name, [])
        if not m.group(1):
            stack.append((m.start(), m.end(), bool(_INLINE_OPEN.fullmatch(m.group(0)))))
        elif stack:
            start, end, ours = stack.pop()
            if ours:
                cut.extend([(start, end), (m.start(), m.end())])
    if not cut:
        return text
    out: list[str] = []
    pos = 0
    for start, end in sorted(cut):
        out.append(text[pos:start])
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _fenced_lines(lines: list[str]) -> list[bool]:
    """For each line: True when it is a fence line or inside a code fence."""
    fenced = [False] * len(lines)
    fence: str | None = None
    for i, line in enumerate(lines):
        opened = _FENCE.match(line)
        if fence is None and opened:
            fence = opened.group(1)
            fenced[i] = True
        elif fence is not None:
            fenced[i] = True
            if line.strip().startswith(fence):
                fence = None
    return fenced


def _strip_blocks(lines: list[str], fenced: list[bool]) -> None:
    """Empty the tag lines of the block forms, in place.

    A ``<div>`` line pushes and a ``</div>`` line pops. A pair whose open
    line is a stored form loses the tags of both lines, and the quote marks
    in front stay. A raw ``<div>`` pushes a mark of its own, so its close
    tag pairs with it.
    """
    stack: list[int | None] = []
    for i, line in enumerate(lines):
        rest = "" if fenced[i] else _rest(line)
        if _BLOCK_LINE.fullmatch(rest):
            lines[i] = _bare(line)
        elif _DIV_OPEN.fullmatch(rest):
            stack.append(i)
        elif _DIV_CLOSE.fullmatch(rest):
            opened_at = stack.pop() if stack else None
            if opened_at is not None:
                lines[opened_at] = _bare(lines[opened_at])
                lines[i] = _bare(line)
        elif rest.lower().startswith(("<div", "</div")):
            for tag in _DIV_TAG.finditer(rest):
                if not tag.group(1):
                    stack.append(None)
                elif stack:
                    stack.pop()


def strip_stored_forms(body_md: str) -> str:
    """The body with the tags of each KB-2p stored form taken off.

    The text stays, and so do the lines: a block tag's line becomes empty,
    so it parts paragraphs as a blank line does. Code fences and code spans
    are code, and stay byte for byte. Nothing else changes.
    """
    if "<" not in (body_md or ""):
        return body_md or ""
    lines = body_md.splitlines()
    fenced = _fenced_lines(lines)
    _strip_blocks(lines, fenced)

    # Inline forms, one paragraph at a time.
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.extend(_strip_inline("\n".join(para)).split("\n"))
            para.clear()

    for i, line in enumerate(lines):
        if fenced[i] or not line.strip():
            flush()
            out.append(line)
        else:
            para.append(line)
    flush()
    return "\n".join(out)


# ── Parsing ─────────────────────────────────────────────────────────────────

@dataclass
class _Section:
    path: list[str]
    lines: list[str] = field(default_factory=list)


def _sections(title: str, body_md: str) -> list[_Section]:
    """The heading sections of a body, each with its heading path."""
    root = (title or "").strip()
    stack: list[tuple[int, str]] = []
    sections = [_Section(path=[root] if root else [])]
    in_fence = False
    for line in (body_md or "").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            sections[-1].lines.append(line)
            continue
        match = None if in_fence else _HEADING.match(line)
        if match is None:
            sections[-1].lines.append(line)
            continue
        level, text = len(match.group(1)), match.group(2).strip()
        if not stack and level == 1 and text.lower() == root.lower():
            # A body that opens with the page title as `# Title` adds no level.
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        path = ([root] if root else []) + [t for _, t in stack if t]
        sections.append(_Section(path=path))
    return sections


def _blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """``("text", paragraph lines)`` and ``("table", table lines)``, in order.

    A blank line ends a paragraph, except inside a code fence. A table starts
    at a line that begins with ``|`` and is followed by a separator row, and
    it runs while the lines begin with ``|``.
    """
    blocks: list[tuple[str, list[str]]] = []
    para: list[str] = []
    in_fence = False
    i = 0

    def flush() -> None:
        if any(p.strip() for p in para):
            blocks.append(("text", list(para)))
        para.clear()

    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            in_fence = not in_fence
            para.append(line)
            i += 1
            continue
        if in_fence:
            para.append(line)
            i += 1
            continue
        if (line.lstrip().startswith("|") and i + 1 < len(lines)
                and _TABLE_SEP.match(lines[i + 1])):
            flush()
            table = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                table.append(lines[i])
                i += 1
            blocks.append(("table", table))
            continue
        if not line.strip():
            flush()
        else:
            para.append(line)
        i += 1
    flush()
    return blocks


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    """A paragraph longer than *max_chars*, split on words."""
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for word in paragraph.split():
        if current and size + 1 + len(word) > max_chars:
            parts.append(" ".join(current))
            current, size = [], 0
        current.append(word)
        size += len(word) + (1 if size else 0)
    if current:
        parts.append(" ".join(current))
    return parts


def _prose_parts(paragraphs: list[str], max_chars: int) -> list[str]:
    """Paragraphs packed into parts of at most *max_chars* characters."""
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for para in paragraphs:
        pieces = [para] if len(para) <= max_chars else _split_long(para, max_chars)
        for piece in pieces:
            extra = len(piece) + (2 if buf else 0)
            if buf and size + extra > max_chars:
                parts.append("\n\n".join(buf))
                buf, size = [], 0
                extra = len(piece)
            buf.append(piece)
            size += extra
    if buf:
        parts.append("\n\n".join(buf))
    return parts


def _table_parts(lines: list[str], max_rows: int) -> list[str]:
    """A table, at most *max_rows* body rows to a part, the header repeated."""
    header, rows = lines[:2], lines[2:]
    if not rows:
        return ["\n".join(header)]
    return [
        "\n".join(header + rows[start:start + max_rows])
        for start in range(0, len(rows), max_rows)
    ]


def chunk_page(
    title: str,
    body_md: str,
    *,
    max_tokens: int = MAX_TOKENS,
    max_rows: int = MAX_TABLE_ROWS,
) -> list[Chunk]:
    """The chunks of a page, in reading order, each sha once.

    A page with no body text gives one chunk with an empty text, so its title
    is still found.
    """
    max_chars = max(1, max_tokens * CHARS_PER_TOKEN)
    out: list[Chunk] = []
    seen: set[str] = set()

    def add(path: list[str], raw: str, metadata: dict[str, Any]) -> None:
        heading_path = PATH_SEP.join(path)
        sha = content_sha(heading_path, raw)
        if sha in seen:
            return
        seen.add(sha)
        out.append(Chunk(ordinal=len(out), heading_path=heading_path, raw=raw,
                         content_sha=sha, metadata=metadata))

    for section in _sections(title, strip_stored_forms(body_md)):
        paragraphs: list[str] = []
        for kind, lines in _blocks(section.lines):
            if kind == "table":
                for part in _prose_parts(paragraphs, max_chars):
                    add(section.path, part, {})
                paragraphs = []
                for part in _table_parts(lines, max_rows):
                    add(section.path, part, {"kind": "table"})
            else:
                paragraphs.append("\n".join(lines).strip())
        for part in _prose_parts(paragraphs, max_chars):
            add(section.path, part, {})
    if not out:
        add([(title or "").strip()] if (title or "").strip() else [], "", {})
    return out[:MAX_CHUNKS]
