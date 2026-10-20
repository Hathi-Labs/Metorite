"""Drive the REAL ``orchestrator`` run, and record where it delegated.

The harness runs ``orchestrator.executor.run_agent_stream`` for the agent
``orchestrator``, as the chat route runs it for the one assistant (D99). The
orchestrator and each specialist it calls load from their REAL factories,
named by the REAL registry (``gateway.routes.agent._AGENT_REGISTRY``). The
delegation is the real one: ``call_agent`` or the specialist tool of
``orchestrator/agents.py``, then ``executor._run_sub_agent_streaming``.

What the harness replaces, and nothing more:

* The loader. ``executor.load_agent`` imports ``agents.py`` by file path from
  a copy of the agent dir, as the Dynamic Agent Loader does.
* The dynamic registry and the skill toggles, which read the database. Both
  answer empty, so the static registry is the catalog.
* The state root (``agents_clone_dir``), and the scope (``maf_coding_scope``
  is empty, so no run is covered).
* The CRM mode read of run assembly (``acb_skills.crm_tools.crm_mode``). It
  answers ``native``, so the CRM writes stay, as in an organization whose CRM
  is its own. It reads the gateway, which a hermetic run does not have.
* The Custom Apps granted to an agent (``app_tools._granted_live_apps``).
  It answers none. It reads the database.
* The Redis relay. ``executor._push_sse_to_stream`` records each line with
  its thread, in :attr:`RoutingHarness.relayed`.
* Scripted (``models`` given): the model of each agent. :class:`ReplyModel`
  replays turns under the real client. Every other host HTTP send raises,
  and :attr:`RoutingHarness.sent` records it.
* Live (``live=True``): the specialists. The orchestrator asks the real model
  through the Router, and ``_run_sub_agent_streaming`` records the agent and
  the message and answers with a fixed line, so no specialist runs, writes or
  spends.

Two best-effort reads still try the database: the member's access
(``acb_auth.resolve_access``) and the tier overrides of ``acb_llm``. With no
database, as in CI, each one fails closed and logs a warning. Neither one
changes which tools the orchestrator holds here, because the harness gives
the run no integration.

A card on the stream is answered through ``executor.resolve_user_input``
with the run's own thread, as ``POST /agent/respond-input`` answers it.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
import tempfile
import types
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = "orchestrator"

#: The tools that name a delegate in their ``agent_name`` argument.
_BY_AGENT_NAME = frozenset({"call_agent", "call_agent_background", "delegate_to_agent"})

#: What a specialist answers in a live run, where it does not run.
LIVE_STAND_IN = (
    "(The routing eval stops here: {agent} received the task and did not run. "
    "Tell the member in one line that {agent} has it.)"
)

#: What the eval answers to a question card it cannot answer.
NO_ANSWER = "No answer: this is an unattended eval. Go on without it."


# ── the model ───────────────────────────────────────────────────────────────


def text_turn(text: str) -> list[dict[str, Any]]:
    return [{"role": "assistant", "content": text}, {"finish_reason": "stop"}]


def tool_turn(name: str, args: dict[str, Any], call_id: str = "call_1") -> list[dict[str, Any]]:
    return [
        {"role": "assistant", "tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }]},
        {"finish_reason": "tool_calls"},
    ]


class ReplyModel:
    """An ``httpx.MockTransport`` handler that plays turns in order.

    It answers a streamed request (the top-level run) and a plain one (the
    batch run of a MAF sub-agent). Each request body is kept in
    :attr:`bodies`, so a check reads what the real client sent.
    """

    def __init__(self, turns: list[list[dict[str, Any]]]) -> None:
        self.turns = turns
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        index = len(self.bodies)
        self.bodies.append(body)
        turn = self.turns[min(index, len(self.turns) - 1)]
        if body.get("stream"):
            return self._streamed(index, turn)
        message: dict[str, Any] = {"role": "assistant", "content": None}
        finish = "stop"
        for part in turn:
            finish = part.get("finish_reason") or finish
            if part.get("content"):
                message["content"] = part["content"]
            if part.get("tool_calls"):
                message["tool_calls"] = [
                    {k: v for k, v in c.items() if k != "index"} for c in part["tool_calls"]
                ]
        return httpx.Response(200, json={
            "id": f"chatcmpl-{index}", "object": "chat.completion", "created": 0,
            "model": "scripted",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    @staticmethod
    def _streamed(index: int, turn: list[dict[str, Any]]) -> httpx.Response:
        chunks = []
        for part in turn:
            delta = {k: v for k, v in part.items() if k != "finish_reason"}
            chunks.append({
                "id": f"chatcmpl-{index}", "object": "chat.completion.chunk",
                "created": 0, "model": "scripted",
                "choices": [{"index": 0, "delta": delta,
                             "finish_reason": part.get("finish_reason")}],
            })
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=data.encode(),
                              headers={"content-type": "text/event-stream"})

    def saw(self, text: str) -> bool:
        """True when *text* is in a message of any request this model got."""
        return any(text in json.dumps(b.get("messages") or [], ensure_ascii=False)
                   for b in self.bodies)


# ── the registry ────────────────────────────────────────────────────────────


def registry() -> dict[str, dict[str, Any]]:
    """The static registry, by agent name. The executor reads the same list."""
    from gateway.routes.agent import _AGENT_REGISTRY

    return {str(e["name"]): dict(e) for e in _AGENT_REGISTRY if e.get("name")}


def orchestrator_tool_names() -> frozenset[str]:
    """The tools of the REAL orchestrator agent, as its factory builds it.

    The specialist tools come from ``orchestrator.agents._load_specialist_agents_as_tools``,
    which reads the registry. The dynamic registry must answer empty first
    (:class:`RoutingHarness` does it), or a database row joins the list.
    """
    from orchestrator.agents import build_orchestrator_agent

    agent = build_orchestrator_agent(with_history=False)
    pool: list[Any] = []
    options = getattr(agent, "default_options", None)
    if isinstance(options, dict):
        pool += list(options.get("tools") or [])
    pool += list(getattr(agent, "_tools", None) or [])
    return frozenset(
        str(getattr(t, "name", None) or getattr(t, "__name__", "")) for t in pool
    )


def specialist_tool(agent: str) -> str:
    """The orchestrator's tool for *agent* (``orchestrator/agents.py``)."""
    return agent.replace("-", "_")


