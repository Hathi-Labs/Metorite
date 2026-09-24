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

⚠️ **It computes nothing.** It lays out what it is given. The numbers in a
report PDF are the render route's, formatted once by ``reportEmail.ts``.
"""
from __future__ import annotations

import html as _html
import io
import re
from html.parser import HTMLParser
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


class PdfRenderError(ValueError):
    """The source cannot become a PDF: too large, too long, or not text."""


class _Sanitizer(HTMLParser):
    """Re-serialise HTML through an allowlist. See the module docstring."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0  # depth inside a _DROP_WITH_CONTENT tag

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_WITH_CONTENT:
            self._skip += 1
            return
        if self._skip or tag not in _ALLOWED_TAGS:
            return
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
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(_html.escape(data, quote=False))


def sanitize_html(source: str) -> str:
    """Keep the text tags of a document and drop everything that can load."""
    parser = _Sanitizer()
    parser.feed(source)
    parser.close()
    return "".join(parser.out)


def markdown_to_html(source: str) -> str:
    """CommonMark plus tables and strikethrough, with raw HTML OFF.

    ``html: False`` makes a ``<script>`` in the Markdown print as text. The
    output still passes through :func:`sanitize_html` in :func:`html_to_pdf`,
    because a Markdown image becomes an ``<img>`` and must be dropped too.
    """
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.enable(["table", "strikethrough"])
    return md.render(source)


def _check_size(source: str) -> None:
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise PdfRenderError(
            f"The document is larger than {MAX_SOURCE_BYTES} bytes, the most "
            "a PDF render accepts."
        )


def html_to_pdf(source: str) -> bytes:
    """Lay out untrusted HTML as an A4 PDF and return the file's bytes.

    Pure: no network, no file system, no clock. The caller runs it in a worker
    thread, because layout is CPU work and the gateway's request path is async.
    """
    _check_size(source)
    return _layout(source)


def _layout(source: str) -> bytes:
    """Sanitize, then lay out. The size check is the caller's, on its input."""
    import fitz  # pymupdf, a gateway dependency since the résumé parser

    clean = sanitize_html(source)
    # ⚠️ No `archive`: the renderer has nowhere to load an image, a font or a
    # stylesheet from, whatever the sanitizer misses.
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
            raise PdfRenderError(f"The document runs past {MAX_PAGES} pages.")
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return buf.getvalue()


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
    raise PdfRenderError(f"A {kind} file cannot become a PDF.")


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
    "MAX_PAGES",
    "MAX_SOURCE_BYTES",
    "SOURCE_KINDS",
    "PdfRenderError",
    "attachment_disposition",
    "html_to_pdf",
    "markdown_to_html",
    "markdown_to_pdf",
    "pdf_filename",
    "sanitize_html",
    "source_to_pdf",
]
