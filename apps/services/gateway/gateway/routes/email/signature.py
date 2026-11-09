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
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any

from acb_common import get_logger
from gateway.routes.email.quoting import split_quoted_html, split_quoted_text
from sqlalchemy import text

_log = get_logger("gateway.email.signature")

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


# ── The stored signature, and the clean on save (WS-17 SG-1, D-EM-70) ───────

#: The read of the stored free HTML of one mailbox. The resolver below is the
#: one reader of this column (D-EM-66). No code outside this module selects
#: ``signature`` from ``email_assistant_settings``. Fence:
#: ``tests/unit/test_email_signature_resolver.py`` (a source scan).
STORED_SIGNATURE_SQL = (
    "SELECT signature FROM email_assistant_settings WHERE account_id = :aid")


async def stored_signature(db: Any, account_id: str) -> str:
    """The signature that ``email_assistant_settings`` holds now, or ``""``.

    The caller has checked the mailbox and opened ``_tenant_session``. The
    readers of the send path ask :func:`resolve_signature`, which calls this
    for a ``custom`` mailbox."""
    row = (await db.execute(text(STORED_SIGNATURE_SQL), {"aid": account_id})).fetchone()
    return (getattr(row, "signature", None) or "") if row else ""


# ── The resolver (WS-17 SG-2, D-EM-64, D-EM-66, D-EM-71) ────────────────────

#: The kinds of mail that ask for a signature (D-EM-66).
SIGNATURE_KINDS: tuple[str, ...] = ("new", "reply", "forward")
#: The modes of a mailbox signature (D-EM-64). A mailbox with no row is
#: ``custom``.
SIGNATURE_MODES: tuple[str, ...] = ("custom", "template", "company")
#: What a reply or a forward carries (D-EM-71). ``short`` is the compact
#: layout of the same fields.
REPLY_MODES: tuple[str, ...] = ("full", "short", "none")

#: The row of one mailbox in ``email_signature_settings``. RLS of the bound
#: tenant holds the org. The resolver reads it only while the flag holds the
#: org, so with the flag off the send runs the one query of main.
SETTINGS_SQL = (
    "SELECT mode, layout, fields, reply_mode FROM email_signature_settings "
    "WHERE account_id = :aid")


def templates_on(org: object = None) -> bool:
    """True when ``EMAIL_SIGNATURE_TEMPLATES_ORGS`` holds organization *org*.
    THE reader of the flag (§23.5).

    The value is a comma list of organization ids, or ``*`` for each one.
    Empty means off, the default. Then the resolver gives the ``custom``
    bytes, as main does, and the preview route answers 404. It reads the
    settings at each call, so a SIGHUP applies a change (``docs/flag_reload.md``).
    *org* defaults to the bound tenant, and no request field reaches it (R5).
    It fails closed: a fault, or no tenant under a list of ids, reads as off.
    """
    try:
        from acb_common import get_settings
        from acb_common.assistant_profile import _parse_orgs

        orgs = _parse_orgs(
            str(getattr(get_settings(), "email_signature_templates_orgs", "") or ""),
            star=True)
        if not orgs or "*" in orgs:
            return bool(orgs)
        if org is None:
            from acb_common.db import current_tenant

            org = current_tenant()
        return str(org or "").strip().lower() in orgs
    except Exception:
        return False


def _check_kind(kind: str) -> None:
    if kind not in SIGNATURE_KINDS:
        raise ValueError(f"Unknown signature kind: {str(kind)[:20]!r}")


def _in_body(signature: str, body_text: str | None) -> bool:
    """True when the body holds the text of the signature, by the test of
    :func:`build_signed_bodies`. That test decides whether a send adds one."""
    sig_text = signature_text(signature)
    return bool(sig_text) and sig_text in (body_text or "")


@dataclass(frozen=True)
class SignatureForms:
    """The signature of one mailbox, in each form that a reader asks for.

    ``stored`` is the free HTML of ``email_assistant_settings.signature``, the
    model of today's editor. ``full`` is the signature of new mail. ``short``
    is the compact form of a layout signature, or ``""`` when the mode has no
    fields. ``reply_mode`` says what a reply and a forward carry."""

    stored: str
    full: str
    short: str = ""
    reply_mode: str = "full"

    def of(self, kind: str) -> str:
        """The signature of a mail of ``kind`` (D-EM-66, D-EM-71)."""
        _check_kind(kind)
        if kind == "new" or self.reply_mode == "full":
            return self.full
        if self.reply_mode == "none":
            return ""
        return self.short or self.full

    def for_body(self, kind: str, body_text: str | None) -> str:
        """The signature that a send of ``kind`` adds to ``body_text``.

        A mail carries one signature (§23.3.3, §23.9). A draft path writes the
        ``new`` form into the body until SG-5 passes the kind of the draft. So
        when the body holds the ``new`` form and not the form of ``kind``, the
        send keeps the ``new`` form. ``build_signed_bodies`` then finds it in
        the text and adds no second one.

        The same holds for the ``stored`` form. A draft written while the
        mailbox was ``custom`` holds the stored signature. When the mode
        changes to ``template`` before the send, the send keeps the stored
        form (§23.9). With the flag off, the three forms are the same bytes,
        so the send gives the signature of main."""
        signature = self.of(kind)
        if _in_body(signature, body_text):
            return signature
        for held in (self.full, self.stored):
            if held != signature and _in_body(held, body_text):
                return held
        return signature


