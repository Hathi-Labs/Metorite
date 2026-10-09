"""The AI tier policy — WS-45 (D90). The flag, the table and the run policy.

Spec: ``project-docs/specs/ai_tier_routing.md`` §4, §5, §9, §10 and §11 S2.

``AI_TIER_ROUTING`` names the agents that the policy covers: a comma list,
or ``*``. Empty means OFF. ``tier_policy.tier_routing_on`` is the ONE reader,
and it fails closed (S1). S2 adds the table of §4.1, the tool hints of §4.4,
the effort mapping of §5, the turn-kind question of §4.3 and
``TierPolicyMiddleware`` (§4.5).

The run tests drive the REAL projects-assistant through the REAL
``run_agent_stream``, with a real ``OpenAIChatCompletionClient``. Only the
HTTP transports are scripts: :class:`ScriptedModel` under the main agent, and
:class:`Wire` under the System-1 client. So a body a test reads is the body
the gateway's ``/v1`` would receive.

Hermetic: no SQL runs on this path, so R8 binds nothing here.

Mutations this file catches (R7), each run red before the change:

* ``tier_routing_on`` returns ``True`` on a settings fault
  -> ``test_a_broken_settings_read_reads_as_off``;
* ``*`` stops covering every agent -> ``test_a_star_covers_every_agent``;
* an empty name reads as covered under ``*`` -> ``test_no_name_is_never_covered``;
* a threshold moves -> ``test_the_thresholds_are_the_owners``;
* a kind maps off the ladder, or code goes to ``tier-code`` ->
  ``test_every_kind_maps_to_a_tier_on_the_ladder``;
* a hint names no real tool -> ``test_every_hint_names_a_real_tool``;
* a ``chat`` turn moves off the default, or a turn moves below it ->
  ``TestChoose``;
* a hint moves a request mid-turn again (owner, 2026-10-09: one model per
  turn) -> ``TestChoose::test_no_hint_moves_the_turn``,
  ``test_the_run_policy_keeps_one_tier_for_the_turn`` and
  ``test_a_hinted_tool_keeps_the_turn_tier_and_is_logged``;
* the hint stops being logged -> ``test_a_hint_is_logged_as_ignored`` and
  ``test_a_hinted_tool_keeps_the_turn_tier_and_is_logged``;
* Thinking moves the tier of a ``chat`` turn ->
  ``test_thinking_moves_no_tier``;
* Max stops sending every main request to ``tier-powerful``, or moves a
  System-1 call -> ``test_max_sends_every_main_request_to_powerful_and_decide_stays_fast``;
* the turn-kind question goes out for a short message, waits past 1.5 s, or
  reads a low confidence as a kind -> ``TestTurnKind``;
* a covered run honours the client's model -> ``test_a_covered_run_ignores_the_client_model``;
* the policy reaches a run that the flag does not cover ->
  ``test_with_the_flag_unset_the_run_sends_the_same_bytes``;
* a Copilot agent loses the turn's tier -> ``test_a_copilot_agent_gets_the_turn_tier_for_the_whole_run``;
* a log line or an event holds tenant text -> ``test_no_route_line_or_event_holds_tenant_text``.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
import openai
import pytest
from acb_common import bind_run_context, clear_run_context
from acb_common.settings import get_settings
from acb_skills import tier_policy
from acb_skills.write_artifact import bind_artifact_context

from tests.unit._native_maf_harness import (
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    text_turn,
    tool_turn,
)

PA = "projects-assistant"
PA_DIR = "apps/agents/agent-projects"


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _flag(monkeypatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("AI_TIER_ROUTING", raising=False)
    else:
        monkeypatch.setenv("AI_TIER_ROUTING", value)
    get_settings.cache_clear()


def test_the_flag_ships_off(monkeypatch) -> None:
    _flag(monkeypatch, None)
    assert get_settings().ai_tier_routing == ""
    assert tier_policy.covered_agents() == frozenset()
    for name in ("projects-assistant", "email-assistant", "orchestrator"):
        assert tier_policy.tier_routing_on(name) is False


def test_a_list_covers_exactly_the_agents_it_names(monkeypatch) -> None:
    _flag(monkeypatch, " projects-assistant , crm-assistant ,, ")
    assert tier_policy.covered_agents() == frozenset({"projects-assistant", "crm-assistant"})
    assert tier_policy.tier_routing_on("projects-assistant") is True
    assert tier_policy.tier_routing_on("crm-assistant") is True
    assert tier_policy.tier_routing_on("email-assistant") is False
    # A name is matched whole, never as a prefix.
    assert tier_policy.tier_routing_on("projects") is False


def test_a_star_covers_every_agent(monkeypatch) -> None:
    _flag(monkeypatch, "*")
    for name in ("projects-assistant", "email-assistant", "task-manager"):
        assert tier_policy.tier_routing_on(name) is True


def test_no_name_is_never_covered(monkeypatch) -> None:
    _flag(monkeypatch, "*")
    assert tier_policy.tier_routing_on(None) is False
    assert tier_policy.tier_routing_on("  ") is False


def test_a_broken_settings_read_reads_as_off(monkeypatch) -> None:
    """The flag fails closed (§10). A settings fault covers no agent."""
    _flag(monkeypatch, "*")
    import acb_common

    def boom():
        raise RuntimeError("settings are broken")

    monkeypatch.setattr(acb_common, "get_settings", boom)
    assert tier_policy.covered_agents() == frozenset()
    assert tier_policy.tier_routing_on("projects-assistant") is False


def test_the_thresholds_are_the_owners() -> None:
    """§5 and Q7: 0.70, 0.80 and 0.90 by effort, and an unknown mode is Auto."""
    assert tier_policy.SYSTEM_ONE_THRESHOLDS == {"auto": 0.70, "thinking": 0.80, "max": 0.90}
    assert tier_policy.system_one_threshold("auto") == 0.70
    assert tier_policy.system_one_threshold("Thinking") == 0.80
    assert tier_policy.system_one_threshold("max") == 0.90
    assert tier_policy.system_one_threshold(None) == 0.70
    assert tier_policy.system_one_threshold("turbo") == 0.70


def test_system_one_runs_on_the_fast_tier() -> None:
    """§5: a decision stays on ``tier-fast`` in every mode."""
    assert tier_policy.SYSTEM_ONE_TIER == "tier-fast"


# ═════════════════════════════════════════════════════════════════════════════
# S2 — the table, the effort mapping, the turn kind and the run policy
# ═════════════════════════════════════════════════════════════════════════════

#: Tenant text. It must never reach a log line or an ``ai.route`` event.
SECRET = "Refactor the invoice exporter for Acme Corp and fix the overdue March batch job now"


# ── 1. The table of record (§4.1, §4.4) ─────────────────────────────────────


def test_every_kind_maps_to_a_tier_on_the_ladder() -> None:
    """§4.1 and §4.2 rule 3: the policy chooses only among three tiers, and
    code goes to ``tier-powerful`` (Q2). ``chat`` keeps the agent's default."""
    assert tier_policy.LADDER == ("tier-fast", "tier-balanced", "tier-powerful")
    assert set(tier_policy.KIND_TIERS) == set(tier_policy.TURN_KINDS)
    assert tier_policy.KIND_TIERS["chat"] is None
    for kind in ("code", "plan", "analysis"):
        assert tier_policy.KIND_TIERS[kind] == "tier-powerful", kind
    assert tier_policy.MAX_TIER == "tier-powerful"
    assert tier_policy.EFFORTS == ("auto", "thinking", "max")  # Q3: no "Fast"


