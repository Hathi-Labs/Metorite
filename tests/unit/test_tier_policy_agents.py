"""The AI tier policy for every other agent — WS-45 S4b (D90).

Spec: ``project-docs/specs/ai_tier_routing.md`` §4.5 and §11 S4.

S2 built the policy for ONE run on the chat path (``run_agent_stream``), and
proved it on the projects-assistant (``test_tier_policy.py``). S4b does two
things, and this file holds both:

1. **Each named agent passes S2's done-when items 1 to 5 under the flag.**
   The native agents run through the REAL ``run_agent_stream`` with their
   REAL factory, on the harness of ``test_native_maf_wire.py``. The two
   Copilot SDK agents cannot switch per request (§4.5, D84), so they hold
   the run-level shape of items 1, 2, 3 and 5. Item 4 cannot hold for them.
2. **A sub-agent runs its own policy** (§4.5, the last bullet). A MAF
   sub-agent runs through the batch path, ``run_agent``, so the batch path
   now attaches the policy. A Copilot SDK sub-agent gets the policy's tier
   for the whole run. Neither emits an ``ai.route`` event, because the
   parent's queue would fold it into the parent's answer label.

Ship dark. With ``AI_TIER_ROUTING`` unset, or naming another agent, a batch
run and a sub-agent run send the same bytes, and the sub-agent inherits the
parent's tier as on ``origin/main``.

Hermetic: no SQL runs on this path, so R8 binds nothing here.

Mutations this file catches (R7), each run red before the change:

* the batch path drops the policy (``tier_policy=None`` in ``run_agent``)
  -> ``TestTheBatchPath::test_a_covered_batch_run_runs_its_own_policy``;
* the batch path reads the policy for an agent the flag does not cover
  -> ``test_with_the_flag_unset_a_batch_run_sends_the_same_bytes``;
* a covered MAF sub-agent inherits the parent's tier again
  -> ``TestSubAgents::test_a_covered_maf_sub_agent_ignores_the_parent_tier``;
* an uncovered sub-agent stops inheriting it
  -> ``TestSubAgents::test_an_uncovered_sub_agent_still_inherits``;
* a sub-agent's ``ai.route`` event reaches the parent's queue
  -> ``test_a_sub_agent_emits_no_route_event``;
* a covered Copilot SDK sub-agent keeps the parent's tier
  -> ``TestSubAgents::test_a_covered_copilot_sub_agent_takes_its_own_tier``;
* a native agent loses an S2 done-when item -> ``TestEveryNativeAgent``.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
import pytest
from acb_skills import tier_policy
from acb_skills.write_artifact import artifact_context, bind_artifact_context

from tests.unit._native_maf_harness import (
    REPO_ROOT,
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    load_agent_module,
    parse_frames,
    text_turn,
    tool_turn,
)
from tests.unit.test_tier_policy import (
    SECRET,
    _CopilotShaped,
    _flags,
    _is_turn_question,
    _ok,
    _routed,  # noqa: F401 — a fixture, used by name
    _routes,
    _tap,
    _turn_reply,
    _wire,
)

#: The native agents of §11 S4, and their dirs. projects-assistant is S1/S2.
NATIVE: dict[str, str] = {
    "email-assistant": "apps/agents/agent-email-assistant",
    "crm-assistant": "apps/agents/agent-crm",
    "whatsapp-assistant": "apps/agents/agent-whatsapp-assistant",
    "orchestrator": "apps/agents/agent-orchestrator",
    "apis-config": "apps/agents/agent-apis-config",
}

#: The Copilot SDK agents of §11 S4. They switch tier per run, never per request.
COPILOT: tuple[str, ...] = ("task-manager", "app-builder")

_DECIDE_ARGS = json.dumps({
    "question": "Which project fits?", "context": "the beta launch",
    "kind": "choice", "options": "alpha\nbeta",
})


def test_the_named_agents_are_the_registry_s() -> None:
    """The lists above name every chat agent of §1.1 but projects-assistant,
    with the runtime the registry declares. A drift fails here, not on a box."""
    routes_agent = pytest.importorskip("gateway.routes.agent")
    runtimes = {e["name"]: e.get("agent_runtime", "maf") for e in routes_agent._AGENT_REGISTRY}
    for name in NATIVE:
        assert runtimes.get(name) == "maf", name
    for name in COPILOT:
        assert runtimes.get(name) == "github-copilot", name


# ── 1. Every native agent, S2 done-when 1 to 5, on the chat path ────────────


@pytest.mark.usefixtures("_routed", "_a_tenant")
@pytest.mark.parametrize("name", sorted(NATIVE))
class TestEveryNativeAgent:
    """§11 S4 done-when 1: each named agent passes S2's items 1 to 5."""

    def test_1_a_covered_run_ignores_the_client_model(self, name, monkeypatch) -> None:
        _flags(monkeypatch, name)
        _wire(monkeypatch, _turn_reply("chat"))
        model = ScriptedModel([text_turn("done")])
        logs = _tap(monkeypatch)
        events, _ = drive_native(name, NATIVE[name], monkeypatch, model,
                                 request_model="tier-powerful")
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        ignored = [r for r in logs if r.get("event") == "ai_route.model_ignored"]
        assert ignored and ignored[0]["model"] == "tier-powerful"
        assert ignored[0]["agent"] == name

    def test_2_a_chat_turn_stays_on_the_default(self, name, monkeypatch) -> None:
        _flags(monkeypatch, name)
        wire = _wire(monkeypatch, _turn_reply("code"))
        model = ScriptedModel([text_turn("done")])
        events, _ = drive_native(name, NATIVE[name], monkeypatch, model, message="hi")
        _ok(events)
        assert wire.requests == []
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        assert [(r["tier"], r["reason"]) for r in _routes(events)] == [
            ("tier-balanced", "default")]

    def test_3_a_code_turn_sends_every_main_request_to_powerful(
        self, name, monkeypatch,
    ) -> None:
        _flags(monkeypatch, name)
        wire = _wire(monkeypatch, _turn_reply("code"))
        model = ScriptedModel([tool_turn("decide", _DECIDE_ARGS), text_turn("done")])
        events, _ = drive_native(name, NATIVE[name], monkeypatch, model, message=SECRET)
        _ok(events)
        assert _is_turn_question(wire.bodies[0]) and wire.bodies[0]["model"] == "tier-fast"
        assert [b["model"] for b in model.bodies] == ["tier-powerful", "tier-powerful"]
        # The System-1 `decide` the agent holds stays on tier-fast.
        assert [b["model"] for b in wire.bodies] == ["tier-fast", "tier-fast"]

    def test_4_a_hinted_tool_raises_the_next_request_only(self, name, monkeypatch) -> None:
        """The hint middleware is on this agent's run. ``decide`` is the one
        tool every covered agent holds, so the test names it as a hint."""
        _flags(monkeypatch, name)
        _wire(monkeypatch, _turn_reply("chat"))
        monkeypatch.setattr(tier_policy, "TOOL_HINTS", {"decide": "analysis"})
        model = ScriptedModel([
            tool_turn("decide", _DECIDE_ARGS, call_id="call_a"),
            text_turn("done"),
        ])
        events, _ = drive_native(name, NATIVE[name], monkeypatch, model, message="hi")
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-balanced", "tier-powerful"]
        assert [r["reason"] for r in _routes(events)] == ["default", "tool_hint"]

    def test_5_max_sends_every_main_request_to_powerful_and_decide_stays_fast(
        self, name, monkeypatch,
    ) -> None:
        _flags(monkeypatch, name)
        wire = _wire(monkeypatch, _turn_reply("chat"))
        model = ScriptedModel([tool_turn("decide", _DECIDE_ARGS), text_turn("done")])
        events, _ = drive_native(name, NATIVE[name], monkeypatch, model,
                                 think_mode="max", message=SECRET)
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-powerful", "tier-powerful"]
        assert len(wire.requests) == 1 and not _is_turn_question(wire.bodies[0])
        assert wire.bodies[0]["model"] == "tier-fast"


