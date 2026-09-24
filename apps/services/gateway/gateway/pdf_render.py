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
refuses nesting past ``MAX_DEPTH`` and a word past ``MAX_WORD_CHARS``
before layout. Fence: ``tests/unit/test_pdf_render.py``.

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
RENDER_TIMEOUT_S = 20.0

#: The most layouts that run at the same time. Each one is a process.
MAX_CONCURRENT_RENDERS = 4

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
#: The characters MuPDF's line breaker breaks at, measured on pymupdf
#: 1.28.0 (fix round 3). A run of 120,000 characters took under 0.2 s with
#: each of these, and 6 s with U+00A0 (NO-BREAK SPACE) or with Thai, which
#: are therefore NOT here. Python's ``\s`` matches U+00A0, so the round 1
#: check (``\S{2001,}``) let ``"a\u00a0" * 300000`` through to a layout of
#: more than a minute.
#:
#: CJK ideographs, kana, Hangul and the full-width forms break between any
#: two characters. 200,000 of them take 0.4 s, so a Japanese paragraph with
#: no space is prose, not an attack, and it must not count as one word.
_BREAKS = (
    " \t\n\r\f\v"
    "\\-\u00ad"
    "\u2000-\u200b\u2028\u2029\u202f\u205f\u2060\u3000\ufeff"
    "\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef"
    "\U00020000-\U0003ffff"
)
_LONG_WORD = re.compile("[^" + _BREAKS + "]{" + str(MAX_WORD_CHARS + 1) + ",}")


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
    text = _ANY_TAG.sub(" ", _INLINE_TAG.sub("", clean))
    if _LONG_WORD.search(_html.unescape(text)):
        raise PdfRenderError(
            f"The document has a run of more than {MAX_WORD_CHARS} characters "
            "with no space, which cannot be laid out on a page."
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


async def render_pdf(kind: str, source: str) -> bytes:
    """The one way a route makes a PDF: in a child process, with a timeout.

    The size check runs here first, so an oversize body never starts a child.
    The slot is taken with an ``await`` and a short bound, so a busy renderer
    answers 503 at once and holds no thread. Every failure is a
    :class:`PdfRenderError` with an HTTP status.
    """
    import asyncio

    if kind not in ("markdown", "html"):
        raise PdfRenderError(f"A {kind} file cannot become a PDF.", status=415)
    _check_size(source)
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
    "RENDER_TIMEOUT_S",
    "SOURCE_KINDS",
    "PdfRenderError",
    "attachment_disposition",
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
