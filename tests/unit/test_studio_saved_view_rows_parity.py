"""The specs that `savedViewRows.ts` writes pass the server check (STU-AF-10b-1).

`savedViewRows.test.ts` builds a spec from each state of a list bar. It compares the specs with a
JSON fixture and never writes it. This test reads the fixture. It runs each spec through
`check_spec` of the views route, the one function that POST and PATCH call. No DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.studio import views  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "workbench/control_plane/src/lib/studio/__fixtures__/savedViewRows_specs.json"
)


def _entries() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_is_not_empty_and_not_all_equal() -> None:
    entries = _entries()
    assert len(entries) >= 10
    assert len({json.dumps(e["spec"], sort_keys=True) for e in entries}) > 5
    assert any(e["spec"]["filter"] for e in entries)
    assert any("sort" in e["spec"] for e in entries)
    assert any("layout" in e["spec"] for e in entries)


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: e["name"])
def test_each_spec_passes_the_server_check(entry: dict) -> None:
    assert views.check_spec(entry["spec"]) == entry["spec"]
