"""HRD-1 (task 2980), chat_failure_modes.md §4.2 B1: an unknown tool name gets
a way through, and the model never reads MAF's raw "not found".

Fence (R7) of ``acb_skills.unknown_tool``. Mutations, each red:

* resolve a name against no pack            -> test_each_tool_of_each_pack_resolves
* drop the close match                      -> test_a_misspelt_name_gets_the_right_suggestion
* name a pack that the loader does not offer -> test_it_never_names_a_tool_the_member_may_not_use
* skip the rewrite                          -> test_no_raw_not_found_reaches_the_model
* write ``result`` only, not ``items``      -> test_no_raw_not_found_reaches_the_model
* read the flag as always on                -> the flag tests
* load into a copy of the tool list          -> test_a_copy_of_the_tool_list_never_takes_a_load
* load for a raw text of the history         -> test_a_raw_text_of_the_history_gets_the_text_and_no_load

The loop tests read each request as the OpenAI client sends it
(``_prepare_messages_for_openai``), because that client sends a tool result's
``items`` and not its ``result``. The auto-load tests use the real
``LoadSkillTools``, so the run check, the caps and the record are the loader's
own.

Production cases: ``create_task`` (2026-11-12, session 96d46caa) and
``move_project`` (2026-11-14, session e0d38914).

Run: ``uv run pytest tests/unit/test_unknown_tool_answer.py -q``.
"""
from __future__ import annotations

import contextlib
import copy
import json
import types
from collections.abc import AsyncIterable, Iterator
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common.db import bind_tenant, release_tenant
from acb_skills import tool_packs as tp
from acb_skills import unknown_tool as ut
from acb_skills.tool_guard import WithholdTools, tool_name
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddleware,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    ContextProvider,
    FunctionInvocationLayer,
    Message,
    tool,
)

PACK_SLUGS = tuple(s for s in tp.PACKS if tp.is_pack(s))
ORG = "0a0a0a0a-0000-4000-8000-0000000000e1"
MEMBER = "kiran@acme.test"
SESSION = "chat-hrd1"
RUN_KEY = "key-hrd1"
#: The packs that the loader offered in the run of each production case.
OFFERED = ("projects", "projects-plan", "projects-structure", "projects-bulk")


def _owner_of(name: str) -> set[str]:
    return {s for s in PACK_SLUGS if name in tp.pack(s).tools}


# ── 1. Each tool of each pack resolves ──────────────────────────────────────


@pytest.mark.parametrize("slug", PACK_SLUGS)
def test_each_tool_of_each_pack_resolves(slug: str) -> None:
    """Mutation: pass ``offered=()`` in the call → no pack named → red."""
    for name in tp.pack(slug).tools:
        text = ut.answer_for(name, live=[], offered=[slug])
        assert "load_skill_tools" in text, (slug, name, text)
        assert f'"{name}"' in text
        named = {s for s in PACK_SLUGS if f'pack "{s}"' in text}
        assert named and named <= _owner_of(name), (slug, name, text)
        assert "not found" not in text.lower()


# ── 2. The production cases, and a misspelt name ────────────────────────────


def test_the_production_names_resolve_to_the_pack_that_holds_them() -> None:
    """Each name of §3 and of 2026-11-14. Mutation: map a tool to the first
    offered pack, not its owner → ``move_project`` names "projects" → red."""
    assert _owner_of("create_task") == {"projects"}
    assert _owner_of("move_project") == {"projects-bulk"}
    cases = {
        "create_task": "projects", "find_tasks": "projects",
        "create_tasks": "projects-bulk", "move_project": "projects-bulk",
        "create_status": "projects-structure", "update_status": "projects-structure",
    }
    for name, slug in cases.items():
        text = ut.answer_for(name, live=["search_company"], offered=OFFERED)
        assert text == ut.SUGGEST_LOAD.format(name=name, slug=slug), (name, text)


