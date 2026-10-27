"""WS-67 OA-7c: layer (a) and the sticky packs in a live run.

``project-docs/specs/one_assistant_context.md`` §4.2.2, §4.2.8 (OA-7c) and
the narrowings of §4.5. Layer (a) takes three inputs here: the sticky packs
of the chat, a reference in the member's words, and the words of a pack. The
surface waits for OA-7d. Each case names, in its docstring, the mutation
that turns it red (R7).

1. Each input of layer (a), and only the packs that the flag allows.
2. A pack becomes sticky only after one of its tools ran, and two pack calls
   at once lose no update.
3. The decay: a sticky pack leaves after 8 member turns with no use.
4. The cap: 3 packs, 110 tools with the base of the run, 35 tools a pack,
   and the least recently used pack leaves first.
5. The order: sticky packs keep their first-load order, the pack tools
   follow the core tools, and the persona holds the rules in load order.
6. The acceptance: an email turn holds the ``email`` pack and no
   ``email_assistant`` tool, and a hand-off to the email assistant gets the
   pack refusal. Rooms, WhatsApp and delegated runs see no pack. A pack send
   in a marked chat, built through the real assembly, shows one card.
7. The six conditions of OA-7b fix round 1.

The probe of a turn after an eviction, with native sessions on, is in
``tests/unit/test_native_session_persistence.py``.

Run: ``uv run pytest tests/unit/test_tool_packs_choice.py -q``.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common.db import bind_tenant, release_tenant
from acb_skills import agent_tools
from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from acb_skills.write_artifact import (
    artifact_context,
    artifact_context_scope,
    bind_artifact_context,
)
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit import _held_card_fakes as F
from tests.unit._egress_mark_store import install_memory_mark_store

REPO = Path(__file__).resolve().parents[2]
EA = "email-assistant"
ORG = "0a0a0a0a-0000-4000-8000-0000000000bb"
MEMBER = "jane@acme.test"
SESSION = "chat-oa7c-1"
BOX = "acc-oa7c-1"
ALL = frozenset({"email", "email-setup"})
EMAIL_TOOLS = frozenset(sf.SKILL_FAMILIES["email"]["tools"]) - {"narrow_and_read"}
SETUP_TOOLS = frozenset(sf.SKILL_FAMILIES["email-setup"]["tools"])
CONFIG = json.loads((REPO / "apps/agents/agent-orchestrator/config.json")
                    .read_text(encoding="utf-8"))

_READ_ONLY = {"read_only": True, "destructive": False, "idempotent": True, "open_world": False}


def _agents_mod() -> Any:
    import orchestrator.agents as pkg  # the package loads agents.py as _mod

    return pkg._mod


def _bullets() -> dict[str, str]:
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    return dict(PACK_DELEGATION_BULLETS)


def _name(tool: Any) -> str:
    return ti._tool_name(tool)


def _turn(turn: int = 1, sticky: tuple[tp.Sticky, ...] = (), words: str = "",
          stored: bool = True) -> tp.PackTurn:
    return tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), turn, tuple(sticky), words, stored)


def _st(slug: str, first: int, used: int, place: int = 0) -> tp.Sticky:
    return tp.Sticky(slug=slug, first=(first, place), used=used)


@pytest.fixture
def store(monkeypatch) -> Any:
    """A fresh in-memory tenant Redis for the marks, the working set and the
    sticky packs. Returns the raw client, which records each call."""
    return install_memory_mark_store(monkeypatch)


@contextlib.contextmanager
def _tenant(org: str = ORG) -> Iterator[None]:
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _run_ctx(**over: Any) -> dict[str, Any]:
    """The server-side run context of the member's own chat run."""
    values: dict[str, Any] = {
        "run_id": "r-oa7c", "session_id": SESSION, "agent_name": "orchestrator",
        "member": MEMBER, "batch_thread": False, "no_egress": False,
        "workspace_root": "/tmp/orch-oa7c",
    }
    values.update(over)
    return values


@contextlib.contextmanager
def _bound(**values: Any) -> Iterator[None]:
    with artifact_context_scope():
        bind_artifact_context(**values)
        yield


def _pack_calls(store: Any) -> list[tuple[str, str]]:
    return [call for call in store.calls if f":{tp.NAMESPACE}:" in call[1]]


def _stub(*tools: Any) -> Any:
    import types

    return types.SimpleNamespace(_agent_tools=lambda: list(tools))


def _probe(name: str, ran: list[str] | None = None) -> Any:
    """A read-only tool of a pack, named *name*."""

    async def tool(email_id: str = "", draft_id: str = "") -> str:
        if ran is not None:
            ran.append(name)
        return f"{name} done"

    tool.__name__ = name
    tool.__tool_risk__ = dict(_READ_ONLY)  # type: ignore[attr-defined]
    return tool


@pytest.fixture
def orchestrator(monkeypatch) -> list[Any]:
    """The real orchestrator agent, as its agent repo builds it."""
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    from orchestrator.agents import build_orchestrator_agent

    return [build_orchestrator_agent(with_history=False)]


async def _apply(agents: list[Any], words: str, *, allowed: frozenset[str] = ALL,
                 run_ctx: dict[str, Any] | None = None) -> Any:
    turn = await tp.begin_turn(ORG, SESSION, MEMBER, words)
    return await ti._apply_tool_packs(
        agents, turn, run_ctx or _run_ctx(), allowed=allowed, agent_name="orchestrator",
        tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG, bullets=_bullets(),
    )


def _names(agents: list[Any]) -> list[str]:
    return [_name(t) for t in agents[0].default_options["tools"]]


# ── 1. The inputs of layer (a) ──────────────────────────────────────────────


def test_a_reference_in_the_members_words_loads_its_pack() -> None:
    """Mutation: drop the reference patterns from the choice → the layer of
    the link reads "words", not "reference" → red."""
    words = "Look at [the quote](/email?email=0c0c0c&account=acc-1) please"
    assert tp.matched_layer("email", words) == "reference"
    choice = tp.choose(_turn(words=words), allowed=ALL, base_tools=40)
    assert choice.slugs == ("email",) and choice.layers == {"email": "reference"}


