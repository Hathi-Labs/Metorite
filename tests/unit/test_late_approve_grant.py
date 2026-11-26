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
   RESUME-2a follow-up, P2-1: a drop never removes a SPENT mark that a twin's
   claim writes in its window. Put back the GET and then the DELETE in
   ``drop_offer``, and the mark goes.
5. A late decline and a late REJECT write nothing and open nothing, and drop
   the offer. So does an answer that came during the park.
6. A grant never crosses a thread, a member or an org.
7. The cards that never take a grant: rows, ``bulk_update`` (it keys as
   ``update_task``), a send, a call with no act key, and a cut card. Test the
   family head and not the real tool, and ``bulk_update`` takes one.
8. Off is today: with this flag and ``CHAT_RESUME_ORGS`` off, nothing is
   read or written, and the message is the same, byte for byte.
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
                  get: bool = False, **_kw: Any) -> Any:
        self._check("set", k)
        old = self.data.get(k.value)
        if nx and k.value in self.data:
            return old if get else None
        self.data[k.value] = value
        self.ttl[k.value] = ex
        # ``SET … GET`` (Redis 6.2): the old value, or None.
        return old if get else True

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

    async def delete_unless(self, k: Any, value: str) -> int:
        """``TenantRedis.delete_unless``: ONE step, as the Lua script."""
        self._check("delete_unless", k)
        if k.value not in self.data or self.data[k.value] == value:
            return 0
        self.ttl.pop(k.value, None)
        del self.data[k.value]
        return 1

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


@pytest.fixture(autouse=True)
def _twin_witness(ask_db, monkeypatch):
    """The in-memory table has the second witness of HRD-22b
    (``pending_ask.twin_approved_since``). A row is stamped with the order in
    which it was asked and answered, and the witness reads that order, as the
    SQL reads ``asked_at`` and ``answered_at``. The SQL itself is
    ``test_late_approve_twin_r8.py``."""
    seq = iter(range(1, 10_000))
    insert, move = ask_db.insert_ask, ask_db.move_ask

    def insert_ask(org, row):
        ok = insert(org, row)
        if ok:
            ask_db.rows[(org, row["request_id"])]["_asked"] = next(seq)
        return ok

    def move_ask(org, rid, *, to, from_states, answer=None):
        out = move(org, rid, to=to, from_states=from_states, answer=answer)
        if out is not None and to == "answered":
            ask_db.rows[(org, rid)]["_answered"] = next(seq)
        return out

    def twin_approved_since(org, member, row):
        card = (row.get("payload") or {})
        if not card.get("title") or row.get("_asked") is None:
            return True
        for (o, rid), other in ask_db.rows.items():
            other_card = other.get("payload") or {}
            if (o == org and rid != row["request_id"] and other["actor_email"] == member
                    and other["kind"] == "confirmation" and other["state"] == "answered"
                    and str(other.get("answer") or "").strip().upper() == "APPROVE"
                    and other.get("_answered", 0) > row["_asked"]
                    and all(str(other_card.get(k) or "") == str(card.get(k) or "")
                            for k in ("title", "detail", "context"))):
                return True
        return False

    monkeypatch.setattr(pending_ask, "insert_ask", insert_ask)
    monkeypatch.setattr(pending_ask, "move_ask", move_ask)
    monkeypatch.setattr(pending_ask, "twin_approved_since", twin_approved_since)


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


def test_the_spent_mark_outlives_each_waiting_row(monkeypatch):
    """RESUME-2a review, P1-A (c). The SPENT mark lives as long as a row
    (``chat_ask_ttl_hours``), so the stored call of a twin row that waits a
    week still finds it. The cost: an identical act in the same thread within
    that week shows one more card. Mutation: drop the hours term of
    ``spent_ttl_s`` -> 1800 s -> red."""
    from acb_common import get_settings

    assert late_approve.spent_ttl_s() == max(
        late_approve.GRANT_TTL_S, pending_ask._ttl_hours() * 3600)
    monkeypatch.setattr(get_settings(), "chat_ask_ttl_hours", 168)
    monkeypatch.setattr(get_settings(), "chat_ask_park_seconds", 600)
    assert late_approve.spent_ttl_s() == 168 * 3600
    monkeypatch.setattr(get_settings(), "chat_ask_ttl_hours", 1)
    monkeypatch.setattr(get_settings(), "chat_ask_park_seconds", 7200)
    assert late_approve.spent_ttl_s() == 7500, "the park window still counts"


