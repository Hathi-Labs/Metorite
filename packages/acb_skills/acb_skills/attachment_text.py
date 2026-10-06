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
* **The font setup of a PDF page has caps** (fix round 2). pypdf builds every
  font entry before the first operator, where the visitor does not run. So
  each resource dictionary that a page can reach holds at most
  :data:`PDF_MAX_FONTS` entries and :data:`PDF_MAX_FONT_BYTES` of font program
  bytes (:func:`_check_fonts`).
* **A Word part is UTF-8 and has no DTD.** Another encoding is refused before
  the parse, and the parser refuses a DTD and an entity declaration at its
  first event, so no entity is ever expanded. The parse keeps no tree, each
  element costs O(1), and the depth and the element count have caps
  (:data:`MAX_DOCX_DEPTH`, :data:`MAX_DOCX_ELEMENTS`).
* **A clean refusal.** Every failure raises :class:`AttachmentRefused`, with
  a sentence for the member. No parser exception reaches the caller.
* **An Excel workbook takes the Word path** (WS-17 EM-T11b, spec
  ``email_app_master_plan.md`` §10.4.13). A ``.xlsx`` is read with the same
  zip and XML helpers as a ``.docx``, with no ``openpyxl``. Each helper takes
  the format name, so a ``.docx`` keeps each refusal sentence. The reader
  follows the relationships of the workbook, reads only parts in ``xl/``,
  reads each part once, and gives each sheet as rows of cells with a tab
  between two columns. A formula gives its cached value. The caps of a
  workbook stop the read and set ``stopped``: :data:`MAX_XLSX_SHEETS`,
  :data:`MAX_XLSX_ROWS`, :data:`MAX_XLSX_COLUMN`, :data:`MAX_XLSX_CELLS`,
  :data:`MAX_XLSX_STRINGS`, :data:`MAX_XLSX_XML_BYTES` and
  :data:`MAX_EXTRACT_CHARS`. A strings part over
  :data:`MAX_XLSX_STRINGS_BYTES` is refused.
* **HTML is the standard library parser** (EM-T11b). ``html.parser`` with
  ``convert_charrefs=True`` expands no declared entity. The parse drops
  scripts, styles, templates, the head, comments and declarations, and it
  checks the deadline before each chunk.

