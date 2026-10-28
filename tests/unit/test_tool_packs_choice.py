"""WS-67 OA-7c: layer (a) and the sticky packs in a live run.

``project-docs/specs/one_assistant_context.md`` §4.2.2, §4.2.8 (OA-7c) and
the narrowings of §4.5. Layer (a) takes three inputs here: the sticky packs
of the chat, a reference in the member's words, and the words of a pack.
OA-7d adds the fourth input, the surface (section 8). Each case names, in its
docstring, the mutation that turns it red (R7).

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
8. The surface (OA-7d): the app of the dock adds its core pack, and nothing else.

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
#: What ``packs_on`` gives with the flag at ``*`` (OA-8a added ``crm-records``).
EVERY = frozenset(sf.pack_slugs())
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
    assert _bullets() == {
        EA: agents.EMAIL_DELEGATION_BULLETS,
        "projects-assistant": agents.PROJECTS_DELEGATION_BULLETS,
    }


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
        assert turn.stored and turn.turn == 1 and allowed == EVERY
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


def _main_try_of_run_agent_stream() -> ast.Try:
    """The try of ``run_agent_stream`` whose finally cancels the pack read."""
    tree = ast.parse((REPO / "apps/services/orchestrator/orchestrator/executor.py")
                     .read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_agent_stream")
    for node in fn.body:
        if isinstance(node, ast.Try) and "_pack_task" in ast.unparse(ast.Module(
                body=node.finalbody, type_ignores=[])):
            return node
    raise AssertionError("no try of run_agent_stream cancels _pack_task in its finally")


def test_the_pack_read_starts_inside_the_try_whose_finally_cancels_it() -> None:
    """Fix round 2: the read of layer (a) is a task. A task that starts
    before the main try of the run outlives a raise between the two, because
    no finally cancels it. Mutation: start the read before the main try
    again → red."""
    main = _main_try_of_run_agent_stream()
    first = main.body[0]
    assert isinstance(first, ast.Assign) and "_start_pack_turn(" in ast.unparse(first), (
        "the read must be the first statement of the main try")
    tree = ast.parse((REPO / "apps/services/orchestrator/orchestrator/executor.py")
                     .read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_agent_stream")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_start_pack_turn"]
    assert len(calls) == 1, "one read for each run, and only inside the main try"


async def test_a_raise_before_the_main_try_leaves_no_pack_read_running(
    store, monkeypatch,
) -> None:
    """Fix round 2, the behaviour: an eligible run with the flag on raises
    before its main try. No read of the sticky packs may keep running.
    Mutation: start the read before ``_bind_solo_room`` again → the read
    task is still pending after the run ended → red."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "tool_packs", "*", raising=False)
    monkeypatch.setattr(get_settings(), "tool_packs_orgs", "*", raising=False)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    started: list[Any] = []
    real_start = ex._start_pack_turn

    def spy(*a: Any, **k: Any) -> Any:
        task = real_start(*a, **k)
        started.append(task)
        return task

    async def slow_read(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(30)

    def early_raise(_solo: Any) -> Any:
        raise RuntimeError("a raise before the main try")

    monkeypatch.setattr(ex, "_start_pack_turn", spy)
    monkeypatch.setattr(tp, "begin_turn", slow_read)
    monkeypatch.setattr(ex, "_bind_solo_room", early_raise)
    with pytest.raises(RuntimeError, match="before the main try"):
        async for _line in ex.run_agent_stream(
            "orchestrator", {"message": "check my inbox"}, run_id="r-early",
            thread_id=SESSION, organization_id=ORG, session_user=MEMBER,
            shows_cards=True, solo_room=True,
        ):
            pass
    await asyncio.sleep(0)
    assert all(task is None or task.done() for task in started), started


def test_the_orchestrator_has_no_own_tool_scope() -> None:
    """Fix round 2, a latent hazard: ``_apply_pack_turn`` runs before
    ``_apply_own_tool_scope``. An ``own_tool_scope`` on the orchestrator would
    strip each pack tool, because no pack tool is in that list. Mutation: add
    an ``own_tool_scope`` to the orchestrator's ``config.json`` → red, so the
    change must move the apply after ``_apply_own_tool_scope`` too."""
    assert "own_tool_scope" not in CONFIG


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


# ── 8. The surface (OA-7d) ──────────────────────────────────────────────────

GATEWAY_ROUTE = REPO / "apps/services/gateway/gateway/routes/agent.py"


def test_a_turn_from_email_loads_the_email_pack_with_no_email_words() -> None:
    """Mutation: ``choose`` ignores ``turn.surface`` -> the pack list is empty
    and the layer is missing -> red."""
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 1, (), "What is the weather?",
                       True, surface="email")
    choice = tp.choose(turn, allowed=ALL, base_tools=40)
    assert choice.slugs == ("email",) and choice.layers == {"email": "surface"}
    assert tp.matched_layer("email", turn.words) is None, "the words load nothing"


