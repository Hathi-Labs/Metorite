"""The interview and the plan card (STU-G2, ``ai_studio.md`` sections 6.2 and 14).

Hermetic: the catalog GET is mocked at ``httpx`` (the fixtures of
``test_studio_builder_tools.py``), and the card is a stub or the real
``request_confirmation`` in a run with no card channel. No model call.

The fences (R7):

* the interview holds at most five questions, one topic each, and never a
  topic that the brief states (``test_studio_interview_caps_at_five_questions``,
  ``test_studio_interview_refuses_repeat_topic``);
* the result of ``studio_interview`` is the exact input of the REAL
  ``ask_questions`` parser;
* an invalid manifest shows no card, and a stop writes nothing: any non-GET
  call fails, and the tool imports no db or store module;
* the plan card shows a count of parts and no price.

"Ask only what the brief does not say" is model judgement. It is advisory
(``ai_studio.md`` section 14, G2).

Mutation runs (recorded in the PR): raise the cap to 6; drop the repeated
topic check; make ``studio_plan`` POST; put a digit after "Credit" in
``COST_LINE``; drop ``studio_plan`` from the family; let an invalid manifest
show a card; remove ``read_only`` from ``studio_plan``.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
from pathlib import Path

import httpx
import pytest
from acb_skills import ask_tools
from acb_skills import skill_families as sf
from acb_skills import studio_tools as st

from tests.unit.test_studio_builder_tools import MEMBER, ROWS, _serve, _valid
from tests.unit.test_studio_builder_tools import catalog as catalog  # the fixture

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "packages/acb_skills/acb_skills/studio_tools.py"


def _q(topic: str, **kw) -> dict:
    return {"topic": topic, "question": f"A question about {topic}?", **kw}


def _five() -> list[dict]:
    return [_q(t) for t in st.INTERVIEW_TOPICS]


# -- 1. The interview ---------------------------------------------------------


async def test_a_valid_interview_gives_the_text_for_the_ask_tool() -> None:
    out = await st.studio_interview(_five())
    assert out["ok"], out
    body = json.loads(out["questions"])
    assert len(body["questions"]) == 5
    assert all("topic" not in q for q in body["questions"])
    assert body["questions"][0]["header"] == "Who uses it"  # a default header


async def test_studio_interview_caps_at_five_questions() -> None:
    """Mutation: raise ``MAX_INTERVIEW_QUESTIONS`` to 6 -> red."""
    six = [*_five(), _q("who")]
    out = await st.studio_interview(six)
    assert out["ok"] is False and "questions" not in out
    assert "at most 5 questions" in out["errors"][0]["message"]
    # Non-vacuity: five pass.
    assert (await st.studio_interview(_five()))["ok"]


async def test_studio_interview_refuses_repeat_topic() -> None:
    """Mutation: drop the repeated-topic check -> red."""
    out = await st.studio_interview([_q("who"), _q("records"), _q("who")])
    assert out["ok"] is False and "questions" not in out
    assert "repeats the topic who" in out["errors"][0]["message"]


async def test_studio_interview_refuses_unknown_topic_and_a_missing_one() -> None:
    for bad in ({"topic": "colour", "question": "Which colour?"}, {"question": "Why?"}):
        out = await st.studio_interview([bad])
        assert out["ok"] is False and "needs a topic" in out["errors"][0]["message"]


async def test_studio_interview_refuses_a_question_on_a_stated_topic() -> None:
    out = await st.studio_interview([_q("who"), _q("records")], stated=["who"])
    assert out["ok"] is False
    assert "states it already" in out["errors"][0]["message"]
    ok = await st.studio_interview([_q("records")], stated=["who"])
    assert ok["ok"], ok
    bad = await st.studio_interview([_q("records")], stated=["nonsense"])
    assert bad["ok"] is False


async def test_studio_interview_refuses_the_empty_and_the_malformed() -> None:
    for bad in ([], "not json", [3], [{"topic": "who"}],
                [_q("who", options=[{"description": "no label"}])],
                [_q("who", options=[{"label": str(i)} for i in range(7)])]):
        out = await st.studio_interview(bad)
        assert out["ok"] is False and "questions" not in out, bad


def _run_ask(text: str) -> dict:
    """Feed *text* to the REAL ``ask_questions`` and return the card it emits."""
    import orchestrator.executor as ex

    async def run() -> tuple[dict, str]:
        queue: asyncio.Queue = asyncio.Queue()
        token = ex._active_run_queue.set(queue)
        try:
            task = asyncio.create_task(ask_tools.ask_questions(text))
            event = await asyncio.wait_for(queue.get(), 5)
            ex._pending_user_input.pop(event["value"]["request_id"]).set_result({"answer": "ok"})
            return event, await task
        finally:
            ex._active_run_queue.reset(token)

    event, reply = asyncio.run(run())
    assert reply == "User response: ok"
    return event


async def test_the_interview_text_is_what_the_real_ask_tool_reads() -> None:
    """The output of ``studio_interview`` is the exact argument of ``ask_questions``."""
    qs = [_q("who", header="Who uses it?", options=[{"label": "Me", "recommended": True},
                                                    {"label": "My team", "description": "all"}]),
          _q("approver", required=False)]
    out = await st.studio_interview(qs)
    assert out["ok"], out
    assert isinstance(out["questions"], str)
    event = await asyncio.to_thread(_run_ask, out["questions"])
    cards = event["value"]["questions"]
    assert [c["question"] for c in cards] == [q["question"] for q in qs]
    assert cards[0]["header"] == "Who uses it?"
    assert [o["label"] for o in cards[0]["options"]] == ["Me", "My team"]
    assert cards[0]["options"][0]["recommended"] is True
    assert cards[1].get("required") is False


def test_the_interview_does_not_wrap_the_ask_tool() -> None:
    """The executor finds ``ask_questions`` by name, so no studio tool calls it."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "ask_questions" not in names


