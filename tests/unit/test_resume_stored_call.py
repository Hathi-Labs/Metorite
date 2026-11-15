"""RESUME-1a: a card stores the call that it guards (task 3260).

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.4 item 1 and the
RESUME-1 row of §4.12.7. Modules: ``acb_skills.stored_call`` (the flag, the
shape and the call id), ``acb_skills.ask_tools`` (the bind at the card) and
``orchestrator.pending_ask`` (the map, the row and the one reader). Flag
``CHAT_RESUME_ORGS`` (default empty).

Hermetic: the rows live in the in-memory table of ``test_pending_ask_flow.py``.
The SQL and the row level security are ``test_resume_stored_call_r8.py``.

What this file holds (R7), each with the mutation that turns it red:

a. Each write of ``egress.HELD_CARD_WRITE_TOOLS``: a plain card inside
   ``card_call`` puts a stored call with each part into the insert. Drop the
   bind in ``request_confirmation`` -> red.
b. Each of these stores none, with its own mutation: a rows card, a reply
   form, a held send, an email tool, an MCP tool with a borrowed name, a cut
   card, a generative UI or a question card, arguments above 64 KB,
   arguments that JSON cannot hold, and the flag off.
c. The bind is there BEFORE the card goes out. On path A the drain writes
   the row in a context of its own and still finds the call. Path C too.
   Move the bind after the push -> red.
d. With ``CHAT_DURABLE_ASKS`` off, the map is empty after the card closes.
   Drop the unbind in ``_block_on`` -> red.
e. ``_COLUMNS`` leaves ``stored_call`` out. The four browser reads and the
   card event hold no stored call and no argument. Add the column -> red.
f. Each listed write awaits its card before any write, and the walk counts
   the calls of an attribute too. Move ``_patch`` above the card in
   ``update_deal_status`` -> red.
g. A stored call holds no org, member or thread key.
h. The call id: the middleware holds the MAF call id while the call runs,
   and the seam attaches it only with the flag on, before the progress notice.

Run::

    uv run pytest tests/unit/test_resume_stored_call.py -q
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import ast
import asyncio
import contextlib
import contextvars
import inspect
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

stream_relay = pytest.importorskip("orchestrator.stream_relay", reason="orchestrator not installed")
pending_ask = pytest.importorskip("orchestrator.pending_ask")
executor = pytest.importorskip("orchestrator.executor")

from acb_skills import ask_tools, late_approve, stored_call  # noqa: E402
from acb_skills import egress as eg  # noqa: E402
from acb_skills.ask_tools import act_keys, card_call, request_confirmation  # noqa: E402

# Fixtures, resolved by name. The imports are load-bearing.
from tests.unit.test_chat_deploy_recovery import (  # noqa: E402, F401
    fake_redis,
    liveness,
    no_persist,
)
from tests.unit.test_pending_ask_flow import (  # noqa: E402, F401
    _ORG_A,
    _ORG_B,
    _Chats,
    _events,
    ask_db,
    flag_on,
)

_ROOT = Path(__file__).resolve().parents[2]
_ALICE = "alice@ask-a.test"
_AGENT = "orchestrator"
_MODULE = "skill_projects.writes"
#: A value that only the arguments of a call hold. No card shows it.
_SENTINEL = "zq-sentinel-7f3a"
_TITLE = "Run this change?"
_DETAIL = "The change of this call."
_PARTS = {"v", "kind", "tool", "module", "agent", "arguments", "call_id",
          "card_text", "act_keys", "digest"}

#: The annotation that a first-party write carries (``tool_annotations``).
_CARD_WRITE = {"read_only": False, "destructive": True, "idempotent": False,
               "open_world": True, "card_write": True}

#: Each write that a stand-in tool made, in order.
WRITES: list[tuple[str, dict[str, Any]]] = []


def _stand_in(name: str, params: Any, *, module: str = _MODULE, card: Any = None,
              risk: dict[str, Any] | None = None) -> Any:
    """A stand-in of the listed write *name*: its pinned parameters and its
    annotation, one card, then one write on approve. *card* gives the keyword
    arguments of ``request_confirmation``."""
    card_kwargs = dict(card or {"title": _TITLE, "detail": _DETAIL})

    async def tool(**kwargs: Any) -> str:
        ok = await request_confirmation(**card_kwargs)
        if ok:
            WRITES.append((name, dict(kwargs)))
        return "done" if ok else "not done"

    tool.__name__ = tool.__qualname__ = name
    tool.__module__ = module
    tool.__signature__ = inspect.Signature([  # type: ignore[attr-defined]
        inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY, default=None)
        for p in sorted(params)])
    tool.__tool_risk__ = dict(_CARD_WRITE if risk is None else risk)  # type: ignore[attr-defined]
    return tool


def _args(params: Any) -> dict[str, Any]:
    """Arguments for each pinned parameter. Each holds the sentinel. A batch
    gives a JSON list, so its act keys read."""
    out: dict[str, Any] = {}
    for p in sorted(params):
        if p == "tasks":
            out[p] = json.dumps([{"title": f"Task {_SENTINEL}"}])
        elif p == "changes":
            out[p] = json.dumps([{"task_id": f"t-{_SENTINEL}", "status": "done"}])
        else:
            out[p] = f"{p}-{_SENTINEL}"
    return out


@pytest.fixture
def durable_on(monkeypatch, ask_db):
    """``CHAT_DURABLE_ASKS`` on, with the park window of production, so a card
    that the member answers at once never parks."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_durable_asks", True)
    WRITES.clear()
    return ask_db


