"""WS-17 SG-1 — the server allowlist of a signature (D-EM-70).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.2 D-EM-70, §23.7.

The server cleans each new signature HTML on save, with one allowlist in
``routes/email/signature.py`` built on the standard-library ``html.parser``.
Each ``href`` and ``src`` must be https, ``mailto:`` or ``tel:``. These cases
pin the rule, and the parity case pins it to the editor's lists in
``SignatureEditor.tsx``, so the editor never shows what the save removes.

Hermetic: no database, no network.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from gateway.routes.email import signature as sig
from gateway.routes.email.signature import (
    clean_signature_html,
    clean_signature_report,
    data_image_sources,
)

_EDITOR = (
    Path(__file__).resolve().parents[2]
    / "workbench/control_plane/src/app/email/components/SignatureEditor.tsx"
)


def test_script_and_its_text_go() -> None:
    out = clean_signature_html("<p>Jane<script>alert(1)</script></p>")
    assert out == "<p>Jane</p>"


def test_each_on_attribute_goes() -> None:
    out = clean_signature_html(
        '<b onclick="steal()">Jane</b><img src="https://cdn.example/l.png" '
        'onerror="steal()" onload="steal()">')
    assert "steal" not in out and "onclick" not in out and "onerror" not in out
    assert out == '<b>Jane</b><img src="https://cdn.example/l.png">'


@pytest.mark.parametrize("href", [
    "javascript:alert(1)",
    " JavaScript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "\x01javascript:alert(1)",
    "vbscript:msgbox(1)",
    "http://track.example/x",
    "data:text/html;base64,PHNjcmlwdD4=",
    "/relative/path",
    "//evil.example/x",
    "https:",
])
def test_a_link_with_a_refused_scheme_keeps_only_its_text(href: str) -> None:
    out = clean_signature_html(f'<a href="{href}">Visit</a>')
    assert out == "<a>Visit</a>", out


@pytest.mark.parametrize("src", [
    "http://track.example/pixel.png",
    "data:image/png;base64,iVBORw0KGgo=",
    "javascript:alert(1)",
    "",
])
def test_an_image_with_a_refused_source_goes(src: str) -> None:
    assert clean_signature_html(f'<p>A<img src="{src}">B</p>') == "<p>AB</p>"


def test_an_image_with_no_source_goes() -> None:
    assert clean_signature_html('<img alt="logo" width="100">') == ""


def test_an_https_image_stays_with_its_size_and_alt() -> None:
    html = '<img src="https://api.metorite.com/email/signature-image/x/y.png" width="120" alt="Acme">'
    assert clean_signature_html(html) == html


@pytest.mark.parametrize("href", [
    "https://acme.example/about",
    "mailto:jane@acme.example",
    "tel:+919876543210",
])
def test_https_mailto_and_tel_links_stay(href: str) -> None:
    out = clean_signature_html(f'<a href="{href}" target="_blank" rel="noopener">x</a>')
    assert out == f'<a href="{href}" target="_blank" rel="noopener">x</a>'


def test_a_hosted_data_image_takes_its_https_url() -> None:
    src = "data:image/png;base64,iVBORw0KGgo="
    url = "https://api.metorite.com/email/signature-image/o/t.png"
    out = clean_signature_html(f'<img src="{src}" width="80">', hosted={src: url})
    assert out == f'<img src="{url}" width="80">'


def test_data_image_sources_finds_each_once_in_order() -> None:
    a = "data:image/png;base64,AAAA"
    b = "data:image/jpeg;base64,BBBB"
    html = f'<img src="{a}"><p>x</p><img src="{b}"/><img src="{a}"><img src="https://x.example/y.png">'
    assert data_image_sources(html) == [a, b]


def test_a_style_that_loads_a_resource_loses_that_declaration() -> None:
    out = clean_signature_html(
        '<span style="color: #333333; background-image: url(https://t.example/p); '
        'width: expression(alert(1)); font-size: 13px">x</span>')
    assert out == '<span style="color: #333333; font-size: 13px">x</span>'


def test_a_style_with_only_refused_declarations_goes() -> None:
    assert clean_signature_html('<p style="background: url(x)">a</p>') == "<p>a</p>"


def test_tags_off_the_list_go_and_their_text_stays() -> None:
    out = clean_signature_html("<section><center>Jane</center></section><o:p>x</o:p>")
    assert out == "Janex"


@pytest.mark.parametrize("tag", ["style", "iframe", "svg", "object", "template", "textarea"])
def test_a_tag_that_holds_code_goes_with_its_content(tag: str) -> None:
    out = clean_signature_html(f"<p>a</p><{tag}>secret<b>x</b></{tag}><p>b</p>")
    assert out == "<p>a</p><p>b</p>"


def test_a_void_tag_off_the_list_does_not_eat_the_rest() -> None:
    assert clean_signature_html('<embed src="x">Jane<input value="y">Doe') == "JaneDoe"


def test_comments_go() -> None:
    assert clean_signature_html("<!--[if mso]><b>x</b><![endif]--><p>a</p>") == "<p>a</p>"


def test_the_output_is_well_formed() -> None:
    out = clean_signature_html("<div><p>open<b>bold</div></p></i>")
    assert out == "<div><p>open<b>bold</b></p></div>"


def test_text_is_escaped_once() -> None:
    out = clean_signature_html("<p>Tom &amp; Jerry &lt;3 \"q\"</p>")
    assert out == '<p>Tom &amp; Jerry &lt;3 "q"</p>'


def test_an_attribute_value_cannot_break_out() -> None:
    out = clean_signature_html('<img src="https://x.example/a.png" alt=\'"><script>x</script>\'>')
    assert out == '<img src="https://x.example/a.png" alt="&quot;&gt;&lt;script&gt;x&lt;/script&gt;">'


def test_a_bad_number_or_colour_attribute_goes() -> None:
    out = clean_signature_html(
        '<table width="100%" border="0" cellpadding="javascript:x"><tr>'
        '<td colspan="2" color="#ff00ff" align="evil">a</td></tr></table>')
    assert out == ('<table width="100%" border="0"><tr><td colspan="2" color="#ff00ff">'
                   'a</td></tr></table>')


@pytest.mark.parametrize("html", [
    "<p>Jane <b>Doe</b></p>",
    '<table style="min-width: 75px"><colgroup><col style="min-width: 25px"></colgroup>'
    '<tbody><tr><td colspan="1" rowspan="1"><p>a</p></td></tr></tbody></table>',
    '<p style="text-align: center"><span style="color: #ff0000; font-size: 14px">x</span></p>',
    '<a href="https://a.example" target="_blank" rel="noopener noreferrer nofollow">a</a>',
])
def test_the_editor_output_passes_unchanged(html: str) -> None:
    """Tiptap's own HTML is what the dialog saves. It must not churn."""
    assert clean_signature_html(html) == html


