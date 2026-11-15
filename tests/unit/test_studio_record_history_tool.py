"""The chat twin of the Activity tab: the history mode of ``studio_record_list`` (STU-C4b, task 3086).

It is a mode (``history_of``), not a tool of its own: a Studio turn holds the cap of tools, so one more
tool in ``studio-more`` would drop the email pack in four cases of the golden matrix.

Hermetic. A fake gateway stands in for the package route and the action door. It checks each
path against the REAL router, and each history body against the REAL key set of the door
(``invoke.HISTORY_INPUT_KEYS``). So a tool that sends a key that the door refuses fails here.

The fences (R7):

* the mode only reads: it posts to the declared ``object:NAME.history`` action and to no other;
* the body holds ``id``, ``limit`` and, past the first page, ``after``, and no other key;
* the tool takes no org, member or action argument;
* the text of a change sits inside one frame, the keys are in the grammar, and a key keeps no
  ``old`` that the door did not send;
* the tool adds no key to a change: it reads no record, so a key that the door left out stays out;
* a page keeps ``next`` of the door, and a cut page gives the id of the last change that is shown.

Mutation runs (recorded in the PR):

1. Send the history to the action whose ``uses`` is ``object:NAME.list`` ->
   ``test_a_history_posts_to_the_declared_history_action_and_nothing_else`` is red.
2. Drop ``next`` from the result -> ``test_next_comes_back_so_the_model_can_page`` is red.
3. Fill ``old`` as None for a key with no ``old`` -> ``test_a_boundary_change_keeps_no_old`` is red.
"""
from __future__ import annotations

import inspect
import json
import uuid
from typing import Any

import httpx
import pytest
from acb_skills import studio_tools as st
from acb_skills.untrusted import MARKER_LINE_RE
from gateway.routes.studio import invoke

from tests.unit.test_studio_builder_tools import MEMBER
from tests.unit.test_studio_record_tools import ACTIONS, PID, Gateway, _Client

RID = str(uuid.uuid4())
HISTORY_ACTIONS = [*ACTIONS, {"id": "past", "uses": "object:visit.history"}]


class HistoryGateway(Gateway):
    def __init__(self) -> None:
        super().__init__()
        self.actions = list(HISTORY_ACTIONS)
        self.answer: dict[str, Any] = {"history": [
            {"id": 1, "op": "create", "actor": "ann@acme.test", "via_kind": None, "via_id": None,
             "changes": {"title": {"new": "Install the press"}}, "at": "2026-11-15T10:00:00+00:00"},
            {"id": 2, "op": "update", "actor": None, "via_kind": "workflow_run", "via_id": "w1",
             "changes": {"title": {"old": "Install the press", "new": "Install the press, line 2"}},
             "at": "2026-11-15T11:00:00+00:00"},
        ], "next": None}

    def request(self, method: str, url: str, headers: dict[str, str], body: Any) -> httpx.Response:
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        parts = path.strip("/").split("/")
        if method == "POST" and parts[:2] == ["studio", "apps"] and len(parts) == 5 and parts[4] == "past":
            self.calls.append((method, path, body, dict(headers)))
            self.route(method, path)
            assert set(body) <= invoke.HISTORY_INPUT_KEYS, body
            assert type(body["limit"]) is int and "id" in body
            self.last_action = parts[4]
            return self._r(200, self.answer)
        return super().request(method, url, headers, body)