# ── one run ─────────────────────────────────────────────────────────────────


@dataclass
class RunRecord:
    """What one run of the orchestrator did."""

    thread_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    #: The delegates the orchestrator named in its own tool calls, in order.
    routed: list[str] = field(default_factory=list)
    #: The agents whose sub-run reached the parent's stream.
    reached: list[str] = field(default_factory=list)
    #: The question cards (``elicitation_requested``) and their answers.
    asked: list[dict[str, Any]] = field(default_factory=list)
    #: The confirmation cards, each with its owner thread and the answer.
    cards: list[dict[str, Any]] = field(default_factory=list)
    #: The orchestrator's own tool calls: name and arguments.
    tool_calls: list[tuple[str, str]] = field(default_factory=list)
    answer: str = ""
    error: str | None = None


def delegates_of(name: str, args: str, agents: frozenset[str]) -> list[str]:
    """The agents that one tool call of the orchestrator names."""
    try:
        parsed = json.loads(args or "{}")
    except ValueError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    if name in _BY_AGENT_NAME:
        return [str(parsed.get("agent_name") or "")]
    if name == "call_agents_parallel":
        try:
            tasks = json.loads(parsed.get("tasks") or "[]")
        except (TypeError, ValueError):
            tasks = []
        return [str(t.get("agent") or "") for t in tasks if isinstance(t, dict)]
    as_agent = name.replace("_", "-")
    return [as_agent] if as_agent in agents else []


