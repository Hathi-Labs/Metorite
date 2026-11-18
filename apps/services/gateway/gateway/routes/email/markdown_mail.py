"""Markdown to the two parts of a mail (task 3605, RC-CHAT).

The email assistant writes the body of a mail in Markdown (owner ask,
2026-11-17: headings, lists, bold, italics). :func:`mail_bodies` is the ONE
converter. It gives the plain text part and the HTML part of the mail.

* **The text part is the Markdown as written.** It never loses a character.
  Only the bidi controls go (:data:`_BIDI`), because they can turn the text
  that a reader sees around. The text part is also what a held chat sends
  and what a draft holds.
* **HTML only for real structure.** A heading, a list, a table, a quote, a
  code block, a rule, inline code, an explicit ``[words](address)`` link, or
  bold, italics or strikethrough with its marks at word edges. A body with none of these
  gets no HTML, so ``2*3*4``, ``__init__`` and ``Jane <jane@x.test>`` go out
  byte for byte as text. A mark inside a word (``a*b*c``) and ``__`` stay as
  typed in the HTML too.
* The parser is ``markdown-it-py``, the Markdown library of the gateway
  (``pdf_render.markdown_to_html`` uses it too). Raw HTML is OFF, so a tag in
  the Markdown stays text. A character reference stays text too (the
  ``entity`` rule is off), so ``&#8203;`` never becomes a hidden character.
* The writer below emits only the tags that the mail allowlist keeps, each
  with an inline style, because Gmail and Outlook drop a ``<style>`` block. A
  quote and a code block become a ``div``, and inline code a ``span``. A
  Markdown image becomes its alt text, so a mail from the chat loads nothing.
* The HTML then goes through ``signature.clean_signature_html``, the server
  allowlist of mail HTML (D-EM-70). It is the one sanitizer: it keeps an
  ``https:``, ``mailto:`` or ``tel:`` link and drops a ``style`` that loads a
  resource. Each link asks it first. A link that it refuses shows its words
  and its address as text, with no link.
* A link whose words LOOK LIKE an address never points to another address
  (:func:`_label_misleads`). It shows its words and its address as text. A
  link whose words are plain words (``[Pay the invoice](…)``) stays a link,
  and its address shows only where the mail client shows a link address.
* A body nested to one level below the parser's limit gets no HTML
  (:func:`_too_deep`), because the parser drops content past that limit. A
  fault of the converter gives no HTML too, and the mail goes as text.

The signature is not added here. ``signature.build_signed_bodies`` adds it
once to each part, at the send. A Markdown send has no quoted thread: the
model writes the new text, and the provider adds the thread. So the send
joins the signature at the end of both parts (``quoted_thread=False``), and a
Markdown quote stays above it. Fence: ``tests/unit/test_email_markdown_mail.py``.
"""
from __future__ import annotations

import html as _html
import re
import unicodedata
from functools import lru_cache
from typing import Any, NamedTuple
from urllib.parse import unquote

from acb_common import get_logger
from gateway.routes.email.signature import clean_signature_html

_log = get_logger("gateway.email.markdown")

#: The inline style of each element. A mail client keeps an inline style, and
#: each value passes ``signature._clean_style``.
_FONT_MONO = "font-family: Consolas, Menlo, monospace"
_STYLES: dict[str, str] = {
    "h1": "margin: 16px 0 8px 0; font-size: 22px; font-weight: bold; line-height: 1.3",
    "h2": "margin: 16px 0 8px 0; font-size: 18px; font-weight: bold; line-height: 1.3",
    "h3": "margin: 12px 0 6px 0; font-size: 16px; font-weight: bold; line-height: 1.3",
    "p": "margin: 0 0 12px 0",
    "list": "margin: 0 0 12px 0; padding-left: 24px",
    "nested_list": "margin: 4px 0 0 0; padding-left: 24px",
    "li": "margin: 0 0 4px 0",
    "table": "margin: 0 0 12px 0; border-collapse: collapse",
    "th": ("border: 1px solid #cccccc; padding: 6px 10px; "
           "background-color: #f3f3f3; font-weight: bold"),
    "td": "border: 1px solid #cccccc; padding: 6px 10px",
    "quote": ("margin: 0 0 12px 0; padding: 0 0 0 12px; "
              "border-left: 3px solid #cccccc; color: #555555"),
    "pre": (f"margin: 0 0 12px 0; padding: 8px 10px; background-color: #f6f8fa; "
            f"{_FONT_MONO}; font-size: 13px; white-space: pre-wrap"),
    "code": f"{_FONT_MONO}; background-color: #f6f8fa",
    "hr": "border: 0; border-top: 1px solid #cccccc; margin: 12px 0",
}
#: A Markdown heading of level 4 to 6 shows as a level 3 heading.
_HEADING_TAGS = {"h1": "h1", "h2": "h2"}
_ALIGN = re.compile(r"text-align:\s*(left|center|right)")
#: The block types that are real structure.
_STRUCTURE_BLOCKS = frozenset({
    "heading", "bullet_list", "ordered_list", "table", "blockquote", "fence", "hr"})
