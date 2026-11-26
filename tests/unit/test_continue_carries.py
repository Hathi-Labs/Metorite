"""HRD-19a: "Start clean" carries the decisions and the item in focus (``chat_failure_modes.md`` E3).

WT-1 (``POST /chat/sessions/{id}/continue``) gave a fresh chat the newest typed
message of the old one and nothing else. Now the route also copies two records of
the SAME member, key to key, and writes no text for them:

* the DECISIONS of the OA-4 list (``chat_constraints.carry_decisions``), under
  ``CHAT_CONSTRAINTS``. A limit stays with the old chat.
* the item in focus (``chat_focus.carry_focus``), under ``CHAT_FOCUS_ORGS``, with
  its ORIGINAL time, so it is no younger in the new chat.

Open cards do not carry (HRD-19b needs a design, ``chat_failure_modes.md``), and
nothing here turns on ``CHAT_CONTINUE_ORGS`` (an owner gate, task 1689).

Each rule names the mutation that turns it red (R7):

1. a decision that goes into ``text`` or a ``chat_message`` row ->
   ``test_the_route_writes_no_text_for_the_records``.
2. a limit that crosses -> ``test_only_decisions_cross_and_the_old_chat_keeps_its_list``.
3. a copy with the flag off -> ``test_with_the_flags_off_nothing_is_copied``.
4. a record of another member or another org that crosses ->
   ``test_another_member_and_another_org_do_not_cross``.
5. an old or bad item that crosses, or a younger time ->
   ``test_the_item_keeps_its_time_and_an_old_or_bad_one_does_not_cross``.
6. a Redis fault that fails the route -> ``test_a_redis_fault_copies_nothing_and_never_raises``.
7. a raw ``cc:`` key built in the route -> ``test_the_route_holds_no_key_of_its_own``.
8. a shared room that carries -> ``test_a_shared_room_carries_nothing`` (R8).

⚠️ The route tests need a real database (R8) and skip without one. A skip is not a pass.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from acb_common import get_settings
from acb_skills import chat_constraints as cc
from acb_skills import chat_focus as cf

from tests.unit._egress_mark_store import install_memory_mark_store

ORG = "org-a"
OTHER_ORG = "org-b"
SRC = "thread-old"
DST = "thread-new"
ME = "vj@hathilabs.test"
OTHER = "kiran@hathilabs.test"
MAIL = "e-sagar-1"
NOW = 1_800_000_000
ROUTE = Path(__file__).resolve().parents[2] / "apps/services/gateway/gateway/routes/chat.py"


def _part(member: str) -> str:
    return hashlib.sha256(member.encode("utf-8")).hexdigest()[:24]


def _limits_key(org: str = ORG, session: str = SRC, member: str = ME) -> str:
    return f"cc:{org}:chat-limits:{session}:{_part(member)}"


def _focus_key(org: str = ORG, session: str = SRC, member: str = ME) -> str:
    return f"cc:{org}:chat-focus:{session}:{_part(member)}"


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(cf, "_now", lambda: NOW)


@pytest.fixture
def flags(monkeypatch):
    """CHAT_CONSTRAINTS on, CHAT_FOCUS_ORGS names org-a only."""
    monkeypatch.setattr(get_settings(), "chat_constraints", True, raising=False)
    monkeypatch.setattr(get_settings(), "chat_focus_orgs", ORG, raising=False)


@pytest.fixture
def store(monkeypatch):
    return install_memory_mark_store(monkeypatch)


def _seed_list(store: Any, items: list[Any], **kw: Any) -> None:
    store.values[_limits_key(**kw)] = json.dumps(items)


def _seed_focus(store: Any, *, at: int = NOW, kind: str = "email", fid: str = MAIL, **kw: Any) -> None:
    store.values[_focus_key(**kw)] = json.dumps({"v": 1, "kind": kind, "id": fid, "at": at})


def _carry(member: str = ME, org: str = ORG, src: str = SRC, dst: str = DST) -> tuple[int, bool]:
    async def both() -> tuple[int, bool]:
        return (await cc.carry_decisions(org, src, dst, member),
                await cf.carry_focus(org, src, dst, member))
    return asyncio.run(both())


# ── The decisions ───────────────────────────────────────────────────────────

def test_only_decisions_cross_and_the_old_chat_keeps_its_list(flags, store) -> None:
    _seed_list(store, ["No Friday sends", {"kind": "decision", "text": "TEDx is done"},
                       {"kind": "decision", "text": "No reply needed to Priya"}])
    decisions, _ = _carry()
    assert decisions == 2
    assert json.loads(store.values[_limits_key(session=DST)]) == [
        {"kind": "decision", "text": "TEDx is done"},
        {"kind": "decision", "text": "No reply needed to Priya"}]  # no limit
    assert len(json.loads(store.values[_limits_key()])) == 3  # the old chat keeps its own list


def test_a_list_of_limits_only_copies_nothing(flags, store) -> None:
    _seed_list(store, ["No Friday sends"])
    assert _carry()[0] == 0
    assert _limits_key(session=DST) not in store.values


def test_with_the_flags_off_nothing_is_copied(monkeypatch, store) -> None:
    monkeypatch.setattr(get_settings(), "chat_constraints", False, raising=False)
    monkeypatch.setattr(get_settings(), "chat_focus_orgs", "", raising=False)
    _seed_list(store, [{"kind": "decision", "text": "TEDx is done"}])
    _seed_focus(store)
    assert _carry() == (0, False)
    assert not [k for k in store.values if k.endswith(("", )) and ":thread-new:" in k]


def test_the_focus_flag_is_the_org_list(flags, store) -> None:
    _seed_focus(store, org=OTHER_ORG)
    assert asyncio.run(cf.carry_focus(OTHER_ORG, SRC, DST, ME)) is False  # org-b is not on the list


def test_another_member_and_another_org_do_not_cross(flags, store) -> None:
    _seed_list(store, [{"kind": "decision", "text": "Kiran decided"}], member=OTHER)
    _seed_list(store, [{"kind": "decision", "text": "Org B decided"}], org=OTHER_ORG)
    _seed_focus(store, member=OTHER)
    assert _carry() == (0, False)  # ME holds nothing in org-a
    assert not [k for k in store.values if ":thread-new:" in k]
    # The call of another org reads that org's key only.
    assert asyncio.run(cc.carry_decisions(OTHER_ORG, SRC, DST, ME)) == 1
    assert _limits_key(org=OTHER_ORG, session=DST) in store.values
    assert _limits_key(org=ORG, session=DST) not in store.values


@pytest.mark.parametrize("raw", ["not json", "{}", "[1, 2]", '[{"kind": "decision"}]',
                                 '[{"kind": "decision", "text": "ok"}, {"kind": "x", "text": "bad kind"}]'])
def test_a_bad_stored_list_is_cleaned_again_on_the_way(flags, store, raw: str) -> None:
    store.values[_limits_key()] = raw
    n = asyncio.run(cc.carry_decisions(ORG, SRC, DST, ME))
    if n:
        assert json.loads(store.values[_limits_key(session=DST)]) == [{"kind": "decision", "text": "ok"}]
    else:
        assert _limits_key(session=DST) not in store.values


# ── The item in focus ───────────────────────────────────────────────────────

def test_the_item_keeps_its_time_and_an_old_or_bad_one_does_not_cross(flags, store) -> None:
    _seed_focus(store, at=NOW - 600)
    assert _carry() == (0, True)
    assert json.loads(store.values[_focus_key(session=DST)]) == {
        "v": 1, "kind": "email", "id": MAIL, "at": NOW - 600}  # the ORIGINAL time
    # An item older than the fresh time, a bad id and a bad kind do not cross.
    for i, bad in enumerate((
            dict(at=NOW - cf.FOCUS_FRESH_S - 5), dict(fid="not an id <script>"), dict(kind="deal"))):
        dst = f"thread-bad-{i}"
        _seed_focus(store, **bad)
        assert asyncio.run(cf.carry_focus(ORG, SRC, dst, ME)) is False
        assert _focus_key(session=dst) not in store.values


def test_the_life_of_the_copy_is_what_is_left(flags, monkeypatch) -> None:
    from acb_common.tenant_redis import TenantRedis

    seen: list[int] = []

    class _Client:
        async def get(self, _name: str) -> str:
            return json.dumps({"v": 1, "kind": "email", "id": MAIL, "at": NOW - 3600})

        async def setex(self, _name: str, seconds: int, _value: str) -> bool:
            seen.append(seconds)
            return True

    monkeypatch.setattr(cf, "get_tenant_redis", lambda: TenantRedis(_Client()))
    assert asyncio.run(cf.carry_focus(ORG, SRC, DST, ME)) is True
    assert seen == [cf.FOCUS_FRESH_S - 3600]


def test_the_same_chat_is_no_copy_and_blank_arguments_copy_nothing(flags, store) -> None:
    _seed_list(store, [{"kind": "decision", "text": "TEDx is done"}])
    _seed_focus(store)
    assert asyncio.run(cc.carry_decisions(ORG, SRC, SRC, ME)) == 0
    assert asyncio.run(cf.carry_focus(ORG, SRC, SRC, ME)) is False
    for org, src, dst, member in (("", SRC, DST, ME), (ORG, "", DST, ME),
                                  (ORG, SRC, "", ME), (ORG, SRC, DST, "  ")):
        assert asyncio.run(cc.carry_decisions(org, src, dst, member)) == 0
        assert asyncio.run(cf.carry_focus(org, src, dst, member)) is False


def test_a_redis_fault_copies_nothing_and_never_raises(flags, monkeypatch) -> None:
    from acb_common.tenant_redis import TenantRedis

    class _Down:
        async def get(self, *_a: Any) -> Any:
            raise ConnectionError("redis down")

        async def setex(self, *_a: Any) -> Any:
            raise ConnectionError("redis down")

    down = TenantRedis(_Down())
    monkeypatch.setattr(cc, "get_tenant_redis", lambda: down)
    monkeypatch.setattr(cf, "get_tenant_redis", lambda: down)
    assert _carry() == (0, False)


# ── The route holds no key and no text of its own ───────────────────────────

def _continue_source() -> str:
    text = ROUTE.read_text(encoding="utf-8")
    start = text.index("async def continue_session(")
    return text[start:text.index("class MessageFeedbackRequest", start)]


def test_the_route_holds_no_key_of_its_own() -> None:
    body = _continue_source()
    assert "chat_constraints.carry_decisions(" in body and "chat_focus.carry_focus(" in body
    assert not re.search(r"""["']cc:|redis|setex|tenant_redis""", body, re.I)


