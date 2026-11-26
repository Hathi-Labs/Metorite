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
        if (v.get("source") or {}).get("action") and "object" not in (
            v.get("source") or {}
        ):  # a stat or chart reads an action
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
    "field-visits": {"board", "form", "table", "detail", "stat", "chart"},
    "support-desk": {"board", "form", "table", "detail", "stat", "chart"},
    "purchase-requests": {"board", "form", "table", "detail", "stat", "chart"},
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


# --- STU-AF9: three templates declare a rail (D219) ----------------------------
# Fence (R7): the three templates keep a `nav` that names their pages. `validate_manifest`
# already refuses a row that names a missing page. This test holds that the rail exists
# and that each row opens a page that shows a view of the template.
FRAMED = ("field-visits", "support-desk", "purchase-requests")


@pytest.mark.parametrize("tid", FRAMED)
def test_a_framed_template_declares_a_rail_over_its_pages(tid: str) -> None:
    doc = _doc(tid)
    pages = {p["id"]: p for p in doc["pages"]}
    views = {v["id"] for v in doc["views"]}
    rows = [r for s in doc["nav"] for r in s["rows"]]
    assert [r["page"] for r in rows] == ["dashboard", "all", "mine", "open", "board"]
    for row in rows:
        shown = [c["view"] for c in pages[row["page"]]["cells"]]
        assert shown and set(shown) <= views
    assert validate_manifest(doc) == []


#: Templates that declare their own rail (K7 and K8, PR 2305, on purpose). AF-9 does not touch them.
OWN_RAIL = ("shipment-tracker", "site-survey")
#: The templates that still have no `nav`: the client draws the default rail for them (§5.5 rule 3).
NO_RAIL = ("expense-claims", "hiring", "spares")


def test_a_template_has_a_rail_from_af9_or_k7_k8_or_none_on_purpose() -> None:
    """Each template is in exactly one list: framed by AF-9, a rail of its own (K7 and K8), or
    no rail. A new template must pick one, so a rail never appears by accident. The test says
    which of the lists a template with a `nav` belongs to, and it holds the three that stay
    unframed free of a `nav`."""
    assert set(FRAMED) | set(OWN_RAIL) | set(NO_RAIL) == set(IDS), set(IDS) ^ (
        set(FRAMED) | set(OWN_RAIL) | set(NO_RAIL))
    assert not (set(FRAMED) & set(OWN_RAIL)) and not (set(NO_RAIL) & (set(FRAMED) | set(OWN_RAIL)))
    for tid in NO_RAIL:
        assert "nav" not in _doc(tid), tid
    for tid in (*FRAMED, *OWN_RAIL):
        assert _doc(tid)["nav"], tid


# --- STU-AF9b: Dashboard, My and Open, with counts on My and Open (D219) ---------
# Fence (R7): the order of the rail, the rows that count, the filters (parsed by the real
# door parser), the `me` token only on a person field, and the dashboard source.
from acb_common import studio_fields as sf  # noqa: E402
from acb_common import studio_query as sq  # noqa: E402
from acb_common.studio_manifest import MAX_NAV_COUNTS  # noqa: E402

#: The person field of each framed template. A template with none gets no My page.
PERSON = {"field-visits": "engineer", "support-desk": "assignee", "purchase-requests": "requester"}
ORDER = ["dashboard", "all", "mine", "open", "board"]


def _rows(doc: dict) -> list[dict]:
    return [r for s in doc["nav"] for r in s["rows"]]


def _object_specs(doc: dict) -> dict[str, list[sf.FieldSpec]]:
    names = {o["name"] for o in doc["objects"]}
    return {o["name"]: [sf.parse_field_spec(f, objects=names) for f in o["fields"]] for o in doc["objects"]}


def _page_views(doc: dict, page_id: str) -> list[dict]:
    page = next(p for p in doc["pages"] if p["id"] == page_id)
    by_id = {v["id"]: v for v in doc["views"]}
    return [by_id[c["view"]] for c in page["cells"]]


