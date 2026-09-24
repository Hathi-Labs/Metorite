"""The gateway's ONE document-to-PDF seam (WS-27bm S8, spec
``projects_ai_chat.md`` §14).

Two callers, one renderer:

* ``routes/workspace.py`` — ``GET /agent/workspace/{sid}/file?path=x.md&format=pdf``
  turns a Markdown or HTML file the agent wrote into a PDF download.
* ``routes/documents.py`` — ``POST /documents/pdf`` turns the HTML that
  ``lib/reportEmail.ts`` formats from a saved report's render into a PDF.

It lives here, beside ``csv_export.py``, for that file's reason: it belongs to
neither caller, and a security control with two copies is one that does not
get the next fix.

⚠️ **The control is the sanitizer, and it is an ALLOWLIST.** The HTML comes
from a member (a report name, a task title) or from a model (a file it wrote),
so it is untrusted. ``sanitize_html`` re-serialises the input through
``html.parser`` and keeps only the text tags a document needs. Every tag that
can name a resource (``img``, ``link``, ``style``, ``svg``, ``iframe``,
``object``, ``embed``, ``video``, ``audio``, ``source``, ``base``, ``meta``)
is dropped, and so is every attribute except ``href``, ``colspan`` and
``rowspan``. So no ``src``, no ``style`` and no ``url()`` reach the renderer.
The renderer also gets no archive, so it has no place to load a resource from.
Together they make the endpoint useless for SSRF. The fence is
``tests/unit/test_pdf_render.py``, which serves an image on a local port and
proves that the render never asks for it.

⚠️ **It never lays out in the gateway process.** ``render_pdf`` runs the
layout in a child process with a 20 s wall-clock limit. MuPDF is C code:
deep nesting overflowed its native stack and killed the whole gateway, and
one long word held the GIL for minutes (fix round 1). The sanitizer also
refuses nesting past ``MAX_DEPTH``, a word past ``MAX_WORD_CHARS``, and a
run of one repeated character or of break spaces past the same length
(``check_repeated_runs``) before layout. Fence:
``tests/unit/test_pdf_render.py``.

⚠️ **It computes nothing.** It lays out what it is given. The numbers in a
report PDF are the render route's, formatted once by ``reportEmail.ts``.
"""
from __future__ import annotations

import html as _html
import io
import os
import re
import subprocess
import sys
import unicodedata
import weakref
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

#: The largest source a caller may convert, in bytes. A report is a few KB and a
#: written document rarely passes 100 KB. The cap bounds the layout work a
#: single request can ask for.
MAX_SOURCE_BYTES = 1_000_000

#: The most pages one render may produce. A source that cannot be placed (one
#: very wide element) makes the layout loop ask for a page forever. The cap
#: turns that into a refusal.
MAX_PAGES = 300

#: The file extensions the workspace route may convert, and how to read each.
SOURCE_KINDS: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".html": "html",
    ".htm": "html",
}

#: Tags kept, with their content. Everything else is dropped as a tag, and its
#: text is kept unless the tag is in ``_DROP_WITH_CONTENT``.
_ALLOWED_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr", "div", "span",
    "ul", "ol", "li", "dl", "dt", "dd",
    "strong", "b", "em", "i", "u", "s", "del", "sup", "sub", "small",
    "code", "pre", "blockquote",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "a",
})

#: Tags whose TEXT is dropped too. A ``<style>`` body is CSS that can name a
#: URL, and a ``<script>`` body is not document text.
_DROP_WITH_CONTENT = frozenset({
    "script", "style", "head", "title", "svg", "math", "template", "noscript",
    "iframe", "object", "embed", "video", "audio", "canvas", "select",
    "textarea", "button",
})

_VOID_TAGS = frozenset({"br", "hr"})

_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}

#: A link is kept only when it names a web page or a mail address. A PDF
#: reader opens a link only on a click, so a link fetches nothing at render
#: time. ``javascript:``, ``file:`` and ``data:`` are dropped all the same.
_SAFE_HREF = re.compile(r"^(https?://|mailto:|#)", re.IGNORECASE)

