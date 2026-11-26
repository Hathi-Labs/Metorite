"""The page layouts that `pageLayout.ts` makes pass the server check (STU-AF11a).

`pageLayout.test.ts` runs 200 random edits for three seeds. It compares the manifests at
op 50, 100, 150 and 200 with a JSON fixture. This test reads that fixture. It puts each
`pages` list into a full manifest and runs the one validator, `validate_manifest`. No DB.
"""

from __future__ import annotations

import json
from pathlib import Path

from acb_common.studio_manifest import validate_manifest

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "workbench/control_plane/src/lib/studio/__fixtures__/pageLayout_outputs.json"
)

BASE: dict = {
    "studio": 1,
    "id": "layout-app",
    "name": "Layout app",
    "purpose": "Check the layouts of the page editor.",
    "objects": [
        {
            "name": "visit",
            "fields": [
                {"key": "customer", "type": "text"},
                {"key": "when", "type": "datetime"},
                {"key": "status", "type": "select", "options": ["planned", "done"]},
            ],
        }
    ],
    "actions": [{"id": "close_visit", "uses": "object:visit.update", "set": {"status": "done"}}],
    "views": [
        {
            "id": "board",
            "block": "board",
            "source": {"object": "visit"},
            "group_by": "status",
            "card": {"title": "customer", "meta": ["when"]},
        },
        {"id": "week", "block": "calendar", "source": {"object": "visit"}, "start": "when"},
        {
            "id": "check_in",
            "block": "form",
            "object": "visit",
            "fields": ["status"],
            "submit": "close_visit",
        },
    ],
}


def _entries() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_is_not_empty() -> None:
    entries = _entries()
    assert len(entries) >= 12
    assert {e["seed"] for e in entries} == {11, 23, 47}


def test_the_base_manifest_is_valid() -> None:
    assert validate_manifest(BASE) == []


def test_every_layout_passes_the_server_check() -> None:
    entries = _entries()
    assert entries
    base_views = [v["id"] for v in BASE["views"]]
    for e in entries:
        m = e["manifest"]
        assert [v["id"] for v in m["views"]] == base_views
        doc = {**BASE, "pages": m["pages"]}
        errors = validate_manifest(doc)
        assert errors == [], f"seed {e['seed']} op {e['op']}: {errors}"


def test_the_layouts_are_not_all_the_same() -> None:
    pages = {json.dumps(e["manifest"]["pages"], sort_keys=True) for e in _entries()}
    assert len(pages) > 1
