"""The text of a chat attachment, by pure parsing (H-229).

D85 took ``code_task`` from every shared agent. On 2026-10-02 and 2026-10-03
the projects-assistant of a customer org used it on the production host to
read a member's ``.docx``. This module gives that flow back with no code
execution at all: it parses bytes in this process and returns text.

The rules, each with a test in ``tests/unit/test_read_attachment.py``:

* **No subprocess and no code.** ``.docx`` is a zip read with :mod:`zipfile`
  and :mod:`xml.parsers.expat` from the standard library. ``.pdf`` goes
  through ``pypdf``, which is pure Python. Its one subprocess, ``jbig2dec`` for
  a JBIG2 image, is switched off for every parse here
  (``jbig2dec_binary=None``).
* **Bounded.** The input is at most :data:`MAX_FILE_BYTES`. A ``.docx`` reads
  ONE part, the main document, and stops at :data:`MAX_DOCX_XML_BYTES` of
  decompressed XML, so a zip bomb costs no more than that. A PDF reads at most
  :data:`MAX_PDF_PAGES` pages, each stream decompresses to at most
  :data:`PDF_STREAM_LIMIT`, and a page enters a form at most
  :data:`PDF_MAX_FORM_INVOCATIONS` times. Text files keep
  :data:`MAX_TEXT_LINES` lines. The text kept is at most
  :data:`MAX_EXTRACT_CHARS`.
* **A deadline that stops a parse in the middle** (PR #609 fix round 1). A PDF
  checks :data:`DEADLINE_SECONDS` before each operator, through pypdf's
  visitor, also inside a page. A Word part checks it between chunks and
  inside a chunk.
* **A Word part is UTF-8 and has no DTD.** Another encoding is refused before
  the parse, and the parser refuses a DTD and an entity declaration at its
  first event, so no entity is ever expanded. Each element costs O(1).
* **A clean refusal.** Every failure raises :class:`AttachmentRefused`, with
  a sentence for the member. No parser exception reaches the caller.

The module reads no file and no context. ``acb_skills.attachment_tools``
finds the file, and calls :func:`extract_text` on its bytes.
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from dataclasses import dataclass
from typing import Any
from xml.parsers import expat

__all__ = [
    "DEADLINE_SECONDS",
    "MAX_DOCX_PARAGRAPHS",
    "MAX_DOCX_XML_BYTES",
    "MAX_EXTRACT_CHARS",
    "MAX_FILE_BYTES",
    "MAX_PDF_PAGES",
    "MAX_TEXT_LINES",
    "PDF_STREAM_LIMIT",
    "SUPPORTED_SUFFIXES",
    "AttachmentRefused",
    "Extracted",
    "extract_text",
]

#: The upload cap of ``gateway/routes/workspace.py`` (``_MAX_UPLOAD_BYTES``).
MAX_FILE_BYTES = 25 * 1024 * 1024
#: The main document part of a ``.docx``, decompressed. The zip-bomb cap.
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
#: The package relationships part, which names the main part.
MAX_DOCX_RELS_BYTES = 256 * 1024
#: A ``.docx`` with more zip entries than this is not a document.
MAX_ZIP_ENTRIES = 5_000
MAX_DOCX_PARAGRAPHS = 5_000
MAX_PDF_PAGES = 100
MAX_TEXT_LINES = 5_000
#: Text kept from one file. The tool shows it a page at a time.
MAX_EXTRACT_CHARS = 2_000_000
#: The decompressed size of one PDF stream. pypdf's own default is 75 MB. A
#: text page's content stream is far smaller. pypdf parses a stream in full
#: before the deadline hook can run, so this cap also bounds how late a
#: parse stops: a 1 MB stream parses in about 0.6 s (measured 2026-10-04).
PDF_STREAM_LIMIT = 1024 * 1024
#: Form XObject entries in one page. pypdf parses a form again at each entry,
#: and its own default is 5,000. A header, a footer or a logo needs few.
PDF_MAX_FORM_INVOCATIONS = 100
#: Read at each call, so a test can lower it.
DEADLINE_SECONDS = 20.0

_TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv"})
SUPPORTED_SUFFIXES = frozenset({".docx", ".pdf"}) | _TEXT_SUFFIXES

#: The bytes of a Word part that the parser takes between two deadline checks.
_CHUNK = 64 * 1024
#: The elements of a Word part between two deadline checks inside a handler.
_CHECK_EVERY = 1024


class AttachmentRefused(ValueError):
    """A file this module will not read. ``str()`` is a sentence for the member."""


@dataclass(frozen=True)
class Extracted:
    """The text of one file, and how much of the file it covers."""

    text: str
    kind: str
    unit: str
    read: int
    total: int | None
    stopped: bool


class _Deadline:
    """A wall-clock limit that the parse loops check."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    def check(self) -> None:
        if time.monotonic() > self._end:
            raise AttachmentRefused(
                "Reading this file took too long, so I stopped. "
                "Ask the member for a shorter file or a plain text copy."
            )


