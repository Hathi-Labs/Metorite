"""STU-BETA-2 (task 1810): a chat-built object gets its five record actions.

The rule (one rule, ``ai_studio.md`` section 14, row BETA-2): an object that
declares NO record action (no ``object:<name>.<verb>`` action) gets the five
(list, get, create, update, delete), and the diff or the plan card names them.
An object with at least one record action stays as the author wrote it.

R7 fence. Mutation: in ``studio_tools._checked`` replace the
``with_record_actions`` call by ``completed, added = manifest, []`` and these
tests go red: ``test_validate_adds_the_five_actions``,
``test_patch_that_adds_an_object_adds_its_five_actions``,
``test_the_plan_card_names_the_added_actions``.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest
from acb_common.studio_manifest import OBJECT_OPS, validate_manifest
from acb_skills import ask_tools
from acb_skills import studio_tools as st

pytestmark = []


def _obj(name: str = "visit") -> dict[str, Any]:
    return {"name": name, "label": name.title(),
            "fields": [{"key": "title", "type": "text", "label": "Title"}]}


def _app(objects: list[dict[str, Any]], actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"studio": 1, "id": "visits", "name": "Visits", "purpose": "Log visits.",
            "objects": objects, "actions": actions or []}


def _uses(manifest: dict[str, Any]) -> set[str]:
    return {a["uses"] for a in manifest["actions"]}


def _five(name: str) -> set[str]:
    return {f"object:{name}.{v}" for v in OBJECT_OPS}


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    async def rows(*, app: str = "") -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(st, "_read_catalog", rows)
    monkeypatch.setattr(st, "_current_user_email", lambda: "jane@acme.test")


async def test_validate_adds_the_five_actions() -> None:
    out = await st.studio_validate(_app([_obj()]))
    assert out["ok"], out
    assert _uses(out["manifest"]) == _five("visit")
    assert len({a["id"] for a in out["manifest"]["actions"]}) == 5
    assert validate_manifest(out["manifest"]) == []
    assert any("visit" in line and "add" in line for line in out["diff"]), out["diff"]
    assert len(out["added_actions"]) == 5


async def test_draft_adds_nothing_to_an_app_with_no_object() -> None:
    out = await st.studio_draft("Track visits")
    assert out["ok"] and out["manifest"]["actions"] == []
    assert "added_actions" not in out


async def test_patch_that_adds_an_object_adds_its_five_actions() -> None:
    base = (await st.studio_draft("Track visits"))["manifest"]
    out = await st.studio_patch(base, [{"op": "add", "path": "/objects/-", "value": _obj()}])
    assert out["ok"], out
    assert _uses(out["manifest"]) == _five("visit")
    assert out["diff"][0] == "add /objects/-"
    assert any(line.startswith("add /actions") and "visit" in line for line in out["diff"])


async def test_each_object_gets_its_own_unique_ids() -> None:
    out = await st.studio_validate(_app([_obj("visit"), _obj("client")]))
    assert out["ok"], out
    ids = [a["id"] for a in out["manifest"]["actions"]]
    assert len(ids) == 10 == len(set(ids))
    assert _uses(out["manifest"]) == _five("visit") | _five("client")


async def test_an_id_that_is_taken_is_not_reused() -> None:
    taken = [{"id": "visit_list", "uses": "integration:slack.post"}]
    done, added = st.with_record_actions(_app([_obj()], taken))
    ids = [a["id"] for a in done["actions"]]
    assert len(ids) == len(set(ids)) == 6 and len(added) == 5


async def test_an_object_with_one_record_action_is_left_alone() -> None:
    one = [{"id": "find", "uses": "object:visit.list"}]
    out = await st.studio_validate(_app([_obj()], one))
    assert out["ok"] and out["manifest"]["actions"] == one
    assert "added_actions" not in out and out["diff"] == []


async def test_only_the_object_with_no_action_gets_them() -> None:
    one = [{"id": "find", "uses": "object:visit.list"}]
    out = await st.studio_validate(_app([_obj("visit"), _obj("client")], one))
    assert out["ok"], out
    assert _uses(out["manifest"]) == {"object:visit.list"} | _five("client")


async def test_the_input_is_not_changed() -> None:
    doc = _app([_obj()])
    before = copy.deepcopy(doc)
    await st.studio_validate(doc)
    assert doc == before


@pytest.mark.parametrize("bad", [None, "x", [], {"objects": "no"},
                                 {"objects": [1, {"name": "Bad Name"}], "actions": "no"}])
def test_the_helper_never_raises_on_a_bad_value(bad: Any) -> None:
    _done, added = st.with_record_actions(bad)
    assert added == []


async def test_an_invalid_manifest_still_returns_errors_and_no_manifest() -> None:
    bad = _app([_obj()])
    bad["objects"][0]["fields"][0]["type"] = "nonsense"
    out = await st.studio_validate(bad)
    assert out["ok"] is False and "manifest" not in out and out["errors"]


async def test_the_plan_card_names_the_added_actions(monkeypatch) -> None:
    shown: list[tuple] = []

    async def fake(title, detail, context, **kw):
        shown.append((title, detail, context))
        return True

    async def kept(manifest):
        shown.append(("kept", manifest))
        return True

    async def clear():
        return False

    monkeypatch.setattr(ask_tools, "request_confirmation", fake)
    monkeypatch.setattr(st, "_record_plan", kept)
    monkeypatch.setattr(st, "_must_ask", clear)
    out = await st.studio_plan(_app([_obj()]))
    assert out["decision"] == "approved" and out["ok"]
    context = shown[0][2]
    assert "Record actions added" in context and "visit" in context
    assert _uses(out["manifest"]) == _five("visit")
    # The record of the approval holds the completed manifest.
    assert _uses(shown[1][1]) == _five("visit")


async def test_a_plan_with_no_added_action_shows_no_such_line(monkeypatch) -> None:
    shown: list[str] = []

    async def fake(title, detail, context, **kw):
        shown.append(context)
        return False

    async def clear():
        return False

    monkeypatch.setattr(ask_tools, "request_confirmation", fake)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    monkeypatch.setattr(st, "_must_ask", clear)

    async def none(manifest):
        return True

    monkeypatch.setattr(st, "_withdraw_plan", none)
    full = _app([_obj()], [{"id": "find", "uses": "object:visit.list"}])
    await st.studio_plan(full)
    assert "Record actions added" not in shown[0]


def test_save_and_publish_do_not_call_the_check() -> None:
    """A save and a publish refuse nothing new: neither calls the helper."""
    import inspect

    for fn in (st.studio_save, st.studio_publish, st._save_draft, st._create):
        assert "with_record_actions" not in inspect.getsource(fn)


def test_the_pack_rules_say_what_the_tools_do() -> None:
    from acb_skills.tool_packs import _STUDIO_RULES

    assert "five record actions" in _STUDIO_RULES


async def test_a_manifest_that_would_pass_the_size_limit_gets_no_actions(monkeypatch) -> None:
    """A manifest that passed before is not refused for the added actions."""
    monkeypatch.setattr(st, "MAX_MANIFEST_CHARS", len(str(_app([_obj()]))) + 60)
    out = await st.studio_validate(_app([_obj()]))
    assert out["ok"], out
    assert out["manifest"]["actions"] == [] and "added_actions" not in out
    assert any("size limit" in line for line in out["diff"])
