"""Task 3599: a call of a pack tool that the turn did not load.

A member saw MAF's own text, ``Function "query_inbox" not found.``, in the
chat. MAF answers a call of a name that the run does not hold with a
function result (``agent_framework/_tools.py``, ``_auto_invoke_function``):
``result`` is ``Error: Requested function "x" not found.`` and
``exception`` is the text of a ``KeyError``, ``'Function "x" not found.'``.
The HRD-1 middleware (``acb_skills.unknown_tool``) gave the MODEL a better
text before its next request. But the stream event of the step
(``orchestrator.event_translator``) sends the ``exception`` first, before
any next request, and the trail of the chat and the saved step drew it.

Two parts, and this file is the fence (R7) of both:

1. The model reads a typed answer, or the turn loads the pack
   (``CHAT_TOOL_AUTOLOAD``). The loader is asked ONCE for a name in a run,
   so the loop cannot spin.
2. The member never reads MAF's text. ``event_translator.tool_result_fields``
   is the one mapping of a result, for the stream and for the calls of a
   batch run (``executor._tool_calls_of``). It shows
   ``unknown_tool.MEMBER_LINE`` in place of MAF's text.

A turn that calls no unknown name is byte-identical to before.

Mutations, each red:

* map the stream from ``exception`` again   -> test_the_trail_never_shows_maf_text
* drop the ``exception`` read of the name   -> test_the_batch_reader_shows_the_member_line
* ask the loader at each call of a name     -> test_the_loader_is_asked_once_for_a_name
* load a pack that the run does not offer   -> test_a_name_of_a_pack_this_run_cannot_load
* change the mapping of any other result    -> test_other_results_map_as_before

Run: ``uv run pytest tests/unit/test_unloaded_pack_tool.py -q``.
"""
from __future__ import annotations

import contextlib
import json
import types
from collections.abc import AsyncIterable, Iterator
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common.db import bind_tenant, release_tenant
from acb_common.tool_refusals import is_refusal
from acb_skills import tool_packs as tp
from acb_skills import unknown_tool as ut
from acb_skills.tool_guard import tool_name
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
from orchestrator.event_translator import (
    TranslationState,
    close_text_message,
    tool_result_fields,
    translate_update,
)

ORG = "0a0a0a0a-0000-4000-8000-0000000000f9"
MEMBER = "asha@acme.test"
SESSION = "chat-3599"
RUN_KEY = "key-3599"
#: The words that only MAF writes. No member-facing text may hold them.
_MAF_WORDS = ("not found", "requested function", 'function "')


def _no_maf_words(text: str) -> bool:
    low = text.lower()
    return not any(w in low for w in _MAF_WORDS)


# ── The scripted model, as the OpenAI client sends each request ─────────────


def _wire_client() -> Any:
    from agent_framework.openai import OpenAIChatCompletionClient

    return OpenAIChatCompletionClient(model="m", api_key="k", base_url="http://127.0.0.1:9/v1")


class _Scripted(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """The layers of ``OpenAIChatCompletionClient``, with a scripted model."""

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
                    contents=[content], role="assistant", response_id=f"r{n}",
                    message_id=f"m{n}")
            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(messages=[Message("assistant", [content])], response_id=f"r{n}")
        return _one()


def _tool_texts(client: _Scripted) -> list[str]:
    """The text of each tool result that the model read, in order."""
    return [str(m.get("content")) for request in client.wire for m in request
            if m.get("role") == "tool"]


def _record() -> tp.PackRun:
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 3, (), "", True)
    choice = tp.choose(turn, allowed=frozenset(), base_tools=0)
    return tp.PackRun.of(turn, choice, [])


def _loader(offered: tuple[str, ...], made: dict[str, list[Any]] | None = None,
            fail: bool = False) -> tp.LoadSkillTools:
    async def build(slug: str, ctx: Any, holds: frozenset[str]) -> Any:
        if fail:
            return None
        return types.SimpleNamespace(tools=tuple((made or {}).get(slug, [])), held=False)

    return tp.LoadSkillTools(offered=offered, run_key=RUN_KEY, record=_record(), build=build)


