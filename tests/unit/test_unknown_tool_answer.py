"""HRD-1 (task 2980), chat_failure_modes.md §4.2 B1: an unknown tool name gets
a way through, and the model never reads MAF's raw "not found".

Fence (R7) of ``acb_skills.unknown_tool``. Mutations, each red:

* resolve a name against no pack            -> test_each_tool_of_each_pack_resolves
* drop the close match                      -> test_a_misspelt_name_gets_the_right_suggestion
* name a pack that the loader does not offer -> test_it_never_names_a_tool_the_member_may_not_use
* skip the rewrite                          -> test_no_raw_not_found_reaches_the_model
* read the flag as always on                -> the flag tests

Run: ``uv run pytest tests/unit/test_unknown_tool_answer.py -q``.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterable
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import tool_packs as tp
from acb_skills import unknown_tool as ut
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

PACK_SLUGS = tuple(s for s in tp.PACKS if tp.is_pack(s))


def _owner_of(tool: str) -> set[str]:
    return {s for s in PACK_SLUGS if tool in tp.pack(s).tools}


# ── 1. Each tool of each pack resolves ──────────────────────────────────────


@pytest.mark.parametrize("slug", PACK_SLUGS)
def test_each_tool_of_each_pack_resolves(slug: str) -> None:
    """Mutation: pass ``offered=()`` in the call → no pack named → red."""
    for tool in tp.pack(slug).tools:
        text = ut.answer_for(tool, live=[], offered=[slug])
        assert "load_skill_tools" in text, (slug, tool, text)
        assert f'"{tool}"' in text
        named = {s for s in PACK_SLUGS if f'pack "{s}"' in text}
        assert named and named <= _owner_of(tool), (slug, tool, text)
        assert "not found" not in text.lower()


# ── 2. A misspelt name gets the right suggestion ────────────────────────────


def test_a_misspelt_name_gets_the_right_suggestion() -> None:
    """Mutation: cutoff 1.0 (no close match) → generic text → red."""
    live = ["search_emails", "read_email"]
    assert '"search_emails"' in ut.answer_for("search_email", live=live, offered=[])
    text = ut.answer_for("create_taks", live=[], offered=["projects", "email"])
    assert '"create_task"' in text and 'pack "projects"' in text
    text = ut.answer_for("update_statuses", live=[], offered=list(PACK_SLUGS))
    assert '"update_status"' in text and 'pack "projects-structure"' in text


def test_a_name_with_no_close_match_names_nothing() -> None:
    text = ut.answer_for("zzzqqq", live=["a_tool"], offered=["projects"])
    assert text == ut.NOT_HERE.format(name="zzzqqq")


# ── 3. Never a tool that the member may not use ─────────────────────────────


def test_it_never_names_a_tool_the_member_may_not_use() -> None:
    """The loader offers only ``projects``. A tool of any other pack is not
    named, not as itself and not as a close match. Mutation: build the pool
    from every pack, not the offered ones → red."""
    for slug in PACK_SLUGS:
        if slug == "projects":
            continue
        for tool in tp.pack(slug).tools:
            if tool in tp.pack("projects").tools:
                continue
            text = ut.answer_for(tool, live=[], offered=["projects"])
            assert f'"{slug}"' not in text and f'pack "{slug}"' not in text
            named = [t for t in tp.pack(slug).tools if t != tool and f'"{t}"' in text]
            assert not named or all(t in tp.pack("projects").tools for t in named), text


def test_the_answer_holds_only_names() -> None:
    """The model's own name is cut to a safe alphabet and 60 characters."""
    text = ut.answer_for('x"; DROP TABLE y;' + "q" * 200, live=[], offered=[])
    assert "DROP TABLE" not in text and '";' not in text
    assert max(len(w) for w in text.split()) < 90


# ── 4. The loop: no raw "not found" reaches the model ───────────────────────