#: The layout of every PDF. No colour: a PDF prints, and the theme does not
#: travel into a file.
_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.4; }
h1 { font-size: 18pt; margin: 0 0 8pt 0; }
h2 { font-size: 14pt; margin: 12pt 0 6pt 0; }
h3 { font-size: 12pt; margin: 10pt 0 4pt 0; }
h4, h5, h6 { font-size: 11pt; margin: 8pt 0 4pt 0; }
p { margin: 0 0 6pt 0; }
ul, ol { margin: 0 0 6pt 0; }
code, pre { font-family: monospace; font-size: 9.5pt; }
pre { margin: 0 0 6pt 0; }
blockquote { margin: 0 0 6pt 12pt; }
table { border-collapse: collapse; width: 100%; margin: 0 0 8pt 0; }
th, td { border: 0.5pt solid black; padding: 2pt 4pt; text-align: left; }
"""

#: A4 with a 54 pt (0.75 in) margin on each side.
_MARGIN = 54


#: The deepest element nesting a document may have. MuPDF lays out nested
#: boxes by recursion on its native stack, and about 50,000 nested ``<div>``
#: overflow it and kill the process (fix round 1, P0). A real document nests
#: a list in a table in a quote, which is well under ten.
MAX_DEPTH = 64

#: The longest run of text with no whitespace. MuPDF's line breaker is
#: quadratic in the length of one unbroken word: 200,000 characters took 16 s
#: and 900,000 took more than 120 s (fix round 1, P1). A URL or a hash is a few
#: hundred characters.
MAX_WORD_CHARS = 2000

#: The wall-clock limit on one layout, in seconds. The layout runs in a child
#: process (:func:`render_pdf`), and the parent kills it at this limit.
#:
#: It is the structural backstop behind the word check: a character the
#: check misses costs this many seconds of one slot, never more (fix round
#: 5). Measured on pymupdf 1.28.0, the slowest LEGITIMATE render was a
#: 286-page Markdown table report (6,000 rows, 608 KB): 4.35 s. A 210-page
#: Markdown document with lists and code took 0.52 s, and a 129-page HTML
#: report 0.14 s. 3 x 4.35 s is 13 s. A table past the 300-page cap takes 6 to
#: 9 s to refuse, which also fits.
RENDER_TIMEOUT_S = 13.0

#: The most layouts that run at the same time. Each one is a process.
#: Never more than the CPUs: the production box has two, and a layout is
#: CPU-bound, so a fifth render only makes four slower (fix round 4).
def concurrent_render_slots(cpu_count: int | None = None) -> int:
    """``min(4, CPUs)``, at least 1. ``cpu_count`` defaults to the host's."""
    cpus = os.cpu_count() if cpu_count is None else cpu_count
    return max(1, min(4, cpus or 1))


MAX_CONCURRENT_RENDERS = concurrent_render_slots()