def _tool_registry_names() -> set[str]:
    """Every tool name that a registry of the platform holds."""
    import skill_projects
    from acb_skills import sandbox_tools
    from orchestrator._tool_injection import _collect_injectable_platform_tools

    names = set(skill_projects.__all__)
    for tool in _collect_injectable_platform_tools():
        names.add(getattr(tool, "name", None) or getattr(tool, "__name__", ""))
    names.add(sandbox_tools.run_command.__name__)
    names.add(_narrow_tool_name())
    return names


def _narrow_tool_name() -> str:
    """The name that ``make_narrow_tool`` gives the WS-48 tool. Each data
    agent builds its own tool, so no registry above holds it."""
    from acb_skills import narrowing

    class _Probe:
        name = "probe"
        filter_keys: frozenset[str] = frozenset()

        async def candidates(self, query: str, filters: Any) -> Any:  # pragma: no cover
            return narrowing.Narrowed(candidates=[], total=0)

        async def read(self, ids: Any) -> list[Any]:  # pragma: no cover
            return []

    return narrowing.make_narrow_tool(_Probe()).__name__


def test_every_hint_names_a_real_tool() -> None:
    """§4.4 and §10: a hint that names no registered tool fails here."""
    pytest.importorskip("orchestrator._tool_injection")
    registry = _tool_registry_names()
    missing = sorted(set(tier_policy.TOOL_HINTS) - registry)
    assert not missing, f"TOOL_HINTS names no real tool: {missing}"
    for name, kind in tier_policy.TOOL_HINTS.items():
        assert kind in tier_policy.TURN_KINDS and kind != "chat", name


