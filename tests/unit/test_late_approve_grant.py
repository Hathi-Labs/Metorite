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
2. A second use finds nothing. Read the grant with GET and the third run
   writes a second time.
3. A changed card (another stage, another deal, a renamed deal) gets a normal
   card. Compare the keys only, or the text only, and one case writes.
4. A late decline and a late REJECT write nothing and open nothing.
5. A grant never crosses a thread, a member or an org. Key the grant by the
   thread alone and the cross tries write.
6. The cards that never take a grant: rows, a tool that is not a first-party
   write that stays in Metorite, and a call with no act key.
7. Off is today: with the flag off, nothing is read or written, and the
   message is the same, byte for byte.
8. A Redis fault shows the normal card.
9. The new message reads as an approve, and only a grant adds its paragraph.
10. The places: the grant write sits after the move of the row, and the use
    sits before the card. Each has one caller.

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
_CAROL = "carol@ask-b.test"

#: The deals of the stand-in CRM: id to title. A test renames one.
DEALS: dict[str, str] = {}
#: Each write that a stand-in tool made, in order.
WRITES: list[tuple[str, ...]] = []


async def update_deal_status(deal_id: str, stage: str) -> str:
    """A stand-in of ``agent-crm`` ``update_deal_status``: one card, then one
    write on approve. The name is a write of ``egress.HELD_CARD_WRITE_TOOLS``."""
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