@contextlib.contextmanager
def _in_run(loader: tp.LoadSkillTools | None) -> Iterator[None]:
    values: dict[str, Any] = {
        "run_id": "r-3599", "session_id": SESSION, "agent_name": "orchestrator",
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
    async def body(query: str = "") -> str:
        ran.append(f"{name}:{query}")
        return "3 emails match."

    body.__name__ = name
    return tool(body, name=name, approval_mode="never_require")


async def _own() -> str:
    """An own tool."""
    return "own"


class _Turn:
    """One scripted turn: what the model read, and what the member saw."""

    def __init__(self, client: _Scripted, events: list[dict[str, Any]], response: Any) -> None:
        self.client = client
        self.events = events
        self.response = response

    def results(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == "TOOL_CALL_RESULT"]

    def member_texts(self) -> list[str]:
        """Every text of the stream that the chat draws or saves."""
        out: list[str] = []
        for e in self.events:
            for key in ("content", "delta", "message"):
                if isinstance(e.get(key), str):
                    out.append(e[key])
        return out


async def _turn(script, loader, *, stream: bool = True, answer: bool = True) -> _Turn:
    """Run *script* on a per-run view of the orchestrator. A stream run goes
    through the real translator, as ``run_agent_stream`` sends it."""
    from orchestrator._native_run_context import agent_with_providers

    client = _Scripted(script)
    agent = Agent(client=client, name="orchestrator", tools=[_own])
    providers: list[Any] = []
    if loader is not None:
        providers.append(tp.LoadSkillToolsProvider(loader))
    if answer:
        providers.append(ut.UnknownToolProvider())
    view = agent_with_providers(agent, providers)
    events: list[dict[str, Any]] = []
    response = None
    with _in_run(loader):
        if stream:
            state = TranslationState("r-3599")
            async for update in view.run([Message("user", ["check my inbox"])], stream=True):
                events.extend(translate_update(update, state))
            events.extend(close_text_message(state))
        else:
            response = await view.run([Message("user", ["check my inbox"])])
    return _Turn(client, events, response)


def _flag(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(ut, "autoload_enabled", lambda: on)


# ── 1. The model reads a typed answer, or the pack loads ────────────────────


async def test_query_inbox_with_the_email_pack_offered_gets_a_typed_answer(monkeypatch) -> None:
    """The production case: the email pack is not loaded, and the model calls
    query_inbox. With the auto-load off, the model reads which pack holds the
    tool and its next step. Nothing loads."""
    _flag(monkeypatch, False)
    loader = _loader(offered=("email",))
    turn = await _turn([("call", "query_inbox", {"query": "Priya"}), ("text", "Done.")], loader)
    seen = _tool_texts(turn.client)
    assert seen == [ut.SUGGEST_LOAD.format(name="query_inbox", slug="email")]
    assert all(_no_maf_words(t) for t in seen), seen
    assert loader.record.holds() == ()


async def test_with_the_flag_on_query_inbox_loads_the_email_pack_and_runs(monkeypatch) -> None:
    """D-HRD-1 (D217): the turn loads the pack. The next call runs the tool,
    and its step draws as done."""
    _flag(monkeypatch, True)
    ran: list[str] = []
    loader = _loader(offered=("email",), made={"email": [_pack_tool("query_inbox", ran)]})
    turn = await _turn(
        [("call", "query_inbox", {"query": "Priya"}), ("call", "query_inbox", {"query": "Priya"}),
         ("text", "Done.")], loader)
    assert loader.record.holds() == ("email",)
    assert ran == ["query_inbox:Priya"]
    first = _tool_texts(turn.client)[0]
    assert first.startswith(
        "Not run on this call. The «email» pack is loaded now, so query_inbox is in your tools.")
    assert [r["success"] for r in turn.results()] == [False, True]
    assert turn.results()[1]["content"] == "3 emails match."


async def test_a_name_in_no_pack_gets_a_typed_answer(monkeypatch) -> None:
    _flag(monkeypatch, True)
    loader = _loader(offered=("email",), made={"email": [_pack_tool("query_inbox", [])]})
    turn = await _turn([("call", "zzq_no_such_tool", {}), ("text", "Done.")], loader)
    assert _tool_texts(turn.client) == [ut.NOT_HERE.format(name="zzq_no_such_tool")]
    assert loader.record.holds() == (), "a name in no pack loads nothing"
    assert [r["content"] for r in turn.results()] == [ut.MEMBER_LINE]


async def test_a_name_of_a_pack_this_run_cannot_load(monkeypatch) -> None:
    """query_inbox is a tool of the email pack of another agent (the email
    assistant), and this run's loader does not offer that pack. Nothing
    loads, and the answer names no pack that the member may not use.
    Mutation: build the owners from every pack → the email pack loads → red."""
    _flag(monkeypatch, True)
    loader = _loader(offered=("projects",), made={"email": [_pack_tool("query_inbox", [])]})
    turn = await _turn([("call", "query_inbox", {}), ("text", "Done.")], loader)
    text = _tool_texts(turn.client)[0]
    assert text == ut.NOT_HERE.format(name="query_inbox")
    assert "«email»" not in text and "load_skill_tools" not in text
    assert loader.record.holds() == ()
    assert [r["content"] for r in turn.results()] == [ut.MEMBER_LINE]


async def test_the_loader_is_asked_once_for_a_name(monkeypatch) -> None:
    """The load refuses. The model calls query_inbox three times. The loader
    is asked once, and each answer gives its refusal with the next step.
    Mutation: drop the ``_tried`` check of ``UnknownToolAnswer`` → 3 asks → red."""
    _flag(monkeypatch, True)
    loader = _loader(offered=("email",), fail=True)
    asks: list[str] = []
    real = loader.process

    async def counting(context: Any, call_next: Any) -> None:
        asks.append(str((getattr(context, "arguments", None) or {}).get("pack")))
        await real(context, call_next)

    loader.process = counting  # type: ignore[method-assign]
    turn = await _turn(
        [("call", "query_inbox", {}), ("call", "query_inbox", {}), ("call", "query_inbox", {}),
         ("text", "Done.")], loader)
    assert asks == ["email"], asks
    # The last request holds the result of each of the three calls.
    seen = [str(m.get("content")) for m in turn.client.wire[-1] if m.get("role") == "tool"]
    assert len(seen) == 3
    head = ut.LOAD_FAILED.format(name="query_inbox", slug="email")
    for text in seen:
        assert text.startswith(head), text
        assert tp.LOAD_UNAVAILABLE.format(label=tp._label("email")) in text
    assert loader.record.holds() == ()


# ── 2. The member never reads MAF's text ────────────────────────────────────


@pytest.mark.parametrize("autoload", [False, True], ids=["text", "autoload"])
async def test_the_trail_never_shows_maf_text(autoload: bool, monkeypatch) -> None:
    """The step of the unknown call draws the member line, and stays failed.
    No text of the stream holds MAF's words. The model never reads the
    member line. Mutation: map ``content`` from ``exception`` again → the
    stream holds 'Function "query_inbox" not found.' → red."""
    _flag(monkeypatch, autoload)
    loader = _loader(offered=("email",), made={"email": [_pack_tool("query_inbox", [])]})
    turn = await _turn([("call", "query_inbox", {}), ("text", "Done.")], loader)
    first = turn.results()[0]
    assert first["content"] == ut.MEMBER_LINE
    assert first["success"] is False
    assert all(_no_maf_words(t) for t in turn.member_texts()), turn.member_texts()
    assert ut.MEMBER_LINE not in _tool_texts(turn.client)


async def test_the_trail_shows_the_member_line_with_no_answer_middleware() -> None:
    """A run with no HRD-1 middleware (a sub-agent) still draws the member
    line: the stream mapping does not depend on the rewrite."""
    turn = await _turn([("call", "query_inbox", {}), ("text", "Done.")], None, answer=False)
    assert [r["content"] for r in turn.results()] == [ut.MEMBER_LINE]
    assert all(_no_maf_words(t) for t in turn.member_texts())


@pytest.mark.parametrize("answer", [True, False], ids=["rewritten", "raw"])
async def test_the_batch_reader_shows_the_member_line(answer: bool, monkeypatch) -> None:
    """A batch sub-run sends its calls as ``SUB_AGENT_TOOL_CALL_RESULT``
    from ``executor._tool_calls_of``, after the run. After the HRD-1 rewrite,
    ``result`` holds the model's answer and ``exception`` still holds MAF's
    text. Mutation: read only ``result`` → the exception text → red."""
    from orchestrator.executor import _sub_call_events, _tool_calls_of

    _flag(monkeypatch, False)
    turn = await _turn([("call", "query_inbox", {}), ("text", "Done.")], None,
                       stream=False, answer=answer)
    calls = _tool_calls_of(turn.response)
    assert [(c["name"], c["result"], c["success"]) for c in calls] == [
        ("query_inbox", ut.MEMBER_LINE, False)]
    for event in _sub_call_events(calls, agent_name="worker", run_id="r1"):
        assert all(_no_maf_words(str(v)) for v in event.values()), event


def test_each_maf_shape_is_read() -> None:
    """The three fields of MAF's result, and the ``KeyError`` text in its
    quotes. Mutation: drop the quote rule of ``is_raw`` → red."""
    key_error = str(KeyError('Function "query_inbox" not found.'))
    assert key_error.startswith("'")
    assert ut.is_raw(key_error) == "query_inbox"
    raw = Content.from_function_result(
        call_id="c1", result='Error: Requested function "query_inbox" not found.',
        exception=key_error)
    assert ut.unknown_call_name(raw) == "query_inbox"
    after = Content.from_function_result(call_id="c1", result="Not run: …", exception=key_error)
    assert ut.unknown_call_name(after) == "query_inbox"
    ok = Content.from_function_result(call_id="c2", result="3 emails match.")
    assert ut.unknown_call_name(ok) == ""
    assert ut.is_raw("'Created the task.'") == ""


def test_the_member_line_is_plain() -> None:
    """No tool, pack or framework word. Each sentence is 25 words or fewer
    (STE, a description)."""
    line = ut.MEMBER_LINE
    assert _no_maf_words(line)
    for word in ("load_skill_tools", "pack", "function", "MAF", "query_inbox", "Error"):
        assert word not in line
    for sentence in filter(None, (s.strip() for s in line.split("."))):
        assert len(sentence.split()) <= 25, sentence
    assert line not in ut.TEXTS, "the model's texts and the member's line differ"


# ── 3. A turn that calls no unknown name is byte-identical ──────────────────


def _before(content: Any) -> tuple[str, bool]:
    """The mapping of a result before task 3599, word for word."""
    exc = getattr(content, "exception", None)
    result = getattr(content, "result", "") or ""
    return (str(exc) if exc else str(result)), exc is None and not is_refusal(result)


@pytest.mark.parametrize("content", [
    types.SimpleNamespace(type="function_result", call_id="c1", result="3 messages", exception=None),
    types.SimpleNamespace(type="function_result", call_id="c1", result="y" * 5000,
                          exception=RuntimeError("boom")),
    types.SimpleNamespace(type="function_result", call_id="c1",
                          result="Refused: The email app is not available (503).", exception=None),
    types.SimpleNamespace(type="function_result", call_id="c1", result="Error: Function failed.",
                          exception="HTTPStatusError: 404"),
    types.SimpleNamespace(type="function_result", call_id="c1", result=None, exception=None),
    types.SimpleNamespace(type="function_result", call_id="c1", result="Task not found.",
                          exception=None),
], ids=["ok", "exception", "refusal", "failed", "empty", "own-not-found"])
def test_other_results_map_as_before(content: Any) -> None:
    """Mutation: change the mapping for any result that is not MAF's
    unknown-name text → red."""
    assert tool_result_fields(content) == _before(content)


async def test_a_turn_with_no_unknown_call_is_byte_identical(monkeypatch) -> None:
    """The same turn, with and without the HRD-1 middleware: the requests and
    the tool result events are equal. Nothing of task 3599 runs."""
    _flag(monkeypatch, True)
    script = [("call", "_own", {}), ("text", "Done.")]
    with_answer = await _turn(list(script), _loader(offered=("email",)))
    without = await _turn(list(script), _loader(offered=("email",)), answer=False)
    assert with_answer.client.wire == without.client.wire
    assert with_answer.client.tools == without.client.tools
    assert with_answer.results() == without.results()
    assert with_answer.results()[0]["content"] == "own"
