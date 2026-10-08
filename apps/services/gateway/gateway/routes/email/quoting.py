"""Quoted / trailing-mail detection — the Python counterpart of the frontend's
``email/lib/quoting.ts``.

A reply carries the whole earlier conversation quoted underneath the new text.
Two server-side jobs need to know where that boundary is:

* **Signing** — the account signature belongs directly under the *new* text, not
  at the very bottom of the message. Appending it to the end put it below the
  quoted thread on every signed reply.
* **Drafting** — the AI drafter is told never to quote the thread back; this is
  the belt-and-braces strip for when it does anyway.

The markers mirror ``TEXT_BOUNDARY`` / ``findQuoteBoundary`` in quoting.ts, so
the client and the server agree on where a quote starts. Both splitters are
conservative: anything they can't confidently split is returned whole, because
losing the user's own words is far worse than a misplaced signature.
"""
from __future__ import annotations

import re

# Plain-text markers that begin a quoted block (quoting.ts TEXT_BOUNDARY).
_TEXT_BOUNDARY: tuple[re.Pattern[str], ...] = (
    re.compile(r"^>"),
    re.compile(r"^\s*On\b.+\bwrote:\s*$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^_{5,}\s*$"),
    re.compile(r"^From:\s.+\S", re.IGNORECASE),
)
_FROM_LINE = re.compile(r"^From:\s", re.IGNORECASE)
_HEADER_AHEAD = re.compile(r"\n(Sent|Date|To|Subject):", re.IGNORECASE)


def split_quoted_text(text: str) -> tuple[str, str]:
    """Split a plain-text body into ``(new_text, quoted_trailing)``.

    ``quoted_trailing`` is "" when there is no quote, when the quote would start
    at line 0 (a forward that is *only* a quote), or when nothing meaningful is
    left above it — in those cases ``new_text`` is the input verbatim.
    """
    lines = (text or "").split("\n")
    idx = -1
    # Scanned from line 0, unlike quoting.ts (which starts at 1). If the FIRST
    # line is already inside a quote the whole body is one — the ``idx < 1``
    # guard below then returns it whole, instead of cutting between two quoted
    # lines and signing in the middle of somebody else's email.
    for i in range(len(lines)):
        line = lines[i]
        if not any(rx.match(line) for rx in _TEXT_BOUNDARY):
            continue
        # "From:" alone is a weak signal — only a quote boundary when it heads a
        # header block (a nearby Sent/Date/To/Subject line follows). Otherwise a
        # sentence like "From: the team" would truncate the message.
        if _FROM_LINE.match(line):
            ahead = "\n" + "\n".join(lines[i:i + 5])
            if not _HEADER_AHEAD.search(ahead):
                continue
        idx = i
        break
    if idx < 1:
        return text, ""
    main = "\n".join(lines[:idx]).rstrip()
    if not main.strip():
        return text, ""
    quoted = "\n".join(lines[idx:]).strip()
    return (main, quoted) if quoted else (text, "")


# HTML containers that begin a quoted block, most reliable first (quoting.ts
# findQuoteBoundary). Regex rather than a DOM parse: the gateway has no HTML
# parser dependency, and an unrecognised body simply falls through unsplit.
_HTML_BOUNDARY: tuple[re.Pattern[str], ...] = (
    re.compile(r"<div[^>]*\bid=[\"']?appendonsend\b", re.IGNORECASE),      # Outlook web
    re.compile(r"<div[^>]*\bid=[\"']?divRplyFwdMsg\b", re.IGNORECASE),     # Outlook desktop
    re.compile(r"<div[^>]*\bclass=[\"'][^\"']*gmail_quote", re.IGNORECASE),
    re.compile(r"<div[^>]*\bclass=[\"'][^\"']*moz-cite-prefix", re.IGNORECASE),
    re.compile(r"<blockquote[^>]*\btype=[\"']?cite", re.IGNORECASE),
    re.compile(r"<blockquote\b", re.IGNORECASE),
)
# Outlook draws a divider above the quote header; pull it into the quote so the
# signature doesn't land between the rule and the thread it belongs to.
_HR_BEFORE = re.compile(r"<hr\b[^>]*>(?:\s|<br\s*/?>|<div[^>]*>|</div>)*$", re.IGNORECASE)


