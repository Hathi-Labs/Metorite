"""The sample page of the page preview passes A2 (STU-D11b).

``/studio/page-preview`` draws ``samplePage.json``. This test validates the same file
through ``validate_manifest``, so the sample cannot drift from the validator.
"""

from __future__ import annotations

import json
from pathlib import Path

from acb_common.studio_manifest import Manifest, validate_manifest

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "workbench/control_plane/src/lib/studio/samplePage.json"
)


def _doc() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_sample_page_is_a_valid_manifest():
    assert validate_manifest(_doc()) == []


def test_sample_page_shows_what_the_preview_claims():
    m = Manifest.model_validate(_doc())
    page = m.pages[0]
    assert len(page.tabs) == 2
    assert page.side is not None
    assert any(c.span and c.span > 1 for t in page.tabs for c in t.cells)
    blocks = {v.block for v in m.views}
    assert {"table", "form", "detail", "board"} <= blocks


def test_a_drifted_sample_is_refused():
    d = _doc()
    d["pages"][0]["tabs"][0]["cells"][0]["view"] = "ghost"
    assert validate_manifest(d) != []