#: Decides a confirmation card: ``(record, event value) -> "APPROVE" | "REJECT"``.
CardAnswer = Callable[[RunRecord, dict[str, Any]], str]


def _reject(_record: RunRecord, _value: dict[str, Any]) -> str:
    return "REJECT"


def _answer_card(record: RunRecord, value: dict[str, Any], answer: str) -> bool:
    """Answer a parked card as the member does, through the run's own thread.

    When the owner check refuses the answer, the card is closed with a REJECT
    so the run ends, and ``answered`` stays False for the check to read.
    """
    from orchestrator import executor

    rid = str(value.get("request_id") or "")
    ok = executor.resolve_user_input(rid, answer, thread_id=record.thread_id)
    if not ok:
        fut = executor._pending_user_input.get(rid)
        if fut is not None and not fut.done():
            fut.set_result({"answer": "REJECT", "wasFreeform": False})
    return ok


def _take(record: RunRecord, event: dict[str, Any], calls: dict[str, dict[str, str]],
          on_card: CardAnswer, question_answer: str) -> None:
    from orchestrator import executor

    kind = event.get("type")
    if kind == "TOOL_CALL_START":
        calls[str(event.get("toolCallId"))] = {
            "name": str(event.get("toolCallName") or ""), "args": str(event.get("args") or ""),
        }
    elif kind == "TOOL_CALL_ARGS":
        call = calls.setdefault(str(event.get("toolCallId")), {"name": "", "args": ""})
        call["deltas"] = call.get("deltas", "") + str(event.get("delta") or "")
    elif kind in ("SUB_AGENT_TEXT_DELTA", "SUB_AGENT_TOOL_CALL_START",
                  "SUB_AGENT_TOOL_CALL_RESULT", "SUB_AGENT_ERROR"):
        name = str(event.get("agentName") or "")
        if name and name not in record.reached:
            record.reached.append(name)
    elif kind == "TEXT_MESSAGE_CONTENT":
        record.answer += str(event.get("delta") or "")
    elif kind == "RUN_ERROR":
        record.error = str(event.get("message") or event)[:500]
    elif kind == "CUSTOM" and isinstance(event.get("value"), dict):
        value = event["value"]
        rid = str(value.get("request_id") or "")
        if event.get("name") == "elicitation_requested":
            ok = _answer_card(record, value, question_answer)
            record.asked.append({"request_id": rid, "questions": value.get("questions"),
                                 "answered": ok})
        elif event.get("name") == "confirmation_requested":
            owner = executor._pending_user_input.owner_of(rid)
            answer = on_card(record, value)
            ok = _answer_card(record, value, answer)
            record.cards.append({"request_id": rid, "title": value.get("title"),
                                 "detail": value.get("detail"), "owner": owner,
                                 "answer": answer, "answered": ok})


def _finish(record: RunRecord, calls: dict[str, dict[str, str]], agents: frozenset[str]) -> None:
    for call in calls.values():
        args = call.get("args") or call.get("deltas") or ""
        record.tool_calls.append((call["name"], args))
        for name in delegates_of(call["name"], args, agents):
            if name and name not in record.routed:
                record.routed.append(name)


# ── the harness ─────────────────────────────────────────────────────────────


