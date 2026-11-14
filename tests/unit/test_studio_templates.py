"""The Studio app templates (STU-K1a): the registry holds only valid, plain manifests.

Spec: ``project-docs/specs/ai_studio.md`` section 14 row K1. Code:
``acb_common/studio_templates/``. The R8 half is ``test_studio_templates_r8.py``.
``studio_manifest.validate_manifest`` is the fence's owner (R7), and this file
holds the registry to it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from acb_common import studio_templates as reg
from acb_common.studio_manifest import validate_manifest

DIR = Path(reg.__file__).resolve().parent
CATALOG: dict[str, str] = {}
IDS = list(reg.TEMPLATE_IDS)


def _doc(tid: str) -> dict:
    doc = reg.get_template(tid)
    assert doc is not None
    return doc


@pytest.mark.parametrize("tid", IDS)
def test_each_template_passes_validate_manifest_with_the_catalog(tid: str) -> None:
    assert validate_manifest(_doc(tid), catalog=CATALOG) == []


@pytest.mark.parametrize("tid", IDS)
def test_each_template_names_only_declared_blocks_and_fields(tid: str) -> None:
    doc = _doc(tid)
    objects = {o["name"]: {f["key"] for f in o["fields"]} for o in doc["objects"]}
    actions = {a["id"] for a in doc["actions"]}
    views = {v["id"] for v in doc["views"]}
    for v in doc["views"]:
        if (v.get("source") or {}).get("action"):  # a stat or chart reads an action
            assert v["source"]["action"] in actions, v["id"]
            continue
        obj = (v.get("source") or {}).get("object") or v.get("object")
        assert obj in objects, v["id"]
        used = [*v.get("fields", []), *v.get("columns", [])]
        used += [x for x in (v.get("group_by"), v.get("sort")) if x]
        card = v.get("card") or {}
        used += [x for x in [card.get("title"), *card.get("meta", [])] if x]
        assert set(used) <= objects[obj], v["id"]
        if v.get("submit"):
            assert v["submit"] in actions
    for a in doc["actions"]:
        if a["uses"].startswith("object:"):
            assert a["uses"].split(":")[1].split(".")[0] in objects
        assert set(a.get("set", {})) <= objects["visit"] if "visit" in objects else True
    assert set(doc["skill"]["cards"]) <= views
    assert {j["view"] for j in doc["shell"]["jobs"]} <= views


def test_ids_are_unique_and_name_and_purpose_are_set() -> None:
    assert len(IDS) == len(set(IDS))
    for tid in IDS:
        doc = _doc(tid)
        assert doc["id"] == tid
        assert doc["name"].strip() and doc["purpose"].strip()


def test_the_files_equal_the_registry() -> None:
    files = {p.stem.replace("_", "-") for p in DIR.glob("*.json")}
    assert files == set(IDS)


#: The views that each template holds. Only built blocks: no calendar (D5), no map (D10).
EXPECTED_BLOCKS: dict[str, set[str]] = {
    "field-visits": {"board", "form", "table", "detail"},
    "support-desk": {"board", "form", "table", "detail"},
    "purchase-requests": {"board", "form", "table", "detail"},
    "expense-claims": {"board", "form", "table", "detail"},
    "hiring": {"board", "form", "table", "detail"},
    "spares": {"board", "form", "table", "detail"},
    "shipment-tracker": {"board", "form", "table", "detail", "stat"},
    "site-survey": {"board", "form", "table", "detail", "stat"},
}


def test_every_template_has_an_expected_block_row() -> None:
    assert set(EXPECTED_BLOCKS) == set(IDS)


@pytest.mark.parametrize("tid", IDS)
def test_each_template_holds_its_blocks_and_nothing_unbuilt(tid: str) -> None:
    doc = _doc(tid)
    blocks = {v["block"] for v in doc["views"]}
    assert blocks == EXPECTED_BLOCKS[tid]
    # What is not built stays out: D5 calendar, D10 map, workflows, connections.
    assert not blocks & {"calendar", "map", "timeline", "mindMap", "processFlow", "custom", "code"}
    assert "workflows" not in doc and "connections" not in doc
    assert "tell_customer" not in {a["id"] for a in doc["actions"]}
    assert all(a["uses"].startswith("object:") for a in doc["actions"])


def test_every_registered_template_is_listed_and_served_fresh() -> None:
    assert [t["id"] for t in reg.list_templates()] == IDS
    for tid in IDS:
        a = _doc(tid)
        a["name"] = "changed"
        assert _doc(tid)["name"] != "changed"


@pytest.mark.parametrize("tid", IDS)
def test_no_colour_px_or_credential(tid: str) -> None:
    text = (DIR / f"{tid.replace('-', '_')}.json").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\d\s*px\b|rgb\(|hsl\(", text)
    assert not re.search(r"\b(color|colour|font|password|secret|token|api_?key)\b", text, re.I)


def test_an_unknown_template_answers_none_and_a_copy_is_fresh() -> None:
    assert reg.get_template("nope") is None
    assert reg.get_template(None) is None
    assert reg.get_template(["field-visits"]) is None
    a = _doc("field-visits")
    a["name"] = "changed"
    assert _doc("field-visits")["name"] == "Field visits"


def test_the_template_route_list_is_the_registry() -> None:
    listed = reg.list_templates()
    assert [t["id"] for t in listed] == IDS
    for t in listed:
        assert set(t) == {"id", "name", "purpose"}
    json.dumps(listed)  # the answer is plain JSON
