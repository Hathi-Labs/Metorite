"""HRD-22: a late approve of a parked card is the member's own consent, once.

Spec: ``project-docs/specs/chat_run_continuity.md`` S2 item 8, and
``chat_failure_modes.md`` row C6 and slice HRD-22 (task 3231). Module:
``acb_skills.late_approve``. Flag ``CHAT_LATE_APPROVE_ORGS`` (default empty).

Hermetic: the rows of ``chat_pending_ask`` live in the in-memory table of
``test_pending_ask_flow.py``, and the tenant Redis is a fake that keeps the
wire keys. The SQL and a real Redis are ``test_late_approve_grant_r8.py``.

What this file holds (R7), each with the mutation that turns it red:

1. The production sequence (2026-11-14): a card parks, the member approves it
   late, and the new run makes the call again. One write, and no second card.
   Drop the use in ``request_confirmation`` and the second run draws a card.
   The real ``agent-crm`` ``update_deal_status``, loaded once for each run as
   the loader names it, does the same.
2. A second use finds nothing. Read the grant with GET and the third run
   writes a second time.
3. A changed card (another stage, another deal, a twin title, a renamed deal,
   other facts) gets a normal card. Compare the keys only, or the text only,
   or drop the facts, and one case writes.
4. An act runs once. A stale twin card, parked before a live approve of the
   same act, writes nothing on a late approve. Drop the live cleanup, and the
   twin writes a second time. Two cards of two acts in one thread each keep
   their own grant: key the grant by the thread alone, and one is lost.
5. A late decline and a late REJECT write nothing and open nothing, and drop
   the offer. So does an answer that came during the park.
6. A grant never crosses a thread, a member or an org.
7. The cards that never take a grant: rows, ``bulk_update`` (it keys as
   ``update_task``), a send, a call with no act key, and a cut card. Test the
   family head and not the real tool, and ``bulk_update`` takes one.
8. Off is today: with the flag off, nothing is read or written, and the
   message is the same, byte for byte.
9. A Redis fault shows the normal card.
10. The new message reads as an approve, and only a grant adds its paragraph.
11. The places: the grant write sits after the move of the row, and the use
    sits before the card. Each record has one writer.

Run::

    uv run pytest tests/unit/test_late_approve_grant.py -q
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

stream_relay = pytest.importorskip("orchestrator.stream_relay", reason="orchestrator not installed")
pending_ask = pytest.importorskip("orchestrator.pending_ask")
executor = pytest.importorskip("orchestrator.executor")

from acb_skills import late_approve  # noqa: E402
from acb_skills.ask_tools import card_call, request_confirmation  # noqa: E402

# Fixtures, resolved by name. The imports are load-bearing.
from tests.unit.test_chat_deploy_recovery import (  # noqa: E402, F401
    fake_redis,
    liveness,
    no_persist,
)
from tests.unit.test_pending_ask_flow import (  # noqa: E402, F401
    _ORG_A,
    _ORG_B,
    _answer_client,
    _events,
    ask_db,
    flag_on,
)

_ROOT = Path(__file__).resolve().parents[2]
_ALICE = "alice@ask-a.test"
_BOB = "bob@ask-a.test"

#: The deals of the stand-in CRM: id to title. A test renames one.
DEALS: dict[str, str] = {}
#: Each write that a stand-in tool made, in order.
WRITES: list[tuple[str, ...]] = []
#: The ids that ``assign`` resolves a name to. A test moves one.
PEOPLE: dict[str, str] = {}

#: The annotation that a first-party write carries (``tool_annotations``).
_CARD_WRITE = {"read_only": False, "destructive": True, "idempotent": False,
               "open_world": True, "card_write": True}


async def update_deal_status(deal_id: str, stage: str, lost_reason: str | None = None) -> str:
    """A stand-in of ``agent-crm`` ``update_deal_status``, with its pinned
    parameters and its annotation: one card, then one write on approve."""
    title = DEALS[deal_id]
    ok = await request_confirmation(
        title=f"Move this deal to {stage}?",
        detail=f"{title} → {stage}",
        context=f"deal: {title}\nstage: {stage}",
    )
    if not ok:
        return f"{title} was not moved to {stage}."
    WRITES.append(("deal", deal_id, stage))
    return f"{title} moved to {stage}."


update_deal_status.__tool_risk__ = dict(_CARD_WRITE)  # type: ignore[attr-defined]


async def assign(task_id: str, assignees: str) -> str:
    """A stand-in of the Projects ``assign``: the card shows the name, and the
    resolved person is a fact that it binds (WAC-4)."""
    from acb_skills.whatsapp_acts import card_facts

    card_facts(assignees=[PEOPLE[assignees]])
    ok = await request_confirmation(title="Assign this task?",
                                    detail=f"Task {task_id} → {assignees}")
    if ok:
        WRITES.append(("assign", task_id, PEOPLE[assignees]))
    return "assigned" if ok else "not assigned"


assign.__tool_risk__ = dict(_CARD_WRITE)  # type: ignore[attr-defined]


async def bulk_update(task_ids: str, status: str) -> str:
    """A stand-in of the class C ``bulk_update``. Its act keys read as the
    family of ``update_task``, a listed write, but the tool itself is not."""
    ok = await request_confirmation(title="Update these tasks?", detail=f"{task_ids} → {status}")
    if ok:
        WRITES.append(("bulk", task_ids, status))
    return "done" if ok else "not done"


bulk_update.__tool_risk__ = dict(_CARD_WRITE)  # type: ignore[attr-defined]


async def send_email(to: str, subject: str) -> str:
    """A stand-in send. Its name is no first-party write of the list."""
    ok = await request_confirmation(title="Send this email?", detail=f"To {to} · {subject}")
    if ok:
        WRITES.append(("mail", to, subject))
    return "sent" if ok else "not sent"


async def update_tasks(changes: str) -> str:
    """A stand-in batch write with rows. A card with rows takes no grant."""
    ticked = await request_confirmation(
        title="Update these tasks?", rows=[{"id": "r1", "label": "Task one", "checked": True}])
    if ticked:
        WRITES.append(("tasks", changes))
    return "done" if ticked else "not done"


update_tasks.__tool_risk__ = dict(_CARD_WRITE)  # type: ignore[attr-defined]


class _Store:
    """The tenant Redis, by wire key. ``fail`` makes each call raise."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}
        self.calls: list[str] = []
        self.fail: BaseException | None = None

    def _check(self, op: str, k: Any) -> None:
        self.calls.append(f"{op} {k.value}")
        if self.fail is not None:
            raise self.fail

    async def set(self, k: Any, value: str, ex: int | None = None, nx: bool = False,
                  **_kw: Any) -> bool | None:
        self._check("set", k)
        if nx and k.value in self.data:
            return None
        self.data[k.value] = value
        self.ttl[k.value] = ex
        return True

    async def get(self, k: Any) -> str | None:
        self._check("get", k)
        return self.data.get(k.value)

    async def getdel(self, k: Any) -> str | None:
        self._check("getdel", k)
        self.ttl.pop(k.value, None)
        return self.data.pop(k.value, None)

    async def delete(self, k: Any) -> int:
        self._check("delete", k)
        self.ttl.pop(k.value, None)
        return 1 if self.data.pop(k.value, None) is not None else 0

    def keys(self, namespace: str) -> list[str]:
        return sorted(k for k in self.data if f":{namespace}:" in k)


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(late_approve, "_redis", lambda: s)
    DEALS.clear()
    DEALS.update({"d-neuro": "Neuropeak", "d-orbit": "Orbit"})
    PEOPLE.clear()
    PEOPLE.update({"Sougata": "sougata@acme.test"})
    WRITES.clear()
    return s