class RoutingHarness:
    """One sweep: the patches, the state root, and the models of this run."""

    def __init__(self, *, live: bool = False) -> None:
        self.live = live
        #: The model of each agent, for the NEXT run. Scripted runs only.
        self.models: dict[str, ReplyModel] = {}
        #: Every agent the loader built, in order.
        self.loaded: list[str] = []
        #: Each line the run pushed to the relay: ``(thread id, line)``.
        self.relayed: list[tuple[str, str]] = []
        #: Each host HTTP send of a scripted run. It must stay empty.
        self.sent: list[str] = []
        #: Live: each delegation the stand-in took, ``(agent, message)``.
        self.delegated: list[tuple[str, str]] = []
        self._mp: Any = None
        self._root: Path | None = None
        self._modules: dict[str, Any] = {}
        self._registry: dict[str, dict[str, Any]] = {}

    def __enter__(self) -> RoutingHarness:
        import acb_memory
        import gateway.routes.agent as routes_agent
        import pytest
        from acb_common import get_settings
        from orchestrator import _tool_injection as ti
        from orchestrator import executor

        self._mp = mp = pytest.MonkeyPatch()
        self._root = Path(tempfile.mkdtemp(prefix="route"))
        settings = get_settings()
        mp.setattr(settings, "agents_clone_dir", str(self._root))
        mp.setattr(settings, "maf_coding_scope", "", raising=False)
        mp.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
        mp.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
        # WS-17 EM-G11d: the upward-egress mark in memory, as in the unit
        # tests. CI has no Redis, and the read fails closed, so each member
        # chat would read as marked and each delegation would ask a card.
        from tests.unit._egress_mark_store import install_memory_mark_store

        install_memory_mark_store(mp)
        # WS-62 CF-3: the panes the member holds, for show_in_app. The real
        # read is acb_auth.resolve_access, and CI has no database, so it
        # would fail closed and refuse every page. The stand-in holds every
        # live pane's feature. A test that wants less sets its own.
        from orchestrator import in_app_path

        held = frozenset(p.feature for p in in_app_path.PANES if p.feature)

        async def _member_features() -> frozenset[str]:
            return held

        mp.setattr(in_app_path, "member_features", _member_features)
        ti._build_registry_block.cache_clear()
        ti._build_injected_tools_addendum.cache_clear()
        self._registry = registry()

        async def nothing(*_a: Any, **_k: Any) -> Any:
            return ""

        async def relay(thread_id: str, line: str) -> None:
            self.relayed.append((thread_id, line))

        mp.setattr(executor, "load_agent", lambda name, **_k: _Loaded(self, name))
        mp.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
        mp.setattr(executor, "_install_push_guard", nothing)
        mp.setattr(executor, "_get_current_head", nothing)
        mp.setattr(executor, "_detect_agent_commits", nothing)
        mp.setattr(executor, "_push_sse_to_stream", relay)
        mp.setattr(acb_memory, "rehydrate_workspace", nothing)

        async def native_crm() -> str:
            return "native"

        import acb_skills.crm_tools as crm_tools

        mp.setattr(crm_tools, "crm_mode", native_crm)
        from orchestrator import app_tools

        mp.setattr(app_tools, "_granted_live_apps", lambda _agent: [])
        mp.setitem(sys.modules, "ddgs", None)
        mp.setitem(sys.modules, "duckduckgo_search", None)
        if self.live:
            mp.setattr(executor, "_run_sub_agent_streaming", self._stand_in)
        else:
            async def trap(_transport: Any, request: Any) -> Any:
                self.sent.append(f"{request.method} {request.url}")
                raise AssertionError(f"the scripted run sent {request.method} {request.url}")

            mp.setattr(httpx.AsyncHTTPTransport, "handle_async_request", trap)
        return self

    def __exit__(self, *_exc: Any) -> None:
        from orchestrator import _tool_injection as ti

        if self._mp is not None:
            self._mp.undo()
        ti._build_registry_block.cache_clear()
        ti._build_injected_tools_addendum.cache_clear()
        if self._root is not None:
            shutil.rmtree(self._root, ignore_errors=True)

    # ── the loader ──────────────────────────────────────────────────────────

    def agent_dir(self, name: str) -> Path:
        """A copy of the agent's dir at ``<root>/repos/<name>``, as the loader clones it."""
        entry = self._registry.get(name)
        if entry is None or not entry.get("local_path"):
            raise LookupError(f"{name!r} is not an in-repo agent of the registry")
        assert self._root is not None
        target = self._root / "repos" / name
        if not target.is_dir():
            shutil.copytree(REPO_ROOT / entry["local_path"], target,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return target

    def build(self, name: str) -> list[Any]:
        """The real factory of *name*. A scripted run replaces only the wire."""
        from tests.unit._native_maf_harness import load_agent_module

        self.loaded.append(name)
        module = self._modules.get(name)
        if module is None:
            module = load_agent_module(self._registry[name]["local_path"])
            self._modules[name] = module
        agents = module.build_agents()
        if self.live:
            return agents
        model = self.models.get(name)
        if model is None:
            raise AssertionError(f"the scripted run has no model for {name!r}")
        client = getattr(getattr(agents[0], "client", None), "client", None)
        if client is None or not hasattr(client, "_client"):
            raise AssertionError(
                f"{name!r} is not a MAF agent with an OpenAI client, so a script cannot drive it",
            )
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(model), event_hooks=client._client.event_hooks,
        )
        return agents

    # ── live: the stand-in for a specialist ─────────────────────────────────

    async def _stand_in(self, agent_name: str, message: str, run_id: str,
                        event_queue: Any = None, model: str | None = None) -> str:
        del model
        self.delegated.append((agent_name, message))
        text = LIVE_STAND_IN.format(agent=agent_name)
        if event_queue is not None:
            await event_queue.put({"type": "SUB_AGENT_TEXT_DELTA", "agentName": agent_name,
                                   "runId": run_id, "delta": text})
        return text

    # ── one run ─────────────────────────────────────────────────────────────

    async def drive(
        self,
        words: str,
        system_context: str,
        *,
        member: str,
        org: str,
        tier: str | None = None,
        on_card: CardAnswer = _reject,
        question_answer: str = NO_ANSWER,
        timeout: float = 120.0,
        shows_cards: bool = True,
    ) -> RunRecord:
        """One chat turn of the one assistant, through ``run_agent_stream``.

        *shows_cards* is what the caller declares. The web chat draws cards,
        so it is True by default."""
        from acb_common.db import bind_tenant, release_tenant
        from acb_skills.write_artifact import bind_artifact_context
        from orchestrator import executor

        from tests.unit._native_maf_harness import parse_frames

        record = RunRecord(thread_id=str(uuid.uuid4()))
        calls: dict[str, dict[str, str]] = {}
        agents = frozenset(self._registry)
        payload = {"mode": "chat", "message": words, "user_email": member,
                   "system_context": system_context}

        async def consume() -> None:
            async for line in executor.run_agent_stream(
                ORCHESTRATOR, payload, run_id=str(uuid.uuid4()),
                thread_id=record.thread_id, model=tier, organization_id=org,
                session_user=member, shows_cards=shows_cards,
            ):
                for event in parse_frames([line]):
                    record.events.append(event)
                    _take(record, event, calls, on_card, question_answer)

        token = bind_tenant(org)
        try:
            await asyncio.wait_for(consume(), timeout=timeout)
        except TimeoutError:
            record.error = f"the run did not end within {timeout:.0f} s"
        finally:
            release_tenant(token)
            bind_artifact_context()
            _finish(record, calls, agents)
            for rid in list(executor._pending_user_input):
                with contextlib.suppress(Exception):
                    executor._pending_user_input.pop(rid, None)
        return record


class _Loaded:
    """What ``executor.load_agent`` returns: a context of the loaded agent."""

    def __init__(self, harness: RoutingHarness, name: str) -> None:
        self._harness = harness
        self._name = name

    def __enter__(self) -> Any:
        h, name = self._harness, self._name
        target = h.agent_dir(name)
        config = json.loads((target / "config.json").read_text(encoding="utf-8"))
        return types.SimpleNamespace(agent_dir=target, agent_name=name, config=config,
                                     build_agents=lambda: h.build(name))

    def __exit__(self, *_a: Any) -> bool:
        return False