@pytest.mark.parametrize(("words", "want"), [
    ("Check my INBOX please", ("email",)),
    ("Any unread e-mails?", ("email",)),
    ("Open my gmail settings", ()),
    ("Turn on auto-draft for me", ("email-setup",)),
    ("Set my writing style", ("email-setup",)),
    ("Make my Email   Rules stricter", ("email", "email-setup")),
    ("What is the weather in Pune?", ()),
    ("Draft a reply to Anil", ("email",)),
    ("Draft the PRD in Notion", ()),
    ("create a Linear ticket from the draft spec", ()),
    ("Show my unread WhatsApp chats", ()),
])
def test_the_words_load_a_pack_as_whole_words_in_any_case(words: str, want: tuple) -> None:
    """Mutation: match a word inside another word, or in one case only →
    "gmail" loads the email pack, "auto-draft" loads it too, or "INBOX"
    loads nothing → red. Fix round 1 (P1-B): each email word needs an email
    context. Mutation: put "draft" or "unread" back → "Draft the PRD in
    Notion" loads the email pack → red."""
    assert tp.choose(_turn(words=words), allowed=ALL, base_tools=40).slugs == want


def test_a_sticky_pack_loads_with_no_words() -> None:
    """Mutation: the choice ignores the sticky state → a follow-up turn with
    no email word ("send it") holds no email tool → red."""
    choice = tp.choose(_turn(turn=5, sticky=(_st("email", 2, 4),), words="send it"),
                       allowed=ALL, base_tools=40)
    assert choice.slugs == ("email",) and choice.layers == {"email": "sticky"}


def test_only_the_packs_that_the_flag_allows_load() -> None:
    """Mutation: the choice reads every registered pack → a pack that the
    owner did not turn on loads → red. A sticky pack that is not allowed now
    does not load, and it stays in the state for the day it is allowed."""
    turn = _turn(turn=3, sticky=(_st("email-setup", 1, 2),),
                 words="check my inbox and my writing style")
    assert tp.choose(turn, allowed={"email"}, base_tools=40).slugs == ("email",)
    choice = tp.choose(turn, allowed=frozenset(), base_tools=40)
    assert choice.slugs == () and choice.evicted == ()


async def test_an_admin_toggle_stops_a_pack(store, orchestrator, monkeypatch) -> None:
    """``skills_registry.md`` rule 1: an admin who turns ``email`` off for
    the orchestrator stops that pack. Mutation: the apply skips the toggles
    → the email pack loads → red."""
    monkeypatch.setattr(ti, "_load_disabled_skill_families",
                        lambda name: frozenset({"email"}) if name == "orchestrator" else frozenset())
    with _tenant():
        applied = await _apply(orchestrator, "What came in my inbox?")
    assert applied is None and "email_assistant" in _names(orchestrator)


# ── 2. Sticky on use ────────────────────────────────────────────────────────


async def test_a_pack_becomes_sticky_only_after_one_of_its_tools_ran(
    store, orchestrator, monkeypatch,
) -> None:
    """A wrong pack costs tokens only: it never becomes sticky, because none
    of its tools ran. The run goes through the real apply, and a real tool
    of the pack runs. Mutation: write the sticky state when the pack loads
    → the next turn holds a pack whose tools never ran → red."""

    async def get(path: str, params: Any = None) -> Any:
        if path == "/email/accounts":
            return [{"id": BOX, "email_address": MEMBER, "display_label": "Work"}]
        return {}

    with _tenant():
        applied = await _apply(orchestrator, "check my inbox")
        assert applied is not None and applied.record.slugs == ("email",)
        later = await tp.begin_turn(ORG, SESSION, MEMBER, "")
        assert later.sticky == (), "a load alone makes no pack sticky"
        monkeypatch.setattr(ti._PACK_OWNER_MODULES[EA], "_get", get)
        tool = next(t for t in orchestrator[0].default_options["tools"]
                    if _name(t) == "list_accounts")
        with _bound(**_run_ctx(), **applied.context()):
            out = await tool.func()
        assert out != ti.PACK_CALL_REFUSED.format(tool="list_accounts"), out
        after = await tp.begin_turn(ORG, SESSION, MEMBER, "")
    assert [(s.slug, s.used) for s in after.sticky] == [("email", applied.record.turn)]
    assert after.turn == applied.record.turn + 2


async def test_two_pack_calls_at_once_lose_no_sticky_update(store) -> None:
    """Each pack is one field of the state hash. Mutation: keep the state as
    one JSON value and write it whole → two calls at once each read it before
    the other writes, and one pack is lost → red."""
    real = store.hset

    async def slow_hset(*a: Any, **k: Any) -> Any:
        await asyncio.sleep(0.01)
        return await real(*a, **k)

    store.hset = slow_hset  # type: ignore[method-assign]
    with _tenant():
        t1 = await tp.begin_turn(ORG, SESSION, MEMBER, "my inbox and my writing style")
        choice = tp.choose(t1, allowed=ALL, base_tools=40)
        record = tp.PackRun.of(t1, choice, [(slug, False) for slug in choice.slugs])
        ctx = {tp.PACKS_KEY: record}
        assert all(await asyncio.gather(tp.note_used("email", ctx),
                                        tp.note_used("email-setup", ctx)))
        t2 = await tp.begin_turn(ORG, SESSION, MEMBER, "")
    assert sorted(s.slug for s in t2.sticky) == ["email", "email-setup"]


async def test_a_run_with_no_record_writes_no_sticky_state(store) -> None:
    """A pack call of a test, or of a run that loaded no pack, binds no
    record. Mutation: write the state from the run context alone → a stray
    call makes a pack sticky → red."""
    with _tenant(), _bound(**_run_ctx()):
        assert await tp.note_used("email") is False
    assert _pack_calls(store) == []


# ── 3. The decay ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("now", "stays"), [(10, True), (11, False)])
def test_a_sticky_pack_leaves_after_8_member_turns_with_no_use(now: int, stays: bool) -> None:
    """Used in turn 2: it stays through turn 10, and leaves at the start of
    turn 11. Mutation: decay after 7 turns, or never → red at one of the
    two turns."""
    choice = tp.choose(_turn(turn=now, sticky=(_st("email", 1, 2),)), allowed=ALL, base_tools=40)
    assert (choice.slugs == ("email",)) is stays
    assert (choice.evicted == ("email",)) is (not stays)