The module reads no file and no context. ``acb_skills.attachment_tools``
finds the file, and calls :func:`extract_text` on its bytes.
"""
from __future__ import annotations

import functools
import io
import posixpath
import re
import time
import zipfile
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
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
    "MAX_XLSX_CELLS",
    "MAX_XLSX_COLUMN",
    "MAX_XLSX_ROWS",
    "MAX_XLSX_SHEETS",
    "MAX_XLSX_STRINGS",
    "MAX_XLSX_STRINGS_BYTES",
    "MAX_XLSX_XML_BYTES",
    "PAGE_UNREADABLE",
    "PDF_STREAM_LIMIT",
    "SUPPORTED_SENTENCE",
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
#: Font entries in one resource dictionary: a page, or a form that it enters.
#: pypdf builds every entry before the first operator, where the deadline hook
#: does not run (PR #609 fix round 2). Real pages hold 2 to 40 fonts (LaTeX
#: math about 25, office documents about 10). 64 entries that share one font
#: with 65,000 widths take 0.95 s (measured 2026-10-04).
PDF_MAX_FONTS = 64
#: The font program bytes that pypdf parses for one resource dictionary: the
#: ToUnicode CMap of each entry, or the Type1 font file of an entry with none.
#: pypdf parses them again at each entry, with no cache, at about 3 MB/s
#: (measured: a 285 KB CMap in 0.094 s). So 2 MB bounds the font setup of one
#: dictionary to under 1 s, and a shared CMap counts once for each entry.
PDF_MAX_FONT_BYTES = 2 * 1024 * 1024
#: The XObject entries that the font walk of one page looks at.
_MAX_XOBJECT_WALK = 2_000
#: A read that the deadline cut inside a form, with less text than this, is
#: refused. With more, it comes back marked ``stopped``.
_MIN_PARTIAL_CHARS = 200
#: Read at each call, so a test can lower it.
DEADLINE_SECONDS = 20.0

#: The line that stands for a PDF page that did not parse. The email text
#: route reads a PDF of only these lines as unreadable (WS-17 EM-T11).
PAGE_UNREADABLE = "(The text of this page could not be read.)"

#: The worksheets that one read takes. More stop the read (EM-T11b).
MAX_XLSX_SHEETS = 50
#: The ``<row>`` elements of one sheet. More end that sheet.
MAX_XLSX_ROWS = 5_000
#: The last column that the reader reads, ``GR``. A cell past it is dropped.
MAX_XLSX_COLUMN = 200
#: The ``<c>`` elements of the whole workbook, empty or not. More stop the
#: read. ``_Guard``'s element cap would refuse a part long before the row and
#: column caps bound it, so this cap turns that refusal into a stop.
MAX_XLSX_CELLS = 100_000
#: The shared strings that the reader keeps. An index past them is empty.
MAX_XLSX_STRINGS = 100_000
#: The shared strings part, unpacked. A bigger part is refused.
MAX_XLSX_STRINGS_BYTES = 20 * 1024 * 1024
#: The unpacked XML of one workbook, every part that the reader reads. A part
#: that does not fit in what is left stops the read.
MAX_XLSX_XML_BYTES = 60 * 1024 * 1024

_TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv"})
_HTML_SUFFIXES = frozenset({".html", ".htm"})
SUPPORTED_SUFFIXES = (
    frozenset({".docx", ".xlsx", ".pdf"}) | _HTML_SUFFIXES | _TEXT_SUFFIXES
)
#: The ONE sentence that lists the kinds. ``attachment_tools`` and the email
#: text route of the gateway say it too.
SUPPORTED_SENTENCE = "I read .docx, .xlsx, .pdf, .html, .htm, .txt, .md and .csv files."

#: The bytes of a Word part that the parser takes between two deadline checks.
_CHUNK = 64 * 1024
#: The elements of a Word part between two deadline checks inside a handler.
_CHECK_EVERY = 1024
#: The nesting depth of a Word part. expat keeps one entry for each open
#: element, so depth costs memory: 2.4 M nested empty elements (17 MB of XML,
#: under the part cap) once peaked at 343 MB. Word nests far less than this.
MAX_DOCX_DEPTH = 256
#: The elements of a Word part. A flat part of 4.5 M empty elements once took
#: 10.8 s. The paragraph cap ends a real document long before this.
MAX_DOCX_ELEMENTS = 1_000_000


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


_TOO_SLOW = (
    "Reading this file took too long, so I stopped. "
    "Ask the member for a shorter file or a plain text copy."
)


class _Deadline:
    """A wall-clock limit that the parse loops check.

    ``fired`` stays true once a check has refused. pypdf drops an error that
    is raised inside a form, so the PDF loop reads the flag after each page.
    """

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds
        self.fired = False

    def check(self) -> None:
        if time.monotonic() > self._end:
            self.fired = True
            raise AttachmentRefused(_TOO_SLOW)


def extract_text(data: bytes, suffix: str, *, seconds: float | None = None) -> Extracted:
    """The text of *data*, a file with the name suffix *suffix*.

    *seconds* defaults to :data:`DEADLINE_SECONDS`, read at the call. Raises
    :class:`AttachmentRefused` for a type it does not read, for a file over a
    cap, for a file that does not parse and for a parse past the deadline.
    """
    kind = suffix.lower()
    if kind not in SUPPORTED_SUFFIXES:
        raise AttachmentRefused(
            f"I cannot read a {kind or 'file without a type'} file. {SUPPORTED_SENTENCE}"
        )
    if len(data) > MAX_FILE_BYTES:
        raise AttachmentRefused(
            f"This file is {len(data)} bytes, and I read files of at most "
            f"{MAX_FILE_BYTES} bytes."
        )
    deadline = _Deadline(DEADLINE_SECONDS if seconds is None else seconds)
    if kind == ".docx":
        return _docx_text(data, deadline)
    if kind == ".xlsx":
        return _xlsx_text(data, deadline)
    if kind == ".pdf":
        return _pdf_text(data, deadline)
    if kind in _HTML_SUFFIXES:
        return _html_text(data, deadline)
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


# ── .html .htm (WS-17 EM-T11b) ──────────────────────────────────────────────

#: Elements whose content is no text. ``<body>`` ends an unclosed ``<head>``.
_HTML_SKIP = frozenset({"script", "style", "template", "noscript", "svg", "head"})
#: Elements that end a line. A ``td`` or a ``th`` adds a tab.
_HTML_BLOCKS = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "details",
    "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
    "pre", "section", "summary", "table", "tbody", "tfoot", "thead", "tr", "ul",
})
_HTML_CELLS = frozenset({"td", "th"})
#: Linear: one pass, no backtracking. It runs on the data of one handler call.
_SPACES = re.compile(r"\s+")
#: The characters that ``html.parser`` may hold for one construct that it has
#: not closed, a tag or a comment. Past this, the reader drops the construct
#: and goes on after its end (review round 1, P2). The parser scans its whole
#: buffer again at each feed, so an unclosed tag of 8 MB once took the full
#: deadline. A long inline image (``src="data:..."``) is such a tag too, so the
#: reader cuts it and never refuses the file.
MAX_HTML_HELD = 64 * 1024
#: The decoded characters of one HTML file that the reader parses. Past this,
#: the read stops and sets ``stopped`` (review round 2, P2). The parse costs
#: about 0.8 us for each character of dense tags, so a file at the 25 MB cap
#: held a parse slot for about 19 s. 4 M characters keep the worst case near
#: 3 to 4 s on the dev box.
MAX_HTML_CHARS = 4_000_000
#: The stops of the scan for the end of a start tag: a ``>`` or a quote.
_TAG_STOP = re.compile("[>\"']")
#: The name of a start tag, as ``html.parser`` reads it.
_TAG_NAME = re.compile(r"<([a-zA-Z][^\t\n\r\f />]*)")
_TAG_SPACE = " \t\n\r\f"
#: The end of a comment, for ``html.parser`` and for a browser (round 3).
_COMMENT_END = re.compile(r"--!?>")
#: The window at the end of a feed where ``html.parser`` holds text back
#: for an ``&`` that may start a character reference.
_CHARREF_WINDOW = 64
_NOT_HTML = (
    "I could not read this file as a web page. Ask the member for a PDF or a "
    "text copy."
)


class _Html(HTMLParser):
    """The text of an HTML file, a line for each block.

    ``convert_charrefs=True`` expands only the fixed character references of
    HTML, never an entity that the file declares. A comment, a declaration
    and a processing instruction are no text, because the class does not
    handle them. A handler raises :class:`_Stop` at :data:`MAX_TEXT_LINES`
    lines or :data:`MAX_EXTRACT_CHARS` characters.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.chars = 0
        self.stopped = False
        self._line: list[str] = []
        self._line_len = 0
        self._ends_space = True
        self._skip: dict[str, int] = {}
        self._skipped = 0
        self._in_subset = False

    def _skipping(self) -> bool:
        return self._skipped > 0

    def end_line(self) -> None:
        """Close the line. Linear in its length (review round 1, P0).

        A regex such as `` *\\t *`` is quadratic on a line of spaces with no
        tab, and it holds the GIL inside one C call, where no deadline check
        runs. A split, a strip of each field and a join are each one pass.
        """
        raw = "".join(self._line)
        line = "\t".join(field.strip(" ") for field in raw.split("\t")).strip(" \t")
        self._line, self._line_len, self._ends_space = [], 0, True
        if not line:
            return
        if len(self.lines) >= MAX_TEXT_LINES:
            self.stopped = True
            raise _Stop
        self.lines.append(line)
        self.chars += len(line) + 1

    def handle_decl(self, decl: str) -> None:
        """A declaration is no text. ``html.parser`` ends one at its first
        ``>``, so the tail of an internal subset (``... ]>``) would read as
        text. The reader drops data up to ``]>`` or the next start tag."""
        if "[" in decl and "]" not in decl:
            self._in_subset = True

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        self._in_subset = False
        if tag in _HTML_SKIP:
            self._skip[tag] = self._skip.get(tag, 0) + 1
            self._skipped += 1
            return
        if tag == "body":
            self._skipped -= self._skip.pop("head", 0)
        if self._skipping():
            return
        if tag in _HTML_CELLS:
            self._add("\t")
        elif tag in _HTML_BLOCKS:
            self.end_line()

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        if tag not in _HTML_SKIP and not self._skipping() and tag in _HTML_BLOCKS:
            self.end_line()

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_SKIP:
            if self._skip.get(tag, 0) > 0:
                self._skip[tag] -= 1
                self._skipped -= 1
            return
        if not self._skipping() and tag in _HTML_BLOCKS:
            self.end_line()

    def handle_data(self, data: str) -> None:
        if self._in_subset:
            end = data.find("]>")
            if end < 0:
                return
            self._in_subset = False
            data = data[end + 2:]
        if not self._skipping():
            self._add(_SPACES.sub(" ", data))

    def _add(self, text: str) -> None:
        """Add *text* to the line. A space never follows a space, and a line
        never starts with one, so a line holds no run of spaces."""
        if text[:1] == " " and self._ends_space:
            text = text[1:]
        if not text:
            return
        self._line.append(text)
        self._line_len += len(text)
        self._ends_space = text[-1] == " "
        if self.chars + self._line_len >= MAX_EXTRACT_CHARS:
            self.stopped = True
            self.end_line()
            raise _Stop