#: The bidi controls U+202A to U+202E and U+2066 to U+2069. They go from both
#: parts, because they can turn around the text that a reader sees.
_BIDI = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
#: Words that look like an address: a scheme, ``www.``, an ``@``, a dotted
#: host such as ``bank.test/pay``, or an IPv4 address. A host can hold any
#: letter, so a label with a Cyrillic look-alike letter is an address too.
#: The words are folded first (:func:`_fold`).
_ADDRESS_FORM = re.compile(
    r"://|www\.|@|[\w-]+\.[^\W\d_]{2,}|\b\d{1,3}(?:\.\d{1,3}){3}\b", re.IGNORECASE)
_SCHEME = re.compile(r"^(?:https?://|mailto:|tel:)", re.IGNORECASE)
#: The text that touches a link and continues an address: a run with no
#: space that ends in ``.``, ``/`` or ``@`` before it, and a run that starts
#: with ``.`` and a letter or digit, ``/`` or ``@`` after it. A full stop at
#: the end of a sentence does not continue an address.
_TAIL = re.compile(r"\S*[./@]$")
_HEAD = re.compile(r"^(?:\.[^\W_]|[/@])\S*")


class MailBodies(NamedTuple):
    """The two parts of a mail. ``html`` is ``None`` for a body that gets no
    HTML. ``text`` is the body as written, less the bidi controls."""

    text: str
    html: str | None


@lru_cache(maxsize=1)
def _parser() -> Any:
    """CommonMark with tables and strikethrough. Raw HTML, character
    references, Setext headings, indented code and link reference
    definitions are off: each one turns plain prose into something else
    (``Regards,`` over ``--`` would be a heading, and an indented line code)."""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True,
                                   "typographer": False})
    md.enable(["table", "strikethrough"])
    md.disable(["lheading", "code", "entity", "reference"])
    return md


def _tree(tokens: list[Any]) -> Any:
    from markdown_it.tree import SyntaxTreeNode

    return SyntaxTreeNode(tokens)


# ── Real structure ──────────────────────────────────────────────────────────

def _at_word_edge(node: Any) -> bool:
    """True when the marks of an emphasis node do not touch a letter or a
    digit outside them: ``*note*`` yes, ``2*3*4`` and ``a**b**c`` no."""
    before, after = node.previous_sibling, node.next_sibling
    if before is not None and before.type == "text" and before.content[-1:].isalnum():
        return False
    return not (after is not None and after.type == "text" and after.content[:1].isalnum())


def _literal_marks(node: Any) -> bool:
    """True when an emphasis node shows as typed: its marks touch a word, or
    it is ``__`` (``__init__`` is a name, not bold)."""
    return node.markup.startswith("_" * 2) or not _at_word_edge(node)


def _inline_structure(node: Any) -> bool:
    for child in node.children:
        if child.type in ("strong", "em", "s") and not _literal_marks(child):
            return True
        if child.type == "code_inline" or (child.type == "link" and child.markup != "autolink"):
            return True
        if child.children and _inline_structure(child):
            return True
    return False


def _has_structure(root: Any) -> bool:
    """True when the body holds real structure (see the module docstring)."""
    for block in root.children:
        if block.type in _STRUCTURE_BLOCKS:
            return True
        if any(_inline_structure(c) for c in block.children):
            return True
    return False


def _too_deep(tokens: list[Any]) -> bool:
    """True when a block token reaches one level below the parser's limit.

    The parser (``maxNesting``, 20) stops a block that it enters at the
    limit, and it drops the rest of that block. It enters such a block only
    after it opens a token at the limit less one. So no token at that level
    means that the parse dropped nothing. A list nested 9 deep, or a quote
    nested 18 deep, reaches it."""
    limit = int(_parser().options["maxNesting"])
    return any(token.level >= limit - 1 for token in tokens)


