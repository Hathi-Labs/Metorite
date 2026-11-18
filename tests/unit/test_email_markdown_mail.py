"""A chat mail is Markdown, and it goes out with its formatting (task 3605).

Owner ask, 2026-11-17: a mail that the chat writes goes out with headings,
lists, bold and italics. ``gateway/routes/email/markdown_mail.py`` is the ONE
converter. It makes the text part and the HTML part, and the HTML goes
through the one mail sanitizer (``signature.clean_signature_html``).

What this file fences (R7). Each test names the mutation that turns it red.

1. The converter keeps headings, nested lists, bold, italics, links and a
   table, each with an inline style. ``test_the_converter_*``.
2. Nothing that loads or runs goes out: a script, a ``javascript:`` or a
   ``data:`` link, an event attribute, remote CSS, a frame, a form, an image,
   a style with ``url(``, a bidi control. The final pass alone drops them
   too. A link whose words name another address is text, in every chat.
   ``test_*_never_*``, ``test_the_final_pass_*``, ``test_a_link_*``.
3. The text part is the body as written, and never loses a character. A
   body with no real structure, or deeper than the parser limit, gets no
   HTML. A fault of the converter sends the text. ``test_the_text_part_*``,
   ``test_a_body_deeper_*``, ``test_a_fault_*``.
4. A fixed sample gives a fixed HTML, and that HTML is a fixed point of the
   sanitizer. ``test_the_golden_*``. A plain body sends as before: its own
   text, no HTML. ``test_plain_*``.
5. The signature joins once, in each part, below the text.
   ``test_the_signature_*``.
6. The send route, the card acts and the draft saves pass the Markdown on.
   A held run (a ``no_egress`` run, H-236) and a draft keep the text only.
   ``test_the_send_*``, ``test_the_*_act_*``, ``test_a_draft_*``.
7. The tool marks its payload as Markdown and tells the member on the card.
   ``test_the_tool_*``.

The database, Redis and the provider are fakes. No test sends a mail.
"""
from __future__ import annotations

import contextlib
import json
import re
import types
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_skills import ask_tools
from acb_skills import reply_card as rcard
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email import markdown_mail as mm
from gateway.routes.email.automation import drafting
from gateway.routes.email.automation import held_card as hc
from gateway.routes.email.automation import new_mail_card as nm
from gateway.routes.email.automation import reply_card as rc
from gateway.routes.email.signature import build_signed_bodies, clean_signature_html
from gateway.routes.email.transport import send as send_mod
from gateway.routes.email.transport.send import SendEmailRequest

from tests.unit import _held_card_fakes as F

SIG = "<p>Jane Doe<br>Acme Works</p>"
SIG_TEXT = "Jane Doe\nAcme Works"


# ── 1. The converter ────────────────────────────────────────────────────────


def test_the_converter_keeps_three_heading_sizes_with_inline_font_sizes() -> None:
    """Mutation: drop the heading branch, or its style → red."""
    out = mm.mail_bodies("# One\n\n## Two\n\n### Three\n\n#### Four")
    assert out.html is not None
    assert '<h1 style="margin: 16px 0 8px 0; font-size: 22px;' in out.html
    assert '<h2 style="margin: 16px 0 8px 0; font-size: 18px;' in out.html
    assert out.html.count("font-size: 16px") == 2  # a level 4 heading is a level 3
    assert "<h4" not in out.html
    assert out.text == "# One\n\n## Two\n\n### Three\n\n#### Four"


def test_the_converter_keeps_a_nested_list_in_both_parts() -> None:
    """Mutation: render a nested list flat, or drop the text markers → red."""
    out = mm.mail_bodies("1. Budget\n   - Q3\n   - Q4\n2. Hiring")
    assert out.html is not None
    assert out.html.startswith('<ol style="margin: 0 0 12px 0; padding-left: 24px">')
    assert '<ul style="margin: 4px 0 0 0; padding-left: 24px">' in out.html
    assert out.html.index("<ul") < out.html.index("Q3") < out.html.index("</ol>")
    assert out.text == "1. Budget\n   - Q3\n   - Q4\n2. Hiring"


def test_the_converter_numbers_a_list_that_starts_late_itself() -> None:
    """The allowlist keeps no ``start``, so the numbers are text."""
    out = mm.mail_bodies("3. three\n4. four")
    assert out.html is not None and "<ol" not in out.html
    assert "list-style-type: none" in out.html and ">3. three</li>" in out.html
    assert out.text == "3. three\n4. four"


def test_the_converter_keeps_bold_italics_and_strikethrough() -> None:
    out = mm.mail_bodies("A **bold**, an _italic_ and a ~~gone~~ word.")
    assert out.html == ('<p style="margin: 0 0 12px 0">A <strong>bold</strong>, an '
                        "<em>italic</em> and a <s>gone</s> word.</p>")
    assert out.text == "A **bold**, an _italic_ and a ~~gone~~ word."


def test_the_converter_keeps_each_link_the_sanitizer_keeps() -> None:
    """https, mailto and tel stay links. An autolink stays a link beside
    other structure. The text part is the Markdown as written."""
    out = mm.mail_bodies("See [the report](https://e.test/r), [Priya](mailto:p@e.test) "
                         "and [call](tel:+911234) or <https://e.test/x>.")
    assert out.html is not None
    for href in ("https://e.test/r", "mailto:p@e.test", "tel:+911234", "https://e.test/x"):
        assert f'<a href="{href}">' in out.html
    assert out.text == ("See [the report](https://e.test/r), [Priya](mailto:p@e.test) "
                        "and [call](tel:+911234) or <https://e.test/x>.")


def test_an_http_link_shows_its_words_and_its_address_as_text() -> None:
    """The mail allowlist keeps https links only (D-EM-70). The reader still
    sees where an http link points. Mutation: drop the text branch → red."""
    out = mm.mail_bodies("- [old site](http://e.test/o)")
    assert out.html is not None and "<a" not in out.html
    assert "old site (http://e.test/o)" in out.html
    assert out.text == "- [old site](http://e.test/o)"