def test_the_surface_adds_the_core_pack_and_nothing_else() -> None:
    """The setup pack, the other pack of the owner, stays out of an email
    turn. Mutation: the surface loads each pack of its owner, or the setup pack
    names ``surface="email"`` too -> ``email-setup`` loads -> red."""
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 1, (), "", True, surface="email")
    assert tp.choose(turn, allowed=ALL, base_tools=40).slugs == ("email",)
    # OA-8b: the Projects core pack serves three apps over one task store.
    assert tp.surfaces() == {"email", "crm", "projects", "tasks", "calendar"}
    assert tp.PACKS["email-setup"].surface == ""


def test_the_surface_adds_no_word_match_and_keeps_the_sticky_order() -> None:
    """A sticky pack keeps its place, and the surface adds its pack once
    after it. Mutation: the surface pack goes before the sticky packs, or
    loads twice -> the order differs -> red."""
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 4,
                       (_st("email-setup", 1, 3),), "", True, surface="email")
    choice = tp.choose(turn, allowed=ALL, base_tools=40)
    assert choice.slugs == ("email-setup", "email")
    assert choice.layers == {"email-setup": "sticky", "email": "surface"}


def test_the_flag_still_decides_what_a_surface_adds() -> None:
    """A forged or honest surface adds only a pack that the flag allows.
    Mutation: the surface bypasses ``allowed`` -> a pack loads with the flag
    off -> red."""
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 1, (), "", True, surface="email")
    assert tp.choose(turn, allowed=frozenset(), base_tools=40).slugs == ()
    assert tp.choose(turn, allowed={"email-setup"}, base_tools=40).slugs == ()


@pytest.mark.parametrize("forged", [
    "billing", "email-setup", "EMAIL/../", "em\nail", "x" * 400, "", " ", "..", "email;drop",
    None, 5, ["email"], {"email": 1}, b"email",
])
def test_an_unknown_or_forged_surface_is_ignored(forged: Any) -> None:
    """The check is an allow-list of the slugs that name a core pack, with a
    bound on the length. Mutation: ``clean_surface`` returns its input, or
    drops the allow-list, or drops the length bound -> a junk value reaches
    the turn -> red."""
    assert tp.clean_surface(forged) == ""
    with tp.surface_scope(forged):
        assert tp.current_surface() == ""
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 1, (), "", True,
                       surface=forged)  # type: ignore[arg-type]
    assert tp.choose(turn, allowed=ALL, base_tools=40).slugs == ()


def test_a_surface_is_cleaned_in_case_and_space() -> None:
    """Mutation: compare the raw value -> "Email " loads nothing although the
    dock meant email -> red. The case is a courtesy, never an extra pack."""
    assert tp.clean_surface("  Email ") == "email"
    assert len("x" * (tp.SURFACE_MAX + 1)) > tp.SURFACE_MAX
    assert tp.clean_surface("email" + "a" * tp.SURFACE_MAX) == ""


def test_the_surface_scope_restores_the_value_it_found() -> None:
    """Mutation: the scope never resets the variable -> the surface of one
    turn reaches the next run of the same context -> red."""
    assert tp.current_surface() == ""
    with tp.surface_scope("email"):
        assert tp.current_surface() == "email"
        with tp.surface_scope("billing"):
            assert tp.current_surface() == ""
        assert tp.current_surface() == "email"
    assert tp.current_surface() == ""