@pytest.fixture
def resume_on(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_resume_orgs", _ORG_A)


def _cards(r, tid: str) -> list[dict[str, Any]]:
    return [e["value"] for e in _events(r, tid) if e.get("name") == "confirmation_requested"]


def _bind_run(tid: str, org: str, agent: str = _AGENT) -> None:
    from acb_common import bind_run_context
    from acb_common.db import bind_tenant

    executor._stream_relay_thread_id.set(tid)
    bind_tenant(org)
    bind_run_context(user=_ALICE, member_verified=True, agent=agent, thread_id=tid)


def _run(r, tid: str, fn: Any, kwargs: dict[str, Any], *, org: str = _ORG_A,
         agent: str = _AGENT, answer: str = "APPROVE", scope: Any = None,
         call: Any = None) -> dict[str, Any]:
    """One run on *tid* (path C, the relay) that calls *fn* once inside
    ``card_call``. The member answers the card at once. ``bound`` says whether
    the map still held a stored call for the card when the member answered.
    With ``CHAT_DURABLE_ASKS`` on, the row took it before that."""
    r.store[f"cc:runactor:{tid}"] = _ALICE
    out: dict[str, Any] = {}
    seen = len(_cards(r, tid))

    async def _the_run() -> None:
        _bind_run(tid, org, agent)
        with scope if scope is not None else card_call(fn, (), kwargs):
            out["result"] = await (call or fn)(**kwargs)

    async def _answer() -> None:
        while len(_cards(r, tid)) == seen:
            await asyncio.sleep(0.002)
        rid = _cards(r, tid)[-1]["request_id"]
        out["rid"] = rid
        out["bound"] = rid in pending_ask._STORED
        executor._pending_user_input[rid].set_result({"answer": answer})

    async def go() -> None:
        run = asyncio.create_task(_the_run())
        stream_relay._DETACHED_TASKS[tid] = run
        helper = asyncio.create_task(_answer())
        try:
            await asyncio.wait_for(run, timeout=60)
        finally:
            helper.cancel()

    asyncio.run(go())
    return out


def _row(db, rid: str, org: str = _ORG_A) -> dict[str, Any]:
    return db.rows[(org, rid)]


def _expected(name: str, kwargs: dict[str, Any], *, module: str = _MODULE,
              agent: str = _AGENT, title: str = _TITLE, detail: str = _DETAIL,
              call_id: str | None = None) -> dict[str, Any]:
    keys = late_approve.keys_wire(act_keys(module, name, kwargs), agent)
    assert keys, "non-vacuity: the act keys of the call read"
    text = late_approve.card_text_of(title, detail, "", None)
    return {
        "v": 1, "kind": "confirmation", "tool": name,
        "module": module if not late_approve._PER_RUN_MODULE.match(module) else f"agent:{agent}",
        "agent": agent, "arguments": kwargs, "call_id": call_id,
        "card_text": text, "act_keys": keys, "digest": late_approve.digest_of(text, keys),
    }


# ---------------------------------------------------------------------------
# a. Each listed write stores its call at the card
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(eg.HELD_CARD_WRITE_TOOLS))
def test_a_plain_card_of_each_listed_write_stores_its_call(
    name, durable_on, resume_on, liveness, no_persist,
):
    """Mutation: drop the bind in ``request_confirmation`` -> red."""
    params = eg.HELD_CARD_WRITE_TOOLS[name]
    fn = _stand_in(name, params)
    assert eg.is_card_write_tool(fn), "non-vacuity: the stand-in passes the three tests"
    kwargs = _args(params)
    tid = f"t-res-a-{name}"
    out = _run(liveness, tid, fn, kwargs)
    assert out["result"] == "done"
    row = _row(durable_on, out["rid"])
    assert row["kind"] == "confirmation"
    assert row["stored_call"] == _expected(name, kwargs)
    assert set(row["stored_call"]) == _PARTS
    assert pending_ask._STORED == {}, "the map keeps nothing after the card"


def test_a_per_run_module_reads_as_the_agent(durable_on, resume_on, liveness, no_persist):
    """C7: a module that the loader made for one run reads as ``agent:<name>``,
    in ``module`` and in the act keys. Mutation: store the raw module -> red."""
    module = "_agent_0f3a9c_agents"
    params = eg.HELD_CARD_WRITE_TOOLS["update_deal_status"]
    fn = _stand_in("update_deal_status", params, module=module)
    kwargs = {"deal_id": f"d-{_SENTINEL}", "stage": "Won"}
    out = _run(liveness, "t-res-per-run", fn, kwargs, agent="crm-assistant")
    stored = _row(durable_on, out["rid"])["stored_call"]
    assert stored["module"] == "agent:crm-assistant"
    assert stored["agent"] == "crm-assistant"
    assert all('"agent:crm-assistant"' in k for k in stored["act_keys"])
    assert stored == _expected("update_deal_status", kwargs, module=module,
                               agent="crm-assistant")


