"""Outbound HTML-signature assembly.

The account signature is stored as HTML (authored in the workbench's hybrid
rich-text / HTML editor). The signature lives IN the draft body: every
draft-write path appends the plain-text rendering to the body via
:func:`append_signature_text`, so a draft synced upstream (Gmail / Outlook)
already shows the signature — it used to be added only at send time, so
upstream drafts looked unsigned. Send remains the safety net: it appends the
signature if a (legacy / hand-made) draft still lacks it, and upgrades an
in-body plain signature to the rendered HTML block with a plain-text fallback,
so mail goes out ``multipart/alternative`` and renders in every client. Kept in
ONE place so every path produces the signature identically.

The AI drafter itself still returns the reply body only — the signature is
appended once, deterministically, by the draft/send paths here, never by the
model (which would double or mangle it).

The signature goes directly under the NEW text, ABOVE any quoted trailing
thread. The composer sends one ``body_text`` containing both (new text + "\\n\\n"
+ quote), so appending to the end of it put the signature underneath the whole
quoted conversation on every signed reply — see :mod:`.quoting` for the split.
"""
from __future__ import annotations

import html as _html
import re

from gateway.routes.email.quoting import split_quoted_html, split_quoted_text

# A value that contains an HTML tag is treated as HTML; otherwise it's plain
# text the user typed and we escape + linebreak it.
_LOOKS_HTML_RE = re.compile(r"<[a-zA-Z!/][^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")


def _looks_html(s: str) -> bool:
    return bool(_LOOKS_HTML_RE.search(s or ""))


def _text_to_html(s: str) -> str:
    """Escape plain text and turn newlines into <br> for an HTML part."""
    return _html.escape(s or "").replace("\n", "<br>")


def html_to_text(s: str) -> str:
    """A crude but serviceable plain-text rendering of HTML: block/break tags
    become newlines, remaining tags are stripped, entities are unescaped."""
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s or "")
    s = re.sub(r"(?i)</\s*(p|div|tr|li|h[1-6]|table)\s*>", "\n", s)
    s = _TAG_RE.sub("", s)
    s = _html.unescape(s)
    # Collapse the runs of blank lines a table/markup strip can leave behind.
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def signature_html(signature: str) -> str:
    """The signature as an HTML fragment (HTML kept as-is; plain text escaped)."""
    sig = (signature or "").strip()
    if not sig:
        return ""
    return sig if _looks_html(sig) else _text_to_html(sig)


def signature_text(signature: str) -> str:
    """The signature as plain text (tags stripped when it's HTML)."""
    sig = (signature or "").strip()
    if not sig:
        return ""
    return html_to_text(sig) if _looks_html(sig) else sig


def append_signature_text(signature: str, body_text: str) -> str:
    """The plain-text half of :func:`build_signed_bodies`: the body with the
    signature's text inserted under the new text, above any quoted thread.

    Idempotent — a body already carrying the signature text is returned
    unchanged — so every draft-write path can call it unconditionally and a
    reopened/re-saved draft never accumulates copies."""
    return build_signed_bodies(signature, body_text, None)[0]


def strip_signature_text(signature: str, body_text: str) -> str:
    """Remove the signature's plain-text block from a body (first occurrence),
    collapsing the blank run it leaves behind. Returns the body unchanged when
    there's no signature or it isn't present.

    Used where the signature must NOT take part: the compose-assist AI sees the
    owner's words only (a signature-only body is an EMPTY draft, not text to
    improve), and edit-learning diffs what the user wrote, not the boilerplate
    both sides carry."""
    sig_text = signature_text(signature)
    body = body_text or ""
    if not sig_text or sig_text not in body:
        return body
    idx = body.find(sig_text)
    out = body[:idx] + body[idx + len(sig_text):]
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _squash(s: str) -> str:
    """Text with each run of white space as one space, for a compare."""
    return " ".join((s or "").split())


