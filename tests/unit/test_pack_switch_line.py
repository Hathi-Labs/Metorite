"""WT-4: the switch line (``one_assistant_context.md`` §9.4).

A turn that loads a pack the last turn did not hold, in a chat that is not
held, sends ONE ``chat_pack_switched`` event with the pack label. It is a
signal beside layer (a), not a router: it compares the sticky set that
``begin_turn`` read with the packs that the apply loaded. It only READS the
hold mark. Each case names the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_pack_switch_line.py -q``.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import types
from typing import Any

import pytest
from acb_common import get_settings
from acb_common.tenant_redis import TenantRedis
from acb_skills import egress as eg
from acb_skills import tool_packs as tp
from orchestrator import executor as ex

ORG = "0a0a0a0a-0000-4000-8000-0000000000cc"
MEMBER = "jane@acme.test"
SESSION = "chat-wt4-1"


class _MarkRedis:
    """A fake raw Redis for the session mark. ``down`` makes each call fail."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.writes: list[str] = []
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("redis is down")

    async def setex(self, key: str, _s: int, value: str) -> bool:
        self.writes.append(key)
        self.keys[key] = value
        return True

    async def exists(self, *keys: str) -> int:
        self._check()
        return sum(1 for k in keys if k in self.keys)

    async def expire(self, key: str, _s: int) -> bool:
        self._check()
        return key in self.keys

    async def delete(self, *keys: str) -> int:
        self.writes.append("delete")
        return 0


@pytest.fixture
def store(monkeypatch) -> _MarkRedis:
    fake = _MarkRedis()
    monkeypatch.setattr(eg, "get_tenant_redis", lambda: TenantRedis(fake))
    monkeypatch.setattr("acb_common.db.current_tenant", lambda: ORG)
    eg._MARKED_SESSIONS.clear()
    yield fake
    eg._MARKED_SESSIONS.clear()


@pytest.fixture(autouse=True)
def packs_on(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "tool_packs", "*", raising=False)
    monkeypatch.setattr(settings, "tool_packs_orgs", "*", raising=False)


def _turn(sticky: tuple[str, ...] = (), n: int = 3, stored: bool = True) -> tp.PackTurn:
    held = tuple(tp.Sticky(slug=s, first=(1, i), used=n - 1) for i, s in enumerate(sticky))
    return tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), n, held, "", stored)


def _ctx() -> dict[str, Any]:
    return {"run_id": "r1", "session_id": SESSION, "agent_name": "orchestrator",
            "member": MEMBER, "batch_thread": False, "no_egress": False}


def _event(turn: tp.PackTurn, slugs: tuple[str, ...], *, task: bool = True) -> Any:
    async def go() -> Any:
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        fut.set_result((turn, frozenset(tp.PACKS)))
        applied = types.SimpleNamespace(record=types.SimpleNamespace(slugs=slugs))
        return await ex._pack_switch_event(
            fut if task else None, applied, _ctx(), agent_name="orchestrator")

    return asyncio.run(go())


def test_a_new_pack_in_an_unheld_chat_sends_one_switch_event_with_its_label(store) -> None:
    """Mutation: send with no label, or twice -> red."""
    event = _event(_turn(("email",)), ("email", "projects"))
    assert event == {"type": "CUSTOM", "name": "chat_pack_switched",
                     "value": {"label": "Projects"}}
    assert tp.PACK_SWITCH_EVENT == "chat_pack_switched"
    hold = ex._ProfileMarkHold()
    hold.add(event)
    out = hold.after({"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"})
    assert len(out) == 1 and "chat_pack_switched" in out[0]
    assert hold.after({"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}) == ()


def test_a_held_chat_sends_no_switch_event_and_its_mark_stays_set(store) -> None:
    """Mutation: skip the hold read, or clear the mark -> red."""
    assert asyncio.run(eg.mark_untrusted_answer(_ctx())) is True
    assert asyncio.run(eg.session_mark_state(_ctx())) == eg.MARK_MARKED
    eg._MARKED_SESSIONS.clear()  # read the mark from Redis, not the process set
    store.writes.clear()
    assert _event(_turn(("email",)), ("email", "projects")) is None
    assert asyncio.run(eg.session_mark_state(_ctx())) != eg.MARK_CLEAR
    assert "delete" not in store.writes
    assert store.writes == []


def test_a_redis_fault_reads_as_held_and_sends_nothing(store) -> None:
    """Mutation: read a fault as clear -> red."""
    store.down = True
    assert _event(_turn(("email",)), ("email", "projects")) is None


def test_tool_packs_off_sends_nothing(store, monkeypatch) -> None:
    """Mutation: send without ``packs_on`` -> red."""
    monkeypatch.setattr(get_settings(), "tool_packs", "", raising=False)
    assert tp.pack_switch_label(_turn(("email",)), ("projects",)) is None
    assert _event(_turn(("email",)), ("projects",)) is None
    assert _event(_turn(("email",)), ("projects",), task=False) is None


def test_the_same_pack_as_the_last_turn_sends_nothing(store) -> None:
    """Mutation: compare to nothing -> red. A first turn and a failed sticky
    read send nothing either."""
    assert _event(_turn(("projects",)), ("projects",)) is None
    assert tp.pack_switch_label(_turn((), n=1), ("projects",)) is None
    assert tp.pack_switch_label(_turn(("email",), stored=False), ("projects",)) is None


def test_the_calendar_surface_names_the_projects_label(store) -> None:
    """Calendar is a surface of the ``projects`` pack. Its day planner follows
    the core pack, so the parent names the line."""
    assert tp.clean_surface("calendar") == "calendar"
    assert tp.pack_switch_label(_turn(("email",)), ("calendar-day", "projects")) == "Projects"
    assert tp.pack_switch_label(_turn(("email",)), ("projects",)) == "Projects"
    assert _event(_turn(("email",)), ("projects", "calendar-day")) is not None


def _calls(tree: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_the_switch_reads_packs_on_and_no_second_matcher() -> None:
    """Mutation: add a regex or a keyword table to the function -> red. Mutation:
    write the mark from the sender -> red."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(tp.pack_switch_label)))
    assert "packs_on" in _calls(tree)
    src = inspect.getsource(tp.pack_switch_label)
    assert "re." not in src and "regex" not in src.lower()
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Dict)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            words = [e for e in node.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            assert len(words) < 2, "a keyword table is a second matcher"
    sender = inspect.getsource(ex._pack_switch_event)
    assert "_pack_held" in sender
    for word in ("setex", "mark_untrusted", "delete(", "hdel", "hset", "drop_sticky"):
        assert word not in sender