def test_the_converter_keeps_a_table_with_inline_borders() -> None:
    """Gmail and Outlook keep an inline border. Mutation: drop the cell
    style, or the alignment → red."""
    out = mm.mail_bodies("| Item | Cost |\n|---|--:|\n| A | 10 |")
    assert out.html is not None
    assert out.html.startswith(
        '<table style="margin: 0 0 12px 0; border-collapse: collapse"><thead><tr>')
    assert ('<th style="border: 1px solid #cccccc; padding: 6px 10px; '
            'background-color: #f3f3f3; font-weight: bold; text-align: left">Item</th>'
            ) in out.html
    assert ('<td style="border: 1px solid #cccccc; padding: 6px 10px; '
            'text-align: right">10</td>') in out.html
    assert out.text == "| Item | Cost |\n|---|--:|\n| A | 10 |"


def test_the_converter_writes_a_quote_and_code_with_kept_tags_only() -> None:
    """The allowlist has no blockquote, pre or code tag. Each one becomes a
    ``div`` or a ``span`` with an inline style."""
    out = mm.mail_bodies("> a quote\n\nUse `x = 1`.\n\n```\nline 1\nline 2\n```\n\n---\n\nEnd")
    assert out.html is not None
    for tag in ("<blockquote", "<pre", "<code"):
        assert tag not in out.html
    assert "border-left: 3px solid #cccccc" in out.html
    assert "font-family: Consolas, Menlo, monospace" in out.html
    assert "line 1<br>line 2" in out.html and "<hr style=" in out.html
    assert out.text.startswith("> a quote\n\nUse `x = 1`.")


# ── 2. Nothing that loads or runs goes out ──────────────────────────────────


@pytest.mark.parametrize("raw", [
    "<script>alert(1)</script>",
    "Hi <script>alert(1)</script> there",
    '<img src="x" onerror="alert(1)">',
    '<style>@import url(https://e.test/a.css)</style>',
    '<link rel="stylesheet" href="https://e.test/a.css">',
    '<iframe src="https://e.test"></iframe>',
    '<form action="https://e.test"><input name="a"></form>',
])
def test_raw_html_never_becomes_a_tag(raw: str) -> None:
    """Raw HTML is OFF in the parser, so a tag in the Markdown is text that
    the mail shows. Two layers hold it: the writer escapes each text, and the
    sanitizer drops a tag that gets past it. Mutation: write a text with no
    escape → red (the sanitizer still drops the tags). Write it with no
    escape AND skip the sanitizer → a ``<script>`` tag goes out, red."""
    out = mm.mail_bodies(f"- item\n\n{raw}")
    assert out.html is not None
    for tag in ("<script", "<img", "<style", "<link", "<iframe", "<form", "<input"):
        assert tag not in out.html
    # No tag carries an event attribute: ``onerror=`` stays in escaped text.
    assert re.search(r"<[a-z][^>]*\son\w+=", out.html) is None
    assert "&lt;" in out.html  # the reader sees the text that was written


def test_a_javascript_link_never_becomes_a_link() -> None:
    out = mm.mail_bodies("- [x](javascript:alert(1))\n- [y](JaVaScRiPt:alert(1))")
    assert out.html is not None and "<a" not in out.html
    assert "href" not in out.html


def test_a_data_link_never_becomes_a_link() -> None:
    """The parser lets a ``data:image`` link through. The sanitizer refuses
    it. Mutation: skip the sanitizer (``_sanitize`` as the identity) → red."""
    out = mm.mail_bodies("- [d](data:image/png;base64,AAAA)")
    assert out.html is not None and "<a" not in out.html
    assert "d (data:image/png;base64,AAAA)" in out.html


def test_an_image_never_loads() -> None:
    """A Markdown image is its alt text. Mutation: render ``<img>`` → red."""
    out = mm.mail_bodies("- ![logo *x*](https://e.test/logo.png)")
    assert out.html is not None and "<img" not in out.html
    assert "e.test/logo.png" not in out.html
    assert out.text == "- ![logo *x*](https://e.test/logo.png)"


def test_a_style_that_loads_a_resource_never_goes_out(monkeypatch) -> None:
    """The whole HTML part goes through the sanitizer. A style with ``url(``
    is dropped there. Mutation: skip the sanitizer → red."""
    monkeypatch.setitem(mm._STYLES, "p", "margin: 0; background: url(https://e.test/t.gif)")
    out = mm.mail_bodies("A **bold** word.")
    assert out.html is not None and "url(" not in out.html
    assert '<p style="margin: 0">' in out.html


def test_a_character_reference_never_becomes_a_hidden_character() -> None:
    """``&#8203;`` is a zero-width space. The ``entity`` rule is off, so it
    stays text. Mutation: enable the rule → red."""
    out = mm.mail_bodies("- a&#8203;b &#x202E; &amp;")
    assert out.html is not None
    for hidden in ("\u200b", "\u202e"):
        assert hidden not in out.html and hidden not in out.text
    assert out.text == "- a&#8203;b &#x202E; &amp;"


def test_the_final_pass_alone_drops_what_loads_or_runs() -> None:
    """The module's own final pass (``_sanitize``) is the mail allowlist. It
    drops a script, an ``http:`` link and a style with ``url(`` by itself,
    whatever the writer gives it. Mutation: ``_sanitize`` as the identity, or
    any sanitizer but the mail allowlist → red."""
    hostile = ('<p style="background: url(https://e.test/t.gif)">a</p>'
               '<script>alert(1)</script><a href="http://a.test">x</a>'
               '<img src="x" onerror="alert(1)"><iframe src="https://e.test"></iframe>')
    clean = mm._sanitize(hostile)
    for bad in ("<script", "alert(1)", 'href="http:', "url(", "<img", "onerror", "<iframe"):
        assert bad not in clean, bad
    assert mm._sanitize is clean_signature_html
    # End to end: an http link never leaves as a link, even if ``_link_kept``
    # said yes, because the final pass drops its address.
    assert 'href="http:' not in (mm.mail_bodies("- [x](http://a.test)").html or "")


