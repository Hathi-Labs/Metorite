"""The wait for the turn kind. Chat speed, task 3592.

Spec: ``project-docs/specs/ai_tier_routing.md`` §4.3 ("The wait of a
delegated run").

Production, 2026-11-17 from 10:46 to 14:22 UTC: 20 of 31 ``ai_route.turn_kind``
lines had ``source=timeout`` at ``latency_ms=3000``. What the code shows:

* The member's own web chat turn of a native agent that
  ``AI_TURN_KIND_PARALLEL`` names never waits. Its first request starts as a
  ``chat`` turn, and the question runs beside it. A timeout reads as ``chat``
  and moves nothing.
* A DELEGATED run waits. The batch path (``run_agent`` under a parent run)
  and a Copilot SDK sub-agent await the answer before their first request,
  up to :data:`tier_policy.TURN_KIND_TIMEOUT_S`. Neither has a seam that can
  start the run again with a late answer.

Choice B: with ``AI_TURN_KIND_NOWAIT_ORGS`` naming the run's org, a delegated
run asks no question (``turn_kind(ask=False)``, source ``nowait``). The cheap
sources (``max``, ``short``, ``pinned``) stay as they are. Every other run
asks as before. ``ai_route.turn_kind`` keeps its fields and gains ONE:
``waited_ms``, the time that the run waited for the answer before its first
request. It is 0 when the run did not wait.

The run tests drive the REAL executor, as ``test_turn_kind_parallel.py`` and
``test_tier_policy_agents.py`` do. Only the HTTP transports are scripts.
Hermetic: no SQL runs here, so R8 binds nothing.

Mutations this file catches (R7). Each was run red and then restored:

* the batch site never passes ``delegated`` ->
  ``test_flag_on_a_delegated_run_asks_no_question_and_starts_at_once``;
* the flag is ignored, so a delegated run never asks ->
  ``test_flag_off_a_delegated_run_still_waits_and_takes_the_answer``;
* the ``delegated and`` guard goes, so every run of a listed org stops
  asking -> ``test_flag_on_a_top_level_batch_run_still_asks`` and
  ``test_a_parallel_turn_logs_waited_ms_0_on_a_timeout``;
* the ``if not ask`` branch moves above a cheap source ->
  ``test_the_cheap_sources_keep_their_source_with_ask_false``;
* the sub-agent site of a Copilot SDK agent asks ->
  ``test_flag_on_a_copilot_sub_agent_asks_no_question``;
* the parallel start counts a wait -> ``test_a_parallel_turn_logs_waited_ms_0_on_a_timeout``;
* the sequential tier site does not start the wait clock ->
  ``test_a_sequential_turn_logs_the_wait``;
* the Tier 2 fallback does not start the wait clock ->
  ``test_a_tier_2_fallback_logs_its_wait``;
* the reader takes no bound tenant, or keeps its case ->
  ``test_a_list_a_star_and_the_bound_tenant``.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

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
    _is_turn_question,
    _ok,
    _routed,  # noqa: F401 — a fixture, used by name
    _tap,
    _turn_reply,
    _wire,
)
from tests.unit.test_turn_kind_parallel import (
    REFUSE,
    GatedModel,
    _deep_reply,
    _parallel,
    say,
    settled,  # noqa: F401 — a fixture, used by name
)

#: The flag of this slice.
FLAG = "AI_TURN_KIND_NOWAIT_ORGS"
#: The org that ``_a_tenant`` binds.
ORG = "org-native-maf-probe"
#: The fields of ``ai_route.turn_kind``. Production counts by them.
LINE_FIELDS = {"event", "log_level", "agent", "run_id", "kind", "source",
               "latency_ms", "waited_ms"}
#: A long message that explicitly asks for deep work.
DEEP = ("Please think hard and be thorough about how the invoice exporter "
        "for Acme should change next quarter")


@pytest.fixture(autouse=True)
def _no_flag(monkeypatch):
    """Each test sets the flag itself. The turn wait is long, so only a hang
    times out, and a stalled stream fails in 60 s."""
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr(tier_policy, "TURN_KIND_TIMEOUT_S", 30.0)
    get_settings.cache_clear()
    executor = pytest.importorskip("orchestrator.executor")
    from orchestrator.watchdog import WatchdogPolicy

    monkeypatch.setattr(executor, "_WATCHDOG", WatchdogPolicy(idle=60.0))


def _nowait(monkeypatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, value)
    get_settings.cache_clear()


def _lines(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [r for r in logs if r.get("event") == event]


def _slow(order: list[str] | None = None, *, kind: str = "code",
          bound_s: float = 10.0) -> Any:
    """System 1 answers *kind* only after *bound_s*. A gate that nothing
    opens stands in for a slow model: a run that waits for it waits the whole
    bound, and a run that asks no question never sees it."""
    never = asyncio.Event()

    async def reply(body: dict[str, Any]) -> Any:
        if _is_turn_question(body):
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(never.wait(), timeout=bound_s)
            if order is not None:
                order.append("answer")
        return _turn_reply(kind)(body)
    return reply


def _quick(order: list[str], *, kind: str = "code", after_s: float = 0.05) -> Any:
    async def reply(body: dict[str, Any]) -> Any:
        if _is_turn_question(body):
            await asyncio.sleep(after_s)
            order.append("answer")
        return _turn_reply(kind)(body)
    return reply


# ── 1. The flag ──────────────────────────────────────────────────────────────


def test_the_flag_ships_off() -> None:
    assert get_settings().ai_turn_kind_nowait_orgs == ""
    assert not tier_policy.turn_kind_nowait_on(ORG)
    assert not tier_policy.turn_kind_nowait_on(None)


def test_a_list_a_star_and_the_bound_tenant(monkeypatch) -> None:
    from acb_common.db import bind_tenant, release_tenant

    _nowait(monkeypatch, " Org-A , org-b ,, ")
    assert tier_policy.turn_kind_nowait_on("org-a")
    assert tier_policy.turn_kind_nowait_on(" ORG-B ")
    assert not tier_policy.turn_kind_nowait_on("org-c")
    assert not tier_policy.turn_kind_nowait_on(None)
    token = bind_tenant("org-a")
    try:
        # No org argument: the bound tenant of the run decides.
        assert tier_policy.turn_kind_nowait_on()
    finally:
        release_tenant(token)
    _nowait(monkeypatch, "*")
    assert tier_policy.turn_kind_nowait_on("org-z")
    assert tier_policy.turn_kind_nowait_on(None)


def test_a_broken_settings_read_reads_as_off(monkeypatch) -> None:
    import acb_common

    _nowait(monkeypatch, "*")

    def broken() -> Any:
        raise RuntimeError("settings are broken")

    monkeypatch.setattr(acb_common, "get_settings", broken)
    assert not tier_policy.turn_kind_nowait_on(ORG)


def test_only_the_operator_writes_the_flag() -> None:
    from acb_common.env_guard import PLATFORM_ENV_NAMES

    assert FLAG in PLATFORM_ENV_NAMES


# ── 2. turn_kind(ask=False) ──────────────────────────────────────────────────


def _kind(message: str, effort: str = "auto", **kw: Any) -> tier_policy.TurnKind:
    return asyncio.run(tier_policy.turn_kind(message, ("vocabulary",), effort, **kw))


@pytest.mark.usefixtures("_routed")
class TestAskFalse:
    def test_ask_false_sends_no_request_and_reads_as_nowait(self, monkeypatch) -> None:
        wire = _wire(monkeypatch, _turn_reply("code"))
        turn = _kind(SECRET, ask=False)
        assert (turn.kind, turn.source, turn.latency_ms) == ("chat", "nowait", None)
        assert (turn.effort, turn.packs) == (None, ())
        assert wire.requests == []
        # The same call with ask True sends the one request of before.
        assert _kind(SECRET).kind == "code"
        assert len(wire.requests) == 1

    def test_the_cheap_sources_keep_their_source_with_ask_false(self, monkeypatch) -> None:
        wire = _wire(monkeypatch, _turn_reply("code"))
        short = "one two three four five six seven eight nine ten eleven"
        assert _kind(short, ask=False).source == "short"
        assert _kind(SECRET, effort="max", ask=False).source == "max"
        assert _kind(SECRET, default="tier-powerful", ask=False).source == "pinned"
        deep_short = _kind("think hard about this", read_effort=True, ask=False)
        assert (deep_short.source, deep_short.effort, deep_short.effort_source) == (
            "short", "thinking", "keyword")
        assert wire.requests == []

    def test_ask_false_still_reads_the_keyword_effort(self, monkeypatch) -> None:
        wire = _wire(monkeypatch, _turn_reply("code"))
        turn = _kind(DEEP, read_effort=True, ask=False)
        assert (turn.kind, turn.source, turn.effort, turn.effort_source) == (
            "chat", "nowait", "thinking", "keyword")
        # Thinking already: the words raise nothing.
        assert _kind(DEEP, effort="thinking", read_effort=True, ask=False).effort is None
        assert wire.requests == []


# ── 3. A delegated run, through the real executor ────────────────────────────


@pytest.mark.usefixtures("_routed", "_a_tenant")
@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
class TestADelegatedRun:
    def test_flag_on_a_delegated_run_asks_no_question_and_starts_at_once(
        self, monkeypatch,
    ) -> None:
        """The flag names the run's org. The sub-run's first request goes out
        at once, on the ``chat`` kind. A slow System 1 would hold it 30 s.

        The clock starts when the run asks its tier policy, not when the
        test starts: the setup of a sub-run before that point can take 15 s
        on a loaded runner, and it is not the wait under test."""
        from tests.unit.test_tier_policy_agents import (
            NATIVE,
            SUB,
            JsonModel,
            _delegate,
            _patch_native_load,
        )

        executor = pytest.importorskip("orchestrator.executor")
        _flags(monkeypatch, SUB)
        _nowait(monkeypatch, ORG)
        wire = _wire(monkeypatch, _slow(bound_s=30.0))
        asked: list[float] = []
        real = executor._tier_policy_for_run

        async def timed(*a: Any, **k: Any) -> Any:
            asked.append(time.monotonic())
            return await real(*a, **k)

        monkeypatch.setattr(executor, "_tier_policy_for_run", timed)
        first: list[float] = []
        model = JsonModel([{"text": "done"}],
                          on_request=lambda _i: first.append(time.monotonic()))
        _patch_native_load(monkeypatch, SUB, NATIVE[SUB], model)
        logs = _tap(monkeypatch)
        text, _ = _delegate(SUB, parent_model="tier-balanced")
        assert text == "done"
        assert wire.requests == []
        assert len(asked) == 1 and first
        assert first[0] - asked[0] < 10.0
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        line = _lines(logs, "ai_route.turn_kind")[0]
        assert set(line) == LINE_FIELDS
        assert (line["kind"], line["source"], line["latency_ms"]) == ("chat", "nowait", None)
        assert line["waited_ms"] < 1000

    def test_flag_off_a_delegated_run_still_waits_and_takes_the_answer(
        self, monkeypatch,
    ) -> None:
        """Flag off is the behaviour of before: the answer comes BEFORE the
        first request, and a ``code`` answer raises the tier. ``waited_ms``
        counts the whole call, because the run awaits it."""
        from tests.unit.test_tier_policy_agents import (
            NATIVE,
            SUB,
            JsonModel,
            _delegate,
            _patch_native_load,
        )

        for value in (None, "org-other"):
            _flags(monkeypatch, SUB)
            _nowait(monkeypatch, value)
            order: list[str] = []
            wire = _wire(monkeypatch, _quick(order))
            model = JsonModel([{"text": "done"}],
                              on_request=lambda _i, _o=order: _o.append("request"))
            _patch_native_load(monkeypatch, SUB, NATIVE[SUB], model)
            logs = _tap(monkeypatch)
            _delegate(SUB, parent_model="tier-balanced")
            assert order == ["answer", "request"], value
            assert len(wire.requests) == 1
            assert [b["model"] for b in model.bodies] == ["tier-powerful"]
            line = _lines(logs, "ai_route.turn_kind")[0]
            assert set(line) == LINE_FIELDS
            assert (line["kind"], line["source"]) == ("code", "system_one")
            assert line["waited_ms"] >= 40

    def test_flag_on_a_top_level_batch_run_still_asks(self, monkeypatch) -> None:
        """A batch run with no parent run (WhatsApp, a workflow) is not
        delegated. It asks as before, also under ``*``."""
        from tests.unit.test_tier_policy_agents import (
            BATCH_AGENT,
            NATIVE,
            JsonModel,
            _batch,
            _patch_native_load,
        )

        _flags(monkeypatch, BATCH_AGENT)
        _nowait(monkeypatch, "*")
        order: list[str] = []
        wire = _wire(monkeypatch, _quick(order))
        model = JsonModel([{"text": "done"}], on_request=lambda _i: order.append("request"))
        _patch_native_load(monkeypatch, BATCH_AGENT, NATIVE[BATCH_AGENT], model)
        result = _batch(BATCH_AGENT, message=SECRET)
        assert result["answer"] == "done"
        assert order == ["answer", "request"]
        assert _is_turn_question(wire.bodies[0])
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]

    def test_flag_on_a_copilot_sub_agent_asks_no_question(self, monkeypatch, tmp_path) -> None:
        """The second delegated site. A delegated Copilot agent is refused
        before it runs (D84, D92), so the test switches the refusal off, as
        ``test_a_covered_copilot_sub_agent_takes_its_own_tier`` does."""
        from tests.unit.test_tier_policy import _CopilotShaped
        from tests.unit.test_tier_policy_agents import _delegate, _loaded

        executor = pytest.importorskip("orchestrator.executor")
        routes_agent = pytest.importorskip("gateway.routes.agent")
        monkeypatch.setattr(executor, "refuse_copilot_specialist", lambda *_a, **_k: None)
        for value, expected in (("*", "tier-balanced"), (None, "tier-powerful")):
            _flags(monkeypatch, "task-manager")
            _nowait(monkeypatch, value)
            wire = _wire(monkeypatch, _turn_reply("code"))
            agent = _CopilotShaped()
            monkeypatch.setattr(executor, "load_agent",
                                lambda *a, _ag=agent, **k: _loaded(_ag, "task-manager", tmp_path))
            monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
            monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
            _delegate("task-manager", parent_model="tier-fast")
            assert set(agent.models) == {expected}, (value, agent.models)
            assert (wire.requests == []) is (value == "*")


# ── 4. The member's own stream turn: waited_ms ───────────────────────────────


@pytest.mark.usefixtures("_routed", "_a_tenant")
class TestTheStreamTurn:
    def test_a_parallel_turn_logs_waited_ms_0_on_a_timeout(
        self, monkeypatch, settled,  # noqa: F811 — the imported fixture
    ) -> None:
        """The flagged turn does not wait. A slow System 1 times out, the
        line keeps its fields, and ``waited_ms`` is 0. The NOWAIT flag does
        not touch this run: the question still goes out."""
        _parallel(monkeypatch, PA)
        _nowait(monkeypatch, "*")
        wire = _wire(monkeypatch, _slow(bound_s=5.0))
        monkeypatch.setattr(tier_policy, "TURN_KIND_TIMEOUT_S", 0.2)
        model = GatedModel([say("done", before=settled[0])])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert len(wire.requests) == 1
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        line = _lines(logs, "ai_route.turn_kind")[0]
        assert set(line) == LINE_FIELDS
        assert (line["kind"], line["source"]) == ("chat", "timeout")
        assert line["latency_ms"] >= 150
        assert line["waited_ms"] == 0
        assert _lines(logs, "executor.run_setup_ms")[0]["tier_parallel"] is True
        assert not _lines(logs, "ai_route.restart")

    def test_a_sequential_turn_logs_the_wait(self, monkeypatch) -> None:
        """Flag off: the tier site awaits the answer, which comes 0.3 s after
        the site read the flag. ``waited_ms`` holds that wait, and never more
        than the question took."""
        executor = pytest.importorskip("orchestrator.executor")
        _parallel(monkeypatch, None)
        read = asyncio.Event()
        real = executor._turn_kind_parallel

        def spy(agent_name: str) -> bool:
            read.set()
            return real(agent_name)

        monkeypatch.setattr(executor, "_turn_kind_parallel", spy)
        order: list[str] = []

        async def reply(body: dict[str, Any]) -> Any:
            if _is_turn_question(body):
                await asyncio.wait_for(read.wait(), timeout=10.0)
                await asyncio.sleep(0.3)
                order.append("answer")
            return _turn_reply("code")(body)

        _wire(monkeypatch, reply)
        model = ScriptedModel([text_turn("done")],
                              on_request=lambda _i, _b: order.append("request"))
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert order == ["answer", "request"]
        line = _lines(logs, "ai_route.turn_kind")[0]
        assert set(line) == LINE_FIELDS
        assert (line["kind"], line["source"]) == ("code", "system_one")
        assert 250 <= line["waited_ms"] <= line["latency_ms"] + 50

    def test_a_tier_2_fallback_logs_its_wait(
        self, monkeypatch, settled,  # noqa: F811 — the imported fixture
    ) -> None:
        """A parallel start whose first request fails before any output
        falls back to Tier 2, and Tier 2 waits for the answer. The answer
        comes 0.3 s after the refused request, so ``waited_ms`` holds that
        wait. It is not 0, as on a parallel turn that never waits."""
        _parallel(monkeypatch, PA)
        refused = asyncio.Event()
        _wire(monkeypatch, _deep_reply(refused, "chat", after_s=0.3))
        model = GatedModel([REFUSE], on_request=lambda i, _b: refused.set() if i == 0 else None)
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=DEEP)
        _ok(events)
        assert _lines(logs, "executor.native_maf_stream_fallback")
        assert [o for o, _fresh in settled[1]] == ["moved"]
        line = _lines(logs, "ai_route.turn_kind")[0]
        assert set(line) == LINE_FIELDS
        assert line["source"] == "system_one"
        assert 100 <= line["waited_ms"] <= line["latency_ms"] + 50

    def test_a_short_turn_waits_for_nothing(self, monkeypatch) -> None:
        _parallel(monkeypatch, None)
        wire = _wire(monkeypatch, _turn_reply("code"))
        model = ScriptedModel([text_turn("done")])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message="hi there")
        _ok(events)
        assert wire.requests == []
        line = _lines(logs, "ai_route.turn_kind")[0]
        assert (line["source"], line["latency_ms"]) == ("short", None)
        assert line["waited_ms"] < 50