def test_the_spent_mark_is_one_set_get_and_one_writer_wins():
    """RESUME-2a review, P1-A (d). ``_spend`` writes the mark and reads the
    old value in ONE ``SET … GET``: the first writer reads no mark, the second
    reads SPENT. ``take_grant`` and ``claim_act`` refuse on SPENT. Mutation:
    a GET, then a SET -> the source test below fails."""
    from acb_common.tenant_redis import organization_scope

    store = _Store()

    async def go() -> tuple[bool, bool]:
        with organization_scope(_ORG_A):
            first = await late_approve._spend(store, _ALICE, "t-spend", "d" * 32)
            second = await late_approve._spend(store, _ALICE, "t-spend", "d" * 32)
        return first, second

    assert asyncio.run(go()) == (False, True)
    # The offer key, the member-level key (HRD-22b), then the grant.
    assert [c.split(" ")[0] for c in store.calls] == [
        "set", "set", "delete", "set", "set", "delete"]
    src = inspect.getsource(late_approve._spend)
    assert "get=True" in src and ".get(" not in src
    assert "if await _spend(" in inspect.getsource(late_approve.take_grant)
    assert "spent = await _spend(" in inspect.getsource(late_approve.claim_act)
    # P2-1: the drop never reads the offer and then deletes it.
    drop = inspect.getsource(late_approve.drop_offer)
    assert "redis.delete_unless(" in drop and "redis.get(" not in drop


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


class _Window(_Store):
    """The fake Redis, with ONE window on the offer key *okey*: a twin's
    claim lands after the drop read the key (a GET), or just before its one
    atomic step (``delete_unless``). Both are the worst order."""

    def __init__(self, okey: str) -> None:
        super().__init__()
        self.okey = okey
        self.hook: Any = None
        self.fired = 0

    async def _twin(self, k: Any) -> None:
        if self.hook is not None and k.value == self.okey:
            hook, self.hook = self.hook, None
            self.fired += 1
            await hook()

    async def get(self, k: Any) -> str | None:
        out = await super().get(k)
        await self._twin(k)
        return out

    async def delete_unless(self, k: Any, value: str) -> int:
        await self._twin(k)
        return await super().delete_unless(k, value)


def test_a_drop_never_removes_a_mark_that_a_twin_claim_writes_in_its_window(monkeypatch):
    """RESUME-2a follow-up, P2-1. Twin rows A and B of one act. B's
    ``_resume_from_row`` drops its offer while the offer is still there. A's
    continuation runs ``claim_act`` in the window of that drop and writes
    SPENT. After the drop the mark is STILL there, so B's claim reads SPENT
    and the act writes once. Mutation: put back the GET and then the DELETE
    in ``drop_offer`` -> the DELETE removes the mark -> red."""
    from acb_common import get_settings
    from acb_common.tenant_redis import key, organization_scope

    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", _ORG_A)
    tid, digest = "t-p21-window", "e" * 32
    with organization_scope(_ORG_A):
        okey = key(late_approve.OFFER_NAMESPACE, _ALICE, tid, digest)
        akey = key(late_approve.ASK_NAMESPACE, _ALICE, tid, "rid-b")
    store = _Window(okey.value)
    store.data[okey.value] = late_approve._encode("rid-a", "Move it?", ['["m","update_deal_status",[]]'])
    store.data[akey.value] = digest
    monkeypatch.setattr(late_approve, "_redis", lambda: store)

    async def _a_claims() -> None:
        # What A's `claim_act` does: ONE `SET … GET` of the mark.
        assert await late_approve._spend(store, _ALICE, tid, digest) is False

    async def go() -> bool:
        store.hook = _a_claims
        await late_approve.drop_offer(_ORG_A, _ALICE, tid, "rid-b")
        with organization_scope(_ORG_A):
            # B's claim, after the drop.
            return await late_approve._spend(store, _ALICE, tid, digest)

    b_spent = asyncio.run(go())
    assert store.fired == 1, "non-vacuity: A's claim landed in the window of the drop"
    assert store.data[okey.value] == late_approve._SPENT, "the drop kept the mark"
    assert b_spent is True, "B reads SPENT, so the act writes once"
    assert akey.value not in store.data, "B's own ask record goes"
    # With no mark, the drop still removes a plain offer.
    store.data[okey.value] = late_approve._encode("rid-a", "Move it?", ['["m","x",[]]'])
    store.data[akey.value] = digest
    asyncio.run(late_approve.drop_offer(_ORG_A, _ALICE, tid, "rid-b"))
    assert okey.value not in store.data


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
    # A comparable card, for the second witness of HRD-22b (``_asked`` is the
    # stand-in of ``asked_at`` in the in-memory table).
    row = {"kind": "confirmation", "state": "parked", "thread_id": tid,
           "request_id": rid, "payload": {"title": "card"}, "_asked": 1}

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
    """``CHAT_LATE_APPROVE_ORGS`` AND ``CHAT_RESUME_ORGS`` off: no Redis call
    at all, so each card and each message is byte for byte as before. Since
    P2-2 either flag writes the SPENT mark, so both must be off here."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", "")
    monkeypatch.setattr(get_settings(), "chat_resume_orgs", "")
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
    (``stored_call.resume_answer``) takes its act with ``claim_act``, the one
    atomic mark (review, P1-A)."""
    names = ("grant_from_offer(", "record_offer(", "take_grant(", "note_approved(",
             "claim_act(", "drop_offer(", "chat-late-approve", "chat-late-offer",
             "chat-late-ask", "GRANT_NAMESPACE")
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
    assert callers["note_approved("] == {own, gate}
    assert callers["claim_act("] == {own, stored}
    assert callers["drop_offer("] == {own, park, agent}
    for name in names[6:]:
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


