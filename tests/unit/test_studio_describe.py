"""The studio_describe tool (STU-G1b, ``ai_studio.md`` section 14).

Hermetic. The tool reads code, so the tests make no network or DB call and set
no member. Mutations (run, then restored):

* hand-edit the schema in the tool -> the manifest test is red;
* drop a field type in ``_describe_fields`` -> the fields test is red;
* add a token to ``_describe_blocks`` -> the blocks test is red;
* lower ``MAX_MANIFEST_CHARS`` -> the cap test is red.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from acb_common import studio_fields as sfl
from acb_common.studio_manifest import (
    BLOCKS,
    OBJECT_OPS,
    RESERVED_BLOCKS,
    Manifest,
    validate_manifest,
)
from acb_skills import studio_tools as st

REPO = Path(__file__).resolve().parents[2]
SCHEMA_FILE = REPO / "packages/acb_common/acb_common/studio_manifest.schema.json"


def run(part: str | None = None) -> dict:
    return asyncio.run(st.studio_describe() if part is None else st.studio_describe(part))


def test_manifest_part_equals_the_live_export_and_the_committed_schema() -> None:
    out = run("manifest")
    committed = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert out["ok"] is True and out["part"] == "manifest"
    assert out["schema"] == Manifest.model_json_schema() == committed


def test_the_default_part_is_the_manifest() -> None:
    assert run() == run("manifest")


def test_fields_part_equals_the_field_module() -> None:
    f = run("fields")["fields"]
    assert f["types"] == list(sfl.FIELD_TYPES) and len(f["types"]) == 16
    assert f["hot_types"] == list(sfl.HOT_TYPES)
    assert f["name_pattern"] == sfl.NAME_PATTERN
    assert f["limits"] == {
        "max_fields_per_object": sfl.MAX_FIELDS_PER_OBJECT,
        "max_options": sfl.MAX_OPTIONS,
        "max_option_chars": sfl.MAX_OPTION_CHARS,
        "max_label_chars": sfl.MAX_LABEL_CHARS,
        "max_many_items": sfl.MAX_MANY_ITEMS,
        "max_text_chars": sfl.MAX_TEXT_CHARS,
    }


def test_blocks_part_equals_the_block_list() -> None:
    b = run("blocks")["blocks"]
    assert b["tokens"] == list(BLOCKS) and len(b["tokens"]) == 13
    assert b["reserved"] == list(RESERVED_BLOCKS)
    assert b["object_ops"] == list(OBJECT_OPS)
    assert not set(b["tokens"]) & set(b["reserved"])


@pytest.mark.parametrize("part", st.DESCRIBE_PARTS)
def test_every_result_is_under_the_cap(part: str) -> None:
    out = run(part)
    assert len(json.dumps(out, ensure_ascii=False, separators=(",", ":"))) <= st.MAX_MANIFEST_CHARS


def test_a_part_over_the_cap_is_refused_in_a_sentence(monkeypatch) -> None:
    monkeypatch.setattr(st, "MAX_MANIFEST_CHARS", 100)
    out = run("manifest")
    assert out["ok"] is False and "schema" not in out
    assert "over the limit of 100" in out["errors"][0]["message"]


@pytest.mark.parametrize("bad", ["", "nope", "MANIFEST", None, 5])
def test_a_bad_part_is_a_sentence_and_does_not_raise(bad) -> None:
    out = asyncio.run(st.studio_describe(bad))
    assert out["ok"] is False
    msg = out["errors"][0]["message"]
    assert all(p in msg for p in st.DESCRIBE_PARTS)


def test_it_needs_no_member_and_no_network(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    monkeypatch.setattr(st, "_current_user_email", lambda: "")
    assert run("manifest")["ok"] is True


def _doc(block: str) -> dict:
    return {
        "studio": 1, "id": "demo", "name": "Demo", "purpose": "Test.",
        "connections": [], "objects": [], "actions": [], "workflows": [],
        "views": [{"id": "v1", "title": "V", "block": block}],
    }


def test_the_block_enum_equals_what_the_validator_accepts() -> None:
    """The schema enum, the tool tokens and the validator agree."""
    schema = json.dumps(run("manifest")["schema"])
    for token in BLOCKS:
        assert f'"{token}"' in schema
        errs = [e for e in validate_manifest(_doc(token)) if e.path.endswith("block")]
        assert not errs, (token, errs)
    for token in [*RESERVED_BLOCKS, "nope"]:
        errs = [e for e in validate_manifest(_doc(token)) if e.path.endswith("block")]
        assert errs, token
        assert token not in run("blocks")["blocks"]["tokens"]
