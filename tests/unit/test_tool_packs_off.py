"""WS-67 OA-7c: with ``TOOL_PACKS`` off, nothing of the tool packs acts.

``project-docs/specs/one_assistant_context.md`` §4.2.7: with the flag off,
these stay byte-identical: the tool names of the orchestrator, their order
and each specialist tool, the persona, the body of the turn-kind request and
the per-turn context. The loader reads and writes no pack state. This is the
pattern of the off test of OA-4 (``tests/unit/test_chat_constraints.py``).
Each case names, in its docstring, the mutation that turns it red (R7).

The runs go through the REAL ``run_agent_stream`` of the orchestrator, with a
scripted model (``tests/unit/_native_maf_harness.py``). Only the HTTP
transport of the model is replaced.

Run: ``uv run pytest tests/unit/test_tool_packs_off.py -q``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_common.assistant_profile import member_typed
from acb_common.tenant_redis import TenantRedis, key, organization_scope
from acb_skills import agent_tools
from acb_skills import tool_packs as tp
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit._native_maf_harness import (
    ScriptedModel,
    load_agent_module,
    parse_frames,
    text_turn,
)

REPO = Path(__file__).resolve().parents[2]
ORCH_DIR = "apps/agents/agent-orchestrator"
CONFIG = json.loads((REPO / ORCH_DIR / "config.json").read_text(encoding="utf-8"))
EA = "email-assistant"
ORG = "0a0a0a0a-0000-4000-8000-0000000000cc"
MEMBER = "jane@acme.test"
SESSION = "chat-oa7c-off"
#: Email words of a short turn. With the flag on they load the email pack, so
#: each case below can tell the off run from an on run. Under 12 words, so
#: the turn-kind question sends no request.
WORDS = "Check my inbox and send the draft"


def _real_turn_kind() -> Any:
    from acb_skills import tier_policy

    return tier_policy.turn_kind


#: The real turn-kind question, taken once, so that a second run's recorder
#: never wraps the first run's recorder.
_REAL_TURN_KIND = _real_turn_kind()


@pytest.fixture
def store(monkeypatch) -> Any:
    """A fresh in-memory tenant Redis. It records each call."""
    return install_memory_mark_store(monkeypatch)


def _flag(monkeypatch, on: bool) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "tool_packs", "*" if on else "", raising=False)
    monkeypatch.setattr(settings, "tool_packs_orgs", "*" if on else "", raising=False)


def _pack_calls(store: Any) -> list[tuple[str, str]]:
    return [call for call in store.calls if f":{tp.NAMESPACE}:" in call[1]]


async def _seed_sticky_email(store: Any) -> None:
    """The chat used the email pack one turn ago, so an on run loads it."""
    with organization_scope(ORG):
        state = key(tp.NAMESPACE, SESSION, tp._member_part(MEMBER))
        client = TenantRedis(store)
        await client.hset(state, "email", json.dumps({"first": [1, 0], "used": 1}))
        await client.hset(state, "~turn", "1")
    store.calls.clear()


def _drive(monkeypatch, run_id: str) -> dict[str, Any]:
    """One turn of the member's own web chat with the orchestrator.

    It returns the first request body of the model, the arguments of the
    turn-kind question and the events. ``AI_TIER_ROUTING`` covers the
    orchestrator, so the run asks the turn kind. The words are short, so
    that question sends no request.
    """
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    from acb_skills import email_chat_context as ecc
    from acb_skills import tier_policy

    model = ScriptedModel([text_turn("Done.")])
    mod = load_agent_module(ORCH_DIR)

    def _build() -> list[Any]:
        agents = mod.build_agents()
        oc = agents[0].client.client
        oc._client = httpx.AsyncClient(transport=httpx.MockTransport(model),
                                       event_hooks=oc._client.event_hooks)
        return agents

    class _Loaded:
        agent_dir = REPO / ORCH_DIR
        agent_name = "orchestrator"
        config = CONFIG

        def build_agents(self) -> list[Any]:
            return _build()

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    async def _none(*_a: Any, **_k: Any) -> None:
        return None

    asked: list[tuple[str, list[str], str]] = []

    async def turn_kind(message: str, tools: Any, think_mode: Any, **kw: Any) -> Any:
        asked.append((message, list(tools), str(think_mode)))
        return await _REAL_TURN_KIND(message, tools, think_mode, **kw)

    import acb_auth.access as auth_access

    monkeypatch.setattr(auth_access, "_tables_missing", auth_access._tables_missing)
    monkeypatch.setattr(ex, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(ex, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(ex, "_tier_policy_covers", lambda name: name == "orchestrator")
    monkeypatch.setattr(tier_policy, "turn_kind", turn_kind)
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _n: frozenset())
    monkeypatch.setattr(ecc, "prime_mailboxes", _none)
    monkeypatch.setattr("acb_common.today.today_line", lambda *_a, **_k: "")

    async def _collect() -> list[str]:
        return [line async for line in ex.run_agent_stream(
            "orchestrator", {"message": WORDS}, run_id=run_id, thread_id=SESSION,
            organization_id=ORG, session_user=MEMBER, shows_cards=True, solo_room=True,
        )]

    with member_typed(WORDS):
        events = parse_frames(asyncio.run(_collect()))
    return {"events": events, "body": model.bodies[0] if model.bodies else {},
            "asked": asked}


def _tool_names(body: dict[str, Any]) -> list[str]:
    return [t["function"]["name"] for t in body.get("tools") or []]


def test_the_flag_ships_off() -> None:
    """Mutation: a default other than empty for either setting → packs act
    on each box that sets nothing → red."""
    settings = get_settings()
    assert getattr(settings, "tool_packs", None) == ""
    assert getattr(settings, "tool_packs_orgs", None) == ""
    assert tp.packs_on(ORG) == frozenset()


async def test_flag_off_reads_and_writes_no_pack_state(store, monkeypatch) -> None:
    """Mutation: start the read of layer (a) before the flag check → an
    eligible run with the flag off counts a turn in Redis → red."""
    _flag(monkeypatch, False)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    task = ex._start_pack_turn(
        "orchestrator", ORG, SESSION, member=MEMBER, delegated=False,
        solo_room=True, batch_thread=False, shows_cards=True)
    assert task is None
    assert _pack_calls(store) == []


def test_flag_off_the_run_is_byte_identical_to_a_run_with_no_pack_code(
    store, monkeypatch,
) -> None:
    """The real stream run of the orchestrator, with the flag off and with a
    sticky email pack in Redis and email words in the turn, against the same
    run with the OA-7c code taken out. The tool list and its order, the
    persona, the per-turn context (the whole first request body) and the
    turn-kind request are the same, and no pack state is read. Mutation: a
    pack loads, the persona swap runs or the state is read with the flag off
    → a body, a request or the Redis log differs → red."""
    _flag(monkeypatch, False)
    asyncio.run(_seed_sticky_email(store))
    off = _drive(monkeypatch, "run-oa7c-off-1")
    assert _pack_calls(store) == [], "the flag-off run read no pack state"
    with monkeypatch.context() as m:
        m.setattr(ex, "_start_pack_turn", lambda *a, **k: None)

        async def _never(*_a: Any, **_k: Any) -> None:
            return None

        m.setattr(ex, "_apply_pack_turn", _never)
        bare = _drive(m, "run-oa7c-off-1")
    assert [e.get("type") for e in off["events"]][-1] == "RUN_FINISHED", off["events"]
    assert off["body"] and off["body"] == bare["body"]
    assert off["asked"] and off["asked"] == bare["asked"]
    names = _tool_names(off["body"])
    assert "email_assistant" in names and "read_email" not in names
    system = off["body"]["messages"][0]["content"]
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    assert PACK_DELEGATION_BULLETS[EA] in system and "EMAIL TOOLS." not in system


def test_flag_on_the_same_turn_holds_the_email_pack(store, monkeypatch) -> None:
    """Non-vacuity of the case above: the same turn with the flag on loads
    the sticky email pack, drops ``email_assistant``, swaps the persona and
    counts a turn in Redis. Mutation: the apply never runs in a live run →
    red."""
    _flag(monkeypatch, True)
    asyncio.run(_seed_sticky_email(store))
    on = _drive(monkeypatch, "run-oa7c-on-1")
    kinds = [e.get("type") for e in on["events"]]
    assert kinds[-1] == "RUN_FINISHED" and "RUN_ERROR" not in kinds, on["events"]
    names = _tool_names(on["body"])
    assert "email_assistant" not in names and "read_email" in names
    system = on["body"]["messages"][0]["content"]
    assert "EMAIL TOOLS." in system and "email_assistant has no memory" not in system
    assert ("hincrby", f"cc:{ORG}:{tp.NAMESPACE}:{SESSION}:{tp._member_part(MEMBER)}") in [
        (op, name) for op, name in _pack_calls(store)]
    assert on["asked"] and "read_email" in on["asked"][0][1]


async def test_flag_off_no_hand_off_is_refused(store, monkeypatch) -> None:
    """With no pack record bound, a hand-off to the email assistant runs as
    before. Mutation: refuse the email assistant whatever the run holds → red."""
    ran: list[str] = []

    async def fake_run_agent(name: str, payload: dict, **_k: Any) -> dict:
        ran.append(name)
        return {"result": "ran", "tool_calls": []}

    monkeypatch.setattr(ex, "run_agent", fake_run_agent)
    with artifact_context_scope():
        bind_artifact_context(run_id="r-off", session_id=SESSION, agent_name="orchestrator",
                              member=MEMBER, no_egress=False)
        assert tp.delegation_refusal(EA) is None
        assert agent_tools._pack_refusal(EA) is None
        assert ex._pack_delegation_refusal(EA) is None
