"""The pages that `tabEdit.ts` makes pass the server check (STU-AF8b-3a).

`tabEdit.test.ts` runs 200 random edits of the tabs and the cells for three seeds. It compares
the manifests at op 50, 100, 150 and 200 with a JSON fixture. This test reads that fixture and
runs the one validator, `validate_manifest`, on every manifest. No DB.

The fixture has NO rail row with `count`. The server on main refuses the key. A follow-up adds one
after AF-3a merges.
"""

from __future__ import annotations

import json
from pathlib import Path

from acb_common.studio_manifest import validate_manifest

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "workbench/control_plane/src/lib/studio/__fixtures__/tabEdit_outputs.json"
)


BASE: dict = {
    "studio": 1,
    "id": "tab-app",
    "name": "Tab app",
    "purpose": "Check the tabs of the page editor.",
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
        {"id": "list", "block": "table", "source": {"object": "visit"}, "columns": ["customer"]},
    ],
}


def _entries() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_is_not_empty() -> None:
    entries = _entries()
    assert len(entries) == 12
    assert {e["seed"] for e in entries} == {11, 23, 47}


def test_every_manifest_passes_the_server_check() -> None:
    entries = _entries()
    assert entries
    base_views = [v["id"] for v in BASE["views"]]
    assert validate_manifest({**BASE, "pages": [{"id": "p", "cells": [{"view": "list"}]}]}) == []
    for e in entries:
        m = e["manifest"]
        assert [v["id"] for v in m["views"]] == base_views
        errors = validate_manifest({**BASE, "pages": m["pages"]})
        assert errors == [], f"seed {e['seed']} op {e['op']}: {errors}"


def test_the_fixture_has_no_count_row() -> None:
    for e in _entries():
        assert "nav" not in e["manifest"]
        assert "nav" not in BASE


def test_the_manifests_are_not_all_the_same() -> None:
    pages = {json.dumps(e["manifest"].get("pages"), sort_keys=True) for e in _entries()}
    assert len(pages) > 1


def test_some_page_has_tabs_and_some_has_cells() -> None:
    pages = [p for e in _entries() for p in e["manifest"]["pages"]]
    assert any(p.get("tabs") for p in pages)
    assert any(p.get("cells") for p in pages)
