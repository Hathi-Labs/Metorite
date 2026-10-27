"""WS-67 OA-7e: layer (b), the meta tool ``load_skill_tools``.

``project-docs/specs/one_assistant_context.md`` §4.2.2 (layer (b)), §4.2.8
(OA-7e) and the "OA-7e facts" of §4.5. A run that layer (a) serves holds
``load_skill_tools`` for each allowed pack that it does not hold. A call adds
the tools of one pack to the live tool list of the run through MAF's
``FunctionInvocationContext.add_tools``, and the model calls them on its next
request. Each case names, in its docstring, the mutation that turns it red
(R7).

1. The pin: MAF keeps ``add_tools``, with the two errors of the facts.
2. The middleware: the same guarded builder, the run check, the caps, the
   no-op of a pack loaded already, the MCP rule, the held answer, sticky on
   load, and the hand-off rule when a load completes the packs of an owner.
3. The offer: only the allowed packs, only in the run that layer (a) serves,
   on a per-run view.
4. The acceptance, in a real stream run with a scripted model
   (``tests/unit/_native_maf_harness.py``): the run loads the ``email`` pack
   and then calls ``compose_reply``, which runs with the held answer of the
   pack. A load past the cap refuses and names ``spawn_subagents``.

Run: ``uv run pytest tests/unit/test_load_skill_tools.py -q``.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import json
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_common.assistant_profile import member_typed
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
from agent_framework import FunctionInvocationContext, tool
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit._native_maf_harness import (
    ScriptedModel,
    load_agent_module,
    parse_frames,
    text_turn,
    tool_turn,
)

REPO = Path(__file__).resolve().parents[2]
ORCH_DIR = "apps/agents/agent-orchestrator"
EA_DIR = "apps/agents/agent-email-assistant"
CONFIG = json.loads((REPO / ORCH_DIR / "config.json").read_text(encoding="utf-8"))
EA = "email-assistant"
ORG = "0a0a0a0a-0000-4000-8000-0000000000ee"
MEMBER = "jane@acme.test"
SESSION = "chat-oa7e-1"
ALL = frozenset({"email", "email-setup"})
EMAIL_TOOLS = tuple(sf.SKILL_FAMILIES["email"]["tools"])
SETUP_TOOLS = tuple(sf.SKILL_FAMILIES["email-setup"]["tools"])
#: Words with no email word, so layer (a) loads no pack. Under 12 words.
WORDS = "Tell Anil that we pay on Friday"

_READ_ONLY = {"read_only": True, "destructive": False, "idempotent": True, "open_world": False}


@pytest.fixture
def store(monkeypatch) -> Any:
    """A fresh in-memory tenant Redis for the marks and the sticky packs."""
    return install_memory_mark_store(monkeypatch)


@contextlib.contextmanager
def _tenant(org: str = ORG) -> Iterator[None]:
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _run_ctx(**over: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "run_id": "r-oa7e", "session_id": SESSION, "agent_name": "orchestrator",
        "member": MEMBER, "batch_thread": False, "no_egress": False,
        "workspace_root": "/tmp/orch-oa7e",
    }
    values.update(over)
    return values


@contextlib.contextmanager
def _bound(**values: Any) -> Iterator[None]:
    with artifact_context_scope():
        bind_artifact_context(**values)
        yield


def _name(item: Any) -> str:
    return ti._tool_name(item)


def _names(items: list[Any]) -> list[str]:
    return [_name(t) for t in items]


def _probe(name: str, seen: list[dict[str, Any]] | None = None) -> Any:
    """A read-only tool of a pack. It records the run context of its body."""

    async def probe(email_id: str = "", instruction: str = "", draft: str = "") -> str:
        if seen is not None:
            ctx = artifact_context()
            seen.append({
                "tool": name,
                "agent_name": ctx.get("agent_name"),
                "pack_call": ctx.get(eg.PACK_CALL_KEY),
                "no_egress": eg.no_egress_for_this_run(),
            })
        return f"{name} done"

    probe.__name__ = name
    probe.__tool_risk__ = dict(_READ_ONLY)  # type: ignore[attr-defined]
    return probe


def _stub_owner(names: tuple[str, ...], seen: list[dict[str, Any]] | None = None) -> Any:
    """An owner module whose ``_agent_tools`` builds a probe for each name."""
    return types.SimpleNamespace(_agent_tools=lambda: [_probe(n, seen) for n in names])


def _record(slugs: tuple[str, ...] = (), turn: int = 3) -> tp.PackRun:
    """The record of a run whose layer (a) loaded *slugs*."""
    t = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), turn, (), "", True)
    choice = tp.choose(t, allowed=frozenset(), base_tools=0)
    return tp.PackRun.of(t, choice, [(slug, False) for slug in slugs])


def _dummies(count: int, prefix: str = "core") -> list[Any]:
    """*count* tools of the run, with names that no pack holds."""

    def make(i: int) -> Any:
        async def fn() -> str:
            return "ok"

        return tool(fn, name=f"{prefix}_{i}", approval_mode="never_require")

    return [make(i) for i in range(count)]


def _loader(record: tp.PackRun, offered: tuple[str, ...] = ("email", "email-setup"),
            build: Any = None) -> tp.LoadSkillTools:
    return tp.LoadSkillTools(offered=offered, run_key="key-oa7e", record=record,
                             build=build or ti._build_pack_for_load)


async def _call(loader: tp.LoadSkillTools, slug: str, live: list[Any] | None) -> tuple[str, bool]:
    """One call of ``load_skill_tools`` through the middleware. Returns the
    answer and whether the call went on to the tool body."""
    passed: list[bool] = []

    async def call_next() -> None:
        passed.append(True)

    context = FunctionInvocationContext(
        function=loader.tool, arguments={"pack": slug}, tools=live)
    await loader.process(context, call_next)
    return str(context.result), bool(passed)


@contextlib.contextmanager
def _in_run(loader: tp.LoadSkillTools, **over: Any) -> Iterator[None]:
    """The frame of the run that offered *loader*: the tenant, the run key
    and the record that the executor binds."""
    values = {**_run_ctx(), tp.PACK_RUN_KEY: loader.run_key, tp.PACKS_KEY: loader.record}
    values.update(over)
    with _tenant(), _bound(**values):
        yield


def _stub_email(monkeypatch, seen: list[dict[str, Any]] | None = None) -> None:
    monkeypatch.setitem(ti._PACK_OWNER_MODULES, EA, _stub_owner(EMAIL_TOOLS + SETUP_TOOLS, seen))


def _held_card_off(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "email_held_card_send", False)


# ── 1. The pin of the MAF hook ──────────────────────────────────────────────


def test_maf_keeps_add_tools_with_the_errors_of_the_facts() -> None:
    """§4.5 "OA-7e facts". Mutation: MAF removes or renames
    ``FunctionInvocationContext.add_tools``, changes its parameter, or stops
    raising ``ValueError`` for a new object of a name in the list and
    ``RuntimeError`` with no live list → red. A same object is a no-op, and a
    raise leaves the live list as it was."""
    from agent_framework import ExperimentalFeature

    add = getattr(FunctionInvocationContext, "add_tools", None)
    assert callable(add), "MAF removed or renamed add_tools"
    assert list(inspect.signature(add).parameters) == ["self", "tools"]
    assert getattr(add, "__feature_id__", None) == ExperimentalFeature.PROGRESSIVE_TOOLS.value
    first, other = _dummies(1, "pin")[0], _dummies(1, "pin")[0]
    live = [first]
    ctx = FunctionInvocationContext(function=first, arguments={}, tools=live)
    ctx.add_tools([first])
    assert live == [first], "the same object is a no-op"
    with pytest.raises(ValueError):
        ctx.add_tools([*_dummies(1, "new"), other])
    assert live == [first], "a raise adds nothing"
    with pytest.raises(RuntimeError):
        FunctionInvocationContext(function=first, arguments={}, tools=None).add_tools([other])


# ── 2. The middleware ───────────────────────────────────────────────────────


async def test_a_load_adds_the_pack_through_the_one_guarded_builder(store, monkeypatch) -> None:
    """The tools come from ``_tool_injection._pack_tools``, with the run
    context of the call and the names of the live list. Mutation: build the
    pack from ``tool_packs.owner_tools`` alone, or with no ``holds`` → the
    tools are the owner's bare functions, or the builder never sees the live
    names → red."""
    _stub_email(monkeypatch)
    calls: list[tuple[str, str, frozenset[str]]] = []
    real = ti._pack_tools

    async def spy(slug: str, run_ctx: Any, **kw: Any) -> Any:
        calls.append((slug, str(run_ctx.get(tp.PACK_RUN_KEY)), frozenset(kw.get("holds") or ())))
        return await real(slug, run_ctx, **kw)

    monkeypatch.setattr(ti, "_pack_tools", spy)
    loader = _loader(_record())
    live = _dummies(40)
    with _in_run(loader):
        answer, passed = await _call(loader, "email", live)
    assert not passed, "the middleware takes the call"
    assert calls == [("email", "key-oa7e", frozenset(_names(_dummies(40))))]
    added = live[40:]
    assert _names(added) == list(EMAIL_TOOLS)
    assert all(getattr(t.func, "__cc_gated__", False) for t in added), "the guarded wrapper"
    assert all(getattr(t.func, ti.PACK_OWNER_ATTR, None) == EA for t in added)
    assert answer.startswith("Loaded the Email tools (31 tools).")
    assert loader.record.holds() == ("email",)


async def test_a_load_counts_as_use(store, monkeypatch) -> None:
    """Sticky on load: the next turn holds the pack by layer (a), so the tool
    list stays byte-stable for the prompt cache. Mutation: drop
    ``note_used`` from the load → the next turn holds no sticky pack → red."""
    _stub_email(monkeypatch)
    with _tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, WORDS)
        choice = tp.choose(turn, allowed=ALL, base_tools=40)
        assert choice.slugs == (), "non-vacuity: layer (a) loaded nothing"
        loader = _loader(tp.PackRun.of(turn, choice, []))
        with _in_run(loader):
            answer, _ = await _call(loader, "email", _dummies(40))
        assert answer.startswith("Loaded"), answer
        later = await tp.begin_turn(ORG, SESSION, MEMBER, "")
    assert [(s.slug, s.used, s.first) for s in later.sticky] == [("email", turn.turn, (turn.turn, 0))]
    assert tp.choose(later, allowed=ALL, base_tools=40).slugs == ("email",)


async def test_a_pack_that_the_run_holds_is_a_no_op(store, monkeypatch) -> None:
    """A pack of layer (a), or one that a load added, answers that it is
    loaded and builds nothing. Mutation: drop the check → the second load
    builds again and answers that the tools are not available → red."""
    _stub_email(monkeypatch)
    built: list[str] = []
    real = ti._build_pack_for_load

    async def spy(slug: str, ctx: Any, holds: Any) -> Any:
        built.append(slug)
        return await real(slug, ctx, holds)

    first = _loader(_record(("email",)), offered=("email-setup",), build=spy)
    live = _dummies(40)
    with _in_run(first):
        answer, _ = await _call(first, "email", live)
    assert answer == tp.LOAD_ALREADY.format(label="Email") and built == [] and len(live) == 40
    second = _loader(_record(), build=spy)
    with _in_run(second):
        await _call(second, "email", live)
        again, _ = await _call(second, "email", live)
    assert again == tp.LOAD_ALREADY.format(label="Email")
    assert built == ["email"] and len(live) == 40 + len(EMAIL_TOOLS)


@pytest.mark.parametrize("slug", ["email-setup", "crm-records", "core", ""])
async def test_a_pack_that_the_run_did_not_offer_is_refused(slug: str, store, monkeypatch) -> None:
    """The run offers only the packs that the flag allows and no toggle
    stops. Mutation: accept any registered pack → ``email-setup`` loads in a
    run that offered ``email`` only → red."""
    _stub_email(monkeypatch)
    loader = _loader(_record(), offered=("email",))
    live = _dummies(40)
    with _in_run(loader):
        answer, _ = await _call(loader, slug, live)
    assert answer.startswith("Not loaded.") and "You can load: email." in answer, answer
    assert len(live) == 40 and loader.record.holds() == ()


async def test_a_load_past_the_pack_cap_refuses_and_names_spawn_subagents(
    store, monkeypatch,
) -> None:
    """A request holds at most 3 packs. Mutation: drop the pack cap from the
    load → a fourth pack loads → red."""
    for slug in ("pa", "pb", "pc", "pd"):
        monkeypatch.setitem(sf.SKILL_FAMILIES, slug, {
            "label": slug.upper(), "summary": slug, "description": slug, "owner": "x-agent",
            "tools": (f"{slug}_t0", f"{slug}_t1"),
        })
        monkeypatch.setitem(tp.PACKS, slug, tp.Pack(slug=slug, rules=f"{slug} RULES"))
    monkeypatch.setitem(ti._PACK_OWNER_MODULES, "x-agent", _stub_owner(
        tuple(f"{s}_t{i}" for s in ("pa", "pb", "pc", "pd") for i in (0, 1))))
    loader = _loader(_record(("pa", "pb", "pc")), offered=("pd",))
    live = _dummies(20)
    with _in_run(loader):
        answer, _ = await _call(loader, "pd", live)
    assert answer == tp.LOAD_PAST_CAP.format(
        label="PD", packs=3, tools=110, slug="pd", owner="x-agent")
    assert "spawn_subagents" in answer and len(live) == 20
    two = _loader(_record(("pa", "pb")), offered=("pd",))
    with _in_run(two):
        ok, _ = await _call(two, "pd", live)
    assert ok.startswith("Loaded the PD tools (2 tools)."), "non-vacuity: a third pack fits"


@pytest.mark.parametrize(("base", "fits"), [(79, True), (80, False)])
async def test_a_load_past_the_tool_cap_refuses_and_names_spawn_subagents(
    base: int, fits: bool, store, monkeypatch,
) -> None:
    """A request holds at most 110 tools, counted on the live list, with the
    load tool itself. 79 + 31 = 110 fits, and 80 + 31 does not. Mutation:
    drop the tool cap, or count with ">=" → red at one of the two bases."""
    _stub_email(monkeypatch)
    loader = _loader(_record())
    live = _dummies(base)
    with _in_run(loader):
        answer, _ = await _call(loader, "email", live)
    if fits:
        assert answer.startswith("Loaded") and len(live) == tp.MAX_REQUEST_TOOLS
    else:
        assert answer == tp.LOAD_PAST_CAP.format(
            label="Email", packs=3, tools=110, slug="email", owner=EA)
        assert "spawn_subagents" in answer and len(live) == base
        assert loader.record.holds() == ()


@pytest.mark.parametrize("why", ["another run key", "another record", "no run"])
async def test_a_load_acts_only_in_the_run_that_offered_it(why: str, store, monkeypatch) -> None:
    """The run check keys on the run key that the server minted, as each
    pack call does (OA-7c condition 3). Mutation: drop the run check → a
    frame of another run loads the pack → red."""
    _stub_email(monkeypatch)
    loader = _loader(_record())
    live = _dummies(40)
    if why == "no run":
        with _tenant():
            answer, _ = await _call(loader, "email", live)
    else:
        over = ({tp.PACK_RUN_KEY: "key-other"} if why == "another run key"
                else {tp.PACKS_KEY: _record()})
        with _in_run(loader, **over):
            answer, _ = await _call(loader, "email", live)
    assert answer == tp.LOAD_REFUSED and len(live) == 40


async def test_another_call_passes_through_and_the_body_alone_loads_nothing(store) -> None:
    """The middleware takes only its own tool, by identity. Mutation: match
    on the name → another run's ``load_skill_tools`` loads through this run's
    middleware → red. With no middleware, the body loads nothing."""
    loader = _loader(_record())
    passed: list[bool] = []

    async def call_next() -> None:
        passed.append(True)

    foreign = tp.load_tool(("email",))
    context = FunctionInvocationContext(function=foreign, arguments={"pack": "email"}, tools=[])
    await loader.process(context, call_next)
    assert passed == [True] and context.result is None
    assert await foreign.func(pack="email") == tp.LOAD_REFUSED


@pytest.mark.parametrize("slug", ["email", "email-setup"])
async def test_a_mail_pack_never_joins_a_run_with_an_mcp_tool(
    slug: str, store, monkeypatch,
) -> None:
    """A run with a pack of the email assistant gets no registry MCP server
    (OA-7b). A load in the middle of a turn cannot take the server away, so
    it refuses. Mutation: drop the MCP rule from the load → the email tools
    join a run that holds an MCP tool → red."""
    _stub_email(monkeypatch)
    mcp = _dummies(1, "notion")[0]
    mcp.additional_properties["_mcp_is_tool"] = True
    assert eg._from_mcp(mcp), "non-vacuity: the egress rule reads it as MCP"
    loader = _loader(_record())
    live = [*_dummies(30), mcp]
    with _in_run(loader):
        answer, _ = await _call(loader, slug, live)
    assert answer.startswith("Not loaded.") and "connected server" in answer, answer
    assert len(live) == 31 and loader.record.holds() == ()
    clean = _loader(_record())
    with _in_run(clean):
        ok, _ = await _call(clean, slug, _dummies(31))
    assert ok.startswith("Loaded"), "non-vacuity: with no MCP tool it loads"


async def test_the_build_skips_each_name_of_the_live_list(store, monkeypatch) -> None:
    """A pack tool never shadows a tool of the run. Mutation: build with no
    names of the live list → ``add_tools`` raises for the second
    ``read_email`` and the pack does not load → red."""
    _stub_email(monkeypatch)
    own = _dummies(1, "x")[0]
    own.name = "read_email"
    loader = _loader(_record())
    live = [*_dummies(30), own]
    with _in_run(loader):
        answer, _ = await _call(loader, "email", live)
    assert answer.startswith("Loaded the Email tools (30 tools)."), answer
    assert [t for t in live if _name(t) == "read_email"] == [own]


@pytest.mark.parametrize("error", [ValueError, RuntimeError])
async def test_an_add_tools_error_loads_nothing(error: type, store, monkeypatch) -> None:
    """§4.5 "OA-7e facts": ``add_tools`` raises ``ValueError`` and
    ``RuntimeError``. Mutation: let the error out, or record the load before
    ``add_tools`` → the call fails, or the record holds a pack that the run
    does not hold → red."""
    _stub_email(monkeypatch)

    def boom(self: Any, tools: Any) -> None:
        raise error("no")

    monkeypatch.setattr(FunctionInvocationContext, "add_tools", boom)
    loader = _loader(_record())
    live = _dummies(40)
    with _in_run(loader):
        answer, _ = await _call(loader, "email", live)
        sticky = await tp.begin_turn(ORG, SESSION, MEMBER, "")
    assert answer == tp.LOAD_UNAVAILABLE.format(label="Email")
    assert loader.record.holds() == () and len(live) == 40 and sticky.sticky == ()


async def test_a_load_with_no_live_list_builds_nothing(store, monkeypatch) -> None:
    """A call outside the function loop has no live list. Mutation: build
    before the check → a build with no list to put it in → red."""
    built: list[str] = []

    async def build(slug: str, _ctx: Any, _holds: Any) -> Any:
        built.append(slug)
        return None

    loader = _loader(_record(), build=build)
    with _in_run(loader):
        answer, _ = await _call(loader, "email", None)
    assert answer == tp.LOAD_UNAVAILABLE.format(label="Email") and built == []


async def test_a_held_load_drops_the_held_set_and_answers_the_hold_note(
    store, monkeypatch,
) -> None:
    """The chat read mail before this call, so the build is held: the held
    set drops, and the answer holds the hold note of a pack, which names no
    other assistant. The real email module builds. Mutation: build an open
    pack in a marked chat → ``send_email`` joins the run → red."""
    _held_card_off(monkeypatch)
    monkeypatch.setitem(ti._PACK_OWNER_MODULES, EA, load_agent_module(EA_DIR))
    loader = _loader(_record())
    live = _dummies(40)
    with _in_run(loader):
        await eg.mark_untrusted_answer()
        answer, _ = await _call(loader, "email", live)
    names = set(_names(live[40:]))
    assert names and not {"send_email", "forward_email"} & names, names
    assert answer.endswith(eg.EGRESS_HOLD_NOTE_PACK) and "Another assistant" not in answer
    assert loader.record.later == {"email": True}


async def test_email_setup_after_email_closes_the_hand_off(store, monkeypatch) -> None:
    """The case where the OA-7c refusal first acts live. Layer (a) loaded
    ``email`` alone, so the rules sent the setup work to the email
    assistant. A load of ``email-setup`` completes the packs of the owner:
    from that call on, a hand-off to the owner gets the pack refusal, and the
    answer gives the old line back. The specialist tool and the persona text
    stay for the turn (``replaced`` does not change). Mutation: the refusal
    reads the record of the run start only, or the answer drops the closing
    lines → red."""
    _stub_email(monkeypatch)
    ran: list[str] = []

    async def fake_run_agent(name: str, payload: dict, **_k: Any) -> dict:
        ran.append(name)
        return {"result": "ran", "tool_calls": []}

    monkeypatch.setattr(ex, "run_agent", fake_run_agent)
    record = _record(("email",))
    assert record.refusals == {} and record.replaced == {EA}
    loader = _loader(record, offered=("email-setup",))
    with _in_run(loader):
        assert tp.delegation_refusal(EA) is None, "non-vacuity: open before the load"
        answer, _ = await _call(loader, "email-setup", _dummies(20))
        refused = tp.delegation_refusal(EA)
        direct = await agent_tools.call_agent(EA, "Make a rule.")
    line, _route = tp.PACKS["email"].handoff_swap
    assert answer.startswith("Loaded the Email setup tools (28 tools).")
    assert answer.endswith(f"Each tool of {EA} is now yours. {line}")
    assert refused == tp.PACKS["email"].delegation_refusal == direct and ran == []
    assert record.refusals == {} and record.replaced == {EA}
    assert record.holds() == ("email", "email-setup")


async def test_email_alone_keeps_the_hand_off_open(store, monkeypatch) -> None:
    """A load of ``email`` with no ``email-setup`` keeps the hand-off for the
    setup work, and its rules say so in one line. Mutation: refuse the
    hand-off whenever a load added ``email`` → red."""
    _stub_email(monkeypatch)
    loader = _loader(_record())
    with _in_run(loader):
        answer, _ = await _call(loader, "email", _dummies(40))
        refused = tp.delegation_refusal(EA)
    line, route = tp.PACKS["email"].handoff_swap
    assert refused is None and route in answer and line not in answer
    assert "Each tool of" not in answer


def test_the_load_answer_and_the_persona_carry_one_rules_text() -> None:
    """ONE rules text for both layers (``tool_packs._rules_block``).
    Mutation: write a second text for the load → the two differ → red."""
    import orchestrator.agents as pkg
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    persona = pkg._mod._orchestrator_instructions(False)
    for loaded in ([("email", False)], [("email", True)], [("email-setup", False)]):
        slug, held = loaded[0]
        block = tp._rules_block(tp.PACKS[slug], held, (slug,))
        assert tp.persona_for(persona, loaded, PACK_DELEGATION_BULLETS).endswith(block + "\n")


# ── 3. The offer ────────────────────────────────────────────────────────────


@pytest.fixture
def orchestrator(monkeypatch) -> list[Any]:
    """The real orchestrator agent, as its agent repo builds it."""
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    from orchestrator.agents import build_orchestrator_agent

    return [build_orchestrator_agent(with_history=False)]


async def _apply(agents: list[Any], words: str, *, allowed: frozenset[str] = ALL,
                 offer_loader: bool = True) -> Any:
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    with _tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, words)
        return await ti._apply_tool_packs(
            agents, turn, _run_ctx(), allowed=allowed, agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG,
            bullets=PACK_DELEGATION_BULLETS, offer_loader=offer_loader,
        )


def _provider(agent: Any) -> tp.LoadSkillToolsProvider | None:
    found = [p for p in agent.context_providers or [] if isinstance(p, tp.LoadSkillToolsProvider)]
    assert len(found) <= 1
    return found[0] if found else None


@pytest.mark.parametrize(("allowed", "disabled", "want"), [
    (ALL, frozenset(), ("email", "email-setup")),
    (frozenset({"email"}), frozenset(), ("email",)),
    (ALL, frozenset({"email-setup"}), ("email",)),
])
async def test_the_catalog_lists_only_the_packs_that_the_flag_allows(
    allowed: frozenset[str], disabled: frozenset[str], want: tuple[str, ...],
    store, orchestrator, monkeypatch,
) -> None:
    """``packs_on`` fails closed, and an admin toggle stops a pack. Mutation:
    offer every registered pack → the catalog lists a pack that the flag or
    a toggle stops → red."""
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _n: disabled)
    original = orchestrator[0]
    tools_before = _names(original.default_options["tools"])
    persona = original.default_options["instructions"]
    applied = await _apply(orchestrator, WORDS, allowed=allowed)
    assert applied is not None and applied.builds == () and applied.record.slugs == ()
    provider = _provider(orchestrator[0])
    assert provider is not None and provider.loader.offered == want
    description = provider.loader.tool.description
    for slug in ALL:
        assert (f"- {slug}: " in description) is (slug in want), description
    assert orchestrator[0] is not original and _provider(original) is None, "a per-run view"
    assert _names(orchestrator[0].default_options["tools"]) == tools_before
    assert orchestrator[0].default_options["instructions"] == persona


async def test_without_the_offer_the_apply_is_layer_a_only(store, orchestrator) -> None:
    """``_apply_tool_packs`` keeps the contract of OA-7c when the caller
    does not ask for the loader. Mutation: offer the loader on each apply →
    a caller that wants layer (a) only gets a view → red."""
    original = orchestrator[0]
    assert await _apply(orchestrator, WORDS, offer_loader=False) is None
    assert orchestrator[0] is original and _provider(original) is None


async def test_a_run_that_holds_each_allowed_pack_gets_no_loader(store, orchestrator) -> None:
    """Nothing is left to offer, so the run gets no load tool. Mutation:
    offer the loader with an empty catalog → red."""
    applied = await _apply(orchestrator, "check my inbox", allowed=frozenset({"email"}))
    assert applied is not None and applied.record.slugs == ("email",)
    assert _provider(orchestrator[0]) is None


async def test_the_loader_offers_the_packs_that_layer_a_did_not_load(store, orchestrator) -> None:
    """Layer (a) loaded ``email``, so the loader offers ``email-setup`` with
    the same run key and the same record that the executor binds. Mutation:
    give the loader its own key or record → each load of the run refuses
    → red."""
    applied = await _apply(orchestrator, "check my inbox")
    assert applied is not None and applied.record.slugs == ("email",)
    provider = _provider(orchestrator[0])
    assert provider is not None and provider.loader.offered == ("email-setup",)
    assert provider.loader.run_key == applied.run_key
    assert provider.loader.record is applied.record


def _calls_with_keyword(path: str, keyword: str) -> set[str]:
    """The functions of *path* that make a call with *keyword* set to True."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    found: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and any(
                        k.arg == keyword and isinstance(k.value, ast.Constant)
                        and k.value.value is True for k in node.keywords):
                    found.add(fn.name)
    return found


