"""WS-61 KB-2b — the chunker indexes what the block editor saves, unchanged.

Spec: ``project-docs/specs/knowledge_base.md`` §21.5 (KB-2b). The editor
writes Markdown (``body_md``), and the chunker of §6.2 reads that Markdown as
it is. ``tests/fixtures/kb_editor_golden.json`` holds the bodies the editor
writes (``saved``), and vitest holds the editor to them
(``src/app/knowledge/lib/editor/editorMarkdown.test.ts``).

For each saved body:

* every ``#`` heading outside a code fence is in a heading path, and
* every other line with text is in a chunk, byte for byte.

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


def _lines(saved: str) -> tuple[list[str], list[str]]:
    """The heading texts and the other lines with text, outside or in fences."""
    headings: list[str] = []
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
            headings.append(match.group(2).strip())
        elif line.strip():
            text.append(line.strip())
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
        assert line in raw, (line, raw)