def split_quoted_html(raw: str) -> tuple[str, str]:
    """Split an HTML body into ``(new_html, quoted_html)``.

    Same contract as :func:`split_quoted_text`: returns ``("", …)`` never — on
    anything it cannot split it returns ``(raw, "")``. A boundary at position 0
    is ignored (the body would be nothing but a quote).
    """
    if not (raw or "").strip():
        return raw, ""
    cut = -1
    for rx in _HTML_BOUNDARY:
        m = rx.search(raw)
        if m:
            cut = m.start()
            break
    if cut <= 0:
        return raw, ""
    head = raw[:cut]
    hr = _HR_BEFORE.search(head)
    if hr:
        cut = hr.start()
        head = raw[:cut]
    # Nothing but markup above the quote → don't split (an empty "new" part would
    # put the signature at the top of a bare forward).
    if not re.sub(r"<[^>]*>", "", head).replace("&nbsp;", " ").strip() \
            and "<img" not in head.lower():
        return raw, ""
    return head, raw[cut:]


# ── The text the email agent reads (WS-17, 2026-10-09) ───────────────────────
#
# 🔴 **Why.** The email agent's ``read_email`` gave the model the first 4,000
# characters of a body, with the quoted thread, the signature and the legal
# footer in it. The model reads each quoted message again on each read, and a
# chat turn sends the earlier tool results again. :func:`strip_for_reading`
# keeps the new text only. ``read_email(full=True)`` still reads the whole body.
#
# ⚠️ **Conservative, as the splitters above.** Each step keeps the input when
# its cut would leave nothing, and a signature cut needs a contact line, so a
# short reply that ends "Thanks, Priya" keeps every word.

#: The title of a legal footer, at the start of a paragraph.
_DISCLAIMER_TITLE = re.compile(
    r"^\s*[*_\[(]*\s*(?:"
    r"(?:legal\s+)?disclaimer\b|confidentiality\b"
    r"|confidential\s+(?:notice|note|statement)\b"
    r"|privileged\s+(?:and|&)\s+confidential\b|important\s+notice\b"
    r"|notice\s+of\s+confidentiality\b"
    r"|please\s+consider\s+the\s+environment\b"
    r")",
    re.IGNORECASE,
)
#: The first words of a legal footer with no title. Such a paragraph must
#: also be long (:data:`_DISCLAIMER_MIN_CHARS`), so a short "This message is
#: confidential, so do not forward it." in a reply stays.
_DISCLAIMER_SENTENCE = re.compile(
    r"^\s*[*_\[(]*\s*(?:"
    r"this\s+(?:e-?mail|message|communication|transmission)\b"
    r"|the\s+(?:information|content)s?\s+(?:contained\s+)?in\s+this\s+"
    r"(?:e-?mail|message|communication)"
    r"|if\s+you\s+(?:are\s+not|have\s+received\s+this)"
    r")",
    re.IGNORECASE,
)
_DISCLAIMER_MIN_CHARS = 120
#: A footer paragraph names one of these. "This email is to confirm our
#: meeting" names none, so it stays.
_DISCLAIMER_WORDS = re.compile(
    r"confidential|privileged|intended\s+(?:solely|only|recipient)|unauthori[sz]ed"
    r"|prohibited|disclos|virus|liabilit|legally|delete\s+(?:it|this)"
    r"|before\s+printing|notify\s+the\s+sender",
    re.IGNORECASE,
)
#: A footer that a mail client adds. Named clients only: "Sent via DHL on
#: Monday" is a sentence, and it stays.
_MOBILE_FOOTER = re.compile(
    r"^\s*(?:sent\s+from\s+my\s+(?:iphone|ipad|android|samsung|galaxy|pixel"
    r"|blackberry|huawei|mobile|smartphone|phone)\b.{0,30}"
    r"|sent\s+from\s+(?:outlook|mail|yahoo\s+mail|gmail|proton\s*mail)"
    r"(?:\s+for\s+\S+(?:\s+\S+)?)?"
    r"|get\s+outlook\s+for\s+(?:ios|android))\s*$",
    re.IGNORECASE,
)
#: The signature line of RFC 3676: two dashes and an optional space.
_SIG_DASHES = re.compile(r"^--\s?$")
#: A closing line, alone on its line.
_SIGN_OFF = re.compile(
    r"^\s*(?:(?:best|kind|warm|warmest|many|with)\s+)?"
    r"(?:regards|wishes|thanks|thank\s+you|cheers|best|sincerely|warmly|rgds|br"
    r"|yours(?:\s+(?:truly|sincerely|faithfully))?)[\s,.!]*$",
    re.IGNORECASE,
)
#: A line that only a signature holds: a phone number, a web address, a mail
#: address, or a "|" between fields.
_CONTACT_LINE = re.compile(
    r"(?:\+?\d[\d\s().-]{6,}\d)|https?://|\bwww\.|[\w.+-]+@[\w-]+\.[\w.]+|\s\|\s",
    re.IGNORECASE,
)
#: A signature block after a closing line holds at most this many lines.
_SIG_MAX_LINES = 12


