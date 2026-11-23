"""The one renderer of a layout signature (WS-17 SG-2, D-EM-65).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.2 and §23.3.3.

``render(layout, fields, brand) -> (html, text)`` draws the five layouts:
``classic``, ``stacked``, ``minimal``, ``photo`` and ``compact``. It is pure.
It opens no session, reads no setting and reads no clock, so a test needs no
database. The resolver (``signature.signature_forms``) and the preview route
(``signature_preview.py``) call it. No TypeScript copy exists (§23.0 rule 2).

The render contract (§23.3.2). ``tests/unit/test_email_signature_render.py``
checks each rule on each layout:

* One outer ``<table role="presentation">``, at most 480 px wide.
* Inline ``style`` only: no ``class``, no ``<style>``, no web font. The font
  is ``Arial, Helvetica, sans-serif``, 13 px, and 15 px for the name.
* Each colour is a six-digit hex value.
* Each ``<img>`` has ``width``, ``height``, ``alt`` and ``display:block``. Its
  ``src`` is a hosted https URL of SG-1 (``/email/signature-image/...``).
* No SVG, no background image, no ``data:`` URI.
* The renderer escapes each field, so a name that holds ``<b>`` shows as text.
* At most 10 KB of HTML. Past it, the renderer drops the optional lines in
  :data:`_DROP_ORDER` until the HTML fits.

The text form is :func:`signature.html_to_text` of the HTML. The draft write,
the strip and the send compare that form (``signature.signature_text``), so
the renderer gives no second text.

``fields`` holds the member's values. ``brand`` holds the values of the
company signature (SG-4). A value in ``fields`` wins. Until SG-4, the
resolver passes no brand.
"""
from __future__ import annotations

import html as _html
import re
from collections.abc import Callable, Mapping
from typing import Any

from gateway.routes.email.signature import html_to_text

#: The layout ids of §23.3.2, in the order of the gallery.
LAYOUTS: tuple[str, ...] = ("classic", "stacked", "minimal", "photo", "compact")
#: The layout of the short reply signature (D-EM-71).
SHORT_LAYOUT = "compact"
#: The most HTML that one signature holds (§23.3.2).
MAX_HTML_BYTES = 10 * 1024
#: The widest signature (§23.3.2).
MAX_WIDTH_PX = 480

FONT_STACK = "Arial, Helvetica, sans-serif"
#: The colours. Each one is a six-digit hex value (§23.3.2).
TEXT_COLOUR = "#222222"
MUTED_COLOUR = "#555555"
FAINT_COLOUR = "#888888"
#: The accent when neither the member nor the company sets one (§23.3.1).
NEUTRAL_ACCENT = "#5f6368"

#: The text fields and the most characters that each one keeps.
TEXT_CAPS: Mapping[str, int] = {
    "name": 80, "title": 100, "company": 100, "phone": 40, "mobile": 40,
    "email": 254, "website": 200, "address": 200, "legal": 400,
}
#: The keys that the company signature may set (§23.3.1, §23.3.5).
BRAND_KEYS: frozenset[str] = frozenset({
    "company", "website", "address", "links", "logo", "accent", "legal",
})
#: Up to four text links, such as LinkedIn. No icon images in v1.
MAX_LINKS = 4
_LINK_LABEL_CAP = 40
_URL_CAP = 300

#: The box of each image: (the widest, the highest) in CSS pixels.
_LOGO_BOX = {"classic": (120, 120), "stacked": (240, 80)}
_PHOTO_BOX = (80, 80)

#: The order in which an optional line goes when the HTML passes 10 KB.
_DROP_ORDER: tuple[str, ...] = (
    "legal", "links", "address", "website", "email", "mobile", "company",
    "title", "phone", "logo", "photo",
)

