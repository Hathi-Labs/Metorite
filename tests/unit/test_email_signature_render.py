"""WS-17 SG-2 — the one renderer of a layout signature (D-EM-65).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.2 (the render
contract), §23.3.3 and §23.7 (the first fence row).

Each layout obeys the render contract, and the renderer escapes each field.
The renderer is pure, so no test here needs a database.
"""
from __future__ import annotations

import inspect
import re
from html.parser import HTMLParser

import pytest
from gateway.routes.email import signature_render as sr
from gateway.routes.email.signature import html_to_text, signature_text

_ORG = "11111111-2222-3333-4444-555555555555"
_LOGO = {"url": f"https://api.example.test/email/signature-image/{_ORG}/{'a' * 43}.png",
         "width": 300, "height": 100}
_PHOTO = {"url": f"https://api.example.test/email/signature-image/{_ORG}/{'b' * 43}.jpg",
          "width": 240, "height": 240}
_FULL = {
    "name": "Jane Doe", "title": "Head of Sales", "company": "Acme Works",
    "phone": "+91 98450 12345", "mobile": "98450 54321", "email": "jane@acme.test",
    "website": "acme.test", "address": "1 Main Street, Pune",
    "links": [{"label": "LinkedIn", "url": "https://linkedin.example/in/jane"}],
    "logo": _LOGO, "photo": _PHOTO, "accent": "#1A73E8",
    "legal": "This mail is confidential.",
}
_HOSTED = re.compile(
    r"https://[^\s\"'<>]+/email/signature-image/[0-9a-f-]{36}/[A-Za-z0-9_-]{43}\.(png|jpg)")