@pytest.fixture
def late_on(monkeypatch, flag_on, store):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", f"{_ORG_A},{_ORG_B}")
    return store


def _run(r, tid: str, fn: Any = update_deal_status, *, member: str = _ALICE,
         org: str = _ORG_A, verified: bool = True, keyed: bool = True,
         agent: str = "orchestrator", live: str | None = None,
         **kwargs: Any) -> dict[str, Any]:
    """One run on *tid* that calls *fn* once. ``parked`` when its card parked.

    The run binds what a chat run binds: the thread, the tenant, the verified
    member and the agent, and (with *keyed*) the act key and the tool of the
    call. With *live*, the member answers the card at once, so it never
    parks."""
    from acb_common import bind_run_context
    from acb_common.db import bind_tenant

    r.store[f"cc:runactor:{tid}"] = member
    out: dict[str, Any] = {}
    seen = len(_cards(r, tid))

    async def _the_run() -> None:
        executor._stream_relay_thread_id.set(tid)
        bind_tenant(org)
        bind_run_context(user=member, member_verified=verified, agent=agent, thread_id=tid)
        scope = card_call(fn, (), kwargs) if keyed else contextlib.nullcontext()
        with scope:
            out["result"] = await fn(**kwargs)

    async def _answer_live() -> None:
        while len(_cards(r, tid)) == seen:
            await asyncio.sleep(0.002)
        rid = _cards(r, tid)[-1]["request_id"]
        executor._pending_user_input[rid].set_result({"answer": live})

    async def go() -> None:
        run = asyncio.create_task(_the_run())
        stream_relay._DETACHED_TASKS[tid] = run
        helper = asyncio.create_task(_answer_live()) if live else None
        try:
            # The first park of a process imports the tool modules, so allow time.
            await asyncio.wait_for(run, timeout=60)
        except asyncio.CancelledError:
            out["parked"] = True
        finally:
            if helper is not None:
                helper.cancel()

    if live:
        saved = pending_ask.park_after_seconds
        pending_ask.park_after_seconds = lambda: 30.0
        try:
            asyncio.run(go())
        finally:
            pending_ask.park_after_seconds = saved
    else:
        asyncio.run(go())
    return out