def test_the_real_insert_names_the_column_only_with_a_stored_call(monkeypatch):
    """The SQL parameters of ``insert_ask``. With no stored call the statement
    and the parameters are those of before, byte for byte. Mutation: always
    name the column -> red."""
    import acb_graph

    seen: list[tuple[str, dict[str, Any]]] = []

    class _S:
        def execute(self, stmt, params):
            seen.append((str(stmt), dict(params)))

            class _R:
                rowcount = 1
            return _R()

    @contextlib.contextmanager
    def _session(_org):
        yield _S()

    monkeypatch.setattr(acb_graph, "tenant_session", _session)
    rid = uuid.uuid4().hex
    base = {"request_id": rid, "thread_id": "t", "kind": "confirmation",
            "payload": {"request_id": rid}}
    stored = _expected("set_reminder", {"title": f"x-{_SENTINEL}"})
    assert pending_ask.insert_ask(_ORG_A, base)
    assert pending_ask.insert_ask(_ORG_A, {**base, "stored_call": stored})
    (old_sql, old_params), (new_sql, new_params) = seen
    assert old_sql == pending_ask._INSERT_SQL and "stored_call" not in old_sql
    assert "stored" not in old_params
    assert new_sql == pending_ask._INSERT_STORED_SQL
    assert json.loads(new_params["stored"]) == stored
    assert {k: v for k, v in new_params.items() if k != "stored"} == old_params


def test_the_move_sql_clears_the_call_on_an_answer_and_a_close_only(monkeypatch):
    """C9: an answered or closed row keeps no stored call. A park keeps it.
    The R8 file runs the same SQL on Postgres. Mutation: clear it on a park,
    or keep it on a close -> red."""
    import acb_graph

    seen: list[str] = []

    class _S:
        def execute(self, stmt, params):
            seen.append(str(stmt))

            class _R:
                rowcount = 0

                def first(self):
                    return None
            return _R()

    @contextlib.contextmanager
    def _session(_org):
        yield _S()

    monkeypatch.setattr(acb_graph, "tenant_session", _session)
    rid = uuid.uuid4().hex
    for to in ("parked", "answered", "closed"):
        pending_ask.move_ask(_ORG_A, rid, to=to, from_states=pending_ask.WAITING, answer="x")
    pending_ask.close_thread_asks(_S(), "t")
    parked, answered, closed, thread = seen
    assert "stored_call" not in parked
    assert "stored_call = NULL" in answered and "stored_call = NULL" in closed
    assert "stored_call = NULL" in thread


# ---------------------------------------------------------------------------
# b. The cards that store none
# ---------------------------------------------------------------------------

_SET_REMINDER = eg.HELD_CARD_WRITE_TOOLS["set_reminder"]
_REPLY = {"recipients": {"reply": {"to": ["a@b.test"]}, "reply_all": {"to": ["a@b.test"]}},
          "message_id": "m-1", "body": "Hello."}


@pytest.mark.parametrize("card", [
    # Mutation: build a PlainCard for a card with rows -> red.
    {"title": _TITLE, "rows": [{"id": "r1", "label": "One", "checked": True}]},
    # Mutation: build a PlainCard for a reply form -> red.
    {"title": _TITLE, "reply": _REPLY, "close_on_steer": False},
    # Mutation: drop `request_id is None` from the plain test -> red.
    {"title": _TITLE, "request_id": "ab" * 16},
    # A held send needs rows. Mutation: drop `_rows is None` and
    # `not _with_answer` from the plain test -> red.
    {"title": _TITLE, "rows": [{"id": "r1", "label": "One", "checked": True}],
     "with_answer": True},
    # C2. Mutation: drop the cut test of `late_approve.act_of` -> red.
    {"title": _TITLE, "context": "x" * 4500},
], ids=["rows", "reply-form", "held-send-id", "held-send-answer", "cut"])
def test_a_card_that_is_not_plain_or_is_cut_stores_none(
    card, durable_on, resume_on, liveness, no_persist,
):
    fn = _stand_in("set_reminder", _SET_REMINDER, card=card)
    out = _run(liveness, f"t-res-none-{uuid.uuid4().hex[:6]}", fn,
               {"title": f"x-{_SENTINEL}"}, answer="REJECT")
    assert out["bound"] is False
    assert "stored_call" not in _row(durable_on, out["rid"])


def test_the_same_tool_with_a_plain_card_stores_one(durable_on, resume_on, liveness, no_persist):
    """Non-vacuity of the cases above: only the card differs."""
    fn = _stand_in("set_reminder", _SET_REMINDER)
    out = _run(liveness, "t-res-plain", fn, {"title": f"x-{_SENTINEL}"})
    assert _row(durable_on, out["rid"])["stored_call"]["tool"] == "set_reminder"


def test_an_email_tool_stores_none(durable_on, resume_on, liveness, no_persist):
    """A send is no listed write, even with the mark of one. Two tests refuse
    it. Mutation: drop the tool test of ``late_approve.act_of`` and the name
    test of ``late_approve.keys_wire`` -> red."""
    fn = _stand_in("send_email", {"to", "subject"})
    out = _run(liveness, "t-res-mail", fn, {"to": f"x-{_SENTINEL}@a.test", "subject": "Hi"})
    assert out["bound"] is False
    assert "stored_call" not in _row(durable_on, out["rid"])