def _resolved(args: dict) -> dict:
    """The args as the door sees them: the `me` token replaced by an address."""
    out = json.loads(json.dumps(args))
    for cond in out.get("filter", []):
        if cond["value"] == "me":
            cond["value"] = "a.person@example.com"
    return out


@pytest.mark.parametrize("tid", FRAMED)
def test_af9b_the_rail_order_and_the_counted_rows(tid: str) -> None:
    doc = _doc(tid)
    rows = _rows(doc)
    expect = ORDER if tid in PERSON else [p for p in ORDER if p != "mine"]
    assert [r["page"] for r in rows] == expect
    assert {r["page"] for r in rows if r.get("count")} == {"mine", "open"} & set(expect)
    assert sum(1 for r in rows if r.get("count")) <= MAX_NAV_COUNTS
    for row in rows:
        if row.get("count"):
            # The count reads the first table or board of the page.
            assert _page_views(doc, row["page"])[0]["block"] in {"table", "board"}


@pytest.mark.parametrize("tid", FRAMED)
def test_af9b_the_filters_parse_with_the_door_parser(tid: str) -> None:
    doc = _doc(tid)
    specs = _object_specs(doc)
    me_seen = 0
    for v in doc["views"]:
        args = (v.get("source") or {}).get("args") or {}
        if "filter" not in args:
            continue
        obj = v["source"]["object"]
        fields = {f.key: f for f in specs[obj]}
        for cond in args["filter"]:
            if cond["value"] == "me":
                me_seen += 1
                assert fields[cond["field"]].type == "person" and cond["field"] == PERSON[tid]
        sq.parse_query(specs[obj], _resolved(args)["filter"])
    assert me_seen == 1  # only the My view holds the token


@pytest.mark.parametrize("tid", FRAMED)
def test_af9b_my_reads_the_person_field_and_open_hides_the_closed_value(tid: str) -> None:
    doc = _doc(tid)
    by_id = {v["id"]: v for v in doc["views"]}
    assert by_id["mine"]["source"]["args"]["filter"] == [{"field": PERSON[tid], "op": "eq", "value": "me"}]
    cond = by_id["open"]["source"]["args"]["filter"][0]
    obj = by_id["open"]["source"]["object"]
    options = next(f for o in doc["objects"] if o["name"] == obj for f in o["fields"] if f["key"] == "status")["options"]
    assert cond["field"] == "status" and cond["op"] in {"ne", "in"}
    named = cond["value"] if isinstance(cond["value"], list) else [cond["value"]]
    assert named and set(named) <= set(options)
    if cond["op"] == "ne":
        assert named == [options[-1]]  # the closed value is the last stage
    else:
        assert set(options) - set(named) == {options[-1], "rejected"}


@pytest.mark.parametrize("tid", FRAMED)
def test_af9b_the_dashboard_reads_an_aggregate_of_its_own_object(tid: str) -> None:
    doc = _doc(tid)
    specs = _object_specs(doc)
    actions = {a["id"]: a["uses"] for a in doc["actions"]}
    shown = _page_views(doc, "dashboard")
    assert {v["block"] for v in shown} == {"stat", "chart"}
    for v in shown:
        src = v["source"]
        assert actions[src["action"]] == f"object:{src['object']}.aggregate"
        args = src["args"]
        sq.parse_aggregate(specs[src["object"]], args.get("filter"), args.get("group_by"), args.get("measures"))
        group = args.get("group_by")
        if group:  # the store groups by a hot field only
            assert next(f for f in specs[src["object"]] if f.key == group["field"]).hot


def test_af9b_each_framed_template_holds_a_person_field() -> None:
    # A template with no person field would get no My page. All three hold one today.
    for tid, key in PERSON.items():
        fields = {f["key"]: f["type"] for o in _doc(tid)["objects"] for f in o["fields"]}
        assert fields[key] == "person"
