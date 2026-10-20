"""WS-61 KB-2p — the chunker reads the stored forms as their text.

Spec: ``project-docs/specs/knowledge_base.md`` §21.7, "The chunker". The
editor stores a block or a mark with no Markdown syntax as one HTML fragment.
``kb_index/chunk.py`` takes off the tags of those fragments and keeps their
text. It strips nothing else, so the ``content_sha`` of a stored
``<details>`` toggle does not change.
"""
from __future__ import annotations

import pytest

pytest.importorskip("gateway.kb_index.chunk", reason="gateway not installed")

from gateway.kb_index.chunk import chunk_page, content_sha, strip_stored_forms

TITLE = "Stored forms"


def _raw(body: str) -> str:
    return "\n\n".join(c.raw for c in chunk_page(TITLE, body))


# ── The inline forms ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("body", "text"),
    [
        ('A <span data-color="#2563eb">blue</span> word.', "A blue word."),
        ('A <span data-color="blue">blue</span> word.', "A blue word."),
        ('A <mark data-color="#fde68a">marked</mark> word.', "A marked word."),
        ('A <mark data-color="yellow">marked</mark> word.', "A marked word."),
        ("E = mc<sup>2</sup> and H<sub>2</sub>O.", "E = mc2 and H2O."),
        ('State: <span data-type="status" data-color="green">Done</span>.', "State: Done."),
        (
            'Due <time data-type="date" datetime="2026-10-19">19 Oct 2026</time>.',
            "Due 19 Oct 2026.",
        ),
    ],
)
def test_an_inline_form_is_indexed_as_its_text(body: str, text: str) -> None:
    assert strip_stored_forms(body) == text
    assert _raw(body) == text


def test_nested_forms_lose_every_tag_and_keep_the_marks_of_markdown() -> None:
    body = '<span data-color="red">**bold <mark data-color="green">hi</mark>**</span> x<sup>*2*</sup>'
    assert strip_stored_forms(body) == "**bold hi** x*2*"


def test_a_form_that_spans_a_hard_break_loses_both_tags() -> None:
    body = 'one <span data-color="blue">two\\\nthree</span> four'
    assert strip_stored_forms(body) == "one two\\\nthree four"


def test_a_heading_with_a_form_gives_a_clean_heading_path() -> None:
    chunks = chunk_page(TITLE, "## Water H<sub>2</sub>O\n\nText.")
    assert chunks[-1].heading_path == f"{TITLE} > Water H2O"


# ── The block forms ─────────────────────────────────────────────────────────

def test_columns_lose_their_div_lines_and_keep_each_column() -> None:
    body = (
        '<div data-type="columns" data-count="2">\n\n'
        '<div data-type="column">\n\nLeft words.\n\n</div>\n\n'
        '<div data-type="column">\n\n- right item\n\n</div>\n\n'
        "</div>"
    )
    raw = _raw(body)
    assert "Left words." in raw
    assert "- right item" in raw
    assert "<div" not in raw and "</div>" not in raw


def test_columns_inside_a_callout_lose_their_tags_and_keep_the_quote() -> None:
    body = (
        "> [!NOTE]\n"
        '> <div data-type="columns" data-count="2">\n>\n'
        '> <div data-type="column">\n>\n> Left.\n>\n> </div>\n>\n'
        '> <div data-type="column">\n>\n> Right.\n>\n> </div>\n>\n'
        "> </div>"
    )
    stripped = strip_stored_forms(body)
    assert "<div" not in stripped and "</div>" not in stripped
    assert "> Left." in stripped and "> Right." in stripped


def test_subpages_and_a_page_break_leave_no_text() -> None:
    body = (
        "Before.\n\n"
        '<div data-type="subpages" data-order="manual"></div>\n\n'
        '<div data-type="page-break"></div>\n\n'
        "After."
    )
    assert _raw(body) == "Before.\n\nAfter."


def test_a_raw_div_keeps_its_tags_and_its_close_pairs_with_it() -> None:
    body = (
        '<div data-type="columns" data-count="2">\n\n'
        '<div data-type="column">\n\n<div align="center">\nraw\n</div>\n\n</div>\n\n'
        '<div data-type="column">\n\nB\n\n</div>\n\n'
        "</div>"
    )
    stripped = strip_stored_forms(body)
    assert '<div align="center">\nraw\n</div>' in stripped
    assert "data-type" not in stripped


# ── What stays ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "body",
    [
        "<details>\n<summary>S</summary>\n\nbody\n\n</details>",
        "<details open>\n<summary>S</summary>\n\nbody <b>x</b>\n\n</details>",
        '<p data-align="center">Centred **text**</p>',
        '<h2 data-align="right">Right</h2>',
        '<span class="x">raw span</span> and <mark>bare mark</mark>',
        '<span data-color="url(x)">not a colour</span>',
        '<time datetime="2026-10-19">no type</time>',
        '<div data-type="page-break" class="x"></div>',
        "<table>\n<tr>\n<td colspan=\"2\">cell</td>\n</tr>\n</table>",
        "Press <kbd>Ctrl</kbd> now.",
    ],
)
def test_other_html_is_not_stripped(body: str) -> None:
    assert strip_stored_forms(body) == body


def test_the_content_sha_of_a_details_toggle_does_not_change() -> None:
    body = "Intro.\n\n<details>\n<summary>Refunds</summary>\n\nThirty days.\n\n</details>"
    chunks = chunk_page(TITLE, body)
    assert [c.content_sha for c in chunks] == [content_sha(TITLE, c.raw) for c in chunks]
    assert chunks[0].raw == body


def test_code_keeps_the_tags() -> None:
    body = (
        "```html\n<sup>2</sup> <span data-color=\"blue\">x</span>\n"
        '<div data-type="page-break"></div>\n```\n\n'
        "Inline `<sup>2</sup>` stays, and <sup>3</sup> goes."
    )
    stripped = strip_stored_forms(body)
    assert "```html\n<sup>2</sup> <span data-color=\"blue\">x</span>\n" in stripped
    assert '<div data-type="page-break"></div>\n```' in stripped
    assert stripped.endswith("Inline `<sup>2</sup>` stays, and 3 goes.")


def test_any_fragment_with_a_data_type_loses_its_tags() -> None:
    """The spec strips by the attribute, so a shape the editor does not read
    (a colour off the list, a count that does not match) is still text."""
    assert strip_stored_forms('<span data-type="other" data-color="teal">x</span>') == "x"
    assert strip_stored_forms('<time data-type="date" datetime="2026-02-30">bad</time>') == "bad"
    body = '<div data-type="columns" data-count="7">\n\nx\n\n</div>'
    assert strip_stored_forms(body) == "\n\nx\n\n"
    assert strip_stored_forms('<div data-type="subpages" data-order="newest"></div>') == ""


def test_an_unclosed_form_stays() -> None:
    body = 'A <span data-color="blue">open and never closed.'
    assert strip_stored_forms(body) == body


def test_a_body_with_no_tag_is_returned_as_it_is() -> None:
    body = "# Title\n\nPlain words, 5 < 6."
    assert strip_stored_forms(body) == body
