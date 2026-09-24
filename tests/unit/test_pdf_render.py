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

MEMBER = "a@fracktal.in"
ORG = "org-test"


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
        asyncio.run(render_pdf("html", "<div>" * 199_000 + "x", member=MEMBER, org=ORG))
    assert err.value.status == 422
    assert time.monotonic() - start < 30


def test_p0_a_child_that_crashes_is_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A MuPDF crash kills the child, never the caller."""
    monkeypatch.setattr(
        pdf_render, "_worker_argv",
        lambda kind: [sys.executable, "-c", "import os; os.abort()"],
    )
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", "<p>x</p>", member=MEMBER, org=ORG))
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
        asyncio.run(render_pdf("html", "<p>x</p>", member=MEMBER, org=ORG))
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
        asyncio.run(render_pdf("html", "x" * 900_000, member=MEMBER, org=ORG))
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
    pdf = asyncio.run(render_pdf("markdown", "# Title\n\nText.", member=MEMBER, org=ORG))
    assert pdf.startswith(b"%PDF")
    assert "Title" in _text(pdf)


def test_render_pdf_refuses_before_starting_a_child(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_child(*_a: object, **_k: object) -> bytes:
        raise AssertionError("a child started")

    monkeypatch.setattr(pdf_render, "_run_child", _no_child)
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", "x" * (MAX_SOURCE_BYTES + 1), member=MEMBER, org=ORG))
    assert err.value.status == 413
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("docx", "x", member=MEMBER, org=ORG))
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
            asyncio.ensure_future(render_pdf("html", "<p>x</p>", member=f"m{i}@x.test", org=f"o{i}"))
            for i in range(40)
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
        task = asyncio.ensure_future(render_pdf("html", "<p>x</p>", member=MEMBER, org=ORG))
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
        asyncio.run(render_pdf("html", body, member=MEMBER, org=ORG))
    assert err.value.status == 422
    assert time.monotonic() - start < 10


def test_r3_a_long_japanese_paragraph_renders() -> None:
    """P2: CJK breaks between any two characters, so 2,480 characters with no
    space is prose. Round 1 refused it."""
    sentence = "日本語の段落はスペースを使わずに書かれることが多いです。"
    text = sentence * (2480 // len(sentence) + 1)
    check_word_lengths(f"<p>{text}</p>")
    pdf = asyncio.run(render_pdf("html", f"<p>{text}</p>", member=MEMBER, org=ORG))
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
    out = asyncio.run(render_pdf("html", "<p>x</p>", member=MEMBER, org=ORG)).decode()
    names = set(out.split()[1:])
    assert "GATEWAY_INTERNAL_TOKEN" not in names
    assert "DATABASE_URL" not in names
    assert "s3cret" not in out
    assert names <= set(pdf_render.CHILD_ENV_KEYS) | {"__CF_USER_TEXT_ENCODING"}


# ── Fix round 4 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ch", ["\u300c", "\u301c", "\u3005"], ids=["kagikakko", "wave-dash", "iteration-mark"])
def test_r4_cjk_punctuation_runs_are_a_fast_422(ch: str) -> None:
    """P1: MuPDF does not break at CJK punctuation. Round 3 counted the whole
    U+2E80-U+9FFF block as breaking, so a run of 「 passed and took 8-10 s per
    120,000 characters."""
    with pytest.raises(PdfRenderError):
        check_word_lengths("<p>" + ch * (MAX_WORD_CHARS + 1) + "</p>")
    start = time.monotonic()
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", ch * 333_000, member=MEMBER, org=ORG))
    assert err.value.status == 422
    assert time.monotonic() - start < 10


def test_r4_the_measured_exceptions_count_toward_a_run() -> None:
    from gateway.pdf_render import _CJK_NO_BREAK

    for cp in sorted(_CJK_NO_BREAK):
        with pytest.raises(PdfRenderError):
            check_word_lengths("<p>" + chr(cp) * (MAX_WORD_CHARS + 1) + "</p>")


def test_r4_a_japanese_paragraph_with_brackets_still_renders() -> None:
    sentence = "「日本語」の段落では、括弧や句読点を使います。"
    text = sentence * (3000 // len(sentence) + 1)
    check_word_lengths(f"<p>{text}</p>")
    pdf = asyncio.run(render_pdf("html", f"<p>{text}</p>", member=MEMBER, org=ORG))
    assert pdf.startswith(b"%PDF")


def test_r4_one_render_per_member_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1 (b): a second concurrent render for one member is refused at once,
    and another member is not held up by it."""
    monkeypatch.setattr(pdf_render, "_worker_argv", _slow_child(2))

    async def scenario() -> tuple[int, float, int]:
        first = asyncio.ensure_future(render_pdf("html", "<p>x</p>", member="Asha@x.test", org="org-1"))
        await asyncio.sleep(0.3)
        start = time.monotonic()
        try:
            await render_pdf("html", "<p>y</p>", member="asha@x.test", org="org-1")
            second = 200
        except PdfRenderError as exc:
            second = exc.status
        refused_in = time.monotonic() - start
        other = asyncio.ensure_future(render_pdf("html", "<p>z</p>", member="ravi@x.test", org="org-2"))
        results = await asyncio.gather(first, other, return_exceptions=True)
        other_status = getattr(results[1], "status", 200)
        return second, refused_in, other_status

    second, refused_in, other_status = asyncio.run(scenario())
    assert second == 429
    assert refused_in < 0.5
    # The slow child prints nothing, so it is a 422 refusal, not a busy 503
    # or a per-member 429.
    assert other_status == 422
    assert not pdf_render._members_rendering


