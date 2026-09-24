"""WS-27bm S8 — the one document-to-PDF seam (spec ``projects_ai_chat.md`` §14).

``gateway/pdf_render.py`` lays out untrusted HTML, so these tests pin the
three properties a caller relies on: the bytes are a real PDF, the render
never fetches a resource, and the size and page caps refuse rather than run.
"""
from __future__ import annotations

import http.server
import threading

import pytest
from gateway import pdf_render
from gateway.pdf_render import (
    MAX_SOURCE_BYTES,
    PdfRenderError,
    attachment_disposition,
    html_to_pdf,
    markdown_to_html,
    markdown_to_pdf,
    pdf_filename,
    sanitize_html,
    source_to_pdf,
)


def _text(pdf: bytes) -> str:
    import fitz

    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return "".join(page.get_text() for page in doc)


def test_html_becomes_a_pdf_with_its_text() -> None:
    pdf = html_to_pdf("<h1>Weekly delivery</h1><p>Finished: 12</p>")
    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    assert "Weekly delivery" in text
    assert "Finished: 12" in text


def test_markdown_becomes_a_pdf_with_its_table() -> None:
    pdf = markdown_to_pdf("# Status\n\n| Project | Flag |\n|---|---|\n| Apollo | at risk |\n")
    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    assert "Apollo" in text
    assert "at risk" in text


def test_markdown_raw_html_prints_as_text() -> None:
    """``html: False``: a script in a Markdown file is text, never a tag."""
    out = markdown_to_html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


@pytest.mark.parametrize(
    "fragment",
    [
        '<img src="http://h/x.png">',
        '<link rel="stylesheet" href="http://h/x.css">',
        "<style>@import url(http://h/x.css);</style>",
        '<p style="background:url(http://h/x.png)">t</p>',
        '<iframe src="http://h/"></iframe>',
        '<object data="http://h/x"></object>',
        '<svg><image href="http://h/x.png"/></svg>',
        '<base href="http://h/">',
        '<video src="http://h/v.mp4"></video>',
    ],
)
def test_the_sanitizer_drops_every_resource_reference(fragment: str) -> None:
    out = sanitize_html(fragment)
    assert "http://h" not in out
    assert "src=" not in out
    assert "style" not in out


def test_the_sanitizer_keeps_document_text_and_safe_links() -> None:
    out = sanitize_html(
        '<h2 class="x">A &amp; B</h2><a href="https://example.com/r">r</a>'
        '<a href="javascript:alert(1)">j</a><td colspan="2" onclick="x">c</td>'
    )
    assert "<h2>A &amp; B</h2>" in out
    assert '<a href="https://example.com/r">r</a>' in out
    assert "javascript" not in out
    assert '<td colspan="2">c</td>' in out
    assert "onclick" not in out


class _Counter(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self) -> None:
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return


def test_a_render_never_fetches_an_external_url() -> None:
    """The SSRF fence: a live server on a local port, and not one request."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _Counter)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        html = (
            f'<p>before</p><img src="{base}/a.png">'
            f'<link rel="stylesheet" href="{base}/b.css">'
            f"<style>@import url({base}/c.css); body {{ background: url({base}/d.png) }}</style>"
            f'<p style="background-image:url({base}/e.png)">after</p>'
        )
        pdf = html_to_pdf(html)
        md_pdf = markdown_to_pdf(f"![chart]({base}/f.png)\n\ntext")
    finally:
        server.shutdown()
        server.server_close()
    assert pdf.startswith(b"%PDF")
    assert md_pdf.startswith(b"%PDF")
    assert _Counter.hits == 0


def test_the_size_cap_refuses_before_layout() -> None:
    big = "<p>" + "x" * (MAX_SOURCE_BYTES + 1) + "</p>"
    with pytest.raises(PdfRenderError):
        html_to_pdf(big)
    with pytest.raises(PdfRenderError):
        markdown_to_pdf("x" * (MAX_SOURCE_BYTES + 1))


def test_the_page_cap_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_render, "MAX_PAGES", 2)
    with pytest.raises(PdfRenderError):
        html_to_pdf("<p>line</p>" * 400)


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(PdfRenderError):
        source_to_pdf("docx", "text")


def test_the_filename_is_safe_and_ends_in_pdf() -> None:
    assert pdf_filename("status.md") == "status.pdf"
    assert pdf_filename('we"ird/na\\me.html') == "we_ird_na_me.pdf"
    assert pdf_filename("") == "document.pdf"
    header = attachment_disposition("रिपोर्ट.pdf")
    assert header.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in header
    assert header.count('"') == 2