def _fields_of(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


async def signature_forms(
    db: Any, account_id: str, *, stored: str | None = None,
) -> SignatureForms:
    """Each form of the signature of one mailbox (D-EM-64, D-EM-66).

    The caller has checked the mailbox and holds a session of the bound
    tenant. ``stored`` is the value that the caller just wrote, so the PUT
    answer reads nothing again.

    * With the flag off, or no row, or the mode ``custom``, each form is the
      stored bytes, unchanged (rule 7). With the flag off, the one query is
      :data:`STORED_SIGNATURE_SQL`, as on main.
    * ``template`` renders the member's layout and fields with
      ``signature_render.render``. A row that does not render, or that has
      no field to show, gives the stored bytes, so a send never fails or
      loses its signature on a bad row.
    * ``company`` is SG-4. It needs the ``email_signature_brand`` key of
      ``org_settings``, and ``load_org_setting`` is sync, so SG-4 reads it
      with ``asyncio.to_thread`` under ``contextvars.copy_context().run``.
      Until then a ``company`` row gives the stored bytes.
    """
    if stored is None:
        stored = await stored_signature(db, account_id)
    custom = SignatureForms(stored=stored, full=stored)
    if not templates_on():
        return custom
    row = (await db.execute(text(SETTINGS_SQL), {"aid": account_id})).fetchone()
    if row is None:
        return custom
    reply_mode = getattr(row, "reply_mode", None)
    reply_mode = reply_mode if reply_mode in REPLY_MODES else "full"
    if getattr(row, "mode", None) != "template":
        return replace(custom, reply_mode=reply_mode)
    # Imported here, because signature_render imports this module.
    from gateway.routes.email import signature_render

    fields = _fields_of(getattr(row, "fields", None))
    try:
        full = signature_render.render(getattr(row, "layout", None), fields)[0]
        short = signature_render.render(signature_render.SHORT_LAYOUT, fields)[0]
    except signature_render.UnknownLayout:
        _log.warning("email.signature_layout_unknown")
        return replace(custom, reply_mode=reply_mode)
    if not full:
        # No field to show: the mailbox keeps its stored signature.
        return replace(custom, reply_mode=reply_mode)
    return SignatureForms(stored=stored, full=full, short=short, reply_mode=reply_mode)


async def resolve_signature(db: Any, account_id: str, kind: str) -> str:
    """The signature of a mail of ``kind`` from one mailbox (D-EM-66).

    ``kind`` is ``new``, ``reply`` or ``forward``. Each reader of §23.3.3
    calls this, or :func:`resolve_signature_for_send`, or
    :func:`signature_forms`. With the flag off or no row, it gives the stored
    bytes unchanged. The caller has checked the mailbox and holds a session
    of the bound tenant."""
    _check_kind(kind)
    return (await signature_forms(db, account_id)).of(kind)


async def resolve_signature_for_send(
    db: Any, account_id: str, kind: str, body_text: str | None,
) -> str:
    """The signature that a send of ``kind`` adds to ``body_text``.

    :meth:`SignatureForms.for_body` holds the rule: a reply whose draft holds
    the ``new`` form keeps it, so the mail carries one signature."""
    _check_kind(kind)
    return (await signature_forms(db, account_id)).for_body(kind, body_text)


def looks_html(signature: str) -> bool:
    """True when the signature is HTML. Plain text is escaped at the send
    (:func:`signature_html`), so only HTML needs the clean."""
    return _looks_html(signature)


#: The tags that a signature keeps. The editor's list in
#: ``SignatureEditor.tsx`` (``SIG_ALLOWED_TAGS``) is the same list.
_SIG_TAGS = frozenset({
    "a", "b", "strong", "i", "em", "u", "s", "strike", "span", "div", "p", "br",
    "hr", "img", "ul", "ol", "li",
    "table", "tbody", "thead", "tr", "td", "th", "colgroup", "col",
    "h1", "h2", "h3", "h4", "font", "small",
})
#: The attributes that a signature keeps, on any kept tag. The editor's list
#: (``SIG_ALLOWED_ATTR``) is the same list. Each one has a value rule below.
_SIG_ATTRS = frozenset({
    "href", "target", "rel", "src", "alt", "width", "height", "style",
    "color", "align", "border", "cellpadding", "cellspacing", "class",
    "colspan", "rowspan",
})
_VOID = frozenset({"br", "hr", "img", "col"})
#: A tag whose CONTENT goes too: it holds code, a document or a form. Each one
#: has a close tag. A void tag (``embed``, ``frame``, ``input``) is not here,
#: because no close would end the skip. It is not a kept tag, so it goes.
_DROP_WITH_CONTENT = frozenset({
    "script", "style", "template", "iframe", "object", "noscript",
    "svg", "math", "textarea", "select", "option", "title", "head", "xmp",
    "noembed", "noframes", "frameset", "applet", "audio", "video",
    "canvas", "form", "button",
})
_NUMBER = re.compile(r"\d{1,4}%?")
_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}|[a-zA-Z]{1,20}")
_ALIGN = frozenset({"left", "center", "right", "justify", "top", "middle", "bottom"})
_CLASS = re.compile(r"[A-Za-z0-9 _-]{1,200}")
_REL_WORDS = frozenset({"noopener", "noreferrer", "nofollow"})
_CSS_PROPERTY = re.compile(r"-?[a-z][a-z0-9-]{0,40}")
#: A CSS value with one of these can load a resource or run code.
_CSS_REFUSED = ("url(", "src(", "expression", "javascript:", "vbscript:", "@import",
                "behavior", "-moz-binding", "\\", "/*", "<", ">", "image(",
                "image-set(", "element(")