def test_move_project_names_projects_bulk_while_projects_is_held() -> None:
    """2026-11-14: the run held Projects, and the model called move_project."""
    live = list(tp.pack("projects").tools)
    text = ut.answer_for("move_project", live=live, offered=["projects-bulk"],
                         held=["projects"])
    assert 'pack "projects-bulk"' in text and "load_skill_tools" in text
    assert "not found" not in text.lower()


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
    text = ut.answer_for("load_skill_tools", live=["search_company"], offered=[])
    assert text == ut.NOT_HERE.format(name="load_skill_tools")


def test_a_tool_that_is_live_now_says_call_it_again() -> None:
    """The model called a pack tool beside ``load_skill_tools`` in one step.
    MAF looked for the tool before the load. Mutation: drop the live branch
    → "the closest tool that you hold is create_task" → red."""
    text = ut.answer_for("create_task", live=["create_task"], offered=["projects"])
    assert text == ut.LIVE_NOW.format(name="create_task")


def test_a_refused_load_gives_the_loaders_own_next_step() -> None:
    refusal = tp.LOAD_UNAVAILABLE.format(label="Projects")
    text = ut.answer_for("create_task", live=[], offered=["projects"],
                         tried="projects", load_answer=refusal)
    assert text.startswith(ut.LOAD_FAILED.format(name="create_task", slug="projects"))
    assert text.endswith(refusal)


# ── 3. Never a tool that the member may not use ─────────────────────────────


def test_it_never_names_a_tool_the_member_may_not_use() -> None:
    """The loader offers only ``projects``. A tool of any other pack is not
    named, not as itself and not as a close match. Mutation: build the pool
    from every pack, not the offered ones → red."""
    for slug in PACK_SLUGS:
        if slug == "projects":
            continue
        for name in tp.pack(slug).tools:
            if name in tp.pack("projects").tools:
                continue
            text = ut.answer_for(name, live=[], offered=["projects"])
            assert f'"{slug}"' not in text and f'pack "{slug}"' not in text
            named = [t for t in tp.pack(slug).tools if t != name and f'"{t}"' in text]
            assert not named or all(t in tp.pack("projects").tools for t in named), text


def test_the_answer_holds_only_names() -> None:
    """The model's own name is cut to a safe alphabet and 60 characters."""
    text = ut.answer_for('x"; DROP TABLE y;' + "q" * 200, live=[], offered=[])
    assert "DROP TABLE" not in text and '";' not in text
    assert max(len(w) for w in text.split()) < 90


def test_both_maf_texts_are_read() -> None:
    assert ut.is_raw('Error: Requested function "create_task" not found.') == "create_task"
    assert ut.is_raw('Function "move_project" not found.') == "move_project"
    assert ut.is_raw("Created the task.") == ""


# ── 4. The loop: what the model reads ───────────────────────────────────────


def _wire_client() -> Any:
    from agent_framework.openai import OpenAIChatCompletionClient

    return OpenAIChatCompletionClient(model="m", api_key="k", base_url="http://127.0.0.1:9/v1")


class _Scripted(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """The layers of ``OpenAIChatCompletionClient``, with a scripted model.
    Each request is kept as the OpenAI client would send it."""

    def __init__(self, script: list[tuple[Any, ...]]) -> None:
        super().__init__()
        self.script = list(script)
        self.wire: list[list[dict[str, Any]]] = []
        self.tools: list[list[str]] = []
        self._out = _wire_client()

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.wire.append(self._out._prepare_messages_for_openai(list(messages)))
        self.tools.append([tool_name(t) for t in (options or {}).get("tools") or []])
        step = self.script.pop(0)
        n = len(self.wire)
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


def _tool_texts(request: list[dict[str, Any]]) -> list[str]:
    """The text of each tool result of one request, as the model reads it."""
    return [str(m.get("content")) for m in request if m.get("role") == "tool"]


def _all_tool_texts(client: _Scripted) -> list[str]:
    return [t for request in client.wire for t in _tool_texts(request)]


def _record(held: tuple[str, ...] = ()) -> tp.PackRun:
    """The record of a run whose layer (a) loaded *held*."""
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 3, (), "", True)
    choice = tp.choose(turn, allowed=frozenset(), base_tools=0)
    return tp.PackRun.of(turn, choice, [(slug, False) for slug in held])