# ── 2. The Copilot SDK agents: one tier for the whole run ───────────────────


def _loaded(agent: Any, name: str, tmp_path) -> Any:
    class _Loaded:
        agent_dir = tmp_path
        agent_name = name
        config: ClassVar[dict[str, Any]] = {"model_tier": "tier-balanced"}

        def build_agents(self) -> list[Any]:
            return [agent]

    class _Ctx:
        def __enter__(self) -> Any:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    return _Ctx()


def _stream_copilot(monkeypatch, tmp_path, name: str, *, message: str,
                    think_mode: str = "auto") -> tuple[_CopilotShaped, list[dict]]:
    executor = pytest.importorskip("orchestrator.executor")
    routes_agent = pytest.importorskip("gateway.routes.agent")
    agent = _CopilotShaped()
    agent.name = name
    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _loaded(agent, name, tmp_path))
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])

    async def _collect() -> list[str]:
        return [line async for line in executor.run_agent_stream(
            name, {"message": message}, run_id=f"run-{name}",
            thread_id=f"thread-{name}", model="tier-fast", think_mode=think_mode,
        )]

    return agent, parse_frames(asyncio.run(_collect()))


@pytest.mark.usefixtures("_routed", "_a_tenant")
@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
@pytest.mark.parametrize("name", COPILOT)
class TestEveryCopilotAgent:
    """Items 1, 2, 3 and 5, at run level. A Copilot SDK agent makes its
    requests out of reach of a MAF middleware, so item 4 cannot hold (§4.5)."""

    def test_1_and_2_a_chat_turn_ignores_the_client_model(
        self, name, monkeypatch, tmp_path,
    ) -> None:
        _flags(monkeypatch, name)
        wire = _wire(monkeypatch, _turn_reply("code"))
        logs = _tap(monkeypatch)
        agent, events = _stream_copilot(monkeypatch, tmp_path, name, message="hi")
        assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
        assert wire.requests == []
        assert set(agent.models) == {"tier-balanced"}
        assert [r for r in logs if r.get("event") == "ai_route.model_ignored"]

    def test_3_a_code_turn_puts_the_whole_run_on_powerful(
        self, name, monkeypatch, tmp_path,
    ) -> None:
        _flags(monkeypatch, name)
        _wire(monkeypatch, _turn_reply("code"))
        agent, events = _stream_copilot(monkeypatch, tmp_path, name, message=SECRET)
        assert set(agent.models) == {"tier-powerful"}
        assert [(r["tier"], r["kind"]) for r in _routes(events)] == [("tier-powerful", "code")]

    def test_5_max_puts_the_whole_run_on_powerful(self, name, monkeypatch, tmp_path) -> None:
        _flags(monkeypatch, name)
        wire = _wire(monkeypatch, _turn_reply("chat"))
        agent, _ = _stream_copilot(monkeypatch, tmp_path, name, message=SECRET,
                                   think_mode="max")
        assert wire.requests == []
        assert set(agent.models) == {"tier-powerful"}