class TestChoose:
    """§4.1, §4.2, §4.4 and §5, on the pure function."""

    def test_a_chat_turn_stays_on_the_default(self) -> None:
        for default in ("tier-fast", "tier-balanced", "tier-powerful", "tier-code"):
            c = tier_policy.choose(default=default, kind="chat", effort="auto")
            assert (c.tier, c.reason) == (default, "default")

    def test_code_plan_and_analysis_go_to_powerful(self) -> None:
        for kind in ("code", "plan", "analysis"):
            c = tier_policy.choose(default="tier-balanced", kind=kind, effort="auto")
            assert (c.tier, c.kind, c.reason) == ("tier-powerful", kind, "turn_kind")

    def test_the_policy_never_moves_below_the_default(self) -> None:
        c = tier_policy.choose(default="tier-powerful", kind="code", effort="auto")
        assert (c.tier, c.reason) == ("tier-powerful", "default")
        c = tier_policy.choose(default="tier-powerful", kind="chat", effort="thinking")
        assert c.tier == "tier-powerful"

    def test_an_unknown_kind_reads_as_chat(self) -> None:
        c = tier_policy.choose(default="tier-balanced", kind="poetry", effort="auto")
        assert (c.tier, c.kind) == ("tier-balanced", "chat")

    def test_no_hint_moves_the_turn(self) -> None:
        """Owner, 2026-10-09: ``choose`` takes no hint. The turn's tier is
        the kind's tier, and ``tool_hint`` is no longer a reason."""
        import inspect

        assert "hint" not in inspect.signature(tier_policy.choose).parameters
        assert "tool_hint" not in tier_policy.REASONS

    def test_max_sends_every_main_request_to_powerful(self) -> None:
        for kind in tier_policy.TURN_KINDS:
            c = tier_policy.choose(default="tier-fast", kind=kind, effort="max")
            assert (c.tier, c.reason) == ("tier-powerful", "effort")

    def test_thinking_moves_no_tier(self) -> None:
        """Owner, 2026-10-09. D90 gave Thinking one rung up after a hinted
        tool. Hints no longer move a request, so Thinking keeps the kind's
        tier for the whole turn. It still sets the reasoning effort."""
        for kind in tier_policy.TURN_KINDS:
            auto = tier_policy.choose(default="tier-balanced", kind=kind, effort="auto")
            think = tier_policy.choose(default="tier-balanced", kind=kind, effort="thinking")
            assert (think.tier, think.reason) == (auto.tier, auto.reason), kind

    def test_the_run_policy_keeps_one_tier_for_the_turn(self) -> None:
        """Owner, 2026-10-09: one model per turn. A hinted tool does not
        raise the next request."""
        policy = tier_policy.RunTierPolicy(
            agent=PA, run_id="r", default="tier-balanced", kind="chat", effort="auto",
        )
        assert policy.next_choice().tier == "tier-balanced"
        policy.note_tool("find_conflicts")
        assert policy.next_choice().tier == "tier-balanced"
        policy.note_tool("run_command")
        assert policy.next_choice().tier == "tier-balanced"
        planned = tier_policy.RunTierPolicy(
            agent=PA, run_id="r", default="tier-balanced", kind="plan", effort="auto",
        )
        assert [planned.next_choice().tier for _ in range(3)] == ["tier-powerful"] * 3

    def test_a_hint_is_logged_as_ignored(self, monkeypatch) -> None:
        """``ai_route.hint_ignored`` names the tool, the hint's kind, the
        turn's tier and the tier D90 would have chosen. No other tool logs."""
        logs = _tap(monkeypatch)
        policy = tier_policy.RunTierPolicy(
            agent=PA, run_id="r-hint", default="tier-balanced", kind="chat", effort="auto",
        )
        policy.note_tool("vocabulary")
        policy.note_tool("find_conflicts")
        lines = [r for r in logs if r.get("event") == "ai_route.hint_ignored"]
        assert len(lines) == 1
        line = lines[0]
        assert (line["tool"], line["hint"], line["tier"], line["would_tier"]) == (
            "find_conflicts", "analysis", "tier-balanced", "tier-powerful")
        assert (line["agent"], line["run_id"], line["kind"]) == (PA, "r-hint", "chat")