class _Store:
    """The tenant Redis, by wire key. ``fail`` makes each call raise."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}
        self.calls: list[str] = []
        self.fail: BaseException | None = None

    async def set(self, k: Any, value: str, ex: int | None = None, **_kw: Any) -> bool:
        self.calls.append(f"set {k.value}")
        if self.fail is not None:
            raise self.fail
        self.data[k.value] = value
        self.ttl[k.value] = ex
        return True

    async def getdel(self, k: Any) -> str | None:
        self.calls.append(f"getdel {k.value}")
        if self.fail is not None:
            raise self.fail
        self.ttl.pop(k.value, None)
        return self.data.pop(k.value, None)


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(late_approve, "_redis", lambda: s)
    DEALS.clear()
    DEALS.update({"d-neuro": "Neuropeak", "d-orbit": "Orbit"})
    WRITES.clear()
    return s


@pytest.fixture
def late_on(monkeypatch, flag_on, store):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", f"{_ORG_A},{_ORG_B}")
    return store


def _run(r, tid: str, fn: Any = update_deal_status, *, member: str = _ALICE,
         org: str = _ORG_A, verified: bool = True, keyed: bool = True,
         **kwargs: Any) -> dict[str, Any]:
    """One run on *tid* that calls *fn* once. ``parked`` when its card parked.

    The run binds what a chat run binds: the thread, the tenant, the verified
    member and the agent, and (with *keyed*) the act key of the call."""
    from acb_common import bind_run_context
    from acb_common.db import bind_tenant

    r.store[f"cc:runactor:{tid}"] = member
    out: dict[str, Any] = {}

    async def _the_run() -> None:
        executor._stream_relay_thread_id.set(tid)
        bind_tenant(org)
        bind_run_context(user=member, member_verified=verified, agent="orchestrator",
                         thread_id=tid)
        scope = card_call(fn, (), kwargs) if keyed else contextlib.nullcontext()
        with scope:
            out["result"] = await fn(**kwargs)

    async def go() -> None:
        run = asyncio.create_task(_the_run())
        stream_relay._DETACHED_TASKS[tid] = run
        try:
            # The first park of a process imports the tool modules, so allow time.
            await asyncio.wait_for(run, timeout=60)
        except asyncio.CancelledError:
            out["parked"] = True

    asyncio.run(go())
    return out


def _cards(r, tid: str) -> list[dict[str, Any]]:
    return [e["value"] for e in _events(r, tid) if e.get("name") == "confirmation_requested"]


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


def _grant_key(org: str, member: str, tid: str) -> str:
    return f"cc:{org}:{late_approve.GRANT_NAMESPACE}:{member}:{tid}"


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
    assert _grant_key(_ORG_A, _ALICE, tid) not in late_on.data, "the grant is used up"

    # A second use finds nothing: the next same call asks again.
    assert _run(liveness, tid, deal_id="d-neuro", stage="Payment Complete").get("parked")
    assert len(_cards(liveness, tid)) == 2
    assert WRITES == [("deal", "d-neuro", "Payment Complete")]


def test_the_records_carry_the_tenant_the_member_the_thread_and_a_ttl(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-keys"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    offer = f"cc:{_ORG_A}:{late_approve.OFFER_NAMESPACE}:{_ALICE}:{tid}:{rid}"
    assert list(late_on.data) == [offer]
    assert late_on.ttl[offer] == pending_ask._ttl_hours() * 3600
    record = json.loads(late_on.data[offer])
    assert record["rid"] == rid
    assert record["text"] == late_approve.card_text_of(
        "Move this deal to Won?", "Neuropeak → Won", "deal: Neuropeak\nstage: Won")
    assert [json.loads(k)[1] for k in record["keys"]] == ["update_deal_status"]

    assert _approve_late(monkeypatch, tid, rid).status_code == 409
    grant = _grant_key(_ORG_A, _ALICE, tid)
    assert list(late_on.data) == [grant], "the offer is taken once, and the grant replaces it"
    assert late_on.ttl[grant] == late_approve.GRANT_TTL_S == 30 * 60
    assert json.loads(late_on.data[grant])["rid"] == rid


# ---------------------------------------------------------------------------
# 3. A changed card gets a normal card
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("change", ["stage", "deal", "renamed"])
def test_a_changed_card_gets_a_normal_card(
    change, late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = f"t-hrd22-changed-{change}"
    _park_and_approve(liveness, monkeypatch, tid)
    call = {"deal_id": "d-neuro", "stage": "Payment Complete"}
    if change == "stage":
        call["stage"] = "Lost"
    elif change == "deal":
        call["deal_id"] = "d-orbit"
    else:
        DEALS["d-neuro"] = "Neuropeak Labs"  # the record changed since the card
    assert _run(liveness, tid, **call).get("parked"), "a changed card asks again"
    assert WRITES == []
    assert len(_cards(liveness, tid)) == 2
    # The grant is gone: the card that it named asks again too.
    DEALS["d-neuro"] = "Neuropeak"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Payment Complete").get("parked")
    assert WRITES == []


# ---------------------------------------------------------------------------
# 4. A late decline and a late REJECT write nothing
# ---------------------------------------------------------------------------

def test_a_late_decline_writes_nothing_and_opens_nothing(
    late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-decline"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    res = _answer_client(monkeypatch, _ALICE, _ORG_A).post("/agent/respond-input", json={
        "request_id": rid, "answer": "", "thread_id": tid, "declined": True,
    })
    assert res.json() == {"ok": True, "declined": True}
    assert ask_db.rows[(_ORG_A, rid)]["state"] == "closed"
    assert _grant_key(_ORG_A, _ALICE, tid) not in late_on.data
    assert not [c for c in late_on.calls
                if c.startswith("set ") and late_approve.GRANT_NAMESPACE in c]
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
    assert _grant_key(_ORG_A, _ALICE, tid) not in late_on.data
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    assert WRITES == []


# ---------------------------------------------------------------------------
# 5. A grant never crosses a thread, a member or an org
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
    assert _grant_key(_ORG_A, _ALICE, tid) in late_on.data
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
    assert not [k for k in late_on.data if late_approve.GRANT_NAMESPACE in k]


# ---------------------------------------------------------------------------
# 6. The cards that never take a grant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", ["rows", "send", "no_key"])
def test_a_card_that_is_not_plain_records_no_offer(
    case, late_on, ask_db, liveness, no_persist, monkeypatch,
):
    tid = f"t-hrd22-plain-{case}"
    if case == "rows":
        out = _run(liveness, tid, update_tasks, changes="[]")
    elif case == "send":
        out = _run(liveness, tid, send_email, to="x@acme.test", subject="Hi")
    else:
        out = _run(liveness, tid, keyed=False, deal_id="d-neuro", stage="Won")
    assert out.get("parked")
    assert late_on.data == {}, "no offer, so no late approve can grant"
    rid = _cards(liveness, tid)[0]["request_id"]
    assert _approve_late(monkeypatch, tid, rid).status_code == 409
    assert late_on.data == {}


def test_only_a_first_party_write_that_stays_in_metorite_has_keys():
    assert late_approve.keys_wire(None) is None
    assert late_approve.keys_wire(frozenset()) is None
    mail = ("acb_skills.email_tools", "send_email", (("to", ("x@acme.test",)),))
    deal = ("_tool_pack_owner_crm_assistant", "update_deal_status", (("deal_id", "d1"),))
    assert late_approve.keys_wire(frozenset({mail})) is None
    assert late_approve.keys_wire(frozenset({deal, mail})) is None, "one bad key spoils all"
    assert late_approve.keys_wire(frozenset({deal})) is not None


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


def test_an_open_row_and_a_row_with_rows_write_no_grant(late_on):
    row = {"kind": "confirmation", "state": "parked", "thread_id": "t1",
           "request_id": "a" * 32, "payload": {}}

    async def go(**change: Any) -> bool:
        return await late_approve.grant_from_offer(_ORG_A, _ALICE, {**row, **change}, "APPROVE")

    assert not asyncio.run(go(state="open"))
    assert not asyncio.run(go(kind="questions"))
    assert not asyncio.run(go(payload={"rows": [{"id": "r1"}]}))
    assert not asyncio.run(go(payload={"reply": {"kind": "new"}}))
    # A parked plain row with no offer writes nothing either.
    assert not asyncio.run(go())
    assert not [c for c in late_on.calls if c.startswith("set ")]


# ---------------------------------------------------------------------------
# 7. Off is today
# ---------------------------------------------------------------------------

def test_with_the_flag_off_nothing_is_read_or_written(
    flag_on, store, ask_db, liveness, no_persist, monkeypatch,
):
    tid = "t-hrd22-off"
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    rid = _cards(liveness, tid)[0]["request_id"]
    res = _approve_late(monkeypatch, tid, rid)
    assert res.json()["detail"]["resumeMessage"] == _envelope("Move this deal to Won?")
    assert _run(liveness, tid, deal_id="d-neuro", stage="Won").get("parked")
    assert WRITES == []
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
# 8. A Redis fault shows the normal card
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
# 9. The new message reads as an approve
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


# ---------------------------------------------------------------------------
# 10. The places of the write and the use
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


def test_each_record_has_one_writer_and_one_reader():
    """``grant_from_offer`` has one caller (the late answer route),
    ``record_offer`` one (the park) and ``take_grant`` one (the card gate).
    No other code names a namespace of the grant."""
    callers: dict[str, set[str]] = {"grant_from_offer(": set(), "record_offer(": set(),
                                    "take_grant(": set(), "chat-late-approve": set(),
                                    "chat-late-offer": set(), "GRANT_NAMESPACE": set()}
    for top in ("apps", "packages"):
        for path in (_ROOT / top).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in callers:
                if needle in text:
                    callers[needle].add(path.relative_to(_ROOT).as_posix())
    own = "packages/acb_skills/acb_skills/late_approve.py"
    assert callers["grant_from_offer("] == {own, "apps/services/gateway/gateway/routes/agent.py"}
    assert callers["record_offer("] == {own, "apps/services/orchestrator/orchestrator/pending_ask.py"}
    assert callers["take_grant("] == {own, "packages/acb_skills/acb_skills/ask_tools.py"}
    for name in ("chat-late-approve", "chat-late-offer", "GRANT_NAMESPACE"):
        assert callers[name] == {own}, (name, callers[name])


def test_the_use_comes_before_the_card_and_reads_once():
    from acb_skills import ask_tools

    src = inspect.getsource(ask_tools.request_confirmation)
    use = src.index("await _late_approved(_title, _detail, _context)")
    assert src.index("if cards_refused():") < use < src.index("_member_waits()")
    assert use < src.index("def _event(")
    take = inspect.getsource(late_approve.take_grant)
    assert ".getdel(key(GRANT_NAMESPACE, member, thread))" in take
    assert ".get(" not in take.replace(".getdel(", ""), "a GET would leave the grant for a replay"