def _builder(made: dict[str, list[Any]], fail: bool = False) -> Any:
    """A pack build that gives the tools of *made* for each slug."""

    async def build(slug: str, ctx: Any, holds: frozenset[str]) -> Any:
        if fail:
            return None
        return types.SimpleNamespace(tools=tuple(made.get(slug, [])), held=False)

    return build


def _loader(offered: tuple[str, ...] = OFFERED, held: tuple[str, ...] = (),
            made: dict[str, list[Any]] | None = None, fail: bool = False) -> tp.LoadSkillTools:
    return tp.LoadSkillTools(offered=offered, run_key=RUN_KEY, record=_record(held),
                             build=_builder(made or {}, fail))


@contextlib.contextmanager
def _in_run(loader: tp.LoadSkillTools | None) -> Iterator[None]:
    """The frame that the executor binds: the tenant, the run key, the record."""
    values: dict[str, Any] = {
        "run_id": "r-hrd1", "session_id": SESSION, "agent_name": "orchestrator",
        "member": MEMBER, "batch_thread": False, "no_egress": False,
    }
    if loader is not None:
        values.update({tp.PACK_RUN_KEY: loader.run_key, tp.PACKS_KEY: loader.record})
    token = bind_tenant(ORG)
    try:
        with artifact_context_scope():
            bind_artifact_context(**values)
            yield
    finally:
        release_tenant(token)


def _pack_tool(name: str, ran: list[str]) -> Any:
    async def body(title: str = "") -> str:
        ran.append(f"{name}:{title}")
        return "made"

    body.__name__ = name
    return tool(body, name=name, approval_mode="never_require")


async def _own() -> str:
    """An own tool."""
    return "own"


async def _held_write() -> str:
    """A write that a held chat takes out of each request."""
    return "held"


class _Withhold(ContextProvider):
    """An outer chat middleware that sends each request a COPY of the tool
    list, as ``ChatHoldProvider`` does in a held chat."""

    def __init__(self) -> None:
        super().__init__("test_withhold")

    async def before_run(self, *, agent, session, context, state) -> None:
        context.extend_middleware("test_withhold", [
            WithholdTools(lambda t: tool_name(t) == "_held_write")])


class _CopyMessages(ContextProvider):
    """An outer chat middleware that sends each request a deep COPY of the
    messages, so a rewrite in one request does not stay for the next."""

    def __init__(self) -> None:
        super().__init__("test_copy_messages")

    async def before_run(self, *, agent, session, context, state) -> None:
        class _Copy(ChatMiddleware):
            async def process(self, ctx: Any, call_next: Any) -> None:
                ctx.messages = [copy.deepcopy(m) for m in ctx.messages]
                await call_next()

        context.extend_middleware("test_copy_messages", [_Copy()])


async def _run(script, loader, *, stream: bool = False, withhold: bool = False,
               history: list[Message] | None = None, copy_messages: bool = False) -> _Scripted:
    from orchestrator._native_run_context import agent_with_providers

    client = _Scripted(script)
    agent = Agent(client=client, name="orchestrator", tools=[_own, _held_write])
    providers: list[Any] = []
    if loader is not None:
        providers.append(tp.LoadSkillToolsProvider(loader))
    if withhold:
        providers.append(_Withhold())
    if copy_messages:
        providers.append(_CopyMessages())
    providers.append(ut.UnknownToolProvider())
    view = agent_with_providers(agent, providers)
    messages: Any = [*(history or []), Message("user", ["go"])]
    with _in_run(loader):
        if stream:
            async for _ in view.run(messages, stream=True):
                pass
        else:
            await view.run(messages)
    return client