@pytest.mark.parametrize("label_href", [
    ("https://acme.test/pay", "https://evil.test/pay"),   # one address shown, another used
    ("acme.test/pay", "https://evil.test/pay"),           # a dotted host, no scheme
    ("https://acme.test", "https://evil.test"),           # another host
    ("www.acme.test", "https://evil.test"),
    ("jane@acme.test", "mailto:mallory@evil.test"),       # another mail address
    ("b" + chr(0x430) + "nk.test/pay", "https://evil.test/pay"),  # a Cyrillic look-alike
    ("".join(map(chr, (0x43F, 0x440, 0x438, 0x43C, 0x435, 0x440))) + ".xn/pay",
     "https://evil.test/pay"),                            # a host with no Latin letter
])
def test_a_link_whose_words_name_another_address_is_text(label_href: tuple[str, str]) -> None:
    """The review of task 3605, P1. A top-level chat that read a stranger's
    mail is not a held run, so it sends HTML. A link whose words look like an
    address must go there, or both show as text. Mutation: drop
    ``_label_misleads`` from ``_inline_html`` → red."""
    label, href = label_href
    out = mm.mail_bodies(f"- [{label}]({href})")
    assert out.html is not None and "<a" not in out.html
    shown = href[len("mailto:"):] if href.startswith("mailto:") else href
    assert f"{label} ({shown})" in out.html


@pytest.mark.parametrize("label_href", [
    ("https://acme.test/pay", "https://acme.test/pay"),   # the same address
    ("acme.test/pay/", "https://acme.test/pay"),          # no scheme, an end slash
    ("www.x.test", "https://x.test"),                     # ``www.`` and the case
    ("WWW.X.TEST", "https://x.test/"),
    ("the plan", "https://evil.test/plan"),               # plain words: a normal link
    ("jane@acme.test", "mailto:jane@acme.test"),
    ("Jane", "mailto:jane@acme.test"),
])
def test_a_link_whose_words_match_or_name_no_address_stays_a_link(
    label_href: tuple[str, str],
) -> None:
    label, href = label_href
    out = mm.mail_bodies(f"- [{label}]({href})")
    assert out.html is not None and f'<a href="{href}">{label}</a>' in out.html


@pytest.mark.parametrize("body", [
    "2*3*4", "5*3 and a*b*c", "__init__", "snake_case", "Jane <jane@x.test>",
    "<https://x.test>", "a | b", "Re: [JIRA-1] x", "[1]", "a**b**c",
])
def test_the_text_part_never_loses_a_character(body: str) -> None:
    """The review of task 3605, P2. The text part is the body as written, and
    a body with no real structure gets no HTML. Mutation: build the text part
    from the render, or count ``a*b*c`` as italics → red."""
    assert mm.mail_bodies(body) == (body, None)
    assert mm.mail_bodies(f"- item\n\n{body}").text == f"- item\n\n{body}"


def test_marks_inside_a_word_stay_as_typed_in_the_html() -> None:
    """Beside real structure, ``2*3*4``, ``a**b**c`` and ``__init__`` still
    show as typed in the HTML. Mutation: render each one as emphasis → red."""
    out = mm.mail_bodies("- item\n\n2*3*4, a**b**c and __init__")
    assert out.html is not None
    assert "2*3*4, a**b**c and __init__" in out.html
    assert "<em>" not in out.html and "<strong>" not in out.html


@pytest.mark.parametrize("body", [
    "- one\n- two", "## Plan", "**bold** words", "| a | b |\n|---|---|\n| 1 | 2 |",
    "Use `x = 1`", "See [the plan](https://e.test/p)", "_note_ this",
])
def test_real_structure_sends_html_and_its_text_as_written(body: str) -> None:
    out = mm.mail_bodies(body)
    assert out.html is not None and out.text == body


@pytest.mark.parametrize("body", [
    "\n".join("  " * i + f"- w{i}" for i in range(25)),
    "".join("> " * (i + 1) + f"q{i}\n" for i in range(25)),
    # Four autolinks before it: their words count once, so they hide no cut.
    # (The parser drops all that follows a cut, so they go first.)
    " ".join(f"<https://a{i}.test/p/q/r/s>" for i in range(4)) + "\n\n"
    + "\n".join("  " * i + f"- w{i}" for i in range(25)),
])
def test_a_body_deeper_than_the_parser_limit_gets_no_html(body: str) -> None:
    """markdown-it drops content past 20 levels. That body sends as text,
    whole. Mutation: drop the word count in ``_convert`` → red."""
    assert mm.mail_bodies(body) == (body, None)


def test_a_bidi_control_goes_from_both_parts() -> None:
    """U+202A to U+202E and U+2066 to U+2069 can turn the text that a reader
    sees around. A zero-width space stays: the card refuses hidden text.
    Mutation: drop ``_BIDI`` → red."""
    controls = "".join(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))
    out = mm.mail_bodies(f"- pay{controls} now\u200b")
    assert out.html is not None
    for c in controls:
        assert c not in out.text and c not in out.html
    assert out.text == "- pay now\u200b"
    assert mm.mail_bodies(f"Hi{chr(0x202E)} Kim") == ("Hi Kim", None)


def test_a_fault_of_the_converter_sends_the_text(monkeypatch) -> None:
    """A fault gives the text part and no HTML, never a 500 after a card act
    spent its answer. Mutation: drop the ``try`` in ``mail_bodies`` → red."""
    def broken(_text: str) -> str:
        raise RecursionError("deep")

    monkeypatch.setattr(mm, "_convert", broken)
    assert mm.mail_bodies("- one") == ("- one", None)
    assert mm.draft_text("- one") == ("- one", False)


# ── 3. The golden HTML ──────────────────────────────────────────────────────