def test_r4_the_slot_count_never_exceeds_the_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """R7 (fix round 5): on a 28-CPU box the old assertion held with
    ``min(cpu)`` deleted. Drive the expression with the CPU count instead."""
    import os

    from gateway.pdf_render import concurrent_render_slots

    assert concurrent_render_slots(2) == 2
    assert concurrent_render_slots(1) == 1
    assert concurrent_render_slots(28) == 4
    assert concurrent_render_slots(0) == 1
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert concurrent_render_slots() == 2
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    assert concurrent_render_slots() == 1
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert concurrent_render_slots() == 1


def test_r4_the_routes_key_the_caps_by_the_authenticated_caller() -> None:
    import inspect

    from gateway.routes import documents, workspace

    doc = inspect.getsource(documents)
    ws = inspect.getsource(workspace)
    assert 'member=user.email, org=current_tenant()' in doc
    assert "member=_user.email" in ws
    assert "org=current_tenant()" in ws


# ── Fix round 5 ─────────────────────────────────────────────────────────────


def _is_refused(ch: str) -> bool:
    try:
        check_word_lengths("<p>" + ch * (MAX_WORD_CHARS + 1) + "</p>")
    except PdfRenderError:
        return True
    return False


#: Characters MuPDF was MEASURED not to break at. Adding any of them back to
#: the break set must turn a test red, because each one costs 2 to 10 s per
#: 120,000 characters.
KNOWN_NO_BREAK = sorted(
    {0x00A0, 0x0E20, 0x3000, 0x3040, 0x3097, 0x3098}
    | set(range(0x3001, 0x3040))
    | set(range(0xD7A4, 0xD7B0))
)


def test_r5_every_known_non_breaking_character_counts_toward_a_run() -> None:
    """B1 (R7): every character in U+3001-U+303F, every `_CJK_NO_BREAK`
    entry and the five groups the round-5 review measured. A single one put
    back in the break set fails here. Cheap: no layout, only the check."""
    from gateway.pdf_render import _CJK_NO_BREAK, break_codepoints

    breaks = set(break_codepoints())
    for cp in [*KNOWN_NO_BREAK, *sorted(_CJK_NO_BREAK)]:
        assert cp not in breaks, f"U+{cp:04X} is in the break set"
        assert _is_refused(chr(cp)), f"U+{cp:04X} passed the word check"


def test_r5_the_break_set_admits_no_unassigned_code_point() -> None:
    import unicodedata

    from gateway.pdf_render import break_codepoints

    for cp in break_codepoints():
        assert unicodedata.category(chr(cp)) != "Cn", f"U+{cp:04X}"


