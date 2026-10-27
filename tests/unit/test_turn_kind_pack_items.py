"""WS-67 OA-7f: layer (c), the pack items of the turn-kind request.

``project-docs/specs/one_assistant_context.md`` §4.2.2 (layer (c)) and §4.2.8
(OA-7f). The request of ``tier_policy.turn_kind`` holds one item for each pack
that the run can still load (``test_tier_policy.py``, ``TestTurnKindPackItems``).
This file fences the other half: which packs the executor offers, and how a
"yes" loads the pack in the first model request. Each case names, in its
docstring, the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_turn_kind_pack_items.py -q``.
"""
from __future__ import annotations

import ast
import types
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import skill_families as sf
from acb_skills import tier_policy
from acb_skills import tool_packs as tp
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit.test_load_skill_tools import (  # noqa: F401  (fixtures and helpers)
    ALL,
    EA,
    EMAIL_TOOLS,
    ORG,
    SETUP_TOOLS,
    WORDS,
    REPO,
    _apply,
    _bound,
    _dummies,
    _in_run,
    _loader,
    _names,
    _provider,
    _record,
    _run_ctx,
    _stub_email,
    _tenant,
    orchestrator,
    store,
)


class _Context:
    """The part of MAF's ``SessionContext`` that ``before_run`` writes."""

    def __init__(self, tools: list[Any] | None = None) -> None:
        self.tools: list[Any] = list(tools or [])
        self.instructions: list[str] = []
        self.middleware: list[Any] = []

    def extend_tools(self, _source: str, tools: Any) -> None:
        self.tools.extend(tools)

    def extend_instructions(self, _source: str, text: Any) -> None:
        self.instructions.append(str(text))

    def extend_middleware(self, _source: str, items: Any) -> None:
        self.middleware.extend(items)


def _agent(pool: list[Any]) -> Any:
    return types.SimpleNamespace(default_options={"tools": pool})


async def _before_run(loader: tp.LoadSkillTools, pool: list[Any]) -> _Context:
    context = _Context()
    provider = tp.LoadSkillToolsProvider(loader)
    with _in_run(loader):
        await provider.before_run(agent=_agent(pool), session=None, context=context, state={})
    return context


# ── 1. What the executor offers ─────────────────────────────────────────────


async def test_the_offer_leaves_out_a_pack_that_layer_a_loaded(
    store, orchestrator, monkeypatch,
) -> None:
    """The items name only the packs that layer (a) did not load. Mutation:
    offer every allowed pack → ``email`` is asked about again → red."""
    monkeypatch.setattr(ti, "_pack_base_count", lambda *_a, **_k: 40)
    applied = await _apply(orchestrator, "check my inbox")
    assert applied is not None and applied.record.slugs == ("email",)
    assert ex._pack_loader_offer(orchestrator[0]) == ("email-setup",)


async def test_the_offer_leaves_out_a_pack_that_the_flag_disallows(
    store, orchestrator,
) -> None:
    """``packs_on`` and the admin toggles narrow the offer. Mutation: offer
    each registered pack → ``email-setup`` is asked about in an org that the
    flag names for ``email`` only → red."""
    await _apply(orchestrator, WORDS, allowed=frozenset({"email"}))
    assert ex._pack_loader_offer(orchestrator[0]) == ("email",)


async def test_the_offer_leaves_out_a_pack_that_does_not_fit_the_caps(
    store, orchestrator, monkeypatch,
) -> None:
    """Mutation: offer past the 110-tool cap → ``email`` is asked about at a
    base of 80 → red."""
    monkeypatch.setattr(ti, "_pack_base_count", lambda *_a, **_k: 80)
    await _apply(orchestrator, WORDS)
    assert ex._pack_loader_offer(orchestrator[0]) == ("email-setup",)


def test_a_run_with_no_loader_offers_nothing() -> None:
    """A room, a WhatsApp run, a delegated run and a run with the flag off
    never get the loader (``test_load_skill_tools.py``), so they ask for no
    pack. Mutation: offer a default list when there is no loader → red."""
    assert ex._pack_loader_offer(types.SimpleNamespace(context_providers=[])) == ()
    assert ex._pack_loader_offer(types.SimpleNamespace()) == ()
    assert ex._pack_loader_offer(None) == ()


def _calls(path: str, name: str) -> set[str]:
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    found: set[str] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and fn.name != name:
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == name):
                    found.add(fn.name)
    return found