# ── 3. The batch path (run_agent) ────────────────────────────────────────────


class JsonModel:
    """An ``httpx.MockTransport`` handler for a NON-streamed run.

    ``Agent.run`` with no stream asks for one JSON body per request. Each
    turn is a text answer or one tool call. Records each request body.
    """

    def __init__(self, turns: list[dict[str, Any]],
                 on_request: Callable[[int], None] | None = None) -> None:
        self.turns = turns
        self.on_request = on_request
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        index = len(self.bodies)
        self.bodies.append(body)
        if self.on_request is not None:
            self.on_request(index)
        turn = self.turns[min(index, len(self.turns) - 1)]
        if "tool" in turn:
            message: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{index}", "type": "function",
                "function": {"name": turn["tool"], "arguments": turn.get("args", "{}")},
            }]}
            finish = "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": turn["text"]}, "stop"
        return httpx.Response(200, json={
            "id": f"chatcmpl-{index}", "object": "chat.completion", "created": 0,
            "model": "probe",
            "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


def _patch_native_load(monkeypatch, name: str, rel_dir: str, model: Any) -> None:
    """``load_agent`` gives the REAL agent of *rel_dir*, on *model*'s wire."""
    executor = pytest.importorskip("orchestrator.executor")
    routes_agent = pytest.importorskip("gateway.routes.agent")
    mod = load_agent_module(rel_dir)
    agent_dir = REPO_ROOT / rel_dir
    config = json.loads((agent_dir / "config.json").read_text(encoding="utf-8"))

    def _build() -> list[Any]:
        agents = mod.build_agents()
        oc = agents[0].client.client
        oc._client = httpx.AsyncClient(
            transport=httpx.MockTransport(model), event_hooks=oc._client.event_hooks,
        )
        return agents

    class _Loaded:
        def __init__(self) -> None:
            self.agent_dir = agent_dir
            self.agent_name = name
            self.config = config

        def build_agents(self) -> list[Any]:
            return _build()

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])