async def test_a_surface_turn_holds_the_email_tools_with_no_email_words(
    store, orchestrator,
) -> None:
    """Through the real assembly: the same words with no surface load no pack,
    and with ``email`` they load the email pack. Mutation: ``begin_turn``
    drops the surface -> the tools never appear -> red."""
    assert "email_assistant" in _names(orchestrator), "non-vacuity"
    words = "Please tidy up my tasks for the week"
    with _tenant():
        none = await _apply(orchestrator, words)
    assert none is None or none.record.slugs == ()
    assert "read_email" not in _names(orchestrator)
    with _tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, words, "email")
        applied = await ti._apply_tool_packs(
            orchestrator, turn, _run_ctx(), allowed=ALL, agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG, bullets=_bullets())
    names = set(_names(orchestrator))
    assert applied is not None and applied.record.slugs == ("email",)
    assert names >= EMAIL_TOOLS and "email_assistant" not in names and not SETUP_TOOLS & names


async def test_begin_turn_cleans_the_surface_again(store) -> None:
    """Mutation: ``begin_turn`` keeps the raw value -> a forged value reaches
    ``PackTurn.surface`` -> red."""
    with _tenant():
        forged = await tp.begin_turn(ORG, SESSION, MEMBER, "", "billing")
        honest = await tp.begin_turn(ORG, SESSION, MEMBER, "", "email")
    assert forged.surface == "" and honest.surface == "email"


@pytest.mark.parametrize("why", [
    "eligible", "flag off", "a shared room", "a delegated run", "a batch run",
    "no card channel", "another agent", "another member",
])
async def test_the_surface_changes_no_guard_of_the_pack_read(
    why: str, store, monkeypatch,
) -> None:
    """A bound surface does not lift a guard of ``_start_pack_turn``. A room,
    the WhatsApp batch path, a delegated run and a flag-off box load no pack,
    and the flag-off box reads no state. Mutation: a surface skips one guard
    -> that run reads the state -> red for its case. The eligible case carries
    the surface into the turn, so it fails if the executor drops it."""
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "tool_packs", "" if why == "flag off" else "*", raising=False)
    monkeypatch.setattr(settings, "tool_packs_orgs", "*", raising=False)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    kw: dict[str, Any] = {
        "member": MEMBER, "delegated": why == "a delegated run",
        "solo_room": why != "a shared room", "batch_thread": why == "a batch run",
        "shows_cards": why != "no card channel",
    }
    if why == "another member":
        kw["member"] = "omar@acme.test"
    agent = EA if why == "another agent" else "orchestrator"
    with tp.surface_scope("email"):
        task = ex._start_pack_turn(agent, ORG, SESSION, **kw)
        if why == "eligible":
            assert task is not None
            turn, allowed = await task
            assert turn.surface == "email" and allowed == EVERY
        else:
            assert task is None
            assert _pack_calls(store) == []


def test_the_forged_surface_variable_is_cleaned_before_the_turn(store, monkeypatch) -> None:
    """A value set straight on the variable (a bug elsewhere) still passes the
    allow-list in ``current_surface``. Mutation: ``current_surface`` returns the
    raw variable -> "billing" reaches the turn -> red."""
    token = tp.SURFACE.set("billing")
    try:
        assert tp.current_surface() == ""
    finally:
        tp.SURFACE.reset(token)


def test_the_gateway_pops_the_surface_and_binds_it_with_the_typed_words() -> None:
    """The route takes ``surface`` out of the payload, so it goes nowhere
    else, and binds it for the run in the same ``with`` as the typed words.
    The value is never read for a tenant or a member. Mutation: leave the
    key in the payload, or drop the binding, or bind it outside the run ->
    red."""
    text = GATEWAY_ROUTE.read_text(encoding="utf-8")
    assert '_surface = req.payload.pop("surface", None)' in text
    assert "with member_typed(_typed), surface_scope(_surface):" in text
    assert text.count("_surface") == 2, "popped once and bound once"
    assert "payload.get(\"surface\")" not in text and "payload[\"surface\"]" not in text


