"""The signature belongs under the NEW text, above the quoted thread.

Reported from production: an AI-drafted reply went out as

    Hi Suresh,
    Noted. Hope the appointment goes well.

    On 2026-07-19T14:47:23+00:00, Fracktal Finance wrote:
    > Hi, …

    Vijay              <- the signature, below the whole quoted thread

Two independent defects produced that, and each is covered here:

1. ``build_signed_bodies`` appended the signature to the END of ``body_text``.
   The composer sends ONE body — new text + "\\n\\n" + quote — so the signature
   landed under the quote on EVERY signed reply, AI-drafted or hand-written.
2. ``_llm_draft_reply`` never told the model not to quote the thread back (its
   sibling ``_llm_compose_assist`` did). The ISO-8601 timestamp in that quote
   header is our own ``_fetch_thread_context`` formatting, echoed by the model.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from gateway.routes.email import quoting as qt
from gateway.routes.email.automation import drafting as dr
from gateway.routes.email.quoting import split_quoted_html, split_quoted_text
from gateway.routes.email.signature import build_signed_bodies, sign_html

_SIG = "Vijay"
_QUOTE = (
    "On 2026-07-19T14:47:23+00:00, Fracktal Finance wrote:\n"
    "> Hi,\n"
    ">\n"
    "> Please consider my half day leave by tomorrow 20th July.\n"
)
_REPLY = "Hi Suresh,\n\nNoted. Hope the appointment goes well."


# ── the split ───────────────────────────────────────────────────────────────


def test_the_reported_quote_header_is_recognised() -> None:
    """An ISO timestamp in the header is unusual, so the marker must match on
    the "On … wrote:" shape rather than on any date format."""
    main, quoted = split_quoted_text(f"{_REPLY}\n\n{_QUOTE}")
    assert main == _REPLY
    assert quoted.startswith("On 2026-07-19T14:47:23+00:00")


def test_a_bare_angle_bracket_chain_is_a_quote() -> None:
    main, quoted = split_quoted_text("Sounds good.\n\n> earlier text\n> more")
    assert main == "Sounds good."
    assert quoted == "> earlier text\n> more"


def test_from_alone_is_not_a_boundary() -> None:
    """"From:" only counts when it heads a real header block — otherwise a
    sentence that happens to start with it would truncate the message."""
    body = "Thanks!\n\nFrom: the whole team, congratulations.\n\nSee you Monday."
    main, quoted = split_quoted_text(body)
    assert quoted == ""
    assert main == body


def test_from_heading_a_header_block_is_a_boundary() -> None:
    body = ("Thanks!\n\nFrom: Suresh Nagaraj\nSent: 19 July 2026\n"
            "To: Vijay\nSubject: Leave\n\nHi,")
    main, quoted = split_quoted_text(body)
    assert main == "Thanks!"
    assert quoted.startswith("From: Suresh Nagaraj")


def test_a_body_that_is_only_a_quote_is_left_whole() -> None:
    """A forward can be nothing but the quoted mail. Splitting it would leave no
    "new text" for the signature to sit under, so don't split at all."""
    body = "> just the quote\n> and more"
    assert split_quoted_text(body) == (body, "")


def test_an_unquoted_body_is_returned_verbatim() -> None:
    main, quoted = split_quoted_text(_REPLY)
    assert (main, quoted) == (_REPLY, "")


# ── placement ───────────────────────────────────────────────────────────────


def test_signature_sits_above_the_quote() -> None:
    """The regression itself: signature between the reply and the quote."""
    text, _html = build_signed_bodies(_SIG, f"{_REPLY}\n\n{_QUOTE}")
    assert text.index(_SIG) < text.index("On 2026-07-19"), (
        "the signature is below the quoted thread again"
    )
    assert text.startswith(_REPLY)


def test_the_quote_survives_signing_verbatim() -> None:
    """Repositioning must never drop or rewrite the quoted conversation."""
    text, _html = build_signed_bodies(_SIG, f"{_REPLY}\n\n{_QUOTE}")
    for line in _QUOTE.strip().splitlines():
        assert line in text


def test_html_part_places_the_signature_the_same_way() -> None:
    """The composer sends no HTML, so the HTML part is derived from the text —
    and it had the identical bug."""
    _text, html = build_signed_bodies(_SIG, f"{_REPLY}\n\n{_QUOTE}")
    assert html is not None
    assert html.index(_SIG) < html.index("On 2026-07-19")