GOLDEN_MD = (
    "## Agenda\n"
    "\n"
    "1. **Budget**\n"
    "   - Q3 numbers\n"
    "   - _Risks_\n"
    "2. Hiring\n"
    "\n"
    "| Item | Cost |\n"
    "|---|--:|\n"
    "| Laptops | 4 |\n"
    "\n"
    "See [the plan](https://e.test/plan).\n"
    "Best,\n"
    "Jane"
)
GOLDEN_HTML = (
    '<h2 style="margin: 16px 0 8px 0; font-size: 18px; font-weight: bold; line-height: 1.3">'
    "Agenda</h2>"
    '<ol style="margin: 0 0 12px 0; padding-left: 24px">'
    '<li style="margin: 0 0 4px 0"><strong>Budget</strong>'
    '<ul style="margin: 4px 0 0 0; padding-left: 24px">'
    '<li style="margin: 0 0 4px 0">Q3 numbers</li>'
    '<li style="margin: 0 0 4px 0"><em>Risks</em></li></ul></li>'
    '<li style="margin: 0 0 4px 0">Hiring</li></ol>'
    '<table style="margin: 0 0 12px 0; border-collapse: collapse"><thead><tr>'
    '<th style="border: 1px solid #cccccc; padding: 6px 10px; background-color: #f3f3f3; '
    'font-weight: bold; text-align: left">Item</th>'
    '<th style="border: 1px solid #cccccc; padding: 6px 10px; background-color: #f3f3f3; '
    'font-weight: bold; text-align: right">Cost</th></tr></thead><tbody><tr>'
    '<td style="border: 1px solid #cccccc; padding: 6px 10px; text-align: left">Laptops</td>'
    '<td style="border: 1px solid #cccccc; padding: 6px 10px; text-align: right">4</td>'
    "</tr></tbody></table>"
    '<p style="margin: 0 0 12px 0">See <a href="https://e.test/plan">the plan</a>.'
    "<br>Best,<br>Jane</p>"
)
#: The text part is the Markdown as written (the review of task 3605, P2).
GOLDEN_TEXT = GOLDEN_MD


def test_the_golden_sample_gives_the_golden_html_and_text() -> None:
    """Mail clients were not checked here (no real mailbox, outbound off). The
    HTML of a fixed sample is fixed instead. A change of the writer shows in
    this diff."""
    out = mm.mail_bodies(GOLDEN_MD)
    assert out.html == GOLDEN_HTML
    assert out.text == GOLDEN_TEXT


def test_the_golden_html_is_a_fixed_point_of_the_sanitizer() -> None:
    """Each tag, attribute and style of the writer is one that the mail
    allowlist keeps, so the sanitizer changes nothing. Mutation: a style or
    a tag that the allowlist drops → red."""
    assert clean_signature_html(GOLDEN_HTML) == GOLDEN_HTML


# ── 4. A plain body sends as before ─────────────────────────────────────────


@pytest.mark.parametrize("plain", [
    "Hi Ravi,\n\nThanks for the call.\n\nBest,\nVijay",
    "Regards,\n--\nName",                      # no Setext heading
    "    an indented line\nstays text",        # no indented code
    "5 * 3 = 15, file_name_v2 and a < b & c",  # no emphasis, no tag
    "Hi <Name>,\n\nSee you.",                  # an angle-bracket word stays
    "[1]: https://e.test is a footnote line",  # no reference definition
    "",
])
def test_plain_prose_keeps_its_own_text_and_no_html(plain: str) -> None:
    """Mutation: send HTML for every body, rewrite a plain text, or turn raw
    HTML on in ``_parser`` (``<Name>`` is then a tag) → red."""
    assert mm.mail_bodies(plain) == (plain, None)
    assert mm.draft_text(plain) == (plain, False)


def test_a_formatted_body_that_quoting_would_cut_gets_no_html() -> None:
    """``split_quoted_text`` cuts at "On ... wrote:", and the HTML has no such
    cut. With no HTML, the signature has one place. Mutation: drop the check
    in ``mail_bodies`` → red."""
    out = mm.mail_bodies("- yes\n\nOn Mon, Priya wrote:\n> old")
    assert out.html is None
    assert out.text == "- yes\n\nOn Mon, Priya wrote:\n> old"
    # A Markdown quote after the first line is a quoted thread for the cut too.
    assert mm.mail_bodies("- one\n\n> a quote\n\nEnd.").html is None


# ── 5. The signature joins once ─────────────────────────────────────────────


def test_a_body_that_holds_the_signature_text_keeps_one_signature() -> None:
    """A model can copy the signature into the body. ``build_signed_bodies``
    then adds none, and the HTML keeps the copy as text. Known limit: that
    mail shows the signature as plain words, not as the HTML block."""
    parts = mm.mail_bodies(f"- one\n- two\n\n{SIG_TEXT}")
    text, html = build_signed_bodies(SIG, *parts)
    assert text.count(SIG_TEXT) == 1
    assert html is not None and SIG not in html and html.count("Jane Doe") == 1


def test_the_card_text_cleaners_keep_a_nested_list_and_a_table() -> None:
    """The card cleans its text before it shows it (``clean_reply_body``,
    ``strip_placeholder_lines``). Each keeps the indent of a nested list and
    the rows of a table, so the sent mail keeps them."""
    from acb_skills.reply_card import clean_reply_body
    from gateway.routes.email.automation.drafting import strip_placeholder_lines

    source = "1. Budget\n   - Q3\n   - Q4\n2. Hiring\n\n| Item | Cost |\n|---|--:|\n| A | 10 |"
    cleaned = clean_reply_body(strip_placeholder_lines(source))
    assert cleaned == source
    assert mm.mail_bodies(cleaned).html == mm.mail_bodies(source).html


@pytest.mark.parametrize("markdown", [GOLDEN_MD, "> a first quote\n\n```\ncode\n```\n\nEnd."])
def test_the_signature_joins_once_below_the_text_in_each_part(markdown: str) -> None:
    """The signature goes below all the text in both parts. Mutation: join
    the signature twice → red."""
    parts = mm.mail_bodies(markdown)
    assert parts.html is not None
    text, html = build_signed_bodies(SIG, *parts)
    assert text.count(SIG_TEXT) == 1 and text.endswith(SIG_TEXT)
    assert html is not None
    assert html.count(SIG) == 1 and html.endswith(SIG)


# ── 6. The send route, the acts and the draft saves ─────────────────────────