#: Tags a new tag of the same family closes, as a browser does: ``<li>`` after
#: an open ``<li>`` is a sibling, not a child. Without this, hand-written HTML
#: with unclosed list items would count as deep nesting.
_IMPLIED_CLOSE: dict[str, frozenset[str]] = {
    "p": frozenset({"p"}),
    "li": frozenset({"li"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
    "tr": frozenset({"tr", "td", "th"}),
    "td": frozenset({"td", "th"}),
    "th": frozenset({"td", "th"}),
}

#: An implied close never reaches past one of these.
_CLOSE_SCOPE = frozenset({
    "ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "blockquote", "div",
})

#: Inline tags do not break a word. Every other tag does.
_INLINE_TAG = re.compile(
    r"</?(?:a|b|strong|em|i|u|s|del|sup|sub|small|code|span)\b[^>]*>"
)
_ANY_TAG = re.compile(r"<[^>]*>")
#: What MuPDF's line breaker breaks at, measured on pymupdf 1.28.0: a run of
#: one character, repeated, took under 0.2 s for 120,000 characters where
#: MuPDF breaks and 2 to 10 s where it does not.
#:
#: ⚠️ **The default is "does NOT break"** (fix round 5). A character counts
#: as a break ONLY IF it is ASSIGNED (``unicodedata.category`` is not ``Cn``)
#: AND it is in the measured set below. Everything else counts toward a word
#: run: unassigned code points, private use, surrogates, noncharacters, and
#: any character nobody measured. Rounds 3 and 4 listed wide ranges and
#: removed exceptions, and each review found more exceptions inside the
#: ranges: U+00A0, Thai, CJK punctuation, small kana, then U+3000 in a run and
#: the unassigned U+3040, U+3097, U+3098 and U+D7A4-U+D7AF. A missed character
#: now costs at most RENDER_TIMEOUT_S, and the fence is
#: ``tests/unit/test_pdf_render.py``, which measures nothing but fails if a
#: character known not to break enters the set.
_BREAK_SPACE_CODEPOINTS = (
    0x20, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x2D, 0xAD,
    *range(0x2000, 0x200C),
    0x2028, 0x2029, 0x202F, 0x205F, 0x2060, 0xFEFF,
)
#: Kana and full-width Latin: every code point measured one by one (round 4).
_CJK_BREAK_RANGES = ((0x3040, 0x30FF), (0xFF10, 0xFF5A))
#: Ideographs, Hangul and the Supplementary Ideographic Plane: measured by
#: sample, one code point every 0x200 to 0x400.
_SAMPLED_BREAK_RANGES = (
    (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0x20000, 0x2FFFF),
)
_CJK_NO_BREAK = frozenset({
    # Small kana, iteration and sound marks, the prolonged sound mark, and
    # the middle dot: measured one by one, fix round 4.
    0x3041, 0x3043, 0x3045, 0x3047, 0x3049, 0x3063, 0x3083, 0x3085, 0x3087,
    0x308E, 0x3095, 0x3096, 0x3099, 0x309A, 0x309B, 0x309C, 0x309D, 0x309E,
    0x30A0, 0x30A1, 0x30A3, 0x30A5, 0x30A7, 0x30A9, 0x30C3, 0x30E3, 0x30E5,
    0x30E7, 0x30EE, 0x30F5, 0x30F6, 0x30FB, 0x30FC, 0x30FD, 0x30FE,
    # Full-width : ; ? [ ]
    0xFF1A, 0xFF1B, 0xFF1F, 0xFF3B, 0xFF3D,
})


def break_codepoints() -> list[int]:
    """Every code point the word check treats as a break, sorted."""
    candidates = list(_BREAK_SPACE_CODEPOINTS)
    for lo, hi in (*_CJK_BREAK_RANGES, *_SAMPLED_BREAK_RANGES):
        candidates.extend(range(lo, hi + 1))
    return sorted({
        cp
        for cp in candidates
        if cp not in _CJK_NO_BREAK and unicodedata.category(chr(cp)) != "Cn"
    })


def _class_ranges(codepoints: list[int]) -> str:
    """Sorted code points as a regex class body: one escaped span per run."""
    out: list[str] = []
    run_start = prev = None
    for cp in codepoints:
        if prev is not None and cp == prev + 1:
            prev = cp
            continue
        if run_start is not None:
            out.append(f"\\U{run_start:08x}-\\U{prev:08x}")
        run_start = prev = cp
    if run_start is not None:
        out.append(f"\\U{run_start:08x}-\\U{prev:08x}")
    return "".join(out)


_BREAKS = _class_ranges(break_codepoints())
_LONG_WORD = re.compile("[^" + _BREAKS + "]{" + str(MAX_WORD_CHARS + 1) + ",}")

#: A run of break characters is slow too (follow-up to fix round 5). MuPDF
#: breaks at each one, and still lays out a long run of them as the square
#: of its length. Measured on pymupdf 1.28.0, 300,000 characters: ``-`` took
#: 38.8 s, U+2003 56.8 s, U+202F 81.9 s, U+2060 and U+FEFF about 37 s, and
#: 300,000 tabs in a ``<pre>`` 22.3 s. An ALTERNATING pair of spaces
#: (U+2003 U+2002) was just as slow, so one repeated character is not the
#: whole rule. Two checks cover it.
#:
#: 1. :data:`_REPEATED` refuses more than :data:`MAX_WORD_CHARS` copies of
#:    one character, whatever its class.
#: 2. :data:`_SPACE_RUN` refuses more than :data:`MAX_WORD_CHARS` characters
#:    from :data:`_BREAK_SPACE_CODEPOINTS` in any mix. A line feed and a
#:    carriage return are left out: in a ``<pre>`` they end the line, and
#:    300,000 of them stop at the page cap in under 1 s.
#:
#: Outside a ``<pre>``, HTML collapses a run of ASCII whitespace to one
#: space, and MuPDF does too: 300,000 spaces, tabs, line feeds or carriage
#: returns in a ``<p>`` took 0.01 s. So the checks collapse that run first,
#: and only a ``<pre>`` keeps it.
#:
#: Three more rules came from the second review of this follow-up.
#:
#: 3. **A combining mark does not end a run.** U+2003 U+0301 repeated
#:    (300,000 characters) took more than 45 s, because each mark split the
#:    run for both patterns. So the checks strip every ``Mn`` and ``Me``
#:    mark first (:func:`_strip_marks`).
#: 4. **MuPDF does NOT collapse a form feed**, though HTML calls it
#:    whitespace. 300,000 of them in a ``<p>`` took more than 45 s. So
#:    :data:`_HTML_SPACE` leaves ``\f`` out, and it counts toward a run.
#: 5. **MuPDF does not wrap a line inside ``<pre>``.** ``\ta`` repeated took
#:    29.5 s and ``x `` repeated 10.6 s (300,000 characters each). So a
#:    ``<pre>`` line longer than :data:`MAX_WORD_CHARS` is refused, whatever
#:    it holds. A line feed, a carriage return and a CRLF each end a line:
#:    ``x\r`` repeated in a ``<pre>`` filled the 300 pages in 0.36 s.
_REPEATED = re.compile(r"(.)\1{" + str(MAX_WORD_CHARS) + ",}", re.DOTALL)
_SPACE_RUN = re.compile(
    "["
    + _class_ranges(sorted(set(_BREAK_SPACE_CODEPOINTS) - {0x0A, 0x0D}))
    + "]{"
    + str(MAX_WORD_CHARS + 1)
    + ",}"
)
#: The whitespace that a browser AND MuPDF collapse outside ``<pre>``. Not
#: ``\f``: see rule 4 above.
_HTML_SPACE = re.compile(r"[ \t\n\r]+")
_TAG_SPLIT = re.compile(r"(<[^>]*>)")
_PRE_LINE_END = re.compile(r"\r\n|\r|\n")
_MARK_CATEGORIES = frozenset({"Mn", "Me"})


def _strip_marks(text: str) -> str:
    """``text`` without its nonspacing and enclosing marks (rule 3).

    It reads the category of each DISTINCT character, not of each character,
    so a 1 MB part costs one ``set`` and one regex pass.
    """
    marks = [c for c in set(text) if unicodedata.category(c) in _MARK_CATEGORIES]
    if not marks:
        return text
    return re.sub("[" + _class_ranges(sorted(map(ord, marks))) + "]", "", text)


class PdfRenderError(ValueError):
    """The source cannot become a PDF. ``status`` is the HTTP answer.

    413 means too large, 422 means the content cannot be laid out, and 503
    means the renderer did not answer in time. The message is for a person.
    """

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


class _Sanitizer(HTMLParser):
    """Re-serialise HTML through an allowlist. See the module docstring.

    It also keeps the output well formed. It tracks the open elements, closes
    the ones a new sibling implies, and closes what is still open at the end.
    So :data:`MAX_DEPTH` measures the nesting that MuPDF will see.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0  # depth inside a _DROP_WITH_CONTENT tag
        self._open: list[str] = []

    def _close_to(self, index: int) -> None:
        while len(self._open) > index:
            self.out.append(f"</{self._open.pop()}>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_WITH_CONTENT:
            self._skip += 1
            return
        if self._skip or tag not in _ALLOWED_TAGS:
            return
        if tag in _VOID_TAGS:
            self.out.append(f"<{tag}>")
            return
        family = _IMPLIED_CLOSE.get(tag)
        if family:
            for i in range(len(self._open) - 1, -1, -1):
                if self._open[i] in _CLOSE_SCOPE:
                    break
                if self._open[i] in family:
                    self._close_to(i)
                    break
        if len(self._open) >= MAX_DEPTH:
            raise PdfRenderError(
                f"The document nests elements more than {MAX_DEPTH} deep."
            )
        kept: list[str] = []
        allowed = _ALLOWED_ATTRS.get(tag, frozenset())
        for name, value in attrs:
            if name not in allowed or value is None:
                continue
            if name == "href" and not _SAFE_HREF.match(value.strip()):
                continue
            if name in ("colspan", "rowspan") and not value.isdigit():
                continue
            kept.append(f' {name}="{_html.escape(value, quote=True)}"')
        self._open.append(tag)
        self.out.append(f"<{tag}{''.join(kept)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS and not self._skip:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_WITH_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        # A close for an element that is not open is dropped. A close for one
        # that is open closes everything opened inside it too.
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i] == tag:
                self._close_to(i)
                return

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(_html.escape(data, quote=False))

    def finish(self) -> str:
        self.close()
        self._close_to(0)
        return "".join(self.out)


def sanitize_html(source: str) -> str:
    """Keep the text tags of a document and drop everything that can load.

    Raises :class:`PdfRenderError` (422) past :data:`MAX_DEPTH`.
    """
    parser = _Sanitizer()
    parser.feed(source)
    return parser.finish()


def check_word_lengths(clean: str) -> None:
    """Refuse a run longer than :data:`MAX_WORD_CHARS` that MuPDF cannot break.

    It reads the SANITIZED HTML, which is what MuPDF lays out. An inline tag
    joins the text on both sides into one word, and any other tag breaks it.
    """
    joined = _INLINE_TAG.sub("", clean)
    text = _ANY_TAG.sub(" ", joined)
    if _LONG_WORD.search(_html.unescape(text)):
        raise PdfRenderError(
            f"The document has a run of more than {MAX_WORD_CHARS} characters "
            "with no space, which cannot be laid out on a page."
        )
    check_repeated_runs(joined)


def check_repeated_runs(joined: str) -> None:
    """Refuse a long run of one character, or of break spaces in any mix.

    ``joined`` is the sanitized HTML with its inline tags removed. Every other
    tag ends a run. See :data:`_REPEATED` for the measurements.
    """
    pre = 0
    for part in _TAG_SPLIT.split(joined):
        if part.startswith("<"):
            # The sanitizer writes `<pre>` and `</pre>` with no attributes.
            if part == "<pre>":
                pre += 1
            elif part == "</pre>":
                pre = max(0, pre - 1)
            continue
        # Unescaping and collapsing only make a part shorter. A part this
        # short cannot hold a run past the limit, so skip the work.
        if len(part) <= MAX_WORD_CHARS:
            continue
        text = _html.unescape(part)
        if pre:
            if any(len(line) > MAX_WORD_CHARS for line in _PRE_LINE_END.split(text)):
                raise PdfRenderError(
                    f"A code block has a line longer than {MAX_WORD_CHARS} "
                    "characters, which cannot be laid out on a page."
                )
        else:
            text = _HTML_SPACE.sub(" ", text)
        text = _strip_marks(text)
        if _REPEATED.search(text) or _SPACE_RUN.search(text):
            raise PdfRenderError(
                "The document repeats one character, or a space, more than "
                f"{MAX_WORD_CHARS} times in a row, which cannot be laid out "
                "on a page."
            )


def markdown_to_html(source: str) -> str:
    """CommonMark plus tables and strikethrough, with raw HTML OFF.

    ``html: False`` makes a ``<script>`` in the Markdown print as text. The
    output still passes through :func:`sanitize_html` in :func:`html_to_pdf`,
    because a Markdown image becomes an ``<img>`` and must be dropped too.
    The depth and word bounds therefore bind Markdown as well.
    """
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.enable(["table", "strikethrough"])
    return md.render(source)


def _check_size(source: str) -> None:
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise PdfRenderError(
            f"The document is larger than {MAX_SOURCE_BYTES} bytes, the most "
            "a PDF render accepts.",
            status=413,
        )


def html_to_pdf(source: str) -> bytes:
    """Lay out untrusted HTML as an A4 PDF and return the file's bytes.

    Pure: no network, no file system, no clock. ⚠️ **In process, it is the
    worker's function, never a route's.** A route calls :func:`render_pdf`,
    which runs this in a child process with a timeout.
    """
    _check_size(source)
    return _layout(source)


def _layout(source: str) -> bytes:
    """Sanitize, check the bounds, then lay out. The size check is the caller's."""
    clean = sanitize_html(source)
    check_word_lengths(clean)
    import fitz  # pymupdf, a gateway dependency since the résumé parser

    try:
        # ⚠️ No `archive`: the renderer has nowhere to load an image, a font
        # or a stylesheet from, whatever the sanitizer misses.
        story = fitz.Story(html=f"<body>{clean}</body>", user_css=_CSS)
        buf = io.BytesIO()
        writer = fitz.DocumentWriter(buf)
        mediabox = fitz.paper_rect("a4")
        where = fitz.Rect(
            _MARGIN, _MARGIN, mediabox.width - _MARGIN, mediabox.height - _MARGIN
        )
        pages = 0
        more = True
        while more:
            pages += 1
            if pages > MAX_PAGES:
                writer.close()
                raise PdfRenderError(
                    f"The document runs past {MAX_PAGES} pages.", status=413
                )
            device = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
        writer.close()
        return buf.getvalue()
    except PdfRenderError:
        raise
    except Exception as exc:
        # MuPDF raises its own classes (FzErrorSyntax and others). Each one
        # is a document the renderer could not read, never a server fault.
        raise PdfRenderError(
            "The document could not be laid out as a PDF."
        ) from exc


def markdown_to_pdf(source: str) -> bytes:
    """A Markdown source as a PDF, through the one HTML path."""
    _check_size(source)
    # The cap reads the Markdown the caller sent. Its HTML is longer (a table
    # grows several times over), and a second check on it would refuse a file
    # the cap admits.
    return _layout(markdown_to_html(source))


def source_to_pdf(kind: str, source: str) -> bytes:
    """Dispatch on a :data:`SOURCE_KINDS` value."""
    if kind == "markdown":
        return markdown_to_pdf(source)
    if kind == "html":
        return html_to_pdf(source)
    raise PdfRenderError(f"A {kind} file cannot become a PDF.", status=415)


# ── Out of process ───────────────────────────────────────────────────────────
#
# ⚠️ MuPDF is C. A crash in it is a crash of the process that called it, and
# a slow layout holds the GIL. So a route never lays out in the gateway
# process: `render_pdf` starts this module as a child, hands it the source on
# stdin, and reads the PDF from stdout. The parent kills a child that runs past
# RENDER_TIMEOUT_S. A child that dies is a refusal, not an outage.

_EXIT_REFUSED = 2

#: How long a render waits for a free slot before it answers 503. The wait is
#: an ``await``: it holds no thread, so a flood of renders cannot starve the
#: default executor that the gateway's other 52 ``to_thread`` sites share
#: (fix round 3, P1: 36 queued renders once delayed an unrelated
#: ``to_thread`` by 38 s).
SLOT_WAIT_S = 2.0

#: The only variables the child inherits. The gateway's environment holds
#: database URLs, provider keys and the internal bearer, and a child that
#: parses untrusted HTML must not hold them (fix round 3, hardening).
CHILD_ENV_KEYS = (
    "PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV",
)

# One semaphore per event loop. An asyncio primitive binds to the first loop
# that waits on it, and the tests run many loops.
_slots: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()


def _slot_semaphore() -> Any:
    import asyncio

    loop = asyncio.get_running_loop()
    sem = _slots.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)
        _slots[loop] = sem
    return sem


def _worker_argv(kind: str) -> list[str]:
    """The child's command line. A test replaces it to simulate a crash."""
    return [sys.executable, "-m", "gateway.pdf_render", kind]


def _child_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in CHILD_ENV_KEYS if k in os.environ}
    # The child writes bytes to stdout. No encoding setting is needed, and
    # none is inherited.
    return env