def test_an_unquoted_message_still_gets_the_signature_appended() -> None:
    text, html = build_signed_bodies(_SIG, "Quick note.")
    assert text == "Quick note.\n\nVijay"
    assert html is not None and _SIG in html


def test_no_signature_configured_changes_nothing() -> None:
    body = f"{_REPLY}\n\n{_QUOTE}"
    assert build_signed_bodies("", body) == (body, None)


def test_an_already_signed_body_is_not_signed_twice() -> None:
    """Re-sending a stored draft must not stack signatures — in either part."""
    body = f"{_REPLY}\n\n{_SIG}\n\n{_QUOTE}"
    text, html = build_signed_bodies(_SIG, body)
    assert text.count(_SIG) == 1
    assert html is not None and html.count(_SIG) == 1


def test_html_bodies_split_on_provider_quote_containers() -> None:
    main, quoted = split_quoted_html(
        '<div>Noted.</div><div id="appendonsend"></div><blockquote>old</blockquote>'
    )
    assert main == "<div>Noted.</div>"
    assert quoted.startswith('<div id="appendonsend"')


def test_unrecognised_html_falls_back_to_appending() -> None:
    """No known marker → behave exactly as before rather than guess."""
    raw = "<p>Just a note.</p>"
    _text, html = build_signed_bodies(_SIG, "Just a note.", raw)
    assert html is not None and html.startswith(raw)


# ── signature lives IN the draft body ───────────────────────────────────────
# Drafts are signed at draft-write time (visible in upstream Gmail/Outlook
# drafts, not only after send); send stays the idempotent safety net.


def test_append_signature_text_is_idempotent_and_quote_aware() -> None:
    from gateway.routes.email.signature import append_signature_text

    body = f"{_REPLY}\n\n{_QUOTE}"
    once = append_signature_text(_SIG, body)
    assert once.count(_SIG) == 1
    assert once.index(_SIG) < once.index("On 2026-07-19")  # above the quote
    # Re-signing (autosave loops, save-then-send) never stacks a second copy.
    assert append_signature_text(_SIG, once) == once


def test_strip_signature_text_removes_only_the_signature() -> None:
    from gateway.routes.email.signature import (
        append_signature_text,
        strip_signature_text,
    )

    signed = append_signature_text(_SIG, _REPLY)
    assert strip_signature_text(_SIG, signed) == _REPLY
    assert strip_signature_text(_SIG, _REPLY) == _REPLY  # absent → unchanged
    assert strip_signature_text("", signed) == signed    # no signature set


def test_presigned_body_sends_with_rich_html_signature() -> None:
    """A draft that already carries the PLAIN signature must still go out with
    the rendered HTML block — swapped in place, not the escaped text, and never
    doubled."""
    from gateway.routes.email.signature import append_signature_text

    sig_html = '<b>Vijay</b> · <a href="https://fracktal.in">fracktal.in</a>'
    body = append_signature_text(sig_html, "Thanks — sending now.")
    assert "Vijay · fracktal.in" in body  # the plain rendering, in the draft

    text, html = build_signed_bodies(sig_html, body)
    assert text == body  # not doubled
    assert html is not None
    assert '<a href="https://fracktal.in">' in html  # the RICH block
    assert "Vijay · fracktal.in" not in html  # not the escaped plain rendering


# ── the drafter ─────────────────────────────────────────────────────────────


def test_the_reply_drafter_forbids_quoting() -> None:
    """The rule ``_llm_compose_assist`` had and ``_llm_draft_reply`` lacked."""
    src = inspect.getsource(dr._llm_draft_reply)
    assert "NEVER quote the earlier" in src
    assert "wrote:" in src, "the quote-header example was dropped from the rule"


def test_a_model_authored_quote_is_stripped_from_the_draft() -> None:
    """Belt and braces — the prompt is an instruction, not a guarantee, and the
    client reattaches the real quote itself."""
    assert dr._clean_draft_body(f"{_REPLY}\n\n{_QUOTE}") == _REPLY


def test_cleaning_leaves_an_honest_draft_alone() -> None:
    assert dr._clean_draft_body(_REPLY) == _REPLY


def test_cleaning_still_removes_placeholder_lines() -> None:
    """The original job of _clean_draft_body must survive the addition."""
    assert "[Your Name]" not in dr._clean_draft_body(f"{_REPLY}\n\n[Your Name]")


