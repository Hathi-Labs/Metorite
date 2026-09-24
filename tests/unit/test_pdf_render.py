"""WS-27bm S8 — the one document-to-PDF seam (spec ``projects_ai_chat.md`` §14).

``gateway/pdf_render.py`` lays out untrusted HTML, so these tests pin the
three properties a caller relies on: the bytes are a real PDF, the render
never fetches a resource, and the size and page caps refuse rather than run.
"""
from __future__ import annotations

import asyncio
import http.server
import sys
import threading
import time

import pytest
from gateway import pdf_render
from gateway.pdf_render import (
    MAX_DEPTH,
    MAX_SOURCE_BYTES,
    MAX_WORD_CHARS,
    PdfRenderError,
    attachment_disposition,
    check_word_lengths,
    html_to_pdf,
    markdown_to_html,
    markdown_to_pdf,
    pdf_filename,
    render_pdf,
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


# ── Fix round 1 — MuPDF must never take the gateway down ────────────────────
#
# Every test below is bounded. The hostile inputs never reach MuPDF in this
# process: they either stop in the pure-Python sanitizer, or they run through
# `render_pdf`, whose child process the parent kills at a timeout.

def test_p0_deep_nesting_is_refused_before_layout() -> None:
    """199,000 nested divs killed the gateway (reviewer P0). The sanitizer
    stops at MAX_DEPTH, in Python, so MuPDF never sees them."""
    with pytest.raises(PdfRenderError) as err:
        sanitize_html("<div>" * 199_000 + "x")
    assert err.value.status == 422
    ok = sanitize_html("<div>" * MAX_DEPTH + "x")
    assert ok.count("<div>") == MAX_DEPTH
    assert ok.endswith("</div>" * MAX_DEPTH)


def test_p0_markdown_nesting_is_bounded_too() -> None:
    deep = "*" * 5000 + "x" + "*" * 5000
    with pytest.raises(PdfRenderError):
        sanitize_html(markdown_to_html(deep))


def test_p0_unclosed_siblings_are_not_nesting() -> None:
    """Hand-written HTML leaves `<li>` and `<p>` open. They are siblings, as
    a browser reads them, so they must not count against MAX_DEPTH."""
    out = sanitize_html("<ul>" + "<li>item" * 500 + "</ul>" + "<p>para" * 500)
    assert out.count("<li>") == 500
    assert out.count("</li>") == 500
    assert out.count("</p>") == 500


def test_p0_deep_nesting_through_render_pdf_is_a_4xx() -> None:
    start = time.monotonic()
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", "<div>" * 199_000 + "x"))
    assert err.value.status == 422
    assert time.monotonic() - start < 30


def test_p0_a_child_that_crashes_is_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A MuPDF crash kills the child, never the caller."""
    monkeypatch.setattr(
        pdf_render, "_worker_argv",
        lambda kind: [sys.executable, "-c", "import os; os.abort()"],
    )
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", "<p>x</p>"))
    assert err.value.status == 422
    assert "could not be laid out" in str(err.value)


def test_p0_p1_a_child_that_hangs_is_killed_at_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pdf_render, "_worker_argv",
        lambda kind: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    monkeypatch.setattr(pdf_render, "RENDER_TIMEOUT_S", 1.0)
    start = time.monotonic()
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", "<p>x</p>"))
    assert err.value.status == 503
    assert time.monotonic() - start < 15


def test_p1_a_long_unbroken_run_is_refused() -> None:
    """900,000 characters with no space took more than 120 s (reviewer P1)."""
    check_word_lengths("<p>" + "x" * MAX_WORD_CHARS + "</p>")
    with pytest.raises(PdfRenderError) as err:
        check_word_lengths("<p>" + "x" * (MAX_WORD_CHARS + 1) + "</p>")
    assert err.value.status == 422
    half = "x" * (MAX_WORD_CHARS // 2 + 1)
    # An inline tag joins a word, and a block tag breaks it.
    with pytest.raises(PdfRenderError):
        check_word_lengths(f"<p>{half}<b>{half}</b></p>")
    check_word_lengths(f"<p>{half}</p><p>{half}</p>")


def test_p1_a_long_run_through_render_pdf_is_a_fast_4xx() -> None:
    start = time.monotonic()
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", "x" * 900_000))
    assert err.value.status == 422
    assert time.monotonic() - start < 30


def test_p2_a_mupdf_error_is_a_422(monkeypatch: pytest.MonkeyPatch) -> None:
    import fitz

    class FzErrorSyntax(Exception):
        pass

    def _boom(*_a: object, **_k: object) -> None:
        raise FzErrorSyntax("syntax error in html")

    monkeypatch.setattr(fitz, "Story", _boom)
    with pytest.raises(PdfRenderError) as err:
        html_to_pdf("<p>x</p>")
    assert err.value.status == 422


def test_render_pdf_returns_a_pdf_from_the_child() -> None:
    pdf = asyncio.run(render_pdf("markdown", "# Title\n\nText."))
    assert pdf.startswith(b"%PDF")
    assert "Title" in _text(pdf)


def test_render_pdf_refuses_before_starting_a_child(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_child(*_a: object, **_k: object) -> bytes:
        raise AssertionError("a child started")

    monkeypatch.setattr(pdf_render, "_run_child", _no_child)
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", "x" * (MAX_SOURCE_BYTES + 1)))
    assert err.value.status == 413
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("docx", "x"))
    assert err.value.status == 415


def test_devanagari_draws_in_a_devanagari_font() -> None:
    """Verifier finding 4. The glyphs draw shaped and correct in MuPDF's
    built-in Noto Serif Devanagari (checked by eye on a rendered page). Text
    EXTRACTION from the PDF is garbled for conjuncts, which is a known limit
    recorded in spec §14.6 and not asserted here."""
    import fitz

    with fitz.open(stream=html_to_pdf("<p>नमस्ते दुनिया</p>"), filetype="pdf") as doc:
        fonts = [f[3] for f in doc[0].get_fonts()]
    assert any("Devanagari" in name for name in fonts), fonts


# ── Fix round 3 ─────────────────────────────────────────────────────────────


def _slow_child(seconds: float):
    return lambda kind: [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_r3_a_flood_holds_no_pool_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1: a queued render waited on a thread-pool thread, so 36 slow renders
    delayed an unrelated ``to_thread`` by 38 s. The slot is an ``await`` now,
    so the probe runs at once, and a render with no slot answers 503 busy."""
    monkeypatch.setattr(pdf_render, "_worker_argv", _slow_child(3))
    monkeypatch.setattr(pdf_render, "SLOT_WAIT_S", 0.5)
    monkeypatch.setattr(pdf_render, "RENDER_TIMEOUT_S", 8.0)

    async def scenario() -> tuple[float, list[int]]:
        renders = [
            asyncio.ensure_future(render_pdf("html", "<p>x</p>")) for _ in range(40)
        ]
        await asyncio.sleep(0.2)
        start = time.monotonic()
        await asyncio.to_thread(lambda: None)
        probe = time.monotonic() - start
        done = await asyncio.gather(*renders, return_exceptions=True)
        return probe, [getattr(r, "status", 0) for r in done]

    started = time.monotonic()
    probe, statuses = asyncio.run(scenario())
    assert probe < 1.0, probe
    assert statuses.count(503) >= 30, statuses
    assert time.monotonic() - started < 20


def test_r3_a_cancelled_render_kills_its_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client that goes away takes its layout with it."""
    monkeypatch.setattr(pdf_render, "_worker_argv", _slow_child(30))
    spawned: list = []
    real = asyncio.create_subprocess_exec

    async def recording(*args, **kwargs):
        proc = await real(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording)

    async def scenario() -> None:
        task = asyncio.ensure_future(render_pdf("html", "<p>x</p>"))
        await asyncio.sleep(1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    start = time.monotonic()
    asyncio.run(scenario())
    assert spawned and spawned[0].returncode is not None
    assert time.monotonic() - start < 10


def test_r3_no_break_spaces_count_as_one_word() -> None:
    """P1: Python's whitespace class matches U+00A0, and MuPDF does not break there."""
    for body in ("<p>" + "a\u00a0" * 300_000 + "</p>", "<p>" + "a&nbsp;" * 140_000 + "</p>"):
        clean = sanitize_html(body)
        with pytest.raises(PdfRenderError) as err:
            check_word_lengths(clean)
        assert err.value.status == 422


@pytest.mark.parametrize(
    "body",
    ["a\u00a0" * 300_000, "<p>" + "a&nbsp;" * 140_000 + "</p>"],
    ids=["nbsp-chars", "nbsp-entities"],
)
def test_r3_the_nbsp_attacks_are_a_fast_422(body: str) -> None:
    start = time.monotonic()
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", body))
    assert err.value.status == 422
    assert time.monotonic() - start < 10


def test_r3_a_long_japanese_paragraph_renders() -> None:
    """P2: CJK breaks between any two characters, so 2,480 characters with no
    space is prose. Round 1 refused it."""
    sentence = "日本語の段落はスペースを使わずに書かれることが多いです。"
    text = sentence * (2480 // len(sentence) + 1)
    check_word_lengths(f"<p>{text}</p>")
    pdf = asyncio.run(render_pdf("html", f"<p>{text}</p>"))
    assert pdf.startswith(b"%PDF")


def test_r3_characters_mupdf_breaks_at_do_not_join_a_word() -> None:
    long_run = "​".join(["x" * 1500] * 3)
    check_word_lengths(f"<p>{long_run}</p>")
    with pytest.raises(PdfRenderError):
        check_word_lengths("<p>" + "ภ" * 2001 + "</p>")  # Thai: measured, no break


def test_r3_the_child_gets_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hardening: the child parses untrusted HTML, so it inherits only
    CHILD_ENV_KEYS, never the gateway's keys."""
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "s3cret-token-value")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setattr(
        pdf_render,
        "_worker_argv",
        lambda kind: [
            sys.executable,
            "-c",
            "import os,sys; sys.stdout.buffer.write(b'%PDF ' + ' '.join(sorted(os.environ)).encode())",
        ],
    )
    out = asyncio.run(render_pdf("html", "<p>x</p>")).decode()
    names = set(out.split()[1:])
    assert "GATEWAY_INTERNAL_TOKEN" not in names
    assert "DATABASE_URL" not in names
    assert "s3cret" not in out
    assert names <= set(pdf_render.CHILD_ENV_KEYS) | {"__CF_USER_TEXT_ENCODING"}