# ── 2. The System-1 wire ─────────────────────────────────────────────────────


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "chatcmpl-s2", "object": "chat.completion", "created": 0,
        "model": "probe",
        "choices": [{
            "index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": content},
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


def _answers(*rows: dict[str, Any]) -> httpx.Response:
    return _completion(json.dumps({"answers": list(rows)}))


class Wire:
    """The HTTP transport under every System-1 client. Records each request."""

    def __init__(self, reply: Callable[[dict[str, Any]], Any]) -> None:
        self.reply = reply
        self.requests: list[httpx.Request] = []

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content or b"{}") for r in self.requests]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        out = self.reply(json.loads(request.content or b"{}"))
        if asyncio.iscoroutine(out):
            out = await out
        return out


def _wire(monkeypatch, reply: Callable[[dict[str, Any]], Any]) -> Wire:
    wire = Wire(reply)

    def factory(**kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(wire), **kw)

    monkeypatch.setattr(openai, "DefaultAsyncHttpxClient", factory)
    return wire


def _is_turn_question(body: dict[str, Any]) -> bool:
    return '\\"id\\": \\"turn\\"' in json.dumps(body.get("messages") or [])


def _turn_reply(kind: str, confidence: float = 0.95) -> Callable[[dict[str, Any]], Any]:
    def reply(body: dict[str, Any]) -> httpx.Response:
        if _is_turn_question(body):
            return _answers({"id": "turn", "choice": kind, "confidence": confidence,
                             "reason": "the message asks for it"})
        return _answers({"id": "q", "choice": "beta", "confidence": 0.9,
                         "reason": "names beta"})
    return reply


@pytest.fixture
def _routed(monkeypatch):
    """A box that routes through the Router, and a bound projects run."""
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", "1")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret")
    monkeypatch.delenv("AI_TIER_ROUTING", raising=False)
    monkeypatch.setenv("DECIDE_ENABLED", "false")
    get_settings.cache_clear()
    clear_run_context()
    bind_run_context(run_id="run-s2", agent=PA, user="member@example.com", source="chat")
    bind_artifact_context(agent_name=PA, run_id="run-s2", think_mode="auto")
    yield
    clear_run_context()
    bind_artifact_context()
    get_settings.cache_clear()


def _kind(message: str, effort: str = "auto", tools: tuple[str, ...] = ("vocabulary",)):
    return asyncio.run(tier_policy.turn_kind(message, tools, effort))