# ── parity with the client splitter ─────────────────────────────────────────
# quoting.py and quoting.ts must agree on where a quote starts: the client
# splits the compose box, the server splits at send time, and a disagreement
# means the signature is placed relative to a boundary the user never saw.
# There is no JS unit-test runner in CI (pr-check is Python only), so these
# read the TypeScript source directly — the only enforcement available.

_QUOTING_TS = (
    Path(__file__).resolve().parents[2]
    / "workbench/control_plane/src/app/email/lib/quoting.ts"
).read_text(encoding="utf-8")


def test_the_client_splitter_also_scans_from_line_zero() -> None:
    """The flaw this port surfaced. Starting at line 1 skips the "is the whole
    body a quote?" check, so a bare forward is cut at its SECOND line: the first
    quoted line becomes "new content" — fed to the AI drafter as the user's own
    text, and left uncollapsed in the viewer."""
    assert "for (let i = 0; i < lines.length; i++)" in _QUOTING_TS, (
        "splitQuotedText no longer scans from line 0 — a body that is entirely "
        "a quote will be split mid-quote again"
    )


def test_both_splitters_recognise_the_same_markers() -> None:
    """Drift here is silent: each side keeps working, on different boundaries."""
    for fragment in (r"^>", r"\bwrote:", "Original Message", "Forwarded message",
                     r"^_{5,}", r"^From:"):
        assert fragment in _QUOTING_TS, f"marker missing from quoting.ts: {fragment}"
        assert any(fragment in rx.pattern for rx in qt._TEXT_BOUNDARY), (
            f"marker missing from quoting.py: {fragment}"
        )


# ── WS-17 EM-G3a-f7 PR-C1b: the signature in the HTML of a Gmail-web draft ──
#
# ``build_signed_bodies`` returns the HTML unchanged when the TEXT already
# holds the signature (D-G3a-f7-7). The upsert always puts it in the text, so
# the signed send of a kept HTML needs ``sign_html``.

_GMAIL_HTML = (
    '<div dir="ltr">Please find the layout.<img src="cid:ii_m1"></div>'
    '<br><div class="gmail_quote"><div>On Mon, Ravi wrote:</div>'
    "<blockquote>Can you send it?</blockquote></div>")


def test_sign_html_adds_the_signature_once_above_the_quote() -> None:
    """Acceptance 7, D-G3a-f7-7. The signature HTML goes under the new text
    and above the quote. A second call adds nothing, so a kept draft goes out
    with the signature once."""
    signed = sign_html("<b>Vijay</b> | Contoso", _GMAIL_HTML)

    assert signed.count("<b>Vijay</b> | Contoso") == 1
    new, quoted = split_quoted_html(signed)
    assert "<b>Vijay</b> | Contoso" in new
    assert new.startswith('<div dir="ltr">Please find the layout.')
    assert quoted == _GMAIL_HTML[_GMAIL_HTML.index('<div class="gmail_quote"'):]
    assert 'src="cid:ii_m1"' in signed
    assert sign_html("<b>Vijay</b> | Contoso", signed) == signed

    plain = sign_html("Vijay\nContoso", _GMAIL_HTML)
    assert plain.count("Vijay<br>Contoso") == 1
    assert plain.index("Vijay<br>Contoso") < plain.index("gmail_quote")
    assert sign_html("Vijay\nContoso", plain) == plain


def test_sign_html_finds_a_signature_that_gmail_wrote_again() -> None:
    """Gmail can write the HTML again, with other spaces or tags. The text of
    the signature still counts, so no second copy goes in."""
    rewritten = ('<div dir="ltr">Thanks.<br><br><div><b>Vijay</b>  |'
                 "\n Contoso</div></div>")
    assert sign_html("<b>Vijay</b> | Contoso", rewritten) == rewritten


def test_sign_html_reads_only_the_new_part() -> None:
    """A quote of an earlier mail of the member holds the signature too. That
    copy does not count, so the new text still gets the signature."""
    html = ("<div>Done.</div><div class=\"gmail_quote\"><blockquote>"
            "Earlier.<br><br>Vijay<br>Contoso</blockquote></div>")
    signed = sign_html("Vijay\nContoso", html)
    new, _quoted = split_quoted_html(signed)
    assert "Vijay<br>Contoso" in new
    assert signed.count("Vijay<br>Contoso") == 2


def test_sign_html_with_no_signature_or_no_html_changes_nothing() -> None:
    assert sign_html("", _GMAIL_HTML) == _GMAIL_HTML
    assert sign_html("   ", _GMAIL_HTML) == _GMAIL_HTML
    assert sign_html("Vijay", "") == ""