def _flag(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(ut, "autoload_enabled", lambda: on)


@pytest.mark.parametrize("stream", [False, True], ids=["batch", "stream"])
async def test_no_raw_not_found_reaches_the_model(stream: bool, monkeypatch) -> None:
    """2026-11-12: the model called create_task with the Projects pack offered
    and not loaded. Mutation: skip ``_rewrite``, or write ``result`` and not
    ``items`` → the OpenAI request holds the raw text → red."""
    _flag(monkeypatch, False)
    loader = _loader()
    client = await _run([("call", "create_task", {}), ("text", "done")], loader, stream=stream)
    seen = _all_tool_texts(client)
    assert seen, "the tool result reached the second request"
    for text in seen:
        assert "not found" not in text.lower(), text
    assert seen[0] == ut.SUGGEST_LOAD.format(name="create_task", slug="projects")
    assert loader.record.holds() == (), "the flag is off: nothing loads"


async def test_the_flag_is_off_by_default(monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.delenv("CHAT_TOOL_AUTOLOAD", raising=False)
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    assert ut.autoload_enabled() is False


@pytest.mark.parametrize("stream", [False, True], ids=["batch", "stream"])
async def test_with_the_flag_on_create_task_loads_projects_and_the_call_runs(
    stream: bool, monkeypatch,
) -> None:
    """The pack loads through the real loader, the answer says so with the
    rules of the pack, and the next call RUNS the tool. Mutation: read the
    flag as off → no load, the tool never runs → red."""
    _flag(monkeypatch, True)
    ran: list[str] = []
    loader = _loader(made={"projects": [_pack_tool("create_task", ran)]})
    client = await _run(
        [("call", "create_task", {"title": "t"}), ("call", "create_task", {"title": "t"}),
         ("text", "done")], loader, stream=stream)
    assert loader.record.holds() == ("projects",)
    second = _tool_texts(client.wire[1])
    assert second[0].startswith(ut.LOADED_NOW.format(
        name="create_task", slug="projects", tool="create_task"))
    assert "Loaded the Projects tools (1 tools)." in second[0], "the loader's own answer"
    assert 'Call load_skill_tools with pack "projects"' not in second[0]
    assert "create_task" in client.tools[1], "the request after the load holds the tool"
    assert ran == ["create_task:t"], "the tool that the load added ran on the next call"
    assert not any("not found" in t.lower() for t in _all_tool_texts(client))


async def test_with_the_flag_on_move_project_loads_projects_bulk(monkeypatch) -> None:
    """2026-11-14: the run held Projects, and the model called move_project."""
    _flag(monkeypatch, True)
    ran: list[str] = []
    loader = _loader(offered=("projects-bulk", "projects-structure"), held=("projects",),
                     made={"projects-bulk": [_pack_tool("move_project", ran)]})
    client = await _run(
        [("call", "move_project", {"title": "p"}), ("call", "move_project", {"title": "p"}),
         ("text", "done")], loader)
    assert "projects-bulk" in loader.record.holds()
    second = _tool_texts(client.wire[1])[0]
    assert second.startswith(ut.LOADED_NOW.format(
        name="move_project", slug="projects-bulk", tool="move_project"))
    assert ran == ["move_project:p"]


async def test_a_copy_of_the_tool_list_never_takes_a_load(monkeypatch) -> None:
    """A held chat sends each request a copy of the tool list. A load into the
    copy would vanish at the next request. So the text names load_skill_tools,
    and the model's own load works. Mutation: drop the identity check of
    ``_live_list`` → the load goes into the copy, create_task is not found
    again, and it never runs → red."""
    _flag(monkeypatch, True)
    ran: list[str] = []
    loader = _loader(made={"projects": [_pack_tool("create_task", ran)]})
    client = await _run(
        [("call", "create_task", {"title": "t"}),
         ("call", "load_skill_tools", {"pack": "projects"}),
         ("call", "create_task", {"title": "t"}), ("text", "done")], loader, withhold=True)
    assert _tool_texts(client.wire[1])[-1] == ut.SUGGEST_LOAD.format(
        name="create_task", slug="projects")
    assert "_held_write" not in client.tools[0], "the outer middleware sent a copy"
    assert ran == ["create_task:t"]
    assert not any("not found" in t.lower() for t in _all_tool_texts(client))


@pytest.mark.parametrize("copy_messages", [False, True], ids=["same", "copied"])
async def test_a_raw_text_of_the_history_gets_the_text_and_no_load(
    copy_messages: bool, monkeypatch,
) -> None:
    """A result of an earlier turn is not a call of this run. The run calls
    one own tool, so the second request holds the live list, and a copy of
    the messages brings the old raw text back to it. Mutation: load for each
    raw text, or key the history by object identity → Projects loads → red."""
    _flag(monkeypatch, True)
    loader = _loader(made={"projects": [_pack_tool("create_task", [])]})
    history = [
        Message("user", ["make a task"]),
        Message("assistant", [Content.from_function_call("old1", "create_task", arguments="{}")]),
        Message("tool", [Content.from_function_result(
            call_id="old1", result='Error: Requested function "create_task" not found.')]),
        Message("assistant", ["I could not make it."]),
    ]
    client = await _run([("call", "_own", {}), ("text", "done")], loader,
                        history=history, copy_messages=copy_messages)
    want = ut.SUGGEST_LOAD.format(name="create_task", slug="projects")
    assert _tool_texts(client.wire[0]) == [want]
    assert _tool_texts(client.wire[1]) == [want, "own"]
    assert loader.record.holds() == ()


async def test_a_refused_load_answers_with_the_loaders_refusal(monkeypatch) -> None:
    _flag(monkeypatch, True)
    loader = _loader(fail=True)
    client = await _run([("call", "create_task", {}), ("text", "done")], loader)
    text = _tool_texts(client.wire[1])[0]
    assert text.startswith(ut.LOAD_FAILED.format(name="create_task", slug="projects"))
    assert tp.LOAD_UNAVAILABLE.format(label="Projects") in text
    assert loader.record.holds() == ()


async def test_with_the_flag_on_a_pack_the_loader_does_not_offer_does_not_load(
    monkeypatch,
) -> None:
    _flag(monkeypatch, True)
    loader = _loader(offered=())
    client = await _run([("call", "create_task", {}), ("text", "done")], loader)
    assert loader.record.holds() == ()
    text = _tool_texts(client.wire[1])[0]
    assert "projects" not in text and "not found" not in text.lower()


def test_the_executor_puts_the_answer_on_the_orchestrator_view_last() -> None:
    """The one hook: ``executor._log_request_tools_view``. It runs after each
    seam of ``_inject_agent_tools`` (the hold, the steer turns, the progress
    notice and the number check), so the answer runs inside their chat
    middleware. Mutation: drop ``UnknownToolProvider`` from the view → red."""
    from orchestrator import executor as ex

    agent = Agent(client=_Scripted([]), name="orchestrator", tools=[_own])
    view = ex._log_request_tools_view([agent], "orchestrator")[0]
    assert view is not agent, "a per-run view, never the shared agent"
    assert type(view.context_providers[-1]) is ut.UnknownToolProvider
    assert ex._log_request_tools_view([agent], "email-assistant")[0] is agent


async def test_with_no_loader_the_text_still_replaces_the_raw_error() -> None:
    client = await _run([("call", "create_task", {}), ("text", "done")], None)
    text = _tool_texts(client.wire[1])[0]
    assert "not found" not in text.lower() and "create_task" in text