def _holds_signature(signature: str, html: str) -> bool:
    """True when ``html`` already holds the signature, as HTML or as text.

    The text compare ignores white space, because Gmail can write the HTML
    of a draft again with other spaces or tags."""
    if signature_html(signature) in html:
        return True
    sig_text = _squash(signature_text(signature))
    return bool(sig_text) and sig_text in _squash(html_to_text(html))


def sign_html(signature: str, body_html: str) -> str:
    """The HTML of a draft with the signature HTML under the new text.

    WS-17 EM-G3a-f7 (D-G3a-f7-7). The signed send of a Gmail-web draft keeps
    the HTML of the draft. :func:`build_signed_bodies` cannot sign it,
    because it returns the HTML unchanged when the TEXT holds the signature,
    and the upsert always puts the signature in the text.

    The signature goes above the quote (:func:`split_quoted_html`), where
    :func:`build_signed_bodies` puts it. When the new part already holds the
    signature, nothing changes. Only the new part counts, because a quote of
    an earlier mail of the member holds the signature too. With no signature
    or no HTML, the HTML comes back unchanged."""
    sig_html = signature_html(signature)
    if not sig_html or not (body_html or "").strip():
        return body_html
    main_html, quoted_html = split_quoted_html(body_html)
    if _holds_signature(signature, main_html):
        return body_html
    return f"{main_html}<br><br>{sig_html}{quoted_html}"


def build_signed_bodies(
    signature: str, body_text: str, body_html: str | None = None,
) -> tuple[str, str | None]:
    """Append the signature to a message in both representations.

    The signature is inserted directly under the NEW text and ABOVE any quoted
    trailing thread, which is where every mail client puts it. A reply arrives
    here as ONE ``body_text`` holding both parts, so a plain append pushed the
    signature below the entire quoted conversation.

    Returns ``(body_text, body_html)``:
      * ``body_text`` — new text + plain-text signature + the quote (deduped:
        the whole insert is skipped if the signature text is already present,
        e.g. an older draft that still carries it).
      * ``body_html`` — the caller's HTML (or the plain body rendered to HTML)
        with the signature HTML block in the same position.

    With no signature set, returns the inputs unchanged — ``body_html`` stays
    ``None`` so a plain-text-only send is preserved exactly as before.
    """
    sig = (signature or "").strip()
    if not sig:
        return body_text, body_html

    sig_text = signature_text(sig)
    sig_html = signature_html(sig)
    body_text = body_text or ""

    # Already signed (drafts now carry the signature in-body): leave the text
    # alone AND never add a second block. When the HTML part must be derived,
    # swap the in-body PLAIN signature for the rendered HTML block in place, so
    # a signed draft still goes out with the rich signature (links, formatting)
    # rather than its escaped plain-text rendering.
    if sig_text and sig_text in body_text:
        if body_html and body_html.strip():
            return body_text, body_html
        idx = body_text.find(sig_text)
        pre = body_text[:idx].rstrip("\n")
        post = body_text[idx + len(sig_text):].strip("\n")
        out_html = f"{_text_to_html(pre)}<br><br>{sig_html}" if pre else sig_html
        if post:
            out_html += f"<br><br>{_text_to_html(post)}"
        return body_text, out_html

    main_text, quoted_text = split_quoted_text(body_text)
    out_text = (
        f"{main_text.rstrip()}\n\n{sig_text}" if main_text.strip() else sig_text
    )
    if quoted_text:
        out_text = f"{out_text}\n\n{quoted_text}"

    if body_html and body_html.strip():
        main_html, quoted_html = split_quoted_html(body_html)
        out_html = f"{main_html}<br><br>{sig_html}{quoted_html}"
    else:
        # Derived from the plain body — render the two halves separately so the
        # signature keeps its place above the quote here too.
        out_html = f"{_text_to_html(main_text)}<br><br>{sig_html}"
        if quoted_text:
            out_html += f"<br><br>{_text_to_html(quoted_text)}"
    return out_text, out_html
