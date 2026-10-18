"""The member's "Not now" on a card that parks a run (owner request, 2026-10-18).

The owner wrote: "if I don't want to answer a question for some reason, I
should be allowed to turn off the card. The agent should take it as input
that the user does not want to answer the question at the moment and might
want to do something else later on."

**The envelope.** ``POST /agent/respond-input`` with ``{"declined": true,
"answer": ""}``. The parked Future resolves with ``{"answer": "",
"declined": True}`` and the run RESUMES. ``acb_skills.ask_tools`` phrases
what the model reads.

What this file holds (R7), hermetic, with the durable table in memory
(``test_pending_ask_flow._AskDb``). It holds no SQL, and the change adds
none: a decline moves a row to ``closed``, a state the R8 suite already
covers (``test_pending_ask_store.py``).

1. A decline resumes the parked run, and the tool result carries the decline
   text, with the default when the card has one.
2. The same on each parking site: ``ask_questions`` path A, the SDK's
   ``ask_user``, a blocking generative UI, and a Projects form.
3. A malformed envelope is refused: ``declined`` that is not a JSON boolean,
   and a decline that carries an answer.
4. A decline of a card whose run ended closes its row and starts no run. A
   second response after it is refused. Another member and another org are
   refused, as for an answer (WS-51 S2).
5. The shared instructions say not to ask a declined question again.

Run::

    uv run pytest tests/unit/test_question_decline.py -q
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from typing import Any

import pytest

executor = pytest.importorskip("orchestrator.executor")
pending_ask = pytest.importorskip("orchestrator.pending_ask")
stream_relay = pytest.importorskip("orchestrator.stream_relay")
at = importlib.import_module("acb_skills.ask_tools")
wa = importlib.import_module("acb_skills.write_artifact")

# Fixtures, resolved by name. The imports are load-bearing.
from tests.unit.test_chat_deploy_recovery import (  # noqa: E402, F401
    _DEAD,
    _seed_run,
    fake_redis,
    liveness,
    no_persist,
)
from tests.unit.test_pending_ask_flow import (  # noqa: E402, F401
    _ALICE,
    _BOB,
    _CAROL,
    _ORG_A,
    _ORG_B,
    _answer_client,
    _bind,
    _line,
    _park_a_confirmation,
    _run_parks,
    _seed_open_row,
    ask_db,
    flag_on,
)

@pytest.fixture(autouse=True)
def _no_stale_futures():
    """Path A pops its Future only on a timeout, so a test leaves one behind.
    Each test starts and ends with an empty registry."""
    executor._pending_user_input.clear()
    yield
    executor._pending_user_input.clear()


#: The CRM revenue card, as ``crm_tools._card`` builds it (the owner's
#: screenshot): Billed, Collected, and "Use the default".
_CRM_CARD = [{
    "header": "Revenue",
    "question": "When you ask about revenue, what should Metorite count?\n\n"
                "Each answer about revenue uses this. Until you answer, Metorite uses Billed.",
    "options": [
        {"label": "Billed", "description": "The amounts that you billed", "key": "invoice"},
        {"label": "Collected", "description": "The money that you received", "key": "payment_in"},
        {"label": "Use the default", "description": "Billed, until a CRM admin answers",
         "key": "@default"},
    ],
    "crm_question": "revenue",
}]


# ---------------------------------------------------------------------------
# 1. What the model reads
# ---------------------------------------------------------------------------

def test_the_decline_text_says_what_to_do_in_plain_words():
    text = at.declined_text()
    assert text.startswith("The member chose not to answer this question now.")
    for words in (
        "Do not ask it again in this reply.",
        "Do not act on this question, or on anything that depends on it.",
        "Do only the work that does not depend on it, or stop and let the member lead.",
        "Tell the member in one line what you did not do.",
        "The member can come back to it later.",
    ):
        assert words in text
    # With no stated default there is nothing to assume.
    assert "assumed" not in text and "use the default" not in text.lower()


def test_the_decline_text_names_the_crm_default_and_holds_the_question():
    text = at.declined_text(_CRM_CARD)
    assert "Use the default that the question states: Billed, until a CRM admin answers." in text
    assert "Do not act on" not in text
    assert 'crm_answer_question(question_id="revenue", choice="@default")' in text
    assert "24 hours" in text


def test_the_crm_default_key_is_the_crm_tools_key():
    crm_tools = importlib.import_module("acb_skills.crm_tools")
    assert at.CRM_DEFAULT_KEY == crm_tools.DEFAULT_KEY


def test_a_recommended_option_is_never_adopted_as_the_default():
    """Review of #1152. "Archive the 8 done tasks?", Yes recommended, then
    Not now: the model must not archive. A decline is never an answer."""
    card = [{"header": "Archive", "question": "Archive the 8 done tasks?", "options": [
        {"label": "Yes", "recommended": True}, {"label": "No"}]}]
    assert at.question_defaults(card) == []
    text = at.declined_text(card)
    assert "Yes" not in text and "recommended" not in text
    assert "use the default" not in text.lower() and "assumed" not in text
    assert "Do not act on this question, or on anything that depends on it." in text
    assert "stop and let the member lead" in text


def test_only_a_default_the_question_states_is_named():
    stated = {"header": "Env", "question": "Where?", "default": "Staging, until you say",
              "options": [{"label": "Production", "recommended": True}, {"label": "Staging"}]}
    plain = {"header": "Region", "question": "Which?", "options": [
        {"label": "EU", "recommended": True}, {"label": "US"}]}
    assert at.question_defaults([stated, plain]) == [("Env", "Staging, until you say")]
    one = at.declined_text([stated])
    assert "Use the default that the question states: Staging, until you say." in one
    assert "Production" not in one and "Do not act on" not in one
    # A card of two: the one with a default uses it, and the other is left
    # alone with everything that depends on it.
    two = at.declined_text([stated, plain])
    assert 'For "Env", use the default that it states: Staging, until you say.' in two
    assert "Do not act on a question with no stated default, or on anything that depends on it." in two
    assert "EU" not in two
    assert "what you assumed" in two and "what you did not do" in two


def test_ask_questions_keeps_a_stated_default_on_the_card():
    tid = "t-default"

    async def go() -> dict:
        executor._stream_relay_thread_id.set(tid)
        queue: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(queue)
        run = asyncio.create_task(at.ask_questions(json.dumps({"questions": [
            {"header": "Env", "question": "Where?", "default": "Staging",
             "options": [{"label": "Staging"}, {"label": "Production"}]},
            {"header": "Plain", "question": "Which?", "options": [{"label": "A"}]},
        ]})))
        evt = await asyncio.wait_for(queue.get(), timeout=2)
        rid = evt["value"]["request_id"]
        assert executor.resolve_user_input(rid, "", thread_id=tid, declined=True)
        await run
        return evt["value"]

    value = asyncio.run(go())
    assert value["questions"][0]["default"] == "Staging"
    assert "default" not in value["questions"][1], "the key goes only when set"


def test_only_a_json_true_declines():
    assert at.is_declined({"answer": "", "declined": True})
    assert not at.is_declined({"answer": "", "declined": "true"})
    assert not at.is_declined({"answer": "Yes"})
    assert not at.is_declined(None)
    assert at.answer_text({"answer": "Yes"}) == "User response: Yes"
    assert at.answer_text({"answer": " "}) == "User provided an empty response."
    assert at.answer_text({"answer": "", "declined": True}, _CRM_CARD) == at.declined_text(_CRM_CARD)


# ---------------------------------------------------------------------------
# 2. A decline resumes each parking site
# ---------------------------------------------------------------------------

def test_a_decline_resumes_ask_questions_and_names_the_default(monkeypatch):
    """Path A. The run is not cancelled: it gets the decline as its result."""
    tid = "t-decline-a"
    out: dict[str, Any] = {}

    async def go() -> None:
        executor._stream_relay_thread_id.set(tid)
        queue: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(queue)
        run = asyncio.create_task(at.ask_questions(json.dumps({"questions": _CRM_CARD})))
        evt = await asyncio.wait_for(queue.get(), timeout=2)
        rid = evt["value"]["request_id"]
        assert executor.resolve_user_input(rid, "", thread_id=tid, declined=True)
        out["result"] = await asyncio.wait_for(run, timeout=2)
        out["cancelled"] = run.cancelled()
        # A second response to the same card finds no Future.
        out["again"] = executor.resolve_user_input(rid, "Billed", thread_id=tid)

    asyncio.run(go())
    assert out["cancelled"] is False
    assert out["result"] == at.declined_text(_CRM_CARD)
    assert "Use the default that the question states: Billed, until a CRM admin answers." in out["result"]
    assert out["again"] is False


def test_a_decline_from_another_thread_resolves_nothing():
    """H-201: the decline must name the thread that owns the card."""
    tid = "t-decline-owner"

    async def go() -> bool:
        executor._stream_relay_thread_id.set(tid)
        queue: asyncio.Queue = asyncio.Queue()
        executor._active_run_queue.set(queue)
        run = asyncio.create_task(at.ask_questions(json.dumps({"questions": _CRM_CARD})))
        evt = await asyncio.wait_for(queue.get(), timeout=2)
        rid = evt["value"]["request_id"]
        refused = executor.resolve_user_input(rid, "", thread_id="t-other", declined=True)
        assert executor.resolve_user_input(rid, "", thread_id=tid, declined=True)
        await run
        return refused

    assert asyncio.run(go()) is False


def test_the_control_bus_carries_the_decline():
    """Cross-worker (P1-2): the applier passes ``declined`` only for a JSON true."""
    seen: list[dict] = []
    real = executor.resolve_user_input

    def _spy(rid, answer, was_freeform=True, *, thread_id, declined=False):
        seen.append({"rid": rid, "answer": answer, "declined": declined, "thread": thread_id})
        return True

    executor.resolve_user_input = _spy
    try:
        apply = executor._respond_input_applier("t-bus")
        apply({"request_id": "r1", "answer": "", "declined": True})
        apply({"request_id": "r2", "answer": "", "declined": "true"})
        apply({"request_id": "r3", "answer": "Yes"})
    finally:
        executor.resolve_user_input = real
    assert [s["declined"] for s in seen] == [True, False, False]
    assert all(s["thread"] == "t-bus" for s in seen)


@pytest.mark.asyncio
async def test_a_decline_answers_the_sdk_ask_user_with_the_decline_text(monkeypatch):
    async def _push(_tid: str, _line: str) -> None:
        return None

    monkeypatch.setattr(executor, "_push_sse_to_stream", _push)
    handler = executor._make_user_input_handler("t-sdk")

    async def _decline() -> None:
        for _ in range(50):
            if executor._pending_user_input:
                break
            await asyncio.sleep(0.01)
        rid = next(r for r in list(executor._pending_user_input)
                   if executor._pending_user_input.owner_of(r) == "t-sdk")
        assert executor.resolve_user_input(rid, "", thread_id="t-sdk", declined=True)

    task = asyncio.create_task(_decline())
    result = await handler({"question": "Proceed?", "choices": ["Yes", "No"]}, {})
    await task
    assert result == {"answer": at.declined_text(), "wasFreeform": True}


def test_a_decline_resumes_a_blocking_generative_ui(monkeypatch):
    q: asyncio.Queue = asyncio.Queue()
    monkeypatch.setattr(executor, "resolve_run_queue", lambda _sid=None: q)
    wa.derive_artifact_context(session_id="t-genui-decline")

    async def go() -> tuple[str, dict]:
        task = asyncio.ensure_future(wa.emit_generative_ui(
            '{"type":"template","hitl":true,"props":{"name":"optionPicker","data":{}}}'
        ))
        ev = await asyncio.wait_for(q.get(), timeout=2)
        rid = ev["value"]["request_id"]
        owner = executor._pending_user_input.owner_of(rid)
        assert executor.resolve_user_input(rid, "", thread_id=owner, declined=True)
        return rid, await asyncio.wait_for(task, timeout=2)

    rid, res = asyncio.run(go())
    assert res["ok"] is True and res["declined"] is True
    assert res["response"] is None
    assert res["request_id"] == rid, "the chat keeps the card declined after a reload"
    assert res["note"].startswith("The member chose not to answer this card now.")


@pytest.mark.asyncio
async def test_a_declined_form_writes_nothing_and_says_so(monkeypatch):
    pytest.importorskip("skill_projects", reason="skill-projects not installed")
    from skill_projects import forms as F

    async def _declined(_ui: str) -> dict:
        return {"ok": True, "declined": True, "response": None, "note": "x"}

    monkeypatch.setattr(wa, "emit_generative_ui", _declined)
    out = await F._ask({"type": "template", "props": {"name": "planCard", "data": {}}})
    assert out == F.declined_form()
    assert out.startswith(F.NOT_SUBMITTED)
    assert "The member chose not to answer this form now." in out


@pytest.mark.asyncio
async def test_the_plan_card_s_cancel_reads_as_a_cancel(monkeypatch):
    """Review of #1152: the plan card's Cancel sends the decline, and the
    model reads that the member cancelled the plan, not "not now"."""
    pytest.importorskip("skill_projects", reason="skill-projects not installed")
    import skill_projects
    from skill_projects import forms as F

    from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
    from tests.unit.test_projects_agent_writes import PLAN_DEPS, _plan_gateway

    async def _declined(_ui: str) -> dict:
        return {"ok": True, "declined": True, "response": None, "note": "x"}

    monkeypatch.setattr(wa, "emit_generative_ui", _declined)
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _plan_gateway())
    out = await skill_projects.propose_plan("Frame", json.dumps(PLAN_DEPS))
    assert out == F.declined_plan()
    assert out.startswith(F.NOT_SUBMITTED)
    assert "The member cancelled the plan. Nothing was created." in out
    assert "Do not propose this plan again unless the member asks for it." in out
    assert "not to answer" not in out
    assert asked == [] and writes(calls) == []


# ---------------------------------------------------------------------------
# 3. The route: the envelope, and a malformed one
# ---------------------------------------------------------------------------

def _post(client, body: dict):
    return client.post("/agent/respond-input", json=body)


def test_the_route_relays_the_decline_to_the_parked_run(monkeypatch, fake_redis):
    calls: list[dict] = []

    def _resolve(rid, answer, was_freeform=True, *, thread_id, declined=False):
        calls.append({"rid": rid, "answer": answer, "declined": declined, "thread": thread_id})
        return True

    monkeypatch.setattr(executor, "resolve_user_input", _resolve)
    client = _answer_client(monkeypatch, _ALICE, _ORG_A)
    rid = uuid.uuid4().hex
    res = _post(client, {"request_id": rid, "answer": "", "declined": True, "thread_id": "t-r"})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "declined": True}
    assert calls == [{"rid": rid, "answer": "", "declined": True, "thread": "t-r"}]

    # An answer is exactly as before: no `declined` on the wire back.
    res = _post(client, {"request_id": rid, "answer": "Billed", "thread_id": "t-r"})
    assert res.json() == {"ok": True}
    assert calls[-1]["declined"] is False


def test_the_control_command_names_the_decline_only_on_a_decline(monkeypatch, fake_redis):
    sent: list[dict] = []

    async def _dispatch(_tid: str, command: dict) -> bool:
        sent.append(command)
        return True

    monkeypatch.setattr(executor, "resolve_user_input", lambda *_a, **_k: False)
    monkeypatch.setattr(stream_relay, "dispatch_control", _dispatch)
    client = _answer_client(monkeypatch, _ALICE, _ORG_A)
    rid = uuid.uuid4().hex
    assert _post(client, {"request_id": rid, "answer": "", "declined": True,
                          "thread_id": "t-w"}).status_code == 200
    assert _post(client, {"request_id": rid, "answer": "Yes", "thread_id": "t-w"}).status_code == 200
    assert sent[0]["declined"] is True
    assert "declined" not in sent[1]


@pytest.mark.parametrize("body", [
    {"answer": "", "declined": "yes"},
    {"answer": "", "declined": 1},
    {"answer": "Billed", "declined": True},
    {"answer": "", "declined": None},
])
def test_a_malformed_decline_is_refused(monkeypatch, fake_redis, body):
    called: list[Any] = []
    monkeypatch.setattr(executor, "resolve_user_input",
                        lambda *a, **k: called.append((a, k)) or True)
    client = _answer_client(monkeypatch, _ALICE, _ORG_A)
    res = _post(client, {"request_id": uuid.uuid4().hex, "thread_id": "t-m", **body})
    assert res.status_code == 422, res.text
    assert called == [], "a malformed envelope must reach no run"


# ---------------------------------------------------------------------------
# 4. A card whose run ended: the row closes, and no run starts
# ---------------------------------------------------------------------------

def _decline(monkeypatch, tid: str, rid: str, *, email: str = _ALICE, org: str = _ORG_A):
    return _post(_answer_client(monkeypatch, email, org), {
        "request_id": rid, "answer": "", "declined": True, "thread_id": tid,
    })


def test_a_late_decline_closes_the_row_and_starts_no_run(
    flag_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-late-decline"
    rid = _park_a_confirmation(liveness, tid)
    res = _decline(monkeypatch, tid, rid)
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "declined": True}
    assert "resumeMessage" not in res.text, "a decline starts no new run"
    row = ask_db.rows[(_ORG_A, rid)]
    assert row["state"] == "closed", "a closed row never draws its card again"
    assert row["answer"] is None

    # A second response after the decline is refused: no row waits now.
    again = _decline(monkeypatch, tid, rid)
    assert again.status_code == 409
    answer = _post(_answer_client(monkeypatch, _ALICE, _ORG_A), {
        "request_id": rid, "answer": "APPROVE", "thread_id": tid,
    })
    assert answer.status_code == 409
    assert isinstance(answer.json()["detail"], str), "no resend after a decline"


def test_another_member_cannot_decline_a_parked_card(
    flag_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-bob-decline"
    rid = _park_a_confirmation(liveness, tid)
    res = _decline(monkeypatch, tid, rid, email=_BOB)
    assert res.status_code == 409
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "parked"


def test_another_org_cannot_decline_a_parked_card(
    flag_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-cross-decline"
    rid = _park_a_confirmation(liveness, tid)
    res = _decline(monkeypatch, tid, rid, email=_CAROL, org=_ORG_B)
    assert res.status_code == 409
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "parked"


def test_a_late_decline_needs_the_thread_that_asked(
    flag_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-own-decline"
    rid = _park_a_confirmation(liveness, tid)
    res = _decline(monkeypatch, "t-some-other-room", rid)
    assert res.status_code == 409
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "parked"


def test_an_open_card_of_a_live_run_stays_open_on_a_late_decline(
    flag_on, ask_db, liveness, no_persist, monkeypatch,
):
    """The decline did not reach the run that still waits. Closing its row
    would leave that run parked on a card nobody can see, so the row stays
    open and the member can try again (the rule of a late answer)."""
    from orchestrator import run_liveness

    tid = "t-live-open"
    asyncio.run(stream_relay.mark_active(tid, reset=True, actor=_ALICE))
    monkeypatch.setattr(run_liveness, "_LOCAL_RUNS", {tid})
    rid = _seed_open_row(ask_db, _ORG_A, tid, _ALICE)
    res = _decline(monkeypatch, tid, rid)
    assert res.status_code == 409
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "open"


def test_a_dead_run_s_open_card_closes_on_a_decline(
    flag_on, ask_db, liveness, no_persist, monkeypatch,
):
    """A restart killed the run while its card was open. The dead run is
    closed and saved, the row closes, and no run starts."""
    tid = "t-died-decline"
    _seed_run(liveness, tid, _DEAD, record={
        "org": _ORG_A, "messageId": f"asst-{tid}", "agent": "orchestrator",
        "runId": f"run-{tid}", "tokens": [],
    })
    rid = _seed_open_row(ask_db, _ORG_A, tid, _ALICE)
    res = _decline(monkeypatch, tid, rid)
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "declined": True}
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "closed"
    assert f"cc:active:{tid}" not in liveness.store, "the dead run is closed first"


def test_with_durable_asks_off_a_decline_no_run_takes_is_a_409(
    ask_db, liveness, no_persist, monkeypatch,
):
    res = _decline(monkeypatch, "t-off", uuid.uuid4().hex)
    assert res.status_code == 409
    assert isinstance(res.json()["detail"], str)


def test_a_live_decline_closes_the_durable_row(flag_on, ask_db, liveness):
    """The run takes the decline, and the row says closed, not answered."""
    tid, rid = "t-live-decline", uuid.uuid4().hex
    liveness.store[f"cc:runactor:{tid}"] = _ALICE
    out: dict[str, Any] = {}

    async def _the_run() -> None:
        executor._stream_relay_thread_id.set(tid)
        _bind(_ORG_A)
        fut = asyncio.get_running_loop().create_future()
        executor._pending_user_input.park(rid, fut, tid)
        await executor._push_sse_to_stream(tid, _line({
            "type": "CUSTOM", "name": "elicitation_requested",
            "value": {"questions": _CRM_CARD, "request_id": rid},
        }))
        assert executor.resolve_user_input(rid, "", thread_id=tid, declined=True)
        out["result"] = await executor.wait_user_future(fut, 30, thread_id=tid)

    async def go() -> None:
        run = asyncio.create_task(_the_run())
        stream_relay._DETACHED_TASKS[tid] = run
        await asyncio.wait_for(run, timeout=5)
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(go())
    assert at.is_declined(out["result"])
    row = ask_db.rows[(_ORG_A, rid)]
    assert row["state"] == "closed" and row["answer"] is None


def test_a_decline_during_the_park_closes_the_row(flag_on, ask_db, liveness, monkeypatch):
    """Review of #1152. The decline lands while the row moves to parked. The
    run takes it and goes on, and the row says closed, never answered."""
    tid, rid = "t-race-decline", uuid.uuid4().hex
    liveness.store[f"cc:runactor:{tid}"] = _ALICE
    real_move = ask_db.move_ask

    def _move_and_decline(org, req, *, to, from_states, answer=None):
        row = real_move(org, req, to=to, from_states=from_states, answer=answer)
        if to == "parked":
            assert executor.resolve_user_input(rid, "", thread_id=tid, declined=True)
        return row

    monkeypatch.setattr(pending_ask, "move_ask", _move_and_decline)
    out: dict[str, Any] = {}

    async def _the_run() -> None:
        executor._stream_relay_thread_id.set(tid)
        _bind(_ORG_A)
        fut = asyncio.get_running_loop().create_future()
        executor._pending_user_input.park(rid, fut, tid)
        await executor._push_sse_to_stream(tid, _line({
            "type": "CUSTOM", "name": "elicitation_requested",
            "value": {"questions": _CRM_CARD, "request_id": rid},
        }))
        out["result"] = await executor.wait_user_future(fut, 30, thread_id=tid)

    async def go() -> None:
        run = asyncio.create_task(_the_run())
        stream_relay._DETACHED_TASKS[tid] = run
        await asyncio.wait_for(run, timeout=5)
        out["cancelled"] = run.cancelled()

    asyncio.run(go())
    assert at.is_declined(out["result"]), "the run took the decline"
    assert out["cancelled"] is False
    row = ask_db.rows[(_ORG_A, rid)]
    assert row["state"] == "closed" and row["answer"] is None


# ---------------------------------------------------------------------------
# 5. The shared instructions: a declined question is not asked again
# ---------------------------------------------------------------------------

def test_every_agent_is_told_not_to_ask_a_declined_question_again():
    addendum = importlib.import_module("acb_skills.addendum")
    full = "".join(s.text for s in addendum.FULL_SECTIONS if "ask_questions" in s.gate)
    compact = "".join(s.text for s in addendum.COMPACT_SECTIONS if "ask_questions" in s.gate)
    assert ("Do not ask a declined question again in this conversation, unless the "
            "member brings up the topic.") in full
    assert ("do not ask it again in this conversation, unless the member brings up "
            "the topic") in compact