def test_the_executor_asks_the_offer_and_applies_the_answer_in_one_place() -> None:
    """``_pack_loader_offer`` has one caller (the tier question of the stream
    run) and ``_apply_turn_pack_items`` has one (the stream run, after the
    tier wait). Mutation: pass an offer from the batch path or a sub-agent →
    red."""
    executor = "apps/services/orchestrator/orchestrator/executor.py"
    assert _calls(executor, "_apply_turn_pack_items") == {"run_agent_stream"}
    # ``_ask_run_tier`` is nested in the stream run, so both names show.
    assert _calls(executor, "_pack_loader_offer") == {"_ask_run_tier", "run_agent_stream"}
    src = (REPO / executor).read_text(encoding="utf-8")
    start = src.index("async def _tier_policy_for_run")
    assert 'pack_offer' in src[start:start + 1500]
    for batch in ("_batch_tier = await _tier_policy_for_run(", "_sub_tier = await _tier_policy_for_run("):
        at = src.index(batch)
        assert "pack_offer" not in src[at:at + 700], batch


async def test_the_policy_call_passes_packs_only_when_the_run_offers_some(monkeypatch) -> None:
    """With the flag off ``_tier_policy_for_run`` calls ``turn_kind`` with the
    arguments of before, so the request is the same bytes. Mutation: always
    pass ``packs`` → the empty call carries the keyword → red."""
    seen: list[dict[str, Any]] = []

    async def fake(message: str, tools: Any, effort: Any, **kw: Any) -> Any:
        seen.append(kw)
        return tier_policy.TurnKind("chat", "system_one", 5, packs=tuple(kw.get("packs", ())[:1]))

    monkeypatch.setattr(tier_policy, "turn_kind", fake)
    monkeypatch.setattr(ex, "_tier_policy_covers", lambda _name: True)
    monkeypatch.setattr(ex, "_agent_default_tier", lambda *a, **k: "tier-balanced")
    monkeypatch.setattr(ex, "_agent_tool_names", lambda _a: ["vocabulary"])
    common: dict[str, Any] = dict(
        run_id="r", thread_id=None, model=None, think_mode="auto", event_payload={"message": "hello"},
        config={}, agent_md_model="", is_copilot=False, emit=False)
    plain = await ex._tier_policy_for_run("orchestrator", object(), **common)
    packed = await ex._tier_policy_for_run(
        "orchestrator", object(), pack_offer=("email", "email-setup"), **common)
    assert seen[0] == {"read_effort": False, "default": "tier-balanced"}
    assert seen[1]["packs"] == ("email", "email-setup")
    assert plain.packs == () and packed.packs == ("email",)
    assert plain.run_choice() == packed.run_choice()


# ── 2. The answer reaches the loader ────────────────────────────────────────


async def test_the_executor_hands_the_asked_packs_to_the_loader(
    store, orchestrator,
) -> None:
    """Only a pack that the loader offered counts. Mutation: take any slug of
    the answer → ``crm-records`` joins ``early`` → red. Mutation: ignore the
    answer → ``early`` stays empty → red."""
    await _apply(orchestrator, WORDS)
    loader = _provider(orchestrator[0]).loader
    assert loader.early == ()
    ex._apply_turn_pack_items(orchestrator, types.SimpleNamespace(packs=("email-setup", "crm-records", "nope")))
    assert loader.early == ("email-setup",)


async def test_no_asked_pack_changes_nothing(store, orchestrator) -> None:
    """A turn with no yes, no tier run and no loader leaves the run as it
    was, and never raises. Mutation: load the whole offer when the answer is
    empty → ``early`` fills → red."""
    await _apply(orchestrator, WORDS)
    loader = _provider(orchestrator[0]).loader
    for tier_run in (None, types.SimpleNamespace(packs=()), types.SimpleNamespace()):
        ex._apply_turn_pack_items(orchestrator, tier_run)
    ex._apply_turn_pack_items([], types.SimpleNamespace(packs=("email",)))
    ex._apply_turn_pack_items([types.SimpleNamespace()], types.SimpleNamespace(packs=("email",)))
    assert loader.early == ()


# ── 3. The load, before the first request ───────────────────────────────────