# -- 2. The plan card ---------------------------------------------------------


@pytest.fixture
def card(monkeypatch):
    """A stub for the card seam. ``answer`` sets what the member does."""
    calls: list[tuple] = []
    box = {"answer": True}

    async def fake(title, detail="", context="", non_interactive_default="deny",
                   close_on_steer=None, **kw):
        calls.append((title, detail, context, non_interactive_default, close_on_steer, kw))
        return box["answer"]

    monkeypatch.setattr(ask_tools, "request_confirmation", fake)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    return calls, box


async def _full_manifest() -> dict:
    m = await _valid()
    m["objects"] = [{"name": "orders", "fields": [{"name": "total", "type": "number"}]}]
    return m


async def test_a_valid_plan_shows_one_card_and_approve_goes_on(catalog, card) -> None:
    calls, _ = card
    out = await st.studio_plan(await _valid())
    assert out["ok"] is True and out["decision"] == "approved"
    assert len(calls) == 1
    title, detail, context, default, steer, _kw = calls[0]
    assert (title, detail, context) == (out["title"], out["detail"], out["context"])
    assert default == "deny" and steer is True
    for word in ("Objects", "Actions", "Views", "Workflows", "Skill", "Connections"):
        assert word in context, word


async def test_a_real_stop_in_a_run_with_a_card_channel_is_stopped(
        catalog, card, monkeypatch) -> None:
    """A denial with a live channel is a Stop. Mutation: map it to ``no_card`` -> red."""
    _calls, box = card
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    box["answer"] = False
    assert (await st.studio_plan(await _valid()))["decision"] == "stopped"
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: False)
    assert (await st.studio_plan(await _valid()))["decision"] == "no_card"


async def test_the_card_binds_the_runs_own_member(catalog, card) -> None:
    calls, _ = card
    await st.studio_plan(await _valid())
    assert calls[0][5].get("responder") == MEMBER


async def test_the_edit_steer_and_the_stop_decisions(catalog, card) -> None:
    calls, box = card
    box["answer"] = ask_tools.REVISED
    assert (await st.studio_plan(await _valid()))["decision"] == "revised"
    for stop in (False, None):
        box["answer"] = stop
        assert (await st.studio_plan(await _valid()))["decision"] == "stopped"
    assert len(calls) == 3


async def test_an_invalid_manifest_shows_no_card(catalog, card) -> None:
    """Mutation: let an invalid manifest reach the card -> red."""
    calls, _ = card
    bad = await _valid()
    bad["actions"] = [{"id": "go", "uses": "no.such.action"}]
    out = await st.studio_plan(bad)
    assert out["ok"] is False and out["errors"]
    assert not {"title", "detail", "context", "decision"} & set(out)
    assert calls == []
    assert (await st.studio_plan("{broken"))["ok"] is False
    assert calls == []


async def test_a_catalog_that_cannot_be_read_shows_no_card(monkeypatch, card) -> None:
    calls, _ = card
    _serve(monkeypatch, status=500)
    out = await st.studio_plan(await _valid())
    assert out["ok"] is False and calls == []