def extract_text(data: bytes, suffix: str, *, seconds: float | None = None) -> Extracted:
    """The text of *data*, a file with the name suffix *suffix*.

    *seconds* defaults to :data:`DEADLINE_SECONDS`, read at the call. Raises
    :class:`AttachmentRefused` for a type it does not read, for a file over a
    cap, for a file that does not parse and for a parse past the deadline.
    """
    kind = suffix.lower()
    if kind not in SUPPORTED_SUFFIXES:
        raise AttachmentRefused(
            f"I cannot read a {kind or 'file without a type'} file. "
            "I read .docx, .pdf, .txt, .md and .csv files."
        )
    if len(data) > MAX_FILE_BYTES:
        raise AttachmentRefused(
            f"This file is {len(data)} bytes, and I read files of at most "
            f"{MAX_FILE_BYTES} bytes."
        )
    deadline = _Deadline(DEADLINE_SECONDS if seconds is None else seconds)
    if kind == ".docx":
        return _docx_text(data, deadline)
    if kind == ".pdf":
        return _pdf_text(data, deadline)
    return _plain_text(data, kind)


# ── .txt .md .csv ────────────────────────────────────────────────────────────


def _decode(data: bytes) -> str:
    """Text from bytes: a UTF-16 or UTF-8 byte order mark, else UTF-8."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    if "\x00" in text:
        raise AttachmentRefused("This file holds binary data, so it is not a text file.")
    return text


def _plain_text(data: bytes, kind: str) -> Extracted:
    lines = _decode(data).splitlines()
    kept = lines[:MAX_TEXT_LINES]
    text = "\n".join(kept)
    clipped = len(text) > MAX_EXTRACT_CHARS
    return Extracted(
        text=text[:MAX_EXTRACT_CHARS], kind=kind.lstrip("."), unit="line",
        read=len(kept), total=len(lines),
        stopped=clipped or len(kept) < len(lines),
    )


# ── .docx ───────────────────────────────────────────────────────────────────

_NOT_WORD = (
    "I could not read this file as a Word document. If it has a password, "
    "ask the member to remove it, or to attach a PDF or a text copy."
)
_NOT_UTF8 = (
    "This Word document is not stored as UTF-8, so I did not read it. Ask the "
    "member to save it again from Word, or to attach a PDF."
)
_OFFICE_DOCUMENT = "/officeDocument"
_ENCODING_RE = re.compile(rb"""encoding\s*=\s*["']([^"']*)["']""")
#: The run marks that stand for a character in the text.
_MARKS = {"tab": "\t", "br": "\n", "cr": "\n", "noBreakHyphen": "-"}


class _Stop(Exception):  # a signal to end the parse, not an error
    """A handler raises it to end the parse at a cap."""


def _local(tag: object) -> str:
    """An XML name without its namespace. Strict and transitional OOXML agree."""
    return str(tag).rsplit("}", 1)[-1]


def _read_part(zf: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int) -> bytes:
    """At most *cap* decompressed bytes of one part, or a refusal.

    The read asks for ``cap + 1`` bytes, so a part that declares a small size
    and holds more still stops at the cap. :mod:`zipfile` also stops at the
    declared size and checks the CRC there.
    """
    if info.flag_bits & 0x1:
        raise AttachmentRefused(_NOT_WORD)
    if info.file_size > cap:
        raise AttachmentRefused(
            "This Word document is too large once unpacked, so I did not read it."
        )
    with zf.open(info) as fh:
        out = fh.read(cap + 1)
    if len(out) > cap:
        raise AttachmentRefused(
            "This Word document is too large once unpacked, so I did not read it."
        )
    return out


def _require_utf8(xml: bytes) -> None:
    """A Word part is UTF-8. Any other encoding is refused before the parse.

    A UTF-16 or UTF-32 part hides an ASCII search for a DTD, and a parser
    then expands its entities (PR #609 review, P1). So a byte order mark of
    either, a NUL in the first bytes, or a declaration that names another
    encoding, each refuse the part.
    """
    head = xml[:4]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in head:
        raise AttachmentRefused(_NOT_UTF8)
    body = xml[3:] if xml.startswith(b"\xef\xbb\xbf") else xml
    if body.startswith(b"<?xml"):
        end = body.find(b"?>", 0, 512)
        found = _ENCODING_RE.search(body[: end if end != -1 else 512])
        if found and found.group(1).strip().lower() not in (b"utf-8", b"utf8"):
            raise AttachmentRefused(_NOT_UTF8)


def _no_dtd(*_args: object) -> None:
    """A Word part has no DTD. The parser refuses one at its first event."""
    raise AttachmentRefused(_NOT_WORD)


def _parse_xml(xml: bytes, handler: Any, deadline: _Deadline) -> None:
    """Feed one Word part to *handler*: ``start``, ``end`` and ``text``.

    The part must be UTF-8 (:func:`_require_utf8`), and the parser decodes it
    as UTF-8 whatever it declares. The parser refuses a DTD and an entity
    declaration at the first event, so no entity is ever expanded, in any
    encoding. The bytes go in chunks, with a deadline check between two. A
    handler ends the parse early with :class:`_Stop`.
    """
    _require_utf8(xml)
    parser = expat.ParserCreate(encoding="UTF-8", namespace_separator="}")
    parser.StartDoctypeDeclHandler = _no_dtd
    parser.EntityDeclHandler = _no_dtd
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.buffer_text = True
    parser.StartElementHandler = handler.start
    parser.EndElementHandler = handler.end
    parser.CharacterDataHandler = handler.text
    try:
        for at in range(0, len(xml), _CHUNK):
            deadline.check()
            parser.Parse(xml[at:at + _CHUNK], False)
        parser.Parse(b"", True)
    except _Stop:
        return


class _Rels:
    """The target of the ``officeDocument`` relationship in ``_rels/.rels``."""

    def __init__(self) -> None:
        self.target: str | None = None

    def start(self, _name: str, attrs: dict[str, str]) -> None:
        if (self.target is None and attrs.get("Target")
                and str(attrs.get("Type", "")).endswith(_OFFICE_DOCUMENT)):
            self.target = str(attrs["Target"]).lstrip("/")

    def end(self, _name: str) -> None:
        return None

    def text(self, _data: str) -> None:
        return None


def _main_part(zf: zipfile.ZipFile, deadline: _Deadline) -> zipfile.ZipInfo:
    """The main document part, named by ``_rels/.rels``, else the usual name."""
    name = "word/document.xml"
    try:
        rels = zf.getinfo("_rels/.rels")
    except KeyError:
        rels = None
    if rels is not None:
        found = _Rels()
        _parse_xml(_read_part(zf, rels, MAX_DOCX_RELS_BYTES), found, deadline)
        name = found.target or name
    try:
        return zf.getinfo(name)
    except KeyError:
        raise AttachmentRefused(_NOT_WORD) from None


class _Body:
    """The lines of a document body, in order. A table row is one line.

    A paragraph inside a text box ends before the paragraph that holds it, so
    it comes first. A tab stop of a paragraph's properties (``w:tabs``) is
    no text.

    Every event costs O(1), however deep the nesting: a part of 100,000
    nested paragraphs once took 313 s, because each close summed every open
    paragraph (PR #609 fix round 1). The deadline is also checked every
    :data:`_CHECK_EVERY` elements, inside one chunk of the parse.
    """

    def __init__(self, deadline: _Deadline) -> None:
        self.lines: list[str] = []
        self.chars = 0
        self.paragraphs = 0
        self.clipped = False
        self._deadline = deadline
        self._events = 0
        self._rows: list[list[str]] = []
        self._cells: list[list[str]] = []
        self._paras: list[list[str]] = []
        self._para_chars: list[int] = []
        self._in_text = 0
        self._in_tabs = 0
        self._pending = 0

    def full(self) -> bool:
        return (self.clipped or self.paragraphs >= MAX_DOCX_PARAGRAPHS
                or self.chars >= MAX_EXTRACT_CHARS)

    def _emit(self, line: str) -> None:
        if line:
            self.lines.append(line)
            self.chars += len(line) + 1

    def start(self, name: str, _attrs: dict[str, str]) -> None:
        self._events += 1
        if self._events % _CHECK_EVERY == 0:
            self._deadline.check()
        local = _local(name)
        if local == "p":
            self._paras.append([])
            self._para_chars.append(0)
        elif local == "t":
            self._in_text += 1
        elif local == "tabs":
            self._in_tabs += 1
        elif local in _MARKS and self._paras and not self._in_tabs:
            self._paras[-1].append(_MARKS[local])
        elif local == "tbl":
            self._rows.append([])
            self._cells.append([])
        elif local == "tr" and self._rows:
            self._rows[-1] = []
        elif local == "tc" and self._cells:
            self._cells[-1] = []

    def text(self, data: str) -> None:
        if self._in_text and self._paras:
            self._paras[-1].append(data)
            self._para_chars[-1] += len(data)
            self._pending += len(data)
            if self.chars + self._pending >= MAX_EXTRACT_CHARS:
                self.clipped = True
                raise _Stop

    def _end_paragraph(self) -> None:
        self.paragraphs += 1
        text = "".join(self._paras.pop()).strip()
        self._pending -= self._para_chars.pop()
        if self._cells:
            self._cells[-1].append(text)
        else:
            self._emit(text)

    def _end_row(self) -> None:
        row = " | ".join(self._rows[-1])
        if len(self._cells) > 1:
            self._cells[-2].append(row)
        else:
            self._emit(row)

    def end(self, name: str) -> None:
        local = _local(name)
        if local == "t":
            self._in_text = max(0, self._in_text - 1)
        elif local == "tabs":
            self._in_tabs = max(0, self._in_tabs - 1)
        elif local == "p" and self._paras:
            self._end_paragraph()
        elif local == "tc" and self._cells and self._rows:
            self._rows[-1].append(" ".join(t for t in self._cells[-1] if t))
        elif local == "tr" and self._rows:
            self._end_row()
        elif local == "tbl" and self._rows:
            self._rows.pop()
            self._cells.pop()
        if self.full():
            raise _Stop

    def flush(self) -> None:
        """Keep the text of the paragraphs that a clip cut off."""
        for buf in self._paras:
            self._emit("".join(buf).strip())
        self._paras.clear()
        self._para_chars.clear()
        self._pending = 0


def _docx_text(data: bytes, deadline: _Deadline) -> Extracted:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if len(zf.infolist()) > MAX_ZIP_ENTRIES:
                raise AttachmentRefused(_NOT_WORD)
            xml = _read_part(zf, _main_part(zf, deadline), MAX_DOCX_XML_BYTES)
        body = _Body(deadline)
        _parse_xml(xml, body, deadline)
        if body.clipped:
            body.flush()
    except AttachmentRefused:
        raise
    except Exception:  # every parser failure is one clean refusal
        raise AttachmentRefused(_NOT_WORD) from None
    stopped = body.full()
    return Extracted(
        text="\n".join(body.lines)[:MAX_EXTRACT_CHARS], kind="docx", unit="paragraph",
        read=body.paragraphs, total=None if stopped else body.paragraphs, stopped=stopped,
    )


# ── .pdf ────────────────────────────────────────────────────────────────────

_NOT_PDF = (
    "I could not read this file as a PDF. Ask the member for another copy, "
    "or for a Word or text version."
)

#: The pypdf limits for every parse here. ``jbig2dec_binary=None`` is the one
#: that matters most: it is the only path in pypdf that starts a subprocess.
_PDF_CONFIG = {
    "jbig2dec_binary": None,
    "disable_legacy_handling": True,
    "maximum_declared_stream_length": PDF_STREAM_LIMIT,
    "array_based_stream_maximum_output_length": PDF_STREAM_LIMIT,
    "lzw_maximum_output_length": PDF_STREAM_LIMIT,
    "run_length_maximum_output_length": PDF_STREAM_LIMIT,
    "zlib_maximum_output_length": PDF_STREAM_LIMIT,
    "image_maximum_buffer_size": PDF_STREAM_LIMIT,
    "xform_maximum_invocations_per_extraction": PDF_MAX_FORM_INVOCATIONS,
}


def _open_pdf(data: bytes) -> Any:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception:  # a cipher that pypdf cannot run is a lock too
            opened = 0
        if not opened:
            raise AttachmentRefused(
                "This PDF has a password, so I cannot read it. Ask the member "
                "to attach a copy without one."
            )
    return reader


def _page_text(pages: Any, index: int, deadline: _Deadline) -> str:
    """One page's text. A page that does not parse says so, and the rest go on.

    pypdf calls the visitor before each operator, also before the ``Do`` that
    enters a form XObject. So the deadline stops a page in the middle (PR #609
    review, P1). pypdf drops an error raised inside a form, so the stop takes
    effect at the next operator of the page, after at most one stream parse,
    which :data:`PDF_STREAM_LIMIT` bounds.
    """

    def _visit(*_args: Any) -> None:
        deadline.check()

    try:
        text = (pages[index].extract_text(visitor_operand_before=_visit) or "").strip()
    except AttachmentRefused:
        raise
    except Exception:  # one bad page is not a bad file
        text = "(The text of this page could not be read.)"
    return f"[Page {index + 1}]\n{text}" if text else f"[Page {index + 1}]"


def _pdf_text(data: bytes, deadline: _Deadline) -> Extracted:
    import pypdf

    parts: list[str] = []
    chars = 0
    try:
        with pypdf.apply_configuration(**_PDF_CONFIG):
            pages = _open_pdf(data).pages
            total = len(pages)
            for index in range(min(total, MAX_PDF_PAGES)):
                deadline.check()
                parts.append(_page_text(pages, index, deadline))
                chars += len(parts[-1]) + 1
                if chars >= MAX_EXTRACT_CHARS:
                    break
    except AttachmentRefused:
        raise
    except Exception:  # every parser failure is one clean refusal
        raise AttachmentRefused(_NOT_PDF) from None
    read = len(parts)
    text = "\n".join(parts)
    # The char cap can cut the last page read, so "every page read" is not
    # "every word read" (PR #609 review, P2).
    return Extracted(
        text=text[:MAX_EXTRACT_CHARS], kind="pdf", unit="page", read=read, total=total,
        stopped=read < total or len(text) > MAX_EXTRACT_CHARS,
    )
