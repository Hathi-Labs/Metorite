"""The parallel start of the turn kind. Chat speed, 2026-10-30.

Spec: ``project-docs/specs/ai_tier_routing.md`` §4.3 ("The parallel start"),
the box in §4.4 and §4.5. Dark behind ``AI_TURN_KIND_PARALLEL``.

Production measured the wait that the turn-kind question left after its
early start: 31 of 59 turns waited, median 1,749 ms, p90 2,702 ms. The
answer changed the kind in 13 of 89 turns. With the flag on, the first
request of a native agent's web chat turn starts at once as a provisional
``chat`` turn. An answer that moves the tier or the reasoning effort before
any output left the run cancels that request and starts the turn again,
ONCE. A later answer is dropped.

The run tests drive the REAL projects-assistant through the REAL
``run_agent_stream``, as ``test_tier_policy.py`` does. Only the HTTP
transports are scripts. :class:`GatedModel` streams each answer chunk by
chunk and holds where a test says, so a test decides which comes first: the
answer or the first token. Hermetic: no SQL runs here, so R8 binds nothing.

Mutations this file catches (R7), each run red before the change:

* the restart fires after the first token -> ``test_a_token_first_keeps_the_turn``;
* the restart fires after a tool started -> ``test_a_tool_first_keeps_the_turn``
  and ``test_the_tool_recorder_seals_before_the_tool_runs``;
* a card on the run queue does not seal -> ``test_a_card_first_keeps_the_turn``;
* the parked queue is not read in the tie -> ``test_a_card_parked_in_the_tie_keeps_the_turn``;
* the parallel path runs with the flag off ->
  ``test_flag_off_the_run_waits_for_the_answer_before_request_1``;
* the batch path, a sub-agent or a Copilot agent goes parallel ->
  ``TestTheOtherPathsStillWait``;
* a late pack answer loads a pack -> ``test_a_late_pack_answer_loads_nothing``
  and ``TestTheSettle::test_a_sealed_start_is_late``;
* the cancelled request is not closed, or its route event reaches the chat ->
  ``test_an_answer_first_restarts_once_on_the_new_tier``;
* the restart stores the cancelled request in the native session ->
  ``test_the_session_stores_the_turn_once``;
* a timeout moves the turn -> ``test_a_timeout_keeps_the_chat_tier``.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from acb_common.settings import get_settings
from acb_skills import tier_policy

from tests.unit._native_maf_harness import (
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    text_turn,
)
from tests.unit.test_tier_policy import (
    PA,
    PA_DIR,
    SECRET,
    _flags,
    _fresh_settings,  # noqa: F401 — an autouse fixture, used by name
    _ok,
    _routed,  # noqa: F401 — a fixture, used by name
    _routes,
    _tap,
    _turn_reply,
    _wire,
)

#: The flag of this slice.
FLAG = "AI_TURN_KIND_PARALLEL"
#: A card event, as ``request_confirmation`` puts it on the run queue.
CARD = {"type": "CUSTOM", "name": "confirmation_requested",
        "value": {"request_id": "card-1", "title": "Send it?"}}


@pytest.fixture(autouse=True)
def _no_flag(monkeypatch):
    """Each test sets the flag itself. The turn wait is long, so a loaded
    runner still gets its answer (``_patient`` in ``test_tier_policy.py``).
    A stalled stream fails in 60 s, not in the 120 s of production."""
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr(tier_policy, "TURN_KIND_TIMEOUT_S", 30.0)
    get_settings.cache_clear()
    executor = pytest.importorskip("orchestrator.executor")
    from orchestrator.watchdog import WatchdogPolicy

    monkeypatch.setattr(executor, "_WATCHDOG", WatchdogPolicy(idle=60.0))


def _parallel(monkeypatch, value: str | None, *, covered: str | None = PA) -> None:
    """``AI_TIER_ROUTING`` and ``AI_TURN_KIND_PARALLEL`` for one test."""
    _flags(monkeypatch, covered)
    if value is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, value)
    get_settings.cache_clear()


def _boom(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("a parallel start began on a path that must wait")


def _texts(events: list[dict[str, Any]]) -> str:
    return "".join(str(e.get("delta") or "") for e in events
                   if e.get("type") == "TEXT_MESSAGE_CONTENT")


def _lines(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [r for r in logs if r.get("event") == event]


# ── A model that streams chunk by chunk, and holds where a test says ────────


def _chunk(index: int, delta: dict[str, Any], finish: str | None = None) -> bytes:
    body = {
        "id": f"chatcmpl-{index}", "object": "chat.completion.chunk",
        "created": 0, "model": "probe",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n".encode()


Script = Callable[[int], AsyncIterator[bytes]]


def say(*parts: str, before: asyncio.Event | None = None,
        after_first: asyncio.Event | None = None) -> Script:
    """A streamed text answer. It waits for *before* first, and for
    *after_first* after its first chunk."""
    async def script(index: int) -> AsyncIterator[bytes]:
        if before is not None:
            await before.wait()
        for n, part in enumerate(parts):
            delta = {"role": "assistant", "content": part} if n == 0 else {"content": part}
            yield _chunk(index, delta)
            if n == 0 and after_first is not None:
                await after_first.wait()
        yield _chunk(index, {}, "stop")
        yield b"data: [DONE]\n\n"
    return script


def call(name: str, args: str = "{}", call_id: str = "call_1") -> Script:
    """A streamed call of tool *name*."""
    async def script(index: int) -> AsyncIterator[bytes]:
        yield _chunk(index, {"role": "assistant", "tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": args},
        }]})
        yield _chunk(index, {}, "tool_calls")
        yield b"data: [DONE]\n\n"
    return script


def hold(streaming: asyncio.Event, release: asyncio.Event) -> Script:
    """A request that starts its stream, says so, and sends nothing until
    *release*. The cancel of a restart lands here. A test passes the settle
    event as *release*: a turn that does not restart then ends at once, with
    text that the test refuses, and never stalls."""
    async def script(index: int) -> AsyncIterator[bytes]:
        streaming.set()
        await release.wait()
        yield _chunk(index, {"role": "assistant", "content": "LATE TEXT"})
        yield _chunk(index, {}, "stop")
        yield b"data: [DONE]\n\n"
    return script


class GatedModel:
    """The HTTP transport under the main agent. One script for each request.

    :attr:`closed` records how each stream ended: ``done`` when it sent its
    last chunk, ``cancelled`` when the run cancelled it in the middle. A
    stream that nobody closed has no entry.
    """

    def __init__(self, scripts: list[Script],
                 on_request: Callable[[int, dict[str, Any]], None] | None = None) -> None:
        self.scripts = scripts
        self.on_request = on_request
        self.bodies: list[dict[str, Any]] = []
        self.closed: dict[int, str] = {}

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        index = len(self.bodies)
        self.bodies.append(body)
        if self.on_request is not None:
            self.on_request(index, body)
        script = self.scripts[min(index, len(self.scripts) - 1)]

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for part in script(index):
                    if part.startswith(b"data: [DONE]"):
                        # The client stops reading here, so mark it first.
                        self.closed[index] = "done"
                    yield part
            except BaseException:
                self.closed.setdefault(index, "cancelled")
                raise

        return httpx.Response(200, content=stream(),
                              headers={"content-type": "text/event-stream"})


@pytest.fixture
def settled(monkeypatch) -> tuple[asyncio.Event, list[tuple[str, bool]]]:
    """Each settle of a parallel start: its outcome and whether it was fresh.
    The event lets a script go on once the executor took the answer."""
    done = asyncio.Event()
    outcomes: list[tuple[str, bool]] = []
    real = tier_policy.RunTierPolicy.settle

    def spy(self: Any, final: Any, *, fresh: bool = False) -> str:
        out = real(self, final, fresh=fresh)
        outcomes.append((out, fresh))
        done.set()
        return out

    monkeypatch.setattr(tier_policy.RunTierPolicy, "settle", spy)
    return done, outcomes


def _waiting_reply(gate: asyncio.Event, kind: str = "code",
                   order: list[str] | None = None) -> Callable[[dict[str, Any]], Any]:
    """System 1 answers *kind* once *gate* is set."""
    async def reply(body: dict[str, Any]) -> Any:
        await gate.wait()
        if order is not None:
            order.append("answer")
        return _turn_reply(kind)(body)
    return reply


# ── 1. The flag ──────────────────────────────────────────────────────────────


def test_the_flag_ships_off(monkeypatch) -> None:
    assert get_settings().ai_turn_kind_parallel == ""
    assert not tier_policy.turn_kind_parallel_on(PA)


def test_a_list_and_a_star(monkeypatch) -> None:
    monkeypatch.setenv(FLAG, "crm-assistant, projects-assistant")
    get_settings.cache_clear()
    assert tier_policy.turn_kind_parallel_on(PA)
    assert not tier_policy.turn_kind_parallel_on("email-assistant")
    monkeypatch.setenv(FLAG, "*")
    get_settings.cache_clear()
    assert tier_policy.turn_kind_parallel_on("email-assistant")
    assert not tier_policy.turn_kind_parallel_on("")
    assert not tier_policy.turn_kind_parallel_on(None)


def test_a_broken_settings_read_reads_as_off(monkeypatch) -> None:
    import acb_common

    monkeypatch.setenv(FLAG, "*")

    def broken() -> Any:
        raise RuntimeError("settings are broken")

    monkeypatch.setattr(acb_common, "get_settings", broken)
    assert not tier_policy.turn_kind_parallel_on(PA)


def test_only_the_operator_writes_the_flag() -> None:
    from acb_common.env_guard import PLATFORM_ENV_NAMES

    assert FLAG in PLATFORM_ENV_NAMES


# ── 2. The settle, at the policy ─────────────────────────────────────────────


def _policy(kind: str = "chat", effort: str = "auto", default: str = "tier-balanced",
            packs: tuple[str, ...] = ()) -> tier_policy.RunTierPolicy:
    return tier_policy.RunTierPolicy(agent=PA, run_id="r-par", default=default,
                                     kind=kind, effort=effort, packs=packs)


class TestTheSettle:
    def test_an_open_start_moves_on_a_new_tier(self, monkeypatch) -> None:
        logs = _tap(monkeypatch)
        policy = _policy()
        policy.open_start()
        policy.record(policy.next_choice())
        out = policy.settle(_policy(kind="code", packs=("email",)))
        assert out == tier_policy.SETTLE_MOVED
        assert (policy.kind, policy.packs, policy.run_tier()) == ("code", ("email",), "tier-powerful")
        assert policy.start == tier_policy.START_MOVED
        line = _lines(logs, "ai_route.restart")[0]
        assert (line["from_tier"], line["to_tier"], line["sent"]) == (
            "tier-balanced", "tier-powerful", 1)
        assert isinstance(line["elapsed_ms"], int)
        # The restart counts its requests from 1 again.
        policy.record(policy.next_choice())
        assert [r["request"] for r in _lines(logs, "ai_route.chosen")] == [1, 1]

    def test_an_answer_that_moves_nothing_keeps_the_turn(self, monkeypatch) -> None:
        logs = _tap(monkeypatch)
        policy = _policy()
        policy.open_start()
        assert policy.settle(_policy(packs=("email",))) == tier_policy.SETTLE_KEPT
        assert (policy.kind, policy.packs) == ("chat", ())
        assert [r["packs"] for r in _lines(logs, "ai_route.pack_late")] == [["email"]]
        assert not _lines(logs, "ai_route.restart")

    def test_a_fresh_turn_takes_the_packs_of_a_kept_answer(self) -> None:
        policy = _policy()
        policy.open_start()
        assert policy.settle(_policy(packs=("email",)), fresh=True) == tier_policy.SETTLE_KEPT
        assert policy.packs == ("email",)

    def test_no_answer_keeps_the_chat_tier(self) -> None:
        policy = _policy()
        policy.open_start()
        assert policy.settle(None) == tier_policy.SETTLE_KEPT
        assert policy.run_tier() == "tier-balanced"

    def test_a_sealed_start_is_late(self, monkeypatch) -> None:
        logs = _tap(monkeypatch)
        policy = _policy()
        policy.open_start()
        assert policy.seal() and not policy.seal()
        out = policy.settle(_policy(kind="plan", packs=("email",)))
        assert out == tier_policy.SETTLE_LATE
        assert (policy.kind, policy.packs, policy.run_tier()) == ("chat", (), "tier-balanced")
        late = _lines(logs, "ai_route.answer_late")[0]
        assert (late["tier"], late["would_tier"], late["moved"]) == (
            "tier-balanced", "tier-powerful", True)
        assert _lines(logs, "ai_route.pack_late")[0]["reason"] == "late"

    def test_a_reasoning_change_alone_is_a_move(self, monkeypatch) -> None:
        """A pinned default keeps the model, but thinking comes back on."""
        monkeypatch.setenv("AI_REASONING_OFF_AGENTS", PA)
        get_settings.cache_clear()
        policy = _policy(default="tier-powerful")
        policy.open_start()
        assert policy.request_shape() == ("tier-powerful", "none", "auto")
        assert policy.settle(_policy(kind="plan", default="tier-powerful")) == "moved"
        assert policy.request_shape() == ("tier-powerful", None, "auto")

    def test_an_effort_change_alone_is_a_move(self) -> None:
        policy = _policy(default="tier-powerful")
        policy.open_start()
        final = _policy(default="tier-powerful", effort="thinking")
        assert final.run_tier() == policy.run_tier()
        assert policy.settle(final) == "moved"
        assert policy.effort == "thinking"

    def test_a_run_with_no_parallel_start_never_changes(self) -> None:
        policy = _policy()
        assert not policy.seal()
        assert policy.start == ""

    def test_the_tool_recorder_seals_before_the_tool_runs(self) -> None:
        policy = _policy()
        policy.open_start()
        seen: list[str] = []

        async def call_next() -> None:
            seen.append(policy.start)

        context = type("Ctx", (), {"function": None})()
        asyncio.run(tier_policy.ToolHintRecorder(policy).process(context, call_next))
        assert seen == [tier_policy.START_SEALED]

    def test_no_line_holds_tenant_text(self, monkeypatch) -> None:
        logs = _tap(monkeypatch)
        policy = _policy()
        policy.open_start()
        policy.settle(_policy(kind="code"))
        text = json.dumps([r for r in logs if str(r.get("event")).startswith("ai_route.")],
                          default=str)
        for word in ("Acme", "invoice", "Refactor"):
            assert word not in text


# ── 3. A real projects-assistant run, through the REAL executor ─────────────


@pytest.mark.usefixtures("_routed", "_a_tenant")
class TestARealRun:
    def test_an_answer_first_restarts_once_on_the_new_tier(self, monkeypatch, settled) -> None:
        """Parallel start: request 1 is on the wire before the answer. The
        answer moves the turn before any output, so the run cancels request 1
        inside its stream and starts again on tier-powerful. The chat sees
        one ``ai.route`` event for each request it sees, numbered from 1."""
        _parallel(monkeypatch, PA)
        streaming = asyncio.Event()
        order: list[str] = []
        wire = _wire(monkeypatch, _waiting_reply(streaming, "code", order))
        model = GatedModel([hold(streaming, settled[0]), call("vocabulary"), say("done")],
                           on_request=lambda _i, _b: order.append("request"))
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert len(wire.requests) == 1
        assert order == ["request", "answer", "request", "request"]
        assert [b["model"] for b in model.bodies] == [
            "tier-balanced", "tier-powerful", "tier-powerful"]
        assert model.closed == {0: "cancelled", 1: "done", 2: "done"}
        assert settled[1] == [("moved", False)]
        assert [(r["tier"], r["kind"], r["reason"], r["request"]) for r in _routes(events)] == [
            ("tier-powerful", "code", "turn_kind", 1), ("tier-powerful", "code", "turn_kind", 2)]
        assert _texts(events) == "done"
        assert "LATE TEXT" not in json.dumps(events)
        start = _lines(logs, "ai_route.parallel_start")
        assert [r["tier"] for r in start] == ["tier-balanced"]
        restart = _lines(logs, "ai_route.restart")
        assert len(restart) == 1
        assert (restart[0]["from_tier"], restart[0]["to_tier"], restart[0]["sent"]) == (
            "tier-balanced", "tier-powerful", 1)
        assert [r["request"] for r in _lines(logs, "ai_route.chosen")] == [1, 1, 2]
        setup = _lines(logs, "executor.run_setup_ms")[0]
        assert setup["tier_parallel"] is True

    def test_a_token_first_keeps_the_turn(self, monkeypatch, settled) -> None:
        """The first token left the run before the answer. The turn keeps the
        ``chat`` tier, and the answer is late."""
        _parallel(monkeypatch, PA)
        token = asyncio.Event()
        wire = _wire(monkeypatch, _waiting_reply(token, "code"))
        model = GatedModel([say("Hello", " world", after_first=settled[0])])
        logs = _tap(monkeypatch)
        events, _ = drive_native(
            PA, PA_DIR, monkeypatch, model, message=SECRET,
            on_event=lambda e: token.set() if e.get("type") == "TEXT_MESSAGE_CONTENT" else None,
        )
        _ok(events)
        assert len(wire.requests) == 1
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        assert settled[1] == [("late", False)]
        assert _texts(events) == "Hello world"
        assert [(r["tier"], r["kind"]) for r in _routes(events)] == [("tier-balanced", "chat")]
        late = _lines(logs, "ai_route.answer_late")
        assert [(r["would_tier"], r["moved"]) for r in late] == [("tier-powerful", True)]
        assert not _lines(logs, "ai_route.restart")

    def test_a_tool_first_keeps_the_turn(self, monkeypatch, settled) -> None:
        _parallel(monkeypatch, PA)
        tool = asyncio.Event()
        _wire(monkeypatch, _waiting_reply(tool, "code"))
        model = GatedModel([call("vocabulary"), say("done", before=settled[0])])
        events, _ = drive_native(
            PA, PA_DIR, monkeypatch, model, message=SECRET,
            on_event=lambda e: tool.set() if e.get("type") == "TOOL_CALL_START" else None,
        )
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-balanced", "tier-balanced"]
        assert settled[1] == [("late", False)]

    def test_a_card_first_keeps_the_turn(self, monkeypatch, settled) -> None:
        """A card on the run queue is output too, so it seals the start."""
        executor = pytest.importorskip("orchestrator.executor")
        _parallel(monkeypatch, PA)
        card = asyncio.Event()
        _wire(monkeypatch, _waiting_reply(card, "code"))
        model = GatedModel(
            [say("done", before=settled[0])],
            on_request=lambda _i, _b: executor._active_run_queue.get().put_nowait(dict(CARD)),
        )
        events, _ = drive_native(
            PA, PA_DIR, monkeypatch, model, message=SECRET,
            on_event=lambda e: card.set() if e.get("name") == CARD["name"] else None,
        )
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        assert settled[1] == [("late", False)]
        names = [e.get("name") or e.get("type") for e in events]
        assert names.index(CARD["name"]) < names.index("TEXT_MESSAGE_CONTENT")

    def test_a_card_parked_in_the_tie_keeps_the_turn(self, monkeypatch, settled) -> None:
        """The tie: the answer and a card come in the same wake, and the
        queue read did not run. The card waits on the run queue, so the start
        seals, and the card still reaches the chat."""
        executor = pytest.importorskip("orchestrator.executor")
        _parallel(monkeypatch, PA)
        streaming = asyncio.Event()
        _wire(monkeypatch, _waiting_reply(streaming, "code"))
        queues: list[Any] = []
        model = GatedModel(
            [_announce(streaming, say("done", before=settled[0]))],
            on_request=lambda _i, _b: queues.append(executor._active_run_queue.get()),
        )
        real_wait = asyncio.wait
        injected: list[bool] = []

        async def wait(fs: Any, *a: Any, **k: Any) -> Any:
            done, pending = await real_wait(fs, *a, **k)
            if not injected and any(_is_answer(f) for f in done):
                queues[0].put_nowait(dict(CARD))
                injected.append(True)
            return done, pending

        monkeypatch.setattr(asyncio, "wait", wait)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert injected
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        assert settled[1] == [("late", False)]
        assert CARD["name"] in [e.get("name") for e in events]

    def test_a_late_pack_answer_loads_nothing(self, monkeypatch, settled) -> None:
        """Packs never join a running turn. The answer's packs go, and
        ``ai_route.pack_late`` says so. The restart test below loads them."""
        executor = pytest.importorskip("orchestrator.executor")
        _parallel(monkeypatch, PA)
        _with_packs(monkeypatch, executor, ("email",))
        handed = _spy_packs(monkeypatch, executor)
        token = asyncio.Event()
        _wire(monkeypatch, _waiting_reply(token, "code"))
        model = GatedModel([say("Hello", " world", after_first=settled[0])])
        logs = _tap(monkeypatch)
        events, _ = drive_native(
            PA, PA_DIR, monkeypatch, model, message=SECRET,
            on_event=lambda e: token.set() if e.get("type") == "TEXT_MESSAGE_CONTENT" else None,
        )
        _ok(events)
        assert settled[1] == [("late", False)]
        assert all(packs == () for packs in handed), handed
        assert [(r["packs"], r["reason"]) for r in _lines(logs, "ai_route.pack_late")] == [
            (["email"], "late")]

    def test_a_restart_hands_the_packs_to_the_loader(self, monkeypatch, settled) -> None:
        executor = pytest.importorskip("orchestrator.executor")
        _parallel(monkeypatch, PA)
        _with_packs(monkeypatch, executor, ("email",))
        handed = _spy_packs(monkeypatch, executor)
        streaming = asyncio.Event()
        _wire(monkeypatch, _waiting_reply(streaming, "code"))
        model = GatedModel([hold(streaming, settled[0]), say("done")])
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert settled[1] == [("moved", False)]
        assert handed == [(), ("email",)]

    def test_a_timeout_keeps_the_chat_tier(self, monkeypatch, settled) -> None:
        _parallel(monkeypatch, PA)

        async def slow(body: dict[str, Any]) -> Any:
            await asyncio.sleep(2.0)
            return _turn_reply("code")(body)

        _wire(monkeypatch, slow)
        monkeypatch.setattr(tier_policy, "TURN_KIND_TIMEOUT_S", 0.2)
        model = GatedModel([say("done", before=settled[0])])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        assert [o for o, _fresh in settled[1]] == ["kept"]
        assert [r["source"] for r in _lines(logs, "ai_route.turn_kind")] == ["timeout"]
        assert not _lines(logs, "ai_route.restart")

    def test_a_restart_sends_what_the_sequential_run_sends(self, monkeypatch, settled) -> None:
        """The restart applies the answer in its own place
        (``_apply_parallel_tier``). Its request must equal the first request
        of a run with the flag off: the same model, the same Thinking effort,
        the same messages and tools."""
        deep = ("Please think hard and be thorough about how the invoice exporter "
                "for Acme should change next quarter")

        def reply(body: dict[str, Any]) -> Any:
            from tests.unit.test_tier_policy import _answers

            return _answers({"id": "turn", "choice": "code", "confidence": 0.95, "reason": "x"},
                            {"id": "effort", "choice": "yes", "confidence": 0.95, "reason": "x"})

        _parallel(monkeypatch, None)
        _wire(monkeypatch, reply)
        flat = ScriptedModel([text_turn("done")])
        events, _ = drive_native(PA, PA_DIR, monkeypatch, flat, message=deep,
                                 thread_id="thread-same")
        _ok(events)

        _parallel(monkeypatch, PA)
        streaming = asyncio.Event()

        async def gated(body: dict[str, Any]) -> Any:
            await streaming.wait()
            return reply(body)

        _wire(monkeypatch, gated)
        model = GatedModel([hold(streaming, settled[0]), say("done")])
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=deep,
                                 thread_id="thread-same")
        _ok(events)
        assert settled[1] == [("moved", False)]
        assert model.bodies[1]["model"] == "tier-powerful"
        assert model.bodies[1].get("reasoning_effort") == "medium"
        assert json.dumps(model.bodies[1], sort_keys=True) == json.dumps(
            flat.bodies[0], sort_keys=True)

    def test_the_session_stores_the_turn_once(self, monkeypatch, settled) -> None:
        """Hazard (d). The cancelled request never finishes, so MAF never
        runs its ``after_run``. The saved session holds the member's message
        once and the answer of the restarted request."""
        executor = pytest.importorskip("orchestrator.executor")
        from agent_framework import AgentSession
        from orchestrator import native_session_store as nss

        turn = nss.SessionTurn(
            organization_id="org-native-maf-probe", thread_id="thread-sess", agent_name=PA,
            outcome=nss.NO_ROW, session=AgentSession(), history=nss.SessionHistoryProvider(),
            fingerprint="", save_digest="",
        )
        saved: list[str] = []

        async def begin(*_a: Any, **_k: Any) -> Any:
            return turn

        async def finish(t: Any) -> None:
            saved.append(json.dumps(t.session.to_dict(), default=str))

        monkeypatch.setattr(executor, "_native_sessions_enabled", lambda: True)
        monkeypatch.setattr(executor, "_begin_native_session", begin)
        monkeypatch.setattr(executor, "_finish_native_session", finish)
        _parallel(monkeypatch, PA)
        streaming = asyncio.Event()
        _wire(monkeypatch, _waiting_reply(streaming, "code"))
        model = GatedModel([hold(streaming, settled[0]), say("done")])
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET,
                                 thread_id="thread-sess")
        _ok(events)
        assert settled[1] == [("moved", False)]
        assert len(saved) == 1
        assert saved[0].count("Refactor the invoice exporter") == 1
        assert saved[0].count('"done"') == 1
        assert "LATE TEXT" not in saved[0]

    def test_flag_off_the_run_waits_for_the_answer_before_request_1(self, monkeypatch) -> None:
        """Flag OFF equals current behaviour: the answer comes BEFORE the
        first request, as on main, and no parallel line shows."""
        _parallel(monkeypatch, None)
        monkeypatch.setattr(tier_policy.RunTierPolicy, "open_start", _boom)
        order: list[str] = []

        async def reply(body: dict[str, Any]) -> Any:
            await asyncio.sleep(0.05)
            order.append("answer")
            return _turn_reply("code")(body)

        _wire(monkeypatch, reply)
        model = ScriptedModel([text_turn("done")],
                              on_request=lambda _i, _b: order.append("request"))
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert order == ["answer", "request"]
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]
        assert "tier_parallel" not in _lines(logs, "executor.run_setup_ms")[0]
        quiet = {"ai_route.parallel_start", "ai_route.restart", "ai_route.answer_late",
                 "ai_route.pack_late"}
        assert not [r for r in logs if r.get("event") in quiet]

    def test_a_flag_for_another_agent_changes_nothing(self, monkeypatch) -> None:
        _parallel(monkeypatch, "crm-assistant")
        monkeypatch.setattr(tier_policy.RunTierPolicy, "open_start", _boom)
        _wire(monkeypatch, _turn_reply("code"))
        model = ScriptedModel([text_turn("done")])
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]


def _announce(streaming: asyncio.Event, script: Script) -> Script:
    async def wrapped(index: int) -> AsyncIterator[bytes]:
        streaming.set()
        async for part in script(index):
            yield part
    return wrapped


def _is_answer(future: Any) -> bool:
    """True for the done task of the turn-kind question."""
    return (future.done() and not future.cancelled() and future.exception() is None
            and isinstance(future.result(), tier_policy.RunTierPolicy))


def _with_packs(monkeypatch, executor: Any, packs: tuple[str, ...]) -> None:
    """The answer asks for *packs*. projects-assistant offers none, so the
    test puts them on the answer's policy."""
    real = executor._tier_policy_for_run

    async def packed(*a: Any, **k: Any) -> Any:
        run = await real(*a, **k)
        if run is not None:
            run.packs = packs
        return run

    monkeypatch.setattr(executor, "_tier_policy_for_run", packed)