class _Db:
    """A session that answers the stored signature."""

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        row = (SimpleNamespace(signature=SIG) if "FROM email_assistant_settings" in sql
               else None)
        return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [])


async def _route_send(monkeypatch, **req: Any) -> dict[str, Any]:
    """One ``POST /email/send`` through the real route. Answers the provider's
    send kwargs."""
    sent: dict[str, Any] = {}

    class _Provider:
        async def send_message(self, **kw: Any) -> str:
            sent.update(kw)
            return "msg-1"

    @asynccontextmanager
    async def session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        yield _Db()

    @asynccontextmanager
    async def provider_session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(provider=_Provider())

    async def target(*_a: Any) -> Any:
        return None, None

    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "email_signature_templates_orgs", "", raising=False)
    monkeypatch.setattr(send_mod, "_tenant_session", session)
    monkeypatch.setattr(send_mod, "provider_session", provider_session)
    monkeypatch.setattr(send_mod, "_reply_target", target)
    await send_mod.send_email(SendEmailRequest(
        account_id="acc-1", to=["kim@client.test"], subject="Plan", **req),
        BackgroundTasks(), user=SimpleNamespace(email="jane@acme.test"))
    return sent


async def test_the_send_route_sends_both_parts_of_a_markdown_body(monkeypatch) -> None:
    """Mutation: ignore ``body_markdown`` in ``_bodies_of`` → red."""
    sent = await _route_send(monkeypatch, body_text=GOLDEN_MD, body_markdown=True)
    assert sent["body_text"] == f"{GOLDEN_TEXT}\n\n{SIG_TEXT}"
    assert sent["body_html"] == f"{GOLDEN_HTML}<br><br>{SIG}"


async def test_the_send_route_sends_a_plain_body_as_before(monkeypatch) -> None:
    """The flag changes nothing for a plain body: the same kwargs as a send
    with no flag."""
    plain = "Hi Kim,\n\nYes, Friday works.\n\nJane"
    with_flag = await _route_send(monkeypatch, body_text=plain, body_markdown=True)
    without = await _route_send(monkeypatch, body_text=plain)
    assert with_flag == without
    assert with_flag["body_text"] == f"{plain}\n\n{SIG_TEXT}"


async def test_the_send_route_refuses_markdown_with_html(monkeypatch) -> None:
    with pytest.raises(HTTPException) as e:
        await _route_send(monkeypatch, body_text="# x", body_html="<p>x</p>",
                          body_markdown=True)
    assert e.value.status_code == 422 and e.value.detail == send_mod.MARKDOWN_AND_HTML


async def test_the_send_route_sends_the_text_when_the_converter_fails(monkeypatch) -> None:
    """A card act spent its answer and its record before the send, so a fault
    of the converter sends the text alone, as before task 3605, and never a
    500. Mutation: drop the ``try`` in ``_bodies_of`` → red."""
    def broken(_source: str) -> Any:
        raise ImportError("markdown_it")

    monkeypatch.setattr(mm, "mail_bodies", broken)
    sent = await _route_send(monkeypatch, body_text=GOLDEN_MD, body_markdown=True)
    # The same mail as a send with no flag: the text, and the HTML of the text.
    assert sent == await _route_send(monkeypatch, body_text=GOLDEN_MD)
    assert sent["body_text"] == f"{GOLDEN_MD}\n\n{SIG_TEXT}" and "<h2" not in sent["body_html"]


def test_a_draft_save_keeps_the_text_part_and_says_so() -> None:
    """A draft holds text only. Mutation: save the Markdown source, or drop
    the ``formatting`` key → red."""
    req = drafting.DraftUpsertRequest(account_id="acc-1", body=GOLDEN_MD, body_markdown=True)
    out, extra = drafting._markdown_draft_text(req)
    assert out.body == GOLDEN_TEXT and extra == {"formatting": "text_only"}
    plain = drafting.DraftUpsertRequest(account_id="acc-1", body="Hi.", body_markdown=True)
    assert drafting._markdown_draft_text(plain) == (plain, {})
    off = drafting.DraftUpsertRequest(account_id="acc-1", body=GOLDEN_MD)
    assert drafting._markdown_draft_text(off) == (off, {})
    keep = drafting.DraftUpsertRequest(account_id="acc-1", body=GOLDEN_MD,
                                       body_markdown=True, keep_body=True,
                                       draft_id="b0000000-0000-4000-8000-0000000000d1")
    assert drafting._markdown_draft_text(keep) == (keep, {})


async def test_a_draft_save_writes_the_text_part_and_one_signature(monkeypatch) -> None:
    """``PUT /email/drafts`` with ``body_markdown``: the provider draft gets
    the text part with the signature once, and no HTML."""
    created: list[dict[str, Any]] = []

    class _Provider:
        async def create_draft(self, **kw: Any) -> str:
            created.append(kw)
            return "pm-1"

    @asynccontextmanager
    async def session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace()

    @asynccontextmanager
    async def provider_session(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(provider=_Provider(), owner_email="jane@acme.test")

    async def nothing(*_a: Any, **_k: Any) -> None:
        return None

    async def signature(*_a: Any, **_k: Any) -> str:
        return SIG

    async def upsert(*_a: Any, **_k: Any) -> str:
        return "d1"

    async def fetch(_db: Any, local_id: str) -> dict[str, Any]:
        return {"id": local_id}

    monkeypatch.setattr(drafting, "_tenant_session", session)
    monkeypatch.setattr(drafting, "_assert_account_owner", nothing)
    monkeypatch.setattr(drafting, "_account_signature", signature)
    monkeypatch.setattr(drafting, "provider_session", provider_session)
    monkeypatch.setattr(drafting, "_upsert_local_draft", upsert)
    monkeypatch.setattr(drafting, "_fetch_message_dict", fetch)
    monkeypatch.setattr(drafting, "_is_gmail", lambda _p: False)
    out = await drafting.upsert_draft(drafting.DraftUpsertRequest(
        account_id="acc-1", to=["kim@client.test"], subject="Plan", body=GOLDEN_MD,
        body_markdown=True, from_chat=True), SimpleNamespace(email="jane@acme.test"))
    [kw] = created
    assert kw["body_text"] == f"{GOLDEN_TEXT}\n\n{SIG_TEXT}"
    assert kw["body_text"].count(SIG_TEXT) == 1 and kw.get("body_html") is None
    assert out == {"id": "d1", "formatting": "text_only"}


TEXT_MD = "Hi Kim,\n\n- **Rates** for May\n- Terms"


@pytest.fixture()
def world(monkeypatch) -> dict[str, Any]:
    """The card acts with fake Redis, a fake session and recorded sends."""
    F.flag_on(monkeypatch)
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "email_edit_card_orgs", "*")
    monkeypatch.setattr(nm, "ANSWER_WAIT_S", 0.0)
    monkeypatch.setattr(hc, "ANSWER_WAIT_S", 0.0)
    F.install_redis(monkeypatch)
    F.install_db(monkeypatch)
    sends = F.install_sends(monkeypatch)
    monkeypatch.setattr(nm, "_tenant_session", hc._tenant_session)
    monkeypatch.setattr(nm, "resolve_self", hc.resolve_self)
    saves: list[Any] = []

    async def upsert(req: Any, user: Any) -> dict[str, Any]:
        saves.append(req)
        lost = drafting._markdown_draft_text(req)[1]
        return {"id": "draft-new-1", **lost}

    monkeypatch.setattr(drafting, "upsert_draft", upsert)
    monkeypatch.setattr(hc, "upsert_draft", upsert)
    return {"sends": sends, "saves": saves}


