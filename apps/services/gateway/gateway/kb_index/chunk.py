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

    for section in _sections(title, body_md):
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