@pytest.mark.usefixtures("_routed")
class TestTurnKind:
    """§4.3: one ``tier-fast`` question at the start of a turn, or none."""

    def test_a_code_message_is_one_fast_request(self, monkeypatch) -> None:
        wire = _wire(monkeypatch, _turn_reply("code"))
        turn = _kind(SECRET, tools=("run_command", "vocabulary"))
        assert (turn.kind, turn.source) == ("code", "system_one")
        assert isinstance(turn.latency_ms, int)
        assert len(wire.requests) == 1
        body = wire.bodies[0]
        assert body["model"] == "tier-fast"
        assert "tools" not in body
        assert body["response_format"] == {"type": "json_object"}
        assert wire.requests[0].headers["X-CC-Source"] == "system_one"
        assert wire.requests[0].headers["X-CC-Agent"] == PA
        payload = json.loads(body["messages"][-1]["content"])
        # The last member message and the tool names, and no history.
        assert "Refactor the invoice exporter" in payload["context"]
        assert "run_command, vocabulary" in payload["context"]
        assert payload["items"][0]["options"] == ["chat", "code", "plan", "analysis"]

    def test_a_message_under_12_words_sends_no_request(self, monkeypatch) -> None:
        wire = _wire(monkeypatch, _turn_reply("code"))
        eleven = "one two three four five six seven eight nine ten eleven"
        turn = _kind(eleven)
        assert (turn.kind, turn.source, turn.latency_ms) == ("chat", "short", None)
        assert wire.requests == []
        # Twelve words do ask.
        assert _kind(eleven + " twelve").kind == "code"
        assert len(wire.requests) == 1

    def test_a_low_confidence_reads_as_chat(self, monkeypatch) -> None:
        """Q7: 0.75 passes Auto (0.70) and fails Thinking (0.80)."""
        _wire(monkeypatch, _turn_reply("plan", confidence=0.75))
        assert _kind(SECRET, effort="auto").kind == "plan"
        turn = _kind(SECRET, effort="thinking")
        assert (turn.kind, turn.source) == ("chat", "unsure")

    def test_a_timeout_reads_as_chat(self, monkeypatch) -> None:
        async def slow(_body: dict[str, Any]) -> httpx.Response:
            await asyncio.sleep(1.0)
            return _turn_reply("code")(_body)

        _wire(monkeypatch, slow)
        monkeypatch.setattr(tier_policy, "TURN_KIND_TIMEOUT_S", 0.1)
        turn = _kind(SECRET)
        assert (turn.kind, turn.source) == ("chat", "timeout")
        assert turn.latency_ms is not None and turn.latency_ms < 900

    def test_the_wait_is_one_and_a_half_seconds(self) -> None:
        assert tier_policy.TURN_KIND_TIMEOUT_S == 1.5
        assert tier_policy.SHORT_MESSAGE_WORDS == 12

    def test_max_sends_no_question(self, monkeypatch) -> None:
        wire = _wire(monkeypatch, _turn_reply("code"))
        turn = _kind(SECRET, effort="max")
        assert (turn.kind, turn.source) == ("chat", "max")
        assert wire.requests == []

    def test_a_box_that_does_not_route_sends_nothing(self, monkeypatch) -> None:
        wire = _wire(monkeypatch, _turn_reply("code"))
        monkeypatch.setenv("ROUTER_SERVING_ENABLED", "0")
        get_settings.cache_clear()
        turn = _kind(SECRET)
        assert (turn.kind, turn.source) == ("chat", "unavailable")
        assert wire.requests == []

    def test_a_choice_outside_the_kinds_reads_as_chat(self, monkeypatch) -> None:
        _wire(monkeypatch, lambda _b: _answers(
            {"id": "turn", "choice": "poetry", "confidence": 0.99, "reason": "x"},
        ))
        assert _kind(SECRET).kind == "chat"


# ── 3. A real projects-assistant run, through the REAL executor ─────────────


class _LogTap:
    """Stands in for a module's structlog logger, and records each line.

    The platform caches a logger on first use. When a later test calls
    ``structlog.configure`` again, the cached logger keeps the old
    processors, and ``structlog.testing.capture_logs`` sees nothing from it.
    The full suite does exactly that (CI, PR #675). So this test swaps the
    logger itself.
    """

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def __getattr__(self, level: str) -> Callable[..., None]:
        def _log(event: str, *_a: Any, **kw: Any) -> None:
            self.records.append({"event": event, "log_level": level, **kw})
        return _log


def _tap(monkeypatch) -> list[dict[str, Any]]:
    """Record every line of the executor and of the tier policy."""
    executor = pytest.importorskip("orchestrator.executor")
    tap = _LogTap()
    monkeypatch.setattr(executor, "_log", tap)
    monkeypatch.setattr(tier_policy, "_log", tap)
    return tap.records


def _flags(monkeypatch, flag: str | None) -> None:
    if flag is None:
        monkeypatch.delenv("AI_TIER_ROUTING", raising=False)
    else:
        monkeypatch.setenv("AI_TIER_ROUTING", flag)
    get_settings.cache_clear()
    clear_run_context()
    bind_artifact_context()
    ti = pytest.importorskip("orchestrator._tool_injection")
    ti._build_injected_tools_addendum.cache_clear()


def _routes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e["value"] for e in events
            if e.get("type") == "CUSTOM" and e.get("name") == "ai.route"]