def _start_tag_end(text: str, start: int, limit: int) -> int:
    """The index after the ``>`` that ends the start tag at *start*, else -1.

    Linear: the scan jumps from one ``>`` or quote to the next. A quote opens
    a value only after ``=``, as in ``html.parser``, and the same quote ends
    it. So a ``>`` inside a quoted value does not end the tag (round 2, P3).
    The scan stops at *limit*.
    """
    i = start + 1
    while True:
        found = _TAG_STOP.search(text, i, limit)
        if found is None:
            return -1
        j = found.start()
        if text[j] == ">":
            return j + 1
        k = j - 1
        while k >= i and text[k] in _TAG_SPACE:
            k -= 1
        if k >= i and text[k] == "=":
            close = text.find(text[j], j + 1, limit)
            if close < 0:
                return -1
            i = close + 1
        else:
            i = j + 1


def _enter_raw_text(page: _Html, tag: str) -> None:
    """Put *page* into the raw text mode of *tag*, as its start tag would."""
    if tag in page.CDATA_CONTENT_ELEMENTS:
        page.set_cdata_mode(tag)
    elif tag in getattr(page, "RCDATA_CONTENT_ELEMENTS", ()):
        page.set_cdata_mode(tag, escapable=True)


def _cut_held(page: _Html, text: str, pos: int, held: int, limit: int) -> int:
    """Drop the construct that *page* holds, and return where to feed next.

    The cut follows the state of the parser (review round 2, P1):

    * In the raw text of ``<script>``, ``<style>``, ``<title>`` or
      ``<textarea>``, the feed goes on AT the end tag that the parser looks
      for, so the parser closes the element. A cut of text that a member
      can see, the text of a ``<textarea>``, sets ``stopped``.
    * A comment ends at ``-->`` or ``--!>``.
    * Visible text that the parser holds back for an ``&`` goes to the text
      handler, up to that ``&``, and the feed goes on there (round 3).
    * A start tag ends at its first ``>`` outside quotes. The reader then
      calls the start handler with no attributes, so the element counts. A
      ``<script>`` or a ``<style>`` puts the parser into its raw text mode.
    * Any other construct ends at ``>``, a marked section at ``]]>``.

    Each search is one linear pass over the text after the construct, and it
    stops at *limit*, the input cap. A cut that cannot find the end, or that
    disagrees with the parser, drops the rest of the held text and sets
    ``stopped``.
    """
    start = pos - held
    page.rawdata = ""
    elem = page.cdata_elem
    if elem:
        if elem not in _HTML_SKIP:
            page.stopped = True
        found = page.interesting.search(text, max(start, pos - len(elem) - 3), limit)
        if found is None:
            page.stopped = True
            return limit
        return found.start()
    if text.startswith("<!--", start):
        found = _COMMENT_END.search(text, pos - 3, limit)
        if found is None:
            page.stopped = True
            return limit
        return found.end()
    if text[start] != "<" and text.find("<", start, pos) < 0:
        amp = text.rfind("&", max(start + 1, pos - _CHARREF_WINDOW), pos)
        cut = amp if amp > start else pos
        page.handle_data(unescape(text[start:cut]))
        return cut
    name = _TAG_NAME.match(text, start)
    if name is not None:
        end = _start_tag_end(text, start, limit)
        if end < 0:
            page.stopped = True
            return limit
        if end < pos:
            page.stopped = True
            end = pos
        tag = name.group(1).lower()
        page.handle_starttag(tag, [])
        _enter_raw_text(page, tag)
        return end
    if text[start] != "<":  # held page text with a tag in it: say that it was cut
        page.stopped = True
    marker = "]]>" if text.startswith("<![", start) else ">"
    end = text.find(marker, pos - len(marker) + 1, limit)
    if end < 0:
        page.stopped = True
        return limit
    return end + len(marker)


def _feed_bounded(page: _Html, text: str, deadline: _Deadline) -> None:
    """Feed *text* to *page* in chunks, with a deadline check before each.

    The parse takes at most :data:`MAX_HTML_CHARS` characters, and more set
    ``stopped`` (review round 2, P2). After each chunk, the parser holds at
    most :data:`MAX_HTML_HELD` characters of a construct that it has not
    closed. Past that, :func:`_cut_held` drops the construct, so no feed
    scans more than two chunks, and the work stays linear (round 1, P2).
    """
    limit = min(len(text), MAX_HTML_CHARS)
    if len(text) > limit:
        page.stopped = True
    pos = 0
    while pos < limit:
        deadline.check()
        end = min(pos + _CHUNK, limit)
        page.feed(text[pos:end])
        pos = end
        held = len(page.rawdata)
        if held > MAX_HTML_HELD:
            pos = _cut_held(page, text, pos, held, limit)
    deadline.check()
    page.close()


