"""AF-8a: Mira writes the frame (`ai_studio.md` 5.5 rule 8, row AF-8).

Hermetic. The patch cases run through the real `studio_patch` and `apply_patch`.
The `record` key is not in the schema, and no slice owns it, so a test pins today's refusal.
"""
from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from acb_common import studio_manifest as sm
from acb_skills import studio_tools as st


class _Catalog:
    """An empty action catalog, so the check can run with no network."""

    def __init__(self, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        return httpx.Response(200, json={"actions": []}, request=httpx.Request("GET", url))


@pytest.fixture(autouse=True)
def _member(monkeypatch):
    monkeypatch.setattr(st, "_current_user_email", lambda: "jane@acme.test")
    monkeypatch.setattr(st.httpx, "AsyncClient", _Catalog)


def run(coro):
    return asyncio.run(coro)


def _base() -> dict:
    return {
        "studio": 1, "id": "demo", "name": "Demo", "purpose": "Test.",
        "connections": [], "objects": [], "actions": [], "workflows": [],
        "views": [{"id": "all-tasks", "title": "All", "block": "table"},
                  {"id": "by-status", "title": "By status", "block": "table"}],
    }


def _framed() -> dict:
    return {**_base(), **copy.deepcopy(st.FRAME_EXAMPLE)}


def _patch(doc: dict, ops: list[dict]) -> dict:
    before = copy.deepcopy(doc)
    out = run(st.studio_patch(doc, ops))
    assert doc == before  # the input never changes
    return out


def _refused(out: dict) -> str:
    assert out["ok"] is False and "manifest" not in out
    assert len(out["errors"]) == 1, out["errors"]
    msg = out["errors"][0]["message"]
    assert msg.endswith(".") and len(msg) < 300
    return msg


# -- the frame part ---------------------------------------------------------


def test_frame_part_is_listed_and_served() -> None:
    assert "frame" in st.DESCRIBE_PARTS
    out = run(st.studio_describe("frame"))
    assert out["ok"] is True and out["part"] == "frame"
    assert "frame" in run(st.studio_describe("nope"))["errors"][0]["message"]


def test_frame_caps_and_icons_come_from_the_model() -> None:
    f = run(st.studio_describe("frame"))["frame"]
    assert f["caps"] == {
        "pages": sm.MAX_PAGES, "tabs_per_page": sm.MAX_TABS_PER_PAGE,
        "cells_per_page": sm.MAX_CELLS_PER_PAGE, "columns": sm.MAX_COLS,
        "nav_sections": sm.MAX_NAV_SECTIONS, "nav_rows_per_section": sm.MAX_NAV_ROWS}
    assert f["icons"] == list(sm.NAV_ICONS)
    text = " ".join(f["rules"])
    assert "A nav needs pages" in text and "page id that the manifest declares" in text
    assert "record" not in json.dumps(f)  # no slice owns the key yet
    assert "count" not in json.dumps(f)  # the rail count belongs to AF-3a


def test_frame_example_validates() -> None:
    doc = _framed()
    assert validate(doc) == []
    assert run(st.studio_patch(doc, []))["ok"] is True
    ex = run(st.studio_describe("frame"))["frame"]["example"]
    assert ex == st.FRAME_EXAMPLE
    assert len(ex["nav"]) == 2


def validate(doc: dict) -> list:
    return sm.validate_manifest(doc)


# -- the outline ------------------------------------------------------------


def test_outline_has_no_new_line_without_a_frame() -> None:
    text = st.outline_text(_base())
    assert len(text.split("\n")) == 7
    assert "Pages" not in text and "Nav" not in text
    assert text == "\n".join([
        "App demo: Demo", "Objects (0): none", "Actions (0): none",
        "Views (2): all-tasks, by-status", "Workflows (0): none", "Skill: none",
        "Connections (0): none"])


def test_outline_names_the_pages_and_counts_the_nav() -> None:
    lines = st.outline_text(_framed()).split("\n")
    assert "Pages (3): home, tasks, help" in lines
    assert "Nav: 2 sections, 3 rows" in lines


def test_outline_cuts_the_page_names_and_the_card_fits() -> None:
    wide = {"id": "x", "name": "N" * 500, "objects": [{"name": "o" * 500, "fields": []}] * 50,
            "views": [{"id": "v" * 500}] * 50, "workflows": [{"id": "w" * 500}] * 50,
            "actions": [{"id": "a" * 500}] * 50, "connections": ["c" * 500] * 50,
            "pages": [{"id": "p" * 500}] * 50,
            "nav": [{"title": "t", "rows": [{"page": "p"}] * 50}] * 9}
    text = st.outline_text(wide, fence=True)
    assert "and 46 more" in text
    assert all(len(x) <= st.MAX_OUTLINE_LINE_CHARS for x in text.split("\n"))
    assert len(st.plan_card_text(wide, held=True)) <= 4000
    assert st.outline_text({"pages": "x", "nav": [1, {"rows": 3}]}) .count("\n") >= 6


# -- the skeleton keeps the frame keys absent -------------------------------


def test_the_skeleton_writes_no_pages_and_no_nav() -> None:
    doc = st._skeleton("An event tracker for a stall", "demo")
    assert "pages" not in doc and "nav" not in doc and "record" not in doc
    assert validate(doc) == []
    drafted = run(st.studio_draft("An event tracker for a stall"))["manifest"]
    assert "pages" not in drafted and "nav" not in drafted


# -- patches through the real path ------------------------------------------


def test_patch_adds_a_section_and_a_row() -> None:
    doc = _framed()
    out = _patch(doc, [
        {"op": "add", "path": "/nav/-", "value": {"title": "Extra", "rows": []}},
        {"op": "add", "path": "/nav/2/rows/-", "value": {"page": "help", "icon": "Star"}},
        {"op": "add", "path": "/nav/0/rows/0", "value": {"page": "help", "label": "First"}},
    ])
    assert out["ok"] is True, out["errors"]
    nav = out["manifest"]["nav"]
    assert [s["title"] for s in nav] == ["Work", "More", "Extra"]
    assert nav[0]["rows"][0] == {"page": "help", "label": "First"}


def test_patch_reorders_pages() -> None:
    doc = _framed()
    out = _patch(doc, [
        {"op": "add", "path": "/pages/0", "value": copy.deepcopy(doc["pages"][2])},
        {"op": "remove", "path": "/pages/3"},
    ])
    assert out["ok"] is True, out["errors"]
    assert [p["id"] for p in out["manifest"]["pages"]] == ["help", "home", "tasks"]


def test_patch_renames_a_page_and_fixes_its_rows_in_one_call() -> None:
    doc = _framed()
    rename = {"op": "replace", "path": "/pages/2/id", "value": "guide"}
    fix = {"op": "replace", "path": "/nav/1/rows/0/page", "value": "guide"}
    msg = _refused(_patch(doc, [rename]))
    assert "help" in msg and "not declared" in msg
    out = _patch(doc, [rename, fix])
    assert out["ok"] is True, out["errors"]
    assert out["manifest"]["nav"][1]["rows"][0]["page"] == "guide"


@pytest.mark.parametrize("ops,words", [
    ([{"op": "add", "path": "/nav/0/rows/0/icon", "value": "Rocket"}], "Icon is not in the icon list"),
    ([{"op": "add", "path": "/nav/0/rows/-", "value": {"page": "nowhere"}}], "not declared"),
    ([{"op": "add", "path": "/nav/0/rows/0/colour", "value": "red"}], "is a style choice"),
])
def test_patch_refuses_a_bad_nav_in_one_sentence(ops, words) -> None:
    doc = _framed()
    assert words in _refused(_patch(doc, ops))


def test_patch_refuses_a_ninth_page() -> None:
    doc = _framed()
    ops = [{"op": "add", "path": "/pages/-",
            "value": {"id": f"p{i}", "cells": [{"view": "all-tasks"}]}}
           for i in range(sm.MAX_PAGES - 3 + 1)]
    msg = _refused(_patch(doc, ops))
    assert f"at most {sm.MAX_PAGES} items" in msg


def test_patch_refuses_a_thirteenth_nav_row() -> None:
    doc = _framed()
    ops = [{"op": "add", "path": "/nav/0/rows/-", "value": {"page": "home"}}
           for _ in range(sm.MAX_NAV_ROWS - 2 + 1)]
    msg = _refused(_patch(doc, ops))
    assert f"at most {sm.MAX_NAV_ROWS} items" in msg


def test_patch_refuses_a_nav_with_no_pages() -> None:
    doc = _base()
    ops = [{"op": "add", "path": "/nav", "value": [{"title": "A", "rows": [{"page": "home"}]}]}]
    msg = _refused(_patch(doc, ops))
    assert "home" in msg and "not declared in `pages`" in msg


def test_patch_refuses_the_record_key() -> None:
    doc = _framed()
    ops = [{"op": "add", "path": "/record", "value": {"tabs": []}}]
    msg = _refused(_patch(doc, ops))
    assert msg == "`record` is not a v1 manifest key. Remove it."