async def test_a_yes_loads_the_pack_through_the_one_guarded_builder(store, monkeypatch) -> None:
    """The tools come from ``_pack_tools`` with the run key and the live
    names, as in layer (b). They join the first request, and the rules ride
    as an instruction of the turn. Mutation: build with another builder or
    with no ``holds`` → the call list differs → red. Mutation: add the tools
    and not the rules → no instruction → red."""
    _stub_email(monkeypatch)
    calls: list[tuple[str, str, frozenset[str]]] = []
    real = ti._pack_tools

    async def spy(slug: str, run_ctx: Any, **kw: Any) -> Any:
        calls.append((slug, str(run_ctx.get(tp.PACK_RUN_KEY)), frozenset(kw.get("holds") or ())))
        return await real(slug, run_ctx, **kw)

    monkeypatch.setattr(ti, "_pack_tools", spy)
    loader = _loader(_record())
    pool = _dummies(40)
    assert loader.set_early(["email"]) == ("email",)
    context = await _before_run(loader, pool)
    # The live names hold the load tool too, so the pack never shadows it.
    assert calls == [("email", "key-oa7e", frozenset([*_names(pool), tp.LOAD_TOOL]))]
    assert [t.name for t in context.tools if t.name in EMAIL_TOOLS] == list(EMAIL_TOOLS)
    assert tp.LOAD_TOOL in [getattr(t, "name", "") for t in context.tools]
    assert len(context.instructions) == 1 and "email" in context.instructions[0].lower()
    assert all(getattr(t.func, "__cc_gated__", False) for t in context.tools if t.name in EMAIL_TOOLS)
    assert loader.record.holds() == ("email",)


async def test_a_load_by_the_turn_kind_is_not_sticky(store, monkeypatch) -> None:
    """The sticky rule is unchanged: a pack becomes sticky after one of its
    tools runs. Mutation: write the sticky state in the load → the next
    turn reads a sticky pack → red."""
    _stub_email(monkeypatch)
    loader = _loader(_record())
    loader.set_early(["email"])
    with _tenant():
        await _before_run(loader, _dummies(40))
        later = await tp.begin_turn(ORG, "chat-oa7e-1", "jane@acme.test", "")
    assert later.sticky == ()


async def test_the_packs_load_once_for_a_retry_of_the_turn(store, monkeypatch) -> None:
    """A Tier 2 retry runs the agent again. It gets the same tools and text,
    and the builder runs once. Mutation: build again → the second build finds
    the pack held and the retry has no tools → red."""
    _stub_email(monkeypatch)
    built: list[str] = []
    real = ti._build_pack_for_load

    async def spy(slug: str, ctx: Any, holds: Any) -> Any:
        built.append(slug)
        return await real(slug, ctx, holds)

    loader = _loader(_record(), build=spy)
    loader.set_early(["email"])
    first = await _before_run(loader, _dummies(40))
    second = await _before_run(loader, _dummies(40))
    assert built == ["email"]
    assert _names(first.tools) == _names(second.tools) and second.instructions == first.instructions


async def test_a_load_by_the_turn_kind_obeys_the_caps(store, monkeypatch) -> None:
    """The caps of layer (b) hold: 3 packs, 35 tools a pack, 110 tools. A
    refused pack loads nothing and the run goes on. Mutation: skip the
    guards in the early path → a pack loads past a cap → red."""
    _stub_email(monkeypatch)
    # 110 tools: the live list is full.
    loader = _loader(_record())
    loader.set_early(["email"])
    context = await _before_run(loader, _dummies(100))
    assert [t for t in context.tools if t.name in EMAIL_TOOLS] == []
    assert context.instructions == [] and loader.record.holds() == ()
    # 3 packs held.
    full = _loader(_record(("email", "email-setup")))
    full.record.add("extra", False)
    full.set_early(["email"])
    assert full.early == ("email",)
    context = await _before_run(full, _dummies(10))
    assert [t for t in context.tools if t.name in EMAIL_TOOLS] == []
    # One pack fits, the next does not: 60 + 1 + 31 + 28 is over 110.
    both = _loader(_record())
    both.set_early(["email", "email-setup"])
    context = await _before_run(both, _dummies(60))
    got = {t.name for t in context.tools}
    assert set(EMAIL_TOOLS) <= got and not set(SETUP_TOOLS) <= got
    assert both.record.holds() == ("email",)