# ---------------------------------------------------------------------------
# HRD-22b (task 3294): one act, once, across chats, a Redis fault and the race
# ---------------------------------------------------------------------------

_CALL = {"deal_id": "d-neuro", "stage": "Payment Complete"}


def test_an_approve_in_another_chat_stops_the_stale_twin(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Item 1 (reviewer P2). C1 parks in chat A. The member approves its twin
    live in chat B (write 1). A late tap on C1 writes no grant, and the new
    run in chat A asks again. Mutation: drop the member-level mark -> the
    grant is written and the act runs twice."""
    assert _run(liveness, "t-hrd22b-a", **_CALL).get("parked")
    c1 = _cards(liveness, "t-hrd22b-a")[0]["request_id"]
    out = _run(liveness, "t-hrd22b-b", live="APPROVE", **_CALL)
    assert "parked" not in out and WRITES == [("deal", "d-neuro", "Payment Complete")]
    assert late_on.keys(late_approve.SPENT_NAMESPACE), "the member-level mark is written"

    res = _approve_late(monkeypatch, "t-hrd22b-a", c1)
    assert res.status_code == 409
    assert res.json()["detail"]["resumeMessage"] == _envelope(
        "Move this deal to Payment Complete?"), "no grant, so no paragraph"
    assert _grants(late_on) == []
    assert _run(liveness, "t-hrd22b-a", **_CALL).get("parked"), "chat A asks again"
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_the_member_level_mark_is_keyed_by_member_and_digest_only(late_on):
    """It has no thread and no org of the other member in its key."""
    async def go():
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(_ORG_A):
            await late_approve._spend(late_on, _ALICE, "t-1", "d" * 32)
            await late_approve._spend(late_on, _BOB, "t-1", "e" * 32)
            return (key(late_approve.SPENT_NAMESPACE, _ALICE, "d" * 32).value,
                    key(late_approve.SPENT_NAMESPACE, _BOB, "e" * 32).value)

    mine, theirs = asyncio.run(go())
    assert late_on.data[mine] == late_on.data[theirs] == late_approve._SPENT
    assert "t-1" not in mine and mine.endswith(":" + "d" * 32)
    assert late_on.ttl[mine] == late_approve.spent_ttl_s()


def test_a_redis_fault_during_the_live_approve_leaves_no_second_run(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Item 2 (reviewer P2). The SPENT mark is best effort: here the live
    approve of C2 writes none (a stall of Redis). The row of C2 is in Postgres,
    answered APPROVE, so the late tap on C1 still writes no grant. Mutation:
    drop the Postgres read -> the stale offer is valid and the act runs twice."""
    assert _run(liveness, "t-hrd22b-f", **_CALL).get("parked")
    c1 = _cards(liveness, "t-hrd22b-f")[0]["request_id"]

    async def stalled(*_a: Any, **_k: Any) -> bool:
        raise TimeoutError

    monkeypatch.setattr(late_approve, "_spend", stalled)
    out = _run(liveness, "t-hrd22b-f2", live="APPROVE", **_CALL)
    assert "parked" not in out and WRITES == [("deal", "d-neuro", "Payment Complete")]
    monkeypatch.undo()  # Redis is back, and no mark was ever written
    assert late_on.keys(late_approve.SPENT_NAMESPACE) == []
    assert _offers(late_on), "the stale offer is still valid in Redis"

    res = _approve_late(monkeypatch, "t-hrd22b-f", c1)
    assert res.status_code == 409
    assert _grants(late_on) == [], "Postgres says the act ran"
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_a_mark_that_expired_and_a_new_twin_cannot_serve_the_stale_tap(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Item 3 (verifier F1, probe test_p1e2: writes = 2). C1 parks. C2 is
    approved live (write 1). The marks expire. A new twin C3 parks and writes a
    fresh offer. The late tap on the STALE C1 takes that offer by digest. The
    Postgres read refuses: C2 was answered APPROVE after C1 was asked."""
    assert _run(liveness, "t-hrd22b-x", **_CALL).get("parked")
    c1 = _cards(liveness, "t-hrd22b-x")[0]["request_id"]
    _run(liveness, "t-hrd22b-x", live="APPROVE", **_CALL)
    for k in list(late_on.data):
        if late_on.data[k] == late_approve._SPENT:
            del late_on.data[k]
    assert _run(liveness, "t-hrd22b-x", **_CALL).get("parked"), "C3 parks"
    assert _offers(late_on), "C3 wrote a fresh offer"

    res = _approve_late(monkeypatch, "t-hrd22b-x", c1)
    assert res.status_code == 409
    assert _grants(late_on) == []
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_a_live_approve_between_the_grant_and_its_use_makes_the_use_refuse(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Item 4 (reviewer P3, verifier F3). The grant is written. Before its use,
    the member approves a twin live in another chat. The use finds SPENT and
    shows the card. Mutation: drop the SPENT read of the use -> a second write."""
    _park_and_approve(liveness, monkeypatch, "t-hrd22b-r", **_CALL)
    assert len(_grants(late_on)) == 1
    out = _run(liveness, "t-hrd22b-r2", live="APPROVE", **_CALL)
    assert "parked" not in out and WRITES == [("deal", "d-neuro", "Payment Complete")]
    # The new run in the original chat takes the grant: the act is SPENT.
    again = _run(liveness, "t-hrd22b-r", **_CALL)
    assert again.get("parked"), "the card shows"
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]
    assert _grants(late_on) == []


def test_an_unreadable_postgres_refuses_the_grant(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Fail closed: a fault of the second witness gives no grant."""
    def boom(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("db down")

    assert _run(liveness, "t-hrd22b-d", **_CALL).get("parked")
    rid = _cards(liveness, "t-hrd22b-d")[0]["request_id"]
    monkeypatch.setattr(pending_ask, "twin_approved_since", boom)
    res = _approve_late(monkeypatch, "t-hrd22b-d", rid)
    assert res.status_code == 409
    assert _grants(late_on) == []


def test_a_repeat_of_the_act_after_it_ran_shows_one_more_card_and_no_more(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """The documented cost of the safe side: after the act ran, the member
    repeats it on purpose. Its late approve shows one more card."""
    _run(liveness, "t-hrd22b-c", live="APPROVE", **_CALL)
    assert _run(liveness, "t-hrd22b-c", **_CALL).get("parked")
    rid = _cards(liveness, "t-hrd22b-c")[-1]["request_id"]
    res = _approve_late(monkeypatch, "t-hrd22b-c", rid)
    assert res.status_code == 409 and _grants(late_on) == []


def test_a_card_that_the_guard_refuses_leaves_no_facts_for_the_next_card():
    """Item 5 (verifier F2). ``card_facts`` binds the ids of the next card. A
    card that ``_check_card`` refuses never takes them, so they were stale.
    Mutation: drop the clear in ``_confirm`` -> the facts stay."""
    skill = pytest.importorskip("skill_projects.writes")
    from acb_common.tool_refusals import GatewayRefusal
    from acb_skills import whatsapp_acts

    async def go() -> tuple[Any, Any]:
        # One task, one context: the tool binds the facts, then the card.
        skill._bind_card_facts(task_id="t-1")
        with pytest.raises(GatewayRefusal):
            await skill._confirm("Change it?", "d", "x" * (skill.CARD_CONTEXT_LIMIT + 5))
        after_refusal = whatsapp_acts._NEXT_FACTS.get()
        skill._bind_card_facts(task_id="t-2")  # a card that goes out still takes facts
        return after_refusal, whatsapp_acts.take_card_facts()

    after_refusal, taken = asyncio.run(go())
    assert after_refusal is None
    assert taken == {"task_id": "t-2"}


def test_the_second_witness_is_read_before_the_offer_is_taken():
    src = inspect.getsource(late_approve.grant_from_offer)
    assert src.index("_twin_approved(") < src.index("redis.getdel(okey)")
    assert src.index("SPENT_NAMESPACE") < src.index("redis.getdel(okey)")


def test_the_member_level_mark_alone_stops_the_stale_twin(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    """Item 1, by behaviour: with the Postgres witness stubbed to False, the
    member-level mark still refuses the grant. Mutation: drop the read of
    ``SPENT_NAMESPACE`` in ``grant_from_offer`` -> a grant is written."""
    assert _run(liveness, "t-hrd22b-m1", **_CALL).get("parked")
    c1 = _cards(liveness, "t-hrd22b-m1")[0]["request_id"]
    _run(liveness, "t-hrd22b-m2", live="APPROVE", **_CALL)
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]
    monkeypatch.setattr(pending_ask, "twin_approved_since", lambda *_a, **_k: False)
    res = _approve_late(monkeypatch, "t-hrd22b-m1", c1)
    assert res.status_code == 409
    assert _grants(late_on) == []