def _html_text(data: bytes, deadline: _Deadline) -> Extracted:
    """The text of an HTML file, decoded as a text file is.

    The text goes to the parser in chunks of :data:`_CHUNK` characters, with
    a deadline check before each one (:func:`_feed_bounded`).
    """
    text = _decode(data)
    page = _Html()
    try:
        _feed_bounded(page, text, deadline)
        page.end_line()
    except _Stop:
        pass
    except AttachmentRefused:
        raise
    except Exception:  # every parser failure is one clean refusal
        raise AttachmentRefused(_NOT_HTML) from None
    return Extracted(
        text="\n".join(page.lines)[:MAX_EXTRACT_CHARS], kind="html", unit="line",
        read=len(page.lines), total=None if page.stopped else len(page.lines),
        stopped=page.stopped,
    )


# ── .docx ───────────────────────────────────────────────────────────────────

#: The format names that the shared zip and XML helpers take (EM-T11b). The
#: first word is the app that saves the file.
_WORD = "Word document"
_EXCEL = "Excel workbook"


def _article(fmt: str) -> str:
    return "an" if fmt[:1] in "AEIOU" else "a"


def _not_readable(fmt: str) -> str:
    return (
        f"I could not read this file as {_article(fmt)} {fmt}. If it has a password, "
        "ask the member to remove it, or to attach a PDF or a text copy."
    )


def _not_utf8(fmt: str) -> str:
    return (
        f"This {fmt} is not stored as UTF-8, so I did not read it. Ask the "
        f"member to save it again from {fmt.split()[0]}, or to attach a PDF."
    )


def _too_complex(fmt: str) -> str:
    return (
        f"This {fmt} holds too many parts, or nests them too deeply, so I "
        "did not read it. Ask the member for a PDF or a text copy."
    )


def _too_large(fmt: str) -> str:
    return f"This {fmt} is too large once unpacked, so I did not read it."


#: The sentences of a ``.docx``, the same as before EM-T11b.
_NOT_WORD = _not_readable(_WORD)
_NOT_UTF8 = _not_utf8(_WORD)
_TOO_COMPLEX = _too_complex(_WORD)
_OFFICE_DOCUMENT = "/officeDocument"
_ENCODING_RE = re.compile(rb"""encoding\s*=\s*["']([^"']*)["']""")
#: The run marks that stand for a character in the text.
_MARKS = {"tab": "\t", "br": "\n", "cr": "\n", "noBreakHyphen": "-"}


class _Stop(Exception):  # a signal to end the parse, not an error
    """A handler raises it to end the parse at a cap."""


def _local(tag: object) -> str:
    """An XML name without its namespace. Strict and transitional OOXML agree."""
    return str(tag).rsplit("}", 1)[-1]


def _read_part(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int, fmt: str = _WORD,
) -> bytes:
    """At most *cap* decompressed bytes of one part, or a refusal.

    The read asks for ``cap + 1`` bytes, so a part that declares a small size
    and holds more still stops at the cap. :mod:`zipfile` also stops at the
    declared size and checks the CRC there. *fmt* names the format in each
    refusal.
    """
    if info.flag_bits & 0x1:
        raise AttachmentRefused(_not_readable(fmt))
    if info.file_size > cap:
        raise AttachmentRefused(_too_large(fmt))
    with zf.open(info) as fh:
        out = fh.read(cap + 1)
    if len(out) > cap:
        raise AttachmentRefused(_too_large(fmt))
    return out


def _require_utf8(xml: bytes, fmt: str = _WORD) -> None:
    """A Word part is UTF-8. Any other encoding is refused before the parse.

    A UTF-16 or UTF-32 part hides an ASCII search for a DTD, and a parser
    then expands its entities (PR #609 review, P1). So a byte order mark of
    either, a NUL in the first bytes, or a declaration that names another
    encoding, each refuse the part.
    """
    head = xml[:4]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in head:
        raise AttachmentRefused(_not_utf8(fmt))
    body = xml[3:] if xml.startswith(b"\xef\xbb\xbf") else xml
    if body.startswith(b"<?xml"):
        end = body.find(b"?>", 0, 512)
        found = _ENCODING_RE.search(body[: end if end != -1 else 512])
        if found and found.group(1).strip().lower() not in (b"utf-8", b"utf8"):
            raise AttachmentRefused(_not_utf8(fmt))


def _no_dtd(fmt: str, *_args: object) -> None:
    """A Word or Excel part has no DTD. The parser refuses one at its first event."""
    raise AttachmentRefused(_not_readable(fmt))


class _Guard:
    """The caps of every Word part, in front of the part's own handler.

    Each element counts toward :data:`MAX_DOCX_ELEMENTS`, and each open one
    toward :data:`MAX_DOCX_DEPTH`. Past either cap the parse refuses, so expat
    never holds more than that many open elements. The deadline is checked
    every :data:`_CHECK_EVERY` elements, inside one chunk too.
    """

    def __init__(self, handler: Any, deadline: _Deadline, fmt: str = _WORD) -> None:
        self._handler = handler
        self._deadline = deadline
        self._fmt = fmt
        self._depth = 0
        self._count = 0
        self.text = handler.text

    def start(self, name: str, attrs: dict[str, str]) -> None:
        self._depth += 1
        self._count += 1
        if self._depth > MAX_DOCX_DEPTH or self._count > MAX_DOCX_ELEMENTS:
            raise AttachmentRefused(_too_complex(self._fmt))
        if self._count % _CHECK_EVERY == 0:
            self._deadline.check()
        self._handler.start(name, attrs)

    def end(self, name: str) -> None:
        self._depth -= 1
        self._handler.end(name)