async def test_the_apply_takes_an_evicted_pack_out_of_the_state(store, orchestrator) -> None:
    """Mutation: the apply drops the eviction write → the decayed pack stays
    in Redis and loads again on a later turn → red."""
    from acb_common.tenant_redis import key, organization_scope

    with _tenant(), organization_scope(ORG):
        state = key(tp.NAMESPACE, SESSION, tp._member_part(MEMBER))
        from acb_common.tenant_redis import TenantRedis

        client = TenantRedis(store)
        await client.hset(state, "email", json.dumps({"first": [1, 0], "used": 1}))
        await client.hset(state, "~turn", "11")
        assert await _apply(orchestrator, "") is None
        left = await client.hgetall(state)
    assert "email" not in left and "~turn" in left


# ── 4. The cap ──────────────────────────────────────────────────────────────


def _measured_base(agents: list[Any]) -> int:
    return ti._pack_base_count(
        agents[0], agent_name="orchestrator", tool_scope=list(CONFIG["tool_scope"]),
        agent_config=CONFIG, disabled=frozenset())


async def test_the_cap_holds_110_tools_with_the_measured_base(orchestrator) -> None:
    """Fix round 1 (Q1, P1-A): the cap reads the measured base of the real
    orchestrator, 55 tools on 2026-10-27. The 31 email tools fit beside it.
    With the 28 setup tools too, a request holds 114, past 110, so the two
    email packs never load together beside that base. Mutation: the cap
    ignores the base tools → both packs load, 114 tools in one request →
    red."""
    base = _measured_base(orchestrator)
    n_email = len(sf.SKILL_FAMILIES["email"]["tools"])
    n_setup = len(sf.SKILL_FAMILIES["email-setup"]["tools"])
    assert base + n_email <= tp.MAX_REQUEST_TOOLS < base + n_email + n_setup, base
    turn = _turn(turn=8, sticky=(_st("email", 1, 5), _st("email-setup", 2, 7)))
    over = tp.choose(turn, allowed=ALL, base_tools=base)
    assert over.slugs == ("email-setup",) and over.evicted == ("email",)
    both = tp.choose(_turn(words="Make my email rules stricter"), allowed=ALL, base_tools=base)
    assert both.slugs == ("email-setup",) and both.skipped == ("email",)
    room = tp.MAX_REQUEST_TOOLS - n_email - n_setup
    assert tp.choose(turn, allowed=ALL, base_tools=room).slugs == ("email", "email-setup")


def test_when_both_email_packs_match_and_one_fits_email_setup_stays() -> None:
    """Fix round 1 (P1-A). With ``email-setup`` loaded, the ``email_assistant``
    tool stays, so the mail keeps a path, and the setup work has its tools.
    Mutation: the cap drops the last new pack again → ``email`` stays, and
    the request for a rule has no tool of its own → red."""
    choice = tp.choose(_turn(words="Make my email rules stricter"), allowed=ALL, base_tools=55)
    assert choice.slugs == ("email-setup",) and choice.skipped == ("email",)
    # One new pack alone past the cap leaves as before.
    alone = tp.choose(_turn(words="check my inbox"), allowed=ALL, base_tools=100)
    assert alone.slugs == () and alone.skipped == ("email",)


def test_past_the_cap_the_least_recently_used_sticky_pack_leaves_first() -> None:
    """Mutation: drop the newest or the first-loaded pack instead → red in
    one of the two orders."""
    a = _turn(turn=8, sticky=(_st("email", 1, 7), _st("email-setup", 2, 5)))
    assert tp.choose(a, allowed=ALL, base_tools=52).evicted == ("email-setup",)
    b = _turn(turn=8, sticky=(_st("email", 1, 5), _st("email-setup", 2, 7)))
    assert tp.choose(b, allowed=ALL, base_tools=52).evicted == ("email",)


def test_a_new_pack_past_the_cap_takes_the_place_of_the_oldest_sticky_one() -> None:
    """The member's words of this turn are the most recent use. Mutation: a
    new pack never displaces a sticky one → the pack that the member asks
    for now does not load → red."""
    turn = _turn(turn=8, sticky=(_st("email", 1, 7),), words="set my writing style")
    choice = tp.choose(turn, allowed=ALL, base_tools=52)
    assert choice.slugs == ("email-setup",) and choice.evicted == ("email",)


def _fake_packs(monkeypatch, sizes: dict[str, int]) -> None:
    """Fake packs of an owner ``x-agent``, each with its own word."""
    for slug, size in sizes.items():
        monkeypatch.setitem(sf.SKILL_FAMILIES, slug, {
            "label": slug, "summary": slug, "description": slug, "owner": "x-agent",
            "tools": tuple(f"{slug}_t{i}" for i in range(size)),
        })
        monkeypatch.setitem(tp.PACKS, slug, tp.Pack(slug=slug, rules=f"{slug} RULES",
                                                    words=(f"{slug}word",)))


def test_at_most_3_packs_load(monkeypatch) -> None:
    """Mutation: lift ``MAX_PACKS`` → 4 packs load → red."""
    _fake_packs(monkeypatch, {"pa": 5, "pb": 5, "pc": 5, "pd": 5})
    turn = _turn(words="paword pbword pcword pdword")
    choice = tp.choose(turn, allowed={"pa", "pb", "pc", "pd"}, base_tools=10)
    assert choice.slugs == ("pa", "pb", "pc") and choice.skipped == ("pd",)


def test_a_pack_of_more_than_35_tools_never_loads(monkeypatch) -> None:
    """The registry fence refuses such a pack, and the choice refuses it
    too. Mutation: drop the limit from the choice → a 36-tool pack loads →
    red."""
    for slug in tp.PACKS:
        if sf.is_pack(slug):
            assert len(sf.SKILL_FAMILIES[slug]["tools"]) <= tp.MAX_PACK_TOOLS
    _fake_packs(monkeypatch, {"pbig": 36, "pok": 35})
    choice = tp.choose(_turn(words="pbigword pokword"), allowed={"pbig", "pok"}, base_tools=10)
    assert choice.slugs == ("pok",)