def test_the_route_writes_no_text_for_the_records() -> None:
    body = _continue_source()
    # `text` is the typed row and nothing else; the records never touch it.
    assert re.search(r'"text": carried\b', body)
    assert "carried_records" in body and "member_typed" not in body and "INSERT" not in body
    assert '"carried":' not in body  # no new field in the answer
    sql = ROUTE.read_text(encoding="utf-8")
    assert sql.count("SELECT content FROM chat_message") == 1  # _CARRY_SQL still reads `content` only


# ── The route (R8, a real database) ─────────────────────────────────────────

from tests.unit.test_continue_carry import (  # noqa: E402, F401  (fixtures by name)
    ALICE,
    BOB,
    TYPED,
    _chat,
    _continue,
    _held_chat,
    _row,
    _track,
    _user,
    db,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401  (fixtures by name)
    _DB_GATE,
    app_engine,
    promoted,
)


def _alice_keys(org: str, session: str) -> tuple[str, str]:
    return (f"cc:{org}:chat-limits:{session}:{_part(ALICE)}",
            f"cc:{org}:chat-focus:{session}:{_part(ALICE)}")


@_DB_GATE
def test_the_route_carries_the_decisions_and_the_focus_and_leaves_text_alone(
    db, flags, store, monkeypatch,  # noqa: F811
) -> None:
    sid = _held_chat(db)
    monkeypatch.setattr(cf, "_now", lambda: NOW)
    limits, focus = _alice_keys(db.org_a, sid)
    store.values[limits] = json.dumps(["No Friday sends", {"kind": "decision", "text": "TEDx is done"}])
    store.values[focus] = json.dumps({"v": 1, "kind": "email", "id": MAIL, "at": NOW - 60})
    monkeypatch.setattr(get_settings(), "chat_focus_orgs", db.org_a, raising=False)
    body = _track(db, _continue(db, sid, _user(ALICE, db.org_a)))
    assert set(body) == {"session_id", "text"}  # the shape that test_continue_carry.py pins
    assert body["text"] == TYPED  # byte-identical to before: the typed row only
    new_limits, new_focus = _alice_keys(db.org_a, body["session_id"])
    assert json.loads(store.values[new_limits]) == [{"kind": "decision", "text": "TEDx is done"}]
    assert json.loads(store.values[new_focus])["at"] == NOW - 60
    assert "TEDx" not in body["text"] and MAIL not in body["text"]


