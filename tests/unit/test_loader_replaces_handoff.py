"""WS-67 OA-7i: with tool packs on, the loader replaces the specialist hand-off.

``project-docs/specs/one_assistant_context.md`` §4.2.8 (OA-7i). OA-7h measured
54 email turns per arm with the real flag. Where no pack loaded by layer (a),
the model handed mail work to ``email_assistant`` in 39 of 42 turns and never
called ``load_skill_tools``. So while the flag is on and the loader is offered,
an owner whose every pack is held or reachable by the loader loses its
specialist tool and its persona text, and a hand-off to it is refused with a
text that names ``load_skill_tools``. ONE function decides it
(``tool_packs.plan_loader``), and the offer of the loader and the removal read
its answer. Each case names, in its docstring, the mutation that turns it red
(R7).

1. The rule: the specialist leaves, the persona swaps, the hand-off refuses.
2. Where the specialist stays: a pack over the cap, an MCP server, a stopped
   pack, no owner module, no persona text.
3. A held chat.
4. One reachability function for the offer and the removal.
5. What does not change: the background hand-off and the hand-off with some
   packs of the owner held.
6. The two-turn path: the load of ``email`` leaves no room for ``email-setup``.

Flag off: ``tests/unit/test_tool_packs_off.py`` (byte-identical run).

Run: ``uv run pytest tests/unit/test_loader_replaces_handoff.py -q``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
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
from tests.unit._native_maf_harness import load_agent_module

REPO = Path(__file__).resolve().parents[2]
EA_DIR = "apps/agents/agent-email-assistant"
CONFIG = json.loads((REPO / "apps/agents/agent-orchestrator/config.json")
                    .read_text(encoding="utf-8"))
EA = "email-assistant"
ORG = "0a0a0a0a-0000-4000-8000-0000000000f1"
MEMBER = "jane@acme.test"
SESSION = "chat-oa7i-1"
ALL = frozenset({"email", "email-setup"})
CRM = "crm-assistant"
CRM_PACK = frozenset({"crm-records"})
CRM_TOOLS = tuple(sf.SKILL_FAMILIES["crm-records"]["tools"])
EMAIL_TOOLS = tuple(sf.SKILL_FAMILIES["email"]["tools"])
SETUP_TOOLS = tuple(sf.SKILL_FAMILIES["email-setup"]["tools"])
#: Words with no email word, so layer (a) loads no pack.
WORDS = "Tell Anil that we pay on Friday"

_READ_ONLY = {"read_only": True, "destructive": False, "idempotent": True, "open_world": False}


@pytest.fixture
def store(monkeypatch) -> Any:
    return install_memory_mark_store(monkeypatch)


@contextlib.contextmanager
def _tenant() -> Iterator[None]:
    token = bind_tenant(ORG)
    try:
        yield
    finally:
        release_tenant(token)


def _run_ctx(**over: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "run_id": "r-oa7i", "session_id": SESSION, "agent_name": "orchestrator",
        "member": MEMBER, "batch_thread": False, "no_egress": False,
        "workspace_root": "/tmp/orch-oa7i",
    }
    values.update(over)
    return values


@contextlib.contextmanager
def _bound(**values: Any) -> Iterator[None]:
    with artifact_context_scope():
        bind_artifact_context(**values)
        yield


@contextlib.contextmanager
def _in_run(applied: Any, **over: Any) -> Iterator[None]:
    """The frame of the run that *applied* served."""
    values = {**_run_ctx(), **applied.context()}
    values.update(over)
    with _tenant(), _bound(**values):
        yield


def _names(items: Any) -> list[str]:
    return [ti._tool_name(t) for t in items]


def _probe(name: str) -> Any:
    async def probe(email_id: str = "", instruction: str = "", draft: str = "") -> str:
        return f"{name} done"

    probe.__name__ = name
    probe.__tool_risk__ = dict(_READ_ONLY)  # type: ignore[attr-defined]
    return probe


@pytest.fixture(autouse=True)
def _stub_owner(monkeypatch) -> None:
    """An owner module whose tools are probes: the tests need no email agent."""
    names = EMAIL_TOOLS + SETUP_TOOLS
    stub = types.SimpleNamespace(_agent_tools=lambda: [_probe(n) for n in names])
    monkeypatch.setitem(ti._PACK_OWNER_MODULES, EA, stub)
    crm_stub = types.SimpleNamespace(_agent_tools=lambda: [_probe(n) for n in CRM_TOOLS])
    monkeypatch.setitem(ti._PACK_OWNER_MODULES, CRM, crm_stub)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())


def _fresh() -> list[Any]:
    """The real orchestrator agent, as its agent repo builds it."""
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    from orchestrator.agents import build_orchestrator_agent

    original = routes_agent._load_dynamic_agents
    routes_agent._load_dynamic_agents = lambda: []
    try:
        return [build_orchestrator_agent(with_history=False)]
    finally:
        routes_agent._load_dynamic_agents = original


@pytest.fixture
def agents() -> list[Any]:
    return _fresh()


async def _apply(
    agents: list[Any], words: str = WORDS, *, allowed: frozenset[str] = ALL,
    run_ctx: dict[str, Any] | None = None, bullets: dict[str, str] | None = None,
) -> Any:
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    with _tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, words)
        return await ti._apply_tool_packs(
            agents, turn, run_ctx or _run_ctx(), allowed=allowed, agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG,
            bullets=PACK_DELEGATION_BULLETS if bullets is None else bullets,
            offer_loader=True)


def _provider(agent: Any) -> tp.LoadSkillToolsProvider | None:
    found = [p for p in agent.context_providers or [] if isinstance(p, tp.LoadSkillToolsProvider)]
    assert len(found) <= 1
    return found[0] if found else None


def _bullet() -> str:
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    return PACK_DELEGATION_BULLETS[EA]


def _fake_run_agent(monkeypatch) -> list[str]:
    ran: list[str] = []

    async def fake(name: str, payload: dict, **_k: Any) -> dict:
        ran.append(name)
        return {"result": "ran", "tool_calls": []}

    monkeypatch.setattr(ex, "run_agent", fake)
    return ran


def _dummies(count: int) -> list[Any]:
    def make(i: int) -> Any:
        async def fn() -> str:
            return "ok"

        return tool(fn, name=f"core_{i}", approval_mode="never_require")

    return [make(i) for i in range(count)]


async def _call(loader: tp.LoadSkillTools, slug: str, live: list[Any]) -> str:
    async def call_next() -> None:
        raise AssertionError("the middleware must take the call")

    context = FunctionInvocationContext(
        function=loader.tool, arguments={"pack": slug}, tools=live)
    await loader.process(context, call_next)
    return str(context.result)


def _base(monkeypatch, count: int) -> None:
    monkeypatch.setattr(ti, "_pack_base_count", lambda *_a, **_k: count)


# ── 1. The rule ─────────────────────────────────────────────────────────────


async def test_with_no_pack_held_the_specialist_leaves_and_the_hand_off_is_refused(
    store, agents, monkeypatch,
) -> None:
    """Acceptance 2. Flag on, no email pack held, the loader offered:
    ``email_assistant`` leaves the tool list, the persona loses the email
    delegation bullet, the "Delegatable agents" block leaves the owner out,
    and ``call_agent`` and ``call_agents_parallel`` answer a refusal that
    names ``load_skill_tools`` and the packs. No sub-run starts, and the chat
    is not marked. Mutation: read ``via_loader`` nowhere (an empty plan) →
    the specialist, the bullet and the hand-off all stay → red."""
    ran = _fake_run_agent(monkeypatch)
    before_tools = _names(agents[0].default_options["tools"])
    before_persona = agents[0].default_options["instructions"]
    assert "email_assistant" in before_tools and _bullet() in before_persona, "non-vacuity"
    applied = await _apply(agents)
    assert applied is not None and applied.builds == ()
    tools = _names(agents[0].default_options["tools"])
    assert [n for n in before_tools if n not in tools] == ["email_assistant"]
    persona = agents[0].default_options["instructions"]
    assert _bullet() not in persona and persona == before_persona.replace(_bullet(), "", 1)
    assert applied.record.via_loader == {EA} and applied.record.replaced == {EA}
    assert getattr(agents[0], ti.PACK_REFUSED_ATTR) == {EA}
    provider = _provider(agents[0])
    assert provider is not None and provider.loader.offered == ("email", "email-setup")
    with _in_run(applied):
        direct = await agent_tools.call_agent(EA, "Read my mail.")
        fan = await agent_tools.call_agents_parallel(json.dumps([{"agent": EA, "message": "x"}]))
        seam = await ex._run_sub_agent_streaming(EA, "Read my mail.", "r-sub", asyncio.Queue())
        state = await eg.session_mark_state()
    want = tp.LOADER_HANDOFF_REFUSED.format(owner=EA, packs="email, email-setup")
    assert direct == want == seam and want in fan
    assert "load_skill_tools" in want and "email-setup" in want
    assert ran == [] and state == eg.MARK_CLEAR


async def test_the_rest_of_the_work_goes_to_the_loader_when_email_is_held(
    store, agents, monkeypatch,
) -> None:
    """``email`` held by layer (a), ``email-setup`` reachable: the rules send
    the setup work to ``load_skill_tools`` (not to the owner), and a hand-off
    is refused with a text that names only ``email-setup``. Mutation: leave
    ``via_loader`` out of the rules → the rules still send the setup work to
    the email assistant, which the run refuses → red."""
    _fake_run_agent(monkeypatch)
    _base(monkeypatch, 40)
    applied = await _apply(agents, "check my inbox")
    assert applied is not None and applied.record.slugs == ("email",)
    assert applied.record.via_loader == {EA}
    persona = agents[0].default_options["instructions"]
    assert tp.PACKS["email"].loader_route in persona
    assert tp._EMAIL_SETUP_ROUTE_LINE not in persona and tp._EMAIL_NO_AGENT_LINE not in persona
    provider = _provider(agents[0])
    assert provider is not None and provider.loader.offered == ("email-setup",)
    with _in_run(applied):
        refused = await agent_tools.call_agent(EA, "Make a rule.")
    assert refused == tp.LOADER_HANDOFF_REFUSED.format(owner=EA, packs="email-setup")


# ── 2. Where the specialist stays ───────────────────────────────────────────


@pytest.mark.parametrize(("base", "via", "offered"), [
    (79, True, ("email", "email-setup")),
    (80, False, ("email-setup",)),
    (90, False, ()),
])
async def test_the_specialist_stays_when_a_pack_of_the_owner_cannot_load(
    base: int, via: bool, offered: tuple[str, ...], store, agents, monkeypatch,
) -> None:
    """Acceptance 3, no pack held. The base counts the load tool: 80, and the
    31 tools of ``email`` make 111, which the cap refuses, so ``email`` is
    unreachable and the hand-off stays open with the tool and the bullet. At
    base 79 the specialist's own slot frees one tool, so 110 fits: the
    discount of the owner's own specialist. Mutation: drop that discount →
    base 79 keeps the specialist → red. Mutation: replace the owner when ANY
    pack is reachable → base 80 loses the specialist, and the hand-off to it
    has no path for ``email`` → red."""
    _base(monkeypatch, base)
    before = _names(agents[0].default_options["tools"])
    persona = agents[0].default_options["instructions"]
    applied = await _apply(agents)
    provider = _provider(agents[0])
    assert (provider.loader.offered if provider else ()) == offered
    tools = _names(agents[0].default_options["tools"])
    if via:
        assert applied is not None and applied.record.via_loader == {EA}
        assert "email_assistant" not in tools
    else:
        assert tools == before and agents[0].default_options["instructions"] == persona
        assert applied is None or applied.record.via_loader == frozenset()
        assert getattr(agents[0], ti.PACK_REFUSED_ATTR, frozenset()) == frozenset()


async def test_with_email_held_and_setup_over_the_cap_the_hand_off_stays_open(
    store, agents, monkeypatch,
) -> None:
    """Acceptance 3. ``email`` is held by layer (a) and ``email-setup`` would
    pass 110: the run is the run of OA-7c. The specialist tool left with the
    pack (as before OA-7i), the hand-off for the setup work stays open, and
    the rules send it to the email assistant. Mutation: refuse the hand-off
    for the owner whenever a pack is held → red."""
    ran = _fake_run_agent(monkeypatch)
    _base(monkeypatch, 60)
    applied = await _apply(agents, "check my inbox")
    assert applied is not None and applied.record.slugs == ("email",)
    assert applied.record.via_loader == frozenset() and applied.record.replaced == {EA}
    assert _provider(agents[0]) is None, "email-setup does not fit, so nothing is left to offer"
    persona = agents[0].default_options["instructions"]
    assert tp._EMAIL_SETUP_ROUTE_LINE in persona and tp.PACKS["email"].loader_route not in persona
    assert getattr(agents[0], ti.PACK_REFUSED_ATTR) == frozenset()
    with _in_run(applied):
        assert tp.delegation_refusal(EA) is None
        out = await agent_tools.call_agent(EA, "Make a rule that files newsletters.")
    assert ran == [EA] and "ran" in out


@pytest.mark.parametrize("servers", ["none", "one", "fault"])
async def test_beside_an_mcp_server_the_specialist_stays(
    servers: str, store, agents, monkeypatch,
) -> None:
    """Acceptance 4. The MCP injection runs after the packs and a mail pack
    never joins a run with an MCP tool, so the plan looks ahead at the
    registry (``_mcp_may_attach``). A server in scope, and a fault of the
    lookahead (fail closed), keep the specialist, the bullet and the
    hand-off. No server frees the replacement. Mutation: pass ``mcp=False``
    to the plan → the specialist leaves in a run that cannot load the pack,
    so the work has no path → red."""
    from gateway.connections import mcp as mcp_registry
    from orchestrator import _maf_mcp

    monkeypatch.setattr(_maf_mcp, "maf_mcp_org_allowed", lambda _org: True)
    rows = [types.SimpleNamespace(
        name="notion", enabled=True, agent_scope=("*",), transport="http-sse",
        url="https://notion.example/mcp")] if servers == "one" else []

    async def list_servers(_org: str) -> list[Any]:
        if servers == "fault":
            raise RuntimeError("registry down")
        return rows

    monkeypatch.setattr(mcp_registry, "enabled_meta", list_servers)
    before = _names(agents[0].default_options["tools"])
    persona = agents[0].default_options["instructions"]
    applied = await _apply(agents)
    if servers == "none":
        assert applied is not None and applied.record.via_loader == {EA}
        assert "email_assistant" not in _names(agents[0].default_options["tools"])
        return
    assert _names(agents[0].default_options["tools"]) == before
    assert agents[0].default_options["instructions"] == persona
    assert applied is not None and applied.record.via_loader == frozenset()


@pytest.mark.parametrize(("row", "authorized", "expect"), [
    ({}, True, True),
    ({"enabled": False}, True, False),
    ({"agent_scope": ("crm-assistant",)}, True, False),
    ({"transport": "stdio"}, True, False),
    ({"url": ""}, True, False),
    ({}, False, False),
])
async def test_the_mcp_lookahead_reads_the_rule_of_the_injection(
    row: dict[str, Any], authorized: bool, expect: bool, agents, monkeypatch,
) -> None:
    """``_mcp_may_attach`` keeps the early exits and the row filters of
    ``_inject_mcp_servers``: a disabled row, a row for another agent, a stdio
    row, a row with no url and a server that the member's grant refuses add no
    tool. Mutation: count every row → the cases that expect False read True →
    red."""
    from gateway.connections import mcp as mcp_registry
    from orchestrator import _maf_mcp

    monkeypatch.setattr(_maf_mcp, "maf_mcp_org_allowed", lambda _org: True)
    base = {"name": "notion", "enabled": True, "agent_scope": ("*",),
            "transport": "http-sse", "url": "https://notion.example/mcp"}

    async def list_servers(_org: str) -> list[Any]:
        return [types.SimpleNamespace(**{**base, **row})]

    monkeypatch.setattr(mcp_registry, "enabled_meta", list_servers)

    def grant(_service: str) -> bool:
        return authorized

    with _tenant():
        got = await ti._mcp_may_attach(
            agents[0], "orchestrator", no_egress=False, is_authorized=grant,
            held_untrusted=False)
    assert got is expect


@pytest.mark.parametrize(("no_egress", "held_untrusted", "org_on"), [
    (True, False, True), (False, True, True), (False, False, False)])
async def test_the_mcp_lookahead_is_false_where_the_injection_adds_no_server(
    no_egress: bool, held_untrusted: bool, org_on: bool, agents, monkeypatch,
) -> None:
    """A no-egress run, a run that holds a mail pack, and an org that
    ``MAF_MCP_ORGS`` does not name get no registry server, and the lookahead
    reads no row. Mutation: read the registry first → the fault below
    raises, or the answer reads True → red."""
    from gateway.connections import mcp as mcp_registry
    from orchestrator import _maf_mcp

    monkeypatch.setattr(_maf_mcp, "maf_mcp_org_allowed", lambda _org: org_on)

    async def boom(_org: str) -> list[Any]:
        raise AssertionError("the registry must not be read")

    monkeypatch.setattr(mcp_registry, "enabled_meta", boom)
    with _tenant():
        assert await ti._mcp_may_attach(
            agents[0], "orchestrator", no_egress=no_egress, is_authorized=None,
            held_untrusted=held_untrusted) is False


@pytest.mark.parametrize("why", ["no owner module", "no persona text"])
async def test_the_specialist_stays_when_the_owner_cannot_load_or_the_persona_lacks_the_text(
    why: str, store, agents, monkeypatch,
) -> None:
    """A run whose builder cannot load the owner, or whose persona does not
    hold the owner's delegation text once, keeps the hand-off: the run is the
    run of OA-7e, and nothing raises. Mutation: skip the ``available`` check
    → the persona swap raises and the run loses the loader, or the specialist
    leaves with no pack to load → red."""
    if why == "no owner module":
        monkeypatch.setattr(ti, "_pack_owner_module", lambda _owner: None)
    before = _names(agents[0].default_options["tools"])
    applied = await _apply(agents, bullets={} if why == "no persona text" else None)
    assert _names(agents[0].default_options["tools"]) == before
    assert applied is not None and applied.record.via_loader == frozenset()
    provider = _provider(agents[0])
    assert provider is not None and provider.loader.offered == ("email", "email-setup")


# ── 3. A held chat ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("held", ["no_egress run", "marked chat"])
async def test_a_held_chat_keeps_the_replacement_and_loads_the_held_pack(
    held: str, store, agents, monkeypatch,
) -> None:
    """Acceptance 5. Nothing in ``_start_pack_turn`` or the offer excludes a
    held chat: a held pack loads with the sends dropped, as layer (a) does
    today. So the replacement holds in a held chat. The specialist leaves,
    the hand-off is refused, and ``load_skill_tools("email")`` loads the pack
    with its hold note and no send tool. A no-egress run gets no MCP server,
    so the lookahead reads False. Mutation: skip the replacement when the
    chat is held → the specialist stays here → red."""
    monkeypatch.setattr(get_settings(), "email_held_card_send", False)
    monkeypatch.setitem(ti._PACK_OWNER_MODULES, EA, load_agent_module(EA_DIR))
    ctx = _run_ctx(no_egress=held == "no_egress run")
    applied = await _apply(agents, run_ctx=ctx)
    assert applied is not None and applied.record.via_loader == {EA}
    assert "email_assistant" not in _names(agents[0].default_options["tools"])
    provider = _provider(agents[0])
    assert provider is not None
    live = _dummies(40)
    with _in_run(applied, **ctx):
        if held == "marked chat":
            await eg.mark_untrusted_answer()
        refused = await agent_tools.call_agent(EA, "Read my mail.")
        answer = await _call(provider.loader, "email", live)
    assert refused.startswith("Not run.") and "load_skill_tools" in refused
    names = set(_names(live[40:]))
    assert names and not {"send_email", "forward_email"} & names, names
    assert answer.endswith(eg.EGRESS_HOLD_NOTE_PACK)


# ── 4. One reachability function ────────────────────────────────────────────


@pytest.mark.parametrize(("plan", "offered", "gone"), [
    (tp.LoaderPlan(("email", "email-setup"), frozenset({EA})), ("email", "email-setup"), True),
    (tp.LoaderPlan(("email", "email-setup"), frozenset()), ("email", "email-setup"), False),
    (tp.LoaderPlan(("email-setup",), frozenset()), ("email-setup",), False),
    (tp.LoaderPlan((), frozenset()), (), False),
])
async def test_the_offer_and_the_removal_read_one_plan(
    plan: tp.LoaderPlan, offered: tuple[str, ...], gone: bool, store, agents, monkeypatch,
) -> None:
    """Acceptance 6. ``tool_packs.plan_loader`` is the only reachability rule:
    patch it, and the catalog of the loader AND the presence of the
    specialist follow its answer at once. Mutation: let
    ``_offer_pack_loader`` or the pool filter compute its own answer → one of
    the four cases disagrees with the plan → red."""
    monkeypatch.setattr(tp, "plan_loader", lambda *_a, **_k: plan)
    applied = await _apply(agents)
    provider = _provider(agents[0])
    assert (provider.loader.offered if provider else ()) == offered
    tools = _names(agents[0].default_options["tools"])
    assert ("email_assistant" not in tools) is gone
    assert (applied is not None and applied.record.via_loader == {EA}) is gone
    if not offered:
        assert applied is None, "no offer and no pack: the run binds nothing"


def test_the_plan_has_one_caller_and_the_offer_computes_nothing() -> None:
    """``plan_loader`` is called in one place, and ``_offer_pack_loader`` holds
    no cap of its own. Mutation: add a second call, or a cap check in the
    offer → red."""
    import inspect

    src = (REPO / "apps/services/orchestrator/orchestrator/_tool_injection.py").read_text(
        encoding="utf-8")
    assert src.count("tool_packs.plan_loader(") == 1
    offer = inspect.getsource(ti._offer_pack_loader)
    assert "MAX_REQUEST_TOOLS" not in offer and "loadable(" not in offer


# ── 5. What does not change ─────────────────────────────────────────────────


async def test_a_background_hand_off_still_runs_with_the_flag_on(
    store, agents, monkeypatch,
) -> None:
    """Acceptance 7. ``call_agent_background`` does not ask the pack rule, so
    mail work for a long job still runs. Mutation: ask the pack rule in
    ``call_agent_background`` → the hand-off is refused → red."""
    ran = _fake_run_agent(monkeypatch)
    applied = await _apply(agents)
    assert applied is not None and applied.record.via_loader == {EA}
    with _in_run(applied):
        assert tp.delegation_refusal(EA) is not None, "non-vacuity: the chat hand-off is refused"
        out = await agent_tools.call_agent_background(EA, "Sort the old mail.")
        await asyncio.sleep(0.05)
    assert ran == [EA] and "load_skill_tools" not in str(out)


async def test_a_delegated_run_never_inherits_the_refusal(store, agents) -> None:
    """The record is server state of the chat run. A delegated run drops the
    pack keys (``executor._without_pack_keys``), so a hand-off from a child
    is never refused here. Mutation: keep the keys → red."""
    applied = await _apply(agents)
    with _in_run(applied, **ex._without_pack_keys()):
        assert tp.delegation_refusal(EA) is None
        assert artifact_context().get(tp.PACKS_KEY) is None


# ── 6. The two-turn path ────────────────────────────────────────────────────


async def test_the_load_of_email_leaves_no_room_for_setup_and_the_next_turn_keeps_the_hand_off(
    store, monkeypatch,
) -> None:
    """Acceptance 8. Turn 1: both packs fit on their own, so the specialist
    leaves. The model loads ``email``. ``email-setup`` then passes 110, and
    its refusal says to finish the mail work and to ask in the next message.
    The refusal sends the model to ``call_agent`` for the setup work, and the
    hand-off is open from then on (fix round 1). A mail tool runs, so ``email``
    is sticky. Turn 2: ``email`` is held, ``email-setup``
    cannot fit, so the plan reaches no owner. The hand-off is open, the rules
    route the setup work to the email assistant, and the run is the run of
    OA-7c. Mutation: refuse the setup load with the old text, or keep the
    hand-off closed after it → the two texts point at each other → red. Mutation: reach the owner
    in turn 2 → the setup work has no path → red."""
    ran = _fake_run_agent(monkeypatch)
    _base(monkeypatch, 60)
    one = _fresh()
    applied = await _apply(one)
    assert applied is not None and applied.record.via_loader == {EA}
    provider = _provider(one[0])
    assert provider is not None
    live = _dummies(61)
    with _in_run(applied):
        loaded = await _call(provider.loader, "email", live)
        assert loaded.startswith("Loaded the Email tools (31 tools).")
        assert tp._EMAIL_SETUP_LOAD_LINE in loaded
        assert tp.delegation_refusal(EA) is not None, "non-vacuity: closed before the refusal"
        setup = await _call(provider.loader, "email-setup", live)
        refusal = tp.delegation_refusal(EA)
        handed = await agent_tools.call_agent(EA, "Make a rule that files newsletters.")
        reader = next(t for t in live[61:] if ti._tool_name(t) == "read_email")
        await reader.func(email_id="m-1")
    assert len(live) + len(SETUP_TOOLS) > tp.MAX_REQUEST_TOOLS, "non-vacuity of the cap"
    assert setup == tp.LOAD_NO_PATH.format(label="Email setup", slug="email-setup", owner=EA)
    assert "call_agent" in setup and EA in setup
    assert refusal is None and ran == [EA] and "ran" in handed
    ran.clear()
    # Turn 2.
    two = _fresh()
    applied2 = await _apply(two, "")
    assert applied2 is not None and applied2.record.slugs == ("email",)
    assert applied2.record.via_loader == frozenset() and applied2.record.replaced == {EA}
    assert _provider(two[0]) is None
    persona = two[0].default_options["instructions"]
    assert tp._EMAIL_SETUP_ROUTE_LINE in persona and tp._EMAIL_SETUP_LOAD_LINE not in persona
    with _in_run(applied2):
        assert tp.delegation_refusal(EA) is None
        out = await agent_tools.call_agent(EA, "Make a rule that files newsletters.")
    assert ran == [EA] and "ran" in out


# ── 7. The plan on its own ──────────────────────────────────────────────────


@pytest.mark.parametrize(("kwargs", "via", "offered"), [
    ({"current": 61}, True, ("email", "email-setup")),
    ({"current": 61, "mcp": True}, False, ("email", "email-setup")),
    ({"current": 61, "available": lambda _o: False}, False, ("email", "email-setup")),
    ({"current": 61, "held": ("email",)}, True, ("email-setup",)),
    ({"current": 61, "held": ("email", "email-setup")}, False, ()),
    ({"current": 61, "allow": ("email",)}, False, ("email",)),
    ({"current": 91, "held": ("email",)}, False, ()),
    ({"current": 61, "held": ("email", "x1", "x2")}, False, ()),
])
def test_plan_loader_on_its_own(kwargs: dict[str, Any], via: bool, offered: tuple) -> None:
    """The pure rule. Every pack of the owner that the run does not hold must
    be allowed, loadable beside MCP, available and within the cap. Mutation:
    any of those checks dropped → the matching row reads the other way →
    red."""
    args = dict(kwargs)
    allow = args.pop("allow", ("email", "email-setup"))
    held = args.pop("held", ())
    plan = tp.plan_loader(allow, held, removable={EA: 1}, **args)
    assert plan.offered == offered
    assert (plan.via_loader == {EA}) is via


# ── 8. Fix round 1: a reached owner is never stranded ───────────────────────


@pytest.mark.parametrize("slug", ["email", "email-setup"])
async def test_a_load_that_refuses_opens_the_hand_off_to_the_owner(
    slug: str, store, agents, monkeypatch,
) -> None:
    """The plan counts no granted Custom App action (they join later, with no
    cap), so it can reach an owner whose pack the live list cannot take. The
    load then refuses, and the refusal must not point at a hand-off that is
    refused. A refusal for a via owner marks it (``PackRun.via_failed``), the
    answer names ``call_agent`` and the owner, and the hand-off runs for the
    rest of the run. Mutation: drop the mark → the hand-off still answers
    ``LOADER_HANDOFF_REFUSED``, and no mail work is possible → red."""
    ran = _fake_run_agent(monkeypatch)
    _base(monkeypatch, 55)
    applied = await _apply(agents)
    assert applied is not None and applied.record.via_loader == {EA}
    provider = _provider(agents[0])
    assert provider is not None
    live = _dummies(100)  # the true count: granted actions that the estimate missed
    with _in_run(applied):
        before = await agent_tools.call_agent(EA, "Read my mail.")
        assert before.startswith("Not run."), "non-vacuity: closed before the load"
        answer = await _call(provider.loader, slug, live)
        after = tp.delegation_refusal(EA)
        out = await agent_tools.call_agent(EA, "Read my mail.")
    assert len(live) + len(sf.SKILL_FAMILIES[slug]["tools"]) > tp.MAX_REQUEST_TOOLS
    assert answer == tp.LOAD_NO_PATH.format(label=tp._label(slug), slug=slug, owner=EA)
    assert "call_agent" in answer and EA in answer
    assert after is None and ran == [EA] and "ran" in out
    assert applied.record.via_failed == {EA}


async def test_an_offered_turn_with_no_pack_logs_no_packs_loaded(store, agents) -> None:
    """The OA-7h eval counts each ``executor.packs_loaded`` line before the
    first model call as a pack load. A turn that offers the loader and builds
    no pack logs the plan on ``executor.pack_plan`` instead. Mutation: log
    ``packs_loaded`` whenever the plan runs → red."""
    from structlog.testing import capture_logs

    with capture_logs() as seen:
        applied = await _apply(agents)
    events = [e["event"] for e in seen]
    assert applied is not None and applied.builds == ()
    assert "executor.packs_loaded" not in events
    plan = [e for e in seen if e["event"] == "executor.pack_plan"]
    assert len(plan) == 1 and plan[0]["via_loader"] == [EA]
    assert plan[0]["offered"] == ["email", "email-setup"]
    with capture_logs() as seen2:
        await _apply(_fresh(), "check my inbox")
    assert "executor.packs_loaded" in [e["event"] for e in seen2], "non-vacuity"


async def test_the_mcp_lookahead_reads_metadata_only(monkeypatch) -> None:
    """``enabled_meta`` opens no header blob and refreshes no token. Mutation:
    read the headers again (``load_headers_by_prefix``) → red."""
    import contextlib as cl

    from gateway.connections import mcp as mcp_registry

    called: list[str] = []

    @cl.asynccontextmanager
    async def fake_session(_org: str):
        yield object()

    async def migrate(_db: Any, _org: str) -> int:
        called.append("migrate")
        return 0

    async def listing(*_a: Any, **_k: Any) -> list[Any]:
        called.append("list")
        return []

    async def headers(*_a: Any, **_k: Any) -> dict[str, Any]:
        called.append("headers")
        return {}

    monkeypatch.setattr(mcp_registry, "tenant_session", fake_session)
    monkeypatch.setattr(mcp_registry, "migrate_legacy", migrate)
    monkeypatch.setattr(mcp_registry.store, "list_by_prefix", listing)
    monkeypatch.setattr(mcp_registry.store, "load_headers_by_prefix", headers)
    assert await mcp_registry.enabled_meta(ORG) == []
    assert called == ["migrate", "list"]


# ── 9. The CRM assistant (OA-8a, `crm-records`) ─────────────────────────────


def _crm_tool_present(agent: Any) -> bool:
    return "crm_assistant" in _names(agent.default_options["tools"])


async def test_crm_is_reached_with_no_pack_held_and_the_texts_agree(
    store, agents, monkeypatch,
) -> None:
    """OA-8a gives CRM no persona bullet (`NO_PERSONA_BULLETS`), so the
    flag-off persona stays byte-identical. The plan still reaches the owner:
    the `crm_assistant` tool leaves, the persona stays as it was, and a
    hand-off answers a refusal that names `load_skill_tools` and
    `crm-records`. The rules of the pack say that no CRM agent exists, so the
    refusal and the rules agree. Mutation: demand a bullet of every owner in
    `_available` → CRM is never reached → red."""
    ran = _fake_run_agent(monkeypatch)
    persona = agents[0].default_options["instructions"]
    assert _crm_tool_present(agents[0]), "non-vacuity"
    applied = await _apply(agents, allowed=CRM_PACK)
    assert applied is not None and applied.builds == ()
    assert applied.record.via_loader == {CRM} and applied.record.replaced == {CRM}
    assert not _crm_tool_present(agents[0])
    assert agents[0].default_options["instructions"] == persona
    provider = _provider(agents[0])
    assert provider is not None and provider.loader.offered == ("crm-records",)
    assert getattr(agents[0], ti.PACK_REFUSED_ATTR) == {CRM}
    with _in_run(applied):
        refused = await agent_tools.call_agent(CRM, "How is the pipeline?")
        background = await agent_tools.call_agent_background(CRM, "Sync the notes.")
        await asyncio.sleep(0.05)
    assert refused == tp.LOADER_HANDOFF_REFUSED.format(owner=CRM, packs="crm-records")
    assert "load_skill_tools" in refused and "crm-records" in refused
    assert "No CRM agent exists" in tp.PACKS["crm-records"].rules
    assert "No CRM agent exists" in tp.PACKS["crm-records"].delegation_refusal
    assert ran == [CRM] and background != refused, "the background hand-off still runs"


async def test_both_owners_are_reached_together(store, agents) -> None:
    """With both owners allowed and room, both specialists leave. The persona
    loses the email bullet and nothing else. Mutation: stop at the first owner
    of the plan → one specialist stays → red."""
    persona = agents[0].default_options["instructions"]
    applied = await _apply(agents, allowed=ALL | CRM_PACK)
    assert applied is not None and applied.record.via_loader == {EA, CRM}
    tools = _names(agents[0].default_options["tools"])
    assert "email_assistant" not in tools and "crm_assistant" not in tools
    assert agents[0].default_options["instructions"] == persona.replace(_bullet(), "", 1)
    provider = _provider(agents[0])
    assert provider is not None and provider.loader.offered == ("email", "email-setup", "crm-records")


@pytest.mark.parametrize(("base", "via"), [(60, True), (102, False)])
async def test_crm_is_not_reached_over_the_cap(
    base: int, via: bool, store, agents, monkeypatch,
) -> None:
    """The cap reads the tools of `crm-records` against 110. At base 102 the
    count is 103 with the load tool, and the 9 pack tools do not fit even with
    the specialist gone, so the tool and the hand-off stay. Mutation: drop the
    cap check from the plan → base 102 loses the specialist → red."""
    _base(monkeypatch, base)
    assert 102 + 1 - 1 + len(CRM_TOOLS) > tp.MAX_REQUEST_TOOLS, "non-vacuity of the cap"
    applied = await _apply(agents, allowed=CRM_PACK)
    assert _crm_tool_present(agents[0]) is (not via)
    assert (applied is not None and applied.record.via_loader == {CRM}) is via
    if not via:
        assert applied is None or tp.delegation_refusal(CRM, {tp.PACKS_KEY: applied.record}) is None


async def test_a_refused_crm_load_opens_the_hand_off(store, agents, monkeypatch) -> None:
    """The fallback of fix round 1 holds for CRM. A true count over the cap
    refuses the load, the text names `call_agent` and `crm-assistant`, and the
    hand-off runs. Mutation: drop the mark → the refusal and the hand-off
    point at each other → red."""
    ran = _fake_run_agent(monkeypatch)
    _base(monkeypatch, 55)
    applied = await _apply(agents, allowed=CRM_PACK)
    assert applied is not None and applied.record.via_loader == {CRM}
    provider = _provider(agents[0])
    assert provider is not None
    live = _dummies(105)
    with _in_run(applied):
        assert (await agent_tools.call_agent(CRM, "x")).startswith("Not run.")
        answer = await _call(provider.loader, "crm-records", live)
        out = await agent_tools.call_agent(CRM, "How is the pipeline?")
    assert answer == tp.LOAD_NO_PATH.format(label=tp._label("crm-records"), slug="crm-records", owner=CRM)
    assert "call_agent" in answer and CRM in answer
    assert ran == [CRM] and "ran" in out and applied.record.via_failed == {CRM}


async def test_an_owner_with_no_bullet_and_no_exemption_is_not_reached(
    store, agents, monkeypatch,
) -> None:
    """Only an owner in `NO_PERSONA_BULLETS` may lack a bullet. Take CRM out of
    that set and the plan does not reach it: the persona would keep no text to
    swap, and a missing bullet of any other owner must not strand the model.
    The specialist and the hand-off stay. Mutation: accept a missing bullet
    for every owner → CRM is reached → red."""
    monkeypatch.setattr(tp, "NO_PERSONA_BULLETS", frozenset())
    persona = agents[0].default_options["instructions"]
    applied = await _apply(agents, allowed=CRM_PACK)
    assert _crm_tool_present(agents[0])
    assert agents[0].default_options["instructions"] == persona
    assert applied is None or applied.record.via_loader == frozenset()
