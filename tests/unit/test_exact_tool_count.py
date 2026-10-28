"""WS-67 OA-8f: the estimate of the pack cap equals the count that a run sends.

``project-docs/specs/one_assistant_context.md`` §4.2.2 and the note "As built,
OA-8f". A staging turn of 2026-10-28 logged ``base_tools=65`` while the run
sent far fewer. The estimate (``_tool_injection._pack_base_count``) differed
from the sent count in three ways, and each has a fence here:

1. It counted the 3 CRM facts tools that ``_add_crm_facts_block`` then removes
   for an org that is not on ``CRM_QUESTIONS_ORGS``.
2. The load tool comes from a context provider, and the true-count log left
   it out.
3. ``tool_packs.choose`` did not subtract the specialist tool that a chosen
   pack replaces.

A granted app tool and an MCP tool stay outside the estimate. The log line
``executor.request_tools`` counts them, because it reads the request.

Mutations this file catches (R7):

* ``_pack_base_count`` stops subtracting ``_facts_removed()`` → the estimate
  holds 3 tools too many → red in the staging-shape test;
* ``choose`` ignores ``specialists`` → the email pack is refused at a base
  where it fits → red;
* ``_log_pack_request_tools`` drops the load tool → the true count is one
  short of the estimate → red;
* ``LogRequestTools`` reads the agent's pool and not the request → the
  count misses the tool of a context provider → red;
* ``tool_digest`` hashes the order of the names → two orders, two hashes → red.

Run: ``uv run pytest tests/unit/test_exact_tool_count.py -q``.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterable
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from acb_skills.tool_guard import LogRequestToolsProvider, tool_digest
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    ContextProvider,
    FunctionInvocationLayer,
    Message,
)
from orchestrator import _tool_injection as ti
from orchestrator import executor as ex

from tests.unit.test_tool_packs_choice import (
    CONFIG,
    EMAIL_TOOLS,
    MEMBER,
    ORG,
    SESSION,
    _agents_mod,
    _bullets,
    _names,
    _run_ctx,
    _tenant,
    _turn,
    orchestrator,  # noqa: F401  (a fixture)
    store,  # noqa: F401  (a fixture)
)

EVERY = frozenset(sf.pack_slugs())
EMAIL = frozenset({"email"})


@pytest.fixture
def staging_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flags of the staging box: the profile tools (7), company search (1)
    and ``decide`` (1) are on. The loader is offered by the apply."""
    mod = _agents_mod()
    monkeypatch.setattr(mod, "_assistant_profile_enabled", lambda org=None: True)
    monkeypatch.setattr(mod, "_company_search_enabled", lambda: True)
    monkeypatch.setattr("acb_skills.decide_tools.decide_tool_enabled", lambda: True)
    monkeypatch.setattr("orchestrator.app_tools.load_app_action_tools", lambda _name: [])


async def _flow(agents: list[Any], words: str, monkeypatch, *, allowed=EVERY) -> dict[str, int]:
    """The real order of a run: the packs, the injection, the facts block.

    Returns ``base`` (the estimate of the cap, with no load tool), ``plan``
    (the ``sent_tools`` that ``executor.pack_plan`` logged) and ``sent`` (the
    pool of the run after the injection, plus the load tool)."""
    seen: list[int] = []
    real = ti._pack_base_count

    def spy(*a: Any, **k: Any) -> int:
        seen.append(real(*a, **k))
        return seen[-1]

    logged: list[dict[str, Any]] = []
    real_info = ti._log.info

    def keep(event: str, **kw: Any) -> None:
        logged.append({"event": event, **kw})
        real_info(event, **kw)

    monkeypatch.setattr(ti, "_pack_base_count", spy)
    monkeypatch.setattr(ti._log, "info", keep)
    with _tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, words)
        applied = await ti._apply_tool_packs(
            agents, turn, _run_ctx(), allowed=allowed, agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG,
            bullets=_bullets(), offer_loader=True,
        )
        ti._inject_agent_tools(
            agents, tool_scope=list(CONFIG["tool_scope"]), agent_name="orchestrator",
            agent_config=CONFIG, no_egress=False,
        )
        await ti._add_crm_facts_block(agents, frozenset(_names(agents)), no_egress=False)
    plan = [x for x in logged if x["event"] == "executor.pack_plan"]
    loader = 1 if ex._pack_loader_offer(agents[0]) else 0
    return {"base": seen[0], "plan": plan[0]["sent_tools"] if plan else -1,
            "sent": len(_names(agents)) + loader, "loader": loader,
            "packs": len(applied.record.slugs) if applied else 0}