def _answer(*, action: str = "send", body: str = TEXT_MD, **extra: Any) -> str:
    mail = {"kind": "new", "action": action, "subject": "Rates", "body": body,
            "to": [F.KNOWN], "cc": [], "bcc": [], **extra}
    return "APPROVE " + json.dumps({"reply": mail})


async def _new_card_act(answer: str) -> dict[str, Any]:
    card = await nm.new_mail_card(nm.NewMailCardRequest(
        account_id=F.BOX, to=[F.KNOWN], subject="Rates", body=TEXT_MD), F.user())
    await F.record_answer(card["card_id"], answer)
    req = nm.NewMailActRequest(card_id=card["card_id"], card_thread_id="chat-thread-1",
                               answer=answer)
    return await nm.new_mail_card_act(req, BackgroundTasks(), F.user())


async def test_the_new_mail_card_act_sends_the_markdown(world) -> None:
    """The card shows the Markdown, and the member's text goes as Markdown.
    The answer keeps the member's own text. Mutation: drop
    ``body_markdown=True`` in the act → red."""
    out = await _new_card_act(_answer())
    [sent] = world["sends"].mails
    assert sent.body_markdown is True and sent.body_text == TEXT_MD
    assert out["body"] == TEXT_MD and out["edited"] is False


async def test_the_new_mail_card_save_keeps_the_text_and_says_so(world) -> None:
    out = await _new_card_act(_answer(action="save"))
    [saved] = world["saves"]
    assert saved.body_markdown is True and saved.body == TEXT_MD
    assert out["formatting"] == "text_only" and world["sends"].mails == []


async def _held_act(answer: str) -> dict[str, Any]:
    card = await hc.held_card(hc.HeldCardRequest(
        kind="new", account_id=F.BOX, to=[F.KNOWN], subject="Rates", note=TEXT_MD),
        F.user())
    await F.record_answer(card["card_id"], answer)
    req = hc.HeldCardActRequest(card_id=card["card_id"], card_thread_id="chat-thread-1",
                                answer=answer)
    return await hc.held_card_act(req, BackgroundTasks(), F.user())


async def test_the_held_card_act_sends_the_text_part_only(world) -> None:
    """A held run (a ``no_egress`` run, H-236) sends no HTML. Its text is the
    Markdown as written, so each link shows its address. Mutation: send
    ``body_markdown=True`` → red."""
    await _held_act(_answer(body="See [the form](https://e.test/f).\n\n- one"))
    [sent] = world["sends"].mails
    assert sent.body_markdown is False and sent.body_html is None
    assert sent.body_text == "See [the form](https://e.test/f).\n\n- one"


async def test_the_held_card_save_keeps_the_text_and_says_so(world) -> None:
    out = await _held_act(_answer(action="save"))
    [saved] = world["saves"]
    assert saved.body_markdown is True
    assert out["formatting"] == "text_only"


# ── The reply card ──────────────────────────────────────────────────────────

ORG = "org-md-1"
MEMBER = "jane@acme.test"
MAIL = "0b0b0b0b-0000-4000-8000-00000000000b"
USER = types.SimpleNamespace(email=MEMBER, organization_id=ORG)


def _reply_answer(action: str = "send") -> str:
    reply = {"action": action, "reply_all": True, "body": TEXT_MD,
             "to": ["kim@contoso.test"], "cc": [], "message_id": MAIL}
    return "APPROVE " + json.dumps({"reply": reply})