def _batch(name: str, *, message: str, model: str | None = None,
           payload: dict[str, Any] | None = None) -> dict[str, Any]:
    executor = pytest.importorskip("orchestrator.executor")
    return asyncio.run(executor.run_agent(
        name, {"message": message, **(payload or {})}, run_id=f"run-batch-{name}",
        model=model, organization_id="org-native-maf-probe",
    ))


BATCH_AGENT = "crm-assistant"


@pytest.mark.usefixtures("_routed", "_a_tenant")
class TestTheBatchPath:
    """S2 build notes: "The batch path (`run_agent`) ... attach[es] no policy.
    S4 owns both." This is that half."""

    def test_a_covered_batch_run_runs_its_own_policy(self, monkeypatch) -> None:
        _flags(monkeypatch, BATCH_AGENT)
        wire = _wire(monkeypatch, _turn_reply("code"))
        model = JsonModel([{"tool": "decide", "args": _DECIDE_ARGS}, {"text": "done"}])
        _patch_native_load(monkeypatch, BATCH_AGENT, NATIVE[BATCH_AGENT], model)
        logs = _tap(monkeypatch)
        result = _batch(BATCH_AGENT, message=SECRET, model="tier-fast")
        assert result["answer"] == "done"
        assert [b["model"] for b in model.bodies] == ["tier-powerful", "tier-powerful"]
        assert _is_turn_question(wire.bodies[0])
        ignored = [r for r in logs if r.get("event") == "ai_route.model_ignored"]
        assert ignored and ignored[0]["model"] == "tier-fast"
        chosen = [r for r in logs if r.get("event") == "ai_route.chosen"]
        assert len(chosen) == len(model.bodies)

    def test_a_covered_batch_run_takes_the_payload_effort(self, monkeypatch) -> None:
        _flags(monkeypatch, BATCH_AGENT)
        wire = _wire(monkeypatch, _turn_reply("chat"))
        model = JsonModel([{"text": "done"}])
        _patch_native_load(monkeypatch, BATCH_AGENT, NATIVE[BATCH_AGENT], model)
        _batch(BATCH_AGENT, message=SECRET, payload={"think_mode": "max"})
        assert wire.requests == []
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]

    def test_with_the_flag_unset_a_batch_run_sends_the_same_bytes(self, monkeypatch) -> None:
        """Ship dark. The caller's model reaches the wire, as on main, and the
        policy is never asked."""
        def boom(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("the policy ran for an agent the flag does not cover")

        monkeypatch.setattr(tier_policy, "turn_kind", boom)
        monkeypatch.setattr(tier_policy, "TierPolicyProvider", boom)
        runs: list[str] = []
        for flag in (None, "projects-assistant"):
            _flags(monkeypatch, flag)
            # The run's OWN bound context, read while the model works. An
            # uncovered run binds no `think_mode`, as on main.
            bound: list[bool] = []
            model = JsonModel(
                [{"tool": "decide", "args": _DECIDE_ARGS}, {"text": "done"}],
                on_request=lambda _i, _b=bound: _b.append("think_mode" in artifact_context()),
            )
            _patch_native_load(monkeypatch, BATCH_AGENT, NATIVE[BATCH_AGENT], model)
            logs = _tap(monkeypatch)
            _batch(BATCH_AGENT, message=SECRET, model="tier-powerful")
            assert not [r for r in logs if str(r.get("event", "")).startswith("ai_route.")]
            assert bound == [False, False], bound
            runs.append(json.dumps(model.bodies, sort_keys=True))
        assert runs[0] == runs[1]
        assert [b["model"] for b in json.loads(runs[0])] == ["tier-powerful", "tier-powerful"]


    def test_a_self_anneal_retry_keeps_the_policy(self, monkeypatch) -> None:
        """Review P3 of S4b. A covered batch run hits a rate limit, and the
        self-anneal retries it. The retry keeps the run's policy, so a Max
        run stays on tier-powerful. Max asks no turn kind, so no cold
        System-1 timeout can make this test pass or fail by chance."""
        import acb_skills.integrations as integrations_mod
        import acb_skills.loader as loader_mod

        executor = pytest.importorskip("orchestrator.executor")
        _flags(monkeypatch, BATCH_AGENT)
        wire = _wire(monkeypatch, _turn_reply("chat"))

        class _LimitedOnce(JsonModel):
            def __call__(self, request: httpx.Request) -> httpx.Response:
                if not self.bodies:
                    self.bodies.append(json.loads(request.content or b"{}"))
                    # A 400, so the SDK does not retry it itself. Its text
                    # reads as a rate limit to the self-anneal.
                    return httpx.Response(400, json={"error": {"message": "rate limit"}})
                return super().__call__(request)

        model = _LimitedOnce([{"text": "done"}])
        _patch_native_load(monkeypatch, BATCH_AGENT, NATIVE[BATCH_AGENT], model)
        # The retry loads through the loader's own names, not the executor's.
        monkeypatch.setattr(loader_mod, "load_agent", executor.load_agent)
        monkeypatch.setattr(integrations_mod, "build_integrations", executor.build_integrations)

        async def _no_wait(_s: float) -> None:
            return None

        monkeypatch.setattr(executor.asyncio, "sleep", _no_wait)
        logs = _tap(monkeypatch)
        result = _batch(BATCH_AGENT, message=SECRET, model="tier-fast",
                        payload={"think_mode": "max"})
        assert result["answer"] == "done"
        assert any(r.get("event") == "self_anneal.retry_success" for r in logs)
        assert [b["model"] for b in model.bodies] == ["tier-powerful", "tier-powerful"]
        assert wire.requests == []
        chosen = [r["request"] for r in logs if r.get("event") == "ai_route.chosen"]
        assert chosen == [1, 1], "the retry counts its requests from 1 again"

    def test_a_covered_copilot_batch_run_keeps_its_agent_md_default(
        self, monkeypatch, tmp_path,
    ) -> None:
        """Review P3 of S4b. The batch path reads the ``.agent.md`` model as the
        default of a Copilot SDK agent, as the stream path does."""
        executor = pytest.importorskip("orchestrator.executor")
        routes_agent = pytest.importorskip("gateway.routes.agent")
        _flags(monkeypatch, "task-manager")
        _wire(monkeypatch, _turn_reply("chat"))
        agent = _CopilotShaped()
        monkeypatch.setattr(executor, "load_agent",
                            lambda *a, **k: _loaded(agent, "task-manager", tmp_path))
        monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
        monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])

        class _Spec:
            model = "tier-fast"

        monkeypatch.setattr(executor, "_apply_agent_md_overrides", lambda *a, **k: _Spec())
        _batch("task-manager", message="hi", model="tier-powerful")
        assert set(agent.models) == {"tier-fast"}