# ── 1. The estimate equals the sent count ───────────────────────────────────


async def test_the_estimate_equals_the_sent_count_on_the_staging_shape(
    staging_shape, orchestrator, store, monkeypatch,  # noqa: F811
) -> None:
    """Staging: 65 tools before the fix (55 + the profile 7, search 1, decide 1,
    and the loader). The 3 facts tools are not in the run of an org off the
    list, so the base falls to 61, and 62 with the load tool. The loader then
    reaches three owners and takes their 3 specialist tools out, so the run
    sends 59. The count of the apply (``sent_tools``) says 59 and equals what
    the run sends. Mutation: the estimate keeps the facts tools → 65, not 62
    → red. Mutation: ``sent_tools`` ignores the specialists that leave → 62
    logged against 59 sent → red."""
    with _tenant():
        old = _estimate_with_facts(orchestrator) + 1
    got = await _flow(orchestrator, "", monkeypatch)
    assert got["packs"] == 0 and got["loader"] == 1
    assert got["plan"] == got["sent"] == 59, got
    assert got["base"] + 1 == 62, got
    assert old == got["base"] + 1 + 3 == 65, (old, got)


def _estimate_with_facts(agents: list[Any]) -> int:
    """The estimate as it was before OA-8f: the facts tools stay in."""
    from unittest import mock

    with mock.patch.object(ti, "_facts_removed", ti._facts_withheld):
        return ti._pack_base_count(
            agents[0], agent_name="orchestrator", tool_scope=list(CONFIG["tool_scope"]),
            agent_config=CONFIG, disabled=frozenset())


async def test_a_loaded_pack_keeps_the_estimate_exact(
    staging_shape, orchestrator, store, monkeypatch,  # noqa: F811
) -> None:
    """With the email pack loaded, and no pack left to offer, the run sends the
    base, plus the 31 pack tools, less the specialist tool that the pack
    replaces, and no load tool. Mutation: ``sent_tools`` counts a load tool
    that is not offered, or keeps the specialist → off by one → red."""
    got = await _flow(orchestrator, "check my inbox", monkeypatch, allowed=EMAIL)
    assert got["packs"] == 1 and got["loader"] == 0
    n = len(EMAIL_TOOLS)  # the family less narrow_and_read, which no pack builds
    assert got["sent"] == got["plan"] == got["base"] + n - 1, got


async def test_the_true_count_log_counts_the_load_tool(
    staging_shape, orchestrator, store, monkeypatch,  # noqa: F811
) -> None:
    """The load tool lives in a context provider, so no pool holds it. The log
    ``executor.pack_request_tools`` adds it. Mutation: drop the ``+ 1`` →
    the logged count is one short of the sent count → red."""
    got = await _flow(orchestrator, "", monkeypatch)
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ex._log, "info", lambda event, **kw: logged.append({"event": event, **kw}))
    ex._log_pack_request_tools(orchestrator)
    line = [x for x in logged if x["event"] == "executor.pack_request_tools"]
    assert line and line[0]["tools"] == got["sent"], (line, got)


# ── 2. The choice subtracts the specialist tool that a pack replaces ────────