@pytest.mark.parametrize("facts", ["crm-questions-off", "org-not-on-the-crm-list"])
async def test_the_base_count_equals_the_injected_count_with_no_app_grant(
    orchestrator, monkeypatch, facts: str,
) -> None:
    """Fix round 1 (Q1): the apply stays before ``_inject_agent_tools``, so
    the cap reads an estimate. With no app grant the estimate equals the
    count after the injection. ``_add_crm_facts_block`` can only take tools
    away after it, and the executor logs the true count and warns past the
    cap. Mutation: drop the CRM facts from the withheld names of the
    estimate → it counts 2 tools that the run never holds → red for an org
    that is not on the CRM list. Mutation: count the agent's own tools only
    → red in both cases."""
    from acb_common import get_settings

    if facts == "org-not-on-the-crm-list":
        monkeypatch.setattr(get_settings(), "crm_questions_orgs", "org-other", raising=False)
    monkeypatch.setattr("orchestrator.app_tools.load_app_action_tools", lambda _name: [])
    with _tenant():
        base = _measured_base(orchestrator)
        ti._inject_agent_tools(
            orchestrator, tool_scope=list(CONFIG["tool_scope"]), agent_name="orchestrator",
            agent_config=CONFIG, no_egress=False,
        )
        injected = _names(orchestrator)
        await ti._add_crm_facts_block(orchestrator, frozenset(injected), no_egress=False)
    assert base == len(injected), (base, len(injected))
    assert len(_names(orchestrator)) <= base


# ── 5. The order ────────────────────────────────────────────────────────────


def test_sticky_packs_keep_their_first_load_order() -> None:
    """The tool list stays byte-stable in a chat, so the prompt cache holds.
    Mutation: order the packs by the registry → the list changes order
    between turns → red."""
    turn = _turn(turn=6, sticky=(_st("email", 4, 5), _st("email-setup", 2, 5)))
    assert tp.choose(turn, allowed=ALL, base_tools=40).slugs == ("email-setup", "email")
    turn = _turn(turn=6, sticky=(_st("email-setup", 2, 5),), words="check my inbox")
    choice = tp.choose(turn, allowed=ALL, base_tools=40)
    assert choice.slugs == ("email-setup", "email")
    assert choice.first == {"email-setup": (2, 0), "email": (6, 1)}


async def test_the_pack_tools_follow_the_core_tools(store, orchestrator) -> None:
    """Mutation: put the pack tools first → each load moves the core tools,
    and every turn misses the cache → red."""
    core = [n for n in _names(orchestrator) if n != "email_assistant"]
    with _tenant():
        applied = await _apply(orchestrator, "What came in my inbox?")
    names = _names(orchestrator)
    assert applied is not None
    assert names[:len(core)] == core
    assert set(names[len(core):]) == EMAIL_TOOLS


def test_the_persona_holds_the_rules_in_load_order() -> None:
    """Mutation: append the rules in registry order, or put them before the
    persona → red. With no pack the persona is the same, byte for byte."""
    agents = _agents_mod()
    persona = agents._orchestrator_instructions(False)
    text = tp.persona_for(persona, [("email-setup", False), ("email", False)], _bullets())
    base = persona.replace(agents.EMAIL_DELEGATION_BULLETS, "", 1).rstrip("\n")
    assert text.startswith(base + "\n\n")
    assert text.index("EMAIL SETUP TOOLS.") < text.index("EMAIL TOOLS.")
    assert agents.EMAIL_DELEGATION_BULLETS not in text and "email_assistant" not in text
    assert tp.persona_for(persona, [], _bullets()) == persona


@pytest.mark.parametrize("company_search", [False, True])
@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("constraints", [False, True])
def test_each_persona_holds_the_email_delegation_text_exactly_once(
    company_search: bool, profile: bool, constraints: bool,
) -> None:
    """Mutation: change a line of the email bullets in the persona but not in
    the constant → the swap finds nothing → red here, before a run ships a
    half-swapped persona."""
    agents = _agents_mod()
    text = agents._orchestrator_instructions(
        company_search, company_search, company_search, profile=profile, constraints=constraints)
    assert text.count(agents.EMAIL_DELEGATION_BULLETS) == 1
    assert _bullets() == {EA: agents.EMAIL_DELEGATION_BULLETS}


def test_a_persona_without_the_owners_text_refuses_the_swap() -> None:
    """Mutation: skip a missing text → a run ships the rules beside the old
    delegation bullets → red."""
    with pytest.raises(RuntimeError):
        tp.persona_for("A persona with no email bullets.\n", [("email", False)], _bullets())


# ── 6. The acceptance ───────────────────────────────────────────────────────


async def test_an_email_turn_holds_the_email_pack_and_no_email_assistant_tool(
    store, orchestrator,
) -> None:
    """Mutation: keep the specialist tool beside the pack → the turn holds
    ``email_assistant`` and the email tools → red. Fix round 1 (P1-A): with
    ``email`` alone, the hand-off stays open for the setup work, and the
    rules say so in one line."""
    assert "email_assistant" in _names(orchestrator), "non-vacuity"
    with _tenant():
        applied = await _apply(orchestrator, "What came in my inbox today?")
    names = set(_names(orchestrator))
    assert applied is not None and applied.record.slugs == ("email",)
    assert "email_assistant" not in names and "delegate_to_agent" in names
    assert names >= EMAIL_TOOLS and not SETUP_TOOLS & names
    persona = orchestrator[0].default_options["instructions"]
    assert _agents_mod().EMAIL_DELEGATION_BULLETS not in persona
    assert _alone_rules() in persona
    assert applied.record.replaced == {EA} and applied.record.refusals == {}


def _alone_rules() -> str:
    """The rules of the ``email`` pack while the run holds no ``email-setup``."""
    line, route = tp.PACKS["email"].handoff_swap
    return tp.PACKS["email"].rules.rstrip("\n").replace(line, route, 1)