# ── The HTML part ───────────────────────────────────────────────────────────

#: THE sanitizer of the HTML part: the server allowlist of mail HTML. Each
#: link asks it first, and the whole part goes through it last.
_sanitize = clean_signature_html


def _link_kept(href: str) -> bool:
    """True when the mail allowlist keeps a link to ``href`` (escaped). The
    sanitizer itself decides, so its link rule has no copy here. A link that
    it refuses (``http:``, ``data:``) shows its words and its address as
    text, so the reader still sees where it points."""
    return ' href="' in _sanitize(f'<a href="{href}">.</a>')


def _fold(words: str) -> str:
    """The words as a reader sees them: NFKC folds a look-alike dot (U+2024,
    U+FE52, U+FF0E, U+FF61) and a wide letter, U+3002 becomes ``.``, and
    each format character (zero-width space, word joiner, soft hyphen, bidi
    control) goes."""
    folded = unicodedata.normalize("NFKC", words).replace("\u3002", ".")
    return "".join(c for c in folded if unicodedata.category(c) != "Cf")


def _address_key(value: str) -> str:
    """An address for a compare: no scheme, no ``www.``, no end slash, lower
    case, with each percent escape read."""
    key = _SCHEME.sub("", unquote(value).strip().lower())
    key = key[len("www."):] if key.startswith("www.") else key
    return key.rstrip("/")


def _label_misleads(words: str, href: str) -> bool:
    """True when the words of a link look like an address and are not the
    address of the link. ``[https://acme.test/pay](https://evil.test/pay)``
    would show one address and go to another, so it shows both as text. A
    link whose words are plain words is not this case."""
    folded = _fold(words)
    return bool(_ADDRESS_FORM.search(folded)) and _address_key(folded) != _address_key(href)


def _text_at(node: Any, pattern: re.Pattern[str]) -> str:
    """The run with no space that ``pattern`` takes from a text node, or ""."""
    if node is None or node.type != "text":
        return ""
    found = pattern.search(node.content)
    return found.group() if found else ""


def _link_misleads(node: Any, words: str, href: str) -> bool:
    """:func:`_label_misleads` for the words of a link, and for its words
    with the text that touches them on each side. ``[acme](https://evil.test)
    .com/pay`` reads as ``acme.com/pay``, with only ``acme`` as a link."""
    if _label_misleads(words, href):
        return True
    before = _text_at(node.previous_sibling, _TAIL)
    after = _text_at(node.next_sibling, _HEAD)
    return bool(before or after) and _label_misleads(f"{before}{words}{after}", href)


def _esc(value: str) -> str:
    return _html.escape(value, quote=False)


def _open(tag: str, style_key: str, extra: str = "") -> str:
    style = _STYLES[style_key] + extra
    return f'<{tag} style="{_html.escape(style, quote=True)}">'


def _inline_html(node: Any) -> str:
    out: list[str] = []
    for child in node.children:
        kind = child.type
        if kind == "text":
            out.append(_esc(child.content))
        elif kind in ("softbreak", "hardbreak"):
            out.append("<br>")
        elif kind in ("strong", "em", "s"):
            if _literal_marks(child):
                out.append(f"{_esc(child.markup)}{_inline_html(child)}{_esc(child.markup)}")
            else:
                out.append(f"<{kind}>{_inline_html(child)}</{kind}>")
        elif kind == "code_inline":
            out.append(f"{_open('span', 'code')}{_esc(child.content)}</span>")
        elif kind == "link":
            raw = str(child.attrs.get("href") or "")
            href = _html.escape(raw, quote=True)
            if _link_kept(href) and not _link_misleads(child, _inline_text(child), raw):
                out.append(f'<a href="{href}">{_inline_html(child)}</a>')
            else:
                out.append(_esc(_link_text(child)))
        elif kind == "image":
            # The alt text only: a mail from the chat loads no image.
            out.append(_esc(_inline_text(child)))
        else:
            out.append(_esc(child.content or ""))
    return "".join(out)


def _link_text(node: Any) -> str:
    """A link as text: its words and its address, or the address alone when
    the words are the address."""
    words = _inline_text(node)
    href = str(node.attrs.get("href") or "")
    address = href[len("mailto:"):] if href.lower().startswith("mailto:") else href
    if words and address and address != words:
        return f"{words} ({address})"
    return words or address