def test_only_the_pack_read_reads_the_surface() -> None:
    """The executor reads ``current_surface`` in ``_start_pack_turn`` and
    nowhere else, after the flag check. Mutation: read it before ``packs_on``
    or in another function -> red."""
    text = (REPO / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    assert text.count("current_surface()") == 1
    start = text.index("def _start_pack_turn(")
    body = text[start:text.index("async def _apply_pack_turn(")]
    assert body.index("packs_on(organization_id)") < body.index("current_surface()")


# ── 9. The CRM pack (WS-67 OA-8a, §4.3.2) ───────────────────────────────────

CRM = "crm-assistant"
CRM_ONLY = frozenset({"crm-records"})


@pytest.mark.parametrize(("words", "loads"), [
    ("How is the CRM looking?", True),
    ("Any new leads from Zoho this week?", True),
    ("What is in the sales pipeline?", True),
    ("Show deals in the deal pipeline", True),
    ("Create a lead for Acme", True),
    ("Please convert the lead to a deal", True),
    ("log a call with Ravi", True),
    ("I need the lost reason", True),
    ("Which pipeline stage is Acme in?", True),
    ("Draft the PRD in Notion", False),
    ("fix the CI pipeline", False),
    ("lead the standup", False),
    ("What stage is the project in?", False),
    ("Close the deal on the contract", False),
    ("Show my crmsync notes", False),
    ("What is the weather in Pune?", False),
])
def test_the_crm_words_load_the_pack_as_whole_words(words: str, loads: bool) -> None:
    """Whole words only. Bare "pipeline", "deal", "lead" and "stage" are out.
    Mutation: add a bare "pipeline" or "lead" to the words -> "fix the CI
    pipeline" or "lead the standup" loads the pack -> red."""
    got = tp.choose(_turn(words=words), allowed=CRM_ONLY, base_tools=55).slugs
    assert got == (("crm-records",) if loads else ())


def test_the_crm_pack_words_leave_out_the_bare_words() -> None:
    """Mutation: a bare word joins the list -> red."""
    words = set(tp.PACKS["crm-records"].words)
    assert not words & {"pipeline", "deal", "lead", "stage"}
    assert {"crm", "zoho", "deals", "leads"} <= words


def test_the_crm_pack_is_last_in_the_order_so_the_cap_drops_it_first() -> None:
    """Mutation: put ``crm-records`` before an email pack in ``PACKS`` -> an
    email pack leaves first -> red."""
    # OA-8b: the four Projects packs follow it, so the order ends with them.
    assert list(tp.PACKS)[-5:] == [
        "crm-records", "projects", "projects-plan", "projects-structure", "projects-bulk"]
    assert list(tp.PACKS).index("crm-records") > list(tp.PACKS).index("email-setup")
    # Email 31 and CRM 9 beside a base of 75 is over 110: the CRM pack leaves,
    # and the email pack stays.
    words = "check my inbox and the crm deals"
    cut = tp.choose(_turn(words=words), allowed=EVERY, base_tools=75)
    assert cut.slugs == ("email",) and cut.skipped == ("crm-records",)
    # Under the cap both load.
    fits = tp.choose(_turn(words=words), allowed=EVERY, base_tools=55)
    assert fits.slugs == ("email", "crm-records") and fits.skipped == ()
    # All three packs are over 110 at the measured base. The CRM pack leaves first.
    all3 = tp.choose(_turn(words="email rules, the inbox and the crm"), allowed=EVERY,
                     base_tools=55)
    assert all3.skipped[0] == "crm-records" and "crm-records" not in all3.slugs


async def test_the_three_packs_are_over_the_cap_at_the_measured_base(orchestrator) -> None:
    """The base is measured, not assumed. This pins the numbers that the order
    rule above relies on."""
    base = _measured_base(orchestrator)
    n = {slug: len(sf.SKILL_FAMILIES[slug]["tools"]) for slug in sf.pack_slugs()}
    assert base + n["email"] + n["crm-records"] <= tp.MAX_REQUEST_TOOLS
    assert base + sum(n.values()) > tp.MAX_REQUEST_TOOLS


def test_the_crm_persona_swaps_nothing_and_adds_the_rules() -> None:
    """The orchestrator has no CRM text, so ``PACK_DELEGATION_BULLETS`` holds
    no ``crm-assistant`` and the swap is skipped. The base persona stays
    byte-identical before the rules. Mutation: raise for an owner with no
    bullets -> ``persona_for`` raises -> red."""
    persona = _agents_mod()._orchestrator_instructions(False)
    assert CRM not in _bullets() and EA in _bullets()
    text = tp.persona_for(persona, [("crm-records", False)], _bullets())
    assert text.startswith(persona.rstrip("\n") + "\n\n")
    assert "CRM TOOLS." in text and "No CRM agent exists to hand a task to." in text
    assert _agents_mod().EMAIL_DELEGATION_BULLETS in text, "the email text is not touched"
    assert text.count("CRM TOOLS.") == 1


def test_an_owner_that_is_present_and_moved_still_raises() -> None:
    """The skip is for an owner ABSENT from the bullets. Email, present in
    ``PACK_DELEGATION_BULLETS``, still raises when its text is gone, and so
    does any other owner that the bullets name. Mutation: skip on a missing
    text instead of a missing owner -> red."""
    persona = _agents_mod()._orchestrator_instructions(False)
    moved = persona.replace(_agents_mod().EMAIL_DELEGATION_BULLETS, "", 1)
    with pytest.raises(RuntimeError):
        tp.persona_for(moved, [("email", False)], _bullets())
    with pytest.raises(RuntimeError):
        tp.persona_for("A persona.\n", [("crm-records", False)], {CRM: "old CRM text"})


def test_the_crm_pack_refusal_and_surface() -> None:
    """Mutation: drop the refusal text, so the hand-off is never refused, or
    the surface, so a /crm turn loads nothing."""
    found = tp.PACKS["crm-records"]
    assert found.owner == CRM and found.surface == "crm"
    assert "No CRM agent exists" in found.delegation_refusal
    assert tp.owner_packs(CRM) == ("crm-records",)
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 1, (), "", True, surface="crm")
    choice = tp.choose(turn, allowed=EVERY, base_tools=55)
    assert choice.slugs == ("crm-records",)
    run = tp.PackRun.of(turn, choice, [("crm-records", False)])
    assert run.refusal(CRM) == found.delegation_refusal
    assert run.refusal("projects-assistant") is None


