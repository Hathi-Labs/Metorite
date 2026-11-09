"""The direct edits of the Studio IDE keep the manifest valid (STU-G3c, fix round 2).

``workbench/control_plane/src/lib/studio/draftEdit.ts`` deletes an object, a view or a field and takes
out what names it. The vitest file ``draftEdit.test.ts`` writes the result of each case to
``tests/fixtures/studio_cascade_cases.json`` and keeps it equal to the code. This test runs the real
validator (``validate_manifest``) on the base and on each output. A reference that the cascade leaves
behind turns it red.

Mutation: drop the ``usesObject`` filter of ``skill.actions`` in ``dropActions``, or the empty-page
filter in ``dropViews``, then run ``G3C_WRITE_FIXTURE=1`` vitest. The matching case here goes red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from acb_common.studio_manifest import validate_manifest

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "studio_cascade_cases.json"
_DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_the_base_manifest_is_valid() -> None:
    assert validate_manifest(_DATA["base"]) == []


@pytest.mark.parametrize("case", _DATA["cases"], ids=lambda c: c["name"])
def test_a_cascade_output_passes_the_validator(case: dict) -> None:
    errors = validate_manifest(case["output"])
    assert errors == [], [(e.path, e.message) for e in errors]


def test_the_cases_cover_an_object_a_view_and_a_field() -> None:
    assert [c["name"] for c in _DATA["cases"]] == [
        "delete the visit object", "delete the visit-table view", "delete the zip field"]