def _parse_xml(xml: bytes, handler: Any, deadline: _Deadline, fmt: str = _WORD) -> None:
    """Feed one Word or Excel part to *handler*: ``start``, ``end`` and ``text``.

    The part must be UTF-8 (:func:`_require_utf8`), and the parser decodes it
    as UTF-8 whatever it declares. The parser refuses a DTD and an entity
    declaration at the first event, so no entity is ever expanded, in any
    encoding. :class:`_Guard` caps the depth and the element count. The bytes
    go in chunks, with a deadline check between two. A handler ends the
    parse early with :class:`_Stop`. *fmt* names the format in each refusal.
    """
    _require_utf8(xml, fmt)
    handler = _Guard(handler, deadline, fmt)
    parser = expat.ParserCreate(encoding="UTF-8", namespace_separator="}")
    refuse = functools.partial(_no_dtd, fmt)
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
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
    """Each relationship of a rels part, in order (EM-T11b).

    ``target`` is the target of the first ``officeDocument`` relationship, the
    main part, as before. ``found`` holds each relationship that names a
    target: its ``Id``, ``Type``, ``Target`` and ``TargetMode``.
    """

    def __init__(self) -> None:
        self.target: str | None = None
        self.found: list[dict[str, str]] = []

    def start(self, _name: str, attrs: dict[str, str]) -> None:
        if not attrs.get("Target"):
            return
        self.found.append({
            k: str(attrs.get(k, "")) for k in ("Id", "Type", "Target", "TargetMode")
        })
        if self.target is None and str(attrs.get("Type", "")).endswith(_OFFICE_DOCUMENT):
            self.target = str(attrs["Target"]).lstrip("/")

    def end(self, _name: str) -> None:
        return None

    def text(self, _data: str) -> None:
        return None


def _main_part(
    zf: zipfile.ZipFile, deadline: _Deadline,
    default: str = "word/document.xml", fmt: str = _WORD,
) -> zipfile.ZipInfo:
    """The main part, named by ``_rels/.rels``, else *default*."""
    name = default
    try:
        rels = zf.getinfo("_rels/.rels")
    except KeyError:
        rels = None
    if rels is not None:
        found = _Rels()
        _parse_xml(_read_part(zf, rels, MAX_DOCX_RELS_BYTES, fmt), found, deadline, fmt)
        name = found.target or name
    try:
        return zf.getinfo(name)
    except KeyError:
        raise AttachmentRefused(_not_readable(fmt)) from None


