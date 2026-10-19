"""WS-61 KB-2b — the chunker indexes what the block editor saves, unchanged.

Spec: ``project-docs/specs/knowledge_base.md`` §21.5 (KB-2b). The editor
writes Markdown (``body_md``), and the chunker of §6.2 reads that Markdown as
it is. ``tests/fixtures/kb_editor_golden.json`` holds the bodies the editor
writes (``saved``), and vitest holds the editor to them
(``src/app/knowledge/lib/editor/editorMarkdown.test.ts``).

For each saved body:

* every ``#`` heading outside a code fence that heads text is in a heading
  path, and
* every other line with text is in a chunk, byte for byte.

KB-2p (§21.7) amends the second rule. The chunker takes off the tags of the
stored forms (a colour, a highlight, ``sup``, ``sub``, a status, a date and
the ``data-type`` blocks). A line outside those fragments is still in a
chunk byte for byte. A line inside one is in a chunk with the tags removed,
and a line that holds only a block tag holds no text.

So no block that the editor writes is lost to the index, and the index reads
no text that the body does not hold.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("gateway.kb_index.chunk", reason="gateway not installed")

from gateway.kb_index.chunk import PATH_SEP, chunk_page

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kb_editor_golden.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
TITLE = "Golden page"

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")

# The tags of the KB-2p stored forms, written out again here so that the
# test does not trust the chunker's own pattern.
_FORM_OPEN = re.compile(
    r'<span data-color="[^"<>]+">|<mark data-color="[^"<>]+">|<sup>|<sub>'
    r'|<span data-type="status" data-color="[a-z]+">'
    r'|<time data-type="date" datetime="[0-9-]+">'
    r'|<div data-type="[a-z-]+"(?: data-[a-z]+="[^"<>]+")*>(?:</div>)?'
)
_FORM_CLOSE = re.compile(r"</span>|</mark>|</sup>|</sub>|</time>|</div>")
_DIV_LINE = re.compile(r"^(?:>\s*)*</div>$")


def _without_form_tags(line: str) -> str | None:
    """The text a line of a stored form indexes, or ``None`` for other lines."""
    if _FORM_OPEN.search(line):
        return _FORM_CLOSE.sub("", _FORM_OPEN.sub("", line)).strip()
    if _DIV_LINE.match(line):
        return ""
    return None


def _lines(saved: str) -> tuple[list[str], list[str]]:
    """The headings that head some text, and the other lines with text.

    A heading with no text under it heads no chunk, so the chunker gives it
    no heading path, and the test asks for none.
    """
    headings: list[str] = []
    pending: list[str] = []
    text: list[str] = []
    fence: str | None = None
    for line in saved.splitlines():
        opened = _FENCE.match(line)
        if opened and fence is None:
            fence = opened.group(1)
        elif opened and fence and line.strip().startswith(fence):
            fence = None
        match = None if fence or opened else _HEADING.match(line)
        if match:
            pending.append(match.group(2).strip())
        elif line.strip():
            text.append(line.strip())
            headings.extend(pending)
            pending = []
    return headings, text


def test_the_fixture_has_cases() -> None:
    assert len(CASES) >= 10
    assert all({"name", "md", "saved"} <= set(case) for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_every_heading_of_the_saved_body_is_a_heading_path(case: dict) -> None:
    headings, _ = _lines(case["saved"])
    paths = [c.heading_path for c in chunk_page(TITLE, case["saved"])]
    for heading in headings:
        assert any(heading in p.split(PATH_SEP) for p in paths), (heading, paths)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_every_line_of_the_saved_body_is_indexed_unchanged(case: dict) -> None:
    _, text = _lines(case["saved"])
    raw = "\n".join(c.raw for c in chunk_page(TITLE, case["saved"]))
    for line in text:
        stripped = _without_form_tags(line)
        if stripped is None:
            assert line in raw, (line, raw)
        else:
            assert stripped in raw, (line, stripped, raw)
            assert not _FORM_OPEN.search(raw), raw