async def test_a_turn_with_no_email_input_keeps_delegation(store, orchestrator) -> None:
    """Mutation: load the pack on every turn → a turn about the weather holds
    31 email tools → red."""
    persona = orchestrator[0].default_options["instructions"]
    names = _names(orchestrator)
    with _tenant():
        assert await _apply(orchestrator, "What is the weather in Pune?") is None
    assert _names(orchestrator) == names
    assert orchestrator[0].default_options["instructions"] == persona


@pytest.mark.parametrize("words", [
    "Draft the PRD in Notion",
    "create a Linear ticket from the draft spec",
    "check my inbox",
])
async def test_a_draft_that_is_no_mail_keeps_the_mcp_server(
    words: str, store, orchestrator, monkeypatch,
) -> None:
    """Fix round 1 (P1-B). A run with the email pack gets no registry MCP
    server, so a false match of the words costs the member their MCP tools.
    For an org on ``TOOL_PACKS`` and ``MAF_MCP_ORGS``, a draft that is no
    mail loads no pack and keeps its server. "check my inbox" is the
    non-vacuity case: it loads the pack, and the server goes. Mutation: put
    the bare word "draft" back in the email words → the Notion turn loses
    its server → red."""
    pytest.importorskip("mcp", reason="mcp not installed")
    from orchestrator import _maf_mcp

    from tests.unit._maf_mcp_fakes import RegistrySpy, row, set_flag

    set_flag(monkeypatch, ORG)
    spy = RegistrySpy([row("srv-notion", org=ORG)]).install(monkeypatch)
    attached: list[list[str]] = []

    async def attach(_agent: Any, servers: Any, _opened: Any, **_kw: Any) -> None:
        attached.append([str(getattr(s, "name", s)) for s in servers])

    monkeypatch.setattr(_maf_mcp, "attach_mcp_tools", attach)
    with _tenant():
        applied = await _apply(orchestrator, words)
        await ti._inject_mcp_servers(orchestrator[0], "orchestrator",
                                     organization_id=ORG, opened=[])
    if words == "check my inbox":
        assert applied is not None and spy.reads == 0 and attached == []
    else:
        assert applied is None
        assert spy.reads == 1 and attached == [["srv-notion"]]


def _record(slugs: tuple[str, ...] = ("email", "email-setup")) -> tp.PackRun:
    """The record of a run that loaded *slugs*. The default holds every pack
    of the email assistant, so it refuses a hand-off to it."""
    turn = _turn(turn=1, words="my inbox")
    choice = tp.choose(turn, allowed=ALL, base_tools=40)
    return tp.PackRun.of(turn, choice, [(slug, False) for slug in slugs])


async def test_a_hand_off_to_the_email_assistant_from_the_chat_run_gets_the_pack_refusal(
    store, monkeypatch,
) -> None:
    """The refusal lives in ``tool_packs.delegation_refusal``. Since fix
    round 1 it acts only while the run holds every email pack. The streaming
    seam ``executor._run_sub_agent_streaming`` asks it for each specialist
    tool and ``delegate_to_agent``. ``agent_tools.call_agent`` and each branch
    of ``call_agents_parallel`` ask it first, so the refused hand-off marks no
    chat. The text is the refusal of the OA-5 fixture
    (``evals/one_assistant_measure/pack.py:47-50``). Mutation: drop the
    refusal from the seam → the hand-off starts an email sub-run → red."""
    from evals.one_assistant_measure.pack import REFUSED

    assert tp.PACKS["email"].delegation_refusal == REFUSED
    ran: list[str] = []

    async def fake_run_agent(name: str, payload: dict, **_k: Any) -> dict:
        ran.append(name)
        return {"result": "ran", "tool_calls": []}

    monkeypatch.setattr(ex, "run_agent", fake_run_agent)
    with _tenant(), _bound(**_run_ctx(), **{tp.PACKS_KEY: _record(), tp.PACK_RUN_KEY: "k"}):
        seam = await ex._run_sub_agent_streaming(EA, "Read my mail.", "r-sub", asyncio.Queue())
        direct = await agent_tools.call_agent(EA, "Read my mail.")
        fan = await agent_tools.call_agents_parallel(json.dumps([{"agent": EA, "message": "x"}]))
        state = await eg.session_mark_state()
    assert seam == REFUSED and direct == REFUSED and REFUSED in fan
    assert ran == [], "no email sub-run started"
    assert state == eg.MARK_CLEAR, "a refused hand-off marks no chat"


async def test_a_background_hand_off_keeps_delegation(store, monkeypatch) -> None:
    """§4.3.1: ``call_agent_background`` is not conversational, so OA-10
    keeps it. Mutation: ask the pack rule in ``_delegation_refusal`` → the
    background hand-off is refused → red."""
    ran: list[str] = []

    async def fake_run_agent(name: str, payload: dict, **_k: Any) -> dict:
        ran.append(name)
        return {"result": "ran"}

    monkeypatch.setattr(ex, "run_agent", fake_run_agent)
    with _tenant(), _bound(**_run_ctx(), **{tp.PACKS_KEY: _record(), tp.PACK_RUN_KEY: "k"}):
        out = await agent_tools.call_agent_background(EA, "Sort the old mail.")
        await asyncio.sleep(0.05)
    assert out != tp.PACKS["email"].delegation_refusal and ran == [EA]


async def test_with_email_alone_a_hand_off_to_the_email_assistant_stays_open(
    store, monkeypatch,
) -> None:
    """Fix round 1 (P1-A): the run holds ``email`` and not ``email-setup``, so
    it lacks the setup tools. ``call_agent`` and ``delegate_to_agent`` still
    reach the email assistant. Mutation: refuse the hand-off whenever the
    ``email`` pack is loaded → a rule or a setting has no path → red."""
    ran: list[str] = []

    async def fake_run_agent(name: str, payload: dict, **_k: Any) -> dict:
        ran.append(name)
        return {"result": "Rule made.", "tool_calls": []}

    monkeypatch.setattr(ex, "run_agent", fake_run_agent)
    alone = _record(("email",))
    assert alone.replaced == {EA} and alone.refusals == {}
    with _tenant(), _bound(**_run_ctx(), **{tp.PACKS_KEY: alone, tp.PACK_RUN_KEY: "k"}):
        assert tp.delegation_refusal(EA) is None and ex._pack_delegation_refusal(EA) is None
        out = await agent_tools.call_agent(EA, "Make a rule that files newsletters.")
    assert ran == [EA] and "Rule made." in out
    assert _record().refusals == {EA: tp.PACKS["email"].delegation_refusal}, "non-vacuity"