def test_choose_subtracts_the_specialist_tool_of_a_replacing_pack() -> None:
    """At a base of 80, the 31 email tools make 111. The email pack replaces
    the one ``email_assistant`` tool, so the request holds 110 and fits.
    Mutation: ``choose`` ignores ``specialists`` → ``email`` is skipped → red."""
    turn = _turn(words="check my inbox")
    plain = tp.choose(turn, allowed=EMAIL, base_tools=80)
    assert plain.slugs == () and plain.skipped == ("email",)
    fits = tp.choose(turn, allowed=EMAIL, base_tools=80, specialists={"email-assistant": 1})
    assert fits.slugs == ("email",) and fits.skipped == ()
    over = tp.choose(turn, allowed=EMAIL, base_tools=81, specialists={"email-assistant": 1})
    assert over.slugs == ()


def test_choose_subtracts_nothing_for_a_pack_that_replaces_no_specialist() -> None:
    """A Projects pack leaves its specialist in until the run reaches the whole
    owner (``_specialist_leaves``). Mutation: subtract for every pack → the
    core pack fits at a base where it would send 111 tools → red."""
    turn = _turn(words="show my tasks")
    n = len(sf.SKILL_FAMILIES["projects"]["tools"])
    base = tp.MAX_REQUEST_TOOLS - n + 1
    out = tp.choose(
        turn, allowed={"projects"}, base_tools=base, specialists={"projects-assistant": 1})
    assert out.slugs == () and out.skipped == ("projects",)


# ── 3. The log line of a request ────────────────────────────────────────────


def test_the_digest_ignores_the_order_and_counts_the_items() -> None:
    """Mutation: hash the names in the order given → red."""

    def a() -> str:
        return ""

    def b() -> str:
        return ""

    a.__name__, b.__name__ = "alpha", "beta"
    one = tool_digest([a, b])
    two = tool_digest([b, a])
    assert one == two and one[0] == 2 and len(one[2]) == 10
    assert tool_digest([a]) != one
    assert tool_digest([a, a])[:2] == (2, 1)


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
                    contents=[content], role="assistant", response_id="r", message_id="m")
            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(messages=[Message("assistant", [content])], response_id="r")
        return _one()


class _Adds(ContextProvider):
    """A provider that adds a tool to the request, as the loader does."""

    def __init__(self, tool: Any) -> None:
        super().__init__("adds")
        self._tool = tool

    async def before_run(self, *, agent, session, context, state) -> None:
        context.extend_tools("adds", [self._tool])


async def test_the_request_log_counts_the_tool_of_a_context_provider(monkeypatch) -> None:
    """The line reads the request, so it holds the tool of a provider (the
    loader) as well as the agent's own tools. Mutation: read the agent's pool
    instead of the request → 1 tool logged, 2 sent → red."""
    from orchestrator._native_run_context import agent_with_providers

    async def own_tool(x: str = "") -> str:
        """An own tool."""
        return "own"

    async def load_skill_tools(pack: str = "") -> str:
        """A tool of a provider."""
        return "loaded"

    client = _Recorder()
    agent = Agent(client=client, name="orchestrator", tools=[own_tool])
    logged: list[dict[str, Any]] = []
    import acb_skills.tool_guard as guard

    monkeypatch.setattr(guard._log, "info", lambda event, **kw: logged.append({"event": event, **kw}))
    view = agent_with_providers(agent, [_Adds(load_skill_tools), LogRequestToolsProvider("orchestrator")])
    await view.run("hello")
    assert client.tools == [["own_tool", "load_skill_tools"]]
    line = [x for x in logged if x["event"] == "executor.request_tools"]
    assert len(line) == 1 and line[0]["tools"] == 2 and line[0]["distinct"] == 2
    assert line[0]["names_hash"] == tool_digest(client.tools[0] and [own_tool, load_skill_tools])[2]
    assert json.dumps(line[0]).count("own_tool") == 0, "the line holds a hash, not the names"