@pytest.mark.parametrize(
    "cp",
    [0x3000, 0x3040, 0x3097, 0x3098, 0xD7A4, 0xD7AF, 0xE000, 0xE0080, 0x10FFFF],
    ids=["ideo-space", "u3040", "u3097", "u3098", "ud7a4", "ud7af", "private-use", "cn-plane-14", "noncharacter"],
)
def test_r5_the_measured_groups_and_unassigned_are_a_fast_422(cp: int) -> None:
    """A1: the five groups inside the old break ranges, and characters no one
    measured, now count toward a word run."""
    assert _is_refused(chr(cp))
    start = time.monotonic()
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", chr(cp) * 200_000, member=MEMBER, org=ORG))
    assert err.value.status == 422
    assert time.monotonic() - start < 10


@pytest.mark.parametrize(
    "sentence",
    [
        "日本語の段落はスペースを使わずに書かれることが多いです。",
        "한국어 문단은 띄어쓰기를 사용하지만 긴 문장도 자주 있습니다.",
        "中文段落通常不使用空格，而是一个字接一个字地书写下去。",  # noqa: RUF001 — real Chinese punctuation
    ],
    ids=["japanese", "korean", "chinese"],
)
def test_r5_normal_cjk_paragraphs_still_render(sentence: str) -> None:
    text = sentence * (3000 // len(sentence) + 1)
    check_word_lengths(f"<p>{text}</p>")
    pdf = asyncio.run(render_pdf("html", f"<p>{text}</p>", member=MEMBER, org=ORG))
    assert pdf.startswith(b"%PDF")


def test_r5_one_render_per_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """A2: two seats of one tenant cannot hold both slots, and a second tenant
    is not blocked by the first. The org wait is cut to 0.3 s here, so the
    second seat's wait runs out while the first child still sleeps."""
    monkeypatch.setattr(pdf_render, "_worker_argv", _slow_child(2))
    monkeypatch.setattr(pdf_render, "ORG_WAIT_S", 0.3)

    async def scenario() -> tuple[int, int, int]:
        first = asyncio.ensure_future(
            render_pdf("html", "<p>x</p>", member="asha@a.test", org="org-a")
        )
        await asyncio.sleep(0.3)
        try:
            await render_pdf("html", "<p>y</p>", member="ravi@a.test", org="org-a")
            same_org = 200
        except PdfRenderError as exc:
            same_org = exc.status
        other = asyncio.ensure_future(
            render_pdf("html", "<p>z</p>", member="bo@b.test", org="org-b")
        )
        results = await asyncio.gather(first, other, return_exceptions=True)
        return same_org, getattr(results[0], "status", 200), getattr(results[1], "status", 200)

    same_org, first_status, other_status = asyncio.run(scenario())
    assert same_org == 429
    # Both slow children print nothing, so each is a 422: they RAN.
    assert first_status == 422
    assert other_status == 422
    assert not pdf_render._orgs_rendering


def test_r5_an_unbound_request_shares_one_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No bound tenant must not mean no org cap."""
    monkeypatch.setattr(pdf_render, "_worker_argv", _slow_child(2))
    monkeypatch.setattr(pdf_render, "ORG_WAIT_S", 0.3)

    async def scenario() -> int:
        first = asyncio.ensure_future(render_pdf("html", "<p>x</p>", member="a@x.test", org=None))
        await asyncio.sleep(0.3)
        try:
            await render_pdf("html", "<p>y</p>", member="b@x.test", org=None)
            status = 200
        except PdfRenderError as exc:
            status = exc.status
        await asyncio.gather(first, return_exceptions=True)
        return status

    assert asyncio.run(scenario()) == 429


def test_r5_the_timeout_is_the_measured_backstop() -> None:
    """A3: 3 x the slowest legitimate render measured (4.35 s).

    The measurement shape, so a change to the limit can be measured again.
    The input is a Markdown table report, one row per task, rendered by
    ``markdown_to_pdf`` with pymupdf 1.28.0.

    * Dev box: 6,000 rows (608 KB), 286 pages, 4.35 s.
    * Production, srv1914284 (2 CPUs), measured by the S8 review:
      6,000 rows (501 KB) in 3.7 to 3.9 s, and 7,300 rows (609 KB) in 5.49 s.
    * The 300-page cap stops a normal document near 7,600 rows, at about 6 s.
    * A 1 MB HTML table is refused with 413 at the page cap, in 8.5 s.

    13 s is above all of them.
    """
    assert pdf_render.RENDER_TIMEOUT_S == 13.0


# ── Follow-up to fix round 5: runs of break characters ──────────────────────


#: Each case took 20 s to 80 s or more to lay out before the run check, on
#: pymupdf 1.28.0. ``_SPACE_RUN`` refuses the alternating pairs, and
#: ``_REPEATED`` refuses every run of one character.
SLOW_RUNS = {
    "hyphen": "-" * 300_000,
    "em-space": "\u2003" * 300_000,
    "narrow-nbsp": "\u202f" * 300_000,
    "word-joiner": "\u2060" * 300_000,
    "bom": "\ufeff" * 300_000,
    "vertical-tab": "\x0b" * 300_000,
    "pre-tabs": "<pre>" + "\t" * 300_000 + "</pre>",
    "pre-spaces": "<pre>" + " " * 300_000 + "</pre>",
    "pre-space-tab": "<pre>" + " \t" * 150_000 + "</pre>",
    "em-en-pair": "\u2003\u2002" * 150_000,
    "entity-em-space": "<p>" + "&#8195;" * 140_000 + "</p>",
    "bold-joined-hyphens": "<p>" + "<b>-</b>" * 120_000 + "</p>",
}


@pytest.mark.parametrize("body", list(SLOW_RUNS.values()), ids=list(SLOW_RUNS))
def test_f1_a_long_run_of_break_characters_is_refused(body: str) -> None:
    """The timer covers the check, not the sanitizer that ran before it.
    Measured at 0.01 to 0.03 s per case on the dev box."""
    clean = sanitize_html(body)
    start = time.monotonic()
    with pytest.raises(PdfRenderError) as err:
        check_word_lengths(clean)
    assert err.value.status == 422
    assert time.monotonic() - start < 1.0


@pytest.mark.parametrize(
    "body",
    [SLOW_RUNS["hyphen"], SLOW_RUNS["em-en-pair"], SLOW_RUNS["pre-tabs"]],
    ids=["hyphen", "em-en-pair", "pre-tabs"],
)
def test_f1_the_slow_runs_are_a_fast_422_through_render_pdf(body: str) -> None:
    """The child refuses before it imports MuPDF. Measured at 0.3 to 0.5 s
    on the dev box. The bound leaves room for a slow CI runner."""
    start = time.monotonic()
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("html", body, member=MEMBER, org=ORG))
    assert err.value.status == 422
    assert time.monotonic() - start < 5.0


def test_f1_one_repeated_character_is_refused_whatever_its_class() -> None:
    """Kana and ideographs break, so the word check passes them. The repeat
    check does not. The limit is exact: 2,000 copies pass and 2,001 fail."""
    for ch in ("\u3042", "\u65e5", "-"):
        check_word_lengths(sanitize_html("<p>" + ch * MAX_WORD_CHARS + "</p>"))
        with pytest.raises(PdfRenderError):
            check_word_lengths(sanitize_html("<p>" + ch * (MAX_WORD_CHARS + 1) + "</p>"))
    with pytest.raises(PdfRenderError):
        check_word_lengths(sanitize_html("<pre>" + "\n" * 5000 + "</pre>"))


def test_f1_a_block_tag_ends_a_run() -> None:
    half = "-" * (MAX_WORD_CHARS // 2 + 1)
    check_word_lengths(sanitize_html(f"<p>{half}</p><p>{half}</p>"))
    check_word_lengths(sanitize_html("<p></p>" * 140_000))
    with pytest.raises(PdfRenderError):
        check_word_lengths(sanitize_html(f"<p>{half}<b>{half}</b></p>"))


@pytest.mark.parametrize(
    "body",
    [
        "<p>a" + " " * 300_000 + "b</p>",
        "<p>a" + "\t" * 300_000 + "b</p>",
        "<p>a" + "\n" * 300_000 + "b</p>",
        "<p><code>" + "\t" * 300_000 + "</code></p>",
    ],
    ids=["p-spaces", "p-tabs", "p-newlines", "code-tabs"],
)
def test_f1_ascii_whitespace_outside_pre_collapses_and_passes(body: str) -> None:
    """HTML collapses these, and MuPDF laid each out in 0.01 s. So the check
    does not refuse them."""
    check_word_lengths(sanitize_html(body))


def test_f1_a_normal_report_with_code_and_ascii_art_renders() -> None:
    art = "\n".join([
        "+--------+      +---------+",
        "| client | ---> | gateway |",
        "+--------+      +---------+",
        "     |                |",
        "     v                v",
        "  [ cache ]      [ postgres ]",
    ])
    rows = "\n".join(f"| T-{i} | Task {i} | asha@x.test | open |" for i in range(200))
    source = (
        "# Weekly report\n\n"
        "Status of the delivery team. See the diagram below.\n\n"
        f"```\n{art}\n```\n\n"
        "```python\ndef total(rows):\n\treturn sum(r.hours for r in rows)\n```\n\n"
        "    indented code block\n    " + " " * 60 + "with a wide gap\n\n"
        "---\n\n"
        "| Id | Title | Owner | Status |\n|---|---|---|---|\n" + rows + "\n\n"
        + "日本語の段落はスペースを使わずに書かれることが多いです。" * 60 + "\n"
    )
    check_word_lengths(sanitize_html(markdown_to_html(source)))
    pdf = asyncio.run(render_pdf("markdown", source, member=MEMBER, org=ORG))
    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    assert "gateway" in text
    assert "T-199" in text


# ── Follow-up to fix round 5: two colleagues download at once ───────────────


def _fake_child(duration: float, starts: list[tuple[str, float]]):
    """An in-process stand-in for the child: it records when a render
    started, sleeps, and returns a PDF. No process, so the timing is exact."""

    async def run(kind: str, source: str, timeout: float) -> bytes:
        starts.append((source, time.monotonic()))
        await asyncio.sleep(duration)
        return b"%PDF-fake " + source.encode()

    return run


def test_f2_a_colleague_waits_and_then_gets_a_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[tuple[str, float]] = []
    monkeypatch.setattr(pdf_render, "_run_child", _fake_child(1.0, starts))
    monkeypatch.setattr(pdf_render, "MAX_CONCURRENT_RENDERS", 4)

    async def scenario() -> list[bytes]:
        first = asyncio.ensure_future(render_pdf("html", "first", member="asha@a.test", org="org-a"))
        await asyncio.sleep(0.2)
        second = asyncio.ensure_future(render_pdf("html", "second", member="ravi@a.test", org="org-a"))
        return await asyncio.gather(first, second)

    began = time.monotonic()
    first, second = asyncio.run(scenario())
    assert first.startswith(b"%PDF") and second.startswith(b"%PDF")
    order = dict(starts)
    # The second render started only after the first one ended.
    assert order["second"] - order["first"] >= 0.95
    assert time.monotonic() - began < 5
    assert not pdf_render._orgs_rendering
    assert not pdf_render._members_rendering


def test_f2_the_wait_runs_out_to_429(monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[tuple[str, float]] = []
    monkeypatch.setattr(pdf_render, "_run_child", _fake_child(3.0, starts))
    monkeypatch.setattr(pdf_render, "ORG_WAIT_S", 0.5)

    async def scenario() -> tuple[int, float, str]:
        first = asyncio.ensure_future(render_pdf("html", "first", member="asha@a.test", org="org-a"))
        await asyncio.sleep(0.2)
        start = time.monotonic()
        try:
            await render_pdf("html", "second", member="ravi@a.test", org="org-a")
            status, message = 200, ""
        except PdfRenderError as exc:
            status, message = exc.status, str(exc)
        waited = time.monotonic() - start
        await first
        return status, waited, message

    status, waited, message = asyncio.run(scenario())
    assert status == 429
    assert 0.4 <= waited < 1.5
    assert message == "A PDF is already being made for your organization. Try again in a moment."
    assert [s for s, _ in starts] == ["first"]
    # The member who gave up can ask again.
    assert not pdf_render._members_rendering
    assert not pdf_render._orgs_rendering


def test_f2_the_same_member_still_gets_429_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A double click is not a colleague. It must not wait."""
    starts: list[tuple[str, float]] = []
    monkeypatch.setattr(pdf_render, "_run_child", _fake_child(2.0, starts))

    async def scenario() -> tuple[int, float]:
        first = asyncio.ensure_future(render_pdf("html", "first", member="asha@a.test", org="org-a"))
        await asyncio.sleep(0.2)
        start = time.monotonic()
        try:
            await render_pdf("html", "again", member="Asha@a.test", org="org-a")
            status = 200
        except PdfRenderError as exc:
            status = exc.status
        refused_in = time.monotonic() - start
        await first
        return status, refused_in

    status, refused_in = asyncio.run(scenario())
    assert status == 429
    assert refused_in < 0.2


def test_f2_another_organization_is_never_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[tuple[str, float]] = []
    monkeypatch.setattr(pdf_render, "_run_child", _fake_child(1.5, starts))
    monkeypatch.setattr(pdf_render, "MAX_CONCURRENT_RENDERS", 4)

    async def scenario() -> float:
        first = asyncio.ensure_future(render_pdf("html", "a1", member="asha@a.test", org="org-a"))
        await asyncio.sleep(0.1)
        waiting = asyncio.ensure_future(render_pdf("html", "a2", member="ravi@a.test", org="org-a"))
        await asyncio.sleep(0.1)
        asked = time.monotonic()
        other = asyncio.ensure_future(render_pdf("html", "b1", member="bo@b.test", org="org-b"))
        await asyncio.gather(first, waiting, other)
        return asked

    asked = asyncio.run(scenario())
    order = dict(starts)
    assert order["b1"] - asked < 0.1
    assert order["a2"] - order["a1"] >= 1.45


def test_f2_waiting_colleagues_hold_no_pool_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """The r3 probe again: 40 members of one organization queue behind one
    slow render. The wait is an ``await``, so an unrelated ``to_thread`` runs
    at once. With a thread per waiter, 40 would fill the default pool."""
    starts: list[tuple[str, float]] = []
    monkeypatch.setattr(pdf_render, "_run_child", _fake_child(1.5, starts))
    monkeypatch.setattr(pdf_render, "ORG_WAIT_S", 1.0)

    async def scenario() -> tuple[float, list[int]]:
        renders = [
            asyncio.ensure_future(render_pdf("html", f"r{i}", member=f"m{i}@a.test", org="org-a"))
            for i in range(40)
        ]
        await asyncio.sleep(0.2)
        start = time.monotonic()
        await asyncio.to_thread(lambda: None)
        probe = time.monotonic() - start
        done = await asyncio.gather(*renders, return_exceptions=True)
        return probe, [getattr(r, "status", 200) for r in done]

    began = time.monotonic()
    probe, statuses = asyncio.run(scenario())
    assert probe < 0.5, probe
    assert statuses.count(200) == 1
    assert statuses.count(429) == 39
    assert time.monotonic() - began < 5
    assert not pdf_render._orgs_rendering
    assert not pdf_render._members_rendering


def test_f2_a_cancelled_waiter_leaves_no_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[tuple[str, float]] = []
    monkeypatch.setattr(pdf_render, "_run_child", _fake_child(1.0, starts))

    async def scenario() -> None:
        first = asyncio.ensure_future(render_pdf("html", "first", member="asha@a.test", org="org-a"))
        await asyncio.sleep(0.1)
        waiter = asyncio.ensure_future(render_pdf("html", "gone", member="ravi@a.test", org="org-a"))
        await asyncio.sleep(0.1)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert "ravi@a.test" not in pdf_render._members_rendering
        await first

    asyncio.run(scenario())
    assert [s for s, _ in starts] == ["first"]
    assert not pdf_render._orgs_rendering
    assert not pdf_render._members_rendering