async def _seed_sticky_email(store: Any) -> None:
    from acb_common.tenant_redis import TenantRedis, key, organization_scope

    with organization_scope(ORG):
        state = key(tp.NAMESPACE, SESSION, tp._member_part(MEMBER))
        await TenantRedis(store).hset(state, "email", json.dumps({"first": [1, 0], "used": 1}))
        await TenantRedis(store).hset(state, "~turn", "1")


@pytest.mark.parametrize("case", ["tool-packs-email-alone", "sticky-email"])
async def test_a_setup_request_beside_the_email_pack_keeps_a_path(
    case: str, store, orchestrator,
) -> None:
    """Fix round 1 (P1-A, 4): two turns that hold ``email`` and no setup tool.
    ``TOOL_PACKS=email`` with a request for an email rule, and a sticky
    ``email`` pack with "make a rule that files newsletters". Each keeps a
    path: the hand-off to the email assistant is open, the rules name it, and
    ``delegate_to_agent`` is on the list. Mutation: refuse the hand-off with
    ``email`` alone, or keep the line "No email agent exists" → red."""
    if case == "sticky-email":
        await _seed_sticky_email(store)
        words, allowed = "make a rule that files newsletters", ALL
    else:
        words, allowed = "Create an email rule that files newsletters", frozenset({"email"})
    with _tenant():
        applied = await _apply(orchestrator, words, allowed=allowed)
        assert applied is not None and applied.record.slugs == ("email",)
        with _bound(**_run_ctx(), **applied.context()):
            refused = ex._pack_delegation_refusal(EA)
    names = set(_names(orchestrator))
    persona = orchestrator[0].default_options["instructions"]
    assert "create_rule" not in names, "non-vacuity: no setup tool in the run"
    assert refused is None and "delegate_to_agent" in names
    assert tp.PACKS["email"].handoff_swap[1] in persona
    assert tp.PACKS["email"].handoff_swap[0] not in persona


async def test_a_run_with_both_email_packs_refuses_the_hand_off_and_hides_the_agent(
    store, orchestrator, monkeypatch,
) -> None:
    """With both packs the run holds every email tool, so a hand-off is
    refused, the rules keep "No email agent exists", and the "Delegatable
    agents" block leaves the email assistant out. With ``email`` alone the
    block still lists it. Mutation: never pass the refused agents to the
    block, or pass every replaced owner → red in one of the two runs."""
    monkeypatch.setattr(ti, "_pack_base_count", lambda *_a, **_k: 40)
    from orchestrator.agents import build_orchestrator_agent

    blocks: dict[str, str] = {}
    with _tenant():
        for label, words in (("both", "Make my email rules stricter"),
                             ("alone", "check my inbox")):
            agents = [build_orchestrator_agent(with_history=False)]
            applied = await _apply(agents, words)
            assert applied is not None
            ti._inject_agent_tools(
                agents, tool_scope=list(CONFIG["tool_scope"]), agent_name="orchestrator",
                agent_config=CONFIG, no_egress=False,
            )
            text = agents[0].default_options["instructions"]
            blocks[label] = text[text.index("## Delegatable agents"):]
            if label == "both":
                assert applied.record.refusals == {EA: tp.PACKS["email"].delegation_refusal}
                assert tp.PACKS["email"].handoff_swap[0] in text
    assert "'email-assistant'" not in blocks["both"]
    assert "'email-assistant'" in blocks["alone"]
    assert "'crm-assistant'" in blocks["both"], "non-vacuity: the block lists the others"


class _StubLoaded:
    def __init__(self, name: str) -> None:
        import tempfile

        self.agent_dir = Path(tempfile.mkdtemp(prefix="oa7c"))
        self.agent_name = name
        self.config: dict[str, Any] = {}

    def build_agents(self) -> list[Any]:
        import types

        return [types.SimpleNamespace(name=self.agent_name)]

    def __enter__(self) -> _StubLoaded:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


async def test_a_delegated_run_never_inherits_the_packs_of_its_parent(store, monkeypatch) -> None:
    """A hand-off to another agent runs as before, and the child holds no run
    key and no record. Mutation: drop ``_without_pack_keys`` from the derive
    of the child → the child's own hand-off to the email assistant gets the
    pack refusal, though it holds no email tool → red."""
    seen: list[dict[str, Any]] = []

    async def fake_run_agent(name: str, payload: dict, **_k: Any) -> dict:
        seen.append(dict(artifact_context()))
        return {"result": "Done.", "tool_calls": []}

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    async def _empty(*_a: Any, **_k: Any) -> str:
        return ""

    monkeypatch.setattr(ex, "run_agent", fake_run_agent)
    monkeypatch.setattr(ex, "load_agent", lambda name, **_k: _StubLoaded(name))
    monkeypatch.setattr(ex, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(ex, "_assert_may_run_agent", _noop)
    monkeypatch.setattr(ex, "_get_current_head", _empty)
    monkeypatch.setattr(ex, "_settle_agent_push", _empty)
    with _tenant(), _bound(**_run_ctx(), **{tp.PACKS_KEY: _record(), tp.PACK_RUN_KEY: "k"}):
        out = await ex._run_sub_agent_streaming(
            "projects-assistant", "List my tasks.", "r-sub", asyncio.Queue())
    assert out == "Done."
    (child,) = seen
    assert tp.PACKS_KEY not in child and tp.PACK_RUN_KEY not in child
    assert tp.delegation_refusal(EA, child) is None


@pytest.mark.parametrize("why", [
    "eligible", "a shared room", "a delegated run", "a batch run", "no card channel",
    "another agent", "no verified member", "another member", "no org", "no chat",
    "flag off", "org not on the list",
])
async def test_only_the_members_own_web_chat_of_the_orchestrator_reads_packs(
    why: str, store, monkeypatch,
) -> None:
    """Packs act only in the stream run of the orchestrator, in the member's
    own chat on the web (§4.2.2). Rooms, the WhatsApp batch path and
    delegated runs see no pack. Mutation: drop one guard of
    ``_start_pack_turn`` → that run reads the sticky state → red for its
    case."""
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "tool_packs", "" if why == "flag off" else "*", raising=False)
    monkeypatch.setattr(settings, "tool_packs_orgs",
                        "org-other" if why == "org not on the list" else "*", raising=False)
    monkeypatch.setattr(ex, "_verified_run_member",
                        lambda: "" if why == "no verified member" else MEMBER)
    kw: dict[str, Any] = {
        "member": MEMBER, "delegated": False, "solo_room": True,
        "batch_thread": False, "shows_cards": True,
    }
    agent, org, chat = "orchestrator", ORG, SESSION
    if why == "a shared room":
        kw["solo_room"] = False
    elif why == "a delegated run":
        kw["delegated"] = True
    elif why == "a batch run":
        kw["batch_thread"] = True
    elif why == "no card channel":
        kw["shows_cards"] = False
    elif why == "another agent":
        agent = EA
    elif why == "another member":
        kw["member"] = "omar@acme.test"
    elif why == "no org":
        org = ""
    elif why == "no chat":
        chat = ""
    task = ex._start_pack_turn(agent, org, chat, **kw)
    if why == "eligible":
        assert task is not None
        turn, allowed = await task
        assert turn.stored and turn.turn == 1 and allowed == ALL
        assert _pack_calls(store), "the eligible run read the state"
    else:
        assert task is None
        assert _pack_calls(store) == []