async def test_a_load_by_the_turn_kind_refuses_beside_an_mcp_server(store, monkeypatch) -> None:
    """MAF keeps an MCP server in ``agent.mcp_tools`` and adds its functions
    after ``before_run``, so the live list cannot show it. A mail pack never
    joins such a run. Mutation: drop ``extra_live=mcp`` in ``_build_early``
    (or the ``mcp_tools`` read) → the pack loads → red. A marked loader tool
    in the pool is refused too."""
    from agent_framework import MCPStreamableHTTPTool

    _stub_email(monkeypatch)
    server = MCPStreamableHTTPTool(name="registry", url="http://127.0.0.1:9/mcp")
    loader = _loader(_record())
    loader.set_early(["email"])
    context = _Context()
    agent = types.SimpleNamespace(default_options={"tools": _dummies(10)}, mcp_tools=[server])
    with _in_run(loader):
        await loader.load_early(agent, context)
    assert [t for t in context.tools if t.name in EMAIL_TOOLS] == [] and context.instructions == []
    assert loader.record.holds() == ()
    # Non-vacuity: the same run with no server loads the pack.
    free = _loader(_record())
    free.set_early(["email"])
    plain = _Context()
    with _in_run(free):
        await free.load_early(types.SimpleNamespace(default_options={"tools": _dummies(10)}), plain)
    assert {t.name for t in plain.tools} >= set(EMAIL_TOOLS)


async def test_the_early_build_carries_the_run_key_the_server_minted(store, monkeypatch) -> None:
    """The build context holds the key of the loader even when the frame
    holds none. Mutation: drop ``PACK_RUN_KEY`` from the early context → the
    builder sees no key → red."""
    _stub_email(monkeypatch)
    seen: list[Any] = []
    real = ti._build_pack_for_load

    async def spy(slug: str, ctx: Any, holds: Any) -> Any:
        seen.append(ctx.get(tp.PACK_RUN_KEY))
        return await real(slug, ctx, holds)

    loader = _loader(_record(), build=spy)
    loader.set_early(["email"])
    with _tenant(), _bound(**_run_ctx()):
        await loader.load_early(_agent(_dummies(10)), _Context())
    assert seen == ["key-oa7e"]


def test_maf_keeps_normalize_tools_and_a_missing_one_loads_nothing(monkeypatch) -> None:
    """``_agent_shape`` imports the private ``agent_framework._tools.normalize_tools``.
    Mutation: MAF renames it → the pin fails. Mutation: pass raw tools when
    the import fails → the second assertion fails."""
    import builtins

    from agent_framework._tools import normalize_tools

    assert callable(normalize_tools)
    real = builtins.__import__

    def fake(name: str, *a: Any, **k: Any) -> Any:
        if name == "agent_framework._tools":
            raise ImportError("gone")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert tp._agent_shape(_dummies(2)) == ()


async def test_the_loader_with_no_asked_pack_adds_only_the_load_tool(store) -> None:
    """Mutation: load the offer when ``early`` is empty → tools and an
    instruction appear → red."""
    loader = _loader(_record())
    context = await _before_run(loader, _dummies(10))
    assert [t.name for t in context.tools] == [tp.LOAD_TOOL] and context.instructions == []


async def test_a_failed_builder_loads_nothing_and_the_turn_goes_on(store) -> None:
    """Mutation: let the builder's error out of ``before_run`` → the run
    fails before its first request → red."""

    async def none(slug: str, ctx: Any, holds: Any) -> Any:
        return None

    loader = _loader(_record(), build=none)
    loader.set_early(["email"])
    context = await _before_run(loader, _dummies(10))
    assert [t.name for t in context.tools] == [tp.LOAD_TOOL] and loader.record.holds() == ()


# ── 5. The CRM pack in layer (c) (WS-67 OA-8a) ──────────────────────────────

CRM_NINE = set(sf.SKILL_FAMILIES["crm-records"]["tools"])


@pytest.mark.parametrize(("mode", "dropped"), [
    ("mirror", {"create_lead", "update_deal_status", "convert_lead"}),
    ("live", set()),
])
async def test_layer_c_drops_the_mirror_record_writes_through_load_early(
    store, monkeypatch, mode: str, dropped: set[str],
) -> None:
    """The pack of a turn-kind "yes" builds in ``before_run``, AFTER the
    executor's ``_drop_mirror_record_writes``, through the real ``load_early``
    path. In Mirror mode the three record writes are not in the request, and
    ``log_activity`` stays. Mutation: take the drop out of ``_pack_tools`` ->
    the Mirror case holds all 9 tools -> red."""
    async def crm_mode() -> str:
        return mode

    monkeypatch.setattr("acb_skills.crm_tools.crm_mode", crm_mode)
    loader = _loader(_record(), offered=("crm-records",))
    assert loader.set_early(["crm-records"]) == ("crm-records",)
    context = await _before_run(loader, _dummies(10))
    got = {t.name for t in context.tools if t.name in CRM_NINE}
    assert got == CRM_NINE - dropped and "log_activity" in got
    assert loader.record.holds() == ("crm-records",)
    assert len(context.instructions) == 1 and "CRM TOOLS." in context.instructions[0]