class _ReplyWorld:
    def __init__(self, monkeypatch) -> None:
        F.install_redis(monkeypatch)
        self.sent: list[Any] = []
        self.saved: list[Any] = []

        async def load_mail(message_id: str, owner: str) -> Any:
            row = types.SimpleNamespace(
                account_id="acc-1", provider_message_id="p1", thread_id="t1",
                subject="Quote", from_address={"name": "Kim", "email": "kim@contoso.test"},
                to_addresses=[{"email": MEMBER}], cc_addresses=[], received_at=None,
                snippet="Can you confirm?", body_text="")
            me = types.SimpleNamespace(address=MEMBER, label="Work",
                                       self_addresses=frozenset({MEMBER}))
            return rc._Mail(row, me, [])

        async def send(req: Any, background: Any, user: Any) -> dict[str, Any]:
            self.sent.append(req)
            return {"id": "sent-1", "ok": True}

        async def save(mail: Any, owner: str, to: list[str], cc: list[str],
                       subject: str, body: str, **_kw: Any) -> str:
            self.saved.append(body)
            return "draft-saved"

        @contextlib.asynccontextmanager
        async def session():
            yield types.SimpleNamespace()

        async def in_drafts(*_a: Any) -> bool:
            return True

        async def recent(*_a: Any) -> dict[str, str]:
            return {}

        monkeypatch.setattr(rc, "_load_mail", load_mail)
        monkeypatch.setattr(rc, "send_email", send)
        monkeypatch.setattr(rc, "_save_draft", save)
        monkeypatch.setattr(rc, "_tenant_session", session)
        monkeypatch.setattr(rc, "draft_in_drafts", in_drafts)
        monkeypatch.setattr(rc, "recent_thread_reply", recent)

    async def act(self, *, held: bool, action: str = "send") -> dict[str, Any]:
        card_id = await rc._write_reply_record(USER, MAIL, [], held=held)
        assert card_id
        # The real card route writes the record twice: the second write
        # names the draft. The held flag must live through it.
        await rc._note_card_draft(USER, card_id, MAIL, "0d0d0d0d-0000-4000-8000-0000000000aa")
        text = _reply_answer(action)
        assert await rcard.record_reply_answer(ORG, card_id, MEMBER, text, "th-1")
        req = rc.ReplyCardActRequest(message_id=MAIL, card_request_id=card_id,
                                     card_thread_id="th-1", answer=text)
        return await rc.reply_card_act(req, types.SimpleNamespace(), USER)


async def test_the_reply_card_act_sends_the_markdown_of_a_chat_that_is_not_held(
    monkeypatch,
) -> None:
    """Mutation: ``html_ok = True`` for each card, or drop the flag → red."""
    w = _ReplyWorld(monkeypatch)
    await w.act(held=False)
    [sent] = w.sent
    assert sent.body_markdown is True and sent.body_text == TEXT_MD


async def test_the_reply_card_act_of_a_held_chat_sends_the_text_part(monkeypatch) -> None:
    w = _ReplyWorld(monkeypatch)
    await w.act(held=True)
    [sent] = w.sent
    assert sent.body_markdown is False and sent.body_text == TEXT_MD


async def test_a_reply_record_names_its_held_flag_and_fails_closed(monkeypatch) -> None:
    """Only a record that says ``held: false`` sends HTML. A record from
    before this release has no key, and it reads as held."""
    F.install_redis(monkeypatch)
    for held, want in ((False, False), (True, True)):
        card_id = await rc._write_reply_record(USER, MAIL, [], held=held)
        assert (await rc._take_reply_record(USER, card_id, MAIL)).held is want
    assert (await rc._take_reply_record(USER, "", MAIL)).held is None
    old = json.dumps({"owner": MEMBER, "message_id": MAIL, "added": [], "bcc": []})
    from acb_common.tenant_redis import organization_scope

    with organization_scope(ORG):
        await hc._tenant_redis().set(rc._reply_record_key("c" * 32), old)
    assert (await rc._take_reply_record(USER, "c" * 32, MAIL)).held is True


async def test_the_reply_card_save_says_the_draft_lost_the_formatting(monkeypatch) -> None:
    w = _ReplyWorld(monkeypatch)
    out = await w.act(held=False, action="save")
    assert w.saved == [TEXT_MD] and out["formatting"] == "text_only"


async def test_the_reply_card_draft_and_its_record_get_the_same_text(monkeypatch) -> None:
    """The chat knows its own draft by its text (``email_ai_drafts``). The
    provider draft and the record get the same text part: the Markdown as
    written, less the bidi controls. Mutation: drop ``draft_text`` in
    ``_write_draft`` (the bidi control stays) → red."""
    wrote: dict[str, str] = {}

    @contextlib.asynccontextmanager
    async def session():
        yield types.SimpleNamespace()

    @contextlib.asynccontextmanager
    async def provider_session(*_a: Any, **_k: Any):
        yield types.SimpleNamespace(provider=object())

    async def upsert(_db: Any, _p: Any, _aid: str, **kw: Any) -> Any:
        wrote["draft"] = kw["body"]
        return types.SimpleNamespace(local_id="d1", kept_draft=False)

    async def store(_db: Any, _aid: str, _tid: str, body: str, **_kw: Any) -> None:
        wrote["record"] = body

    monkeypatch.setattr(rc, "_tenant_session", session)
    monkeypatch.setattr(rc, "provider_session", provider_session)
    monkeypatch.setattr(rc, "_upsert_reply_draft", upsert)
    monkeypatch.setattr(rc, "_store_ai_draft", store)
    mail = types.SimpleNamespace(account_id="acc-1", thread_id="t1",
                                 provider_message_id="p1", address=MEMBER)
    await rc._write_draft(mail, MEMBER, ["kim@contoso.test"], [], "Re: Quote",
                          TEXT_MD + chr(0x202E), chat_wrote=True)
    assert wrote == {"draft": TEXT_MD, "record": TEXT_MD}


# ── 7. The tool ─────────────────────────────────────────────────────────────

agents = F.load_email_agent("ea_markdown_mail")


def test_the_tool_text_says_the_body_is_markdown_in_the_same_words() -> None:
    """The cal-tasks approval: one sentence and one clause, the same words
    in each tool. Mutation: change the words in one tool → red."""
    words = "The body is Markdown. HTML tags show as text."
    for tool in (agents.send_email, agents.compose_reply):
        assert words in " ".join((tool.__doc__ or "").split()), tool.__name__
    assert "plain-text body" not in (agents.send_email.__doc__ or "")


@pytest.mark.parametrize(("body", "shown"), [
    (TEXT_MD, True), ("## Plan\nText", True), ("1. one\n2. two", True),
    ("See [x](https://e.test)", True), ("Please *note* this", True),
    ("_note_ well", True), ("~~gone~~ now", True), ("Use `x`", True),
    ("Hi Kim,\n\nThanks.\n\nJane", False), ("2*3*4 and a*b*c", False),
    ("__init__ and snake_case", False),
    # An autolink alone gets no HTML from the gateway, so no card line.
    ("Jane <jane@x.test> or <https://x.test>", False),
    # A quote after the first line gets no HTML, and a pipe is no table.
    ("Hi,\n\n> quote\n\nBye", False), ("| a | b |", False), ("don`t", False),
    ("| a | b |\n|---|---|\n| 1 | 2 |", True),
])
def test_the_tool_marks_a_card_of_a_markdown_body(body: str, shown: bool) -> None:
    """The card line shows where the gateway makes HTML. Mutation: drop the
    italics or strikethrough branch of ``_MARKDOWN_MARK`` → red."""
    assert bool(agents._formatted_notice(body)) is shown
    gateway_html = mm.mail_bodies(body).html is not None
    assert gateway_html is shown, body