def _spy_packs(monkeypatch, executor: Any) -> list[tuple[str, ...]]:
    handed: list[tuple[str, ...]] = []
    real = executor._apply_turn_pack_items

    def spy(agents: Any, tier_run: Any) -> None:
        handed.append(tuple(getattr(tier_run, "packs", ()) or ()))
        real(agents, tier_run)

    monkeypatch.setattr(executor, "_apply_turn_pack_items", spy)
    return handed


# ── 4. The other paths still wait ────────────────────────────────────────────


@pytest.mark.usefixtures("_routed", "_a_tenant")
@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
class TestTheOtherPathsStillWait:
    """The verdict: the batch path, a sub-agent and a Copilot SDK agent keep
    the sequential order with the flag at ``*``."""

    def _order(self, monkeypatch) -> list[str]:
        order: list[str] = []

        async def reply(body: dict[str, Any]) -> Any:
            await asyncio.sleep(0.05)
            order.append("answer")
            return _turn_reply("code")(body)

        _wire(monkeypatch, reply)
        monkeypatch.setattr(tier_policy.RunTierPolicy, "open_start", _boom)
        return order

    def test_the_batch_path(self, monkeypatch) -> None:
        from tests.unit.test_tier_policy_agents import (
            BATCH_AGENT,
            NATIVE,
            JsonModel,
            _batch,
            _patch_native_load,
        )

        _parallel(monkeypatch, "*", covered=BATCH_AGENT)
        order = self._order(monkeypatch)
        model = JsonModel([{"text": "done"}], on_request=lambda _i: order.append("request"))
        _patch_native_load(monkeypatch, BATCH_AGENT, NATIVE[BATCH_AGENT], model)
        result = _batch(BATCH_AGENT, message=SECRET)
        assert result["answer"] == "done"
        assert order == ["answer", "request"]
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]

    def test_a_sub_agent(self, monkeypatch) -> None:
        from tests.unit.test_tier_policy_agents import (
            NATIVE,
            SUB,
            JsonModel,
            _delegate,
            _patch_native_load,
        )

        _parallel(monkeypatch, "*", covered=SUB)
        order = self._order(monkeypatch)
        model = JsonModel([{"text": "done"}], on_request=lambda _i: order.append("request"))
        _patch_native_load(monkeypatch, SUB, NATIVE[SUB], model)
        text, _ = _delegate(SUB, parent_model="tier-balanced")
        assert text == "done"
        assert order == ["answer", "request"]
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]

    def test_a_copilot_agent(self, monkeypatch, tmp_path) -> None:
        from tests.unit.test_tier_policy_agents import _stream_copilot

        _parallel(monkeypatch, "*", covered="task-manager")
        self._order(monkeypatch)
        agent, events = _stream_copilot(monkeypatch, tmp_path, "task-manager", message=SECRET)
        assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
        assert set(agent.models) == {"tier-powerful"}


def test_only_the_stream_run_starts_in_parallel() -> None:
    """The parallel helpers have ONE caller, the stream run. Mutation: call
    one from ``run_agent`` or ``_run_sub_agent_streaming`` -> red."""
    from tests.unit.test_turn_kind_pack_items import _calls

    executor = "apps/services/orchestrator/orchestrator/executor.py"
    for name in ("_provisional_tier_policy", "_turn_kind_parallel", "_parallel_moved",
                 "_apply_parallel_tier"):
        assert _calls(executor, name) == {"run_agent_stream"}, name