def _cards(r, tid: str) -> list[dict[str, Any]]:
    return [e["value"] for e in _events(r, tid) if e.get("name") == "confirmation_requested"]


def card_call_keys(fn: Any, **kwargs: Any) -> Any:
    """The act keys that ``card_call`` binds for one call of *fn*."""
    from acb_skills.ask_tools import act_keys

    return act_keys(fn.__module__, fn.__name__, kwargs)


def _envelope(question: str, answer: str = "APPROVE") -> str:
    from gateway.chat_recovery import compose_card_answer

    return compose_card_answer(question, answer)


def _approve_late(monkeypatch, tid: str, rid: str, *, member: str = _ALICE,
                  org: str = _ORG_A, answer: str = "APPROVE"):
    return _answer_client(monkeypatch, member, org).post("/agent/respond-input", json={
        "request_id": rid, "answer": answer, "thread_id": tid,
    })


def _park_and_approve(r, monkeypatch, tid: str, **kwargs: Any) -> str:
    """Run 1 parks the card, and the member approves it late. The request id."""
    kwargs = {"deal_id": "d-neuro", "stage": "Payment Complete", **kwargs}
    assert _run(r, tid, **kwargs).get("parked")
    rid = _cards(r, tid)[-1]["request_id"]
    res = _approve_late(monkeypatch, tid, rid)
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["error"] == "run_restarted"
    return rid


def _grants(store: _Store) -> list[str]:
    return store.keys(late_approve.GRANT_NAMESPACE)


def _offers(store: _Store) -> list[str]:
    return [k for k in store.keys(late_approve.OFFER_NAMESPACE)
            if store.data[k] != late_approve._SPENT]


# ---------------------------------------------------------------------------
# 1 and 2. The production sequence, and a second use
# ---------------------------------------------------------------------------

def test_the_production_sequence_writes_once_with_one_card(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-prod"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Payment Complete").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "parked"
    assert WRITES == [], "a parked card writes nothing"

    res = _approve_late(monkeypatch, tid, rid)
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    from gateway.chat_recovery import LATE_APPROVE_NOTE

    assert detail["resumeMessage"] == (
        _envelope("Move this deal to Payment Complete?") + "\n\n" + LATE_APPROVE_NOTE)
    assert detail["resumeDisplay"]["decision"] == "approve"
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "answered"

    # The new run makes the same call: one write, and no second card.
    out = _run(liveness, tid, deal_id="d-neuro", stage="Payment Complete")
    assert "parked" not in out, out
    assert out["result"] == "Neuropeak moved to Payment Complete."
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]
    assert len(_cards(liveness, tid)) == 1, "the member saw ONE card"
    assert _grants(late_on) == [], "the grant is used up"

    # A second use finds nothing: the next same call asks again.
    assert _run(liveness, tid, deal_id="d-neuro", stage="Payment Complete").get("parked")
    assert len(_cards(liveness, tid)) == 2
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_the_records_carry_the_tenant_the_member_the_thread_and_the_act(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-keys"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    text = late_approve.card_text_of(
        "Move this deal to Won?", "Neuropeak → Won", "deal: Neuropeak\nstage: Won")
    keys = late_approve.keys_wire(card_call_keys(update_deal_status, deal_id="d-neuro",
                                                 stage="Won"), "orchestrator")
    digest = late_approve.digest_of(text, keys)
    ask = f"cc:{_ORG_A}:{late_approve.ASK_NAMESPACE}:{_ALICE}:{tid}:{rid}"
    offer = f"cc:{_ORG_A}:{late_approve.OFFER_NAMESPACE}:{_ALICE}:{tid}:{digest}"
    assert sorted(late_on.data) == sorted([ask, offer])
    assert late_on.data[ask] == digest
    assert late_on.ttl[ask] == late_on.ttl[offer] == pending_ask._ttl_hours() * 3600
    record = json.loads(late_on.data[offer])
    assert (record["rid"], record["text"], record["keys"]) == (rid, text, keys)

    assert _approve_late(monkeypatch, tid, rid).status_code == 409
    grant = f"cc:{_ORG_A}:{late_approve.GRANT_NAMESPACE}:{_ALICE}:{tid}:{digest}"
    assert list(late_on.data) == [grant], "the ask and the offer are taken once"
    assert late_on.ttl[grant] == late_approve.GRANT_TTL_S == 30 * 60