def _inline_text(node: Any) -> str:
    out: list[str] = []
    for child in node.children:
        kind = child.type
        if kind in ("softbreak", "hardbreak"):
            out.append("\n")
        elif kind == "link":
            out.append(_link_text(child))
        elif child.children:
            out.append(_inline_text(child))
        else:
            out.append(child.content or "")
    return "".join(out)


def _cell_align(node: Any) -> str:
    match = _ALIGN.search(str(node.attrs.get("style") or ""))
    return f"; text-align: {match.group(1) if match else 'left'}"


def _blocks_html(nodes: Any, depth: int = 0) -> str:
    out: list[str] = []
    for node in nodes:
        kind = node.type
        if kind == "paragraph":
            body = "".join(_inline_html(c) for c in node.children)
            out.append(body if node.hidden else f"{_open('p', 'p')}{body}</p>")
        elif kind == "inline":
            out.append(_inline_html(node))
        elif kind == "heading":
            tag = _HEADING_TAGS.get(node.tag, "h3")
            body = "".join(_inline_html(c) for c in node.children)
            out.append(f"{_open(tag, tag)}{body}</{tag}>")
        elif kind in ("bullet_list", "ordered_list"):
            key = "nested_list" if depth else "list"
            start = int(node.attrs.get("start") or 1)
            if kind == "ordered_list" and start != 1:
                # The allowlist keeps no ``start``, so a list that starts at
                # another number writes each number itself.
                items = "".join(
                    f"{_open('li', 'li')}{start + i}. {_blocks_html(item.children, depth + 1)}</li>"
                    for i, item in enumerate(node.children))
                out.append(f"{_open('ul', key, '; list-style-type: none')}{items}</ul>")
                continue
            tag = "ul" if kind == "bullet_list" else "ol"
            out.append(f"{_open(tag, key)}{_blocks_html(node.children, depth + 1)}</{tag}>")
        elif kind == "list_item":
            out.append(f"{_open('li', 'li')}{_blocks_html(node.children, depth + 1)}</li>")
        elif kind == "blockquote":
            out.append(f"{_open('div', 'quote')}{_blocks_html(node.children, depth)}</div>")
        elif kind == "fence":
            lines = node.content.rstrip("\n").split("\n")
            out.append(f"{_open('div', 'pre')}{'<br>'.join(_esc(x) for x in lines)}</div>")
        elif kind == "hr":
            out.append(_open("hr", "hr"))
        elif kind == "table":
            out.append(f"{_open('table', 'table')}{_blocks_html(node.children, depth)}</table>")
        elif kind in ("thead", "tbody", "tr"):
            out.append(f"<{kind}>{_blocks_html(node.children, depth)}</{kind}>")
        elif kind in ("th", "td"):
            body = "".join(_inline_html(c) for c in node.children)
            out.append(f"{_open(kind, kind, _cell_align(node))}{body}</{kind}>")
        else:
            out.append(_esc(node.content or ""))
    return "".join(out)


# ── The converter ───────────────────────────────────────────────────────────

def _convert(text: str) -> str | None:
    """The HTML part of ``text``, or None when it gets no HTML."""
    tokens = _parser().parse(text)
    if _too_deep(tokens):
        return None  # the parser can drop content at this depth
    root = _tree(tokens)
    if not _has_structure(root):
        return None
    return _sanitize(_blocks_html(root.children))


def mail_bodies(source: str) -> MailBodies:
    """The text part and the HTML part of a mail whose body is Markdown.

    The text part is the body as written, less the bidi controls. The HTML
    part is None for a body with no real structure, a body nested as deep as
    the parser's limit, and a fault of the converter.
    """
    text = _BIDI.sub("", source or "")
    try:
        html = _convert(text)
    except Exception as exc:  # a fault sends the text, never a 500
        _log.warning("email.markdown_failed", error=type(exc).__name__)
        html = None
    return MailBodies(text, html)


def mail_text(source: str) -> str:
    """Only the text part of :func:`mail_bodies`. A held chat sends this text
    with no HTML (task 3605)."""
    return mail_bodies(source).text


def draft_text(source: str) -> tuple[str, bool]:
    """The text that a draft holds for a Markdown body, and True when the
    body had formatting that the draft does not show. A draft holds text
    only (task 3605), so it keeps the text part of :func:`mail_bodies`."""
    parts = mail_bodies(source)
    return parts.text, parts.html is not None
