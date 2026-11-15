"""WS-STU REL-R1: the title rule and the shape of ``record_titles``, with no database.

The real database half is ``test_studio_relation_names_r8.py``. The rule is
``studio_fields.title_key_of`` and ``record_title``. Its client twin is
``packagePreview.ts``. Both read ``tests/fixtures/studio_title_rule.json``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import studio_fields as sf
from acb_common.studio_access import Viewer
from gateway.studio import objects_store as store

_ROOT = Path(__file__).resolve().parents[2]
_CASES = json.loads((_ROOT / "tests/fixtures/studio_title_rule.json").read_text(encoding="utf-8"))["cases"]
OID = "00000000-0000-0000-0000-00000000000a"
PID = "00000000-0000-0000-0000-00000000000b"


def _fields(raw: list[dict[str, Any]]) -> list[sf.FieldSpec]:
    return [sf.parse_field_spec(f) for f in raw]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_the_title_rule_gives_the_key_and_the_title_of_the_shared_table(case: dict[str, Any]) -> None:
    fields = _fields(case["fields"])
    assert sf.title_key_of(fields) == case["key"]
    assert sf.record_title(fields, case["data"]) == case["title"]


def test_a_long_title_is_cut_at_160_characters_after_the_trim() -> None:
    fields = _fields([{"key": "name", "type": "text"}])
    got = sf.record_title(fields, {"name": "  " + "é" * 300 + "  "})
    assert got == "é" * sf.MAX_TITLE_CHARS and sf.MAX_TITLE_CHARS == 160


def test_record_titles_takes_viewer_as_a_required_keyword_and_the_cap_is_200() -> None:
    param = inspect.signature(store.record_titles).parameters["viewer"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty
    assert store.RELATION_NAMES_CAP == 200


def test_a_page_of_non_uuid_ids_runs_no_statement_and_gives_none_for_each() -> None:
    class Db:
        ran = 0

        async def execute(self, *a: Any, **k: Any) -> Any:
            Db.ran += 1
            raise AssertionError("no statement may run")

    ids = ["x", "1", "", "not-a-uuid", "x"]
    got = asyncio.run(store.record_titles(Db(), OID, ids, package_id=PID, viewer=Viewer("a@x.test")))
    assert got == {"x": None, "1": None, "": None, "not-a-uuid": None} and Db.ran == 0


def test_a_bad_viewer_is_refused_before_any_statement() -> None:
    with pytest.raises(store.ObjectStoreError):
        asyncio.run(store.record_titles(SimpleNamespace(), OID, [], package_id=PID,
                                        viewer="a@x.test"))  # type: ignore[arg-type]


def test_the_titles_statement_names_the_org_the_package_and_the_row_rule() -> None:
    # The bind of the records themselves, and not only the one of the `_IN_PACKAGE` subquery.
    assert f"FROM studio_records WHERE organization_id = {store._BOUND_ORG} AND" in store._GET_RECORDS_SQL
    assert "o.package_id = CAST(:pid AS uuid)" in store._GET_RECORDS_SQL
    assert f"{store._GET_RECORDS_SQL} AND {store._ACCESS_ROW_SQL}" == store._GET_RECORDS_SCOPED_SQL
    assert "ANY(CAST(:rids AS uuid[]))" in store._GET_RECORDS_SQL