def _callers(path: str, name: str) -> set[str]:
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    found: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name != name:
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == name):
                    found.add(fn.name)
    return found


def test_only_the_run_that_layer_a_serves_offers_the_loader() -> None:
    """Rooms, WhatsApp and delegated runs get no ``load_skill_tools``: the one
    caller that asks for the loader is ``_apply_pack_turn``, which only
    ``run_agent_stream`` calls, after ``_start_pack_turn`` said yes
    (``test_tool_packs_choice.py``). Mutation: offer the loader from another
    path → red."""
    executor = "apps/services/orchestrator/orchestrator/executor.py"
    injection = "apps/services/orchestrator/orchestrator/_tool_injection.py"
    assert _calls_with_keyword(executor, "offer_loader") == {"_apply_pack_turn"}
    assert _calls_with_keyword(injection, "offer_loader") == set()
    assert _callers(injection, "_offer_pack_loader") == {"_apply_tool_packs", "_loader_only"}
    assert _callers(executor, "_apply_pack_turn") == {"run_agent_stream"}


# ── 4. The live run, with a scripted model ──────────────────────────────────


def _flag(monkeypatch, on: bool) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "tool_packs", "*" if on else "", raising=False)
    monkeypatch.setattr(settings, "tool_packs_orgs", "*" if on else "", raising=False)