def _ok(events: list[dict[str, Any]]) -> None:
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED"


@pytest.mark.usefixtures("_routed", "_a_tenant")
class TestARealProjectsRun:
    """S2 done-when 1 to 8 on the wire."""

    def test_a_covered_run_ignores_the_client_model(self, monkeypatch) -> None:
        """Done-when 1. The chat's pick does not reach the wire, and the run
        logs ``ai_route.model_ignored`` with the value."""
        _flags(monkeypatch, PA)
        _wire(monkeypatch, _turn_reply("chat"))
        model = ScriptedModel([text_turn("done")])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model,
                                 request_model="tier-powerful")
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]
        ignored = [r for r in logs if r.get("event") == "ai_route.model_ignored"]
        assert ignored and ignored[0]["model"] == "tier-powerful"

    def test_a_chat_turn_stays_on_the_default(self, monkeypatch) -> None:
        """Done-when 2, and done-when 7: "hi" sends no turn-kind request."""
        _flags(monkeypatch, PA)
        wire = _wire(monkeypatch, _turn_reply("code"))
        model = ScriptedModel([tool_turn("vocabulary", "{}"), text_turn("done")])
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message="hi")
        _ok(events)
        assert wire.requests == []
        assert [b["model"] for b in model.bodies] == ["tier-balanced", "tier-balanced"]
        assert [r["tier"] for r in _routes(events)] == ["tier-balanced", "tier-balanced"]

    def test_a_code_turn_sends_every_main_request_to_powerful(self, monkeypatch) -> None:
        """Done-when 3. One ``ai_route.chosen`` line and one ``ai.route`` event
        for each main request."""
        _flags(monkeypatch, PA)
        wire = _wire(monkeypatch, _turn_reply("code"))
        model = ScriptedModel([tool_turn("vocabulary", "{}"), text_turn("done")])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert len(wire.requests) == 1 and wire.bodies[0]["model"] == "tier-fast"
        assert [b["model"] for b in model.bodies] == ["tier-powerful", "tier-powerful"]
        routes = _routes(events)
        assert [(r["tier"], r["kind"], r["reason"]) for r in routes] == [
            ("tier-powerful", "code", "turn_kind")] * 2
        chosen = [r for r in logs if r.get("event") == "ai_route.chosen"]
        assert len(chosen) == len(model.bodies)

    def test_a_hinted_tool_keeps_the_turn_tier_and_is_logged(self, monkeypatch) -> None:
        """Done-when 4, amended by the owner on 2026-10-09 (one model per
        turn). ``find_conflicts`` is hinted, ``vocabulary`` is not. Every
        main request keeps the turn's tier, and the hint is logged once."""
        _flags(monkeypatch, PA)
        _wire(monkeypatch, _turn_reply("chat"))
        model = ScriptedModel([
            tool_turn("find_conflicts", "{}", call_id="call_a"),
            tool_turn("vocabulary", "{}", call_id="call_b"),
            text_turn("done"),
        ])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message="hi")
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-balanced"] * 3
        assert [r["reason"] for r in _routes(events)] == ["default"] * 3
        ignored = [r for r in logs if r.get("event") == "ai_route.hint_ignored"]
        assert [(r["tool"], r["would_tier"]) for r in ignored] == [
            ("find_conflicts", "tier-powerful")]

    def test_max_sends_every_main_request_to_powerful_and_decide_stays_fast(
        self, monkeypatch,
    ) -> None:
        """Done-when 5. Max asks no turn kind. The ``decide`` call is one
        System-1 request on ``tier-fast``."""
        _flags(monkeypatch, PA)
        wire = _wire(monkeypatch, _turn_reply("chat"))
        args = {"question": "Which project fits?", "context": "the beta launch",
                "kind": "choice", "options": "alpha\nbeta"}
        model = ScriptedModel([tool_turn("decide", json.dumps(args)), text_turn("done")])
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, think_mode="max",
                                 message=SECRET)
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-powerful", "tier-powerful"]
        assert [b.get("reasoning_effort") for b in model.bodies] == ["high", "high"]
        assert len(wire.requests) == 1
        assert not _is_turn_question(wire.bodies[0])
        assert wire.bodies[0]["model"] == "tier-fast"

    def test_a_turn_kind_timeout_reads_as_chat(self, monkeypatch) -> None:
        """Done-when 6."""
        _flags(monkeypatch, PA)

        async def slow(_body: dict[str, Any]) -> httpx.Response:
            await asyncio.sleep(1.0)
            return _turn_reply("code")(_body)

        wire = _wire(monkeypatch, slow)
        monkeypatch.setattr(tier_policy, "TURN_KIND_TIMEOUT_S", 0.1)
        model = ScriptedModel([text_turn("done")])
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert len(wire.requests) == 1
        assert [b["model"] for b in model.bodies] == ["tier-balanced"]

    def test_with_the_flag_unset_the_run_sends_the_same_bytes(self, monkeypatch) -> None:
        """Done-when 8. Ship dark. With ``AI_TIER_ROUTING`` unset, a run of
        three requests is byte for byte the run whose flag names another
        agent. The policy is never asked, and no route line or event shows."""
        def boom(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("the policy ran for an agent the flag does not cover")

        monkeypatch.setattr(tier_policy, "turn_kind", boom)
        monkeypatch.setattr(tier_policy, "TierPolicyProvider", boom)
        runs: list[str] = []
        for flag in (None, "crm-assistant"):
            _flags(monkeypatch, flag)
            model = ScriptedModel([
                tool_turn("find_conflicts", "{}", call_id="call_a"),
                tool_turn("vocabulary", "{}", call_id="call_b"),
                text_turn("done"),
            ])
            logs = _tap(monkeypatch)
            events, _ = drive_native(PA, PA_DIR, monkeypatch, model,
                                     message=SECRET, thread_id="thread-dark",
                                     request_model="tier-powerful")
            _ok(events)
            assert not _routes(events)
            assert not [r for r in logs if str(r.get("event", "")).startswith("ai_route.")]
            runs.append(json.dumps(model.bodies, sort_keys=True))
        assert runs[0] == runs[1]
        assert len(json.loads(runs[0])) == 3

    def test_no_route_line_or_event_holds_tenant_text(self, monkeypatch) -> None:
        _flags(monkeypatch, PA)
        _wire(monkeypatch, _turn_reply("analysis"))
        model = ScriptedModel([text_turn("done")])
        logs = _tap(monkeypatch)
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        route_logs = [r for r in logs if str(r.get("event", "")).startswith("ai_route.")]
        assert {r["event"] for r in route_logs} == {"ai_route.turn_kind", "ai_route.chosen"}
        text = json.dumps([route_logs, _routes(events)], default=str)
        for word in ("Acme", "invoice", "Refactor"):
            assert word not in text


# ── 4. A Copilot SDK agent (done-when 9) ─────────────────────────────────────


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[Any] = []


class _CopilotShaped:
    """A Copilot-SDK-shaped agent: it carries ``_default_options``.

    It records the model it holds when the run starts. A streamed run (the
    Copilot path, Tier 1.5) yields one text update.
    """

    def __init__(self) -> None:
        self.name = "task-manager"
        self._default_options: dict[str, Any] = {}
        self._tools: list[Any] = []
        self.models: list[str] = []

    def run(self, *_a: Any, stream: bool = False, **_k: Any) -> Any:
        self.models.append(str(self._default_options.get("model")))
        if stream:
            return self._updates()
        return self._reply()

    async def _reply(self) -> _Resp:
        return _Resp("done")

    async def _updates(self) -> Any:
        from types import SimpleNamespace

        yield SimpleNamespace(
            role="assistant", message_id="m1",
            contents=[SimpleNamespace(type="text", text="done")],
        )

    async def __aenter__(self) -> _CopilotShaped:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


@pytest.mark.usefixtures("_routed", "_a_tenant")
@pytest.mark.filterwarnings("ignore:coroutine .*run.* was never awaited:RuntimeWarning")
def test_a_copilot_agent_gets_the_turn_tier_for_the_whole_run(monkeypatch, tmp_path) -> None:
    """Done-when 9. task-manager cannot switch per request (§4.5). A code
    turn sets ``tier-powerful`` once, for the whole run, over the client's
    pick, and the chat gets ONE ``ai.route`` event."""
    executor = pytest.importorskip("orchestrator.executor")
    routes_agent = pytest.importorskip("gateway.routes.agent")
    from tests.unit._native_maf_harness import parse_frames

    _flags(monkeypatch, "task-manager")
    wire = _wire(monkeypatch, _turn_reply("code"))
    agent = _CopilotShaped()

    class _Loaded:
        agent_dir = tmp_path
        agent_name = "task-manager"
        config: ClassVar[dict[str, Any]] = {"model_tier": "tier-balanced"}

        def build_agents(self) -> list[Any]:
            return [agent]

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])

    async def _collect() -> list[str]:
        return [line async for line in executor.run_agent_stream(
            "task-manager", {"message": SECRET}, run_id="run-tm",
            thread_id="thread-tm", model="tier-fast",
        )]

    events = parse_frames(asyncio.run(_collect()))
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert len(wire.requests) == 1 and _is_turn_question(wire.bodies[0])
    assert agent.models and set(agent.models) == {"tier-powerful"}
    assert [(r["tier"], r["kind"]) for r in _routes(events)] == [("tier-powerful", "code")]