def test_an_mcp_tool_with_a_borrowed_name_stores_none(durable_on, resume_on, liveness, no_persist):
    """The name, the mark, the parameters and the act keys match, and the tool
    of the card came from an MCP server. Mutation: drop the tool test of
    ``late_approve.act_of`` -> red."""
    from agent_framework import FunctionTool

    fn = _stand_in("set_reminder", _SET_REMINDER)
    tool = FunctionTool(name="set_reminder", description="x", func=fn,
                        additional_properties={"_mcp_is_tool": True},
                        approval_mode="never_require")
    assert eg._from_mcp(tool) and not eg.is_card_write_tool(tool)
    kwargs = {"title": f"x-{_SENTINEL}"}

    @contextlib.contextmanager
    def _mcp_call() -> Any:
        # The act keys of the listed write, and the MCP tool as the tool of
        # the card: only the tool test of `act_of` refuses it.
        with card_call(fn, (), kwargs):
            token = ask_tools._CARD_TOOL.set(tool)
            try:
                yield
            finally:
                ask_tools._CARD_TOOL.reset(token)

    out = _run(liveness, "t-res-mcp", fn, kwargs, scope=_mcp_call())
    assert out["bound"] is False
    assert "stored_call" not in _row(durable_on, out["rid"])


def test_a_delegation_scope_and_a_card_with_no_call_store_none(
    durable_on, resume_on, liveness, no_persist,
):
    """A card of a child run never takes the call that started the child, and
    a card outside a gated call has no tool. Mutation: give a keyless scope
    the tool, the keys and the arguments -> red."""
    fn = _stand_in("set_reminder", _SET_REMINDER)
    kwargs = {"title": f"x-{_SENTINEL}"}
    keyless = _run(liveness, "t-res-keyless", fn, kwargs,
                   scope=card_call(fn, (), kwargs, keyless=True))
    bare = _run(liveness, "t-res-bare", fn, kwargs, scope=contextlib.nullcontext())
    for out in (keyless, bare):
        assert out["bound"] is False
        assert "stored_call" not in _row(durable_on, out["rid"])


@pytest.mark.parametrize("kind_event", sorted(
    e for e, k in pending_ask.KIND_BY_EVENT.items() if k != "confirmation"))
def test_a_card_of_another_kind_takes_no_stored_call(kind_event, durable_on, fake_redis):
    """A generative UI and a question card (RESUME-1b) take no stored call,
    even when the map holds one for the request id. Mutation: drop the kind
    test in ``note_event`` -> red."""
    from acb_common.db import bind_tenant

    tid, rid = f"t-res-kind-{kind_event}", uuid.uuid4().hex
    fake_redis.store[f"cc:runactor:{tid}"] = _ALICE

    async def go() -> None:
        bind_tenant(_ORG_A)
        fut = asyncio.get_running_loop().create_future()
        executor._pending_user_input.park(rid, fut, tid)
        assert pending_ask.bind_call(rid, {"v": 1, "tool": "set_reminder"})
        event = {"type": "CUSTOM", "name": kind_event,
                 "value": {"request_id": rid, "question": "Pick one?"}}
        waiter = asyncio.create_task(executor.wait_user_future(fut, 30, thread_id=tid))
        await executor._push_sse_to_stream(tid, f"data: {json.dumps(event)}\n\n")
        for _ in range(20):
            await asyncio.sleep(0)
        fut.set_result({"answer": "x"})
        await waiter

    asyncio.run(go())
    row = _row(durable_on, rid)
    assert row["kind"] == pending_ask.KIND_BY_EVENT[kind_event]
    assert "stored_call" not in row