def test_the_park_binds_the_records_to_the_life_of_the_row(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """A row that expires in 15 minutes gets records of 15 minutes or less."""
    from datetime import UTC, datetime, timedelta

    real = ask_db.move_ask

    def _soon(org, rid, **kw):
        row = real(org, rid, **kw)
        if row is not None:
            row["expires_at"] = (datetime.now(UTC) + timedelta(seconds=900)).isoformat()
        return row

    monkeypatch.setattr(pending_ask, "move_ask", _soon)
    tid = "t-hrd22-row-life"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    assert late_on.ttl and all(0 < t <= 900 for t in late_on.ttl.values()), late_on.ttl


def test_a_record_lives_no_longer_than_its_row():
    from datetime import UTC, datetime, timedelta

    soon = (datetime.now(UTC) + timedelta(seconds=900)).isoformat()
    assert 890 <= late_approve._row_ttl_s({"expires_at": soon}, 604800) <= 900
    gone = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    assert late_approve._row_ttl_s({"expires_at": gone}, 604800) < 0
    assert late_approve._row_ttl_s({"expires_at": "not a time"}, 604800) == 0
    assert late_approve._row_ttl_s({}, 3600) == 3600


# ---------------------------------------------------------------------------
# 3. A changed card gets a normal card
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("change", ["stage", "deal", "twin", "renamed"])
def test_a_changed_card_gets_a_normal_card(
    change, late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Each change keeps one of the two parts equal: the act key ("stage",
    "renamed") or the card text ("twin", another deal with the same title)."""
    tid = f"t-hrd22-changed-{change}"
    _park_and_approve(liveness, monkeypatch, tid)
    call = {"deal_id": "d-neuro", "stage": "Payment Complete"}
    if change == "stage":
        call["stage"] = "Lost"
    elif change == "deal":
        call["deal_id"] = "d-orbit"
    elif change == "twin":
        DEALS["d-twin"] = "Neuropeak"  # the same card text, another deal id
        call["deal_id"] = "d-twin"
    else:
        DEALS["d-neuro"] = "Neuropeak Labs"  # the record changed since the card
    assert _run(liveness, tid, **call).get("parked"), "a changed card asks again"
    assert WRITES == []
    assert len(_cards(liveness, tid)) == 2
    # Another act never takes the grant away: the card that it names still
    # gets it, once.
    DEALS["d-neuro"] = "Neuropeak"
    assert "parked" not in _run(liveness, tid, deal_id="d-neuro", stage="Payment Complete")
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_other_facts_behind_the_same_words_get_a_normal_card(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """The card shows a name. The tool binds the person that the name resolved
    to (WAC-4 facts). A name that now resolves to another person asks again."""
    tid = "t-hrd22-facts"
    assert _run(liveness, tid, assign, task_id="t-7", assignees="Sougata").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    offer = json.loads(late_on.data[_offers(late_on)[0]])
    assert "sougata@acme.test" in offer["text"], "the facts are in the act"
    assert _approve_late(monkeypatch, tid, rid).status_code == 409
    PEOPLE["Sougata"] = "sougata.b@acme.test"
    assert _run(liveness, tid, assign, task_id="t-7", assignees="Sougata").get("parked")
    assert WRITES == []
    PEOPLE["Sougata"] = "sougata@acme.test"
    assert "parked" not in _run(liveness, tid, assign, task_id="t-7", assignees="Sougata")
    assert WRITES == [("assign", "t-7", "sougata@acme.test")]


# ---------------------------------------------------------------------------
# 4. An act runs once
# ---------------------------------------------------------------------------

def test_a_stale_twin_never_writes_after_a_live_approve(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Review P1. Card C1 parks. The member writes again, the model draws C2
    with the same act, and the member approves C2 live (write 1). A late tap
    on C1 then writes nothing, and its message has no paragraph."""
    tid = "t-hrd22-twin"
    call = {"deal_id": "d-neuro", "stage": "Payment Complete"}
    assert _run(liveness, tid, **call).get("parked")
    c1 = _cards(liveness, tid)[0]["request_id"]
    out = _run(liveness, tid, live="APPROVE", **call)
    assert "parked" not in out and WRITES == [("deal", "d-neuro", "Payment Complete")]
    assert _offers(late_on) == [], "the live approve spent the act"

    res = _approve_late(monkeypatch, tid, c1)
    assert res.status_code == 409
    assert res.json()["detail"]["resumeMessage"] == _envelope(
        "Move this deal to Payment Complete?"), "no grant, so no paragraph"
    assert _grants(late_on) == []
    assert _run(liveness, tid, **call).get("parked"), "the next same call asks again"
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_a_twin_that_parks_after_a_live_approve_records_no_offer(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """A twin card open at the live approve parks later. The SPENT mark keeps
    it from an offer."""
    tid = "t-hrd22-twin-later"
    call = {"deal_id": "d-neuro", "stage": "Payment Complete"}
    assert "parked" not in _run(liveness, tid, live="APPROVE", **call)
    assert _run(liveness, tid, **call).get("parked")
    c2 = _cards(liveness, tid)[-1]["request_id"]
    assert _offers(late_on) == []
    assert _approve_late(monkeypatch, tid, c2).status_code == 409
    assert _grants(late_on) == []
    assert _run(liveness, tid, **call).get("parked")
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_a_twin_that_parks_after_a_used_grant_records_no_offer(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """A used grant marks the act SPENT too. A twin card that parks after the
    use records no offer, so its late tap writes nothing."""
    tid = "t-hrd22-twin-after-use"
    call = {"deal_id": "d-neuro", "stage": "Payment Complete"}
    _park_and_approve(liveness, monkeypatch, tid, **call)
    assert "parked" not in _run(liveness, tid, **call)
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]
    assert _run(liveness, tid, **call).get("parked")
    twin = _cards(liveness, tid)[-1]["request_id"]
    assert _offers(late_on) == []
    assert _approve_late(monkeypatch, tid, twin).status_code == 409
    assert _grants(late_on) == []
    assert _run(liveness, tid, **call).get("parked")
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_two_acts_in_one_thread_each_keep_their_grant(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Two cards of two acts park, and the member approves both late. Each
    act runs once, in either order."""
    tid = "t-hrd22-two"
    _park_and_approve(liveness, monkeypatch, tid, stage="Payment Complete")
    _park_and_approve(liveness, monkeypatch, tid, deal_id="d-orbit", stage="Won")
    assert len(_grants(late_on)) == 2
    assert "parked" not in _run(liveness, tid, deal_id="d-orbit", stage="Won")
    assert "parked" not in _run(liveness, tid, deal_id="d-neuro", stage="Payment Complete")
    assert WRITES == [("deal", "d-orbit", "Won"), ("deal", "d-neuro", "Payment Complete")]
    assert _grants(late_on) == []


# ---------------------------------------------------------------------------
# 5. A late decline and a late REJECT write nothing, and drop the offer
# ---------------------------------------------------------------------------

def test_a_late_decline_writes_nothing_and_drops_the_offer(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-decline"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    assert _offers(late_on)
    res = _answer_client(monkeypatch, _ALICE, _ORG_A).post("/agent/respond-input", json={
        "request_id": rid, "answer": "", "thread_id": tid, "declined": True,
    })
    assert res.json() == {"ok": True, "declined": True}
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "closed"
    assert late_on.data == {}, "the offer goes with its row"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    assert WRITES == []


def test_a_late_reject_writes_nothing_and_its_message_is_as_before(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-reject"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    res = _approve_late(monkeypatch, tid, rid, answer="REJECT")
    assert res.json()["detail"]["resumeMessage"] == _envelope("Move this deal to Won?", "REJECT")
    assert late_on.data == {}, "the offer goes with its row"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    assert WRITES == []


def test_an_answer_during_the_park_drops_the_offer(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """The member answers while the offer is written. The answer reaches the
    live run, the row leaves ``parked``, and the offer goes."""
    real = late_approve.record_offer

    async def _answer_during(org, thread_id, request_id, row, cap_s):
        written = await real(org, thread_id, request_id, row, cap_s)
        executor._pending_user_input[request_id].set_result({"answer": "APPROVE"})
        return written

    monkeypatch.setattr(late_approve, "record_offer", _answer_during)
    tid = "t-hrd22-race"
    out = _run(liveness, tid, deal_id="d-neuro", stage="Won")
    assert "parked" not in out and WRITES == [("deal", "d-neuro", "Won")]
    rid = _cards(liveness, tid)[0]["request_id"]
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "answered"
    assert _offers(late_on) == [] and late_on.keys(late_approve.ASK_NAMESPACE) == []


# ---------------------------------------------------------------------------
# 6. A grant never crosses a thread, a member or an org
# ---------------------------------------------------------------------------

def test_a_grant_never_crosses_a_thread_a_member_or_an_org(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-cross"
    _park_and_approve(liveness, monkeypatch, tid)
    call = {"deal_id": "d-neuro", "stage": "Payment Complete"}

    # Another thread of the same member.
    assert _run(liveness, "t-hrd22-cross-other", **call).get("parked")
    # Another member of the same org, in the same thread.
    assert _run(liveness, tid, member=_BOB, **call).get("parked")
    # Another org, with the same member and the same thread.
    assert _run(liveness, tid, org=_ORG_B, **call).get("parked")
    # The same member with no verified binding.
    assert _run(liveness, tid, verified=False, **call).get("parked")
    assert WRITES == []
    # None of them read the grant, so its own run still takes it, once.
    assert len(_grants(late_on)) == 1
    assert "parked" not in _run(liveness, tid, **call)
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_another_member_cannot_answer_and_so_writes_no_grant(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-bob"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    res = _approve_late(monkeypatch, tid, rid, member=_BOB)
    assert res.status_code == 409 and isinstance(res.json()["detail"], str)
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "parked"
    assert _grants(late_on) == []


# ---------------------------------------------------------------------------
# 7. The cards that never take a grant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", ["rows", "bulk", "send", "no_key", "cut_mark",
                                  "long_context", "long_title"])
def test_a_card_that_may_not_skip_records_no_offer(
    case, late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = f"t-hrd22-plain-{case}"
    if case == "rows":
        # A real item, so the call HAS an act key of a listed write
        # (``update_task``). Only the rows keep it from an offer.
        changes = json.dumps([{"task_id": "t-1", "title": "Ship it"}])
        assert late_approve.keys_wire(card_call_keys(update_tasks, changes=changes))
        out = _run(liveness, tid, update_tasks, changes=changes)
    elif case == "bulk":
        # Its keys read as the listed family ``update_task``. The tool is not.
        assert late_approve.keys_wire(card_call_keys(bulk_update, task_ids="t-1 t-2",
                                                     status="Done"))
        out = _run(liveness, tid, bulk_update, task_ids="t-1 t-2", status="Done")
    elif case == "send":
        out = _run(liveness, tid, send_email, to="x@acme.test", subject="Hi")
    elif case == "no_key":
        out = _run(liveness, tid, keyed=False, deal_id="d-neuro", stage="Won")
    elif case == "long_title":
        # The stage is in the title, so the title loses text to the clip.
        out = _run(liveness, tid, deal_id="d-neuro", stage="W" * 130)
    else:
        from acb_common.card_body import CUT_MARK

        DEALS["d-neuro"] = {
            "cut_mark": f"Neuropeak {CUT_MARK}",
            "long_context": "N" * 4100,
        }[case]
        out = _run(liveness, tid, deal_id="d-neuro", stage="Won")
    assert out.get("parked")
    assert _offers(late_on) == [], "no offer, so no late approve can grant"
    rid = _cards(liveness, tid)[0]["request_id"]
    assert _approve_late(monkeypatch, tid, rid).status_code == 409
    assert _grants(late_on) == []


def test_bulk_update_never_takes_a_grant_even_with_one_in_place(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """A grant of the same digest that some writer put in place still does
    not let ``bulk_update`` skip its card: the real tool fails the tests."""
    from acb_common.tenant_redis import key, organization_scope

    tid = "t-hrd22-bulk-grant"
    keys = late_approve.keys_wire(card_call_keys(bulk_update, task_ids="t-1", status="Done"))
    text = late_approve.card_text_of("Update these tasks?", "t-1 → Done", "")
    with organization_scope(_ORG_A):
        k = key(late_approve.GRANT_NAMESPACE, _ALICE, tid, late_approve.digest_of(text, keys))
    late_on.data[k.value] = late_approve._encode("a" * 32, text, keys)
    assert _run(liveness, tid, bulk_update, task_ids="t-1", status="Done").get("parked")
    assert WRITES == []


def test_only_a_parked_plain_confirmation_row_turns_its_offer_into_a_grant(late_on):
    """The offer waits, and only the right row and the right answer take it.
    Each refusal before the approve leaves the records of Alice in place."""
    from acb_common.tenant_redis import key, organization_scope

    rid, tid = "a" * 32, "t1"
    deal = ("tests.unit.test_late_approve_grant", "update_deal_status", (("deal_id", "d1"),))
    keys = late_approve.keys_wire(frozenset({deal}))
    digest = late_approve.digest_of("card", keys)
    with organization_scope(_ORG_A):
        ask = key(late_approve.ASK_NAMESPACE, _ALICE, tid, rid).value
        offer = key(late_approve.OFFER_NAMESPACE, _ALICE, tid, digest).value
        grant = key(late_approve.GRANT_NAMESPACE, _ALICE, tid, digest).value
    row = {"kind": "confirmation", "state": "parked", "thread_id": tid,
           "request_id": rid, "payload": {}}

    def seed() -> None:
        late_on.data.update({ask: digest, offer: late_approve._encode(rid, "card", keys)})

    async def go(answer: str = "APPROVE", member: str = _ALICE, **change: Any) -> bool:
        return await late_approve.grant_from_offer(_ORG_A, member, {**row, **change}, answer)

    seed()
    for change in ({"state": "open"}, {"state": "answered"}, {"kind": "questions"},
                   {"payload": {"rows": [{"id": "r1"}]}}, {"payload": {"reply": {"kind": "new"}}}):
        assert not asyncio.run(go(**change)), change
        assert sorted(late_on.data) == sorted([ask, offer]), change
    assert not asyncio.run(go(member=_BOB))
    assert sorted(late_on.data) == sorted([ask, offer]), "Bob reads no record of Alice"
    assert asyncio.run(go(answer=" approve "))
    assert list(late_on.data) == [grant]
    # A second late answer finds no ask record, so it writes nothing.
    late_on.data.clear()
    assert not asyncio.run(go())
    assert late_on.data == {}
    # A REJECT drops the offer.
    seed()
    assert not asyncio.run(go(answer="REJECT"))
    assert late_on.data == {}


def test_only_a_first_party_write_that_stays_in_metorite_has_keys():
    assert late_approve.keys_wire(None) is None
    assert late_approve.keys_wire(frozenset()) is None
    mail = ("acb_skills.email_tools", "send_email", (("to", ("x@acme.test",)),))
    deal = ("_tool_pack_owner_crm_assistant", "update_deal_status", (("deal_id", "d1"),))
    assert late_approve.keys_wire(frozenset({mail})) is None
    assert late_approve.keys_wire(frozenset({deal, mail})) is None, "one bad key spoils all"
    assert late_approve.keys_wire(frozenset({deal})) is not None


def test_the_real_tool_passes_the_three_tests_of_a_card_write():
    from acb_skills.egress import is_card_write_tool

    assert is_card_write_tool(update_deal_status)
    assert not is_card_write_tool(bulk_update), "not on the list"
    assert not is_card_write_tool(send_email)

    async def update_deal_status_unmarked(deal_id: str, stage: str,
                                          lost_reason: str | None = None) -> str:
        return ""

    update_deal_status_unmarked.__name__ = "update_deal_status"
    assert not is_card_write_tool(update_deal_status_unmarked), "no annotation"

    async def wider(deal_id: str, stage: str, lost_reason: str | None = None,
                    owner: str = "") -> str:
        return ""

    wider.__name__ = "update_deal_status"
    wider.__tool_risk__ = dict(_CARD_WRITE)  # type: ignore[attr-defined]
    assert not is_card_write_tool(wider), "a parameter outside the pin"


def test_a_per_run_agent_module_reads_as_the_run_agent():
    """``acb_skills.loader`` names an agent module for ONE run, so its key is
    read as the run's agent. With no agent bound, it gives no key."""
    one = ("_agent_1f2e3d4c_aaaa_agents", "update_deal_status", (("deal_id", "d1"),))
    two = ("_agent_9a8b7c6d_bbbb_agents", "update_deal_status", (("deal_id", "d1"),))
    assert late_approve.keys_wire(frozenset({one}), "crm-assistant") == \
        late_approve.keys_wire(frozenset({two}), "crm-assistant")
    assert late_approve.keys_wire(frozenset({one}), "crm-assistant") != \
        late_approve.keys_wire(frozenset({two}), "orchestrator")
    assert late_approve.keys_wire(frozenset({one}), "") is None


def test_the_real_crm_tool_of_two_runs_writes_once(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Review F8: the real ``agent-crm`` ``update_deal_status``, loaded for
    each run under the per-run module name of ``acb_skills.loader``. It parks,
    the member approves late, and the next run's copy writes once."""
    from tests.unit._crm_agent_fakes import fake_gateway, load_agent_module, writes

    deal_id, won = str(uuid.uuid4()), str(uuid.uuid4())

    def responder(call: dict) -> Any:
        if call["path"] == "/crm/statuses/deal":
            return [{"id": won, "name": "Payment Complete", "type": "won"}]
        if call["path"] == f"/crm/deals/{deal_id}":
            return {"id": deal_id, "name": "Neuropeak", **(call["json"] or {})}
        raise AssertionError(f"an unexpected route: {call['method']} {call['path']}")

    runs = [load_agent_module(f"_agent_{uuid.uuid4().hex}_agents") for _ in range(2)]
    calls = [fake_gateway(m, monkeypatch, responder, user=_ALICE) for m in runs]
    assert runs[0].update_deal_status.__module__ != runs[1].update_deal_status.__module__
    tid = "t-hrd22-real-crm"
    assert _run(liveness, tid, runs[0].update_deal_status, agent="crm-assistant",
                deal_id=deal_id, stage="Payment Complete").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    assert _approve_late(monkeypatch, tid, rid).status_code == 409
    out = _run(liveness, tid, runs[1].update_deal_status, agent="crm-assistant",
               deal_id=deal_id, stage="Payment Complete")
    assert "parked" not in out, out
    assert len(_cards(liveness, tid)) == 1
    assert writes(calls[0]) == []
    assert [(c["method"], c["path"]) for c in writes(calls[1])] == [
        ("PATCH", f"/crm/deals/{deal_id}")]


# ---------------------------------------------------------------------------
# 8. Off is today
# ---------------------------------------------------------------------------

def test_with_the_flag_off_nothing_is_read_or_written(
    flag_on, store, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-off"
    assert "parked" not in _run(liveness, tid, live="APPROVE", deal_id="d-neuro", stage="Won")
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    rid = _cards(liveness, tid)[-1]["request_id"]
    res = _approve_late(monkeypatch, tid, rid)
    assert res.json()["detail"]["resumeMessage"] == _envelope("Move this deal to Won?")
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    res = _answer_client(monkeypatch, _ALICE, _ORG_A).post("/agent/respond-input", json={
        "request_id": _cards(liveness, tid)[-1]["request_id"], "answer": "",
        "thread_id": tid, "declined": True})
    assert res.json() == {"ok": True, "declined": True}
    assert WRITES == [("deal", "d-neuro", "Won")]
    assert store.calls == [], "off reads and writes no Redis"


def test_another_org_off_the_list_gets_nothing(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", _ORG_B)
    tid = "t-hrd22-org-off"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    assert late_on.data == {}
    assert late_approve.late_approve_on(_ORG_B) and not late_approve.late_approve_on(_ORG_A)


# ---------------------------------------------------------------------------
# 9. A Redis fault shows the normal card
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("where", ["offer", "grant", "use"])
def test_a_redis_fault_shows_the_normal_card(
    where, late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = f"t-hrd22-fault-{where}"
    if where == "offer":
        late_on.fail = ConnectionError("redis down")
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    if where == "grant":
        late_on.fail = ConnectionError("redis down")
    res = _approve_late(monkeypatch, tid, rid)
    assert res.status_code == 409
    message = res.json()["detail"]["resumeMessage"]
    if where == "use":
        assert message != _envelope("Move this deal to Won?"), "the grant was written"
    else:
        assert message == _envelope("Move this deal to Won?"), "no grant, so no paragraph"
    late_on.fail = ConnectionError("redis down") if where == "use" else None
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    assert WRITES == []


# ---------------------------------------------------------------------------
# 10. The new message reads as an approve
# ---------------------------------------------------------------------------

def test_the_new_message_reads_as_an_approve_with_its_display():
    from gateway.chat_recovery import (
        LATE_APPROVE_NOTE,
        card_answer_marker,
        card_resend,
        with_late_approve_note,
    )

    plain = card_resend("confirmation", {"title": "Move this deal to Won?"},
                        "Move this deal to Won?", "APPROVE", uuid.uuid4().hex)
    noted = with_late_approve_note(plain)
    assert noted.message == plain.message + "\n\n" + LATE_APPROVE_NOTE
    assert noted.display == plain.display
    marker = card_answer_marker(noted.message, noted.display)
    assert marker is not None and marker["value"]["decision"] == "approve"
    # The paragraph is outside the quoted block: the member's own words.
    assert noted.message.index("<<<end-asked-question>>>") < noted.message.index(LATE_APPROVE_NOTE)
    assert "If this call already ran in this chat, do not run it again." in LATE_APPROVE_NOTE


# ---------------------------------------------------------------------------
# 11. The places of the write and the use
# ---------------------------------------------------------------------------

def test_the_grant_is_written_only_after_the_row_moves():
    """The grant write sits on the one path where the member's own late
    answer moved the row to ``answered``, after the CRM hook."""
    from gateway.routes import agent as agent_routes

    src = inspect.getsource(agent_routes._late_answer_from_row)
    moved = src.index("    if moved is None:\n        return None, True\n")
    crm = src.index("await record_late_answer(org, me, ")
    call = src.index("await grant_from_offer(org, me, row, req.answer)")
    assert moved < crm < call < src.index('"agent.pending_ask_answered_late"')
    assert src.count("grant_from_offer(") == 1
    decline = inspect.getsource(agent_routes._late_decline_from_row)
    assert decline.index("if moved is None:") < decline.index("await drop_offer(")


def test_each_record_has_one_writer_and_one_reader():
    """``grant_from_offer`` has one caller (the late answer route),
    ``record_offer`` one (the park), ``take_grant`` and ``note_approved`` one
    each (the card gate), and ``drop_offer`` the park and the decline. No
    other code names a namespace of the grant. RESUME-2a: the stored-call yes
    (``stored_call.resume_answer``) marks its act SPENT too."""
    names = ("grant_from_offer(", "record_offer(", "take_grant(", "note_approved(",
             "drop_offer(", "chat-late-approve", "chat-late-offer", "chat-late-ask",
             "GRANT_NAMESPACE")
    callers: dict[str, set[str]] = {n: set() for n in names}
    for top in ("apps", "packages"):
        for path in (_ROOT / top).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in callers:
                if needle in text:
                    callers[needle].add(path.relative_to(_ROOT).as_posix())
    own = "packages/acb_skills/acb_skills/late_approve.py"
    agent = "apps/services/gateway/gateway/routes/agent.py"
    park = "apps/services/orchestrator/orchestrator/pending_ask.py"
    gate = "packages/acb_skills/acb_skills/ask_tools.py"
    stored = "packages/acb_skills/acb_skills/stored_call.py"
    assert callers["grant_from_offer("] == {own, agent}
    assert callers["record_offer("] == {own, park}
    assert callers["take_grant("] == {own, gate}
    assert callers["note_approved("] == {own, gate, stored}
    assert callers["drop_offer("] == {own, park, agent}
    for name in names[5:]:
        assert callers[name] == {own}, (name, callers[name])


def test_the_use_comes_before_the_card_and_reads_once():
    from acb_skills import ask_tools

    src = inspect.getsource(ask_tools.request_confirmation)
    use = src.index("await _late_approved(_card)")
    assert src.index("if cards_refused():") < use < src.index("_member_waits()")
    assert use < src.index("def _event(")
    spent = src.index("await _late_approve_spent(_card)")
    assert spent < src.index('return _answer == "APPROVE"', spent)
    take = inspect.getsource(late_approve.take_grant)
    assert "redis.getdel(\n                key(GRANT_NAMESPACE, member, thread, act.digest))" in take
    assert ".get(" not in take.replace(".getdel(", ""), "a GET would leave the grant for a replay"
