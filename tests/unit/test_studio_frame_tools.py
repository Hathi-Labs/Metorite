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
        "cells_per_list": sm.MAX_CELLS_PER_PAGE, "columns": sm.MAX_COLS,
        "nav_sections": sm.MAX_NAV_SECTIONS, "nav_rows_per_section": sm.MAX_NAV_ROWS,
        "nav_counts": sm.MAX_NAV_COUNTS}
    assert f["icons"] == list(sm.NAV_ICONS)
    text = " ".join(f["rules"])
    assert "A nav needs pages" in text and "page id that the manifest declares" in text
    assert "record" not in json.dumps(f)  # no slice owns the key yet
    assert "for each list" in text and "`count: true`" in text
    assert "cells_per_page" not in json.dumps(f)


def test_frame_example_validates() -> None:
    doc = _framed()
    assert validate(doc) == []
    assert run(st.studio_patch(doc, []))["ok"] is True
    ex = run(st.studio_describe("frame"))["frame"]["example"]
    assert ex == st.FRAME_EXAMPLE
    assert len(ex["nav"]) == 2
    assert [r.get("count") for sec in ex["nav"] for r in sec["rows"]] == [None, True, None]
    assert run(st.studio_patch(_base(), []))["ok"] is True


def test_the_caps_of_the_frame_part_move_with_the_model(monkeypatch) -> None:
    """Mutation: each imported constant +100 moves its cap in the answer, and the size fits."""
    names = {"MAX_PAGES": "pages", "MAX_TABS_PER_PAGE": "tabs_per_page",
             "MAX_CELLS_PER_PAGE": "cells_per_list", "MAX_COLS": "columns",
             "MAX_NAV_SECTIONS": "nav_sections", "MAX_NAV_ROWS": "nav_rows_per_section",
             "MAX_NAV_COUNTS": "nav_counts"}
    for const, key in names.items():
        assert getattr(st, const) == getattr(sm, const)
        with monkeypatch.context() as m:
            m.setattr(st, const, getattr(sm, const) + 100)
            assert run(st.studio_describe("frame"))["frame"]["caps"][key] == getattr(sm, const) + 100
    assert "100" not in json.dumps(run(st.studio_describe("frame"))["frame"]["rules"])


def test_the_frame_part_fits_the_describe_cap_with_room() -> None:
    size = len(json.dumps(run(st.studio_describe("frame")), ensure_ascii=False, separators=(",", ":")))
    assert size <= st.MAX_MANIFEST_CHARS // 2, size


def test_each_step_example_validates_through_studio_patch() -> None:
    steps = run(st.studio_describe("frame"))["frame"]["steps"]
    assert len(steps) == 6
    for step in steps:
        out = _patch(_framed(), step["ops"])
        assert out["ok"] is True, (step["act"], out["errors"])
        assert validate(out["manifest"]) == [], step["act"]
        assert all(o["op"] in ("add", "replace", "remove") for o in step["ops"])


def test_the_step_examples_make_the_shape_they_name() -> None:
    by = {x["act"].split(" (")[0].rstrip("."): x for x in st.FRAME_STEPS}
    m = _patch(_framed(), by["Use tabs on a flat page"]["ops"])["manifest"]
    assert "cells" not in m["pages"][0] and len(m["pages"][0]["tabs"][0]["cells"]) == 2
    m = _patch(_framed(), by["Return to a flat page"]["ops"])["manifest"]
    assert "tabs" not in m["pages"][1] and len(m["pages"][1]["cells"]) == 2
    m = _patch(_framed(), by["Move a tab to a new index"]["ops"])["manifest"]
    assert [t["id"] for t in m["pages"][1]["tabs"]] == ["stats", "open"]
    m = _patch(_framed(), by["Move a cell to a new index in its list"]["ops"])["manifest"]
    assert [c["view"] for c in m["pages"][0]["cells"]] == ["by-status", "all-tasks"]
    m = _patch(_framed(), by["Move a cell to another tab"]["ops"])["manifest"]
    assert m["pages"][1]["tabs"][0]["cells"] == [] and len(m["pages"][1]["tabs"][1]["cells"]) == 2


def test_the_tab_and_cell_caps_refuse_one_more() -> None:
    tabs = [{"id": f"t{i}", "title": "T", "cells": [{"view": "all-tasks"}]}
            for i in range(sm.MAX_TABS_PER_PAGE)]
    out = run(st.studio_patch(_framed(), [
        {"op": "replace", "path": "/pages/1/tabs", "value": tabs},
        {"op": "add", "path": "/pages/1/tabs/-", "value": tabs[0] | {"id": "z"}}]))
    assert out["ok"] is False and f"at most {sm.MAX_TABS_PER_PAGE} items" in out["errors"][0]["message"]
    cells = [{"view": "all-tasks"}] * (sm.MAX_CELLS_PER_PAGE + 1)
    out = run(st.studio_patch(_framed(), [{"op": "replace", "path": "/pages/1/tabs/0/cells", "value": cells}]))
    assert out["ok"] is False and f"at most {sm.MAX_CELLS_PER_PAGE} items" in out["errors"][0]["message"]
    # twelve cells in each of two tabs is fine: the cap is for each list
    two = [{"id": "a", "title": "A", "cells": cells[:-1]}, {"id": "b", "title": "B", "cells": cells[:-1]}]
    assert run(st.studio_patch(_framed(), [{"op": "replace", "path": "/pages/1/tabs", "value": two}]))["ok"]


def test_the_acts_that_break_the_tab_rules_are_refused() -> None:
    both = [{"op": "add", "path": "/pages/1/cells", "value": [{"view": "all-tasks"}]}]
    assert run(st.studio_patch(_framed(), both))["ok"] is False  # tabs and cells both
    empty = [{"op": "remove", "path": "/pages/0/cells/1"}, {"op": "remove", "path": "/pages/0/cells/0"}]
    assert run(st.studio_patch(_framed(), empty))["ok"] is False  # an empty page
    dup = [{"op": "add", "path": "/pages/1/tabs/-", "value": {"id": "open", "title": "X", "cells": []}}]
    assert run(st.studio_patch(_framed(), dup))["ok"] is False  # a repeated tab id
    mv = [{"op": "move", "from": "/pages/0/cells/0", "path": "/pages/0/cells/1"}]
    assert "not supported" in run(st.studio_patch(_framed(), mv))["errors"][0]["message"]


def test_a_count_row_needs_a_table_or_board_page_and_the_cap_holds() -> None:
    doc = _framed()
    doc["views"].append({"id": "note", "title": "Note", "block": "text", "text": "x"})
    doc["pages"].append({"id": "notes", "cells": [{"view": "note"}]})
    bad = run(st.studio_patch(doc, [{"op": "add", "path": "/nav/1/rows/-", "value": {"page": "notes", "count": True}}]))
    assert bad["ok"] is False and "no table or board view" in bad["errors"][0]["message"]
    rows = [{"page": "home", "count": True}] * (sm.MAX_NAV_COUNTS + 1)
    over = run(st.studio_patch(_framed(), [{"op": "replace", "path": "/nav/1/rows", "value": rows}]))
    assert over["ok"] is False and f"at most {sm.MAX_NAV_COUNTS} counts" in over["errors"][0]["message"]


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
    assert "Nav: 2 sections, 3 rows, 1 with a count" in lines
    plain = _framed()
    del plain["nav"][0]["rows"][1]["count"]
    assert "Nav: 2 sections, 3 rows" in st.outline_text(plain).split("\n")


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
    assert "count" not in json.dumps(doc)
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