# ── 4. Sub-agents (§4.5, the last bullet) ────────────────────────────────────


def _delegate(name: str, *, parent_model: str, think_mode: str = "auto",
              message: str = SECRET) -> tuple[str, list[dict[str, Any]]]:
    """Run *name* as a sub-agent of a projects-assistant run, as call_agent does.

    Returns the text and the events the parent's queue received.
    """
    executor = pytest.importorskip("orchestrator.executor")
    bind_artifact_context(agent_name="projects-assistant", run_id="run-parent",
                          session_id="thread-parent", think_mode=think_mode,
                          member="member@example.com")

    async def _go() -> tuple[str, list[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        token = executor._active_run_queue.set(queue)
        try:
            text = await executor._run_sub_agent_streaming(
                name, message, "run-sub", event_queue=queue, model=parent_model,
            )
        finally:
            executor._active_run_queue.reset(token)
        got: list[dict[str, Any]] = []
        while not queue.empty():
            item = queue.get_nowait()
            if item is not None:
                got.append(item)
        return text, got

    return asyncio.run(_go())


SUB = "whatsapp-assistant"


@pytest.mark.usefixtures("_routed", "_a_tenant")
@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
class TestSubAgents:
    def test_a_covered_maf_sub_agent_ignores_the_parent_tier(self, monkeypatch) -> None:
        """The parent published tier-powerful. A covered sub-agent asks its
        own turn kind and runs on its own default for a chat turn."""
        _flags(monkeypatch, SUB)
        wire = _wire(monkeypatch, _turn_reply("chat"))
        model = JsonModel([{"text": "done"}])
        _patch_native_load(monkeypatch, SUB, NATIVE[SUB], model)
        text, _ = _delegate(SUB, parent_model="tier-powerful")
        assert text == "done"
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        assert len(wire.requests) == 1 and _is_turn_question(wire.bodies[0])

    def test_a_covered_sub_agent_takes_the_parent_effort(self, monkeypatch) -> None:
        _flags(monkeypatch, SUB)
        _wire(monkeypatch, _turn_reply("chat"))
        model = JsonModel([{"text": "done"}])
        _patch_native_load(monkeypatch, SUB, NATIVE[SUB], model)
        _delegate(SUB, parent_model="tier-balanced", think_mode="max")
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]
        assert [b.get("reasoning_effort") for b in model.bodies] == [None]

    def test_a_sub_agent_emits_no_route_event(self, monkeypatch) -> None:
        _flags(monkeypatch, SUB)
        _wire(monkeypatch, _turn_reply("code"))
        model = JsonModel([{"text": "done"}])
        _patch_native_load(monkeypatch, SUB, NATIVE[SUB], model)
        _, events = _delegate(SUB, parent_model="tier-balanced")
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]
        assert not _routes(events), events

    def test_an_uncovered_sub_agent_still_inherits(self, monkeypatch) -> None:
        """Ship dark. The flag names the PARENT only, so the sub-agent runs
        on the parent's published tier, as on main."""
        _flags(monkeypatch, "projects-assistant")
        wire = _wire(monkeypatch, _turn_reply("chat"))
        model = JsonModel([{"text": "done"}])
        _patch_native_load(monkeypatch, SUB, NATIVE[SUB], model)
        _delegate(SUB, parent_model="tier-powerful")
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]
        assert wire.requests == []

    def test_a_covered_copilot_sub_agent_takes_its_own_tier(
        self, monkeypatch, tmp_path,
    ) -> None:
        executor = pytest.importorskip("orchestrator.executor")
        routes_agent = pytest.importorskip("gateway.routes.agent")
        for flag, parent, kind, expected in (
            ("task-manager", "tier-fast", "code", "tier-powerful"),
            ("task-manager", "tier-powerful", "chat", "tier-balanced"),
            (None, "tier-fast", "code", "tier-fast"),
        ):
            _flags(monkeypatch, flag)
            _wire(monkeypatch, _turn_reply(kind))
            agent = _CopilotShaped()
            monkeypatch.setattr(executor, "load_agent",
                                lambda *a, _ag=agent, **k: _loaded(_ag, "task-manager", tmp_path))
            monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
            monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
            _, events = _delegate("task-manager", parent_model=parent)
            assert set(agent.models) == {expected}, (flag, parent, kind, agent.models)
            assert not _routes(events)