#: The C0 controls and DEL. A kept URL holds none of them.
_URL_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")
#: What a browser trims at each end of a URL: the C0 controls and space.
_URL_TRIM = "".join(chr(c) for c in range(0x21))
_DATA_IMAGE = re.compile(r"data:image/[a-z0-9.+-]+;base64,", re.IGNORECASE)
#: The removed addresses that a save reports, and the length of each one.
_REMOVED_MAX = 20
_REMOVED_CHARS = 120


def _clean_url(value: str) -> str | None:
    """The URL kept as a browser reads it, or ``None`` to drop it.

    Only https, ``mailto:`` and ``tel:`` stay (D-EM-70). The editor's
    ``ALLOWED_URI_REGEXP`` has the same three. A browser removes each tab and
    newline inside a URL, and trims the C0 controls and space at each end, so
    ``java&#9;script:`` reads as ``javascript:``. The check runs on that form,
    and a URL with another control is dropped."""
    url = re.sub(r"[\t\n\r]", "", value).strip(_URL_TRIM)
    if _URL_CONTROLS.search(url):
        return None
    lower = url.lower()
    if lower.startswith("https://") and len(url) > len("https://"):
        return url
    if lower.startswith(("mailto:", "tel:")):
        return url
    return None


def _clean_style(value: str) -> str | None:
    """The declarations of a ``style`` that load nothing, or ``None``."""
    kept: list[str] = []
    for part in value.split(";"):
        name, sep, val = part.partition(":")
        name, val = name.strip().lower(), val.strip()
        if not sep or not val or not _CSS_PROPERTY.fullmatch(name):
            continue
        compact = re.sub(r"\s+", "", val).lower()
        if any(bad in compact for bad in _CSS_REFUSED) or _URL_CONTROLS.search(val):
            continue
        kept.append(f"{name}: {val}")
    return "; ".join(kept) or None


def _clean_attr(name: str, value: str) -> str | None:
    """The kept value of one attribute, or ``None`` to drop it."""
    if name in ("href", "src"):
        return _clean_url(value)
    if name == "style":
        return _clean_style(value)
    if name in ("width", "height", "border", "cellpadding", "cellspacing",
                "colspan", "rowspan"):
        value = value.strip()
        return value if _NUMBER.fullmatch(value) else None
    if name == "color":
        value = value.strip()
        return value if _COLOR.fullmatch(value) else None
    if name == "align":
        value = value.strip().lower()
        return value if value in _ALIGN else None
    if name == "target":
        return "_blank" if value.strip().lower() == "_blank" else None
    if name == "rel":
        words = [w for w in value.lower().split() if w in _REL_WORDS]
        return " ".join(dict.fromkeys(words)) or None
    if name == "class":
        value = value.strip()
        return value if _CLASS.fullmatch(value) else None
    if name == "alt":
        return value[:500]
    return None