def _outcome(returncode: int | None, stdout: bytes, stderr: bytes) -> bytes:
    if returncode == 0 and stdout.startswith(b"%PDF"):
        return stdout
    if returncode == _EXIT_REFUSED:
        status, _, message = stderr.decode("utf-8", "replace").partition("|")
        if status.isdigit():
            raise PdfRenderError(message.strip(), status=int(status))
    raise PdfRenderError("The document could not be laid out as a PDF.")


def _timed_out(timeout: float) -> PdfRenderError:
    return PdfRenderError(
        f"The PDF took longer than {int(timeout)} seconds to lay out. "
        "Try a shorter document.",
        status=503,
    )


def _run_child_blocking(kind: str, source: str, timeout: float) -> bytes:
    """The fallback for an event loop that cannot spawn a subprocess (the
    Windows selector loop). It runs in a thread, but only once a slot is held,
    so at most MAX_CONCURRENT_RENDERS threads ever wait here."""
    try:
        done = subprocess.run(  # our own module, no shell
            _worker_argv(kind),
            input=source.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_child_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise _timed_out(timeout) from exc
    except OSError as exc:
        raise PdfRenderError("The PDF renderer could not start. Try again.", status=503) from exc
    return _outcome(done.returncode, done.stdout, done.stderr)


async def _run_child(kind: str, source: str, timeout: float) -> bytes:
    """Lay out in a child process with no pool thread waiting on it.

    The child is killed and reaped on a timeout, and also when the caller is
    cancelled (a client that went away), so no layout outlives its request.
    """
    import asyncio

    try:
        proc = await asyncio.create_subprocess_exec(
            *_worker_argv(kind),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_child_env(),
        )
    except NotImplementedError:
        return await asyncio.to_thread(_run_child_blocking, kind, source, timeout)
    except OSError as exc:
        raise PdfRenderError("The PDF renderer could not start. Try again.", status=503) from exc
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(source.encode("utf-8")), timeout
        )
    except TimeoutError as exc:
        raise _timed_out(timeout) from exc
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    return _outcome(proc.returncode, stdout, stderr)