def test_the_tool_says_when_a_draft_lost_the_formatting(monkeypatch) -> None:
    """A draft holds the Markdown as typed, and ``send_draft`` sends it so. A
    held run sends text only, so its line names no send with formatting.
    Mutation: one line for both, or a line that says a draft send keeps the
    formatting → red."""
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    line = agents._draft_text_note({"formatting": "text_only"})
    assert line == agents._DRAFT_TEXT_ONLY and "send_draft sends it with its marks" in line
    assert agents._draft_text_note({}) == agents._draft_text_note("x") == ""
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    held = agents._draft_text_note({"formatting": "text_only"})
    assert held == agents._DRAFT_TEXT_ONLY_HELD and "formatting" not in held


class _Gw:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []

    async def post(self, path: str, body: dict[str, Any] | None = None, **_k: Any) -> Any:
        self.posts.append((path, dict(body or {})))
        if path == "/email/send":
            return {"id": "sent-1"}
        raise AssertionError(f"unexpected POST {path}")


async def test_the_tool_posts_the_markdown_flag_and_shows_the_card_line(monkeypatch) -> None:
    """The plain card path of ``send_email``. Mutation: drop
    ``body_markdown`` from the payload, or the card line → red."""
    gw = _Gw()
    monkeypatch.setattr(agents, "_post", gw.post)
    monkeypatch.setattr(agents, "_current_user_email", lambda: F.MEMBER)

    async def name(_aid: str) -> str:
        return f"Work · {F.MEMBER}"

    async def no_note(*_a: Any) -> str:
        return ""

    async def no_rule(_aid: str, to: list[str], cc: list[str], _bcc: Any) -> Any:
        return cc, []

    cards: list[dict[str, Any]] = []

    async def card(**kw: Any) -> Any:
        cards.append(kw)
        return True

    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_usual_mailbox_note", no_note)
    monkeypatch.setattr(agents, "_always_copy_cc", no_rule)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    monkeypatch.setattr(agents, "_edit_card_open", lambda: False)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    await agents.send_email(F.BOX, body=TEXT_MD, to=[F.KNOWN], subject="Rates")
    [(path, body)] = gw.posts
    assert path == "/email/send" and body["body_markdown"] is True
    assert body["body_text"] == TEXT_MD
    [shown] = cards
    assert agents._FORMATTED_NOTICE in shown["context"]
    _ = ask_tools  # the card seam of this file is ``request_confirmation``


class _DraftGw:
    """The draft routes as ``edit_draft`` sees them. The save answers the
    ``formatting`` key, as ``PUT /email/drafts`` does for a Markdown body."""

    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    async def put(self, path: str, body: dict[str, Any] | None = None, **_k: Any) -> Any:
        assert path == "/email/drafts"
        self.puts.append(dict(body or {}))
        lost = drafting._markdown_draft_text(drafting.DraftUpsertRequest(**body))[1]
        return {"id": "d0000000-0000-4000-8000-0000000000d1", **lost}

    async def get(self, path: str, params: Any = None, **_k: Any) -> Any:
        return {"body_trimmed": False, "body_text": "Old text."}


@pytest.fixture()
def draft_gw(monkeypatch) -> _DraftGw:
    gw = _DraftGw()

    async def name(_aid: str) -> str:
        return f"Work · {F.MEMBER}"

    async def approve(*_a: Any, **_k: Any) -> None:
        return None

    async def row(_aid: str, _did: str) -> Any:
        return {"to_addresses": [F.KNOWN], "cc_addresses": [], "bcc_addresses": [],
                "subject": "Rates"}, f"Work · {F.MEMBER}"

    async def nothing(*_a: Any) -> Any:
        return []

    async def no_line(*_a: Any) -> str:
        return ""

    monkeypatch.setattr(agents, "_put", gw.put)
    monkeypatch.setattr(agents, "_get", gw.get)
    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_card_refusal", approve)
    monkeypatch.setattr(agents, "_draft_row", row)
    monkeypatch.setattr(agents, "_pending_schedules", nothing)
    monkeypatch.setattr(agents, "_cancel_schedules", no_line)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    return gw


async def test_the_tool_saves_a_new_draft_as_markdown_and_says_so(draft_gw) -> None:
    """``edit_draft`` with no draft id. The instructions now say a reply is
    Markdown, so the draft keeps the text with no marks. Mutation: drop
    ``body_markdown`` from the payload, or the answer line → red."""
    out = await agents.edit_draft(F.BOX, body=TEXT_MD, subject="Rates", to=[F.KNOWN])
    [put] = draft_gw.puts
    assert put["body_markdown"] is True and put["body"] == TEXT_MD
    assert agents._DRAFT_TEXT_ONLY in out


async def test_the_tool_changes_a_draft_text_as_markdown(draft_gw) -> None:
    out = await agents.edit_draft(F.BOX, draft_id="d0000000-0000-4000-8000-0000000000d1",
                                  body=TEXT_MD)
    [put] = draft_gw.puts
    assert put["body_markdown"] is True and put["keep_body"] is False
    assert agents._DRAFT_TEXT_ONLY in out


async def test_the_tool_sends_no_markdown_flag_when_the_draft_text_stays(draft_gw) -> None:
    """A change of the people alone keeps the stored text: no flag, and the
    gateway changes no text."""
    out = await agents.edit_draft(F.BOX, draft_id="d0000000-0000-4000-8000-0000000000d1",
                                  to=[F.KNOWN, F.COLLEAGUE])
    [put] = draft_gw.puts
    assert "body_markdown" not in put and put["keep_body"] is True
    assert agents._DRAFT_TEXT_ONLY not in out