@_DB_GATE
def test_with_the_flags_off_the_route_answers_as_before(
    db, store, monkeypatch,  # noqa: F811
) -> None:
    sid = _held_chat(db)
    monkeypatch.setattr(get_settings(), "chat_constraints", False, raising=False)
    monkeypatch.setattr(get_settings(), "chat_focus_orgs", "", raising=False)
    limits, focus = _alice_keys(db.org_a, sid)
    store.values[limits] = json.dumps([{"kind": "decision", "text": "TEDx is done"}])
    store.values[focus] = json.dumps({"v": 1, "kind": "email", "id": MAIL, "at": NOW})
    body = _track(db, _continue(db, sid, _user(ALICE, db.org_a)))
    assert set(body) == {"session_id", "text"} and body["text"] == TYPED
    assert not [k for k in store.values if body["session_id"] in k]


@_DB_GATE
def test_a_shared_room_carries_nothing(
    db, flags, store, monkeypatch,  # noqa: F811
) -> None:
    sid = _chat(db, org=db.org_a, owner=ALICE, members=(BOB,))
    _row(db, org=db.org_a, sid=sid, ts=1_000, content=TYPED)
    limits, _ = _alice_keys(db.org_a, sid)
    store.values[limits] = json.dumps([{"kind": "decision", "text": "TEDx is done"}])
    body = _track(db, _continue(db, sid, _user(ALICE, db.org_a)))
    assert set(body) == {"session_id", "text"}
    assert not [k for k in store.values if body["session_id"] in k]


@_DB_GATE
def test_the_flag_off_for_continue_still_answers_404_and_copies_nothing(
    db, flags, store, monkeypatch,  # noqa: F811
) -> None:
    sid = _held_chat(db)
    limits, _ = _alice_keys(db.org_a, sid)
    store.values[limits] = json.dumps([{"kind": "decision", "text": "TEDx is done"}])
    monkeypatch.setattr(get_settings(), "chat_continue_orgs", "", raising=False)
    assert _continue(db, sid, _user(ALICE, db.org_a)).status_code == 404
    assert [k for k in store.values if ":chat-limits:" in k] == [limits]