class _Body:
    """The lines of a document body, in order. A table row is one line.

    A paragraph inside a text box ends before the paragraph that holds it, so
    it comes first. A tab stop of a paragraph's properties (``w:tabs``) is
    no text.

    Every event costs O(1), and depth does not change that: a part of 100,000
    nested paragraphs once took 313 s, because each close summed every open
    paragraph (PR #609 fix round 1). It keeps no tree. :class:`_Guard` caps
    the depth, the element count and the time.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.chars = 0
        self.paragraphs = 0
        self.clipped = False
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
        body = _Body()
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


# ── .xlsx (WS-17 EM-T11b) ────────────────────────────────────────────────────

_WORKSHEET = "/worksheet"
_SHARED_STRINGS = "/sharedStrings"
_REF_RE = re.compile(r"([A-Za-z]{1,3})([0-9]{1,7})")
#: ASCII digits only. ``str.isdigit`` takes ``²``, and ``int`` refuses it.
_ROW_RE = re.compile(r"[0-9]{1,7}")
_INDEX_RE = re.compile(r"[0-9]{1,9}")
#: A tab, and each line break of ``str.splitlines``, in a value or a sheet
#: name becomes one space. So no value can start a line such as ``## Sheet:``
#: (review round 1, P3: U+2028, U+2029, NEL, VT, FF and FS, GS and RS too).
_LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
_FLAT = str.maketrans(dict.fromkeys("\t" + _LINE_BREAKS, " "))


def _column_name(col: int) -> str:
    """``1`` is ``A``, ``27`` is ``AA``."""
    out = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cell_ref(ref: str) -> tuple[int, int] | None:
    """``(column, row)`` of a reference such as ``B3``, else ``None``."""
    found = _REF_RE.fullmatch(ref or "")
    if not found:
        return None
    col = 0
    for ch in found.group(1).upper():
        col = col * 26 + ord(ch) - 64
    return col, int(found.group(2))


def _attr(attrs: dict[str, str], local: str) -> str | None:
    """The attribute *local*, matched by its local name, whatever its namespace."""
    for name, value in attrs.items():
        if _local(name) == local:
            return str(value)
    return None


class _Workbook:
    """The worksheets of ``workbook.xml``, in order: name, hidden and part.

    It keeps only an entry whose rel id names a worksheet part in *parts*,
    and only the first entry for each part. It stops after
    :data:`MAX_XLSX_SHEETS` + 1 parts, so a workbook of a million entries
    keeps no more than that.
    """

    def __init__(self, parts: dict[str, str]) -> None:
        self._parts = parts
        self._kept: set[str] = set()
        self.sheets: list[tuple[str, bool, str]] = []

    def start(self, name: str, attrs: dict[str, str]) -> None:
        if _local(name) != "sheet":
            return
        part = self._parts.get(_attr(attrs, "id") or "")
        if part is None or part in self._kept:
            return
        self._kept.add(part)
        state = (_attr(attrs, "state") or "").lower()
        self.sheets.append(
            (_attr(attrs, "name") or "", state in ("hidden", "veryhidden"), part)
        )
        if len(self.sheets) > MAX_XLSX_SHEETS:
            raise _Stop

    def end(self, _name: str) -> None:
        return None

    def text(self, _data: str) -> None:
        return None


class _Strings:
    """The shared strings, in order, at most :data:`MAX_XLSX_STRINGS`.

    A string is its ``<t>`` text, also the ``<t>`` of each ``<r>`` run. The
    ``<t>`` of a phonetic run (``<rPh>``) is no text.
    """

    def __init__(self) -> None:
        self.items: list[str] = []
        self.stopped = False
        self._buf: list[str] | None = None
        self._phonetic = 0
        self._in_text = False

    def start(self, name: str, _attrs: dict[str, str]) -> None:
        local = _local(name)
        if local == "si":
            if len(self.items) >= MAX_XLSX_STRINGS:
                self.stopped = True
                raise _Stop
            self._buf = []
        elif local == "rPh":
            self._phonetic += 1
        elif local == "t":
            self._in_text = self._buf is not None and not self._phonetic

    def text(self, data: str) -> None:
        if self._in_text and self._buf is not None:
            self._buf.append(data)

    def end(self, name: str) -> None:
        local = _local(name)
        if local == "t":
            self._in_text = False
        elif local == "rPh":
            self._phonetic = max(0, self._phonetic - 1)
        elif local == "si" and self._buf is not None:
            self.items.append("".join(self._buf))
            self._buf = None


class _Book:
    """The text of a workbook so far, and the caps that stop the whole read."""

    def __init__(self, strings: list[str]) -> None:
        self.strings = strings
        self.lines: list[str] = []
        self.chars = 0
        self.cells = 0
        self.values = 0
        self.stopped = False
        self.done = False

    def emit(self, line: str) -> None:
        self.lines.append(line)
        self.chars += len(line) + 1

    def stop_all(self) -> None:
        self.stopped = True
        self.done = True
        raise _Stop


class _Sheet:
    """The rows of one worksheet as lines, one value for each column.

    A line starts with the reference of its first value and a tab, and one tab
    separates two columns, so an empty cell between two values is an empty
    field. The parse ends at ``</sheetData>``. Each ``<c>`` counts toward
    :data:`MAX_XLSX_CELLS`, and the char cap is checked after each cell, so a
    read can stop in the middle of a row.
    """

    def __init__(self, book: _Book, title: str) -> None:
        self._book = book
        self._title: str | None = title
        self._in_data = False
        self._rows = 0
        self._last_row = 0
        self._row: list[str] | None = None
        self._row_num: int | None = None
        self._line_len = 0
        self._last_col = 0
        self._prev_value_col = 0
        self._cell: tuple[int, int | None, str] | None = None
        self._v: list[str] = []
        self._is: list[str] = []
        self._in_v = False
        self._in_t = False
        self._in_is = 0
        self._phonetic = 0

    def _pending(self) -> int:
        title = len(self._title) + 1 if self._title is not None else 0
        return self._book.chars + title + self._line_len + 1

    def _flush_row(self) -> None:
        if not self._row:
            return
        if self._title is not None:
            self._book.emit(self._title)
            self._title = None
        self._book.emit("".join(self._row))
        self._row = []
        self._line_len = 0

    def start(self, name: str, attrs: dict[str, str]) -> None:
        local = _local(name)
        if local == "sheetData":
            self._in_data = True
        elif not self._in_data:
            return
        elif local == "row":
            self._start_row(attrs)
        elif local == "c" and self._row is not None:
            self._start_cell(attrs)
        elif self._cell is None:
            return
        elif local == "v":
            self._in_v = True
        elif local == "is":
            self._in_is += 1
        elif local == "rPh":
            self._phonetic += 1
        elif local == "t":
            self._in_t = bool(self._in_is) and not self._phonetic

    def _start_row(self, attrs: dict[str, str]) -> None:
        self._rows += 1
        if self._rows > MAX_XLSX_ROWS:
            self._book.stopped = True
            raise _Stop
        raw = _attr(attrs, "r") or ""
        self._row_num = int(raw) if _ROW_RE.fullmatch(raw) else None
        self._row = []
        self._line_len = 0
        self._last_col = 0

    def _start_cell(self, attrs: dict[str, str]) -> None:
        self._book.cells += 1
        if self._book.cells > MAX_XLSX_CELLS:
            self._flush_row()
            self._book.stop_all()
        ref = _cell_ref(_attr(attrs, "r") or "")
        col = ref[0] if ref else self._last_col + 1
        self._v, self._is = [], []
        if col <= self._last_col:
            self._cell = (0, None, "")  # not after the column before it: dropped
            return
        if col > MAX_XLSX_COLUMN:
            # Past column GR: dropped. The column still moves, so a later cell
            # with no `r` is past GR too (review round 1, P3).
            self._book.stopped = True
            self._last_col = col
            self._cell = (0, None, "")
            return
        self._cell = (col, ref[1] if ref else None, _attr(attrs, "t") or "")

    def text(self, data: str) -> None:
        if self._in_v:
            self._v.append(data)
        elif self._in_t:
            self._is.append(data)

    def _value(self, kind: str) -> str:
        raw = "".join(self._v)
        if kind == "s":
            index = raw.strip()
            if not _INDEX_RE.fullmatch(index) or int(index) >= len(self._book.strings):
                return ""
            return self._book.strings[int(index)]
        if kind == "inlineStr":
            return "".join(self._is)
        if kind == "b":
            if not raw.strip():
                return ""
            return "TRUE" if raw.strip().lower() in ("1", "true") else "FALSE"
        return raw

    def _end_cell(self) -> None:
        col, row_num, kind = self._cell or (0, None, "")
        self._cell = None
        self._in_v = self._in_t = False
        if not col or self._row is None:
            return
        self._last_col = col
        value = self._value(kind).translate(_FLAT)
        if not value:
            return
        if not self._row:
            if self._row_num is None:
                self._row_num = row_num if row_num is not None else self._last_row + 1
            head = f"{_column_name(col)}{self._row_num}"
            self._row.append(head)
            self._line_len = len(head)
            gap = 1
        else:
            gap = col - self._prev_value_col
        self._row.append("\t" * gap + value)
        self._line_len += gap + len(value)
        self._prev_value_col = col
        self._book.values += 1
        if self._pending() >= MAX_EXTRACT_CHARS:
            self._flush_row()
            self._book.stop_all()

    def end(self, name: str) -> None:
        local = _local(name)
        if not self._in_data:
            return
        if local == "sheetData":
            self._flush_row()
            raise _Stop
        if local == "v":
            self._in_v = False
        elif local == "t":
            self._in_t = False
        elif local == "is":
            self._in_is = max(0, self._in_is - 1)
        elif local == "rPh":
            self._phonetic = max(0, self._phonetic - 1)
        elif local == "c":
            self._end_cell()
        elif local == "row" and self._row is not None:
            self._last_row = self._row_num if self._row_num is not None else self._last_row + 1
            self._flush_row()
            self._row = None


def _xl_part(name: str) -> str:
    """*name* when it is a normal path inside ``xl/``, else a refusal."""
    norm = posixpath.normpath(name)
    if norm != name or not norm.startswith("xl/"):
        raise AttachmentRefused(_not_readable(_EXCEL))
    return norm


def _target(base_dir: str, target: str) -> str:
    """The part that a relationship of the workbook names, inside ``xl/``.

    A target that starts with ``/`` starts at the package root. Any other
    target starts at the folder of the workbook.
    """
    joined = target.lstrip("/") if target.startswith("/") else posixpath.join(base_dir, target)
    return _xl_part(posixpath.normpath(joined))


class _Budget:
    """The unpacked XML that one workbook may read, :data:`MAX_XLSX_XML_BYTES`."""

    def __init__(self, zf: zipfile.ZipFile) -> None:
        self._zf = zf
        self.left = MAX_XLSX_XML_BYTES

    def read(self, info: zipfile.ZipInfo, cap: int) -> bytes | None:
        """The bytes of *info*, or ``None`` when they do not fit what is left.

        A part over its own *cap* is refused, as a Word part is.
        """
        if info.file_size > cap:
            raise AttachmentRefused(_too_large(_EXCEL))
        if info.file_size > self.left:
            return None
        data = _read_part(self._zf, info, min(cap, self.left), _EXCEL)
        self.left -= len(data)
        return data


def _getinfo(zf: zipfile.ZipFile, name: str) -> zipfile.ZipInfo | None:
    try:
        return zf.getinfo(name)
    except KeyError:
        return None


def _book_parts(
    zf: zipfile.ZipFile, deadline: _Deadline, budget: _Budget,
) -> tuple[list[tuple[str, bool, str]], str | None, set[str]]:
    """``([(title, hidden, sheet part)], strings part, parts read)``.

    Each sheet comes in the order of the workbook, with its worksheet part.
    An entry whose relationship is not a worksheet, is external or names no
    part is skipped. The strings part comes from its relationship, else from
    the usual name.
    """
    main = _xl_part(_main_part(zf, deadline, "xl/workbook.xml", _EXCEL).filename)
    folder = posixpath.dirname(main)
    rels_name = posixpath.join(folder, "_rels", posixpath.basename(main) + ".rels")
    seen = {main, rels_name}
    rels = _Rels()
    rels_info = _getinfo(zf, rels_name)
    if rels_info is not None:
        _parse_xml(_read_budgeted(budget, rels_info, MAX_DOCX_RELS_BYTES), rels, deadline,
                   _EXCEL)
    by_id: dict[str, str] = {}
    strings: str | None = None
    for rel in rels.found:
        if rel["TargetMode"].lower() == "external":
            continue
        if rel["Type"].endswith(_WORKSHEET):
            by_id.setdefault(rel["Id"], _target(folder, rel["Target"]))
        elif rel["Type"].endswith(_SHARED_STRINGS) and strings is None:
            strings = _target(folder, rel["Target"])
    if strings is None and _getinfo(zf, "xl/sharedStrings.xml") is not None:
        strings = "xl/sharedStrings.xml"
    main_info = _getinfo(zf, main)
    if main_info is None:
        raise AttachmentRefused(_not_readable(_EXCEL))
    # Only a part that the zip holds, and that is not another part of the
    # book, counts toward the bound of `_Workbook` (review round 1, P3).
    usable = {
        rid: part for rid, part in by_id.items()
        if part not in seen and part != strings and _getinfo(zf, part) is not None
    }
    workbook = _Workbook(usable)
    _parse_xml(_read_budgeted(budget, main_info, MAX_DOCX_XML_BYTES), workbook, deadline,
               _EXCEL)
    return workbook.sheets, strings, seen


def _read_budgeted(budget: _Budget, info: zipfile.ZipInfo, cap: int) -> bytes:
    """The workbook or its rels part. These come first, so they always fit."""
    part = budget.read(info, cap)
    if part is None:
        raise AttachmentRefused(_too_large(_EXCEL))
    return part


def _xlsx_text(data: bytes, deadline: _Deadline) -> Extracted:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if len(zf.infolist()) > MAX_ZIP_ENTRIES:
                raise AttachmentRefused(_not_readable(_EXCEL))
            book = _read_book(zf, deadline)
    except AttachmentRefused:
        raise
    except Exception:  # every parser failure is one clean refusal
        raise AttachmentRefused(_not_readable(_EXCEL)) from None
    return Extracted(
        text="\n".join(book.lines)[:MAX_EXTRACT_CHARS], kind="xlsx", unit="cell",
        read=book.values, total=None if book.stopped else book.values,
        stopped=book.stopped,
    )


def _read_book(zf: zipfile.ZipFile, deadline: _Deadline) -> _Book:
    """Each worksheet, in order, under the caps. Each part is read once."""
    budget = _Budget(zf)
    sheets, strings_name, seen = _book_parts(zf, deadline, budget)
    strings = _Strings()
    if strings_name is not None and strings_name not in seen:
        seen.add(strings_name)
        info = _getinfo(zf, strings_name)
        raw = None if info is None else budget.read(info, MAX_XLSX_STRINGS_BYTES)
        if info is not None and raw is None:
            book = _Book([])
            book.stopped = True
            return book
        if raw is not None:
            _parse_xml(raw, strings, deadline, _EXCEL)
    book = _Book(strings.items)
    book.stopped = strings.stopped
    read = 0
    for name, hidden, part in sheets:
        if part in seen:
            continue
        seen.add(part)
        info = _getinfo(zf, part)
        if info is None:
            continue
        read += 1
        if read > MAX_XLSX_SHEETS:
            book.stopped = True
            break
        xml = budget.read(info, MAX_DOCX_XML_BYTES)
        if xml is None:
            book.stopped = True
            break
        title = f"## Sheet: {name.translate(_FLAT)}{' (hidden)' if hidden else ''}"
        _parse_xml(xml, _Sheet(book, title), deadline, _EXCEL)
        if book.done:
            break
    return book


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


_TOO_MANY_FONTS = (
    "This PDF has a page with too many fonts, or with too much font data, so "
    "I did not read it. Ask the member for a text copy."
)


def _resolve(obj: Any) -> Any:
    """*obj* with an indirect reference followed, or ``None``."""
    try:
        return obj.get_object() if obj is not None else None
    except Exception:  # a broken reference is no font
        return None


def _font_bytes(font: Any, sizes: dict[int, int]) -> int:
    """The font program bytes that pypdf parses for one font entry.

    That is the entry's ToUnicode CMap, or for a Type1 font with none, its
    font file. *sizes* keeps each stream's size by object, so a stream that
    many entries share decodes once here.
    """
    from pypdf.generic import DictionaryObject, StreamObject

    if not isinstance(font, DictionaryObject):
        return 0
    target = _resolve(font.get("/ToUnicode"))
    if target is None and font.get("/Subtype") == "/Type1":
        descriptor = _resolve(font.get("/FontDescriptor"))
        if isinstance(descriptor, DictionaryObject):
            target = _resolve(descriptor.get("/FontFile"))
    if not isinstance(target, StreamObject):
        return 0
    key = id(target)
    if key not in sizes:
        try:
            sizes[key] = len(target.get_data())
        except Exception:  # pypdf fails this font at once, so it costs nothing
            sizes[key] = 0
    return sizes[key]


def _check_fonts(page: Any, deadline: _Deadline) -> None:
    """Refuse a page whose font setup the deadline could not stop in time.

    pypdf builds every font entry of a page, and of each form it enters,
    before the first operator, and the deadline hook runs only between
    operators (PR #609 fix round 2). So this walks the page's resources and
    the resources of every form it can reach, and refuses a dictionary with
    more than :data:`PDF_MAX_FONTS` entries or :data:`PDF_MAX_FONT_BYTES`
    of font program bytes.
    """
    from pypdf.generic import DictionaryObject, StreamObject

    sizes: dict[int, int] = {}
    seen: set[int] = set()
    stack = [_resolve(page.get_inherited("/Resources", None))]
    walked = 0
    while stack:
        deadline.check()
        resources = stack.pop()
        if not isinstance(resources, DictionaryObject):
            continue
        fonts = _resolve(resources.get("/Font"))
        if isinstance(fonts, DictionaryObject):
            if len(fonts) > PDF_MAX_FONTS:
                raise AttachmentRefused(_TOO_MANY_FONTS)
            if sum(_font_bytes(_resolve(fonts[n]), sizes) for n in fonts) > PDF_MAX_FONT_BYTES:
                raise AttachmentRefused(_TOO_MANY_FONTS)
        xobjects = _resolve(resources.get("/XObject"))
        if not isinstance(xobjects, DictionaryObject):
            continue
        for name in xobjects:
            walked += 1
            if walked > _MAX_XOBJECT_WALK:
                raise AttachmentRefused(_TOO_MANY_FONTS)
            form = _resolve(xobjects[name])
            if (isinstance(form, StreamObject) and form.get("/Subtype") == "/Form"
                    and id(form) not in seen):
                seen.add(id(form))
                stack.append(_resolve(form.get("/Resources")))


def _page_text(pages: Any, index: int, deadline: _Deadline) -> tuple[str, int]:
    """One page's text and its count of characters.

    A page that does not parse says so, and the rest go on. pypdf calls the
    visitor before each operator, also before the ``Do`` that enters a form
    XObject. So the deadline stops a page in the middle (PR #609 review, P1).
    pypdf drops an error raised inside a form, so the stop takes effect at
    the next operator of the page, after at most one stream parse, which
    :data:`PDF_STREAM_LIMIT` bounds. :func:`_check_fonts` bounds the font
    setup that runs before the first operator.
    """

    def _visit(*_args: Any) -> None:
        deadline.check()

    try:
        _check_fonts(pages[index], deadline)
        text = (pages[index].extract_text(visitor_operand_before=_visit) or "").strip()
    except AttachmentRefused:
        raise
    except Exception:  # one bad page is not a bad file
        text = PAGE_UNREADABLE
    head = f"[Page {index + 1}]"
    return (f"{head}\n{text}" if text else head), len(text)


def _pdf_text(data: bytes, deadline: _Deadline) -> Extracted:
    import pypdf

    parts: list[str] = []
    chars = 0
    words = 0
    try:
        with pypdf.apply_configuration(**_PDF_CONFIG):
            pages = _open_pdf(data).pages
            total = len(pages)
            for index in range(min(total, MAX_PDF_PAGES)):
                deadline.check()
                part, count = _page_text(pages, index, deadline)
                parts.append(part)
                words += count
                chars += len(part) + 1
                # A stop inside a form leaves the page short, with no error
                # (PR #609 fix round 2, P2). Little text is a refusal.
                if deadline.fired and words < _MIN_PARTIAL_CHARS:
                    raise AttachmentRefused(_TOO_SLOW)
                if deadline.fired or chars >= MAX_EXTRACT_CHARS:
                    break
    except AttachmentRefused:
        raise
    except Exception:  # every parser failure is one clean refusal
        raise AttachmentRefused(_NOT_PDF) from None
    read = len(parts)
    text = "\n".join(parts)
    # The char cap can cut the last page read, so "every page read" is not
    # "every word read" (PR #609 review, P2). Nor is a page the deadline cut.
    return Extracted(
        text=text[:MAX_EXTRACT_CHARS], kind="pdf", unit="page", read=read, total=total,
        stopped=read < total or deadline.fired or len(text) > MAX_EXTRACT_CHARS,
    )