def test_the_crm_rules_carry_the_specialist_rules_and_no_email_line() -> None:
    """The orchestrator has no CRM persona text, so the rules carry the useful
    rules of ``agent-crm/instructions.md``. Mutation: drop one -> red."""
    rules = tp.rules_for("crm-records")
    for needle in ("Mirror mode", '"Refused:"', "Search with search_crm before create_lead",
                   "by name", "Never invent", "data that other people wrote",
                   "in digits", "Never name a tool", "No CRM agent exists"):
        assert needle in rules, needle
    assert "call_agent" not in rules and "WhatsApp" not in rules
    assert len(rules) < 3300


async def test_a_crm_turn_holds_the_crm_pack_and_no_crm_assistant_tool(
    store, orchestrator, monkeypatch,
) -> None:
    """Through the real orchestrator: the nine tools join, the ``crm_assistant``
    tool leaves, and a hand-off to the CRM assistant gets the pack refusal.
    Mutation: keep the specialist beside the pack -> red."""
    async def crm_mode() -> str:
        return "live"

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", crm_mode)
    assert "crm_assistant" in _names(orchestrator), "non-vacuity"
    with _tenant():
        applied = await _apply(orchestrator, "Show me my deals in the CRM", allowed=EVERY)
    names = set(_names(orchestrator))
    assert applied is not None and applied.record.slugs == ("crm-records",)
    assert "crm_assistant" not in names
    assert names >= set(sf.SKILL_FAMILIES["crm-records"]["tools"])
    persona = orchestrator[0].default_options["instructions"]
    assert "CRM TOOLS." in persona and _agents_mod().EMAIL_DELEGATION_BULLETS in persona
    assert applied.record.replaced == {CRM}
    assert applied.record.refusals == {CRM: tp.PACKS["crm-records"].delegation_refusal}
    assert "email_assistant" in names, "the email specialist is not touched"