@pytest.mark.parametrize("html", [
    "<div><p>open<b>bold</div>",
    '<a href="javascript:x" onclick="y">z</a><img src="data:image/png;base64,AA">',
    "<p>Tom &amp; Jerry</p><script>x</script>",
])
def test_a_second_clean_changes_nothing(html: str) -> None:
    once = clean_signature_html(html)
    assert clean_signature_html(once) == once


def test_the_server_lists_equal_the_editor_lists() -> None:
    """D-EM-70: two lists that differ let the editor show what the save then
    removes. The tags, the attributes and the URL schemes must agree."""
    source = _EDITOR.read_text(encoding="utf-8")

    def _array(name: str) -> set[str]:
        body = re.search(rf"const {name} = \[(.*?)\];", source, re.S)
        assert body, name
        return set(re.findall(r'"([a-z0-9]+)"', body.group(1)))

    assert _array("SIG_ALLOWED_TAGS") == set(sig._SIG_TAGS)
    assert _array("SIG_ALLOWED_ATTR") == set(sig._SIG_ATTRS)
    uri = re.search(r"SIG_ALLOWED_URI = /\^\(\?:(.*?)\)/i;", source)
    assert uri, "SIG_ALLOWED_URI is gone"
    assert set(uri.group(1).split("|")) == {"https:", "mailto:", "tel:"}
    assert "data:" not in uri.group(1) and "https?" not in uri.group(1)
    # Fix round 3: the editor LOADS with a wider list, so an http: address or
    # a legacy data: image reaches the save, and the server judges it here.
    load = re.search(r"SIG_LOAD_URI =\s*/\^\(\?:(.*?)\)/i;", source)
    assert load, "SIG_LOAD_URI is gone"
    assert load.group(1).startswith("https?:|mailto:|tel:|data:image")
    assert "ALLOWED_URI_REGEXP: SIG_LOAD_URI" in source


def test_a_typo_fix_names_the_http_logo_that_the_save_removed() -> None:
    """Fix round 3: the editor keeps an http: logo at load, so the save sees
    it. The clean removes it, and the report names it for the dialog."""
    html = '<p>Jane Doe, CEO</p><img src="http://acme.com/logo.png" width="120">'
    out, removed = clean_signature_report(html)
    assert out == "<p>Jane Doe, CEO</p>"
    assert removed == [{"kind": "image", "value": "http://acme.com/logo.png"}]


def test_the_report_names_each_refused_address_once() -> None:
    """Fix round 1, P2: the save answers what the clean removed."""
    long = "http://track.example/" + "x" * 300
    html = ('<a href="http://a.example">a</a><a href="http://a.example">again</a>'
            '<a href="javascript:alert(1)">j</a><img src="http://cdn.example/l.png">'
            f'<img src="{long}"><a href="https://ok.example">ok</a><a href="">empty</a>')
    out, removed = clean_signature_report(html)
    assert out == clean_signature_html(html)
    assert removed[:3] == [
        {"kind": "link", "value": "http://a.example"},
        {"kind": "link", "value": "javascript:alert(1)"},
        {"kind": "image", "value": "http://cdn.example/l.png"},
    ]
    assert removed[3]["kind"] == "image"
    assert len(removed[3]["value"]) == 120 and removed[3]["value"].endswith("…")
    assert len(removed) == 4, "a kept link and an empty one are not reported"


def test_a_hosted_data_image_is_not_reported() -> None:
    src = "data:image/png;base64,iVBORw0KGgo="
    _, removed = clean_signature_report(
        f'<img src="{src}">', hosted={src: "https://h.example/x.png"})
    assert removed == []


@pytest.mark.parametrize("value", [
    'src("https://t.example/p.png")',
    "SRC( https://t.example/p.png )",
    'image-set(src("https://t.example/a.png") 1x)',
])
def test_the_css_src_function_is_refused_as_url_is(value: str) -> None:
    """Fix round 1, P3: CSS ``src()`` loads a resource, as ``url()`` does."""
    out = clean_signature_html(f'<span style="color: #111111; background-image: {value}">x</span>')
    assert out == '<span style="color: #111111">x</span>'