#: A hosted image of SG-1: ``https://<base>/email/signature-image/<org>/<token>.<ext>``.
_HOSTED_IMAGE = re.compile(
    r"https://[^\s\"'<>?#\\]+/email/signature-image/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/"
    r"[A-Za-z0-9_-]{43}\.(?:png|jpg)")
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_EMAIL = re.compile(r"[^@\s<>\"'(),;:\\]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
#: A web address with no scheme, as a member types it: ``acme.com/about``.
_BARE_HOST = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/[^\s\"'<>\\]*)?")
_URL_REFUSED = re.compile(r"[\s\"'<>\\\x00-\x1f\x7f]")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_SEP = " · "


class UnknownLayout(ValueError):
    """The layout is not one of :data:`LAYOUTS`."""


# ── The field rules ─────────────────────────────────────────────────────────


def _text(value: Any, cap: int) -> str:
    """One line of plain text: each run of white space is one space, no
    control character, at most ``cap`` characters."""
    if not isinstance(value, str):
        return ""
    return " ".join(_CONTROLS.sub(" ", value).split())[:cap].strip()


def _https(value: Any) -> str | None:
    """An https address to link, or ``None``. A bare host gets ``https://``.
    Any other scheme, ``http:`` too, gives no link (D-EM-70)."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > _URL_CAP or _URL_REFUSED.search(url):
        return None
    if url.lower().startswith("https://") and len(url) > len("https://"):
        return url
    if ":" not in url and _BARE_HOST.fullmatch(url):
        return f"https://{url}"
    return None


def _tel(value: str) -> str | None:
    """A ``tel:`` address of the digits of a phone number, or ``None``."""
    digits = re.sub(r"\D", "", value)
    if len(digits) < 3:
        return None
    return f"tel:{'+' if value.lstrip().startswith('+') else ''}{digits}"


def _mailto(value: str) -> str | None:
    return f"mailto:{value}" if _EMAIL.fullmatch(value) else None


def _colour(value: Any) -> str | None:
    if isinstance(value, str) and _HEX.fullmatch(value.strip()):
        return value.strip().lower()
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= MAX_WIDTH_PX else None


def _image(value: Any, box: tuple[int, int]) -> tuple[str, int, int] | None:
    """``(src, width, height)`` of a hosted image fitted in ``box``, or ``None``.

    Only an address in the shape of a hosted image of SG-1 passes, so the send
    never carries a ``data:`` image. The renderer stays pure, so it checks the
    path and not the host. The SG-3 writer stores only an address that starts
    with ``public_base_url()`` (§23.6). The image keeps its aspect ratio."""
    if not isinstance(value, Mapping):
        return None
    src = value.get("url")
    width, height = _int(value.get("width")), _int(value.get("height"))
    if not isinstance(src, str) or not _HOSTED_IMAGE.fullmatch(src) or not width or not height:
        return None
    scale = min(1.0, box[0] / width, box[1] / height)
    return src, max(1, round(width * scale)), max(1, round(height * scale))


def hosted_image_src(value: Any) -> str | None:
    """The address of an image that the renderer draws, or ``None``.

    The same shape check as :func:`_image`: a hosted address of SG-1, and a
    ``width`` and a ``height`` from 1 to 480. The SG-3a writer
    (``signature_settings.py``) stores an image only when this passes, and it
    adds the host and the tenant, because the renderer stays pure."""
    image = _image(value, _PHOTO_BOX)
    return image[0] if image else None


def _links(value: Any) -> list[tuple[str, str]]:
    """Up to four ``(label, https url)`` pairs. A link with no https address
    goes. A link with no label shows its address."""
    if not isinstance(value, list):
        return []
    out: list[tuple[str, str]] = []
    for item in value:
        if len(out) >= MAX_LINKS:
            break
        if not isinstance(item, Mapping):
            continue
        url = _https(item.get("url"))
        if url is None:
            continue
        label = _text(item.get("label"), _LINK_LABEL_CAP) or url.removeprefix("https://")
        out.append((label[:_LINK_LABEL_CAP], url))
    return out


def _present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, [], {})


def _merged(fields: Mapping[str, Any], brand: Mapping[str, Any] | None) -> dict[str, Any]:
    """The brand values, then the member's values over them."""
    out: dict[str, Any] = {}
    for key in BRAND_KEYS:
        if brand and _present(brand.get(key)):
            out[key] = brand[key]
    for key, value in (fields or {}).items():
        if isinstance(key, str) and _present(value):
            out[key] = value
    return out


# ── The HTML parts ──────────────────────────────────────────────────────────


def _e(value: str) -> str:
    return _html.escape(value, quote=True)


def _style(*parts: str) -> str:
    return ";".join(p for p in parts if p)


def _font(size: int, colour: str, *, bold: bool = False) -> str:
    line = 20 if size == 15 else 18
    return _style(f"font-family:{FONT_STACK}", f"font-size:{size}px",
                  f"line-height:{line}px", f"color:{colour}",
                  "font-weight:bold" if bold else "")


def _a(href: str, label: str, accent: str) -> str:
    style = _style(f"color:{accent}", "text-decoration:none")
    return f'<a href="{_e(href)}" style="{style}">{_e(label)}</a>'


def _img(src: str, width: int, height: int, alt: str) -> str:
    style = _style("display:block", "border:0", "outline:none", "text-decoration:none")
    return (f'<img src="{_e(src)}" width="{width}" height="{height}" alt="{_e(alt)}" '
            f'style="{style}">')


def _div(content: str, style: str) -> str:
    return f'<div style="{style}">{content}</div>'


class _Values:
    """The checked values of one render."""

    def __init__(self, merged: Mapping[str, Any], layout: str) -> None:
        self.text = {key: _text(merged.get(key), cap) for key, cap in TEXT_CAPS.items()}
        self.accent = _colour(merged.get("accent")) or NEUTRAL_ACCENT
        self.links = _links(merged.get("links"))
        self.logo = _image(merged.get("logo"), _LOGO_BOX.get(layout, _LOGO_BOX["classic"]))
        self.photo = _image(merged.get("photo"), _PHOTO_BOX)

    def drop(self, key: str) -> None:
        if key in self.text:
            self.text[key] = ""
        elif key == "links":
            self.links = []
        elif key == "logo":
            self.logo = None
        elif key == "photo":
            self.photo = None

    def phone_parts(self, accent: str) -> list[str]:
        """The phone and the mobile. Each is a ``tel:`` link. With both, each
        has its label."""
        phone, mobile = self.text["phone"], self.text["mobile"]
        both = bool(phone and mobile)
        out: list[str] = []
        for label, number in (("Phone", phone), ("Mobile", mobile)):
            if not number:
                continue
            href = _tel(number)
            shown = _a(href, number, accent) if href else _e(number)
            out.append(f"{label} {shown}" if both else shown)
        return out

    def email_part(self, accent: str) -> list[str]:
        email = self.text["email"]
        if not email:
            return []
        href = _mailto(email)
        return [_a(href, email, accent) if href else _e(email)]

    def website_part(self, accent: str) -> list[str]:
        site = self.text["website"]
        if not site:
            return []
        href = _https(site)
        return [_a(href, site.removeprefix("https://"), accent) if href else _e(site)]

    def link_parts(self, accent: str) -> list[str]:
        return [_a(url, label, accent) for label, url in self.links]


def _lines(v: _Values, *, address: bool = True) -> list[str]:
    """The text lines of a full layout, each one a ``<div>``."""
    body = _font(13, TEXT_COLOUR)
    muted = _font(13, MUTED_COLOUR)
    out: list[str] = []
    if v.text["name"]:
        out.append(_div(_e(v.text["name"]), _font(15, v.accent, bold=True)))
    for key in ("title", "company"):
        if v.text[key]:
            out.append(_div(_e(v.text[key]), muted))
    contact = v.phone_parts(v.accent) + v.email_part(v.accent)
    if contact:
        out.append(_div(_SEP.join(contact), body))
    web = v.website_part(v.accent) + v.link_parts(v.accent)
    if web:
        out.append(_div(_SEP.join(web), body))
    if address and v.text["address"]:
        out.append(_div(_e(v.text["address"]), muted))
    return out


def _legal_row(v: _Values, colspan: int = 1) -> str:
    if not v.text["legal"]:
        return ""
    span = f' colspan="{colspan}"' if colspan > 1 else ""
    style = _style("padding:8px 0 0 0", _font(13, FAINT_COLOUR))
    return f'<tr><td{span} style="{style}">{_e(v.text["legal"])}</td></tr>'


def _table(rows: str) -> str:
    style = _style("border-collapse:collapse", f"max-width:{MAX_WIDTH_PX}px",
                   _font(13, TEXT_COLOUR))
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="{style}">{rows}</table>')


# ── The five layouts ────────────────────────────────────────────────────────


def _classic(v: _Values) -> str:
    """Logo beside: the logo at the left (up to 120 px), a thin accent rule,
    then the text."""
    lines = "".join(_lines(v))
    if not lines and v.logo is None:
        return ""
    cells = ""
    if v.logo is not None:
        alt = v.text["company"] or "Logo"
        cells += (f'<td style="{_style("padding:0 12px 0 0", "vertical-align:top")}">'
                  f"{_img(*v.logo, alt)}</td>")
    rule = _style(f"border-left:2px solid {v.accent}", "padding:0 0 0 12px",
                  "vertical-align:top")
    cells += f'<td style="{rule}">{lines}</td>'
    width = 2 if v.logo is not None else 1
    return _table(f"<tr>{cells}</tr>{_legal_row(v, width)}")


def _stacked(v: _Values) -> str:
    """Logo above: the logo, then the name and the lines under it."""
    lines = "".join(_lines(v))
    if not lines and v.logo is None:
        return ""
    rows = ""
    if v.logo is not None:
        alt = v.text["company"] or "Logo"
        rows += (f'<tr><td style="{_style("padding:0 0 8px 0")}">'
                 f"{_img(*v.logo, alt)}</td></tr>")
    if lines:
        rows += f'<tr><td style="{_style("vertical-align:top")}">{lines}</td></tr>'
    return _table(rows + _legal_row(v))


def _minimal(v: _Values) -> str:
    """Text only: the name in bold, the title and the company, then one
    contact line."""
    out: list[str] = []
    if v.text["name"]:
        out.append(_div(_e(v.text["name"]), _font(15, v.accent, bold=True)))
    role = [_e(v.text[k]) for k in ("title", "company") if v.text[k]]
    if role:
        out.append(_div(_SEP.join(role), _font(13, MUTED_COLOUR)))
    contact = (v.phone_parts(v.accent) + v.email_part(v.accent)
               + v.website_part(v.accent))
    if contact:
        out.append(_div(_SEP.join(contact), _font(13, TEXT_COLOUR)))
    if not out:
        return ""
    return _table(f'<tr><td style="{_style("vertical-align:top")}">{"".join(out)}</td></tr>'
                  f"{_legal_row(v)}")


def _photo(v: _Values) -> str:
    """Photo beside: a square photo, 80 px, at the left, then the text."""
    lines = "".join(_lines(v))
    if not lines and v.photo is None:
        return ""
    cells = ""
    if v.photo is not None:
        alt = v.text["name"] or "Photo"
        cells += (f'<td style="{_style("padding:0 12px 0 0", "vertical-align:top")}">'
                  f"{_img(*v.photo, alt)}</td>")
    cells += f'<td style="{_style("vertical-align:top")}">{lines}</td>'
    return _table(f"<tr>{cells}</tr>{_legal_row(v, 2 if v.photo is not None else 1)}")


def _compact(v: _Values) -> str:
    """One line: Name · Title · Company · Phone (D-EM-71)."""
    parts: list[str] = []
    if v.text["name"]:
        parts.append(f'<span style="{_font(15, v.accent, bold=True)}">'
                     f'{_e(v.text["name"])}</span>')
    parts += [_e(v.text[k]) for k in ("title", "company") if v.text[k]]
    phone = v.text["phone"] or v.text["mobile"]
    if phone:
        href = _tel(phone)
        parts.append(_a(href, phone, v.accent) if href else _e(phone))
    if not parts:
        return ""
    return _table(f'<tr><td style="{_style("vertical-align:top")}">{_SEP.join(parts)}</td></tr>')


_DRAW: Mapping[str, Callable[[_Values], str]] = {
    "classic": _classic,
    "stacked": _stacked,
    "minimal": _minimal,
    "photo": _photo,
    "compact": _compact,
}


def render(
    layout: str, fields: Mapping[str, Any] | None, brand: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """The HTML and the text of one signature in ``layout``.

    Raises :class:`UnknownLayout` for a layout that is not in
    :data:`LAYOUTS`. Gives ``("", "")`` when no field has a value to show."""
    draw = _DRAW.get(layout) if isinstance(layout, str) else None
    if draw is None:
        raise UnknownLayout(f"Unknown signature layout: {str(layout)[:40]!r}")
    values = _Values(_merged(fields or {}, brand), layout)
    html = draw(values)
    for key in _DROP_ORDER:
        if len(html.encode("utf-8")) <= MAX_HTML_BYTES:
            break
        values.drop(key)
        html = draw(values)
    return html, html_to_text(html)