async def _crm_write_through_the_assembly(orchestrator, monkeypatch, *, mark: bool):
    """Build the CRM pack on the real orchestrator, run the real injection,
    and call ``create_lead``. Returns the cards, the posts, the answer and the
    count of permission-gate decisions."""
    async def crm_mode() -> str:
        return "live"

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", crm_mode)
    decisions: list[str] = []
    real_gate = ti._permission_gate

    def counting_gate(name: str, *args: Any, **kwargs: Any) -> Any:
        decisions.append(name)
        return real_gate(name, *args, **kwargs)

    monkeypatch.setattr(ti, "_permission_gate", counting_gate)
    own: list[str] = []
    untrusted: list[str] = []
    posts: list[tuple[str, Any]] = []

    async def post(path: str, body: Any) -> Any:
        posts.append((path, body))
        return {"id": "l-1", "lead_name": "Acme", "owner_email": MEMBER}

    async def card(**kw: Any) -> bool:
        own.append(str(kw.get("title")))
        return True

    async def untrusted_card(name: str, _kwargs: Any) -> bool:
        untrusted.append(name)
        return True

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    monkeypatch.setattr(ti, "_untrusted_answer_card", untrusted_card)
    with _tenant():
        applied = await _apply(orchestrator, "Create a lead for Acme in the CRM", allowed=EVERY)
        assert applied is not None and applied.record.slugs == ("crm-records",)
        ti._inject_agent_tools(
            orchestrator, tool_scope=list(CONFIG["tool_scope"]), agent_name="orchestrator",
            agent_config=CONFIG, no_egress=False,
        )
        tool = next(t for t in orchestrator[0].default_options["tools"]
                    if _name(t) == "create_lead")
        monkeypatch.setattr(ti._PACK_OWNER_MODULES[CRM], "_post", post)
        assert ti._is_egress(tool.func), "non-vacuity: an egress write"
        assert getattr(tool.func, "__cc_gated__", False) is True
        with _bound(**_run_ctx(), **applied.context()):
            if mark:  # this process read mail in the turn, after the pack was built
                await eg.mark_untrusted_answer()
            answer = await tool.func("Acme")
    return own, untrusted, posts, str(answer), decisions


async def test_a_crm_write_through_the_real_assembly_shows_one_card_and_one_gate(
    store, orchestrator, monkeypatch,
) -> None:
    """The pack tool carries ``__cc_gated__``, so ``_gate_own_maf_tools`` gates
    it no second time: one permission decision, one card, one post. Mutation:
    drop ``__cc_gated__`` from the pack tool -> the assembly gates it again ->
    two decisions -> red."""
    own, untrusted, posts, _answer, decisions = await _crm_write_through_the_assembly(
        orchestrator, monkeypatch, mark=False)
    assert untrusted == [] and len(own) == 1 and "CRM lead" in own[0], (own, untrusted)
    assert [p for p, _ in posts] == ["/crm/leads"]
    assert decisions == ["create_lead"], decisions


async def test_a_crm_write_after_mail_in_the_same_turn_is_held_and_shows_no_card(
    store, orchestrator, monkeypatch,
) -> None:
    """The chat read mail after the pack was built (the pack builds open). The
    call answers the held text: no card of any kind, and no post. A chat that
    was marked BEFORE the build gets a held pack with no write tool
    (``test_pack_guards.py``). Mutation: drop the in-turn hold from
    ``_pack_call`` -> the write runs -> red."""
    own, untrusted, posts, answer, _decisions = await _crm_write_through_the_assembly(
        orchestrator, monkeypatch, mark=True)
    assert own == [] and untrusted == [] and posts == []
    assert answer == eg.EGRESS_HELD_PACK_ANSWER_OTHER.format(name="create_lead"), answer


# ── 10. OA-8a fix round 1: the reason of a held CRM pack, the persona set ───


async def _mark_chat_as_mail_read() -> None:
    """The chat read mail on an earlier turn: Redis and this process hold the mark."""
    with _tenant(), _bound(**_run_ctx()):
        await eg.mark_untrusted_answer()


async def test_a_held_crm_pack_in_a_mail_chat_gives_the_mail_reason_in_the_persona(
    store, orchestrator, monkeypatch,
) -> None:
    """The chat read mail, so the CRM pack builds held. The persona says why:
    mail from outside senders, never "a sandboxed turn". With the ``email``
    pack held too, no note of the persona gives the sandbox reason. Mutation:
    take the session out of ``_hold_note`` -> the sandbox note -> red."""
    async def crm_mode() -> str:
        return "live"

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", crm_mode)
    await _mark_chat_as_mail_read()
    with _tenant():
        applied = await _apply(orchestrator, "show my crm deals and check my inbox", allowed=EVERY)
    assert applied is not None and set(applied.record.slugs) == {"crm-records", "email"}
    assert applied.record.held == {"crm-records": True, "email": True}
    persona = orchestrator[0].default_options["instructions"]
    assert eg.EGRESS_HOLD_NOTE_SANDBOX not in persona
    assert eg.EGRESS_HOLD_NOTE_PACK_OTHER in persona and eg.EGRESS_HOLD_NOTE_PACK in persona
    assert "create_lead" not in set(_names(orchestrator))