async def test_a_stop_writes_nothing_so_any_non_get_call_fails(monkeypatch, card) -> None:
    """Mutation: make ``studio_plan`` POST (or call ``studio_patch``) -> red."""
    _calls, box = card
    seen: list[str] = []

    class _GetOnly:
        def __init__(self, **_kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, headers=None):
            seen.append("GET")
            return httpx.Response(200, json={"actions": ROWS}, request=httpx.Request("GET", url))

        def __getattr__(self, name):  # post, put, patch, delete, request, send ...
            raise AssertionError(f"a {name} call from a read-only tool")

    monkeypatch.setattr(st, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(st.httpx, "AsyncClient", _GetOnly)
    patch_calls: list[int] = []

    async def no_patch(*a, **k):
        patch_calls.append(1)
        raise AssertionError("studio_plan called studio_patch")

    monkeypatch.setattr(st, "studio_patch", no_patch)
    box["answer"] = False
    out = await st.studio_plan(await _valid())
    assert out["decision"] == "stopped" and seen and set(seen) == {"GET"}
    assert patch_calls == []


def test_studio_plan_imports_no_db_or_store_module() -> None:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "studio_plan")
    mods = []
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
        elif isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
    assert mods == ["acb_skills"], mods
    banned = re.compile(r"(?i)\b(db|sqlalchemy|psycopg|asyncpg|redis|store|session)\b")
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            assert not banned.search(node.id), node.id
        if isinstance(node, ast.Attribute):
            assert not banned.search(node.attr), node.attr
    # The module itself imports no db or store module either.
    top = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    text = " ".join((getattr(n, "module", None) or "") + " ".join(a.name for a in n.names)
                    for n in top)
    assert not re.search(r"acb_common\.db|sqlalchemy|redis|studio_store", text)


def test_the_new_tools_are_read_only_annotated() -> None:
    """Mutation: remove ``read_only`` from ``studio_plan`` -> red."""
    from acb_skills.tool_annotations import get_annotations as annotations_of

    for fn in (st.studio_plan, st.studio_interview):
        ann = annotations_of(fn)
        assert ann.get("read_only") is True and ann.get("open_world") is False, fn.__name__


# -- 3. The cost --------------------------------------------------------------


def test_the_card_text_shows_parts_and_no_price() -> None:
    """Mutation: put a number after "credit" in the card -> red."""
    m = {"id": "orders", "name": "Orders", "objects": [{"name": "a", "fields": []}],
         "views": [{"id": "v"}], "workflows": [], "connections": ["slack"],
         "skill": {"actions": []}, "actions": []}
    text = st.plan_card_text(m)
    assert st.parts_count(m) == 4 and "Parts: 4" in text
    assert "Build cost: not priced yet. Credit limits come with a later release." in text
    assert not re.search(r"(?i)(credit|inr|rupee|price)\D{0,20}\d", text)
    assert not re.search(r"(?i)\d\D{0,20}(credit|inr|rupee)", text)
    assert "₹" not in text and "turn" not in text.lower()
    # The fence is not vacuous: it sees a number next to "credit".
    assert re.search(r"(?i)(credit)\D{0,20}\d", text + " Credit limit 5")


def test_the_card_text_is_deterministic_and_fits_the_card() -> None:
    wide = {"id": "x", "name": "N" * 500, "objects": [{"name": "o" * 500, "fields": []}] * 50,
            "views": [{"id": "v" * 500}] * 50, "workflows": [{"id": "w" * 500}] * 50,
            "actions": [{"id": "a" * 500}] * 50, "connections": ["c" * 500] * 50}
    text = st.plan_card_text(wide)
    assert text == st.plan_card_text(wide)
    assert len(text) <= 4000  # request_confirmation clips the context at 4000
    assert st.parts_count("nope") == 0


async def test_a_run_with_no_card_channel_still_returns_the_plan_text(catalog) -> None:
    """The real ``request_confirmation`` denies with no channel. The decision is
    ``no_card``, not ``stopped``, and the text stays.
    Mutation: map ``no_card`` back to ``stopped`` -> red."""
    with ask_tools.refuse_cards():
        out = await st.studio_plan(await _valid())
    assert out["ok"] is True and out["decision"] == "no_card"
    assert out["context"].startswith("App ") and "Parts: 0" in out["context"]


# -- 4. The registry ----------------------------------------------------------


def test_the_family_holds_both_new_tools_in_the_order_of_the_module() -> None:
    """Mutation: drop ``studio_plan`` from the family -> red."""
    tools = tuple(sf.SKILL_FAMILIES["studio"]["tools"])
    assert "studio_interview" in tools and "studio_plan" in tools
    assert tools == tuple(fn.__name__ for fn in st.STUDIO_TOOLS)
    assert all(inspect.iscoroutinefunction(fn) for fn in st.STUDIO_TOOLS)