@pytest.fixture
def gw(monkeypatch):
    gateway = HistoryGateway()
    monkeypatch.setattr(st, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(st.httpx, "AsyncClient", lambda **kw: _Client(gateway))
    return gateway


def _history(out: dict[str, Any]) -> list[dict[str, Any]]:
    text = out["history"]
    body = [ln for ln in text.splitlines() if not MARKER_LINE_RE.match(ln) and ln.strip().startswith("[")]
    assert body, text
    return json.loads(body[0])


def test_the_mode_is_in_the_read_tool_and_adds_no_tool() -> None:
    hints = st.studio_record_list.__tool_risk__
    assert hints["read_only"] is True and hints["open_world"] is False
    params = set(inspect.signature(st.studio_record_list).parameters)
    assert {"history_of", "next"} <= params
    assert not params & {"organization_id", "org_id", "member", "member_email", "email", "action",
                         "action_id", "actor", "visibility", "data", "request_id"}
    assert not hasattr(st, "studio_record_history")
    assert len(st.STUDIO_RECORD_TOOLS) == 6


def test_the_helper_names_the_door_route_and_no_write() -> None:
    src = inspect.getsource(st._record_history)
    assert "/studio/apps/{pid}/actions/" in src
    for verb in ("create", "update", "delete"):
        assert f".{verb}" not in src
    assert '"PATCH"' not in src and '"PUT"' not in src and '"DELETE"' not in src


def test_studio_describe_tells_a_builder_to_declare_history() -> None:
    assert "history" in st._OWN_DECLARED_OPS
    assert "history" in st._describe_object_actions()["declare_yourself"]


async def test_without_history_of_a_list_is_still_a_list(gw) -> None:
    out = await st.studio_record_list(PID, "visit")
    assert out["ok"] is True and "records" in out and "history" not in out
    assert gw.last_action == "find"


async def test_a_history_posts_to_the_declared_history_action_and_nothing_else(gw) -> None:
    out = await st.studio_record_list(PID, "visit", history_of=RID)
    assert out["ok"] is True and out["order"] == "oldest first"
    posts = gw.sent("POST")
    assert [c[1] for c in posts] == [f"/studio/apps/{PID}/actions/past"]
    assert posts[0][2] == {"id": RID, "limit": st.MAX_HISTORY_ROWS}
    assert posts[0][3]["X-User-Email"] == MEMBER


async def test_the_body_holds_after_only_past_the_first_page(gw) -> None:
    await st.studio_record_list(PID, "visit", history_of=RID, next=2, limit=5)
    assert gw.sent("POST")[0][2] == {"id": RID, "limit": 5, "after": 2}


async def test_a_package_with_no_history_action_gives_a_sentence_and_sends_no_post(gw) -> None:
    gw.actions = list(ACTIONS)
    out = await st.studio_record_list(PID, "visit", history_of=RID)
    assert out["ok"] is False and "object:visit.history" in out["errors"][0]["message"]
    assert not gw.sent("POST")


async def test_the_text_of_a_change_sits_inside_one_frame(gw) -> None:
    out = await st.studio_record_list(PID, "visit", history_of=RID)
    assert any(MARKER_LINE_RE.match(ln) for ln in out["history"].splitlines())
    assert "Install the press" in out["history"]
    assert "note" in out


async def test_a_boundary_change_keeps_no_old(gw) -> None:
    out = await st.studio_record_list(PID, "visit", history_of=RID)
    first, second = _history(out)
    assert first["changes"]["title"] == {"new": "Install the press"}
    assert "old" in second["changes"]["title"]
    assert first["actor"] == "ann@acme.test" and second["via"] == "workflow_run"


async def test_a_key_that_the_door_left_out_is_not_added(gw) -> None:
    gw.answer["history"][1]["changes"] = {}
    out = await st.studio_record_list(PID, "visit", history_of=RID)
    assert _history(out)[1]["changes"] == {}
    # No other call: the tool never reads the record to fill a key.
    assert all(c[0] != "GET" or c[1] == f"/studio/packages/{PID}" for c in gw.calls)


async def test_a_key_outside_the_grammar_is_dropped(gw) -> None:
    gw.answer["history"][0]["changes"] = {"Bad Key": {"new": "x"}, "title": {"new": "y"}}
    out = await st.studio_record_list(PID, "visit", history_of=RID)
    assert list(_history(out)[0]["changes"]) == ["title"]


async def test_next_comes_back_so_the_model_can_page(gw) -> None:
    gw.answer["next"] = 2
    out = await st.studio_record_list(PID, "visit", history_of=RID)
    assert out["next"] == 2 and out["truncated"] is False
    gw.answer["next"] = None
    assert (await st.studio_record_list(PID, "visit", history_of=RID))["next"] is None


async def test_a_page_over_the_cap_is_cut_and_next_names_the_last_shown_change(gw, monkeypatch) -> None:
    gw.answer["history"] = [
        {"id": i, "op": "update", "actor": "a@x.test", "via_kind": None, "via_id": None,
         "changes": {"title": {"old": "a" * 190, "new": "b" * 190}}, "at": "2026-11-15T10:00:00+00:00"}
        for i in range(1, 26)]
    out = await st.studio_record_list(PID, "visit", history_of=RID)
    assert out["truncated"] is True and out["count"] < 25
    assert out["next"] == out["count"]


async def test_a_long_value_is_cut(gw) -> None:
    gw.answer["history"][0]["changes"] = {"title": {"new": "x" * 1000}}
    shown = _history(await st.studio_record_list(PID, "visit", history_of=RID))[0]["changes"]["title"]["new"]
    assert len(shown) <= st.MAX_HISTORY_VALUE_CHARS


@pytest.mark.parametrize("bad", ["", "x", "../etc", "12"])
async def test_a_bad_id_sends_nothing(gw, bad) -> None:
    assert (await st.studio_record_list(bad, "visit", history_of=RID))["ok"] is False
    if bad:  # an empty history_of is the list mode
        assert (await st.studio_record_list(PID, "visit", history_of=bad))["ok"] is False
    assert not gw.calls


async def test_a_bad_object_name_or_cursor_sends_nothing(gw) -> None:
    assert (await st.studio_record_list(PID, "Not A Name", history_of=RID))["ok"] is False
    assert (await st.studio_record_list(PID, "visit", history_of=RID, next=-1))["ok"] is False
    assert not gw.calls


async def test_a_404_of_the_door_gives_a_sentence(gw) -> None:
    gw.answer = {}
    gw.status = None
    orig = gw.request

    def gone(method, url, headers, body):
        if method == "POST":
            return httpx.Response(404, json={"detail": {"code": "not_found"}}, request=httpx.Request("POST", "http://x"))
        return orig(method, url, headers, body)

    gw.request = gone  # type: ignore[method-assign]
    out = await st.studio_record_list(PID, "visit", history_of=RID)
    assert out["ok"] is False