def _callers_of(name: str) -> set[str]:
    """The functions of ``executor.py`` that call *name*."""
    tree = ast.parse((REPO / "apps/services/orchestrator/orchestrator/executor.py")
                     .read_text(encoding="utf-8"))
    found: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == name):
                    found.add(fn.name)
    return found


def test_only_the_stream_run_starts_and_applies_packs() -> None:
    """The WhatsApp path (D115) and every batch run go through ``run_agent``,
    and a delegated run through ``_run_sub_agent_streaming``. Mutation: call
    the read or the apply on another path → red."""
    assert _callers_of("_start_pack_turn") == {"run_agent_stream"}
    assert _callers_of("_apply_pack_turn") == {"run_agent_stream"}


# ── 6b. One card through the real assembly ──────────────────────────────────


async def test_a_pack_send_in_a_marked_chat_shows_one_card_through_the_real_assembly(
    store, orchestrator, monkeypatch,
) -> None:
    """§4.5: the pack tools go in before ``_inject_agent_tools``, and
    ``_gate_own_maf_tools`` gates each tool with no ``__cc_gated__``. In a
    marked chat a second gate, in the name of the orchestrator, would ask the
    untrusted card too. Mutation: drop ``__cc_gated__`` from the pack tool →
    the assembly gates it again → a second card → red."""
    with _tenant():
        applied = await _apply(orchestrator, "Update my writing style please")
        assert applied is not None and applied.record.slugs == ("email-setup",)
        ti._inject_agent_tools(
            orchestrator, tool_scope=list(CONFIG["tool_scope"]), agent_name="orchestrator",
            agent_config=CONFIG, no_egress=False,
        )
        tool = next(t for t in orchestrator[0].default_options["tools"]
                    if _name(t) == "save_knowledge")
        module = ti._PACK_OWNER_MODULES[EA]
        posts: list[tuple[str, Any]] = []

        async def post(path: str, body: Any) -> Any:
            posts.append((path, body))
            return {"id": "k-1"}

        async def get(path: str, params: Any = None) -> Any:
            if path == "/email/accounts":
                return [{"id": BOX, "email_address": MEMBER, "display_label": "Work"}]
            return {}

        monkeypatch.setattr(module, "_post", post)
        monkeypatch.setattr(module, "_get", get)
        own: list[str] = []
        untrusted: list[str] = []

        async def card(**kw: Any) -> bool:
            own.append(str(kw.get("title")))
            return True

        async def untrusted_card(name: str, _kwargs: Any) -> bool:
            untrusted.append(name)
            return True

        monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
        monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
        monkeypatch.setattr(ti, "_untrusted_answer_card", untrusted_card)
        with _bound(**_run_ctx(), **applied.context()):
            # Another process marked the chat: Redis holds it, this one not.
            await eg.mark_untrusted_answer()
            with eg._MARKED_LOCK:
                eg._MARKED_SESSIONS.clear()
            assert eg.read_untrusted_answer() is False, "this process holds no mark"
            assert any(eg.MARK_NAMESPACE in k for k in store.values), "Redis holds it"
            assert ti._is_egress(tool.func), "non-vacuity: a gate in the run's name would hold it"
            await tool.func(BOX, title="Prices", content="List A")
    assert untrusted == [], untrusted
    assert len(own) == 1, own
    assert [p for p, _ in posts] == ["/email/knowledge"]


# ── 7. The six conditions of OA-7b fix round 1 ──────────────────────────────


async def test_each_run_builds_its_packs_again(store, monkeypatch) -> None:
    """Condition 1: never keep a ``PackBuild`` across runs. Mutation: keep
    the build of a slug for the process → the second run holds the tools of
    the first → red."""
    from orchestrator.agents import build_orchestrator_agent

    routes_agent = pytest.importorskip("gateway.routes.agent")
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _n: frozenset())
    real = ti._pack_tools
    calls: list[str] = []

    async def spy(slug: str, run_ctx: Any, **kw: Any) -> Any:
        calls.append(slug)
        return await real(slug, run_ctx, **kw)

    monkeypatch.setattr(ti, "_pack_tools", spy)
    runs = []
    with _tenant():
        for _ in range(2):
            agents = [build_orchestrator_agent(with_history=False)]
            runs.append((agents, await _apply(agents, "check my inbox")))
    (first_agents, first), (second_agents, second) = runs
    assert calls == ["email", "email"]
    assert first.run_key != second.run_key
    a = {_name(t): t for t in first_agents[0].default_options["tools"]}
    b = {_name(t): t for t in second_agents[0].default_options["tools"]}
    assert a["read_email"].func is not b["read_email"].func
    with _bound(**_run_ctx(), **second.context()):
        out = await a["read_email"].func(email_id="m-1")
    assert out == ti.PACK_CALL_REFUSED.format(tool="read_email")