# ── 5. PR #675 review, round 1 ───────────────────────────────────────────────


def test_an_off_ladder_default_is_never_left() -> None:
    """§4.2 rule 2. An admin's ``tier-code`` (or a ``provider/model``) default
    cannot be compared with a rung, so no kind, hint or effort moves it. A
    hinted tool logs and moves nothing (owner, 2026-10-09)."""
    for default in ("tier-code", "deepseek/deepseek-v4-pro"):
        for kind in tier_policy.TURN_KINDS:
            for effort in tier_policy.EFFORTS:
                c = tier_policy.choose(default=default, kind=kind, effort=effort)
                assert c.tier == default, (default, kind, effort, c)
                policy = tier_policy.RunTierPolicy(
                    agent=PA, run_id="r", default=default, kind=kind, effort=effort,
                )
                policy.note_tool("run_command")
                assert policy.next_choice().tier == default, (default, kind, effort)


@pytest.mark.usefixtures("_routed", "_a_tenant")
class TestReviewRoundOne:
    """P1: a Tier 1 fault still falls back. P2: a sub-agent inherits the
    default, never the turn's tier."""

    def test_a_sub_agent_inherits_the_default_not_the_turn_tier(self, monkeypatch) -> None:
        """A code turn puts the parent's requests on ``tier-powerful``. The run
        publishes the agent's default through ``_active_run_model``, so a
        ``call_agent`` fan-out does not run every request on Powerful."""
        executor = pytest.importorskip("orchestrator.executor")
        _flags(monkeypatch, PA)
        _wire(monkeypatch, _turn_reply("code"))
        seen: list[str | None] = []
        model = ScriptedModel(
            [text_turn("done")],
            on_request=lambda _i, _b: seen.append(executor._active_run_model.get()),
        )
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message=SECRET)
        _ok(events)
        assert [b["model"] for b in model.bodies] == ["tier-powerful"]
        assert seen == ["tier-balanced"]

    def test_a_tier1_fault_falls_back_with_one_route_event_per_step(
        self, monkeypatch,
    ) -> None:
        """Tier 1 fails before its first output. The held ``ai.route`` event of
        that attempt never reaches the chat, the run falls back to Tier 2, and
        Tier 2 counts its requests from 1."""
        executor = pytest.importorskip("orchestrator.executor")
        _flags(monkeypatch, PA)
        _wire(monkeypatch, _turn_reply("chat"))

        def fail_first(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("tier 1 down before its first event")

        monkeypatch.setattr(executor, "_translate_update", fail_first)

        class _StreamThenBatch(ScriptedModel):
            """Streams for Tier 1, and answers one JSON body for Tier 2."""

            def __call__(self, request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content or b"{}")
                if body.get("stream"):
                    return super().__call__(request)
                self.bodies.append(body)
                return _completion("done")

        model = _StreamThenBatch([text_turn("done")])
        events, _ = drive_native(PA, PA_DIR, monkeypatch, model, message="hi")
        _ok(events)
        assert len(model.bodies) == 2, "the run never reached the Tier 2 path"
        routes = _routes(events)
        assert [r["request"] for r in routes] == [1], routes
        assert routes[0]["tier"] == "tier-balanced"