async def test_a_held_crm_pack_alone_gives_the_mail_reason_and_no_email_talk(
    store, orchestrator, monkeypatch,
) -> None:
    """The CRM note talks about the CRM pack, not about email tools. Mutation:
    use the email note for every owner -> ``send_draft`` in the CRM persona ->
    red."""
    async def crm_mode() -> str:
        return "live"

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", crm_mode)
    await _mark_chat_as_mail_read()
    with _tenant():
        applied = await _apply(orchestrator, "show my crm deals", allowed=EVERY)
    assert applied is not None and applied.record.slugs == ("crm-records",)
    persona = orchestrator[0].default_options["instructions"]
    block = persona[persona.index("CRM TOOLS."):]
    assert eg.EGRESS_HOLD_NOTE_PACK_OTHER in block and eg.EGRESS_HOLD_NOTE_SANDBOX not in block
    assert "send_draft" not in block and "schedule_send" not in block


async def test_a_covered_run_keeps_the_sandbox_reason_for_a_held_crm_pack(
    store, orchestrator, monkeypatch,
) -> None:
    """A chat that read no mail but is held by a covered run (H-236) keeps the
    sandbox reason. The fix reads the real session, and invents no mail reason.
    Mutation: always answer the mail note -> red."""
    async def crm_mode() -> str:
        return "live"

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", crm_mode)
    with _tenant():
        applied = await _apply(
            orchestrator, "show my crm deals", allowed=EVERY,
            run_ctx=_run_ctx(no_egress=True))
    assert applied is not None and applied.record.held == {"crm-records": True}
    persona = orchestrator[0].default_options["instructions"]
    assert eg.EGRESS_HOLD_NOTE_SANDBOX in persona
    assert eg.EGRESS_HOLD_NOTE_PACK_OTHER not in persona


async def test_the_held_answer_of_a_crm_write_names_the_tool_and_a_next_step(store) -> None:
    """The in-turn answer of a held CRM write names the held tool, says why, and
    gives a next step that fits a write. It talks of no email tool. The email
    owner keeps the email wording. Mutation: fall back to the email text for
    every owner -> ``send_draft`` in the CRM answer -> red."""
    await _mark_chat_as_mail_read()
    crm = {"agent_name": CRM, eg.PACK_CALL_KEY: True, "session_id": SESSION}
    email = {"agent_name": EA, eg.PACK_CALL_KEY: True, "session_id": SESSION}
    with _tenant():
        answer = eg.egress_held_answer("update_deal_status", crm)
        mail = eg.egress_held_answer("send_email", email)
    assert answer == eg.EGRESS_HELD_PACK_ANSWER_OTHER.format(name="update_deal_status")
    assert answer.startswith("update_deal_status is off now.")
    assert "new chat" in answer and "send_draft" not in answer and "mailbox" not in answer
    assert "send_draft" in mail, "the email wording stays for an email tool"


def test_a_missing_persona_bullet_raises_unless_the_owner_has_none_by_design(
    monkeypatch,
) -> None:
    """``NO_PERSONA_BULLETS`` names the owners with no persona text. Any other
    owner that a pack replaces and that has no bullet raises. Mutation: skip
    the swap for any absent owner (the old rule) -> the second case does not
    raise -> red. Mutation: drop ``crm-assistant`` from the set -> the first
    case raises -> red."""
    # OA-8b: the Projects assistant joins it. No bullet joins PACK_DELEGATION_BULLETS.
    assert {CRM} == tp.NO_PERSONA_BULLETS
    assert set(_bullets()) == {EA, "projects-assistant"}
    persona = _agents_mod()._orchestrator_instructions(False)
    text = tp.persona_for(persona, [("crm-records", False)], _bullets())
    assert text.startswith(persona.rstrip("\n") + "\n\n")
    with pytest.raises(RuntimeError, match="email-assistant"):
        tp.persona_for(persona, [("email", False)], {})
    _fake_packs(monkeypatch, {"x-pack": 3})
    monkeypatch.setitem(tp.PACKS, "x-pack", tp.Pack(
        slug="x-pack", rules="X RULES", words=("xword",), delegation_refusal="Not run."))
    with pytest.raises(RuntimeError, match="x-agent"):
        tp.persona_for(persona, [("x-pack", False)], _bullets())