#: The members with a render in flight in this process. One each (fix round
#: 4): one member must not hold every slot and turn the renderer away for
#: every other tenant. Keyed by the AUTHENTICATED email the route passes,
#: never by anything in the request.
_members_rendering: set[str] = set()

#: The organizations with a render in flight, each mapped to the event its
#: render sets when it ends. One each (fix round 5): with two slots on the
#: box, one tenant with two seats could otherwise hold both. Keyed by the
#: tenant the authenticated request bound (``current_tenant()`` at the
#: route), never by request input. A request with no bound tenant shares one
#: key, ``"unbound"``: that fails toward fairness, not toward a second slot.
_orgs_rendering: dict[str, Any] = {}

#: How long a second member of one organization waits for that
#: organization's render to end, then 429 (follow-up to fix round 5). Two
#: colleagues who download at the same moment must both get a file. A
#: 286-page report takes 5 to 6 s on the 2-CPU box, so 8 s covers it. The
#: wait is an ``await`` on an event and holds no thread.
ORG_WAIT_S = 8.0


def _org_busy() -> PdfRenderError:
    return PdfRenderError(
        "A PDF is already being made for your organization. Try again in a moment.",
        status=429,
    )


async def _take_org_turn(tenant: str) -> None:
    """Wait up to :data:`ORG_WAIT_S` for ``tenant`` to have no render, then
    claim it. The check and the claim run with no ``await`` between them, so
    two waiters cannot both claim."""
    import asyncio

    loop = asyncio.get_running_loop()
    deadline = loop.time() + ORG_WAIT_S
    while tenant in _orgs_rendering:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise _org_busy()
        try:
            await asyncio.wait_for(_orgs_rendering[tenant].wait(), remaining)
        except TimeoutError as exc:
            raise _org_busy() from exc
    _orgs_rendering[tenant] = asyncio.Event()