def test_arguments_above_64_kb_store_none(durable_on, resume_on, liveness, no_persist):
    """Mutation: drop the size cap of ``pending_ask.bind_call`` -> red."""
    fn = _stand_in("set_reminder", _SET_REMINDER)
    big = {"title": "x" * (pending_ask._PAYLOAD_BYTES + 1)}
    out = _run(liveness, "t-res-big", fn, big)
    assert out["bound"] is False
    assert "stored_call" not in _row(durable_on, out["rid"])
    # The cap counts bytes of UTF-8, not characters.
    wide = {"v": 1, "t": "अ" * (pending_ask._PAYLOAD_BYTES // 3 + 10)}
    assert len(json.dumps(wide, ensure_ascii=False)) < pending_ask._PAYLOAD_BYTES
    assert pending_ask.bind_call(uuid.uuid4().hex, wide) is False


@pytest.mark.parametrize("value", [{"a", "b"}, float("nan"), object()],
                         ids=["set", "nan", "object"])
def test_arguments_that_json_cannot_hold_store_none(
    value, durable_on, resume_on, liveness, no_persist,
):
    """Mutation: store the arguments with ``default=str`` -> red."""
    fn = _stand_in("set_reminder", _SET_REMINDER)
    out = _run(liveness, f"t-res-json-{uuid.uuid4().hex[:6]}", fn, {"title": value})
    assert out["bound"] is False
    assert "stored_call" not in _row(durable_on, out["rid"])


@pytest.mark.parametrize("orgs", ["", _ORG_B, " , "], ids=["empty", "another-org", "blank"])
def test_with_the_flag_off_for_the_org_nothing_is_stored(
    orgs, durable_on, liveness, no_persist, monkeypatch,
):
    """C5. Off, the row is the row of before, byte for byte: it has no
    ``stored_call`` key. Mutation: drop the flag test -> red."""
    from acb_common import get_settings
    from acb_common.settings import Settings

    assert Settings.model_fields["chat_resume_orgs"].default == ""
    monkeypatch.setattr(get_settings(), "chat_resume_orgs", orgs)
    fn = _stand_in("set_reminder", _SET_REMINDER)
    out = _run(liveness, f"t-res-off-{uuid.uuid4().hex[:6]}", fn, {"title": f"x-{_SENTINEL}"})
    assert out["bound"] is False
    row = _row(durable_on, out["rid"])
    assert set(row) == {"request_id", "thread_id", "run_id", "actor_email", "agent_name",
                        "kind", "question", "payload", "state", "answer", "asked_at"}


def test_the_flag_reader(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_resume_orgs", "")
    assert not stored_call.chat_resume_on(_ORG_A)
    monkeypatch.setattr(get_settings(), "chat_resume_orgs", f"x, {_ORG_A.upper()}")
    assert stored_call.chat_resume_on(_ORG_A)
    assert not stored_call.chat_resume_on(_ORG_B)
    assert not stored_call.chat_resume_on(None)
    monkeypatch.setattr(get_settings(), "chat_resume_orgs", "*")
    assert stored_call.chat_resume_on(_ORG_B)


def test_the_flag_is_named_in_the_three_places_of_a_flag():
    from acb_common import env_guard
    from gateway import flag_reload

    env = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nCHAT_RESUME_ORGS=\n" in env
    assert env_guard.is_platform_env("CHAT_RESUME_ORGS")
    assert flag_reload.reloadable("CHAT_RESUME_ORGS")
    doc = (_ROOT / "docs" / "flag_reload.md").read_text(encoding="utf-8")
    assert "`CHAT_RESUME_ORGS`" in doc


# ---------------------------------------------------------------------------
# c. The bind is there before the card goes out, on both paths
# ---------------------------------------------------------------------------

def test_path_a_binds_before_the_queue_and_the_drain_finds_it(
    durable_on, resume_on, liveness, no_persist,
):
    """C3. On path A the executor's drain writes the row, in a context that
    holds none of the tool's ContextVars. Mutation: move the bind after
    ``queue.put`` -> red."""
    from acb_common import bind_run_context
    from acb_common.db import bind_tenant

    tid = "t-res-path-a"
    liveness.store[f"cc:runactor:{tid}"] = _ALICE
    params = eg.HELD_CARD_WRITE_TOOLS["update_deal_status"]
    fn = _stand_in("update_deal_status", params, module="agent_crm.agents")
    kwargs = {"deal_id": f"d-{_SENTINEL}", "stage": "Won"}
    at_put: list[bool] = []
    in_drain: list[Any] = []
    rids: list[str] = []

    class _Queue(asyncio.Queue):
        async def put(self, item: Any) -> None:
            if item.get("name") == "confirmation_requested":
                at_put.append(item["value"]["request_id"] in pending_ask._STORED)
            await super().put(item)

    async def go() -> str:
        queue = _Queue()

        async def _drain() -> None:
            bind_tenant(_ORG_A)
            in_drain.append((ask_tools._CARD_ARGS.get(), ask_tools._CARD_TOOL.get()))
            while True:
                item = await queue.get()
                await executor._push_sse_to_stream(tid, f"data: {json.dumps(item)}\n\n")
                if item.get("name") == "confirmation_requested":
                    rid = item["value"]["request_id"]
                    rids.append(rid)
                    executor._pending_user_input[rid].set_result({"answer": "APPROVE"})

        drainer = asyncio.get_running_loop().create_task(
            _drain(), context=contextvars.Context())

        async def _the_run() -> str:
            executor._active_run_queue.set(queue)
            bind_tenant(_ORG_A)
            bind_run_context(user=_ALICE, member_verified=True, agent=_AGENT, thread_id=tid)
            with card_call(fn, (), kwargs):
                return await fn(**kwargs)

        try:
            return await asyncio.wait_for(_the_run(), 60)
        finally:
            drainer.cancel()

    assert asyncio.run(go()) == "done"
    assert at_put == [True], "the call was bound before the card went on the queue"
    assert in_drain == [(None, None)], "the drain reads none of the tool's context"
    stored = _row(durable_on, rids[0])["stored_call"]
    assert stored == _expected("update_deal_status", kwargs, module="agent_crm.agents")
    assert pending_ask._STORED == {}


def test_path_c_binds_before_the_push(durable_on, resume_on, liveness, no_persist, monkeypatch):
    """Path C pushes the card to the relay, and the push writes the row.
    Mutation: move the bind after ``_push_sse_to_stream`` -> red."""
    real = executor._push_sse_to_stream
    at_push: list[bool] = []

    async def _spy(tid: str, line: str) -> Any:
        if line.startswith("data: "):
            event = json.loads(line[len("data: "):])
            if event.get("name") == "confirmation_requested":
                at_push.append(event["value"]["request_id"] in pending_ask._STORED)
        return await real(tid, line)

    monkeypatch.setattr(executor, "_push_sse_to_stream", _spy)
    fn = _stand_in("set_reminder", _SET_REMINDER)
    kwargs = {"title": f"x-{_SENTINEL}"}
    out = _run(liveness, "t-res-path-c", fn, kwargs)
    assert at_push == [True]
    assert _row(durable_on, out["rid"])["stored_call"] == _expected("set_reminder", kwargs)


def test_a_failed_push_leaves_nothing_in_the_map(durable_on, resume_on, liveness, monkeypatch):
    """A card that never went out keeps no stored call in the process."""
    async def _down(_tid: str, _line: str) -> None:
        raise RuntimeError("the relay is down")

    monkeypatch.setattr(executor, "_push_sse_to_stream", _down)
    fn = _stand_in("set_reminder", _SET_REMINDER)
    kwargs = {"title": f"x-{_SENTINEL}"}

    async def go() -> str:
        _bind_run("t-res-down", _ORG_A)
        with card_call(fn, (), kwargs):
            return await fn(**kwargs)

    assert asyncio.run(go()) == "not done", "no channel denies"
    assert pending_ask._STORED == {}


# ---------------------------------------------------------------------------
# d. With CHAT_DURABLE_ASKS off, nothing stays
# ---------------------------------------------------------------------------

def test_with_durable_asks_off_the_map_is_empty_after_the_card(
    ask_db, resume_on, liveness, no_persist, monkeypatch,
):
    """Mutation: drop ``_unbind_stored_call`` from the wait of ``_block_on`` -> red."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_durable_asks", False)
    fn = _stand_in("set_reminder", _SET_REMINDER)
    out = _run(liveness, "t-res-durable-off", fn, {"title": f"x-{_SENTINEL}"})
    assert out["bound"] is True, "non-vacuity: the card bound a call while it waited"
    assert pending_ask._STORED == {}
    assert ask_db.rows == {}, "no row with the flag off"


def test_the_map_is_bounded_and_refuses_a_bad_id():
    pending_ask._reset_for_tests()
    try:
        assert pending_ask.bind_call("not-a-request-id", {"v": 1}) is False
        assert pending_ask.bind_call(uuid.uuid4().hex, "not a dict") is False
        assert pending_ask.bind_call(uuid.uuid4().hex, {}) is False
        first = uuid.uuid4().hex
        assert pending_ask.bind_call(first, {"v": 1})
        for _ in range(pending_ask._STORED_KEEP):
            pending_ask.bind_call(uuid.uuid4().hex, {"v": 1})
        assert len(pending_ask._STORED) == pending_ask._STORED_KEEP
        assert first not in pending_ask._STORED, "the oldest goes first"
    finally:
        pending_ask._reset_for_tests()


# ---------------------------------------------------------------------------
# e. No browser read returns the stored call
# ---------------------------------------------------------------------------

def test_the_columns_of_a_browser_read_leave_the_stored_call_out():
    """Mutation: add ``stored_call`` to ``_COLUMNS`` -> red."""
    columns = [c.strip() for c in pending_ask._COLUMNS.split(",")]
    assert "stored_call" not in columns
    assert "stored_call" not in pending_ask._COLUMNS
    src = inspect.getsource(pending_ask)
    reads = [line for line in src.splitlines() if "SELECT" in line and "chat_pending_ask" in line]
    assert reads and all("_COLUMNS" in line or "stored_call" in line for line in reads), reads
    one = inspect.getsource(pending_ask.read_stored_call)
    assert "SELECT stored_call FROM chat_pending_ask" in one


def _project(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """A row as the SQL of ``_COLUMNS`` gives it back."""
    if row is None:
        return None
    return {c.strip(): row.get(c.strip()) for c in pending_ask._COLUMNS.split(",")}


def _park_one(r, tid: str) -> str:
    """A run on *tid* draws a plain card of ``set_reminder`` that parks."""
    fn = _stand_in("set_reminder", _SET_REMINDER)
    kwargs = {"title": f"x-{_SENTINEL}"}
    r.store[f"cc:runactor:{tid}"] = _ALICE

    async def _the_run() -> None:
        _bind_run(tid, _ORG_A)
        with card_call(fn, (), kwargs):
            await fn(**kwargs)

    async def go() -> None:
        run = asyncio.create_task(_the_run())
        stream_relay._DETACHED_TASKS[tid] = run
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, timeout=60)

    asyncio.run(go())
    return _cards(r, tid)[-1]["request_id"]


def test_the_four_browser_reads_and_the_card_hold_no_stored_call(
    flag_on, ask_db, resume_on, liveness, no_persist, monkeypatch,
):
    """``GET /chat/pending-asks``, ``GET /chat/active-sessions``, the My Day
    chat provider and the waiting list of the shell, after a parked card that
    stored a call. Each reads its rows through ``waiting_asks``, which gives
    ``_COLUMNS`` only, so none holds ``stored_call`` or an argument."""
    import acb_graph
    from acb_auth import UserContext, UserRole, get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import agent as agent_routes
    from gateway.routes import chat as chat_routes
    from gateway.routes.shell import feed, waiting

    tid = "t-res-browser"
    rid = _park_one(liveness, tid)
    row = ask_db.rows[(_ORG_A, rid)]
    assert row["state"] == "parked" and row["stored_call"]["tool"] == "set_reminder", \
        "non-vacuity: the row holds a stored call with the sentinel"
    assert _SENTINEL in json.dumps(row["stored_call"])

    real_waiting, real_read = ask_db.waiting_asks, ask_db.read_ask
    monkeypatch.setattr(pending_ask, "waiting_asks",
                        lambda *a, **k: [_project(r) for r in real_waiting(*a, **k)])
    monkeypatch.setattr(pending_ask, "read_ask", lambda *a, **k: _project(real_read(*a, **k)))
    monkeypatch.setattr(acb_graph, "tenant_session", _Chats({_ORG_A: [tid]}).session)

    class _Room:
        can_send = True

    monkeypatch.setattr(agent_routes, "_resolve_room", lambda *_a, **_k: _Room())
    user = UserContext(email=_ALICE, role=UserRole.EMPLOYEE, organization_id=_ORG_A)
    app = FastAPI()
    app.get("/chat/pending-asks")(chat_routes.list_pending_asks)
    app.dependency_overrides[get_current_user] = lambda: user
    pending = TestClient(app).get("/chat/pending-asks", params={"thread_id": tid})
    assert pending.status_code == 200 and [a["requestId"] for a in pending.json()] == [rid]

    active = asyncio.run(chat_routes.list_active_sessions(user=user))
    assert tid in [a["threadId"] for a in active]
    found_feed = asyncio.run(feed._chat(user))
    signals = list(found_feed.signals())
    assert len(signals) == 1
    found_waiting = asyncio.run(waiting._chat_asks(user))
    bodies = {
        "pending-asks": pending.text,
        "active-sessions": json.dumps(active, default=str),
        "my-day": repr(signals) + repr(found_feed),
        "waiting": repr(found_waiting),
        "card": json.dumps(_events(liveness, tid), default=str),
    }
    for where, body in bodies.items():
        assert "stored_call" not in body, where
        assert _SENTINEL not in body, where


# ---------------------------------------------------------------------------
# f. Each listed write awaits its card before any write
# ---------------------------------------------------------------------------

def _all_functions() -> dict[str, ast.AsyncFunctionDef]:
    from tests.unit.test_held_card_writes import CRM_AGENT, _functions, _skill_functions

    return {**_skill_functions(), **_functions(CRM_AGENT)}


def _calls(fn: ast.AsyncFunctionDef, names: set[str]) -> list[int]:
    """The lines of each call of *names*, a bare name or an attribute."""
    return sorted(
        node.lineno for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", "") in names or getattr(node.func, "attr", "") in names))


@pytest.mark.parametrize("name", sorted(eg.HELD_CARD_WRITE_TOOLS))
def test_each_listed_write_awaits_its_card_before_any_write(name):
    """The §5 proof of ``test_held_card_writes.py``, for each name of the list,
    with the calls of an attribute (``client.post``) too. A stored call runs
    the tool again, so a write before the card would write twice. Mutation:
    move ``_patch`` above ``request_confirmation`` in ``update_deal_status``
    -> red."""
    from tests.unit.test_held_card_writes import _DOORS, _WRITES

    fn = _all_functions()[name]
    doors = _calls(fn, _DOORS)
    assert doors, f"{name} shows no card"
    writes = _calls(fn, _WRITES)
    assert not writes or doors[0] < writes[0], f"{name} writes before its card"


def test_the_walk_counts_an_attribute_call():
    """Non-vacuity of the walk: a write through an attribute counts."""
    tree = ast.parse(
        "async def f():\n"
        "    await client.patch('/x', {})\n"
        "    await ask_tools.request_confirmation(title='t')\n")
    fn = tree.body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    doors = _calls(fn, {"request_confirmation"})
    writes = _calls(fn, {"_patch", "patch"})
    assert doors == [3] and writes == [2]


# ---------------------------------------------------------------------------
# g. No org, member or thread in the stored call
# ---------------------------------------------------------------------------

def test_a_stored_call_holds_no_org_member_or_thread(durable_on, resume_on, liveness, no_persist):
    """C6: the columns of the row hold them. Mutation: add the org, the member
    or the thread to the shape -> red."""
    fn = _stand_in("set_reminder", _SET_REMINDER)
    tid = "t-res-no-keys"
    out = _run(liveness, tid, fn, {"title": f"x-{_SENTINEL}"})
    row = _row(durable_on, out["rid"])
    stored = row["stored_call"]
    assert set(stored) == _PARTS
    text = json.dumps(stored)
    for value in (_ORG_A, _ALICE, tid):
        assert value not in text, value
    assert (row["thread_id"], row["actor_email"]) == (tid, _ALICE)


# ---------------------------------------------------------------------------
# h. The call id (C4)
# ---------------------------------------------------------------------------

def _scripted(script: list[Any]) -> Any:
    from tests.unit.test_progress_notice import ScriptedClient

    return ScriptedClient(script)


def test_the_middleware_holds_the_call_id_while_the_call_runs(monkeypatch):
    """A real MAF run: the tool reads the call id of its own call, and none for
    another name. With no provider it reads none. Mutation: read the call id
    of any name -> red."""
    from acb_common import get_settings
    from acb_common.db import bind_tenant, release_tenant
    from agent_framework import Agent
    from orchestrator import _tool_injection

    seen: list[tuple[str, Any]] = []

    async def set_reminder(title: str) -> str:
        """Set a reminder."""
        seen.append(("own", stored_call.current_call_id("set_reminder")))
        seen.append(("other", stored_call.current_call_id("update_task")))
        return "ok"

    def _agent() -> Any:
        return Agent(client=_scripted([("calls", [("set_reminder", {"title": "x"})]),
                                       ("text", "Done.")]),
                     name="probe", tools=[set_reminder])

    monkeypatch.setattr(get_settings(), "chat_resume_orgs", _ORG_A)
    token = bind_tenant(_ORG_A)
    try:
        agents: list[Any] = [_agent()]
        shared = agents[0]
        _tool_injection._apply_resume_call_id(agents)
        assert agents[0] is not shared, "a native agent gets a per-run view"
        assert not any(isinstance(p, stored_call.CallIdProvider)
                       for p in shared.context_providers)
        asyncio.run(agents[0].run("hi"))
        assert seen == [("own", "c1-0"), ("other", None)]
        seen.clear()
        asyncio.run(_agent().run("hi"))
        assert seen == [("own", None), ("other", None)], "no provider, no call id"
    finally:
        release_tenant(token)
    assert stored_call._CALL_ID.get() is None


def test_the_seam_attaches_nothing_with_the_flag_off(monkeypatch):
    from acb_common import get_settings
    from agent_framework import Agent
    from orchestrator import _tool_injection

    monkeypatch.setattr(get_settings(), "chat_resume_orgs", "")
    agent = Agent(client=_scripted([]), name="probe")
    agents: list[Any] = [agent]
    _tool_injection._apply_resume_call_id(agents)
    assert agents[0] is agent


def test_the_seam_runs_after_the_steer_and_before_the_progress_notice():
    """``test_progress_notice.py`` keeps the notice right before the number
    check. The call id goes before both."""
    tree = ast.parse((_ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py")
                     .read_text(encoding="utf-8-sig"))
    inject = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_inject_agent_tools")
    names = [getattr(getattr(getattr(stmt, "value", None), "func", None), "id", None)
             for stmt in inject.body]
    assert names.count("_apply_resume_call_id") == 1
    assert (names.index("_apply_steer_turns") < names.index("_apply_resume_call_id")
            < names.index("_apply_progress_notice"))


def test_the_stored_call_takes_the_call_id_of_its_own_tool(
    durable_on, resume_on, liveness, no_persist,
):
    """The bind reads the call id that the middleware holds for the tool of the
    card. Mutation: drop ``call_id`` from the shape -> red."""
    fn = _stand_in("set_reminder", _SET_REMINDER)
    kwargs = {"title": f"x-{_SENTINEL}"}

    async def _with_id(**kw: Any) -> str:
        token = stored_call._CALL_ID.set(("set_reminder", "call_9"))
        try:
            return await fn(**kw)
        finally:
            stored_call._CALL_ID.reset(token)

    out = _run(liveness, "t-res-call-id", fn, kwargs, call=_with_id)
    assert _row(durable_on, out["rid"])["stored_call"] == _expected(
        "set_reminder", kwargs, call_id="call_9")


@pytest.mark.parametrize("stored", [
    {"name": "create_task", "args": {"fields": {"note": "a\x00b"}}},
    {"name": "create_task", "args": {"tags": ["x\x00"]}},
    {"name": "create_task", "args": {"k\x00ey": "v"}},
])
def test_a_nul_in_an_argument_value_or_key_stores_none(stored):
    """A NUL is escaped as \u0000 and jsonb refuses it. Store no call.
    Mutation: remove the NUL check in ``_stored_text`` -> red."""
    assert pending_ask._stored_text(stored) is None
    assert pending_ask._stored_text({"name": "create_task", "args": {"a": "b"}})


def test_a_nul_call_still_inserts_the_row_of_before(monkeypatch):
    import acb_graph

    seen: list[tuple[str, dict[str, Any]]] = []

    class _S:
        def execute(self, stmt, params):
            seen.append((str(stmt), dict(params)))

            class _R:
                rowcount = 1
            return _R()

    @contextlib.contextmanager
    def _session(_org):
        yield _S()

    monkeypatch.setattr(acb_graph, "tenant_session", _session)
    rid = uuid.uuid4().hex
    row = {"request_id": rid, "thread_id": "t", "kind": "confirmation",
           "payload": {"request_id": rid},
           "stored_call": {"name": "create_task", "args": {"tags": ["a\x00"]}}}
    assert pending_ask.insert_ask(_ORG_A, row)
    (sql, params), = seen
    assert sql == pending_ask._INSERT_SQL
    assert "stored" not in params and json.loads(params["payload"])


def test_a_failed_stored_insert_retries_the_row_without_the_call(monkeypatch):
    """The card never loses its row for the new column. The log holds the
    error type only."""
    calls: list[dict[str, Any]] = []

    def _insert(org, row):
        calls.append(dict(row))
        if "stored_call" in row:
            raise RuntimeError("SECRET-ARG-TEXT")
        return True

    async def _facts(_tid):
        return _ORG_A, "run", "agent", "a@b.c"

    logged: list[tuple[str, dict[str, Any]]] = []

    class _L:
        def warning(self, event, **kw):
            logged.append((event, kw))

        def info(self, *a, **kw):
            pass

    async def _bump(*a, **kw):
        return None

    monkeypatch.setattr(pending_ask, "insert_ask", _insert)
    monkeypatch.setattr(pending_ask, "_run_facts", _facts)
    monkeypatch.setattr(pending_ask, "_bump_my_day", _bump)
    monkeypatch.setattr(pending_ask, "_log", _L())
    rid = uuid.uuid4().hex
    ask = pending_ask.CardAsk(request_id=rid, kind="confirmation", question="q",
                              payload={"request_id": rid})
    org = asyncio.run(pending_ask._record("t", ask, {"name": "x", "args": {"a": 1}}))
    assert org == _ORG_A
    assert len(calls) == 2 and "stored_call" not in calls[1]
    assert [e for e, _ in logged] == ["pending_ask.stored_insert_failed"]
    assert logged[0][1]["error"] == "RuntimeError"
    assert "exc_info" not in logged[0][1]