async def test_a_run_with_no_tenant_loads_no_pack(store, orchestrator) -> None:
    """Condition 2: build the packs after the run binds its tenant. A run
    with no tenant loads no pack, so it holds no send, and a build with no
    tenant is held. Mutation: drop the tenant check → the run holds the
    email pack and its sends → red."""
    names = _names(orchestrator)
    assert await _apply(orchestrator, "check my inbox and send the draft") is None
    assert _names(orchestrator) == names
    build = await ti._pack_tools("email", {**_run_ctx(), tp.PACK_RUN_KEY: "k"})
    assert build.held is True
    assert not {"send_email", "forward_email"} & {_name(t) for t in build.tools}


async def test_the_run_key_comes_from_the_server_not_the_request(store, orchestrator) -> None:
    """Condition 3: key the run check on a value that the server mints. A
    client can set ``run_id`` (``gateway/routes/agent.py``, ``req.run_id``).
    Mutation: take the key from the run context that the caller gave, or key
    on ``run_id`` → a frame with the client's values passes → red."""
    client = {"run_id": "client-run", tp.PACK_RUN_KEY: "client-key"}
    with _tenant():
        applied = await _apply(orchestrator, "check my inbox", run_ctx=_run_ctx(**client))
        assert applied is not None and applied.run_key not in {"client-key", "client-run"}
        assert len(applied.run_key) >= 24
        tool = next(t for t in orchestrator[0].default_options["tools"]
                    if _name(t) == "read_email")
        with _bound(**_run_ctx(**client)):
            out = await tool.func(email_id="m-1")
    assert out == ti.PACK_CALL_REFUSED.format(tool="read_email")


async def test_two_pack_calls_at_once_lose_no_working_set_update(store, monkeypatch) -> None:
    """Condition 4: MAF can run two tool calls at once. Each read the working
    set before the other wrote it, so one update was lost. Mutation: drop the
    lock of the chat in ``_record_pack_call`` → both calls read the empty set,
    and the second write drops the first id → red."""
    from acb_skills import email_chat_context as ecc

    monkeypatch.setattr(ex, "_email_ctx_member", lambda: MEMBER)
    monkeypatch.setattr(ex, "_current_run_org", lambda: ORG)

    async def no_mailboxes(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(ecc, "prime_mailboxes", no_mailboxes)
    real_save = ecc.save_working_set

    async def slow_save(ws: Any, ctx: Any = None) -> bool:
        # The wait sits between the read and the write of one call, so the
        # other call reads before this one writes, unless the lock holds it.
        await asyncio.sleep(0.02)
        return await real_save(ws, ctx)

    monkeypatch.setattr(ecc, "save_working_set", slow_save)
    module = _stub(_probe("read_email"), _probe("edit_draft"))
    with _tenant(), _bound(**_run_ctx(), **{tp.PACK_RUN_KEY: "k-ws"}):
        build = await ti._pack_tools("email", artifact_context(), module=module)
        tools = {t.__name__: t for t in build.tools}
        await asyncio.gather(tools["read_email"](email_id="m-1"),
                             tools["edit_draft"](draft_id="d-1"))
        ws = await ecc.load_working_set()
    assert ws.get("email_id") == "m-1" and ws.get("draft_id") == "d-1", ws


async def test_the_mailbox_event_reaches_a_run_with_no_queue(monkeypatch) -> None:
    """Condition 5: "Now working on <mailbox>" went to the run queue only, so
    a relay-only run lost it. Mutation: emit with ``_emit_to_run`` (the queue
    or nowhere) → the relay gets nothing → red. With a queue the event goes
    on the queue, once."""
    event = {"type": "CUSTOM", "name": "email.mailbox_mark", "value": {"label": "Work"}}
    pushed: list[tuple[str, str]] = []

    async def push(tid: str, line: str) -> None:
        pushed.append((tid, line))

    async def working_set_for(*_a: Any, **_k: Any) -> tuple[dict[str, str], str]:
        return {}, ""

    async def record(_owner: Any, _ctx: Any, _before: Any, _calls: Any, emit: Any) -> None:
        await emit(dict(event))

    monkeypatch.setattr(ex, "_push_sse_to_stream", push)
    monkeypatch.setattr(ex, "_email_working_set_for", working_set_for)
    monkeypatch.setattr(ex, "_record_email_working_set", record)
    relay = ex._stream_relay_thread_id.set(SESSION)
    try:
        await ti._record_pack_call(EA, {"session_id": SESSION}, "read_email", {}, "ok")
        assert [(tid, json.loads(line[len("data: "):])) for tid, line in pushed] == [
            (SESSION, event)]
        queue: asyncio.Queue[Any] = asyncio.Queue()
        token = ex._active_run_queue.set(queue)
        try:
            await ti._record_pack_call(EA, {"session_id": SESSION}, "read_email", {}, "ok")
        finally:
            ex._active_run_queue.reset(token)
    finally:
        ex._stream_relay_thread_id.reset(relay)
    assert queue.get_nowait() == event and len(pushed) == 1


@pytest.mark.parametrize("switch", [False, True], ids=["switch-off", "switch-on"])
def test_the_hold_note_of_a_held_pack_names_no_other_assistant(monkeypatch, switch: bool) -> None:
    """Condition 6: no other assistant called the run, as
    ``EGRESS_HELD_PACK_ANSWER`` says. Mutation: a held pack takes the note of
    a delegated run → "Another assistant called this run" → red."""
    (F.flag_on if switch else F.flag_off)(monkeypatch)
    persona = _agents_mod()._orchestrator_instructions(False)
    text = tp.persona_for(persona, [("email", True)], _bullets())
    note = eg.EGRESS_HOLD_NOTE_PACK_CARDS if switch else eg.EGRESS_HOLD_NOTE_PACK
    assert text.endswith(_alone_rules() + "\n\n" + note + "\n")
    assert "assistant" not in note.lower()
    assert "Another assistant" not in text
    assert eg.egress_hold_note({"agent_name": EA, eg.PACK_CALL_KEY: True}) == note