def _end_org_turn(tenant: str) -> None:
    done = _orgs_rendering.pop(tenant, None)
    if done is not None:
        done.set()


async def render_pdf(
    kind: str, source: str, *, member: str, org: str | None
) -> bytes:
    """The one way a route makes a PDF: in a child process, with a timeout.

    ``member`` is the caller's authenticated email and ``org`` the tenant the
    request bound. A second render for the same member while one runs is
    refused at once with 429. A second render for the same organization
    waits up to :data:`ORG_WAIT_S` for the first to end, then gets 429. The
    size check runs first, so an oversize body never starts a child. Each
    wait is an ``await`` with a bound, so a busy renderer answers 503 and
    holds no thread. Every failure is a :class:`PdfRenderError` with an HTTP
    status.
    """
    import asyncio

    if kind not in ("markdown", "html"):
        raise PdfRenderError(f"A {kind} file cannot become a PDF.", status=415)
    _check_size(source)
    who = (member or "").strip().lower()
    tenant = str(org) if org else "unbound"
    if who in _members_rendering:
        raise PdfRenderError(
            "A PDF is already being made for you. Wait for it, then try again.",
            status=429,
        )
    _members_rendering.add(who)
    try:
        await _take_org_turn(tenant)
    except BaseException:
        _members_rendering.discard(who)
        raise
    try:
        sem = _slot_semaphore()
        try:
            await asyncio.wait_for(sem.acquire(), SLOT_WAIT_S)
        except TimeoutError as exc:
            raise PdfRenderError(
                "The PDF renderer is busy. Try again in a moment.", status=503
            ) from exc
        try:
            return await _run_child(kind, source, RENDER_TIMEOUT_S)
        finally:
            sem.release()
    finally:
        _members_rendering.discard(who)
        _end_org_turn(tenant)