class _Scripted(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    def __init__(self, script: list[tuple[Any, ...]]) -> None:
        super().__init__()
        self.script = list(script)
        self.requests: list[list[Message]] = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.requests.append(list(messages))
        step = self.script.pop(0)
        n = len(self.requests)
        if step[0] == "call":
            content = Content.from_function_call(f"c{n}", step[1], arguments=json.dumps(step[2]))
        else:
            content = Content.from_text(step[1])
        if stream:
            async def _gen() -> AsyncIterable[ChatResponseUpdate]:
                yield ChatResponseUpdate(
                    contents=[content], role="assistant", response_id=f"r{n}", message_id="m")
            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(messages=[Message("assistant", [content])], response_id=f"r{n}")
        return _one()


def _results(request: list[Message]) -> list[str]:
    return [str(c.result) for m in request for c in m.contents if c.type == "function_result"]


class _Provider(ContextProvider):
    def __init__(self, loader: Any) -> None:
        super().__init__("test_unknown")
        self._loader = loader

    async def before_run(self, *, agent, session, context, state) -> None:
        context.extend_middleware("test_unknown", [ut.UnknownToolAnswer(self._loader)])


class _FakeLoader:
    """Stands in for ``LoadSkillTools``: a pack ``projects`` that adds one tool."""

    def __init__(self, offered: list[str], add: Any) -> None:
        import asyncio

        self.offered = tuple(offered)
        self._lock = asyncio.Lock()
        self._add = add
        self.loads: list[str] = []

    async def _load(self, frame: Any) -> str:
        slug = frame.arguments["pack"]
        self.loads.append(slug)
        frame.add_tools([self._add])
        return f"Loaded the {slug} tools (1 tools). Call them from your next step."


async def _run(script, loader, tools, stream: bool = False):
    from orchestrator._native_run_context import agent_with_providers

    client = _Scripted(script)
    agent = Agent(client=client, name="orchestrator", tools=tools)
    view = agent_with_providers(agent, [_Provider(loader)])
    if stream:
        async for _ in view.run("go", stream=True):
            pass
    else:
        await view.run("go")
    return client


async def _own() -> str:
    """An own tool."""
    return "own"


@pytest.mark.parametrize("stream", [False, True], ids=["batch", "stream"])
async def test_no_raw_not_found_reaches_the_model(stream: bool) -> None:
    """Mutation: do not call ``_rewrite`` → the model reads the raw text → red."""
    loader = _FakeLoader(["projects"], _own)
    client = await _run(
        [("call", "create_task", {}), ("text", "done")], loader, [_own], stream=stream)
    seen = [r for req in client.requests for r in _results(req)]
    assert seen, "the tool result reached the second request"
    for text in seen:
        assert "not found" not in text.lower(), text
    assert 'pack "projects"' in seen[0] and "load_skill_tools" in seen[0]
    assert loader.loads == [], "the flag is off: nothing loads"


async def test_the_flag_is_off_by_default(monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.delenv("CHAT_TOOL_AUTOLOAD", raising=False)
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    assert ut.autoload_enabled() is False


async def test_with_the_flag_on_the_turn_loads_the_pack_and_says_so(monkeypatch) -> None:
    """The pack loads, the answer says so, and the next call RUNS the tool.
    Mutation: read the flag as off → no load, the third request holds no result → red."""
    ran: list[str] = []

    async def create_task(title: str = "") -> str:
        """Make a task."""
        ran.append(title)
        return "made"

    monkeypatch.setattr(ut, "autoload_enabled", lambda: True)
    loader = _FakeLoader(["projects"], create_task)
    client = await _run(
        [("call", "create_task", {"title": "t"}), ("call", "create_task", {"title": "t"}),
         ("text", "done")], loader, [_own])
    assert loader.loads == ["projects"]
    second = _results(client.requests[1])
    assert second and "loaded now" in second[0] and "load_skill_tools" not in second[0]
    assert ran == ["t"], "the tool that the load added ran on the next call"
    assert "not found" not in " ".join(_results(client.requests[2])).lower()


async def test_with_the_flag_on_a_pack_the_loader_does_not_offer_does_not_load(monkeypatch) -> None:
    monkeypatch.setattr(ut, "autoload_enabled", lambda: True)
    loader = _FakeLoader([], _own)
    client = await _run([("call", "create_task", {}), ("text", "done")], loader, [_own])
    assert loader.loads == []
    text = _results(client.requests[1])[0]
    assert "projects" not in text and "not found" not in text.lower()


async def test_with_no_loader_the_text_still_replaces_the_raw_error() -> None:
    client = await _run([("call", "create_task", {}), ("text", "done")], None, [_own])
    text = _results(client.requests[1])[0]
    assert "not found" not in text.lower() and "create_task" in text