class _Tree(HTMLParser):
    """Each tag with its attributes, and the depth of the outer element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.depth = 0
        self.closed_at_top = 0
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag not in ("img", "br"):
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        self.depth -= 1
        if self.depth == 0:
            self.closed_at_top += 1

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _tree(html: str) -> _Tree:
    tree = _Tree()
    tree.feed(html)
    tree.close()
    return tree


def _styles(tree: _Tree) -> list[str]:
    return [attrs.get("style") or "" for _, attrs in tree.tags if attrs.get("style")]


def _declarations(tree: _Tree) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for style in _styles(tree):
        for part in style.split(";"):
            name, _, value = part.partition(":")
            if name.strip():
                out.append((name.strip().lower(), value.strip()))
    return out


@pytest.mark.parametrize("layout", sr.LAYOUTS)
def test_each_layout_obeys_the_render_contract(layout: str) -> None:
    html, text = sr.render(layout, _FULL)
    tree = _tree(html)
    # One outer table, role presentation, at most 480 px wide.
    assert tree.tags[0][0] == "table" and tree.tags[0][1].get("role") == "presentation"
    assert html.endswith("</table>") and tree.closed_at_top == 1 and tree.depth == 0
    assert "max-width:480px" in (tree.tags[0][1].get("style") or "")
    for _, attrs in tree.tags:
        if attrs.get("width"):
            assert int(attrs["width"]) <= sr.MAX_WIDTH_PX
    # Inline style only: no class, no <style>, no web font.
    assert all("class" not in attrs for _, attrs in tree.tags)
    assert {tag for tag, _ in tree.tags} <= {"table", "tr", "td", "div", "span", "a", "img"}
    assert "@import" not in html and "@font-face" not in html
    for name, value in _declarations(tree):
        if name == "font-family":
            assert value == "Arial, Helvetica, sans-serif"
        if name == "font-size":
            assert value in ("13px", "15px")
        if name in ("color", "border-left"):
            assert re.search(r"#[0-9a-f]{6}\b", value), (name, value)
        assert "background" not in name
    # Each colour is a six-digit hex value.
    for hexa in re.findall(r"#[0-9A-Za-z]+", html):
        assert re.fullmatch(r"#[0-9a-f]{6}", hexa), hexa
    assert "rgb(" not in html
    # Each image: width, height, alt, display:block, a hosted https source.
    for tag, attrs in tree.tags:
        if tag == "img":
            assert attrs.get("width") and attrs.get("height") and attrs.get("alt")
            assert "display:block" in (attrs.get("style") or "")
            assert _HOSTED.fullmatch(attrs.get("src") or ""), attrs.get("src")
    # No SVG, no background image, no data: URI.
    assert "<svg" not in html.lower() and "data:" not in html and "background" not in html
    # At most 10 KB.
    assert len(html.encode("utf-8")) <= sr.MAX_HTML_BYTES
    # The text is the form that the send and the draft compare.
    assert text == html_to_text(html) == signature_text(html)
    assert text.splitlines()[0].startswith("Jane Doe")


def test_the_name_is_15_px_and_the_rest_13_px() -> None:
    html, _ = sr.render("classic", _FULL)
    name_div = re.search(r'<div style="([^"]*)">Jane Doe</div>', html)
    assert name_div and "font-size:15px" in name_div.group(1)
    assert "font-weight:bold" in name_div.group(1)


@pytest.mark.parametrize("layout", sr.LAYOUTS)
@pytest.mark.parametrize("field", ["name", "title", "company", "phone", "address", "legal"])
def test_a_field_that_holds_html_shows_as_text(layout: str, field: str) -> None:
    hostile = '<b>Jane</b><script>alert(1)</script><img src=x onerror="alert(2)">'
    html, text = sr.render(layout, {**_FULL, field: hostile})
    tags = {tag for tag, _ in _tree(html).tags}
    assert "script" not in tags and "b" not in tags
    assert all("onerror" not in attrs for _, attrs in _tree(html).tags)
    shown = {"name", "title", "company", "phone"} if layout == "compact" else {field}
    if field in shown and not (layout == "minimal" and field == "address"):
        assert "&lt;script&gt;" in html
        assert "<script>alert(1)</script>" in text


def test_a_link_that_is_not_https_is_no_link() -> None:
    html, _ = sr.render("classic", {
        **_FULL, "website": "javascript:alert(1)",
        "links": [{"label": "Old", "url": "http://old.example"},
                  {"label": "Bad", "url": 'https://x.example" onmouseover="alert(1)'},
                  {"label": "Ok", "url": "https://ok.example"}]})
    hrefs = [attrs.get("href") for tag, attrs in _tree(html).tags if tag == "a"]
    assert "https://ok.example" in hrefs
    assert not any(h and (h.startswith(("http:", "javascript:")) or "onmouseover" in h)
                   for h in hrefs)
    assert all("onmouseover" not in attrs for _, attrs in _tree(html).tags)


@pytest.mark.parametrize("src", [
    "data:image/png;base64,iVBORw0KGgo=",
    "http://api.example.test/email/signature-image/x/y.png",
    "https://evil.example/logo.png",
    f"https://api.example.test/email/signature-image/{_ORG}/{'a' * 43}.svg",
    "javascript:alert(1)",
])
def test_only_a_hosted_image_is_drawn(src: str) -> None:
    html, _ = sr.render("classic", {**_FULL, "logo": {**_LOGO, "url": src}})
    assert "<img" not in html and "data:" not in html and "evil" not in html


def test_an_image_keeps_its_ratio_in_its_box() -> None:
    classic = _tree(sr.render("classic", _FULL)[0])
    [logo] = [a for t, a in classic.tags if t == "img"]
    assert (logo["width"], logo["height"]) == ("120", "40")
    stacked = _tree(sr.render("stacked", _FULL)[0])
    [logo] = [a for t, a in stacked.tags if t == "img"]
    assert (logo["width"], logo["height"]) == ("240", "80")
    photo = _tree(sr.render("photo", _FULL)[0])
    [img] = [a for t, a in photo.tags if t == "img"]
    assert (img["width"], img["height"], img["alt"]) == ("80", "80", "Jane Doe")


def test_compact_is_one_line() -> None:
    html, text = sr.render("compact", _FULL)
    assert text == "Jane Doe · Head of Sales · Acme Works · +91 98450 12345"
    assert "<img" not in html


def test_the_phone_is_a_tel_link_of_its_digits() -> None:
    html, _ = sr.render("minimal", _FULL)
    hrefs = [a.get("href") for t, a in _tree(html).tags if t == "a"]
    assert "tel:+919845012345" in hrefs and "tel:9845054321" in hrefs
    assert "mailto:jane@acme.test" in hrefs and "https://acme.test" in hrefs


def test_a_bad_accent_takes_the_neutral_grey() -> None:
    for accent in ("red", "#fff", "rgb(1,2,3)", "#12345g", None):
        html, _ = sr.render("classic", {**_FULL, "accent": accent})
        assert f"color:{sr.NEUTRAL_ACCENT}" in html
    assert "color:#1a73e8" in sr.render("classic", _FULL)[0]


def test_the_brand_fills_a_field_and_the_member_value_wins() -> None:
    brand = {"company": "Brand Co", "website": "brand.example", "legal": "Brand legal.",
             "accent": "#aa0000", "name": "Not a brand field"}
    html, text = sr.render("classic", {"name": "Jane"}, brand)
    assert "Brand Co" in text and "Brand legal." in text and "color:#aa0000" in html
    assert "Not a brand field" not in text
    html, text = sr.render("classic", {"name": "Jane", "company": "Mine"}, brand)
    assert "Mine" in text and "Brand Co" not in text


def test_four_links_at_most() -> None:
    links = [{"label": f"L{i}", "url": f"https://l{i}.example"} for i in range(9)]
    html, _ = sr.render("classic", {"name": "Jane", "links": links})
    assert sum(1 for t, _ in _tree(html).tags if t == "a") == sr.MAX_LINKS


def test_the_html_stays_under_10_kb() -> None:
    """Each field at its cap, made of the characters that escape longest."""
    huge = {key: '"' * cap for key, cap in sr.TEXT_CAPS.items()}
    huge["links"] = [{"label": '"' * 40, "url": "https://x.example/" + "a" * 280}] * 4
    huge["logo"], huge["photo"] = _LOGO, _PHOTO
    for layout in sr.LAYOUTS:
        html, _ = sr.render(layout, huge)
        assert len(html.encode("utf-8")) <= sr.MAX_HTML_BYTES, layout
        assert html.startswith('<table role="presentation"')


def test_no_value_gives_no_signature() -> None:
    for layout in sr.LAYOUTS:
        assert sr.render(layout, {}) == ("", "")
        assert sr.render(layout, {"name": "   ", "links": []}) == ("", "")


def test_an_unknown_layout_is_refused() -> None:
    for layout in ("fancy", "", None, "CLASSIC"):
        with pytest.raises(sr.UnknownLayout):
            sr.render(layout, _FULL)  # type: ignore[arg-type]


def test_the_renderer_is_pure() -> None:
    """No session, no setting, no clock (§23.3.3): a test needs no database."""
    source = inspect.getsource(sr)
    for word in ("execute(", "get_settings", "datetime", "sqlalchemy", "os.environ"):
        assert word not in source, word