def _worker_main(kind: str) -> int:
    source = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        pdf = source_to_pdf(kind, source)
    except PdfRenderError as exc:
        sys.stderr.write(f"{exc.status}|{exc}")
        return _EXIT_REFUSED
    sys.stdout.buffer.write(pdf)
    sys.stdout.buffer.flush()
    return 0


_UNSAFE_NAME = re.compile(r'[\x00-\x1f\x7f"\\/]')


def pdf_filename(name: str) -> str:
    """``report.md`` → ``report.pdf``. Quotes, slashes and control bytes go."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = _UNSAFE_NAME.sub("_", stem).strip() or "document"
    return f"{stem[:120]}.pdf"


def attachment_disposition(filename: str) -> str:
    """A ``Content-Disposition: attachment`` value that survives any name.

    The plain ``filename`` carries an ASCII copy for old clients. ``filename*``
    carries the real name, UTF-8 and percent-encoded (RFC 6266), so a report
    named in Hindi keeps its name.
    """
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    ascii_name = _UNSAFE_NAME.sub("_", ascii_name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


__all__ = [
    "MAX_DEPTH",
    "MAX_PAGES",
    "MAX_SOURCE_BYTES",
    "MAX_WORD_CHARS",
    "ORG_WAIT_S",
    "RENDER_TIMEOUT_S",
    "SOURCE_KINDS",
    "PdfRenderError",
    "attachment_disposition",
    "check_repeated_runs",
    "check_word_lengths",
    "html_to_pdf",
    "markdown_to_html",
    "markdown_to_pdf",
    "pdf_filename",
    "render_pdf",
    "sanitize_html",
    "source_to_pdf",
]


if __name__ == "__main__":
    raise SystemExit(_worker_main(sys.argv[1] if len(sys.argv) > 1 else ""))
