"""Markdown to the two parts of a mail (task 3605, RC-CHAT).

The email assistant writes the body of a mail in Markdown (owner ask,
2026-11-17: headings, lists, bold, italics). :func:`mail_bodies` is the ONE
converter. It gives the plain text part and the HTML part of the mail.

* The parser is ``markdown-it-py``, the Markdown library of the gateway
  (``pdf_render.markdown_to_html`` uses it too). Raw HTML is OFF, so a tag in
  the Markdown stays text. The parser escapes it, and the mail shows it as
  text. A character reference stays text too (the ``entity`` rule is off), so
  ``&#8203;`` never becomes a hidden character.
* The writer below emits only the tags that the mail allowlist keeps, each
  with an inline style, because Gmail and Outlook drop a ``<style>`` block. A
  quote and a code block become a ``div``, and inline code a ``span``. A
  Markdown image becomes its alt text, so a mail from the chat loads nothing.
* The HTML then goes through ``signature.clean_signature_html``, the server
  allowlist of mail HTML (D-EM-70). It is the one sanitizer: it keeps an
  ``https:``, ``mailto:`` or ``tel:`` link and drops a ``style`` that loads a
  resource. Each link asks it first, and a link that it refuses shows its
  words and its address as text. No second sanitizer exists here.
* A body with no formatting gives its own text back and no HTML, so a plain
  mail goes out byte for byte as before this module.

The signature is not added here. ``signature.build_signed_bodies`` adds it
once to each part, at the send. Fence: ``tests/unit/test_email_markdown_mail.py``.
"""
from __future__ import annotations

import html as _html
import re
from functools import lru_cache
from typing import Any, NamedTuple

from gateway.routes.email.quoting import split_quoted_text
from gateway.routes.email.signature import clean_signature_html

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
#: The node types of a body with no formatting: paragraphs of text and line
#: breaks only.
_PLAIN_INLINE = frozenset({"text", "softbreak", "hardbreak"})
#: The indent of a quote in the text part. It is not ``>``: a line that starts
#: with ``>`` is a quoted thread for ``quoting.split_quoted_text``, and the
#: signature would go above it in the text part and below it in the HTML.
_QUOTE_INDENT = "    "


class MailBodies(NamedTuple):
    """The two parts of a mail. ``html`` is ``None`` for a plain body."""

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


def _tree(source: str) -> Any:
    from markdown_it.tree import SyntaxTreeNode

    return SyntaxTreeNode(_parser().parse(source))


def _is_plain(root: Any) -> bool:
    """True when the body holds paragraphs of text and line breaks only."""
    for block in root.children:
        if block.type != "paragraph":
            return False
        for inline in block.children:
            if any(child.type not in _PLAIN_INLINE for child in inline.children):
                return False
    return True


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
            out.append(f"<{kind}>{_inline_html(child)}</{kind}>")
        elif kind == "code_inline":
            out.append(f"{_open('span', 'code')}{_esc(child.content)}</span>")
        elif kind == "link":
            href = _html.escape(str(child.attrs.get("href") or ""), quote=True)
            if _link_kept(href):
                out.append(f'<a href="{href}">{_inline_html(child)}</a>')
            else:
                # The words and the address as text, as in the text part.
                out.append(_esc(_link_text(child)))
        elif kind == "image":
            # The alt text only: a mail from the chat loads no image.
            out.append(_esc(_inline_text(child)))
        else:
            out.append(_esc(child.content or ""))
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


# ── The text part ───────────────────────────────────────────────────────────

def _link_text(node: Any) -> str:
    """A link as text: its words and its address, or the address alone when
    the words are the address. The text part keeps each address readable."""
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


def _indent(text: str, first: str, rest: str) -> str:
    lines = text.split("\n")
    return "\n".join([first + lines[0], *(rest + x if x else x for x in lines[1:])])


def _blocks_text(nodes: Any, sep: str = "\n\n") -> str:
    parts: list[str] = []
    for node in nodes:
        kind = node.type
        if kind in ("paragraph", "heading"):
            parts.append("".join(_inline_text(c) for c in node.children))
        elif kind in ("bullet_list", "ordered_list"):
            number = int(node.attrs.get("start") or 1)
            items: list[str] = []
            for item in node.children:
                marker = "- " if kind == "bullet_list" else f"{number}. "
                number += 1
                body = _blocks_text(item.children, sep="\n")
                items.append(_indent(body, marker, " " * len(marker)))
            parts.append("\n".join(items))
        elif kind == "blockquote":
            body = _blocks_text(node.children)
            parts.append(_indent(body, _QUOTE_INDENT, _QUOTE_INDENT))
        elif kind == "fence":
            parts.append(node.content.rstrip("\n"))
        elif kind == "hr":
            parts.append("---")
        elif kind == "table":
            rows = [
                " | ".join("".join(_inline_text(c) for c in cell.children)
                           for cell in row.children)
                for section in node.children for row in section.children
            ]
            parts.append("\n".join(rows))
        else:
            parts.append(node.content or "")
    return sep.join(p for p in parts if p.strip())


def _clean_text(text: str) -> str:
    """No space at a line end, and no run of more than one blank line. A
    chat draft refuses a line-end space (``drafting.check_draft_send``)."""
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # Only the line ends go at each end: a quote keeps its indent.
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


# ── The converter ───────────────────────────────────────────────────────────

def mail_bodies(source: str) -> MailBodies:
    """The text part and the HTML part of a mail whose body is Markdown.

    A body with no formatting comes back as it is, with no HTML. A send then
    goes out as before (``build_signed_bodies`` adds the signature as it did).
    A body whose text part ``quoting.split_quoted_text`` would cut gets no
    HTML either: the text part alone keeps the signature in one place.
    """
    source = source or ""
    root = _tree(source)
    if _is_plain(root):
        return MailBodies(source, None)
    text = _clean_text(_blocks_text(root.children))
    if split_quoted_text(text)[1]:
        return MailBodies(text, None)
    return MailBodies(text, _sanitize(_blocks_html(root.children)))


def mail_text(source: str) -> str:
    """Only the text part of :func:`mail_bodies`. A held chat sends this text
    with no HTML (task 3605)."""
    return mail_bodies(source).text


def draft_text(source: str) -> tuple[str, bool]:
    """The text that a draft holds for a Markdown body, and True when the
    draft loses formatting. A draft holds text only (task 3605), so it keeps
    the text part of :func:`mail_bodies`. A plain body keeps its own text,
    and the flag is False."""
    parts = mail_bodies(source)
    return parts.text, parts.html is not None or parts.text != (source or "")