def _drive(monkeypatch, model: ScriptedModel, run_id: str, *, words: str = WORDS,
           solo_room: bool = True, shows_cards: bool = True) -> list[dict[str, Any]]:
    """One turn of the member's own web chat with the REAL orchestrator, run
    by the REAL ``run_agent_stream``. Only the HTTP transport of the model is
    replaced. Returns the events."""
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    from acb_skills import email_chat_context as ecc

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

    import acb_auth.access as auth_access

    monkeypatch.setattr(auth_access, "_tables_missing", auth_access._tables_missing)
    monkeypatch.setattr(ex, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(ex, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _n: frozenset())
    monkeypatch.setattr(ecc, "prime_mailboxes", _none)
    monkeypatch.setattr("acb_common.today.today_line", lambda *_a, **_k: "")

    async def _collect() -> list[str]:
        return [line async for line in ex.run_agent_stream(
            "orchestrator", {"message": words}, run_id=run_id, thread_id=SESSION,
            organization_id=ORG, session_user=MEMBER, shows_cards=shows_cards,
            solo_room=solo_room,
        )]

    with member_typed(words):
        return parse_frames(asyncio.run(_collect()))


def _tool_names(body: dict[str, Any]) -> list[str]:
    return [t["function"]["name"] for t in body.get("tools") or []]


def _tool_result(body: dict[str, Any], call_id: str) -> str:
    for message in body.get("messages") or []:
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            content = message.get("content")
            return content if isinstance(content, str) else json.dumps(content)
    raise AssertionError(f"no result of {call_id} in the request")


def _mark_chat() -> None:
    """Another turn of this chat read mail, so Redis holds the mark."""

    async def mark() -> None:
        with _tenant(), _bound(**_run_ctx(run_id="r-earlier")):
            assert await eg.mark_untrusted_answer()

    asyncio.run(mark())


@pytest.mark.parametrize("marked", [False, True], ids=["clean-chat", "marked-chat"])
def test_a_scripted_run_loads_email_then_compose_reply_runs_with_the_held_answer(
    marked: bool, store, monkeypatch,
) -> None:
    """THE acceptance of OA-7e. In one run, the model calls
    ``load_skill_tools("email")`` and then ``compose_reply``. The first
    request holds no email tool, and the second one does. ``compose_reply``
    runs in the scope of the owner, with the held answer of the pack: open in
    a clean chat, held in a chat that read mail. The load counts as use, so
    the chat holds the pack in the next turn. Mutation: the provider adds the
    tool but not the middleware → the load answers ``LOAD_REFUSED`` and the
    second request holds no ``compose_reply`` → red. Mutation: the build
    skips the scope of the owner → the body reads the orchestrator → red."""
    _flag(monkeypatch, True)
    _held_card_off(monkeypatch)
    seen: list[dict[str, Any]] = []
    _stub_email(monkeypatch, seen)
    if marked:
        _mark_chat()
    model = ScriptedModel([
        tool_turn(tp.LOAD_TOOL, json.dumps({"pack": "email"}), call_id="c-load"),
        tool_turn("compose_reply", json.dumps({"email_id": "m-1", "instruction": "pay Friday"}),
                  call_id="c-reply"),
        text_turn("Done."),
    ])
    events = _drive(monkeypatch, model, f"run-oa7e-{marked}")
    kinds = [e.get("type") for e in events]
    assert kinds[-1] == "RUN_FINISHED" and "RUN_ERROR" not in kinds, events
    assert len(model.bodies) == 3
    first, second = _tool_names(model.bodies[0]), _tool_names(model.bodies[1])
    assert first[-1] == tp.LOAD_TOOL and "compose_reply" not in first
    assert "email_assistant" in first, "layer (a) loaded nothing"
    assert set(EMAIL_TOOLS) <= set(second) and second[:len(first)] == first
    loaded = _tool_result(model.bodies[1], "c-load")
    assert loaded.startswith("Loaded the Email tools (31 tools).") and "EMAIL TOOLS." in loaded
    assert (eg.EGRESS_HOLD_NOTE_PACK in loaded) is marked
    assert seen == [{"tool": "compose_reply", "agent_name": EA, "pack_call": True,
                     "no_egress": marked}]
    assert "compose_reply done" in _tool_result(model.bodies[2], "c-reply")

    async def next_turn() -> Any:
        return await tp.begin_turn(ORG, SESSION, MEMBER, "")

    later = asyncio.run(next_turn())
    assert [s.slug for s in later.sticky] == ["email"]


def test_a_scripted_load_past_the_cap_refuses_and_names_spawn_subagents(
    store, monkeypatch,
) -> None:
    """The second load of the run passes the tool cap: it refuses, its answer
    names ``spawn_subagents``, and the next request holds no setup tool. The
    cap is set low here, so the case does not hang on the size of the base.
    Mutation: drop the tool cap from the load → the setup tools join → red."""
    _flag(monkeypatch, True)
    _stub_email(monkeypatch)
    monkeypatch.setattr(tp, "MAX_REQUEST_TOOLS", 90)
    model = ScriptedModel([
        tool_turn(tp.LOAD_TOOL, json.dumps({"pack": "email"}), call_id="c-1"),
        tool_turn(tp.LOAD_TOOL, json.dumps({"pack": "email-setup"}), call_id="c-2"),
        text_turn("Done."),
    ])
    events = _drive(monkeypatch, model, "run-oa7e-cap")
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED", events
    names = _tool_names(model.bodies[2])
    assert set(EMAIL_TOOLS) <= set(names) and not set(SETUP_TOOLS) & set(names)
    assert len(names) + len(SETUP_TOOLS) > 90 >= len(names), "non-vacuity of the cap"
    refused = _tool_result(model.bodies[2], "c-2")
    assert refused == tp.LOAD_PAST_CAP.format(
        label="Email setup", packs=3, tools=90, slug="email-setup", owner=EA)
    assert "spawn_subagents" in refused


@pytest.mark.parametrize("why", ["flag off", "a shared room", "no card channel"])
def test_a_run_that_layer_a_does_not_serve_gets_no_load_tool(
    why: str, store, monkeypatch,
) -> None:
    """Rooms, WhatsApp and delegated runs see no pack, and no load tool. With
    the flag off the run reads and writes no pack state. Mutation: offer the
    loader whatever ``_start_pack_turn`` said → the tool shows here → red."""
    _flag(monkeypatch, why != "flag off")
    model = ScriptedModel([text_turn("Done.")])
    events = _drive(monkeypatch, model, f"run-oa7e-{why}",
                    solo_room=why != "a shared room", shows_cards=why != "no card channel")
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED", events
    assert tp.LOAD_TOOL not in _tool_names(model.bodies[0])
    if why == "flag off":
        assert [c for c in store.calls if f":{tp.NAMESPACE}:" in c[1]] == []


def test_flag_on_a_turn_with_no_pack_holds_the_load_tool_last(store, monkeypatch) -> None:
    """Non-vacuity of the case above, and the order: the load tool comes
    after each tool of the run, and the persona is the persona of a run with
    no pack. Mutation: the executor never asks for the loader → red."""
    _flag(monkeypatch, True)
    model = ScriptedModel([text_turn("Done.")])
    _drive(monkeypatch, model, "run-oa7e-on")
    names = _tool_names(model.bodies[0])
    assert names[-1] == tp.LOAD_TOOL and names.count(tp.LOAD_TOOL) == 1
    spec = model.bodies[0]["tools"][-1]["function"]
    assert "- email: " in spec["description"] and "- email-setup: " in spec["description"]
    assert spec["parameters"]["required"] == ["pack"]
    system = model.bodies[0]["messages"][0]["content"]
    assert "EMAIL TOOLS." not in system and tp.LOAD_TOOL not in system