class _SignatureCleaner(HTMLParser):
    """Write a signature again through the allowlist (D-EM-70).

    The precedent is ``pdf_render._Sanitizer``. It keeps the output well
    formed: a close for a tag that is not open is dropped, and each tag still
    open at the end is closed. A ``data:image`` source that ``hosted`` maps
    takes its hosted https URL. Any other source that the URL rule refuses
    drops its ``<img>``, because an image with no source shows nothing. A
    link with a refused ``href`` keeps its text."""

    def __init__(self, hosted: Mapping[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._open: list[str] = []
        self._hosted = hosted
        #: Each link or image address that the URL rule refused, in order
        #: (fix round 1, P2): the save tells the member what went.
        self.removed: list[dict[str, str]] = []

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> list[str] | None:
        """The kept attributes, or ``None`` when the tag itself goes."""
        kept: list[str] = []
        has_src = False
        seen: set[str] = set()
        for name, value in attrs:
            if name not in _SIG_ATTRS or value is None or name in seen:
                continue
            seen.add(name)
            given = value
            if name == "src" and _DATA_IMAGE.match(value.strip()):
                value = self._hosted.get(value.strip(), "")
            clean = _clean_attr(name, value)
            if clean is None:
                if name in ("href", "src") and given.strip():
                    self._note_removed("image" if name == "src" else "link", given)
                continue
            has_src = has_src or name == "src"
            kept.append(f' {name}="{_html.escape(clean, quote=True)}"')
        if tag == "img" and not has_src:
            return None
        return kept

    def _note_removed(self, kind: str, value: str) -> None:
        """Note one refused address, at most :data:`_REMOVED_MAX` of them,
        each cut to :data:`_REMOVED_CHARS` characters."""
        shown = _URL_CONTROLS.sub("", value.strip())
        if len(shown) > _REMOVED_CHARS:
            shown = shown[: _REMOVED_CHARS - 1] + "…"
        item = {"kind": kind, "value": shown}
        if item not in self.removed and len(self.removed) < _REMOVED_MAX:
            self.removed.append(item)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_WITH_CONTENT:
            self._skip += 1
            return
        if self._skip or tag not in _SIG_TAGS:
            return
        kept = self._attrs(tag, attrs)
        if kept is None:
            return
        self.out.append(f"<{tag}{''.join(kept)}>")
        if tag not in _VOID:
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_WITH_CONTENT or self._skip or tag not in _SIG_TAGS:
            return
        kept = self._attrs(tag, attrs)
        if kept is None:
            return
        self.out.append(f"<{tag}{''.join(kept)}>")
        if tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_WITH_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip or tag not in _SIG_TAGS or tag in _VOID:
            return
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i] == tag:
                while len(self._open) > i:
                    self.out.append(f"</{self._open.pop()}>")
                return

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(_html.escape(data, quote=False))

    def finish(self) -> str:
        self.close()
        while self._open:
            self.out.append(f"</{self._open.pop()}>")
        return "".join(self.out)


class _DataImageFinder(HTMLParser):
    """Each ``data:image`` source of an ``<img>``, in order, once."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        for name, value in attrs:
            if name == "src" and value and _DATA_IMAGE.match(value.strip()):
                src = value.strip()
                if src not in self.found:
                    self.found.append(src)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def data_image_sources(signature: str) -> list[str]:
    """Each ``data:image/...;base64,`` source in the signature HTML, once.

    The save hosts each one (D-EM-70) before :func:`clean_signature_html`."""
    finder = _DataImageFinder()
    finder.feed(signature or "")
    finder.close()
    return finder.found


def clean_signature_report(
    signature: str, *, hosted: Mapping[str, str] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """:func:`clean_signature_html`, and each address that it removed.

    Each item is ``{"kind": "link" | "image", "value": <the address>}``. The
    settings save answers them as ``signature_removed``, so the dialog can say
    "We removed 2 items that do not use https" (fix round 1, P2)."""
    cleaner = _SignatureCleaner(hosted or {})
    cleaner.feed(signature or "")
    return cleaner.finish(), cleaner.removed


def clean_signature_html(
    signature: str, *, hosted: Mapping[str, str] | None = None,
) -> str:
    """The signature HTML through the server allowlist (D-EM-70).

    It keeps the tags and the attributes of ``SignatureEditor.tsx``. Each
    ``href`` and ``src`` must be https, ``mailto:`` or ``tel:``. Script, each
    ``on*`` attribute, a ``style`` that loads a resource, a comment, and a
    ``javascript:``, ``http:`` or ``data:`` URL go. ``hosted`` maps a
    ``data:image`` source to its hosted URL. A second run gives the same text.
    """
    return clean_signature_report(signature, hosted=hosted)[0]
