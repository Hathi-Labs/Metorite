"""WS-67 TS-2a PR 3: ``find_tools`` in a live run, through the real MAF loop.

``project-docs/specs/one_assistant_context.md`` §4.2.9, the block TS-2a, "PR 3
must", "The join" and "Acceptance". PR 2 built the tool, the middleware and
the provider with no caller. PR 3 wires them in
``_tool_injection._apply_tool_packs`` behind ``TOOL_SEARCH_ORGS``. Each case
names, in its docstring, the mutation that turns it red (R7).

1. The loop. One stream run of the orchestrator (``run_agent_stream``) with a
   scripted model. Layer (a) loads the core ``studio`` pack alone (a sticky
   pack of the chat), so ``studio-more`` holds tools to find. The model calls
   ``find_tools``, and the SECOND request holds the found schema. It fails
   when the setup builds outside the run frame (an empty catalog), when the
   finder gets another record than the apply's, when ``add_tools`` is
   skipped, and when the provider attaches without its middleware.
2. The join. ``FindToolsProvider`` comes BEFORE ``LoadSkillToolsProvider`` in
   the one list of ``_offer_pack_loader``, so layer (c) counts
   ``find_tools``. With 108 own tools and the load tool, the run holds 109
   live tools. A pack of 1 tool then does not fit, and the request holds 110
   tools. With the order swapped, it would hold 111.
3. The ``allow`` rule. An admin who turned ``studio`` off gets no
   ``studio-more`` candidate, because the setup reads the ``allow`` of the
   apply, never a fresh flag read.
4. The name. No platform tool, no pack tool and no tool of the orchestrator's
   own list is named ``find_tools``.

The mutations, each run once and proved red (the report of PR 3 lists the
counts):

L1. ``_apply_tool_packs`` gives ``_search_setup`` the ``run_ctx`` (no run
    key) in place of ``build_ctx``.
L2. ``_make_finder`` passes a copy of ``applied.record``.
L3. ``FindTools._find`` skips ``context.add_tools``.
L4. ``FindToolsProvider.before_run`` adds no middleware.
L5. ``_offer_pack_loader`` lists the loader before the finder.
L6. ``_apply_tool_packs`` gives ``_search_setup`` the fresh ``allowed`` in
    place of ``allow``.

Run: ``uv run pytest tests/unit/test_find_tools_maf_loop.py -q -p no:xdist``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterable, Iterator
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common.db import bind_tenant, release_tenant
from acb_common.tenant_redis import TenantRedis, key, organization_scope
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    tool,
)
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit._native_maf_harness import text_turn, tool_turn
from tests.unit.test_tool_search_off import (
    CONFIG,
    MEMBER,
    ORG,
    _flags,
    _studio,
    drive,
    session_of,
    store,  # noqa: F401  (a fixture)
    tool_names,
)

#: The follow pack of the core Studio pack, and its tools.
MORE = "studio-more"
MORE_TOOLS = frozenset(sf.SKILL_FAMILIES[MORE]["tools"])
CORE_TOOLS = frozenset(sf.SKILL_FAMILIES["studio"]["tools"])
#: The tool that the scripted model finds, and its query.
FOUND = "studio_delete_package"
QUERY = json.dumps({"query": "delete an app for good"})
#: The first line of the rules of ``studio-more`` (``_STUDIO_MORE_RULES``).
MORE_RULES = "STUDIO SKILL, RECORD AND WORKFLOW TOOLS."


async def _seed_sticky(store: Any, name: str, slug: str = "studio") -> None:  # noqa: F811
    """The chat of turn *name* used pack *slug* one turn ago, so layer (a)
    loads it again, and only it, on a turn with no pack words."""
    with organization_scope(ORG):
        state = key(tp.NAMESPACE, session_of(name), tp._member_part(MEMBER))
        client = TenantRedis(store)
        await client.hset(state, slug, json.dumps({"first": [1, 0], "used": 1}))
        await client.hset(state, "~turn", "1")


def _schema(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in body.get("tools") or []:
        if item.get("function", {}).get("name") == name:
            return item["function"]
    return None


def _tool_results(body: dict[str, Any]) -> list[str]:
    return [
        str(m.get("content") or "") for m in body.get("messages") or [] if m.get("role") == "tool"
    ]


# ── 1. The loop ─────────────────────────────────────────────────────────────


def _loop_run(store: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:  # noqa: F811
    _flags(monkeypatch, ORG)
    _studio(monkeypatch)
    asyncio.run(_seed_sticky(store, "core_alone"))
    return drive(
        monkeypatch,
        "core_alone",
        words="hello there",
        turns=[tool_turn(tp.FIND_TOOL, QUERY), text_turn("Done.")],
    )


def test_the_second_request_holds_the_found_schema(store, monkeypatch) -> None:  # noqa: F811
    """The acceptance of TS-2a. Layer (a) loads the core alone, the first
    request holds ``find_tools`` with ``studio-more`` in its catalog and no
    tool of ``studio-more``. The model calls ``find_tools``, and the second
    request of the SAME run holds the full schema of the found tool. The
    answer gives the rules of ``studio-more`` once. Mutations L1 to L4 ->
    the second request lacks the tool -> red."""
    got = _loop_run(store, monkeypatch)
    kinds = [e.get("type") for e in got["events"]]
    assert kinds[-1] == "RUN_FINISHED" and "RUN_ERROR" not in kinds, got["events"]
    assert len(got["bodies"]) == 2, "the model asked twice"
    first, second = got["bodies"]
    one, two = tool_names(first), tool_names(second)
    # The first request: the core, find_tools before the loader, no follow tool.
    assert set(one) >= CORE_TOOLS, sorted(CORE_TOOLS - set(one))
    assert not MORE_TOOLS & set(one), sorted(MORE_TOOLS & set(one))
    assert one.count(tp.FIND_TOOL) == 1
    finder = _schema(first, tp.FIND_TOOL)
    assert finder is not None and tp.catalog((MORE,)) in finder["description"]
    assert "- studio:" not in finder["description"], "a loaded pack is no candidate"
    # The second request: the found tool with its full schema, after the rest.
    assert FOUND in two and two[: len(one)] == one, two
    schema = _schema(second, FOUND)
    assert schema is not None and "package_id" in schema["parameters"]["properties"]
    answer = [text for text in _tool_results(second) if text.startswith("Found ")]
    assert len(answer) == 1 and f"- {FOUND}:" in answer[0], _tool_results(second)
    assert answer[0].count(MORE_RULES) == 1


def test_the_loop_binds_one_finder_with_the_record_of_the_apply(store, monkeypatch) -> None:  # noqa: F811
    """ONE ``FindTools`` per run, and its record is the record that the run
    binds (``PACKS_KEY``), the same ``PackRun`` that the loader holds, so the
    two share one lock. Mutation L2 -> red."""
    made: list[Any] = []
    real = tp.FindTools.__init__

    def spy(self: Any, *a: Any, **k: Any) -> None:
        made.append(self)
        real(self, *a, **k)

    monkeypatch.setattr(tp.FindTools, "__init__", spy)
    got = _loop_run(store, monkeypatch)
    assert len(made) == 1
    finder = made[0]
    loader = ex._pack_loader(got["views"][0])
    assert loader is not None and finder.record is loader.record
    assert finder.run_key == loader.run_key and finder._lock is loader._lock
    assert finder.found[0] == FOUND and finder.searches == 1
    assert len(finder.found) <= tp.FIND_LIMIT and set(finder.found) <= MORE_TOOLS


# ── 2. The join: the order of the providers ─────────────────────────────────


class _Recorder(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """A chat client that answers once and keeps the tools of each request."""

    def __init__(self) -> None:
        super().__init__()
        self.tools: list[list[str]] = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        from acb_skills.tool_guard import tool_name

        self.tools.append([tool_name(t) for t in (options or {}).get("tools") or []])
        content = Content.from_text("ok")
        if stream:

            async def _gen() -> AsyncIterable[ChatResponseUpdate]:
                yield ChatResponseUpdate(
                    contents=[content], role="assistant", response_id="r", message_id="m"
                )

            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(messages=[Message("assistant", [content])], response_id="r")

        return _one()


def _named(name: str) -> Any:
    async def fn() -> str:
        return "ok"

    return tool(fn, name=name, description=name, approval_mode="never_require")


def _own_tools(count: int) -> list[Any]:
    return [_named(f"own_{i}") for i in range(count)]


@contextlib.contextmanager
def _in_run(applied: Any) -> Iterator[None]:
    token = bind_tenant(ORG)
    try:
        with artifact_context_scope():
            bind_artifact_context(
                run_id="r-ts2a-join",
                session_id="chat-ts2a-join",
                agent_name="orchestrator",
                member=MEMBER,
                no_egress=False,
                **applied.context(),
            )
            yield
    finally:
        release_tenant(token)


async def test_layer_c_counts_find_tools_and_the_request_stays_at_110(monkeypatch) -> None:
    """108 own tools and ``load_skill_tools`` make 109 live tools. Layer (c)
    asks for ``studio``, whose build gives 1 tool. With ``find_tools`` in the
    live list first, 110 + 1 does not fit, so the request holds 110 tools.
    Mutation L5 (the loader first) -> layer (c) sees 109 and loads the tool
    -> 111 tools -> red."""
    one = _named("studio_draft")

    async def build(slug: str, ctx: Any, holds: frozenset[str]) -> Any:
        return ti.PackBuild(slug=slug, owner="studio-builder", held=False, tools=(one,))

    monkeypatch.setattr(ti, "_build_pack_for_load", build)
    client = _Recorder()
    agents = [Agent(client=client, name="orchestrator", tools=_own_tools(108))]
    turn = tp.PackTurn(ORG, "chat-ts2a-join", tp._member_part(MEMBER), 1, (), "", True)
    choice = tp.choose(turn, allowed=frozenset(), base_tools=0)
    record = tp.PackRun.of(turn, choice, [], offered=("studio",))
    applied = ti.PackApply(builds=(), record=record, run_key="key-ts2a-join")
    plan = tp.LoaderPlan(offered=("studio",))
    assert ti._offer_pack_loader(agents, applied, plan, tp.FindCandidates()) is True
    kinds = [type(p).__name__ for p in agents[0].context_providers]
    assert kinds[-2:] == ["FindToolsProvider", "LoadSkillToolsProvider"], kinds
    assert ex._pack_loader(agents[0]).set_early(["studio"]) == ("studio",)
    with _in_run(applied):
        await agents[0].run("hello")
    sent = client.tools[0]
    assert len(sent) == tp.MAX_REQUEST_TOOLS == 110, len(sent)
    assert tp.FIND_TOOL in sent and "load_skill_tools" in sent and "studio_draft" not in sent


async def test_with_no_offer_the_list_holds_the_finder_alone() -> None:
    """With the search on and no pack to offer, the one list holds only
    ``FindToolsProvider``. With the search off and no offer, nothing
    attaches, as before TS-2a. Mutation: attach the loader with no offer, or
    the finder with no candidates -> red."""
    turn = tp.PackTurn(ORG, "chat-ts2a-alone", tp._member_part(MEMBER), 1, (), "", True)
    record = tp.PackRun.of(turn, tp.choose(turn, allowed=frozenset(), base_tools=0), [])
    applied = ti.PackApply(builds=(), record=record, run_key="key-ts2a-alone")
    agent = Agent(client=_Recorder(), name="orchestrator", tools=_own_tools(2))
    agents = [agent]
    assert ti._offer_pack_loader(agents, applied, tp.LoaderPlan(), None) is False
    assert agents[0] is agent
    assert ti._offer_pack_loader(agents, applied, tp.LoaderPlan(), tp.FindCandidates()) is False
    kinds = [type(p).__name__ for p in agents[0].context_providers]
    assert kinds.count("FindToolsProvider") == 1 and "LoadSkillToolsProvider" not in kinds
    assert agents[0] is not agent, "a view of the run, never the shared agent"


# ── 3. The allow rule ───────────────────────────────────────────────────────


async def _apply_plain(
    monkeypatch,
    store: Any,  # noqa: F811
    disabled: frozenset[str],
    turn: Any = None,
) -> Any:
    from tests.unit.test_tool_packs_choice import _bullets, _run_ctx

    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: disabled)
    from orchestrator.agents import build_orchestrator_agent

    agents = [build_orchestrator_agent(with_history=False)]
    token = bind_tenant(ORG)
    try:
        if turn is None:
            turn = await tp.begin_turn(ORG, "chat-ts2a-allow", MEMBER, "hello there")
        applied = await ti._apply_tool_packs(
            agents,
            turn,
            _run_ctx(),
            allowed=tp.with_followers({"studio"}),
            agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]),
            agent_config=CONFIG,
            bullets=_bullets(),
            offer_loader=True,
        )
    finally:
        release_tenant(token)
    return applied, ex._pack_finder(agents[0])


async def test_an_admin_who_turned_studio_off_gets_no_studio_more_candidate(
    store,  # noqa: F811
    monkeypatch,
) -> None:
    """The setup reads the ``allow`` of the apply: the flag less the admin's
    toggles, widened by FOLLOWS. With ``studio`` off, ``studio-more`` is off
    too, so it is no candidate. The run still holds ``find_tools`` (decision
    13), with an empty catalog. Mutation L6 (a fresh ``allowed``) -> red."""
    _flags(monkeypatch, ORG)
    _studio(monkeypatch)
    applied, finder = await _apply_plain(monkeypatch, store, frozenset())
    assert applied is not None and finder is not None
    assert finder.candidates.packs == ("studio", MORE), "non-vacuity: both on"
    applied, finder = await _apply_plain(monkeypatch, store, frozenset({"studio"}))
    assert applied is not None, "the flag on: the apply binds the run key"
    assert finder is not None and finder.candidates.packs == ()
    assert not set(finder.candidates.tools) & (MORE_TOOLS | CORE_TOOLS)
    assert "- studio" not in finder.tool.description


async def test_a_sticky_pack_that_left_at_the_start_is_no_candidate(
    store,  # noqa: F811
    monkeypatch,
) -> None:
    """Opus review P2-1. A sticky ``studio-more`` that leaves at the start of
    the turn (decay: no use in 19 member turns) is in ``PackRun.gone``, and the
    line of the turn names it (``left_line``). The search must not offer it
    again in the same turn, so it is no candidate. The core stays one. The
    line of the turn is the same text with the flag on and off. Mutation L7
    (the setup gets ``choice.slugs`` only) -> ``studio-more`` is a candidate
    -> red."""
    _studio(monkeypatch)
    old = tp.Sticky(slug=MORE, first=(1, 0), used=1)
    lines: dict[bool, str] = {}
    for on in (False, True):
        _flags(monkeypatch, ORG if on else "")
        turn = tp.PackTurn(
            ORG, "chat-ts2a-gone", tp._member_part(MEMBER), 20, (old,), "hello there", True
        )
        applied, finder = await _apply_plain(monkeypatch, store, frozenset(), turn=turn)
        assert applied is not None and MORE in applied.record.gone, "non-vacuity: it left"
        lines[on] = tp.left_line(applied.record)
        assert (finder is not None) is on
        if on:
            assert finder.candidates.packs == ("studio",)
            assert not set(finder.candidates.tools) & MORE_TOOLS
    assert lines[True] == lines[False] and "left this chat" in lines[True]


# ── 4. The name ─────────────────────────────────────────────────────────────


def test_no_other_tool_is_named_find_tools() -> None:
    """The name of the search is unique in the tool lists that the repo
    holds: each tool of each skill family (the packs and the injected
    families), each platform tool that the injection can add, and the own
    tools of the orchestrator. Mutation: a pack or platform tool named
    ``find_tools`` -> red. The tools of a registry MCP server are not in the
    repo, so this fence cannot see them."""
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    from acb_skills.tool_guard import tool_name

    families = {str(n) for fam in sf.SKILL_FAMILIES.values() for n in fam.get("tools", ())}
    platform = {tool_name(fn) for fn in ti._collect_injectable_platform_tools("orchestrator")}
    platform |= {tool_name(fn) for fn in ti._collect_injectable_platform_tools(None)}
    with pytest.MonkeyPatch.context() as m:
        m.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
        from orchestrator.agents import build_orchestrator_agent

        own = {
            tool_name(t)
            for t in build_orchestrator_agent(with_history=False).default_options["tools"]
        }
    assert len(families) > 100 and len(platform) > 20 and len(own) > 10, "non-vacuity"
    for names in (families, platform, own):
        assert tp.FIND_TOOL not in names
    assert "load_skill_tools" not in families | platform | own