def _keep(before: str, after: str) -> str:
    """``after`` when it still holds text, else ``before``."""
    return after if after.strip() else before


def _paragraphs(lines: list[str]) -> list[int]:
    """The index of the first line of each paragraph."""
    starts: list[int] = []
    blank = True
    for i, line in enumerate(lines):
        if line.strip() and blank:
            starts.append(i)
        blank = not line.strip()
    return starts


def strip_disclaimer(text: str) -> str:
    """Cut a legal footer, from its first paragraph to the end.

    The footer paragraph must start with a title (:data:`_DISCLAIMER_TITLE`)
    or with the first words of a long footer (:data:`_DISCLAIMER_SENTENCE`),
    and it must name a legal word (:data:`_DISCLAIMER_WORDS`). The first
    paragraph is never a footer, so the cut always keeps the new text.
    """
    lines = (text or "").split("\n")
    for start in _paragraphs(lines)[1:]:
        titled = bool(_DISCLAIMER_TITLE.match(lines[start]))
        if not titled and not _DISCLAIMER_SENTENCE.match(lines[start]):
            continue
        para: list[str] = []
        for line in lines[start:]:
            if not line.strip():
                break
            para.append(line)
        body = " ".join(para)
        if titled:
            # A title can stand on its own line, above the legal text.
            body = " ".join(lines[start:start + 12])
        elif len(body) < _DISCLAIMER_MIN_CHARS:
            continue
        if _DISCLAIMER_WORDS.search(body):
            return _keep(text, "\n".join(lines[:start]).rstrip())
    return text


def strip_signature(text: str) -> str:
    """Cut the signature block at the end of a body.

    Three shapes, each cut only below the first line:

    - a mobile footer ("Sent from my iPhone") and what follows it;
    - the RFC 3676 line ``-- `` and what follows it;
    - after a closing line ("Best regards,") and the name under it, a block
      of at most :data:`_SIG_MAX_LINES` lines that holds a contact line
      (:data:`_CONTACT_LINE`). The closing line and the name stay, so the
      reader still sees who wrote it.
    """
    lines = (text or "").rstrip().split("\n")
    for i in range(1, len(lines)):
        if _MOBILE_FOOTER.match(lines[i]) or _SIG_DASHES.match(lines[i]):
            return _keep(text, "\n".join(lines[:i]).rstrip())
    content = [i for i, line in enumerate(lines) if line.strip()]
    for i in reversed(content[-_SIG_MAX_LINES - 2:]):
        if i == content[0] or not _SIGN_OFF.match(lines[i]):
            continue
        name = next((j for j in content if j > i), None)
        if name is None:
            return text
        tail = [lines[j] for j in content if j > name]
        if tail and len(tail) <= _SIG_MAX_LINES \
                and any(_CONTACT_LINE.search(t) for t in tail):
            return _keep(text, "\n".join(lines[:name + 1]).rstrip())
        return text
    return text


def strip_for_reading(text: str) -> str:
    """The new text of a plain-text body, for a model to read.

    The quoted thread goes first (:func:`split_quoted_text`), then the legal
    footer, then the signature. Each step keeps its input when its cut leaves
    nothing. The one seam for this: the email agent's default ``read_email``
    reaches it through ``GET /email/messages/{id}?trim=true``.
    """
    main = split_quoted_text(text or "")[0]
    main = strip_disclaimer(main)
    return strip_signature(main)
