"""Agent executor — runs a dynamically loaded agent's MAF agent list.

Flow (ADR-013, ADR-016, ADR-018, WBS 0.7):

1. Delegate to :func:`load_agent` to clone repos + import ``agents.py``.
2. Call ``loaded.build_agents()`` to get the agent's MAF ``list[Agent]``.
3. Run ``_run_with_maf_agent`` — calls ``agents[0].run(message)`` via MAF.
   No PostgresSaver / LangGraph checkpointer needed; MAF AgentSession is
   in-memory for background runs; RedisHistoryProvider handles chat persistence.
4. On any unhandled exception, call :func:`~orchestrator.mutation.attempt_self_mutation`
   (ADR-006, ADR-021) which enforces ``max_mutation_attempts = 1``.
5. Cleanup happens in the :class:`~acb_skills.loader.LoadedAgent` context manager.

Usage::

    from orchestrator.executor import run_agent, run_agent_stream

    # Batch (existing):
    result = await run_agent("task-manager", {"clickup_event": {...}})

    # Streaming SSE (new — for /agent/run/stream endpoint):
    async for line in run_agent_stream("task-manager", payload, run_id=..., thread_id=...):
        yield line  # each line is a complete "data: {...}\\n\\n" SSE frame
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from acb_audit import AuditEvent, record
from acb_common import get_logger, get_settings
from acb_skills.ask_tools import is_hitl_blocking_tool as _is_hitl_blocking_tool
from acb_skills.integrations import build_integrations
from acb_skills.loader import AgentLoadError, load_agent

# Max self-anneal retries before giving up and falling back to LLM recovery.
_MAX_ANNEAL_ATTEMPTS = 2

# ── Tool execution timeout ────────────────────────────────────────────────
# When the agent calls a tool (shell command, sub-agent, web fetch, etc.),
# the tool runs inside the agent's async event loop.  If the tool hangs
# (infinite loop, waiting for stdin, network partition), the stream blocks
# until the HTTP-level abort.  This bounds individual ASYNC tool execution in
# the Tier-2 batch shim (the native MAF path has its own per-tool idle
# watchdog) so a hung tool is surfaced as an error instead of a silent hang.
#
# The watchdog VALUES + the native tier-selection rule now live in ONE place —
# orchestrator/watchdog.py (core_module_map A1/A2 "unified watchdog policy") —
# instead of being read via os.environ.get() at three call sites. Env knob
# names are unchanged (COPILOT_TOOL_TIMEOUT_SECONDS etc.).
from orchestrator.watchdog import (
    LoopDetector as _LoopDetector,
    default_watchdog,
)

_WATCHDOG = default_watchdog()


# Copilot-SDK session tuning (permission handler + infinite-session policy)
# extracted to orchestrator/_copilot_session.py (maintainability refactor).
# Re-exported so existing bare-name call sites + the copilot-infinite-sessions
# tests (which reach them as ``executor.<name>``) keep resolving unchanged.
from orchestrator._copilot_session import (
    _apply_copilot_infinite_sessions,
    _copilot_infinite_session_config,
    _copilot_permission_handler,
)


# Platform tool injection extracted to orchestrator/_tool_injection.py
# (maintainability refactor). Re-exported so bare-name call sites in the run
# paths + external importers (tests, tool-addendum eval) keep resolving.
from orchestrator._tool_injection import (
    _apply_own_tool_scope,
    _build_injected_tools_addendum,
    _build_registry_block,
    _gate_injected_tool,
    _inject_agent_tools,
    _inject_mcp_servers,
    _tool_name,
    materialize_skill_bodies_for_agent,
)
def _missing_module_name(exc: BaseException) -> str | None:
    """Best-effort top-level module name from an ImportError/ModuleNotFoundError.

    Prefers ``exc.name`` (set by ModuleNotFoundError); falls back to parsing
    "No module named 'pkg.sub'" → "pkg". Returns None if it can't tell — the
    caller then surfaces a clear error instead of installing a guess.
    """
    name = getattr(exc, "name", None)
    if not name:
        import re as _re
        m = _re.search(r"No module named ['\"]([^'\"]+)['\"]", str(exc))
        name = m.group(1) if m else None
    # Install the TOP-LEVEL distribution name (submodule installs never work).
    return name.split(".")[0] if name else None
_TOOL_EXECUTION_TIMEOUT: float = _WATCHDOG.tool_execution

# ── Elicitation bridge: track which tool_call_id maps to a pending
# ask_questions Future so cleanup only fires for the matching result.
_elicitation_tc_ids: dict[str, str] = {}

_log = get_logger("orchestrator.executor")


# ── agent_framework OpenTelemetry instrumentation kill switch ────────────────
# MAF's telemetry is enabled-by-default but exports NOWHERE in this deployment
# (no OTLP / App Insights backend is configured), and its streaming-telemetry
# cleanup hook (_finalize_stream) resets a ContextVar in a DIFFERENT async
# context than the one it set it in — its reset runs in the asyncio.ensure_future
# child context used to pull each stream chunk — so CPython raises
# "Token was created in a different Context" at the END of a streamed run, after
# the full answer already streamed, turning a successful run into a RUN_ERROR.
# Disabling the dead instrumentation removes the cause (the wrapper is bypassed
# entirely when OBSERVABILITY_SETTINGS.ENABLED is False). Re-enable by setting
# ENABLE_INSTRUMENTATION=1 once an actual trace backend is wired up.
_telemetry_disabled = False


def _disable_agent_telemetry_once() -> None:
    """Disable agent_framework instrumentation process-wide (idempotent, sticky).

    Called at the top of every agent entry point so it runs before the first
    ``agent.run(stream=True)``. No-op when the operator opted in via
    ``ENABLE_INSTRUMENTATION``."""
    global _telemetry_disabled
    if _telemetry_disabled:
        return
    _telemetry_disabled = True
    if os.getenv("ENABLE_INSTRUMENTATION", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return  # operator explicitly wants tracing — leave it on
    try:
        from agent_framework.observability import (
            disable_instrumentation,
        )
        disable_instrumentation()
        _log.info("executor.agent_telemetry_disabled")
    except Exception as exc:
        # Best-effort — the executor's ValueError guard still catches the symptom.
        _log.warning(
            "executor.agent_telemetry_disable_failed", error=str(exc)[:160])


# Pre-compiled regex for tool result clearing (technique #1).
# Matches tool-result code blocks embedded in assistant messages so they can
# be stripped from old history turns — avoids re-sending 5k-token API dumps.
_TOOL_RESULT_RE = re.compile(
    r"\n?(?:Tool call|\[tool\]|```json)[^`]*```", re.S
)

# ContextVar that holds the active SSE queue for the current agent run.
# Set by run_agent_stream so that call_agent (injected as a tool) can push
# SUB_AGENT_* events into the parent stream, making sub-agent progress visible
# in the UI in real time.
_active_run_queue: contextvars.ContextVar["asyncio.Queue[dict[str, Any] | None] | None"] = (
    contextvars.ContextVar("_active_run_queue", default=None)
)

# Plain (non-ContextVar) registry of the active run's event queue, keyed by
# session/thread id. The GitHub-Copilot SDK dispatches tool callables from its
# JSON-RPC read thread via ``asyncio.run_coroutine_threadsafe`` (see
# copilot/_jsonrpc.py), which schedules the coroutine with a FRESH context —
# so ``_active_run_queue`` (a ContextVar) is NOT visible inside a tool invoked
# by a Copilot agent. Tools that must push CUSTOM events into the live chat
# stream (write_artifact, emit_generative_ui) resolve the queue from HERE first
# (survives the thread hop), then fall back to the ContextVar for native-MAF
# runs. Set/cleared alongside _active_run_queue by every run path.
_RUN_QUEUES: dict[str, "asyncio.Queue[dict[str, Any] | None]"] = {}

# Plain (non-ContextVar) registry of the active run's tenant (organization_id),
# keyed by session/thread id — the tenant twin of ``_RUN_QUEUES`` above, and it
# exists for the SAME reason. The core agent run writes tenant tables through the
# SYNC ``acb_graph`` engine inside ``loop.run_in_executor(...)`` worker threads,
# and Python contextvars are NOT copied across ``run_in_executor`` — so the
# request's ambient tenant binding (a ContextVar) is already gone by the time
# those worker-thread writes run, and the detached run outlives the request
# scope anyway. Later slices' worker-thread writes read the org from HERE
# (captured into the closure BEFORE the ``run_in_executor`` hop) to open
# ``acb_graph.tenant_session(org)``. Set at the top of ``run_agent_stream`` and
# deleted in its ``finally``. WS-29 MT-1d (H4), slice 2 — DARK: nothing reads it
# yet (no write is converted this slice).
_RUN_ORG: dict[str, str] = {}


def _opener_for_org(org: str | None):
    """Map an already-resolved tenant to the ``acb_graph`` session opener.

    The ONE opener-construction shared by :func:`_graph_session_opener` (which
    resolves the org from ``_RUN_ORG`` keyed by an explicit thread id, for the
    worker-thread writes) and :func:`_graph_session_opener_current` (which
    resolves it from the ambient run frame, for reads with no thread id in hand).
    Extracting it keeps a single flag/opener doctrine — not a second one — as the
    read conversions land (WS-29 acb_graph slice 7). Behind
    ``ACB_GRAPH_TENANT_BIND``:

    * flag OFF → :func:`acb_graph.get_session` (unbound) — byte-identical runtime.
    * flag ON + a resolvable tenant → ``lambda: acb_graph.tenant_session(org)``.
    * flag ON + NO resolvable tenant → ``None``: the caller MUST fail closed
      (skip a best-effort write / fall back / return empty on a read) rather than
      touch a FORCE-RLS'd table with an unbound tenant.
    """
    from acb_graph import tenant_bind_enabled
    if not tenant_bind_enabled():
        from acb_graph import get_session
        return get_session
    if not org:
        return None
    from acb_graph import tenant_session
    return lambda: tenant_session(org)


def _graph_session_opener(thread_id: str | None):
    """Pick the ``acb_graph`` session context-manager factory for a converted
    read/write, resolving the run's tenant on the CURRENT (event-loop) frame.

    Returns a **zero-arg callable** that opens the session — so a worker-thread
    write can close over it and open the session AFTER the ``run_in_executor``
    hop WITHOUT reading ``_RUN_ORG`` (a plain dict) or any ContextVar from the
    worker thread (contextvars are not copied across the hop). Behind
    ``ACB_GRAPH_TENANT_BIND`` (WS-29 acb_graph slice 3):

    * flag OFF → :func:`acb_graph.get_session` (unbound) — byte-identical runtime.
    * flag ON + a resolvable tenant → ``lambda: acb_graph.tenant_session(org)``.
    * flag ON + NO resolvable tenant → ``None``: the caller MUST fail closed
      (skip a best-effort write / fall back on a read) rather than touch a
      FORCE-RLS'd table with an unbound tenant.

    ``_RUN_ORG`` is read HERE, on the caller's frame — for the worker-thread
    writes that is the event loop BEFORE the hop, which is the whole reason this
    resolution is separated from the write closure.
    """
    return _opener_for_org(_RUN_ORG.get(thread_id) if thread_id else None)


def _graph_session_opener_current():
    """:func:`_graph_session_opener` for a converted READ that has NO thread id
    in hand — an agent tool (``retrieve_entity_context``) or the per-run tool
    injection (``_granted_live_apps`` / ``_load_disabled_skill_families``).

    Resolves the CURRENT run's tenant on the caller's (event-loop) frame via
    :func:`_current_run_org`, then hands it to :func:`_opener_for_org` — the SAME
    flag/opener doctrine as the thread-keyed opener. WS-29 acb_graph slice 7. The
    read site MUST call this BEFORE any ``asyncio.to_thread`` / ``run_in_executor``
    hop and close the returned opener over the worker-thread body, exactly like
    the slice 3-5 writes: ``_RUN_ORG`` is a plain dict and contextvars do not
    cross the hop, so the tenant must be captured on this frame.

    * flag OFF → the unbound ``get_session`` — byte-identical.
    * flag ON + the run's tenant → ``lambda: tenant_session(org)`` (returns rows).
    * flag ON + no resolvable tenant → ``None``: the caller fails closed (returns
      empty / no tools) rather than read a FORCE-RLS'd table unbound (0 rows).
    """
    return _opener_for_org(_current_run_org())


def _guarded_pop_run_org(
    thread_id: str | None, organization_id: str | None,
) -> None:
    """Drop this run's ``_RUN_ORG`` record ONLY if it is STILL this run's org.

    Mirrors the ``_DETACHED_TASKS.pop`` identity guard in ``stream_relay`` (WS-29
    slice 3, P2): a superseded run's late ``finally`` — it lost the thread to a
    newer run that already overwrote ``_RUN_ORG[thread_id]`` — must NOT delete the
    NEWER run's org, or that run's worker-thread writes would fail-closed-skip. A
    run that carried no org never set the key, so it never pops. Reverting this to
    an unconditional ``_RUN_ORG.pop(thread_id, None)`` reintroduces the bug (and
    turns ``run-org-guarded-pop`` RED).
    """
    if (
        organization_id is not None
        and _RUN_ORG.get(thread_id) == organization_id
    ):
        _RUN_ORG.pop(thread_id, None)


def _current_run_org() -> str | None:
    """The tenant of the run executing on THIS event-loop frame, resolved
    SERVER-SIDE from the run boundary — NEVER from tool arguments / the event
    payload (R11, which is why this takes no argument).

    WS-29 acb_graph slice 7. This is the ambient-frame twin of
    :func:`_graph_session_opener`'s ``_RUN_ORG[thread_id]`` lookup, for a
    converted READ that has no thread id in hand (an agent tool, per-run tool
    injection). Read on the CALLER's (event-loop) frame, BEFORE any worker-thread
    hop, in this order:

    1. ``_RUN_ORG`` keyed by this run's thread id — the ``_stream_relay_thread_id``
       ContextVar (set by ``run_agent_stream``), or, failing that, the bound run
       context's ``thread_id``. This is the primary path for a stream/batch run.
    2. the async bound tenant (:func:`acb_common.db.current_tenant`) — the
       fallback for the ``/copilot/chat`` path, which binds the tenant on its
       detached drain task (slice 6a) but runs the MAF orchestrator directly and
       so sets no ``_RUN_ORG`` entry.

    Returns ``None`` when no tenant is resolvable (a truly untenanted background
    run); the caller fails closed.
    """
    tid = _stream_relay_thread_id.get(None)
    if not tid:
        try:
            from acb_common import get_run_context
            tid = (get_run_context() or {}).get("thread_id")
        except Exception:
            tid = None
    org = _RUN_ORG.get(tid) if tid else None
    if org:
        return org
    try:
        from acb_common.db import current_tenant
        return current_tenant()
    except Exception:
        return None


def _resolve_sub_agent_org() -> str | None:
    """The tenant a delegated sub-run must act in: its PARENT run's org.

    WS-29 acb_graph slice 6a. A sub-agent acts in the SAME tenant as the run that
    delegated to it — and that run is precisely the one executing on THIS frame
    when the delegation call is made, so this is exactly :func:`_current_run_org`
    (slice 7 unified the two resolvers into one, rather than keeping a second
    copy of the ``_RUN_ORG`` → bound-tenant walk). Resolved SERVER-SIDE from the
    run boundary — NEVER from the delegated message / event payload (R11, which is
    why this, like ``_current_run_org``, takes no argument).

    Returns ``None`` when no parent tenant is resolvable (a truly untenanted
    background run); the sub-run then behaves like any org-less batch run
    (operator-org audit fallback / flag-ON write skip).
    """
    return _current_run_org()


def _register_run_queue(
    key: str | None, queue: "asyncio.Queue[dict[str, Any] | None]",
) -> None:
    """Register the active run's event queue under *key* (session/thread id).

    A no-op when *key* is falsy. Idempotent — last writer for a key wins, which
    is correct because a given thread has exactly one live run at a time.
    """
    if key:
        _RUN_QUEUES[key] = queue


def _unregister_run_queue(key: str | None) -> None:
    """Drop the run-queue registration for *key* (best-effort)."""
    if key:
        _RUN_QUEUES.pop(key, None)


def resolve_run_queue(
    key: str | None = None,
) -> "asyncio.Queue[dict[str, Any] | None] | None":
    """Return the active run's event queue for pushing CUSTOM SSE events.

    Resolution order: the ContextVar (native-MAF, same-context tool calls) →
    the plain registry keyed by *key* (Copilot-SDK tools that run in a
    context-reset thread). Tools pass the session/thread id they already hold
    (from _WRITE_ARTIFACT_CONTEXT["session_id"]) as *key*.
    """
    q = _active_run_queue.get(None)
    if q is not None:
        return q
    if key and key in _RUN_QUEUES:
        return _RUN_QUEUES[key]
    # Last resort: if exactly one run is active, it must be this one.
    if len(_RUN_QUEUES) == 1:
        return next(iter(_RUN_QUEUES.values()))
    return None


def resolve_relay_thread_id() -> str | None:
    """Return the active run's stream-relay thread_id, surviving a thread hop.

    The blocking HITL tools (``ask_questions``, ``request_confirmation``) need the
    thread_id to push their card to the Redis relay and park on a Future. The
    natural source, ``_stream_relay_thread_id``, is a ContextVar — visible on the
    native-MAF path but RESET when the GitHub-Copilot SDK dispatches a tool from
    its JSON-RPC read thread (``run_coroutine_threadsafe`` schedules with a fresh
    context). So a Copilot agent's ask_questions saw ``None`` and fell through to
    the non-blocking path — the card looped and never accepted input.

    Resolution order (works for BOTH runtimes):
      1. ``_stream_relay_thread_id`` ContextVar — native-MAF, same-context tools.
      2. ``_WRITE_ARTIFACT_CONTEXT["session_id"]`` — a plain module dict the
         executor sets to ``thread_id or run_id`` on every run path (Copilot too),
         so it survives the SDK thread hop. Same trick write_artifact uses.
      3. The single active run-queue key, when exactly one run is live.
    """
    tid = _stream_relay_thread_id.get(None)
    if tid:
        return tid
    try:
        from acb_skills.write_artifact import (
            _WRITE_ARTIFACT_CONTEXT,
        )
        sid = _WRITE_ARTIFACT_CONTEXT.get("session_id")
        if sid:
            return str(sid)
    except Exception:
        pass
    if len(_RUN_QUEUES) == 1:
        return next(iter(_RUN_QUEUES.keys()))
    return None

# ContextVar that bridges the executor's ask_questions detection with the
# ask_questions tool function.  When the executor sees the Copilot SDK about
# to call ask_questions, it generates a request_id, creates a
# _pending_user_input Future, and sets this ContextVar so the tool function
# can find the Future and block on it (instead of returning immediately via
# the non-blocking Path B which causes the chat to die without output).
_active_elicitation_request_id: contextvars.ContextVar[str | None] = (
    contextvars.ContextVar("_active_elicitation_request_id", default=None)
)

# ContextVar that holds the current thread_id for stream relay tee-ing.
# When set, every _sse() call automatically pushes the event to Redis Stream
# so the reconnect endpoint can replay missed events after a disconnect.
_stream_relay_thread_id: contextvars.ContextVar[str | None] = (
    contextvars.ContextVar("_stream_relay_thread_id", default=None)
)

# ContextVar that holds the resolved model/tier of the CURRENT agent run, so
# sub-agents spawned via call_agent / call_agents_parallel / delegate_to_agent
# inherit the parent's tier instead of silently falling back to their own
# config default. Set inside run_agent_stream once the model is resolved.
_active_run_model: contextvars.ContextVar[str | None] = (
    contextvars.ContextVar("_active_run_model", default=None)
)


async def _push_sse_to_stream(thread_id: str, sse_line: str) -> None:
    """Push an SSE line to the Redis stream for reconnection support.

    Best-effort: failures are silently swallowed so the SSE stream is never
    interrupted by Redis issues.
    """
    try:
        from orchestrator.stream_relay import push_sse_event
        await push_sse_event(thread_id, sse_line)
    except Exception:
        pass


# Per-thread chains of in-flight Redis pushes.  Each new push awaits the
# previous one so events land in Redis in EXACT emission order (bare
# fire-and-forget create_task calls can interleave under load).
_push_chains: dict[str, "asyncio.Task[None]"] = {}


def _tee_sse_line(line: str) -> None:
    """Schedule an ordered, best-effort push of an SSE line to the Redis
    stream for the current relay thread (no-op when relay is unset)."""
    tid = _stream_relay_thread_id.get(None)
    if tid is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # No running event loop — skip relay
    prev = _push_chains.get(tid)

    async def _chained() -> None:
        if prev is not None:
            try:
                await prev
            except Exception:
                pass
        await _push_sse_to_stream(tid, line)

    task = loop.create_task(_chained())
    _push_chains[tid] = task
    task.add_done_callback(
        lambda t, _tid=tid: (
            _push_chains.pop(_tid, None)
            if _push_chains.get(_tid) is t
            else None
        )
    )


# ── Native HITL (Copilot SDK ask_user) ─────────────────────────────────────
# The Copilot SDK's built-in ``ask_user`` tool is enabled by registering an
# ``on_user_input_request`` handler.  The handler is awaited by the SDK and
# BLOCKS the agent turn until it returns the user's answer — this is the
# correct way to pause/resume a run (unlike the fire-and-forget custom
# ``ask_questions`` tool, which forced the user's reply to queue as a new
# message).  The handler emits a ``user_input_requested`` SSE frame to the
# live stream and parks on an asyncio.Future until the frontend POSTs the
# answer to ``/agent/respond-input`` (which calls :func:`resolve_user_input`).
_pending_user_input: dict[str, "asyncio.Future[dict[str, Any]]"] = {}

# How long the agent waits for a human answer before giving up (seconds).
_USER_INPUT_TIMEOUT = int(os.environ.get("ASK_USER_TIMEOUT", "3600"))


def resolve_user_input(
    request_id: str, answer: str, was_freeform: bool = True
) -> bool:
    """Resolve a pending ``ask_user`` request with the user's answer.

    Called by the gateway ``/agent/respond-input`` route.  Returns True when
    a matching pending request was found and resolved, False otherwise.
    """
    fut = _pending_user_input.get(request_id)
    if fut is None or fut.done():
        return False
    payload = {"answer": answer, "wasFreeform": was_freeform}
    try:
        loop = fut.get_loop()
    except Exception:
        if not fut.done():
            fut.set_result(payload)
        return True
    loop.call_soon_threadsafe(
        lambda: (not fut.done()) and fut.set_result(payload)
    )
    return True


async def wait_user_future(
    fut: "asyncio.Future[dict[str, Any]]",
    timeout: float,
    thread_id: str | None = None,
    slice_seconds: float = 60.0,
) -> dict[str, Any]:
    """Await a parked HITL future, heartbeating the relay while waiting.

    A parked question pushes no events, so the ``cc:active`` flag's TTL
    (refreshed only by ``push_event``) lapsed during long waits — live
    subscribers terminated and reconnect reported the still-parked run as
    finished, clearing the question card (audit R3). Waiting in 60s slices
    and touching the flag between them keeps the run visibly alive for the
    whole HITL budget. Raises ``asyncio.TimeoutError`` when *timeout*
    elapses, like the ``asyncio.wait_for`` it replaces; the future itself is
    left to the caller to clean up.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    tid = thread_id or resolve_relay_thread_id() or ""
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        try:
            # Shield: a slice timeout must not cancel the shared future —
            # the next slice keeps waiting on it.
            return await asyncio.wait_for(
                asyncio.shield(fut), timeout=min(slice_seconds, remaining)
            )
        except asyncio.TimeoutError:
            if fut.done():
                return fut.result()
            if tid:
                try:
                    from orchestrator.stream_relay import touch_active
                    await touch_active(tid)
                except Exception:
                    pass


def _make_user_input_handler(thread_id: str) -> Any:
    """Build an ``on_user_input_request`` handler bound to *thread_id*.

    The returned coroutine emits a ``user_input_requested`` event straight to
    the Redis relay (the streaming generator is parked awaiting ``agent.run``
    and cannot yield this frame itself) and blocks until the answer arrives.
    """

    async def _handler(request: Any, _ctx: Any) -> dict[str, Any]:
        global _sse_seq
        import time as _time
        import uuid as _uuid

        if isinstance(request, dict):
            question = request.get("question", "") or ""
            choices = request.get("choices") or []
            allow_freeform = request.get("allowFreeform", True)
        else:
            question = getattr(request, "question", "") or ""
            choices = getattr(request, "choices", None) or []
            allow_freeform = getattr(request, "allowFreeform", True)

        request_id = _uuid.uuid4().hex
        payload: dict[str, Any] = {
            "type": "CUSTOM",
            "name": "user_input_requested",
            "value": {
                "request_id": request_id,
                "question": str(question),
                "choices": [str(c) for c in choices],
                "allowFreeform": bool(allow_freeform),
            },
        }
        _sse_seq += 1
        payload["_stream_id"] = f"local-{int(_time.time() * 1000)}-{_sse_seq}"
        line = f"data: {json.dumps(payload)}\n\n"

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        _pending_user_input[request_id] = fut

        # Push to the relay so the live HTTP subscriber receives the prompt.
        await _push_sse_to_stream(thread_id, line)

        try:
            result = await wait_user_future(
                fut, _USER_INPUT_TIMEOUT, thread_id=thread_id
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            result = {"answer": "", "wasFreeform": True}
        finally:
            _pending_user_input.pop(request_id, None)

        return {
            "answer": str(result.get("answer", "")),
            "wasFreeform": bool(result.get("wasFreeform", True)),
        }

    return _handler


# ── Todo-list tracking (VS Code Copilot parity) ────────────────────────────
# Extracted to orchestrator/_todo_tracker.py (maintainability refactor).
# Re-exported here so existing references (``_TodoTracker`` / ``_unwrap_json_param``
# inside run_agent_stream) and any external importers keep resolving unchanged.
from orchestrator._todo_tracker import (
    _TodoTracker,
    _unwrap_json_param,
)


class AgentRunError(Exception):
    """Raised after an agent run fails (mutation already attempted if applicable)."""

    def __init__(
        self,
        message: str,
        *,
        agent_name: str,
        run_id: str,
        original: Exception,
        mutation_pr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.agent_name = agent_name
        self.run_id = run_id
        self.original = original
        self.mutation_pr = mutation_pr


async def _run_sub_agent_streaming(
    agent_name: str,
    message_str: str,
    run_id: str,
    event_queue: "asyncio.Queue[dict[str, Any] | None] | None" = None,
    model: str | None = None,
) -> str:
    """Run a sub-agent and forward its streaming events to *event_queue*.

    Called by ``call_agent`` when there is an active parent SSE queue so that
    the sub-agent's progress is visible in the UI in real time.

    *model* is the parent run's resolved tier; when set it takes priority over
    the sub-agent's own config default so a delegated task inherits the tier the
    user chose for the parent (both Copilot SDK and native MAF sub-agents).

    Supports GitHub Copilot SDK agents (native stream) and MAF agents (batch
    run with a single result delta at the end).

    When *event_queue* is ``None`` but ``_stream_relay_thread_id`` is set
    (Tier 1 / Tier 1.5 / any path without a queue), events are pushed
    directly to the Redis relay so the frontend subscriber receives them.

    Returns the final text response.
    """
    settings = get_settings()
    _repo_name: str | None = None
    _local_path: str | None = None
    _runtime: str = "maf"
    try:
        from gateway.routes.agent import _AGENT_REGISTRY
        from gateway.routes.agent import _load_dynamic_agents
        _all = _load_dynamic_agents() + _AGENT_REGISTRY
        entry = next((e for e in _all if e["name"] == agent_name), None)
        if entry:
            raw = entry.get("repo_name") or ""
            # Pass the full org/repo slug — load_agent splits it when needed
            _repo_name = raw if raw else None
            _local_path = entry.get("local_path")
            _runtime = entry.get("agent_runtime", "maf")
    except (ImportError, Exception):
        pass

    # Initialised before try so the finally block always has access
    # even when load_agent() or build_agents() raises early.
    _saved_artifact_ctx: dict[str, str] = {}

    # ── Redis relay fallback for paths without _active_run_queue ──────
    # Tier 1 (MAF AG-UI) and Tier 1.5 (Copilot SDK) don't set
    # _active_run_queue, so call_agent passes event_queue=None.  In that
    # case we push SUB_AGENT_* events directly to the Redis relay so the
    # frontend subscriber receives them in real time (same pattern as
    # ask_questions Path C).
    _relay_tid = _stream_relay_thread_id.get(None)
    _push_to_relay = bool(_relay_tid and event_queue is None)

    async def _emit_sub_event(evt: dict[str, Any]) -> None:
        """Push to queue (if available) and/or Redis relay."""
        if event_queue is not None:
            await event_queue.put(evt)
        if _push_to_relay:
            import json as _json_sub
            _payload = _json_sub.dumps(evt, default=str)
            _line = f"data: {_payload}\n\n"
            await _push_sse_to_stream(_relay_tid, _line)  # type: ignore[arg-type]

    # B6 Phase-5 Tier 0: init before the try so the finally can always restore.
    _integration_env_token: IntegrationEnvToken = None

    # Delegation is a run too, and until now it was invisible to everyone
    # outside the tab that started it: SUB_AGENT_* events reach the parent's
    # SSE stream and nothing else. So a delegated agent looked idle in the
    # office view while it was doing the work, and a second person watching a
    # shared room saw the parent stall with no explanation. `delegated_by` is
    # what makes it readable as one agent asking another rather than as two
    # unrelated runs that happen to overlap.
    import time as _time_sub
    _sub_started = _time_sub.monotonic()
    try:
        from acb_common import get_run_context
        _parent_agent = (get_run_context() or {}).get("agent")
    except Exception:
        _parent_agent = None
    try:
        from acb_common import publish_activity
        publish_activity(
            kind="agent", phase="start", agent=agent_name,
            run_id=run_id, source="delegation",
            delegated_by=_parent_agent, model=(model or None),
        )
    except Exception:
        pass

    try:
        with load_agent(agent_name, run_id=run_id, repo_name=_repo_name, local_path=_local_path) as loaded:
            mandatory = loaded.config.get("integrations", [])
            optional = loaded.config.get("optional_integrations", [])
            integrations, _ = build_integrations(mandatory, optional, settings)
            # Scope this sub-agent's creds to its run; restored in the finally.
            _integration_env_token = _bind_run_credentials(integrations)
            agents = loaded.build_agents()
            # Honour .github/agents/<name>.agent.md instructions for sub-agents
            # too, so a delegated Copilot SDK agent keeps its authored identity.
            _agent_md_spec = _apply_agent_md_overrides(
                agents, loaded.agent_dir, agent_name,
            )
            # Technique #3: read tool_scope from config.json to inject only the
            # tools this sub-agent actually needs (avoids the Berkeley leaderboard
            # accuracy degradation from too many tools).
            _sub_tool_scope = _merged_tool_scope(
                loaded.config.get("tool_scope") or None
                if hasattr(loaded, "config")
                else None,
                _agent_md_spec,
            )
            _apply_own_tool_scope(
                agents,
                loaded.config.get("own_tool_scope") or None
                if hasattr(loaded, "config") else None,
            )
            _inject_agent_tools(
                agents,
                is_sub_agent=True,
                tool_scope=_sub_tool_scope,
                agent_name=agent_name,
            )
            if not agents:
                return f"({agent_name!r} returned empty agent list)"
            agent = agents[0]

            # Apply the risk-aware permission handler for Copilot SDK agents (B6).
            try:
                _ph = _copilot_permission_handler()
                for _a in agents:
                    if hasattr(_a, "_permission_handler") and _a._permission_handler is None:
                        _a._permission_handler = _ph
            except Exception:
                pass

            # ── Set working directory for Copilot SDK sub-agents ──────────
            # The Copilot SDK CLI defaults to the gateway process CWD
            # unless working_directory is explicitly set.  Without this,
            # shell commands, file reads, AGENTS.md, and skill resolution
            # all happen in the wrong directory.
            _sub_agent_dir = str(loaded.agent_dir)
            if (
                _runtime == "github-copilot"
                and hasattr(agent, "_default_options")
                and agent._default_options is not None
            ):
                agent._default_options["working_directory"] = _sub_agent_dir
                # ── HITL for sub-agents (P0-8) ─────────────────────────
                # Without this, a delegated Copilot SDK agent that calls
                # its native ask_user has no on_user_input_request handler
                # bound: the SDK blocks forever with no card in the UI.
                # Bind the same handler the top-level run uses, keyed by
                # the PARENT thread so /agent/respond-input resolves it.
                if (
                    _relay_tid
                    and not agent._default_options.get("on_user_input_request")
                ):
                    agent._default_options["on_user_input_request"] = (
                        _make_user_input_handler(_relay_tid)
                    )

            # ── Point write_artifact at sub-agent's own workspace ────────
            # Save orchestrator's context, switch to sub-agent workspace so
            # artifacts land in the sub-agent's repo (visible in the Files
            # sidebar).  Restored after sub-agent completes.
            try:
                from acb_skills.write_artifact import \
                    _WRITE_ARTIFACT_CONTEXT
                for _k in ("workspace_root", "session_id", "integrations"):
                    _saved_artifact_ctx[_k] = _WRITE_ARTIFACT_CONTEXT.get(
                        _k, ""
                    )
                _WRITE_ARTIFACT_CONTEXT["workspace_root"] = _sub_agent_dir
                # The sub-agent's scripts get ITS declared integrations, not
                # the parent's (same scoping as the env injection above).
                _WRITE_ARTIFACT_CONTEXT["integrations"] = sorted(integrations)
                # session_id stays as orchestrator's so download URLs
                # resolve correctly in the parent chat window.
            except Exception:
                pass

            # Skills-as-an-index bodies (QM-2). A sub-agent gets the COMPACT
            # index but the same full bodies — a body costs nothing until it
            # is read. No-op while SKILLS_INDEX_ONLY is off.
            materialize_skill_bodies_for_agent(
                agent_name, _sub_agent_dir, tool_scope=_sub_tool_scope,
            )

            text_parts: list[str] = []

            # Max chars returned from a sub-agent to the parent (technique #5).
            # Research: sub-agents explore deeply (10k+ tokens) but the parent
            # only needs a condensed 1-2k token summary.  Prevents single
            # sub-agent calls from bloating the orchestrator's context.
            _MAX_SUB_RESULT_CHARS = int(
                os.environ.get("SUB_AGENT_MAX_RESULT_CHARS", "8000")
            )

            if _runtime == "github-copilot" and hasattr(agent, "run"):
                # Resolve model with priority:
                #   1. parent run's resolved tier (model arg) — tier inheritance
                #   2. copilot_chat_model (global setting)
                #   3. .github/agents/<name>.agent.md model (authored choice)
                #   4. Agent's model_tier from config.json
                _model = (model or "").strip() or (
                    getattr(settings, "copilot_chat_model", "") or ""
                ).strip() or (
                    (_agent_md_spec.model or "").strip()
                    if _agent_md_spec is not None else ""
                ) or (
                    loaded.config.get("model_tier") or ""
                ).strip()
                # BYOK-by-default: normalise bare/empty names to the default
                # tier and force gateway routing (mirrors the chat path).
                _model, _is_sub_byok = _byok_default_model(_model, settings)

                if _model:
                    try:
                        if (
                            hasattr(agent, "_default_options")
                            and agent._default_options is not None
                        ):
                            # BYOK: route LiteLLM-gateway models through the
                            # gateway's /v1 endpoint so the Copilot SDK session
                            # uses the BYOK provider instead of the default
                            # api.githubcopilot.com endpoint.
                            if _is_sub_byok:
                                _gw_base = (
                                    getattr(
                                        settings, "litellm_base_url", ""
                                    )
                                    or "http://127.0.0.1:8080"
                                ).rstrip("/")
                                # /v1 only — LLM API key, not the identity
                                # token: this provider config is attached to
                                # agent code (BO-2 residual #4).
                                _gw_key = (
                                    getattr(settings, "llm_api_key", "")
                                    or "sk-local"
                                ).strip()
                                agent._default_options["provider"] = {
                                    "type": "openai",
                                    "base_url": f"{_gw_base}/v1",
                                    "api_key": _gw_key,
                                }
                                agent._default_options["model"] = _model
                                _log.info(
                                    "executor.sub_agent_byok",
                                    agent=agent_name,
                                    model=_model,
                                    base_url=_gw_base,
                                )
                            else:
                                agent._default_options["model"] = _model
                    except Exception:
                        pass

                async with agent:
                    stream = agent.run(message_str, stream=True)
                    # ONE canonical mapping, sub-agent envelope on top —
                    # only the wrapper differs from the parent stream paths.
                    _sub_t_state = _TranslationState(run_id)
                    async for update in stream:
                        for _sev in _wrap_sub_agent_events(
                            _translate_update(update, _sub_t_state),
                            agent_name=agent_name, run_id=run_id,
                        ):
                            if _sev["type"] == "SUB_AGENT_TEXT_DELTA":
                                text_parts.append(_sev["delta"])
                            await _emit_sub_event(_sev)
            else:
                # MAF or unknown runtime: batch run, emit one result delta.
                # Forward the parent's resolved tier so native MAF sub-agents
                # inherit it (run_agent applies it via default_options["model"]).
                # WS-29 acb_graph slice 6a: a sub-agent acts in its PARENT's
                # tenant — resolve the parent run's org SERVER-SIDE (from
                # _RUN_ORG / the bound tenant, never message_str) and pass it so
                # the batch path binds it for this sub-run's acb_graph writes.
                result = await run_agent(
                    agent_name,
                    {"message": message_str, "mode": "sub_task"},
                    run_id=run_id,
                    model=model,
                    organization_id=_resolve_sub_agent_org(),
                )
                text = result.get("result") or result.get("answer") or ""
                if isinstance(text, dict):
                    text = text.get("content", str(text))
                final_text = str(text) if text else ""
                if final_text:
                    text_parts.append(final_text)
                    await _emit_sub_event({
                        "type": "SUB_AGENT_TEXT_DELTA",
                        "agentName": agent_name,
                        "runId": run_id,
                        "delta": final_text,
                    })

            # ── Technique #5: sub-agent result compression ───────────────
            # Anthropic multi-agent research: sub-agents explore with 10k+
            # tokens but the parent only needs a 1-2k summary.  Cap here to
            # prevent single sub-agent calls from bloating the orchestrator.
            raw_result = "\n".join(text_parts)
            if not raw_result:
                return f"({agent_name!r} returned an empty response)"
            if len(raw_result) > _MAX_SUB_RESULT_CHARS:
                trimmed = raw_result[:_MAX_SUB_RESULT_CHARS]
                last_nl = trimmed.rfind("\n")
                if last_nl > _MAX_SUB_RESULT_CHARS // 2:
                    trimmed = trimmed[:last_nl]
                _log.debug(
                    "executor.sub_agent_result_truncated",
                    agent=agent_name,
                    original=len(raw_result),
                    capped=_MAX_SUB_RESULT_CHARS,
                )
                return (
                    trimmed
                    + f"\n\n[Sub-agent result truncated to"
                    f" {_MAX_SUB_RESULT_CHARS} chars]"
                )
            return raw_result

    except Exception as exc:
        await _emit_sub_event({
            "type": "SUB_AGENT_ERROR",
            "agentName": agent_name,
            "runId": run_id,
            "error": str(exc),
        })
        return f"Sub-task to {agent_name!r} failed: {exc}"
    finally:
        try:
            from acb_common import publish_activity
            publish_activity(
                kind="agent", phase="end", agent=agent_name,
                run_id=run_id, source="delegation",
                delegated_by=_parent_agent,
                duration_ms=int((_time_sub.monotonic() - _sub_started) * 1000),
            )
        except Exception:
            pass
        # B6 Phase-5 Tier 0: tear down this sub-agent's scoped integration creds
        # so a delegated agent's secrets don't linger for the parent/next run.
        _release_run_credentials(_integration_env_token)
        # Restore orchestrator's artifact context so subsequent tool calls
        # (including write_artifact) target the correct workspace.
        if _saved_artifact_ctx:
            try:
                from acb_skills.write_artifact import \
                    _WRITE_ARTIFACT_CONTEXT
                for _key, _val in _saved_artifact_ctx.items():
                    if _val:
                        _WRITE_ARTIFACT_CONTEXT[_key] = _val
            except Exception:
                pass


def _custom_apps_root() -> Path:
    """Root directory that per-session workspaces must live under.

    Matches the Custom Apps workspace root used by the gateway apps routes:
    ``settings.custom_apps_root`` when configured, else
    ``{agents_clone_dir}/custom_apps``.
    """
    settings = get_settings()
    raw = str(getattr(settings, "custom_apps_root", "") or "")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path(settings.agents_clone_dir).expanduser() / "custom_apps").resolve()


def _session_workspace_override(
    thread_id: str | None, agent_config: dict[str, Any]
) -> str | None:
    """Per-session working-directory override for opt-in agents.

    An agent whose config sets ``"allow_session_workspace": true`` (the
    ``app-builder``) runs each chat session in the workspace bound to that
    session (``chat_session.workspace_path``, set by the Workshop UI via
    ``PATCH /agent/workspace/{session_id}``) instead of its own clone.

    Fail-closed containment: the stored path must resolve to an existing
    directory inside the Custom Apps root — anything else (traversal,
    symlink escape, stale path) falls back to the agent clone.
    """
    if not thread_id or not agent_config.get("allow_session_workspace"):
        return None
    workspace_path = ""
    try:
        from sqlalchemy import text
        _open = _graph_session_opener(thread_id)
        # flag ON + no resolvable tenant → fall back to the agent clone. flag OFF
        # → _open is the unbound get_session, byte-identical to before.
        if _open is None:
            return None
        with _open() as s:
            row = s.execute(
                text("SELECT workspace_path FROM chat_session WHERE id = :id"),
                {"id": thread_id},
            ).fetchone()
        if row and row.workspace_path:
            workspace_path = str(row.workspace_path)
    except Exception:
        return None
    if not workspace_path:
        return None
    try:
        resolved = Path(workspace_path).expanduser().resolve(strict=True)
        root = _custom_apps_root()
        if resolved.is_dir() and resolved != root and resolved.is_relative_to(root):
            return str(resolved)
    except OSError:
        pass
    _log.warning(
        "executor.session_workspace_rejected",
        thread_id=thread_id,
        workspace_path=workspace_path,
    )
    return None


def _resolve_agent_instance(
    agent_config: dict[str, Any], agent_name: str, actor: str,
) -> str:
    """The state-partition key for this run: ``''`` / ``u:<email>`` / ``t:<team>``.

    Resolved from the agent's declared ``sharing`` block
    (:class:`acb_skills.manifest.AgentManifest`), keyed by the acting member.
    Only a real email counts as an actor: background runs (cron, webhooks,
    the reconciler) and legacy ``user_id='default'`` payloads resolve to the
    shared partition rather than minting a ``u:default`` bucket nobody can
    reach again — the same asymmetry `_integration_authorizer` documents.
    Never raises; an unparseable config means shared, today's behaviour.
    """
    if "@" not in (actor or ""):
        return ""
    try:
        from acb_skills.manifest import AgentManifest
        manifest = AgentManifest.from_config(agent_config, name=agent_name)
        return manifest.instance_key(actor)
    except Exception:
        return ""


def _bind_run_instance(instance: str, run_id: str = "") -> None:
    """Top up this run's correlation context with its tenant partition (WS-6a).

    A SECOND, additive :func:`acb_common.bind_run_context` call rather than a
    moved one — deliberately. The run boundary binds run_id/thread_id/agent/
    user/source *before* ``load_agent`` so a failure DURING load is still
    correlated, but :func:`_resolve_agent_instance` cannot run until the config
    is loaded. Binding only ``instance`` here leaves every earlier field in
    place and keeps the blast radius to one key.

    ``''`` (the shared partition) binds nothing — bind_run_context only takes
    non-empty values — so a shared agent's context has no ``instance`` key,
    which is exactly how the store spells "shared" (migration 136). Never
    raises: attribution must not be able to fail a run.

    The live presence key is patched too, given *run_id*. It was written from
    the ``phase="start"`` event body, published before the load and therefore
    without an instance — so without this refresh ``active_runs()``,
    ``/observability/active`` and the office roster could never show a
    partition for ANY run. Patching the snapshot beats re-publishing a start
    event, which every stream consumer would read as a second activation.

    **Scope — this is the partition of the run that RESOLVED it.** A delegated
    sub-run (``_run_sub_agent_streaming``) resolves none and cannot unbind the
    caller's, so its events inherit the caller's ``instance`` while its blobs
    are written under the shared partition. Known asymmetry, recorded in
    ``specs/observability_e2.md`` §7 WS-6a; do not read the stamp as proof
    that a given event's blobs live in that partition.
    """
    try:
        from acb_common import bind_run_context
        bind_run_context(instance=instance)
    except Exception:
        pass
    if not instance or not run_id:
        return
    try:
        from acb_common import refresh_run_presence
        refresh_run_presence(run_id, instance=instance)
    except Exception:
        pass


def _resolve_effective_agent_dir(
    agent_dir: Path,
    agent_config: dict[str, Any],
    session_override: str | None = None,
    instance: str = "",
) -> str:
    """Resolve the effective working directory for an agent.

    By default this is the agent's clone directory.  If the agent config
    specifies ``workspace_root`` (optionally as an env-var reference like
    ``"$SOME_REPO_ROOT"``), that directory is used instead — provided it
    exists on disk.

    ``session_override`` (an already-validated per-session workspace from
    :func:`_session_workspace_override`) takes precedence over both.

    ``instance`` (a non-empty key from :func:`_resolve_agent_instance`) wins
    over ``workspace_root`` but not over the session override: a personal
    agent's run must land in the tenant's private state directory
    (:func:`acb_skills.agent_paths.agent_state_dir`) — pointing it at a
    shared external repo would reintroduce exactly the commingling the
    partition exists to prevent. The directory is created here because the
    run is about to write into it, and because callers treat the returned
    path as existing (push guard, mkdir of the visible folders, rehydrate).

    This lets an agent opt in to working on an external repo while its
    agent definition stays in its own clone, exactly like every other
    Copilot SDK agent.  When unset (the default), the agent operates in
    its own cloned repo directory.
    """
    if session_override:
        return session_override
    if instance:
        from acb_skills.agent_paths import ensure_state_dir
        # The clone dir's basename IS the agent name (loader clones with
        # clone_as=agent_name), so no separate parameter to drift from it.
        return str(ensure_state_dir(agent_dir.name, instance))
    raw = agent_config.get("workspace_root") or ""
    if not raw:
        return str(agent_dir)

    # Resolve $ENV_VAR references
    resolved = raw
    if raw.startswith("$"):
        var_name = raw[1:]
        resolved = os.environ.get(var_name, "")

    if resolved and Path(resolved).is_dir():
        return resolved

    # Fall back to the agent clone if the workspace_root is not available
    _log.debug(
        "executor.workspace_root_unavailable",
        configured=raw,
        resolved=resolved,
        fallback=str(agent_dir),
    )
    return str(agent_dir)


async def _maybe_sandbox_session_workspace(
    *,
    thread_id: str | None,
    session_ws: str | None,
    effective_agent_dir: str,
    agent_dir: Path,
    agent: Any,
    settings: Any,
) -> None:
    """BO-7 phase 2: containerize a session-workspace-override agent's Copilot
    CLI (the App Workshop app-builder — identified via the SAME
    ``allow_session_workspace`` hook :func:`_session_workspace_override` gates
    on, never a hardcoded agent name) when ``"app_builder"`` is in
    ``settings.copilot_sandbox_scope``.

    Sticky per-``thread_id`` reuse (``copilot_sandbox.get_or_spawn_sticky``) —
    a fresh container on every chat turn would regress the latency this
    interactive path doesn't have today.

    Mutates *agent* (``_sandbox_cli_url``, ``_default_options
    ["working_directory"]``) and ``_WRITE_ARTIFACT_CONTEXT
    ["permission_check_root"]`` in place on success. Off by default (empty
    scope); any spawn failure leaves *agent* untouched, falling back to the
    in-process ``working_directory`` the caller already set. Never raises.
    """
    try:
        from acb_skills.write_artifact import _WRITE_ARTIFACT_CONTEXT
        scope = str(getattr(settings, "copilot_sandbox_scope", "") or "")
        if not (
            session_ws
            and thread_id
            and "app_builder" in {s.strip() for s in scope.split(",") if s.strip()}
        ):
            _WRITE_ARTIFACT_CONTEXT.pop("permission_check_root", None)
            return

        from gateway.routes.apps._common import t2_vendor_dir

        from orchestrator.copilot_sandbox import CONTAINER_WORKSPACE, get_or_spawn_sticky

        # T2 builds run `node build_t2.mjs` — a host-absolute path already
        # baked into this agent's system prompt at import time
        # (agent-app-builder/agents.py). Mounting its own repo clone at the
        # IDENTICAL container path makes that baked-in path resolve correctly
        # with no prompt/agent changes. The vendor cache (react/react-dom/
        # esbuild) is mounted at a fresh container path and pointed to via
        # the same env var build_t2.mjs already reads on the host.
        abuilder_dir = str(agent_dir)
        sandbox = await get_or_spawn_sticky(
            thread_id=thread_id,
            workspace=effective_agent_dir,
            label="app_builder",
            settings=settings,
            extra_mounts={
                abuilder_dir: abuilder_dir,
                str(t2_vendor_dir()): "/opt/t2-vendor",
            },
            extra_env={"CUSTOM_APPS_T2_VENDOR_DIR": "/opt/t2-vendor"},
        )
        if sandbox is not None:
            agent._sandbox_cli_url = sandbox.cli_url
            agent._default_options["working_directory"] = CONTAINER_WORKSPACE
            _WRITE_ARTIFACT_CONTEXT["permission_check_root"] = CONTAINER_WORKSPACE
        else:
            _WRITE_ARTIFACT_CONTEXT.pop("permission_check_root", None)
    except Exception:
        pass


def _apply_agent_md_overrides(
    agents: list[Any],
    agent_dir: Path,
    agent_name: str,
) -> Any | None:
    """Honour ``.github/agents/<name>.agent.md`` for a loaded agent.

    Copilot SDK agents are wrapped inside MAF and author their identity in
    ``.github/agents/<name>.agent.md`` (instructions, model, tool affinity).
    Historically the runtime ignored that file and built the agent purely
    from ``agents.py`` / ``instructions.md``.  This applies the authored
    definition so a live chat (or any run) reflects it.

    Behaviour (per product decision):
      * **Instructions** — the inline markdown body *overrides* the repo's
        ``instructions.md`` content.  It replaces the ``system_message``
        content while preserving the SDK's append ``mode`` so the Copilot
        CLI base prompt is retained.  Called *before* tool injection so the
        platform-tools addendum still appends on top.
      * **Tools** — the frontmatter ``tools`` list uses VS Code Copilot's
        vocabulary. It stays additive (it never restricts the agent), but it is
        no longer inert: :func:`derive_tool_scope` maps those IDE names onto the
        platform tools that actually do the job, and the caller unions them into
        the injected scope via :func:`_merged_tool_scope`.
      * **Runtime note** — a VS-Code-authored body points at affordances that do
        not exist headless (Problems panel, ``#codebase``, the editor UI). We do
        NOT rewrite the author's prose; we append a short note reconciling the
        IDE tool names with the real ones.
      * **MCP** — ``.vscode/mcp.json`` / ``.mcp.json`` are honoured. They were
        never read before, so an agent that declares a server it depends on
        (e.g. a diagramming agent declaring draw.io) silently lost it.

    Returns the parsed :class:`AgentMd` (so the caller can fold its ``model``
    into the model-priority chain and derive the tool scope), or ``None`` when
    no usable file exists. Fully defensive — never raises.
    """
    try:
        from acb_skills.agent_md import (
            load_agent_md,
            load_repo_mcp_servers,
            runtime_note_for,
        )
        spec = load_agent_md(agent_dir, agent_name)
    except Exception as exc:
        _log.debug("executor.agent_md_load_failed", agent=agent_name, error=str(exc))
        return None

    # Repo-declared MCP servers stand on their own — an agent can depend on one
    # without having an .agent.md at all.
    try:
        _repo_mcp = load_repo_mcp_servers(agent_dir)
    except Exception:
        _repo_mcp = {}
    if _repo_mcp:
        from orchestrator._tool_injection import merge_mcp_servers
        for _ag in agents:
            # override=False: the DB registry runs later and outranks the repo.
            merge_mcp_servers(_ag, _repo_mcp, override=False)
        _log.info(
            "executor.agent_md_repo_mcp_applied",
            agent=agent_name, servers=sorted(_repo_mcp),
        )

    if spec is None:
        return None

    _note = runtime_note_for(spec.tools)
    if spec.body:
        _body = spec.body + _note
        for _ag in agents:
            try:
                opts = getattr(_ag, "_default_options", None)
                if isinstance(opts, dict):
                    # Preserve the SDK's system_message mode (default "append").
                    prev = opts.get("system_message")
                    mode = prev.get("mode", "append") if isinstance(prev, dict) else "append"
                    opts["system_message"] = {"mode": mode, "content": _body}
                # Pure-MAF agents expose ``instructions`` directly.
                if hasattr(_ag, "instructions"):
                    try:
                        _ag.instructions = _body
                    except (AttributeError, TypeError):
                        pass
            except Exception:
                pass

    _log.info(
        "executor.agent_md_applied",
        agent=agent_name,
        source=str(spec.path) if spec.path else None,
        model=spec.model,
        tools_advisory=spec.tools,
        body_chars=len(spec.body),
        vscode_normalized=bool(_note),
    )
    return spec


def _merged_tool_scope(
    config_scope: list[str] | None, spec: Any | None,
) -> list[str] | None:
    """config.json ``tool_scope`` UNION the scope implied by ``.agent.md tools``.

    Returns ``None`` (inject everything) whenever config.json set no scope: a
    VS Code ``tools:`` list must be able to WIDEN an already-narrowed agent, but
    must never NARROW an agent that was never scoped in the first place —
    otherwise adding an .agent.md would silently strip tools the agent had.
    """
    if not config_scope:
        return None
    derived: list[str] = []
    if spec is not None:
        try:
            from acb_skills.agent_md import derive_tool_scope
            derived = derive_tool_scope(getattr(spec, "tools", []) or [])
        except Exception:
            derived = []
    return list(dict.fromkeys(list(config_scope) + derived))


# The ONLY tier aliases the gateway /v1 actually resolves (see
# v1_compat._TIER_NAME_TO_ID). A bare ``tier…`` name that is NOT one of these
# (e.g. ``tier1-local-qwen3``) is NOT gateway-routable — litellm would 400 with
# "LLM Provider NOT provided" — so it must be treated as unknown and coerced to
# the safe default by _byok_default_model, not passed through.
# BYOK model resolution extracted to orchestrator/_model_resolution.py
# (maintainability refactor). Re-exported so run-path call sites +
# gateway.main / test_byok_default keep resolving as executor.<name>.
from orchestrator._model_resolution import (
    _apply_byok_provider_for_copilot_sdk,
    _apply_model_for_maf_agent,
    _apply_thinking_mode_for_agent,
    _byok_default_model,
    _is_gateway_model,
)
async def _get_current_head(agent_dir: str) -> str:
    """Return the current HEAD SHA of *agent_dir*, or '' on error."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=agent_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return out.decode(errors="replace").strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


async def _commit_on_remote(agent_dir: str, commit_sha: str) -> bool:
    """True if *commit_sha* is already on a remote (the agent pushed it).

    A successful ``git push`` updates the local ``origin/<branch>`` tracking ref,
    so ``git branch -r --contains <sha>`` lists a remote ref when the commit was
    pushed. Used to auto-approve a commit the user told the agent to push from
    chat (it lands on origin, so it needs no separate Control-Plane approval).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "branch", "-r", "--contains", commit_sha,
            cwd=agent_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0 and bool(out.decode(errors="replace").strip())
    except Exception:
        return False


async def _install_push_guard(agent_dir: str) -> None:
    """Install git hooks for commit-gate workflow.

    Installs two hooks (idempotent — skips if already present):

    1. **pre-push**: Rejects all direct pushes.  The human-approval gateway
       endpoint handles the push, or the operator may tell the agent to
       ``git push --no-verify`` from chat to bypass this hook explicitly.

    2. **post-commit**: Appends the new commit SHA to ``.git/cc-commits-queue``.
       The post-run commit scanner reads this file to detect commits made
       *during* a chat session (before ``run_agent`` returns) so they appear
       in the Self Mutation Commits UI immediately after the run.

    Non-fatal — if any hook write fails, execution continues; the post-run
    commit scan (including catch-up) still catches new commits.
    """
    try:
        hooks_dir = Path(agent_dir) / ".git" / "hooks"
        if not hooks_dir.is_dir():
            return
        # --- pre-push hook ---
        pre_push = hooks_dir / "pre-push"
        if not pre_push.exists():
            pre_push.write_text(
                "#!/bin/sh\n"
                "echo 'Direct push blocked: commits are queued for human approval'\n"
                "echo 'Approve via the Metorite Control Plane inbox, or tell the agent you approve and it will push with --no-verify.'\n"
                "exit 1\n",
                encoding="utf-8",
            )
            pre_push.chmod(0o755)
        # --- post-commit hook ---
        post_commit = hooks_dir / "post-commit"
        if not post_commit.exists():
            post_commit.write_text(
                "#!/bin/sh\n"
                "# Append the new commit SHA to the queue file.\n"
                "# The executor reads this file at end-of-run to register commits\n"
                "# that were made *during* the chat session.\n"
                "echo \"$(git rev-parse HEAD)\" >> \"$(git rev-parse --git-dir)/cc-commits-queue\"\n",
                encoding="utf-8",
            )
            post_commit.chmod(0o755)
    except Exception as exc:
        _log.warning("executor.push_guard_install_failed", agent=agent_dir, error=str(exc))


async def _detect_agent_commits(
    agent_name: str,
    agent_dir: str | None,
    run_id: str,
    *,
    since_sha: str | None = None,
    thread_id: str | None = None,
) -> None:
    """After an agent run, register any new commits for inbox approval.

    Detection strategy (layered — catches orphaned commits from prior runs):

    0. **Queue-file scan** (real-time, fastest path): reads
       ``.git/cc-commits-queue`` written by the post-commit hook.
       Dequeues all SHAs, deduplicates them, registers any that are not
       already in ``pending_commit``, then truncates the file.  This
       catches commits made *during* the chat session so they appear in
       the Self Mutation Commits UI immediately after the run.

    1. **Since-sha scan**: detects commits made during THIS run
       (``git log {since_sha}..HEAD``).  This catches all new commits
       that may have been missed by the post-commit hook.

    2. **Catch-up scan** (always runs): scans the last 50 commits and
       registers any missing from ``pending_commit``.  This recovers
       commits orphaned by a previous detection failure, DB outage, or
       gateway restart.

    All phases are deduplicated against ``pending_commit.commit_sha``
    so no commit is ever registered twice.

    Called for **all** agents (not just github-copilot).  MAF agents that
    do not commit simply produce empty scans.

    Non-fatal — any subprocess or DB error is logged and swallowed.
    """
    if not agent_dir:
        return

    # Resolve the pending_commit session opener ONCE, HERE — on the event-loop
    # frame where _RUN_ORG[thread_id] is still THIS run's org (this runs before
    # run_agent_stream's finally pops it) and no worker-thread / ContextVar hop
    # has intervened (WS-29 acb_graph slice 4, the slice-3 discipline). Reused for
    # BOTH the dedup read and every _register_pending_commit write below, so the
    # tenant decision is made exactly once per detection pass:
    #   * flag OFF               → the unbound get_session — byte-identical.
    #   * flag ON + a tenant     → lambda: tenant_session(org).
    #   * flag ON + NO tenant    → None → fail closed: the read falls back to an
    #                              empty dedup set, the writes SKIP + log.
    _pc_open = _graph_session_opener(thread_id)

    try:
        # ── Load existing commit SHAs for dedup ─────────────────────────
        _existing_shas: set[str] = set()
        try:
            from sqlalchemy import text as _txt
            # flag ON + no resolvable tenant (_pc_open is None) → skip the
            # FORCE-RLS'd read and fall back to an empty dedup set. That never
            # duplicates rows: the writes below also fail-closed-skip when the
            # opener is None, so nothing is registered on this pass anyway. flag
            # OFF → _pc_open is the unbound get_session, byte-identical to before.
            if _pc_open is not None:
                with _pc_open() as _s:
                    _rows = _s.execute(
                        _txt(
                            "SELECT commit_sha FROM pending_commit "
                            "WHERE agent_name = :a"
                        ),
                        {"a": agent_name},
                    ).fetchall()
                    _existing_shas = {r[0] for r in _rows}
        except Exception:
            pass

        all_lines: list[str] = []

        # ── Phase 0: queue-file scan (post-commit hook) ─────────────────
        # The post-commit hook appends each new SHA to this file.  We read
        # it, deduplicate, register any unseen SHAs, then truncate so they
        # are not re-registered on the next scan.
        queue_file = Path(agent_dir) / ".git" / "cc-commits-queue"
        if queue_file.exists():
            try:
                raw = queue_file.read_text(encoding="utf-8").strip()
                if raw:
                    queue_shas: list[str] = []
                    seen_queue: set[str] = set()
                    for line in raw.splitlines():
                        sha = line.strip()
                        # Must be a full 40-char SHA
                        if sha and len(sha) == 40 and sha not in seen_queue:
                            seen_queue.add(sha)
                            if sha not in _existing_shas:
                                queue_shas.append(sha)
                    if queue_shas:
                        _log.info(
                            "executor.commits_queue_file",
                            agent=agent_name,
                            count=len(queue_shas),
                        )
                        # Fetch the full message for each queued SHA inline.
                        for sha in queue_shas:
                            _existing_shas.add(sha)
                            msg = ""
                            try:
                                p = await asyncio.create_subprocess_exec(
                                    "git", "log", "-1", "--format=%s", sha,
                                    cwd=agent_dir,
                                    stdout=asyncio.subprocess.PIPE,
                                    stderr=asyncio.subprocess.DEVNULL,
                                )
                                out, _ = await asyncio.wait_for(p.communicate(), timeout=5)
                                msg = out.decode(errors="replace").strip()
                            except Exception:
                                msg = sha[:12]
                            all_lines.append(f"{sha}|{msg}")
                # Truncate so SHAs are not re-registered on next scan.
                queue_file.write_text("", encoding="utf-8")
            except Exception as exc:
                _log.warning(
                    "executor.commit_scan_queue_failed",
                    agent=agent_name, error=str(exc),
                )

        # ── Phase 1: since-sha scan (this run's commits) ────────────────
        if since_sha:
            proc = await asyncio.create_subprocess_exec(
                "git", "log", f"{since_sha}..HEAD", "--format=%H|%s",
                cwd=agent_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=10,
            )
            if proc.returncode == 0:
                phase1 = stdout_bytes.decode(errors="replace").strip()
                if phase1:
                    all_lines.extend(phase1.splitlines())
                    _log.info(
                        "executor.commits_since_sha",
                        agent=agent_name, count=len(phase1.splitlines()),
                    )
            else:
                _log.debug(
                    "executor.since_sha_failed",
                    agent=agent_name,
                    stderr=stderr_bytes.decode(errors="replace")[:200],
                )

        # ── Phase 2: catch-up scan (orphaned from prior runs) ───────────
        # Scans the last 50 commits in the repo and registers any that
        # are missing from pending_commit.  This recovers commits that were
        # missed by a previous detection attempt (silent exception, DB
        # outage, gateway restart) OR were pushed before the push guard
        # was installed.  Capped at 50 to keep it fast; older history is
        # assumed already reviewed or irrelevant.
        catchup_proc = await asyncio.create_subprocess_exec(
            "git", "log", "HEAD", "-n", "50", "--format=%H|%s",
            cwd=agent_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        catchup_out, _ = await asyncio.wait_for(
            catchup_proc.communicate(), timeout=10,
        )
        if catchup_proc.returncode == 0:
            catchup_text = catchup_out.decode(errors="replace").strip()
            if catchup_text:
                # Merge with Phase 1, dedup by SHA, skip already-registered
                seen = {ln.split("|", 1)[0].strip() for ln in all_lines}
                new_from_catchup = 0
                # ── System-generated commit messages to skip ──────────
                # (duplicated from the registration loop below so the
                # catch-up scan doesn't even log them as "recovered".)
                _CATCHUP_SKIP_PREFIXES = (
                    "initial: seeded from local source",
                )
                for ln in catchup_text.splitlines():
                    sha = ln.split("|", 1)[0].strip()
                    msg = ln.split("|", 1)[1].strip() if "|" in ln else ""
                    if (
                        sha and sha not in seen
                        and sha not in _existing_shas
                        and not msg.lower().startswith(_CATCHUP_SKIP_PREFIXES)
                    ):
                        all_lines.append(ln)
                        seen.add(sha)
                        new_from_catchup += 1
                if new_from_catchup:
                    _log.info(
                        "executor.commits_catchup",
                        agent=agent_name,
                        new_count=new_from_catchup,
                        hint=(
                            "Recovered orphaned commits from prior runs "
                            "(pushed before guard or missed by detector)."
                        ),
                    )

        if not all_lines:
            return  # nothing new

        _log.info(
            "executor.agent_commits_detected",
            agent=agent_name,
            run_id=run_id,
            count=len(all_lines),
        )

        from orchestrator.mutation import _git_diff
        from orchestrator.mutation import _register_pending_commit

        # ── System-generated commit messages to skip ──────────────────
        # These are infrastructure commits (initial clones, auto-seeds,
        # sync baselines) that should never surface for human approval.
        _SYSTEM_COMMIT_PREFIXES = (
            "initial: seeded from local source",
        )

        for raw_line in all_lines:
            parts = raw_line.split("|", 1)
            commit_sha = parts[0].strip()
            commit_message = (
                parts[1].strip() if len(parts) > 1 else commit_sha[:8]
            )
            if not commit_sha or commit_sha in _existing_shas:
                continue

            # Skip system-generated baseline commits — these are infra
            # artefacts, not agent-authored changes worth reviewing.
            if commit_message.lower().startswith(_SYSTEM_COMMIT_PREFIXES):
                _log.debug(
                    "executor.skip_system_commit",
                    agent=agent_name,
                    commit_sha=commit_sha[:8],
                    commit_message=commit_message[:80],
                )
                continue

            # Capture the diff for inline review
            diff_text = await _git_diff(agent_dir, commit_sha)

            # If the agent already pushed this commit (the user told it to
            # "commit and push" in chat, so it ran `git push --no-verify`), it's
            # on origin — record it as already-approved instead of queuing it for
            # a redundant Control-Plane approval.
            pushed = await _commit_on_remote(agent_dir, commit_sha)

            await _register_pending_commit(
                agent_name=agent_name,
                run_id=run_id,
                local_clone_dir=agent_dir,
                commit_sha=commit_sha,
                commit_message=commit_message,
                diff_text=diff_text,
                test_summary="(agent self-improvement — no test run)",
                status="approved" if pushed else "pending",
                reviewed_by="chat:autopush" if pushed else None,
                # WS-29 slice 4: the tenant opener resolved on the event-loop
                # frame above (flag OFF → get_session; ON+org → tenant_session;
                # ON+no-org → None → the write fails closed + logs).
                opener=_pc_open,
            )

        record(
            AuditEvent(
                actor=f"agent:{agent_name}",
                action="agent_self_commit_detected",
                target=f"agent:{agent_name}",
                payload={
                    "run_id": run_id,
                    "commit_count": len(all_lines),
                    "commits": [
                        ln.split("|", 1)[0].strip()[:12]
                        for ln in all_lines
                    ],
                },
                # WS-29 acb_graph slice 5: stamp the run's tenant ONTO the event
                # on THIS frame (never a ContextVar inside _persist's worker
                # thread). None ⇒ tenant-less run → _persist binds the operator
                # org so the audit row is retained. _RUN_ORG.get(None) is None.
                organization_id=_RUN_ORG.get(thread_id),
            )
        )

    except Exception as exc:
        _log.warning(
            "executor.detect_commits_failed",
            agent=agent_name,
            run_id=run_id,
            error=str(exc),
        )


async def _integration_authorizer(event_payload: Any, thread_id: str | None = None):
    """Build the per-run integration filter for the run's authority, or None.

    Org access control (spec §5, seam 3). An agent's ``config.json`` declares
    which integrations it *wants*; this decides which the caller may actually
    hand it, so a member denied `integrations:use:zoho-crm` cannot reach Zoho
    by picking an agent that declares it.

    In a shared session the deciding authority is not the typer alone: it is
    the **intersection of every participant's access**
    (``groups_sessions_authority.md`` §3, via ``resolve_session_access``) —
    otherwise the permitted member's Zoho output lands in a transcript the
    denied member reads. A solo session resolves to exactly the actor's own
    access, so nothing changes until a second participant exists.

    Returns ``None`` — meaning no filtering, the pre-org-access-control
    behaviour — whenever the run is **not** attributable to an active member:

    * no ``user_email`` in the payload (cron, reconciler, webhook-triggered
      runs: the platform acting on its own behalf, not a person's);
    * a synthetic principal such as ``system:internal``;
    * an address that does not resolve to an ACTIVE member.

    That last case is the important one. This feature exists to restrict a
    known member's reach — it must never become a new way for a background
    run to silently lose its credentials because the address in the payload
    happened not to be a provisioned member. Who may start a run at all is
    already enforced upstream by ``assert_can_run_agent``, so an
    unidentifiable address here means "platform-initiated", not "attacker".
    """
    email = ""
    if isinstance(event_payload, dict):
        email = str(
            event_payload.get("user_email") or event_payload.get("user_id") or ""
        ).strip()
    if not email or ":" in email:
        return None

    try:
        from acb_auth import (
            integration_use_permission,
            resolve_access,
            resolve_session_access,
        )

        access = await resolve_access(email)
        if not access.is_active:
            return None

        # Shared-session fold: the intersection REPLACES the actor's access
        # only when a second member actually exists — a solo session keeps
        # the object resolved above, byte-identically.
        if thread_id:
            folded, members = await resolve_session_access(thread_id, email)
            if len(members) > 1:
                access = folded
                # The cap must be visible, never silent: name whose presence
                # narrows the run, so "it worked yesterday" is answerable.
                _log.info(
                    "executor.session_authority_intersected",
                    thread_id=thread_id, actor=email, members=members,
                )

        # Same resolution also arms the memory tools' permission gate, so
        # org-memory writes are checked against the same authority. Done here
        # so there is exactly one access lookup per run.
        try:
            from acb_skills.memory_tools import (
                _set_memory_permission,
            )

            _set_memory_permission(access.has)
        except Exception:
            pass

        return lambda service: access.has(integration_use_permission(service))
    except Exception as exc:
        # resolve_access is documented never to raise, so reaching here is a
        # bug rather than a misconfiguration. Preserve the prior behaviour
        # rather than failing every integration on a transient fault.
        _log.warning("executor.integration_authorizer_failed", error=str(exc))
        return None


def _payload_user(event_payload: Any) -> str:
    """The acting user this payload names, or ``""`` when it names nobody.

    ``""`` is a real answer, not a missing one — see :func:`_bind_run_identity`.
    """
    if not isinstance(event_payload, dict):
        return ""
    return str(
        event_payload.get("user_email") or event_payload.get("user_id") or ""
    )


def _bind_run_identity(event_payload: Any, agent_name: str = "") -> Any:
    """Open this run's acting-user scope; hand back what closes it.

    One helper for both executors so the two paths cannot drift — they did
    before, and the drift is invisible until somebody reads the other one.
    """
    try:
        from acb_skills.memory_tools import (
            _bind_memory_user_id,
            _get_memory_user_id,
        )
        binding = _bind_memory_user_id(_payload_user(event_payload))
        if not _get_memory_user_id():
            # Not an error: a platform run (cron, reconciler, an event with no
            # person behind it) legitimately has nobody. Logged because the
            # consequence is otherwise invisible — the gateway-calling tools
            # will refuse, and "the agent said it had nobody to act as" has to
            # be answerable without a debugger. It used to be answerable the
            # wrong way, by acting as whoever ran last.
            _log.info("executor.run_has_no_acting_user", agent=agent_name)
        return binding
    except Exception:
        return None


def _unbind_run_identity(binding: Any) -> None:
    """Close a :func:`_bind_run_identity` scope. Never raises."""
    try:
        from acb_skills.memory_tools import _unbind_memory_user_id
        _unbind_memory_user_id(binding)
    except Exception:
        pass


async def run_agent(
    agent_name: str,
    event_payload: dict[str, Any],
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    model: str | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """Dynamically load and execute a named agent.

    Thin wrapper over :func:`_run_agent_inner` whose only job is to open and —
    crucially — CLOSE this run's acting-user scope, so the identity cannot
    outlive the run on a caller's task. A wrapper rather than a ``try/finally``
    around the body because the body is three hundred lines and its own
    ``except`` re-raises; the boundary belongs where it is impossible to miss.

    Args:
        agent_name:    Bare agent name, e.g. ``"task-manager"``.
        event_payload: Arbitrary event data injected as the initial state.
        run_id:        Unique execution ID (auto-generated if ``None``).
        thread_id:     Conversation thread ID (defaults to ``"{agent_name}:{run_id}"``).
        organization_id: The caller's tenant, threaded through for the
                       worker-thread ``acb_graph`` writes (WS-29 MT-1d / H4).
                       HONOURED as of slice 6a: ``_run_agent_inner`` sets
                       ``_RUN_ORG[thread_id]`` + ``bind_tenant`` from it. A
                       sub-agent inherits its parent's tenant (see
                       ``_run_sub_agent_streaming``); the workflow / schedule
                       callers resolve their own org in slice 6c and pass
                       ``None`` until then (operator-org fallback / skip).

        The final MAF agent result dict.

    Raises:
        :class:`AgentRunError` on failure (includes mutation PR URL if one was opened).
    """
    _identity = _bind_run_identity(event_payload, agent_name)
    try:
        return await _run_agent_inner(
            agent_name, event_payload,
            run_id=run_id, thread_id=thread_id, model=model,
            organization_id=organization_id,
        )
    finally:
        _unbind_run_identity(_identity)


async def _run_agent_inner(
    agent_name: str,
    event_payload: dict[str, Any],
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    model: str | None = None,
    # HONOURED as of slice 6a — see the tenant-binding block below.
    organization_id: str | None = None,
) -> dict[str, Any]:
    """The batch run itself. Call :func:`run_agent`, not this — this one assumes
    the acting-user scope is already open.

    WS-29 acb_graph slice 6a: the tenant-binding block below sets
    ``_RUN_ORG[thread_id]`` + ``bind_tenant`` from ``organization_id`` (cleared
    in the ``finally``), exactly as ``run_agent_stream`` does for chat, so the
    slice 3-5 ``acb_graph`` writes carry the tenant for org-carrying batch runs
    (sub-agent dispatch today; workflow / schedule callers pass their own org in
    slice 6c). When ``organization_id`` is ``None`` the run stays byte-identical
    to pre-slice behaviour (operator-org audit fallback / flag-ON write skip).
    """
    _disable_agent_telemetry_once()
    settings = get_settings()
    run_id = run_id or str(uuid.uuid4())
    thread_id = thread_id or f"{agent_name}:{run_id}"

    # ── Tenant binding for this batch run's writes (WS-29 acb_graph slice 6a) ──
    # The batch path now HONOURS the ``organization_id`` its callers pass, exactly
    # as ``run_agent_stream`` does for the chat path (slice 2): set the run-keyed
    # ``_RUN_ORG`` — what the SYNC ``acb_graph`` worker-thread writes read AFTER
    # the ``loop.run_in_executor`` hop, where contextvars do not propagate — and
    # ``bind_tenant`` this run's own event loop, so async reads and the
    # ``acb_audit`` fallback resolve the tenant. The org is resolved SERVER-SIDE by
    # the caller (a sub-agent inherits its PARENT run's tenant — see
    # ``_run_sub_agent_streaming`` / ``_resolve_sub_agent_org``) and passed in;
    # it is NEVER read from ``event_payload``, which is agent/client-visible and
    # would be a tenant-spoofing hole (R11, user_management_contract.md).
    #
    # When the caller passes ``None`` (the workflow cron scheduler / schedule
    # sweep until slice 6c, or the self-mutation sandbox) this stays on the
    # pre-slice behaviour: no bind, so with the flag ON the ``chat_session`` /
    # ``pending_commit`` writes fail closed (skip) and the ``audit_event`` write
    # falls back to the operator org (slice 5). LOG the gap for every source so it
    # is VISIBLE (WS-29 slice 3, P1) — same signal the stream path emits.
    _batch_source = ""
    if isinstance(event_payload, dict):
        _batch_source = str(event_payload.get("source") or "").strip()
    _tenant_token = None
    if organization_id:
        _RUN_ORG[thread_id] = organization_id
        try:
            from acb_common.db import bind_tenant
            _tenant_token = bind_tenant(organization_id)
        except Exception:
            _tenant_token = None
    else:
        _log.warning(
            "executor.run_missing_org",
            agent=agent_name, thread_id=thread_id,
            source=_batch_source or "batch",
        )
    # TODO(WS-29 slice 6b): inbound webhooks (WhatsApp/ClickUp) + email-automation
    # chat need a mailbox/account→org resolver over an RLS-scoped table before they
    # can pass organization_id here (exempt-resolver design). TODO(WS-29 slice 6c):
    # the workflow cron scheduler / orphan reconciler / schedule sweep resolve the
    # owning record's org and pass it in.

    record(
        AuditEvent(
            actor="system:gateway",
            action="agent_run_start",
            target=f"agent:{agent_name}",
            payload={"run_id": run_id, "event_keys": list(event_payload.keys())},
            # WS-29 acb_graph slice 5: the run's tenant, captured on this frame.
            # Slice 6a: the batch path now sets _RUN_ORG above when its caller
            # supplies an org (sub-agent inheritance), so an org-carrying batch
            # run stamps its real tenant here instead of the operator fallback.
            organization_id=_RUN_ORG.get(thread_id),
        )
    )

    try:
        _effective_agent_dir: str | None = None

        # Look up optional repo_name override from the gateway's agent registry.
        # This allows repos not following the "agent-{name}" naming convention
        # (e.g. FracktalWorks/sales-prospector instead of agent-sales-prospector).
        # Checks dynamic agents (agents.json) first, then falls back to static registry.
        _registry_repo_name: str | None = None
        _registry_local_path: str | None = None
        try:
            from gateway.routes.agent import _AGENT_REGISTRY
            from gateway.routes.agent import _load_dynamic_agents
            _all_entries = _load_dynamic_agents() + _AGENT_REGISTRY
            _registry_entry = next(
                (e for e in _all_entries if e["name"] == agent_name), None
            )
            if _registry_entry:
                raw_repo = _registry_entry.get("repo_name") or ""
                # repo_name may be stored as "owner/repo" (full slug from registration)
                # or just "repo". Pass the full slug — load_agent splits when needed.
                _registry_repo_name = raw_repo if raw_repo else None
                _registry_local_path = _registry_entry.get("local_path")
        except ImportError:
            pass

        with load_agent(
            agent_name,
            run_id=run_id,
            repo_name=_registry_repo_name,
            local_path=_registry_local_path,
        ) as loaded:
            # ── Resolve effective workspace directory ──────────────────
            # Agents may optionally specify workspace_root in config.json to
            # work on an external repo.  All directory operations — push
            # guard, HEAD capture, working directory, commit detection — use
            # this resolved path so they stay consistent.  When unset, the
            # agent operates in its own clone directory.
            # A personal/team agent instead lands in its tenant state dir —
            # the same key its blob-store rows carry (migration 136).
            _agent_instance = _resolve_agent_instance(
                loaded.config, agent_name,
                str(
                    event_payload.get("user_email")
                    or event_payload.get("user_id") or ""
                ) if isinstance(event_payload, dict) else "",
            )
            _effective_agent_dir = _resolve_effective_agent_dir(
                loaded.agent_dir, loaded.config,
                session_override=_session_workspace_override(
                    thread_id, loaded.config,
                ),
                instance=_agent_instance,
            )

            # For GitHub Copilot SDK agents: install the push guard (prevents
            # direct pushes; commits stay local until operator approves) and
            # record the current HEAD so we can detect new commits after the run.
            _head_before: str = ""
            _is_copilot_agent = False
            try:
                from gateway.routes.agent import _AGENT_REGISTRY
                from gateway.routes.agent import \
                    _load_dynamic_agents as _lda
                _ea = next(
                    (e for e in _lda() + _AGENT_REGISTRY if e["name"] == agent_name),
                    None,
                )
                if _ea and _ea.get("agent_runtime") == "github-copilot":
                    _is_copilot_agent = True
                # Install push guard + capture HEAD for ALL agents (not just
                # github-copilot).  MAF agents may also generate commits during
                # a run, and the guard protects against unapproved pushes.
                await _install_push_guard(_effective_agent_dir)
                _head_before = await _get_current_head(_effective_agent_dir)
            except Exception:
                pass

            # Resolve credentials for both mandatory and optional integrations.
            # Never raises — partial configs are fine.  Missing integrations are
            # passed to the agent via integration_warnings so it can inform the
            # user at tool-call time rather than blocking the entire run.
            mandatory_integrations: list[str] = loaded.config.get("integrations", [])
            optional_integrations: list[str] = loaded.config.get("optional_integrations", [])
            integrations, integration_warnings = build_integrations(
                mandatory_integrations, optional_integrations, settings,
                is_authorized=await _integration_authorizer(event_payload, thread_id),
            )
            if integration_warnings:
                _log.warning(
                    "executor.integrations_partial",
                    agent=agent_name,
                    run_id=run_id,
                    unavailable=list(integration_warnings.keys()),
                )

            agents = loaded.build_agents()
            # Honour .github/agents/<name>.agent.md (instructions override).
            _apply_agent_md_overrides(agents, loaded.agent_dir, agent_name)
            _apply_own_tool_scope(
                agents, loaded.config.get("own_tool_scope") or None,
            )
            _inject_agent_tools(
                agents,
                tool_scope=loaded.config.get("tool_scope") or None,
                agent_name=agent_name,
            )  # inject call_agent / call_agent_background

            # Set write_artifact context + ensure visible workspace dirs exist.
            try:
                from acb_skills.write_artifact import \
                    _WRITE_ARTIFACT_CONTEXT
                _WRITE_ARTIFACT_CONTEXT["session_id"] = thread_id or run_id
                _WRITE_ARTIFACT_CONTEXT["agent_name"] = agent_name
                _WRITE_ARTIFACT_CONTEXT["run_id"] = run_id
                _WRITE_ARTIFACT_CONTEXT["workspace_root"] = _effective_agent_dir
                # The blob-store partition every write-through must carry —
                # keeping disk and store on the SAME tenant key (migration 136).
                _WRITE_ARTIFACT_CONTEXT["instance"] = _agent_instance
                # Declared+resolved integrations for this run — read by
                # list_integrations (discoverability) and code_tools
                # (_script_env grants a script exactly these creds).
                _WRITE_ARTIFACT_CONTEXT["integrations"] = sorted(integrations)
                _WRITE_ARTIFACT_CONTEXT["integration_warnings"] = dict(
                    integration_warnings
                )
                _WRITE_ARTIFACT_CONTEXT["gateway_url"] = str(
                    getattr(settings, "gateway_base_url", "http://127.0.0.1:8000")
                )
                _WRITE_ARTIFACT_CONTEXT["gateway_token"] = str(
                    getattr(settings, "gateway_internal_token", "")
                    or getattr(settings, "litellm_master_key", "")
                    or "sk-local"
                )
                _ws_root = Path(_effective_agent_dir)
                for _d in ("inputs", "outputs", "agent-data"):
                    (_ws_root / _d).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            # Rehydrate the agent's durable folders from the authoritative blob
            # store BEFORE it runs, so a wiped/migrated volume comes back (store
            # is source of truth, disk is a cache). Best-effort; never blocks.
            # The instance selects WHICH partition — restoring the shared one
            # into a tenant dir would put other people's notes in this run.
            try:
                from acb_memory import rehydrate_workspace
                await rehydrate_workspace(
                    agent_name, _effective_agent_dir, instance=_agent_instance,
                )
            except Exception:
                pass

            # Skills-as-an-index (WS-23 successor / QM-2): when
            # SKILLS_INDEX_ONLY is on, the addendum is a one-line-per-family
            # index and the full guidance lands in agent-data/skills/*.md for
            # recall_notes to fetch on demand. Written AFTER the rehydrate so
            # a restore can never overwrite a freshly rendered body.
            # No-op (and no filesystem touch at all) while the switch is off.
            materialize_skill_bodies_for_agent(
                agent_name, _effective_agent_dir,
                tool_scope=loaded.config.get("tool_scope") or None,
            )

            # ── Set working directory for Copilot SDK agents ────────────
            # The Copilot SDK CLI defaults to the gateway CWD unless
            # working_directory is explicitly set.  Point it at the agent's
            # effective workspace (clone dir or workspace_root) so shell
            # commands, file I/O, AGENTS.md, and skill resolution all work.
            if _is_copilot_agent:
                for _ag in agents:
                    try:
                        if (
                            hasattr(_ag, "_default_options")
                            and _ag._default_options is not None
                        ):
                            _ag._default_options["working_directory"] = (
                                _effective_agent_dir
                            )
                    except Exception:
                        pass

            # Resolve + apply the run model to the agent (batch path).
            #  - Copilot-SDK agents: pin to gateway /v1 (BYOK) + set model so
            #    agent.run() routes through litellm (no native Copilot 402).
            #  - Native MAF agents: _apply_byok_… is a no-op, so set
            #    default_options["model"] here — the MAF client otherwise keeps
            #    its build-time model and ignores the requested/inherited tier.
            if agents:
                _agent0 = agents[0]
                _cfg_tier = (loaded.config.get("model_tier") or "")
                try:
                    # Copilot-SDK agents: pin gateway /v1 (BYOK) + model.
                    _apply_byok_provider_for_copilot_sdk(
                        _agent0, model or "", settings,
                        agent_model_tier=_cfg_tier,
                    )
                    # Native MAF agents: set default_options["model"] so the
                    # requested/inherited tier is honoured (no-op for Copilot SDK).
                    _apply_model_for_maf_agent(
                        _agent0, model or "", settings,
                        agent_model_tier=_cfg_tier,
                    )
                except Exception as _be:
                    _log.warning("executor.byok_apply_failed",
                                 agent=agent_name, error=str(_be)[:160])

            final_state = await _run_with_maf_agent(
                agents,
                agent_name=agent_name,
                run_id=run_id,
                thread_id=thread_id,
                event_payload={
                    **event_payload,
                    "integration_warnings": integration_warnings,
                },
                integrations=integrations,
            )

        record(
            AuditEvent(
                actor=f"agent:{agent_name}",
                action="agent_run_complete",
                target=f"agent:{agent_name}",
                payload={
                    "run_id": run_id,
                    "result_keys": list(final_state.keys()),
                },
                # WS-29 acb_graph slice 5: the run's tenant, captured here.
                organization_id=_RUN_ORG.get(thread_id),
            )
        )
        # Post-run: detect commits the agent made during this run (ALL agents)
        _registry_runtime = "maf"
        try:
            from gateway.routes.agent import _AGENT_REGISTRY
            from gateway.routes.agent import _load_dynamic_agents
            _e = next(
                (e for e in _load_dynamic_agents() + _AGENT_REGISTRY if e["name"] == agent_name),
                None,
            )
            if _e:
                _registry_runtime = _e.get("agent_runtime", "maf")
        except Exception:
            pass
        await _detect_agent_commits(
            agent_name, _effective_agent_dir, run_id,
            since_sha=_head_before if _head_before else None,
            # WS-29 slice 6a: the batch path now sets _RUN_ORG[thread_id] when its
            # caller supplied an org, so with the flag ON an org-carrying batch run
            # (sub-agent) binds the tenant for its pending_commit write. An org-less
            # batch run (workflow/schedule until 6c) resolves None → fail-closed
            # skip. flag OFF → byte-identical either way.
            thread_id=thread_id,
        )
        return final_state

    except AgentLoadError as exc:
        _log.error("executor.load_error", agent=agent_name, run_id=run_id, error=str(exc))
        record(
            AuditEvent(
                actor="system:executor",
                action="agent_load_error",
                target=f"agent:{agent_name}",
                payload={"run_id": run_id, "error": str(exc)},
                # WS-29 acb_graph slice 5: the run's tenant, captured here.
                organization_id=_RUN_ORG.get(thread_id),
            )
        )
        # Structural incompatibility (missing agents.py, no tools, LangGraph remnant, etc.)
        # Trigger the Copilot SDK mutation sandbox to auto-fix the repo and open a PR.
        # The sandbox receives the full error + the agent_repo_compatibility.md guide
        # so the SDK agent knows exactly what the repo needs to look like.
        from orchestrator.mutation import \
            attempt_self_mutation
        mutation_result = await attempt_self_mutation(
            agent_name=agent_name,
            run_id=run_id,
            error=exc,
            agent_dir=_effective_agent_dir,
            incompatibility=True,
        )
        pr_url = mutation_result.pr_url if mutation_result else None
        raise AgentRunError(
            f"Agent repo incompatible — self-repair PR opened: {pr_url}" if pr_url
            else str(exc),
            agent_name=agent_name,
            run_id=run_id,
            original=exc,
            mutation_pr=pr_url,
        ) from exc

    except Exception as exc:
        # exc_info=True → the format_exc_info processor renders the full
        # traceback; with Phase-1 correlation this log line already carries
        # run_id/agent/user, so `journalctl … | grep <run_id>` shows the stack.
        _log.error(
            "executor.run_error", agent=agent_name, run_id=run_id,
            error=str(exc), exc_info=True,
        )
        import traceback as _tb
        _run_tb = "".join(
            _tb.format_exception(type(exc), exc, exc.__traceback__)
        )[:8000]

        # ── Self-annealing: detect → fix in-process → retry → LLM recovery ──
        recovery = await _self_anneal(
            agent_name=agent_name,
            run_id=run_id,
            thread_id=thread_id,
            event_payload=event_payload,
            agent_dir=_effective_agent_dir,
            error=exc,
        )
        if recovery is not None:
            return recovery

        # All anneal attempts exhausted — attempt self-mutation (ADR-021)
        from orchestrator.mutation import \
            attempt_self_mutation

        mutation_result = await attempt_self_mutation(
            agent_name=agent_name,
            run_id=run_id,
            error=exc,
            agent_dir=_effective_agent_dir,  # pass persistent clone path for authenticated push
        )
        pr_url = mutation_result.pr_url if mutation_result else None

        record(
            AuditEvent(
                actor=f"agent:{agent_name}",
                action="agent_run_error",
                target=f"agent:{agent_name}",
                payload={
                    "run_id": run_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "traceback": _run_tb,
                    "mutation_pr": pr_url,
                },
                # WS-29 acb_graph slice 5: the run's tenant, captured here.
                organization_id=_RUN_ORG.get(thread_id),
            )
        )
        raise AgentRunError(
            str(exc),
            agent_name=agent_name,
            run_id=run_id,
            original=exc,
            mutation_pr=pr_url,
        ) from exc

    finally:
        # Drop this batch run's tenant record and release the async binding, so a
        # tenant left bound is never inherited by whatever runs next on this task
        # (WS-29 acb_graph slice 6a — mirrors run_agent_stream's finally).
        # GUARDED pop (slice 3, P2): only drop the record if it is STILL this
        # run's org — a superseded same-thread run must not clobber a newer one.
        _guarded_pop_run_org(thread_id, organization_id)
        if _tenant_token is not None:
            try:
                from acb_common.db import release_tenant
                release_tenant(_tenant_token)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Streaming executor — yields AG-UI SSE events for /agent/run/stream
# ---------------------------------------------------------------------------

# Monotonic counter for local event IDs.  Combined with a timestamp so the
# frontend can track ``lastEventId`` even for initial streams (where the Redis
# entry ID isn't yet known).  Format: ``local-<ms>-<seq>``.
_sse_seq: int = 0

# Per-thread emit counter (P1-5 reconnect cursoring).  A run resets its Redis
# stream (mark_active(reset=True)) and pushes events in EXACT emission order
# (_push_chains), so the Nth event this thread emits IS the Nth Redis entry.
# We stamp that ordinal into the local id as ``local-<ms>-<seq>#<threadSeq>`` so
# a reconnect with a local cursor can skip the first <threadSeq> Redis entries
# instead of blindly re-replaying the whole run from 0-0.
_thread_emit_seq: dict[str, int] = {}


def _sse(payload: dict[str, Any]) -> str:
    """Return a single SSE frame as a string.

    When ``_stream_relay_thread_id`` context var is set, also schedules a
    background push to the Redis Stream so reconnection can replay events.

    Includes a local ``_stream_id`` in every event so the frontend always
    has a cursor to resume from, even before the Redis push completes.  The
    id embeds a per-thread emit ordinal (``#<n>``) so a reconnect can resume
    by count (P1-5) — see the reconnect handler's local-cursor branch.
    """
    global _sse_seq
    import time as _time
    _sse_seq += 1
    _tid = _stream_relay_thread_id.get(None)
    _thread_seq = ""
    if _tid is not None:
        _n = _thread_emit_seq.get(_tid, 0) + 1
        _thread_emit_seq[_tid] = _n
        _thread_seq = f"#{_n}"
    _local_id = f"local-{int(_time.time() * 1000)}-{_sse_seq}{_thread_seq}"
    payload["_stream_id"] = _local_id

    line = f"data: {json.dumps(payload)}\n\n"

    # Tee to Redis Stream for reconnection support (ordered, best-effort).
    _tee_sse_line(line)

    return line


# ── ONE event translator (core_loop_unification Phase 2) ────────────────────
# The canonical runtime-update → AG-UI mapping lives in event_translator.py
# and is shared by all four streaming paths (native MAF, Copilot, Tier 2
# batch, sub-agent).  These aliases preserve the executor's historical API
# (tests and older call sites import them from here).
from orchestrator.event_translator import (
    ToolCallStreamState as _FcStreamState,
    TranslationState as _TranslationState,
    TranslatorHooks as _TranslatorHooks,
    close_text_message as _close_text_message,
    function_call_events as _native_fc_events,
    text_message_events as _text_message_events,
    translate_update as _translate_update,
    wrap_sub_agent_events as _wrap_sub_agent_events,
)


async def run_agent_stream(
    agent_name: str,
    event_payload: dict[str, Any],
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    model: str | None = None,
    think_mode: str = "auto",
    organization_id: str | None = None,
) -> AsyncIterator[str]:
    """Load a named agent and yield AG-UI SSE events while it runs.

    Strategy (two-tier with automatic fallback):

    Tier 1 — MAF AG-UI streaming (preferred)
        If ``agent_framework.ag_ui`` exposes a ``stream_agent_response`` helper,
        delegate to it.  This forwards native TOOL_CALL_START / TOOL_CALL_ARGS /
        TOOL_CALL_END / TEXT_MESSAGE_CONTENT / RUN_FINISHED events — exactly what
        the Next.js translation layer already handles.

    Tier 2 — Instrumented batch fallback
        Wraps each tool function on the agent with a thin shim that pushes
        TOOL_CALL_START / TOOL_CALL_END events onto an asyncio.Queue while the
        main run executes in a background task.  The final text result is then
        word-streamed as TEXT_MESSAGE_CONTENT deltas so the UI renders the
        response progressively rather than all-at-once.

    Either way the caller (FastAPI StreamingResponse or the Next.js route) sees
    a standards-compliant AG-UI event stream.
    """
    _disable_agent_telemetry_once()
    run_id = run_id or str(uuid.uuid4())
    thread_id = thread_id or f"{agent_name}:{run_id}"

    settings = get_settings()

    # ── User context for tools/memory ──────────────────────────────────────
    # Bind the acting user HERE (inside the generator, before any agent task
    # spawns) from the payload, so user-scoped tools and memory see it: setting
    # it in the calling route doesn't survive into the streaming/agent execution
    # context. Released in this generator's finally — an identity that outlives
    # its run is the next run's identity, which is the bug this shape exists to
    # prevent (see _bind_memory_user_id).
    _identity_binding = _bind_run_identity(event_payload, agent_name)

    # ── Run correlation (E2 observability) ─────────────────────────────────
    # Bind run_id/thread_id/agent/user into structlog contextvars so EVERY log
    # line this run emits (across all tiers + injected tools on this context)
    # carries them — the thing that makes "show me all logs for run X / agent Y"
    # possible. Cleared in the finally below. The fifth field of decision D1's
    # stamp, `instance`, cannot be resolved yet (it needs loaded.config) and is
    # topped up by _bind_run_instance right after the load — this bind stays
    # here so a failure DURING the load is still correlated.
    _corr_source = "chat"
    _corr_user = ""
    try:
        from acb_common import bind_run_context
        if isinstance(event_payload, dict):
            _corr_user = str(
                event_payload.get("user_email")
                or event_payload.get("user_id") or ""
            )
            # Originating surface (chat / email / tasks / webhook / …) so the
            # live activity feed can attribute this run to the app that fired it.
            _corr_source = str(event_payload.get("source") or "").strip() or "chat"
        bind_run_context(
            run_id=run_id, thread_id=thread_id,
            agent=agent_name, user=_corr_user or None, source=_corr_source,
        )
    except Exception:
        pass

    # ── Tenant binding for this run's writes (WS-29 MT-1d / H4, slice 2 — DARK) ─
    # ``organization_id`` is stamped SERVER-SIDE by the gateway chat route from
    # the authenticated ``UserContext.organization_id`` — NEVER from
    # ``event_payload``, which is agent/client-visible and would be a tenant-
    # spoofing hole (R11, user_management_contract.md). Do NOT source the org
    # from ``event_payload`` here: ``_corr_user`` / ``_corr_source`` above read
    # the payload for correlation only, and the tenant deliberately does not.
    #
    # Two mechanisms, both dark this slice (no DB write is converted yet — the
    # refuse-on-missing behaviour lands with the write conversions behind
    # ``ACB_GRAPH_TENANT_BIND`` in later slices):
    #   1. ``_RUN_ORG[thread_id]`` — a plain dict the worker-thread writes read
    #      AFTER the ``loop.run_in_executor`` hop, where contextvars do not
    #      propagate (that hop is the whole reason this dict exists).
    #   2. ``bind_tenant(org)`` — so any async ``acb_common.tenant_session`` read
    #      on THIS run's own event loop resolves the tenant.
    _tenant_token = None
    if organization_id:
        _RUN_ORG[thread_id] = organization_id
        try:
            from acb_common.db import bind_tenant
            _tenant_token = bind_tenant(organization_id)
        except Exception:
            _tenant_token = None
    else:
        # No tenant resolved for this run. A chat run always has an authenticated
        # caller behind it, so this is a real plumbing gap; and the NON-chat
        # surfaces (email-automation, any source != "chat") are NOT wired to
        # thread org yet (slice 6). LOG the gap for EVERY source so both are
        # VISIBLE in logs (WS-29 slice 3, P1) — not refused (fail-closed refusal
        # is per-write, behind ACB_GRAPH_TENANT_BIND).
        _log.warning(
            "executor.run_missing_org",
            agent=agent_name, thread_id=thread_id, source=_corr_source,
        )
    # WS-29 slice 6a: sub-agent runs now inherit the parent's org (via the batch
    # path's organization_id, resolved by _resolve_sub_agent_org), and /copilot/
    # chat threads its org through run_detached. TODO(WS-29 slice 6b): email-
    # automation chat + inbound webhooks (WhatsApp/ClickUp) need an RLS-scoped
    # mailbox/account→org resolver. TODO(WS-29 slice 6c): the workflow cron
    # scheduler / orphan reconciler / schedule sweep resolve the owning record's
    # org and pass it into the batch path.

    # ── Live activity feed (E2): agent activation START ───────────────────────
    # Publish to the global bus so /observability shows this run the instant it
    # begins — across chat and every app. Best-effort; the matching END event is
    # emitted in the finally below. Duration is measured from here.
    import time as _time
    _activity_started = _time.monotonic()
    try:
        from acb_common import publish_activity
        publish_activity(
            kind="agent", phase="start",
            agent=agent_name, run_id=run_id, thread_id=thread_id,
            user=(_corr_user or None), model=(model or None),
            source=_corr_source,
        )
    except Exception:
        pass

    # Tool-call activations (E2 granular observability): publish a kind:"tool"
    # event when the agent starts/finishes a tool so /observability can show the
    # EXACT tool on the agent's avatar. Best-effort; inherits agent/run context.
    def _emit_tool(_name: str, _phase: str) -> None:
        try:
            from acb_common import publish_activity
            publish_activity(
                kind="tool", phase=_phase, tool=_name,
                agent=agent_name, run_id=run_id, source=_corr_source,
            )
        except Exception:
            pass

    # ── Stream relay: tee all SSE events to Redis for reconnection support ─
    _relay_token = _stream_relay_thread_id.set(thread_id)
    # Expose the run's model so sub-agents inherit the parent tier. Seed with the
    # raw requested model now; refined to the fully-resolved tier once known.
    _model_token = _active_run_model.set((model or "").strip() or None)
    _relay_mark_inactive = None  # type: ignore[assignment]
    try:
        from orchestrator.stream_relay import (
            mark_active as _relay_mark_active,
            mark_inactive as _relay_mark_inactive,
        )
        await _relay_mark_active(thread_id)
    except Exception:
        pass

    # Fresh per-thread emit ordinal for this run (P1-5): the stream was just
    # reset, so the next event emitted is entry #1 in Redis.
    _thread_emit_seq[thread_id] = 0

    # Cross-worker HITL delivery (P1-2): register a "respond_input" applier so a
    # user's ask_user answer that arrives on a DIFFERENT worker is relayed here
    # (this worker owns the parked Future) and resolves it.  resolve_user_input
    # is keyed by request_id, so the applier just forwards.  Unregistered in the
    # finally alongside the relay tokens.
    try:
        from orchestrator.stream_relay import (
            register_control_command as _register_ctl,
        )

        def _respond_input_apply(command: dict[str, Any]) -> bool:
            return resolve_user_input(
                str(command.get("request_id", "")),
                str(command.get("answer", "")),
                bool(command.get("was_freeform", True)),
            )

        _register_ctl(thread_id, "respond_input", _respond_input_apply)
    except Exception:
        pass

    # ── Resolve agent metadata ──────────────────────────────────────────────
    _registry_repo_name: str | None = None
    _registry_local_path: str | None = None
    _agent_runtime: str = "maf"
    try:
        from gateway.routes.agent import _AGENT_REGISTRY
        from gateway.routes.agent import _load_dynamic_agents
        _all = _load_dynamic_agents() + _AGENT_REGISTRY
        entry = next((e for e in _all if e["name"] == agent_name), None)
        if entry:
            raw = entry.get("repo_name") or ""
            # Pass the full org/repo slug — load_agent splits when needed
            _registry_repo_name = raw if raw else None
            _registry_local_path = entry.get("local_path")
            _agent_runtime = entry.get("agent_runtime", "maf")
    except ImportError:
        pass

    # Emit RUN_STARTED immediately so the UI can show ThinkingContainer at once.
    yield _sse({"type": "RUN_STARTED", "runId": run_id, "threadId": thread_id})

    # B6 Phase-5 Tier 0: initialised here so the finally can always restore,
    # even if load_agent / build_integrations raises before creds are injected.
    _integration_env_token: IntegrationEnvToken = None

    try:
        with load_agent(
            agent_name,
            run_id=run_id,
            repo_name=_registry_repo_name,
            local_path=_registry_local_path,
        ) as loaded:
            mandatory = loaded.config.get("integrations", [])
            optional = loaded.config.get("optional_integrations", [])
            # Filter BEFORE the env injection below, so a credential the
            # acting member may not use never enters the run's environment at
            # all — not merely absent from state["integrations"].
            integrations, integration_warnings = build_integrations(
                mandatory, optional, settings,
                is_authorized=await _integration_authorizer(event_payload, thread_id),
            )
            # B6 Phase-5 Tier 0: scope creds to this run; token restored in the
            # finally below so they don't linger in the shared process env.
            _integration_env_token = _bind_run_credentials(integrations)
            agents = loaded.build_agents()
            # Honour .github/agents/<name>.agent.md (Copilot SDK definition):
            # override instructions + capture model, BEFORE tool injection so
            # the platform-tools addendum appends on top of the authored body.
            _agent_md_spec = _apply_agent_md_overrides(
                agents, loaded.agent_dir, agent_name,
            )
            _apply_own_tool_scope(
                agents, loaded.config.get("own_tool_scope") or None,
            )
            _inject_agent_tools(
                agents,
                # .agent.md's VS Code tools widen (never narrow) the scope, so a
                # declared capability like `codebase` resolves to a real tool.
                tool_scope=_merged_tool_scope(
                    loaded.config.get("tool_scope") or None, _agent_md_spec,
                ),
                agent_name=agent_name,
            )  # inject call_agent / call_agent_background
            # Inject MCP servers from the registry into every agent at runtime
            for _a in agents:
                await _inject_mcp_servers(_a, agent_name)

            # Per-session workspace override (Custom Apps builder sessions):
            # resolved once here, reused for the artifact context and the
            # Copilot working directory below so both stay consistent.
            _session_ws = _session_workspace_override(thread_id, loaded.config)

            # Tenant partition for a personal/team agent ('' = shared). Must
            # resolve BEFORE the artifact context, the visible-folder mkdir
            # and the rehydrate below — all three must agree on the same
            # directory, or one person's files surface in another's run.
            _agent_instance = _resolve_agent_instance(
                loaded.config, agent_name, _corr_user,
            )
            # Attribution (WS-6a): every log line and every model activation
            # from here on carries the partition this run is executing in, and
            # the presence snapshot published at start (which predates the
            # load, so it has no instance) is patched to match.
            _bind_run_instance(_agent_instance, run_id)
            _effective_ws = _resolve_effective_agent_dir(
                loaded.agent_dir, loaded.config,
                session_override=_session_ws,
                instance=_agent_instance,
            )

            # Set write_artifact context so the tool knows which session to
            # report files to and where the workspace root lives.
            try:
                from acb_skills.write_artifact import \
                    _WRITE_ARTIFACT_CONTEXT
                _WRITE_ARTIFACT_CONTEXT["session_id"] = thread_id or run_id
                _WRITE_ARTIFACT_CONTEXT["agent_name"] = agent_name
                _WRITE_ARTIFACT_CONTEXT["run_id"] = run_id
                _WRITE_ARTIFACT_CONTEXT["workspace_root"] = _effective_ws
                # The blob-store partition every write-through must carry —
                # keeping disk and store on the SAME tenant key (migration 136).
                _WRITE_ARTIFACT_CONTEXT["instance"] = _agent_instance
                # Declared+resolved integrations for this run — read by
                # list_integrations (discoverability) and code_tools
                # (_script_env grants a script exactly these creds).
                _WRITE_ARTIFACT_CONTEXT["integrations"] = sorted(integrations)
                _WRITE_ARTIFACT_CONTEXT["integration_warnings"] = dict(
                    integration_warnings
                )
                _WRITE_ARTIFACT_CONTEXT["gateway_url"] = str(
                    getattr(settings, "gateway_base_url", "http://127.0.0.1:8000")
                )
                _WRITE_ARTIFACT_CONTEXT["gateway_token"] = str(
                    getattr(settings, "gateway_internal_token", "")
                    or getattr(settings, "litellm_master_key", "")
                    or "sk-local"
                )

                # Ensure the three visible workspace directories exist so the
                # Files Viewer sidebar shows them even before the agent writes
                # its first artefact.
                _ws_root = Path(_effective_ws)
                for _d in ("inputs", "outputs", "agent-data"):
                    (_ws_root / _d).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            # Rehydrate durable folders from the authoritative blob store before
            # the agent runs (store is source of truth). Best-effort. The
            # instance selects WHICH partition — restoring the shared one into
            # a tenant dir would put other people's notes in this run. A
            # session-override run keeps restoring into the agent's own dir
            # (never the app workspace), exactly as before.
            try:
                from acb_memory import rehydrate_workspace
                from acb_skills.agent_paths import ensure_state_dir
                await rehydrate_workspace(
                    agent_name,
                    str(ensure_state_dir(loaded.agent_dir.name, _agent_instance))
                    if _agent_instance else str(loaded.agent_dir),
                    instance=_agent_instance,
                )
            except Exception:
                pass

            # Skills-as-an-index bodies (QM-2) — see the MAF path above for
            # why this sits after the rehydrate. No-op while the switch is off.
            materialize_skill_bodies_for_agent(
                agent_name, _effective_ws,
                tool_scope=_merged_tool_scope(
                    loaded.config.get("tool_scope") or None, _agent_md_spec,
                ),
            )

            if not agents:
                raise ValueError(f"Agent {agent_name!r}: build_agents() returned empty list.")

            agent = agents[0]

            # Detect Copilot-SDK-backed agents by capability, NOT the registry
            # runtime label. Some agents (e.g. task-manager, apis-config) are
            # built with GitHubCopilotAgent but registered as runtime "maf";
            # they still need BYOK provider routing or agent.run() opens a
            # NATIVE Copilot session (→ 402). A genuine MAF agent has no
            # ``_default_options``. Computed BEFORE session restore so every
            # session-continuity gate below keys on capability too — gating on
            # the label made mislabeled Copilot agents silently skip session
            # resume (re-injecting history every turn) and the ask_user/
            # working_directory wiring (audit follow-up, 2026-07-22).
            _is_copilot_sdk = (
                _agent_runtime == "github-copilot"
                or (hasattr(agent, "_default_options")
                    and agent._default_options is not None)
            )

            # ── Session continuity: restore Copilot SDK session if available ─
            # Storing the service_session_id allows MAF's _get_or_create_session
            # to call resume_session() instead of create_session(), maintaining
            # server-side conversation state across browser restarts.
            _copilot_session_id: str | None = None
            if _is_copilot_sdk and thread_id:
                _copilot_session_id = _get_stored_session_id(thread_id)
                if _copilot_session_id:
                    _log.debug("executor.session_restore",
                               thread_id=thread_id,
                               copilot_session=_copilot_session_id[:12])

            # Ensure the risk-aware permission handler is set for
            # GitHubCopilotAgent before ANY execution path — repos often omit it
            # from default_options (B6).
            try:
                _ph = _copilot_permission_handler()
                for _a in agents:
                    if hasattr(_a, "_permission_handler") and _a._permission_handler is None:
                        _a._permission_handler = _ph
            except Exception:
                pass

            # ── BYOK early detection (must happen BEFORE tier selection) ────
            # When a LiteLLM model is requested (contains '/' or starts with
            # 'tier') AND the agent uses the GitHub Copilot SDK runtime, the
            # BYOK provider must be configured on the agent before any MAF
            # streaming path runs — otherwise the Copilot SDK session will
            # reject the unknown model name.
            #
            # Model priority:
            #   1. Request ``model`` parameter (explicit user override)
            #   2. Global ``copilot_chat_model`` setting (env / .env)
            #   3. Agent's ``model_tier`` from config.json (per-agent default)
            _requested_model_early = (model or "").strip()
            _configured_model_early = (
                getattr(settings, "copilot_chat_model", "") or ""
            ).strip()
            _agent_model_tier = (
                loaded.config.get("model_tier") or ""
            ).strip()
            # .github/agents/<name>.agent.md model wins over config.json's
            # model_tier (the repo's authored choice) but never over an
            # explicit request/global override, keeping BYOK routing intact.
            _agent_md_model = (
                (_agent_md_spec.model or "").strip()
                if _agent_md_spec is not None
                else ""
            )
            _final_model_early = (
                _requested_model_early
                or _configured_model_early
                or _agent_md_model
                or _agent_model_tier
            )
            # BYOK-by-default: route every Copilot SDK agent through the LiteLLM
            # gateway and normalise any bare/empty model to the default tier.
            _final_model_early, _is_byok_early = _byok_default_model(
                _final_model_early, settings,
            )
            # Refine the run's model ContextVar to the fully-resolved tier so
            # sub-agents spawned during this run inherit it (call_agent etc.).
            if _final_model_early:
                _active_run_model.set(_final_model_early)
            _byok_provider_early: dict[str, Any] | None = None
            _byok_model_id_early = _final_model_early
            if _is_byok_early and _is_copilot_sdk:
                _gw_base = (
                    getattr(settings, "litellm_base_url", "")
                    or "http://127.0.0.1:8080"
                ).rstrip("/")
                # Internal-token precedence must match require_internal_auth
                # (gateway_internal_token → litellm_master_key); a divergence
                # otherwise 401s the BYOK /v1 call and surfaces on the Copilot
                # session as "Authorization error, run /login".
                _gw_key = (
                    getattr(settings, "gateway_internal_token", "")
                    or getattr(settings, "litellm_master_key", "")
                    or "sk-local"
                ).strip()
                _byok_provider_early = {
                    "type": "openai",
                    "base_url": f"{_gw_base}/v1",
                    "api_key": _gw_key,
                }
                agent._default_options["provider"] = _byok_provider_early
                agent._default_options["model"] = _byok_model_id_early
                _log.info(
                    "executor.copilot_maf_byok_early",
                    agent=agent_name,
                    runtime=_agent_runtime,
                    model=_byok_model_id_early,
                    base_url=_gw_base,
                )
            elif _final_model_early and _is_copilot_sdk:
                agent._default_options["model"] = _final_model_early
            elif _final_model_early and not _is_copilot_sdk:
                # Native MAF agent (e.g. email-assistant, orchestrator): make it
                # honour the resolved tier via default_options["model"] — the
                # Tier-1 stream_agent_response path otherwise keeps the build-time
                # client model and silently ignores the requested tier.
                try:
                    _apply_model_for_maf_agent(
                        agent, _final_model_early, settings,
                    )
                    _log.info(
                        "executor.maf_model_override",
                        agent=agent_name,
                        runtime=_agent_runtime,
                        model=_final_model_early,
                    )
                except Exception:
                    pass

            # ── Reasoning depth (chat UI "thinking" toggle) ─────────────
            # Applied at the SAME seam as the model, and to every agent this
            # run loaded, so a named agent honours the toggle on either
            # runtime.  Until now this was handled only on /copilot/chat, so
            # the control silently did nothing for named agents
            # (agent_architecture.md §11.1.1).  "auto" is a no-op.
            if think_mode and think_mode != "auto":
                for _ag in agents:
                    try:
                        if _apply_thinking_mode_for_agent(_ag, think_mode):
                            _log.info(
                                "executor.think_mode_applied",
                                agent=agent_name,
                                runtime=_agent_runtime,
                                think_mode=think_mode,
                            )
                    except Exception:  # never break a run over a rendering hint
                        pass

            # ── Set working directory for Copilot SDK agents ────────────
            # The Copilot SDK CLI defaults to the gateway CWD unless
            # working_directory is explicitly set.  Point it at the agent's
            # effective workspace (clone dir, workspace_root from config,
            # tenant state dir, or the session-bound Custom Apps workspace) —
            # the SAME path resolved above for the artifact context, so shell
            # commands, file I/O and the write-through all agree on one root.
            _effective_agent_dir = _effective_ws
            if _is_copilot_sdk:
                for _ag in agents:
                    try:
                        if (
                            hasattr(_ag, "_default_options")
                            and _ag._default_options is not None
                        ):
                            _ag._default_options[
                                "working_directory"
                            ] = _effective_agent_dir
                            # Native HITL: register the blocking ask_user
                            # handler bound to this run's relay thread so the
                            # agent can pause for human input mid-turn.
                            _ag._default_options[
                                "on_user_input_request"
                            ] = _make_user_input_handler(thread_id)
                    except Exception:
                        pass
            # If the user switches models mid-thread, the Copilot SDK
            # session is bound to the old model.  Invalidate the stored
            # session so a new one is created with the new model.  The
            # conversation continuity fallback prepends messages[] history
            # so the LLM sees full context despite the new session.
            if (_is_copilot_sdk
                    and _copilot_session_id
                    and _final_model_early
                    and thread_id):
                _prev_model = _copilot_model_store.get(thread_id)
                if _prev_model and _prev_model != _final_model_early:
                    _log.info(
                        "executor.model_switch",
                        agent=agent_name,
                        previous=_prev_model,
                        requested=_final_model_early,
                    )
                    _copilot_session_id = None  # force new session

            # ── Tier 1: native MAF agent live streaming ─────────────────────
            # The intended ``agent_framework.ag_ui.stream_agent_response`` is not
            # exported by the installed ``agent_framework_ag_ui``, and the Tier
            # 1.5 Copilot path below is gated to Copilot-SDK agents.  Without this
            # branch a native MAF agent (e.g. email-assistant) falls to the Tier
            # 2 BATCH path: no streamed reasoning, and the answer appears only
            # once the whole run finishes.  Here we stream MAF's native run and
            # translate its content deltas into the SAME AG-UI events the Copilot
            # path emits — live text, reasoning, and tool calls — while draining
            # ``_active_run_queue`` so injected-tool events (artifacts / todos /
            # elicitation) still surface live.
            #
            # Safety net: if streaming raises BEFORE emitting anything, fall
            # through to the proven Tier 2 batch path below.
            if not _is_copilot_sdk and hasattr(agent, "run"):
                _native_input = _compose_maf_run_input(
                    agent_name, run_id, event_payload, integrations,
                )
                # Context-pressure notice (audit CX6): eviction was silent —
                # a long conversation just degraded as oldest turns dropped.
                # Surface one unobtrusive progress line so the user knows.
                try:
                    from acb_llm.context import last_fit_stats
                    _fit = last_fit_stats.get() or {}
                    if _fit.get("dropped_turns"):
                        yield _sse({
                            "type": "PROGRESS_UPDATE",
                            "message": (
                                "Long conversation: the oldest "
                                f"{_fit['dropped_turns']} turn(s) no longer "
                                "fit the model's context and were left out."
                            ),
                        })
                except Exception:
                    pass
                _nq: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
                _nq_token = _active_run_queue.set(_nq)
                _n_emitted = False
                # Canonical translation state (message lifecycle + tool-call
                # id dedup) — shared mapping with every other stream path.
                _t_state = _TranslationState(run_id)
                # Idle watchdog: if the native stream yields no update for this
                # many seconds, treat the agent as stalled and error out rather
                # than hold the SSE open until the HTTP-level abort (~5 min).
                _native_idle = False
                _native_idle_after = 0.0
                try:
                    async with contextlib.AsyncExitStack() as _nstack:
                        if hasattr(type(agent), "__aenter__"):
                            await _nstack.enter_async_context(agent)
                        _agen = agent.run(
                            _native_input, stream=True,
                        ).__aiter__()
                        # Race the agent's next update against the injected-tool
                        # event queue. A BLOCKING tool (ask_questions HITL) puts
                        # its card on _nq then parks awaiting the user; the old
                        # code only drained _nq *between* agent updates, so the
                        # card never surfaced and the idle watchdog killed the
                        # run. Draining concurrently makes HITL work on native MAF.
                        _hitl_names = (
                            "elicitation_requested",
                            "confirmation_requested",
                            "user_input_requested",
                        )

                        def _is_hitl_event(ev: dict[str, Any]) -> bool:
                            """True when *ev* parks the run awaiting the user.

                            Besides the dedicated HITL card events, a
                            ``generative_ui`` event whose spec carries a
                            ``request_id`` is a BLOCKING emit_generative_ui
                            (hitl:true) — the tool is parked on a Future, so
                            the idle watchdog must grant the 3600s HITL budget,
                            not the 600s tool-open one.
                            """
                            name = ev.get("name")
                            if name in _hitl_names:
                                return True
                            if name == "generative_ui":
                                val = ev.get("value")
                                return isinstance(val, dict) and bool(
                                    val.get("request_id")
                                )
                            return False
                        # Idle budgets + tier-selection now come from the shared
                        # WatchdogPolicy (see idle-watchdog block below).
                        _tools_open = 0
                        # Loop detection: a model can wedge itself calling the
                        # SAME tool with the SAME args over and over (bad plan,
                        # ignored error). Track a per-call signature (name+args)
                        # and abort the run when one repeats too many times, so
                        # a loop surfaces as an error instead of burning tokens
                        # until the idle/HTTP timeout. Thresholds are generous —
                        # legitimate retries with identical args are rare.
                        _loop_max = int(
                            os.environ.get("TOOL_LOOP_MAX_REPEATS", "5")
                        )
                        _tc_name: dict[str, str] = {}
                        _tc_args: dict[str, str] = {}
                        _loop_detector = _LoopDetector(max_repeats=_loop_max)
                        _loop_tripped = False
                        _next_task: "asyncio.Task[Any] | None" = None
                        _hitl_pending = False
                        while True:
                            if _next_task is None:
                                _next_task = asyncio.ensure_future(
                                    _agen.__anext__()
                                )
                            _q_task = asyncio.ensure_future(_nq.get())
                            # Tiered idle watchdog (unified policy): 3600s while
                            # a HITL question is pending, 600s while a tool is in
                            # flight, else 120s — selection owned by
                            # WatchdogPolicy.idle_timeout so value + ordering
                            # can't drift across paths.
                            _wait_to = _WATCHDOG.idle_timeout(
                                hitl_pending=_hitl_pending,
                                tools_open=_tools_open,
                            )
                            _done, _ = await asyncio.wait(
                                {_next_task, _q_task},
                                timeout=_wait_to,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if _q_task not in _done:
                                _q_task.cancel()
                            if not _done:
                                # Genuine stall: no update, no event in window.
                                _native_idle = True
                                _native_idle_after = _wait_to
                                _next_task.cancel()
                                with contextlib.suppress(Exception):
                                    await _agen.aclose()
                                break
                            # Surface queued injected-tool / HITL events at once
                            # (this is what delivers a blocked tool's card live).
                            if _q_task in _done:
                                try:
                                    _qev = _q_task.result()
                                except Exception:
                                    _qev = None
                                while _qev is not None:
                                    _n_emitted = True
                                    if _is_hitl_event(_qev):
                                        _hitl_pending = True
                                    yield _sse(_qev)
                                    _qev = (
                                        _nq.get_nowait()
                                        if not _nq.empty() else None
                                    )
                            if _next_task not in _done:
                                continue
                            try:
                                _u = _next_task.result()
                            except StopAsyncIteration:
                                break
                            except ValueError as _ve:
                                # Defense-in-depth for the agent_framework
                                # streaming-telemetry teardown bug: its cleanup
                                # hook resets a ContextVar in a different async
                                # context and raises "Token was created in a
                                # different Context" AT END OF STREAM — after the
                                # answer already streamed. Treat that benign
                                # teardown error as a clean end-of-stream so a
                                # successful run is never reported as RUN_ERROR.
                                # (Root cause is removed by
                                # _disable_agent_telemetry_once; this guards any
                                # residual / re-enabled case.)
                                if "different Context" in str(_ve):
                                    _log.warning(
                                        "executor.native_maf_telemetry_teardown",
                                        agent=agent_name,
                                    )
                                    break
                                raise
                            _next_task = None
                            # Agent advanced (e.g. the HITL answer arrived) — no
                            # longer parked on a question.
                            _hitl_pending = False
                            for _ev in _translate_update(_u, _t_state):
                                _et = _ev.get("type")
                                # Watchdog bookkeeping: a new logical tool
                                # call in flight parks the agent, so the idle
                                # watchdog backs off (a long tool/sub-agent
                                # isn't a stall); results re-tighten it.
                                if _et == "TOOL_CALL_START":
                                    _tools_open += 1
                                    _n_emitted = True
                                    _cid = str(_ev.get("toolCallId") or "")
                                    _tc_name[_cid] = str(
                                        _ev.get("toolCallName") or "tool"
                                    )
                                    _tc_args[_cid] = str(_ev.get("args") or "")
                                    _emit_tool(_tc_name[_cid], "start")
                                elif _et == "TOOL_CALL_ARGS":
                                    _cid = str(_ev.get("toolCallId") or "")
                                    _tc_args[_cid] = _tc_args.get(_cid, "") + str(
                                        _ev.get("delta") or ""
                                    )
                                elif _et == "TOOL_CALL_RESULT":
                                    _tools_open = max(0, _tools_open - 1)
                                    # Loop detection: bump this call's signature
                                    # count; trip when the SAME name+args repeats
                                    # past the threshold.
                                    _cid = str(_ev.get("toolCallId") or "")
                                    if _loop_detector.record(
                                        _tc_name.get(_cid, "tool"),
                                        _tc_args.get(_cid, ""),
                                    ):
                                        _loop_tripped = True
                                    _emit_tool(_tc_name.get(_cid, "tool"), "end")
                                    _tc_name.pop(_cid, None)
                                    _tc_args.pop(_cid, None)
                                else:
                                    _n_emitted = True
                                yield _sse(_ev)
                            if _loop_tripped:
                                _log.warning(
                                    "executor.native_maf_tool_loop_detected",
                                    agent=agent_name, repeats=_loop_max,
                                )
                                yield _sse({
                                    "type": "RUN_ERROR", "runId": run_id,
                                    "message": (
                                        "Agent stopped: the same tool was "
                                        f"called with identical arguments "
                                        f"{_loop_max} times (loop detected)."
                                    ),
                                })
                                # _next_task is None here in the normal flow
                                # (reset after .result() above); it is only a
                                # live task if the trip path ever moves before
                                # the reset. Guard it — an unconditional
                                # .cancel() crashed the loop-trip path with
                                # AttributeError and skipped the aclose().
                                if _next_task is not None:
                                    _next_task.cancel()
                                with contextlib.suppress(Exception):
                                    await _agen.aclose()
                                break
                            # Surface injected-tool events (write_artifact,
                            # manage_todo_list, …) emitted during this update.
                            while not _nq.empty():
                                _qev = _nq.get_nowait()
                                if _qev:
                                    _n_emitted = True
                                    if _is_hitl_event(_qev):
                                        _hitl_pending = True
                                    yield _sse(_qev)
                    # Drain any events that landed as the stream closed.
                    while not _nq.empty():
                        _qev = _nq.get_nowait()
                        if _qev:
                            yield _sse(_qev)
                    for _ev in _close_text_message(_t_state):
                        yield _sse(_ev)
                    if _native_idle:
                        _log.warning(
                            "executor.native_maf_stream_idle_timeout",
                            agent=agent_name, idle_seconds=_native_idle_after,
                        )
                        yield _sse({
                            "type": "RUN_ERROR", "runId": run_id,
                            "message": (
                                "Agent produced no output for "
                                f"{int(_native_idle_after)}s and was stopped "
                                "(possible stall)."
                            ),
                        })
                    elif not _loop_tripped:
                        # A loop trip already emitted its terminal RUN_ERROR
                        # inline — don't follow it with a RUN_FINISHED that
                        # would make the client render the run as successful.
                        yield _sse({
                            "type": "RUN_FINISHED", "runId": run_id,
                            "threadId": thread_id,
                        })
                    _active_run_queue.reset(_nq_token)
                    return
                except Exception as _nexc:
                    with contextlib.suppress(Exception):
                        _active_run_queue.reset(_nq_token)
                    if _n_emitted:
                        _log.exception(
                            "executor.native_maf_stream_error",
                            agent=agent_name,
                        )
                        # Close any open text message first so the UI bubble
                        # doesn't hang in "streaming" before the error lands.
                        for _ev in _close_text_message(_t_state):
                            yield _sse(_ev)
                        yield _sse({
                            "type": "RUN_ERROR", "runId": run_id,
                            "message": str(_nexc),
                        })
                        return
                    # Nothing emitted yet — fall through to Tier 2 batch.
                    _log.warning(
                        "executor.native_maf_stream_fallback",
                        agent=agent_name, error=str(_nexc)[:200],
                    )

            # ── Tier 1.5: GitHubCopilotAgent native streaming ───────────────
            # agent.run(stream=True) uses _stream_updates() which subscribes to
            # the Copilot session event bus — no 60s timeout, genuine token-by-
            # token streaming, live tool events.  This is the correct path for
            # any GitHub-sourced agent.
            #
            # BYOK provider + model already resolved in the early-detection
            # block above (before Tier 1).  Reuse those values here so the
            # GitHub Copilot SDK path doesn't duplicate the lookup.
            #
            # Queue-based approach (not direct yield): the agent runs in a
            # background task that pushes events to a queue.  The main loop
            # drains the queue and yields SSE.  This allows tool calls
            # (including call_agent sub-delegation) to push SUB_AGENT_* events
            # into the same queue while the main loop is waiting — giving
            # real-time visibility of sub-agent progress in the UI.

            # ── GitHub Copilot path (MAF-wrapped via MetoriteCopilotAgent) ─
            # Covers BOTH github-copilot-runtime agents and Copilot-SDK agents
            # registered as "maf" (e.g. email-assistant) so they get BYOK
            # provider forwarding instead of a native Copilot session.
            if _is_copilot_sdk:
                from orchestrator.copilot_agent import MetoriteCopilotAgent

                # Patch the loaded agent with enhanced BYOK + streaming methods.
                agent.start = MetoriteCopilotAgent.start.__get__(
                    agent, type(agent)
                )
                agent._create_session = MetoriteCopilotAgent._create_session.__get__(
                    agent, type(agent)
                )
                agent._resume_session = MetoriteCopilotAgent._resume_session.__get__(
                    agent, type(agent)
                )
                agent._stream_updates = MetoriteCopilotAgent._stream_updates.__get__(
                    agent, type(agent)
                )

                # BO-7 phase 2: containerize the App Workshop app-builder's
                # session (no-op for every other agent — see docstring).
                # _effective_agent_dir IS _session_ws here whenever it applies
                # (_resolve_effective_agent_dir gives the session override
                # full precedence), so it's always the Custom App workspace,
                # never the app-builder's own clone.
                await _maybe_sandbox_session_workspace(
                    thread_id=thread_id,
                    session_ws=_session_ws,
                    effective_agent_dir=_effective_agent_dir,
                    agent_dir=loaded.agent_dir,
                    agent=agent,
                    settings=settings,
                )

                # Install push guard + capture HEAD for post-run commit detection.
                await _install_push_guard(_effective_agent_dir)
                _stream_head_before = await _get_current_head(_effective_agent_dir)

                # BYOK provider + model already resolved in the early-
                # detection block.  Reuse pre-computed values.
                _is_byok = _is_byok_early
                _byok_provider = _byok_provider_early
                _byok_model_id = _byok_model_id_early

                # Ensure the risk-aware permission handler (B6).
                try:
                    if hasattr(agent, "_permission_handler") and agent._permission_handler is None:
                        agent._permission_handler = _copilot_permission_handler()
                except Exception:
                    pass

                _msg_text = event_payload.get("message") or event_payload.get("user_query") or ""

                # ── Conversation continuity fallback ──────────────────────
                # If no stored Copilot SDK session exists for this thread
                # (first message, or session record was lost due to gateway
                # restart / Copilot CLI process death), the Copilot SDK will
                # create a brand-new session that only sees _msg_text — losing
                # all prior conversation context.
                #
                # As a safety net, prepend recent conversation history from
                # the payload's messages[] array.  When the session IS alive
                # (_copilot_session_id found), this block is skipped — the
                # Copilot SDK already has full history in its session state.
                #
                # Stale session (gateway restart / Copilot CLI death): handled
                # via _session_retry_attempted in the streaming block below —
                # on resume failure the run retries with a fresh session and
                # history injected from messages[].
                if not _copilot_session_id:
                    _prior = event_payload.get("messages") or []
                    if _prior:
                        # Budgeted to the model's REAL window. This used to be a
                        # flat last-30 x 600 chars — which contradicted its own
                        # comment about diffs/file contents being "meaningless
                        # when cut to a few sentences" by cutting them to 600.
                        # This path runs on every session rebuild (e.g. after a
                        # gateway restart), so it decides how much of the thread
                        # a resumed chat actually remembers.
                        _rendered = _render_history_block(
                            _prior, _msg_text, _active_run_model.get() or "",
                        )
                        if _rendered:
                            _msg_text = (
                                "## Prior conversation (for context)\n"
                                + _rendered
                                + f"\n\n## Current message\n{_msg_text}"
                            )
                            _log.debug(
                                "executor.copilot_context_fallback",
                                agent=agent_name,
                                history_chars=len(_rendered),
                            )

                # ── Memory context injection (pre-enriched by the route handler) ──
                _memory_context = event_payload.get("memory_context") or ""
                if _memory_context:
                    try:
                        # Prompt-cache boundary marker (specs Phase 3.1): the
                        # sentinel separates the stable prefix (instructions +
                        # tool addendum) from the dynamic memory block so the
                        # acb_llm.prompt_cache transform can put a cache_control
                        # breakpoint at exactly this point for Anthropic tiers.
                        # It is consumed (Anthropic) or stripped (others) before
                        # the request leaves the gateway — never seen by the LLM.
                        from acb_llm.prompt_cache import (
                            CACHE_BREAK as _CACHE_BREAK,
                        )
                        _opts = agent.default_options
                        if isinstance(_opts, dict):
                            _existing = (
                                _opts.get("instructions")
                                or _opts.get("system_message")
                                or ""
                            )
                            _merged = (
                                f"{_existing}\n{_CACHE_BREAK}\n{_memory_context}"
                            )
                            # MAF Agent base class uses "instructions"
                            _opts["instructions"] = _merged

                            # GitHubCopilotAgent uses "system_message" in
                            # a SEPARATE _default_options dict.  Merge with
                            # any existing content (tool addendum injected
                            # earlier by _inject_agent_tools) rather than
                            # overwriting — otherwise the tool guidance
                            # addendum that tells the LLM about call_agent,
                            # web_search, write_artifact, etc. is lost.
                            _copilot_opts = getattr(
                                agent, "_default_options", None
                            )
                            if isinstance(_copilot_opts, dict):
                                _existing_copilot = _copilot_opts.get(
                                    "system_message"
                                )
                                if isinstance(_existing_copilot, dict):
                                    # Preserve mode:'append'; extend content.
                                    # Sentinel marks the stable/dynamic boundary
                                    # (base instructions + addendum stay stable;
                                    # memory is the dynamic suffix).
                                    _prev = (
                                        _existing_copilot.get("content")
                                        or ""
                                    )
                                    _copilot_opts["system_message"] = {
                                        "mode": "append",
                                        "content": (
                                            f"{_prev}\n{_CACHE_BREAK}"
                                            f"\n{_memory_context}"
                                        ),
                                    }
                                elif isinstance(_existing_copilot, str):
                                    _copilot_opts["system_message"] = (
                                        f"{_existing_copilot}\n{_CACHE_BREAK}\n"
                                        f"{_memory_context}"
                                    )
                                else:
                                    _copilot_opts["system_message"] = (
                                        _merged
                                    )
                    except Exception:
                        pass

                # ── Thinking mode (Auto / Thinking / Max) ──
                # reasoning_effort unlocks the model's full token-level
                # chain-of-thought stream (ASSISTANT_REASONING_DELTA) — the
                # same verbose stream-of-consciousness VS Code Copilot shows.
                # Without it most models emit only sparse fragments, so Auto
                # defaults to "low" rather than omitting it entirely.
                # _create_session() retries without the option if the model
                # rejects it, so unsupported models degrade gracefully.
                _think_mode = event_payload.get("think_mode") or "auto"
                try:
                    _opts = agent.default_options
                    if isinstance(_opts, dict):
                        _effort = {"thinking": "medium", "max": "high"}.get(
                            _think_mode, "low"
                        )
                        _opts["reasoning_effort"] = _effort
                except Exception:
                    pass

                # Canonical translation state — same mapping as every other
                # stream path (core_loop_unification Phase 2).
                _t15_state = _TranslationState(run_id)
                _todo_tracker = _TodoTracker()
                # Last tool STARTED on this run. A turn that ends on a blocking
                # HITL tool has no closing text by design — the question card is
                # the output — so the no-text handler needs the name to tell
                # "waiting for the user" apart from "the answer went missing".
                _t15_last_tool: str = ""

                # ── Tier-1.5 translator hooks ─────────────────────────────
                # The Copilot path's extras live here, NOT in divergent
                # mapping copies: TODO_LIST interception + the blocking
                # elicitation bridge on function_call, and elicitation
                # cleanup on function_result.  The native path must NOT get
                # these — its injected tools push TODO_LIST / elicitation
                # events themselves via _active_run_queue, so hook-driven
                # interception would double-emit.
                def _t15_fc_extras(
                    _tc_id: str, _tc_name: str, _tc_args: Any, _args_str: str,
                ) -> list[dict[str, Any]]:
                    _extra: list[dict[str, Any]] = []
                    _emitted_todo = False
                    # granular observability: mark this tool as the agent's current
                    # one (cleared on the agent-run end event / TTL on the client).
                    _emit_tool(_tc_name, "start")
                    # Structured todo-list tracking — two paths to the same
                    # TODO_LIST event: manage_todo_list (primary) and the
                    # legacy sql-on-todos fallback via _TodoTracker.
                    if _tc_name == "manage_todo_list":
                        _todos = _unwrap_json_param(
                            _tc_args.get("todoList", "[]")
                            if isinstance(_tc_args, dict) else "[]",
                            "todoList",
                        )
                        if isinstance(_todos, list):
                            _cleaned: list[dict] = []
                            for _t in _todos:
                                if isinstance(_t, dict):
                                    _cleaned.append({
                                        "id": str(_t.get("id", "")),
                                        "title": str(_t.get("title", "")),
                                        "status": str(
                                            _t.get("status", "not-started")
                                        ),
                                    })
                            _extra.append(
                                {"type": "TODO_LIST", "todos": _cleaned}
                            )
                            _emitted_todo = True
                    # HITL elicitation — validate before rendering, then park
                    # a Future so the ask_questions tool blocks until the
                    # user answers (without this the tool returns via Path B,
                    # the LLM stops with no text, and the chat "dies").
                    if _tc_name == "ask_questions":
                        try:
                            _qs = _unwrap_json_param(
                                _tc_args.get("questions", "[]")
                                if isinstance(_tc_args, dict) else "[]",
                                "questions",
                            )
                            _valid: list[dict] = []
                            if isinstance(_qs, list):
                                for _qi, _q in enumerate(_qs):
                                    if not isinstance(_q, dict):
                                        continue
                                    _qh = str(
                                        _q.get("header", f"Q{_qi + 1}")
                                    ).strip()[:50]
                                    _qt = str(
                                        _q.get("question", "")
                                    ).strip()[:200]
                                    if not _qt:
                                        continue
                                    _valid.append({
                                        "header": _qh,
                                        "question": _qt,
                                        "multiSelect": bool(
                                            _q.get("multiSelect", False)
                                        ),
                                        "allowFreeformInput": bool(
                                            _q.get("allowFreeformInput", True)
                                        ),
                                        "options": (
                                            _q.get("options")
                                            if isinstance(
                                                _q.get("options"), list
                                            ) and len(_q["options"]) > 0
                                            else None
                                        ),
                                    })
                            if _valid:
                                _req_id = uuid.uuid4().hex
                                _active_elicitation_request_id.set(_req_id)
                                _loop = asyncio.get_running_loop()
                                _fut: "asyncio.Future[dict[str, Any]]" = (
                                    _loop.create_future()
                                )
                                _pending_user_input[_req_id] = _fut
                                # Map tool_call_id → elicitation so cleanup
                                # only fires for the matching result.
                                _elicitation_tc_ids[_req_id] = _tc_id
                                _log.debug(
                                    "executor.elicitation_parked",
                                    request_id=_req_id[:12],
                                    question_count=len(_valid),
                                    tool_call_id=_tc_id[:12],
                                )
                                _extra.append({
                                    "type": "CUSTOM",
                                    "name": "elicitation_requested",
                                    "value": {
                                        "questions": _valid,
                                        "request_id": _req_id,
                                    },
                                })
                        except Exception:
                            pass
                    if not _emitted_todo and _todo_tracker.feed(
                        _tc_name, _tc_args,
                    ):
                        _extra.append({
                            "type": "TODO_LIST",
                            "todos": _todo_tracker.snapshot(),
                        })
                    return _extra

                def _t15_fr_cleanup(_tc_id: str) -> None:
                    # Elicitation-bridge cleanup: only clear when this
                    # function_result belongs to the pending ask_questions
                    # call (matched by tool_call_id) — clearing on ANY result
                    # raced a second tool's completion against the parked
                    # Future.
                    _elic_id = _active_elicitation_request_id.get(None)
                    if not _elic_id:
                        return
                    _elic_tc_id = _elicitation_tc_ids.get(_elic_id)
                    if _elic_tc_id is None or _elic_tc_id == _tc_id:
                        _active_elicitation_request_id.set(None)
                        _pending_user_input.pop(_elic_id, None)
                        _elicitation_tc_ids.pop(_elic_id, None)
                        _log.debug(
                            "executor.elicitation_cleaned",
                            request_id=_elic_id[:12],
                            tool_call_id=_tc_id[:12],
                        )

                _t15_hooks = _TranslatorHooks(
                    extra_function_call_events=_t15_fc_extras,
                    on_function_result=_t15_fr_cleanup,
                )

                # Retry state — set to True after a stale-session recovery so
                # the second attempt is never retried again (max 1 retry).
                _session_retry_attempted = False
                _effective_msg = _msg_text

                # ── Inner async generator: one Copilot streaming attempt ───
                # Extracted so the retry loop below can call it twice without
                # duplicating the ~100-line event-translation body.
                # _agent_sess=None forces a new Copilot SDK session;
                # passing a session object resumes an existing one.
                async def _run_copilot_attempt(
                    _eff: str, _agent_sess: Any
                ) -> AsyncIterator[str]:  # type: ignore[return]
                    nonlocal _t15_last_tool
                    async with agent:
                        _run_opts_inner: dict[str, Any] = {}
                        if _is_byok and _byok_provider:
                            _run_opts_inner["model"] = _byok_model_id
                        elif _final_model_early:
                            _run_opts_inner["model"] = _final_model_early
                        # ── Forward max_tokens to prevent model truncation ─
                        _copilot_max_tok = os.environ.get(
                            "COPILOT_MAX_OUTPUT_TOKENS", ""
                        ).strip()
                        if _copilot_max_tok:
                            _run_opts_inner["max_tokens"] = int(_copilot_max_tok)
                        _stream = agent.run(
                            _eff, stream=True,
                            options=_run_opts_inner if _run_opts_inner else None,
                            session=_agent_sess,
                        )
                        async for _update in _stream:
                            # ONE canonical mapping (event_translator);
                            # Copilot extras (TODO_LIST interception,
                            # elicitation bridge, cleanup) arrive via the
                            # _t15_hooks defined above — not a divergent
                            # copy of the translation.
                            for _ev in _translate_update(
                                _update, _t15_state, _t15_hooks,
                            ):
                                # Remember the last tool STARTED. Read off the
                                # emitted events rather than the translator's
                                # own state so every path counts — a native SDK
                                # tool (ask_user) and a platform tool reach the
                                # stream through different translator branches,
                                # and only the emitted event is common to both.
                                if _ev.get("type") == "TOOL_CALL_START":
                                    _t15_last_tool = str(
                                        _ev.get("toolCallName") or ""
                                    )
                                yield _sse(_ev)

                        # ── Save Copilot session ID before context exits ──
                        # The CopilotClient is closed when async with agent:
                        # exits.  Capture the session ID while still inside
                        # the context manager block.  Gated on capability, not
                        # the registry label — this whole attempt only runs
                        # for Copilot-SDK agents, and label-gating meant a
                        # "maf"-labelled Copilot agent never saved (or later
                        # resumed) its session.
                        if _is_copilot_sdk and thread_id:
                            try:
                                _last_sid = await agent._client.get_last_session_id()
                                _log.info(
                                    "executor.store_copilot_session",
                                    thread_id=thread_id[:12],
                                    sid=str(_last_sid)[:12] if _last_sid else "None",
                                )
                                if _last_sid:
                                    _store_session_id(thread_id, _last_sid)
                                    # Record the model used for this session
                                    # so future requests can detect switches.
                                    _copilot_model_store[thread_id] = (
                                        _final_model_early
                                    )
                            except Exception:
                                _log.exception("executor.store_session_failed")

                # ── Artifact / CUSTOM event relay ────────────────────────
                # Create a queue and expose it so tools (write_artifact,
                # emit_generative_ui, …) can push CUSTOM events into this SSE
                # stream.  The Copilot SDK dispatches tool callables from its
                # JSON-RPC read thread with a FRESH context, so the ContextVar
                # alone is invisible inside those tools — register the queue in
                # the plain _RUN_QUEUES registry (keyed by the session id the
                # tools already hold) as well, so resolve_run_queue() finds it.
                _artifact_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
                _t15_token = _active_run_queue.set(_artifact_queue)
                _t15_qkey = thread_id or run_id
                _register_run_queue(_t15_qkey, _artifact_queue)

                # ── Retry loop: at most 2 attempts ─────────────────────────
                # Attempt 0: normal run (may use a stored Copilot session).
                # Attempt 1 (only if attempt 0 raised a stale-session error):
                #   cleared stale session + history injected as context.
                for _attempt in range(2):
                    _ag_sess: Any = None
                    # Nothing streamed to the user yet in THIS attempt. A
                    # genuine stale-session resume fails inside the SDK's
                    # session setup BEFORE any event is emitted; once output
                    # has streamed, a "session error" is a mid-run failure
                    # (provider 4xx, CLI death) and retrying would re-run the
                    # whole turn and duplicate everything already rendered.
                    _attempt_emitted = False
                    if _copilot_session_id and not _session_retry_attempted:
                        try:
                            _ag_sess = agent.get_session(_copilot_session_id)
                        except Exception:
                            pass
                    try:
                        async for _line in _run_copilot_attempt(_effective_msg, _ag_sess):
                            _attempt_emitted = True
                            yield _line
                            # ── Drain artifact events (write_artifact pushes
                            # artifact_created CUSTOM events here) ──────────
                            while not _artifact_queue.empty():
                                _aev = _artifact_queue.get_nowait()
                                if _aev:
                                    yield _sse(_aev)
                        # Drain any remaining artifact events after stream ends
                        while not _artifact_queue.empty():
                            _aev = _artifact_queue.get_nowait()
                            if _aev:
                                yield _sse(_aev)
                        break  # success — exit retry loop
                    except Exception as _exc:
                        # ── Gap-1 fix: stale Copilot session ─────────────
                        # After a gateway restart the Copilot CLI process is
                        # dead, so resume_session() raises
                        # "Failed to create GitHub Copilot session: ..."
                        # Detect this, wipe the stale record, inject prior
                        # conversation history, and retry with a new session.
                        _is_resume_err = (
                            not _session_retry_attempted
                            and bool(_copilot_session_id)
                            # Emitted-output guard: only a failure BEFORE any
                            # event streamed can be a stale resume. Without
                            # this, a mid-stream provider/session error (the
                            # broad substring match below catches any
                            # "GitHub Copilot session error: …") re-ran the
                            # turn with fresh message ids and the user saw
                            # the entire partial output duplicated.
                            and not _attempt_emitted
                            and (
                                "Failed to create GitHub Copilot session" in str(_exc)
                                or "resume_session" in str(_exc).lower()
                                or (
                                    "session" in str(_exc).lower()
                                    and "error" in str(_exc).lower()
                                )
                            )
                        )
                        if not _is_resume_err:
                            _log.exception(
                                "executor.copilot_maf_stream_error",
                                agent=agent_name,
                            )
                            yield _sse({"type": "RUN_ERROR", "runId": run_id,
                                        "message": str(_exc)})
                            return
                        # Stale session — clear it and prepare a retry.
                        _log.warning(
                            "executor.stale_session_clear",
                            agent=agent_name,
                            thread_id=(thread_id or "")[:16],
                            error=str(_exc)[:120],
                        )
                        _session_retry_attempted = True
                        _copilot_session_id = None  # type: ignore[assignment]
                        _copilot_session_store.pop(thread_id or "", None)
                        _clear_stored_session_id(thread_id)
                        # Inject conversation history so the new session has
                        # context despite losing the Copilot SDK session.
                        _msg_base = (
                            event_payload.get("message")
                            or event_payload.get("user_query")
                            or ""
                        )
                        _prior_msgs = event_payload.get("messages") or []
                        if _prior_msgs:
                            # Window-budgeted (was a flat last-30 x 800 chars).
                            # This is the recovery path after the Copilot CLI
                            # session died, so it is the ONLY thing standing
                            # between the user and a total loss of thread
                            # context — it should carry as much as the model can
                            # actually hold.
                            _rendered = _render_history_block(
                                _prior_msgs, _msg_base,
                                _active_run_model.get() or "",
                            )
                            if _rendered:
                                _effective_msg = (
                                    "## Prior conversation (for context)\n"
                                    + _rendered
                                    + f"\n\n## Current message\n{_msg_base}"
                                )
                                _log.debug(
                                    "executor.session_retry_history_injected",
                                    agent=agent_name,
                                    history_chars=len(_rendered),
                                )
                        # Reset streaming state for a clean second attempt.
                        # _t15_last_tool resets with it: a tool name left over
                        # from the discarded attempt must not decide how THIS
                        # attempt is allowed to end.
                        _t15_state = _TranslationState(run_id)
                        _todo_tracker = _TodoTracker()
                        _t15_last_tool = ""
                        # continue → next iteration of retry loop

                # ── Drain any late-arriving artifact events + reset queue ─
                while not _artifact_queue.empty():
                    _aev = _artifact_queue.get_nowait()
                    if _aev:
                        yield _sse(_aev)
                _active_run_queue.reset(_t15_token)
                _unregister_run_queue(_t15_qkey)

                # ── Premature stream-end detection ───────────────────────
                # If text was started but no tool call completed after it,
                # the model likely stopped mid-thought (token limit reached,
                # content filter, provider error).  Log a warning so
                # operators can diagnose and tune COPILOT_MAX_OUTPUT_TOKENS.
                _t15_had_text = _t15_state.text_started
                for _ev in _close_text_message(_t15_state):
                    yield _sse(_ev)
                if not _t15_had_text:
                    # The stream ended without any *visible* assistant text.
                    # Two very different situations hide behind that, and they
                    # must NOT be treated the same:
                    #
                    #   • Tool work happened → the model almost certainly ran
                    #     out of output budget (max_tokens) part-way through —
                    #     classically mid tool-call or right after the last
                    #     tool — so it never got to write its closing answer.
                    #     Reasoning models (e.g. deepseek) burn the budget on
                    #     reasoning + a large tool argument and get truncated
                    #     (the last tool call's JSON arrives malformed:
                    #     "Unterminated string in JSON ...").  But the run DID
                    #     real work: the tool cards are already folded and
                    #     persisted.  Dead-ending on a hard RUN_ERROR wipes all
                    #     that from the UI and alarms the user — so instead we
                    #     soft-finish: emit a short closing message and FINISH
                    #     the run (records as 'completed', keeps the work).
                    #
                    #   • No tool work at all → the model genuinely returned
                    #     nothing (content filter / provider error).  That is a
                    #     real failure worth surfacing as an error.
                    _tool_activity = bool(_t15_state.fc.seen)
                    _awaiting_user = (
                        _tool_activity
                        and _is_hitl_blocking_tool(_t15_last_tool)
                    )
                    # Not a warning when the agent is simply waiting on the
                    # user — that is normal interactive flow, and logging it as
                    # a problem buries the real ones.
                    (_log.info if _awaiting_user else _log.warning)(
                        "executor.copilot_no_text",
                        agent=agent_name,
                        run_id=run_id,
                        tool_activity=_tool_activity,
                        tools=len(_t15_state.fc.seen),
                        last_tool=_t15_last_tool or None,
                        awaiting_user=_awaiting_user,
                    )
                    _nt_events, _nt_finish = _copilot_no_text_end(
                        run_id=run_id, tool_activity=_tool_activity,
                        last_tool=_t15_last_tool,
                    )
                    for _ev in _nt_events:
                        yield _sse(_ev)
                    if not _nt_finish:
                        # No tool work — a genuine empty response; hard stop.
                        return
                    # Tool work happened: fall through to RUN_FINISHED +
                    # commit detection so the run records as 'completed' and
                    # the folded tool cards are preserved.
                yield _sse({"type": "RUN_FINISHED", "runId": run_id, "threadId": thread_id})

                await _detect_agent_commits(
                    agent_name, _effective_agent_dir, run_id,
                    since_sha=_stream_head_before if _stream_head_before else None,
                    # Stream path: run_agent_stream set _RUN_ORG[thread_id] at run
                    # start and its finally has not fired yet, so with the flag ON
                    # this resolves the run's tenant. flag OFF → byte-identical.
                    thread_id=thread_id,
                )
                return

            # ── Tier 2: instrumented batch fallback ─────────────────────────
            # (orphaned old code removed — see MetoriteCopilotAgent path above)

            # ── Tier 2: instrumented batch fallback ─────────────────────────
            # Wrap every callable tool on the agent so it pushes tool events
            # onto a queue that we drain while the run executes in a task.
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            _t2_token = _active_run_queue.set(queue)  # expose to call_agent for sub-streaming

            _tool_counter: list[int] = [0]

            def _make_tool_shim(original_fn: Any, tool_name: str) -> Any:
                """Return an async wrapper that emits TOOL_CALL_* events."""
                import functools
                import inspect

                @functools.wraps(original_fn)
                async def _shim(*args: Any, **kwargs: Any) -> Any:
                    _tool_counter[0] += 1
                    tool_call_id = f"{run_id}:{_tool_counter[0]}"

                    # Serialise the arguments for the UI
                    try:
                        call_args: dict[str, Any] = {}
                        sig = inspect.signature(original_fn)
                        bound = sig.bind(*args, **kwargs)
                        bound.apply_defaults()
                        for k, v in bound.arguments.items():
                            try:
                                json.dumps(v)  # only include JSON-serialisable values
                                call_args[k] = v
                            except (TypeError, ValueError):
                                call_args[k] = str(v)
                    except Exception:
                        call_args = {}

                    await queue.put({
                        "type": "TOOL_CALL_START",
                        "toolCallId": tool_call_id,
                        "toolCallName": tool_name,
                    })
                    # Emit args as a single TOOL_CALL_ARGS frame
                    if call_args:
                        await queue.put({
                            "type": "TOOL_CALL_ARGS",
                            "toolCallId": tool_call_id,
                            "delta": json.dumps(call_args),
                        })

                    # Permission gate for the native-MAF path (B6): the Copilot
                    # SDK has its own PermissionRequest hook, but MAF tool calls
                    # flow through this shim. Gate + log by tool name (enforce
                    # denies, audit logs-only). Named platform tools approve
                    # today; this is the enforcement point for any future
                    # denyable tool + gives audit parity across runtimes.
                    #
                    # BO-7 cheap win 1/3: reuse call_args (already bound above,
                    # for the UI event) as the tool's real call context instead
                    # of gating on tool_name alone — run_script's actual
                    # path/args now reach decide()'s shell-denylist check here
                    # too, not just on the injected-tool gate's path.
                    try:
                        from acb_skills.permission_policy import (
                            build_tool_call_context as _perm_context,
                            decide as _perm_decide,
                        )
                        _ok, _code, _det = _perm_decide(
                            _perm_context(tool_name, call_args)
                        )
                        _pmode = os.environ.get(
                            "AGENT_PERMISSION_MODE", "enforce"
                        ).strip().lower()
                        if not _ok and _pmode == "enforce":
                            await queue.put({
                                "type": "TOOL_CALL_RESULT",
                                "toolCallId": tool_call_id,
                                "content": (
                                    f"Blocked by permission policy ({_code}). "
                                    "Ask the user to approve this explicitly."
                                ),
                                "success": False,
                            })
                            return (
                                f"[blocked by permission policy: {_code}]"
                            )
                    except Exception:
                        pass

                    try:
                        if not inspect.iscoroutinefunction(original_fn):
                            # Sync tools run inline and can't be interrupted by
                            # wait_for — run them directly.
                            result = original_fn(*args, **kwargs)
                        elif tool_name in (
                            "call_agent",
                            "ask_questions",
                            "ask_user",
                            "request_confirmation",
                            "emit_generative_ui",  # blocks when hitl:true
                        ):
                            # Long-running by design: sub-agent delegation has
                            # its own watchdog, and the blocking HITL tools
                            # park on a Future while the HUMAN answers (their
                            # own budget is HITL_IDLE_TIMEOUT_SECONDS, default
                            # 3600s). Bounding them by the 5-min per-tool
                            # timeout cancelled the wait mid-question — the
                            # question card vanished before the user replied.
                            result = await original_fn(*args, **kwargs)
                        else:
                            # Per-tool timeout: bound async tool execution so a
                            # hung tool (infinite loop, stuck await, network
                            # partition) surfaces as an error instead of blocking
                            # the whole stream until the HTTP-level abort.
                            result = await asyncio.wait_for(
                                original_fn(*args, **kwargs),
                                timeout=_TOOL_EXECUTION_TIMEOUT,
                            )
                    except (ModuleNotFoundError, ImportError) as _imp_exc:
                        # Missing-dependency self-heal: an agent's requirements
                        # may have failed to install (network, apt-only lib) or a
                        # tool imports a package lazily. Rather than fail the tool
                        # with an obscure ModuleNotFoundError, install the missing
                        # module once and retry — the shared venv makes it
                        # importable immediately. One attempt only (no loop).
                        _mod = _missing_module_name(_imp_exc)
                        _healed = False
                        if _mod:
                            _log.info(
                                "executor.tool_dep_selfheal",
                                agent=agent_name, tool=tool_name, module=_mod,
                            )
                            try:
                                from acb_skills.dep_tools import (
                                    install_dependency,
                                )
                                _msg = await install_dependency(_mod)
                                _healed = _msg.startswith("Installed")
                            except Exception:
                                _healed = False
                        if _healed:
                            try:
                                result = await asyncio.wait_for(
                                    original_fn(*args, **kwargs),
                                    timeout=_TOOL_EXECUTION_TIMEOUT,
                                )
                            except Exception as _retry_exc:
                                await queue.put({
                                    "type": "TOOL_CALL_RESULT",
                                    "toolCallId": tool_call_id,
                                    "content": (
                                        f"Error after installing '{_mod}': "
                                        f"{_retry_exc}"
                                    ),
                                    "success": False,
                                })
                                raise
                            else:
                                result_str = str(result) if result is not None else ""
                                await queue.put({
                                    "type": "TOOL_CALL_RESULT",
                                    "toolCallId": tool_call_id,
                                    "content": result_str[:2000],
                                    "success": True,
                                })
                                return result
                        # Couldn't heal — surface a clear, actionable error.
                        await queue.put({
                            "type": "TOOL_CALL_RESULT",
                            "toolCallId": tool_call_id,
                            "content": (
                                f"Error: tool '{tool_name}' needs a package that "
                                f"isn't installed ({_imp_exc}). Auto-install "
                                f"{'failed' if _mod else 'could not identify the module'}"
                                f"; call install_dependency('<package>') and retry."
                            ),
                            "success": False,
                        })
                        raise
                    except asyncio.TimeoutError:
                        await queue.put({
                            "type": "TOOL_CALL_RESULT",
                            "toolCallId": tool_call_id,
                            "content": (
                                f"Error: tool '{tool_name}' exceeded "
                                f"{int(_TOOL_EXECUTION_TIMEOUT)}s and was "
                                "cancelled (possible hang)."
                            ),
                            "success": False,
                        })
                        raise
                    except Exception as exc:
                        await queue.put({
                            "type": "TOOL_CALL_RESULT",
                            "toolCallId": tool_call_id,
                            "content": f"Error: {exc}",
                            "success": False,
                        })
                        raise
                    else:
                        # Mid-run steer (§4.6): a tool boundary is where guidance
                        # that arrived during the turn enters the model's
                        # context. No pending steer => `result` is returned
                        # unchanged, so this path is a dict lookup for every
                        # ordinary call.
                        from orchestrator.steer import (  # noqa: PLC0415
                            decorate_tool_result,
                        )
                        result = decorate_tool_result(result, thread_id)
                        result_str = str(result) if result is not None else ""
                        await queue.put({
                            "type": "TOOL_CALL_RESULT",
                            "toolCallId": tool_call_id,
                            "content": result_str[:2000],  # truncate for SSE safety
                            "success": True,
                        })
                        return result

                return _shim

            # Discover and patch tools on the agent.
            # MAF agents expose tools as `agent.tools` (list) or as annotated
            # methods decorated with @tool.  Try both patterns.
            import inspect
            patched: list[tuple[str, str, Any]] = []  # (attr, name, original)

            _tool_attrs: list[str] = []
            if hasattr(agent, "tools") and isinstance(agent.tools, (list, tuple)):
                for t in agent.tools:
                    fn = getattr(t, "func", t) if not callable(t) else t
                    attr = getattr(fn, "__name__", None)
                    if attr and hasattr(agent, attr):
                        _tool_attrs.append(attr)
            # Also look for methods marked with @tool decorator (common MAF pattern)
            for name in dir(agent):
                if name.startswith("_"):
                    continue
                val = getattr(type(agent), name, None)
                if val and (
                    getattr(val, "_is_tool", False)
                    or getattr(val, "is_tool", False)
                    or getattr(val, "__tool__", False)
                ):
                    _tool_attrs.append(name)

            for attr in set(_tool_attrs):
                original = getattr(agent, attr, None)
                if original and callable(original):
                    shim = _make_tool_shim(original, attr)
                    try:
                        object.__setattr__(agent, attr, shim)
                        patched.append((attr, attr, original))
                    except (AttributeError, TypeError):
                        pass  # some agents use __slots__ or properties — skip

            # Shim injected tools that live only in agent.tools (not as agent attributes).
            # These include call_agent, web_search, fetch_page — appended by
            # _inject_agent_tools.  They are NOT reachable via getattr(agent, name), so
            # the loop above misses them.  We shim them in-place in the list so the
            # Tier 2 stream also shows TOOL_CALL_START/END events for delegation and
            # web calls.
            _shimmed_list_indices: list[tuple[int, Any]] = []  # (index, original)
            if hasattr(agent, "tools") and isinstance(agent.tools, (list, tuple)):
                _tools_list = agent.tools
                for _idx, _t in enumerate(_tools_list):
                    _fn = getattr(_t, "func", _t)
                    _fn_name = getattr(_fn, "__name__", None)
                    if _fn_name and not hasattr(agent, _fn_name) and callable(_fn):
                        _shim = _make_tool_shim(_fn, _fn_name)
                        try:
                            _tools_list[_idx] = _shim
                            _shimmed_list_indices.append((_idx, _t))
                        except (AttributeError, TypeError):
                            pass

            # Run the agent in a background task.
            message = _build_event_message(agent_name, run_id, event_payload, integrations)

            async def _run_task() -> str:
                async with contextlib.AsyncExitStack() as stack:
                    # Pre-configure CopilotClient to deny the built-in shell tool.
                    # On Windows the shell tool requires pwsh.exe (PowerShell 7+) which
                    # may not be installed.  Our Python tools (e.g. zoho_crm) work fine
                    # without it, and we don't want the LLM to fall back to shell execution.
                    try:
                        from copilot import \
                            CopilotClient as _CopilotClient
                        if hasattr(agent, "_client") and agent._client is None:
                            _agent_settings = getattr(agent, "_settings", {}) or {}
                            _cli_opts: dict[str, Any] = {}
                            # For GitHub Copilot agents (repo-sourced), allow all tools
                            # including shell — pwsh 7.6.2 is installed. For local/built-in
                            # MAF agents with Python tools, deny shell to prevent the LLM
                            # from bypassing structured Python tools with raw shell calls.
                            if _agent_runtime != "github-copilot":
                                _cli_opts["cli_args"] = ["--deny-tool", "shell"]
                            _cli_path = _agent_settings.get("cli_path")
                            if _cli_path:
                                _cli_opts["cli_path"] = _cli_path
                            _log_level = _agent_settings.get("log_level")
                            if _log_level:
                                _cli_opts["log_level"] = _log_level
                            # Headless auth: explicit Copilot token (servers
                            # have no logged-in copilot CLI user).
                            # Fall back to the canonical GITHUB_TOKEN — the
                            # only secret the connect UI / settings store / DB
                            # hydration actually populate. Without this, the
                            # headless CLI has no auth and no logged-in copilot
                            # user, yielding "Authorization error, run /login".
                            _cop_tok = (
                                os.environ.get("COPILOT_GITHUB_TOKEN")
                                or os.environ.get("GITHUB_COPILOT_TOKEN")
                                or os.environ.get("GITHUB_TOKEN")
                                or ""
                            ).strip()
                            if _cop_tok:
                                _cli_opts["github_token"] = _cop_tok
                            agent._client = _CopilotClient(_cli_opts if _cli_opts else None)
                            agent._owns_client = True
                    except Exception:
                        pass

                    if hasattr(type(agent), "__aenter__"):
                        await stack.enter_async_context(agent)
                    # Apply the risk-aware permission handler if needed (B6).
                    try:
                        if hasattr(agent, "_permission_handler") and agent._permission_handler is None:
                            agent._permission_handler = _copilot_permission_handler()
                    except Exception:
                        pass
                    # Pass history as proper MAF Message objects so the model sees
                    # full user/assistant turn structure, not a flat string.
                    #
                    # This used to hand-roll the message list with a blind
                    # `[-20:]` count cap ("last 10 exchanges") behind an
                    # is_byok gate — the same two bugs fixed on the other paths:
                    # a count cap spends a 128K window on ~2% of itself, and the
                    # gate meant a non-BYOK agent silently got a flat string here
                    # while its other turns got real turn structure. It also
                    # contradicted _compose_maf_run_input's docstring, which
                    # already claimed both paths shared one assembler.
                    #
                    # Now it genuinely is one assembler: token-budgeted to the
                    # model's real window, current-turn dedup, system context
                    # preserved, and degrades to the composed string when there's
                    # no history to structure.
                    _run_input = _compose_maf_run_input(
                        agent_name, run_id, event_payload, integrations)
                    response = await agent.run(_run_input)
                return getattr(response, "text", "") or ""

            run_task = asyncio.create_task(_run_task())

            # Drain the queue until the task finishes.
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.1)
                    if ev is None:
                        break
                    yield _sse(ev)
                except asyncio.TimeoutError:
                    if run_task.done():
                        # Drain any remaining events
                        while not queue.empty():
                            ev = queue.get_nowait()
                            if ev:
                                yield _sse(ev)
                        break

            _active_run_queue.reset(_t2_token)  # restore after drain

            # Restore patched tools (attribute-based)
            for attr, _, original in patched:
                try:
                    object.__setattr__(agent, attr, original)
                except Exception:
                    pass

            # Restore shimmed list entries
            if hasattr(agent, "tools") and isinstance(agent.tools, (list, tuple)):
                for _idx, _orig in _shimmed_list_indices:
                    try:
                        agent.tools[_idx] = _orig
                    except Exception:
                        pass

            # Get the final text result
            try:
                text = await run_task
            except AgentRunError:
                raise
            except Exception as exc:
                raise exc

            # Strip integration setup tokens (same as batch path)
            setup_token_re = __import__("re").compile(r"<<<SETUP:[^>]+>>>")
            raw_matches = __import__("re").findall(
                r"<<<SETUP:([^:]+):([A-Z0-9_]+)=([^>]+)>>>", text
            )
            if raw_matches:
                text = setup_token_re.sub("", text).strip()
                vars_to_save = [{"key": k, "value": v.strip()} for _, k, v in raw_matches if v.strip()]
                if vars_to_save:
                    _GATEWAY_URL = settings.gateway_base_url if hasattr(settings, "gateway_base_url") else "http://127.0.0.1:8000"
                    _token = getattr(settings, "gateway_internal_token", "") or getattr(settings, "litellm_master_key", "")
                    try:
                        import httpx
                        async with httpx.AsyncClient(timeout=5) as c:
                            await c.post(
                                f"{_GATEWAY_URL}/integrations/configure",
                                json={"vars": vars_to_save},
                                headers={"Authorization": f"Bearer {_token}"},
                            )
                    except Exception:
                        pass

            # Emit the final text as TOKEN-STREAMED TEXT_MESSAGE_CONTENT deltas
            # via the shared translator, so even the batch tier speaks the
            # message-id-native START/CONTENT/END protocol.
            for _t2_ev in _text_message_events(text):
                yield _sse(_t2_ev)
                if _t2_ev.get("type") == "TEXT_MESSAGE_CONTENT":
                    await asyncio.sleep(0)  # yield event loop so SSE flushes
            # RUN_FINISHED must be emitted INSIDE the try block — the finally
            # below resets the relay contextvar and marks the thread inactive,
            # so a later yield would never reach Redis (reconnecting clients
            # would hang waiting for the run to finish).
            yield _sse({"type": "RUN_FINISHED", "runId": run_id, "threadId": thread_id})

    except AgentRunError:
        raise
    except Exception as exc:
        yield _sse({
            "type": "RUN_ERROR",
            "message": str(exc),
            "code": type(exc).__name__,
        })
        return
    finally:
        # ── Live activity feed (E2): agent activation END ────────────────────
        # Clears this run from the "running now" panel and stamps duration +
        # status. sys.exc_info() is set here iff we're unwinding an exception.
        try:
            import sys as _sys
            import time as _time
            from acb_common import publish_activity
            _status = "error" if _sys.exc_info()[0] is not None else "completed"
            _dur_ms = int((_time.monotonic() - _activity_started) * 1000)
            publish_activity(
                kind="agent", phase="end",
                agent=agent_name, run_id=run_id, thread_id=thread_id,
                status=_status, duration_ms=_dur_ms, source=_corr_source,
            )
        except Exception:
            pass

        # Deactivate stream relay so the reconnect endpoint knows the run
        # has finished and can drain remaining events from the Redis stream.
        # Wait for any in-flight ordered pushes first so RUN_FINISHED lands
        # in Redis BEFORE the active flag is cleared.
        _pending_push = _push_chains.get(thread_id)
        if _pending_push is not None:
            try:
                await _pending_push
            except Exception:
                pass
        _stream_relay_thread_id.reset(_relay_token)
        _active_run_model.reset(_model_token)
        # Same reason, for the thing that says WHO this run was: an acting user
        # left bound is inherited by whatever runs next on this task (S1-4).
        _unbind_run_identity(_identity_binding)
        # Same rule for the tenant (WS-29 MT-1d / H4): drop this run's
        # worker-thread org record and release the async tenant binding, so a
        # tenant left bound is never inherited by whatever runs next on this task.
        # GUARDED pop (WS-29 slice 3, P2): only drop the record if it is STILL
        # this run's org — see :func:`_guarded_pop_run_org`.
        _guarded_pop_run_org(thread_id, organization_id)
        if _tenant_token is not None:
            try:
                from acb_common.db import release_tenant
                release_tenant(_tenant_token)
            except Exception:
                pass
        # B6 Phase-5 Tier 0: tear down this run's scoped integration creds so
        # they don't linger in the shared process env for the next agent.
        _release_run_credentials(_integration_env_token)
        try:
            from acb_common import clear_run_context
            clear_run_context()
        except Exception:
            pass
        _thread_emit_seq.pop(thread_id, None)  # P1-5 counter cleanup
        # Drop the cross-worker HITL applier for this run (P1-2).  run_detached's
        # finally also clears ALL handlers for the thread, but this generator can
        # outlive/precede that path (batch tiers), so unregister defensively.
        try:
            from orchestrator.stream_relay import (
                unregister_control_command as _unreg_ctl,
            )
            _unreg_ctl(thread_id, "respond_input")
        except Exception:
            pass
        if _relay_mark_inactive is not None:
            try:
                await _relay_mark_inactive(thread_id)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Self-annealing engine  DETECT → FIX → RETRY → LLM RECOVERY
# ---------------------------------------------------------------------------

def _classify_error(exc: Exception) -> str:
    """Return a short error class label used to pick the right fix strategy."""
    msg = str(exc).lower()
    etype = type(exc).__name__
    if isinstance(exc, UnicodeDecodeError):
        return "encoding"
    if isinstance(exc, (IndexError, KeyError)):
        return "index"
    if isinstance(exc, ImportError):
        return "import"
    if any(t in msg for t in ("rate limit", "ratelimit", "429", "overload")):
        return "rate_limit"
    if any(t in msg for t in ("timeout", "connection", "503", "unavailable")):
        return "transient"
    if "no choices" in msg or "choices" in msg:
        return "empty_choices"
    if "api key" in msg or "authentication" in msg or "unauthorized" in msg:
        return "auth"
    return f"unknown:{etype}"


def _fix_encoding_in_dir(agent_dir: str | None) -> bool:
    """Replace cp1252 smart-quote bytes with their UTF-8 equivalents in all
    .md and .py files under *agent_dir*. Returns True if anything was fixed."""
    if not agent_dir:
        return False
    _CP1252_MAP = {
        b"\x96": "\u2013".encode(),   # en-dash
        b"\x97": "\u2014".encode(),   # em-dash
        b"\x91": "\u2018".encode(),   # left single quote
        b"\x92": "\u2019".encode(),   # right single quote
        b"\x93": "\u201c".encode(),   # left double quote
        b"\x94": "\u201d".encode(),   # right double quote
        b"\x85": "\u2026".encode(),   # ellipsis
    }
    fixed_any = False
    for p in Path(agent_dir).rglob("*"):
        if p.suffix not in {".md", ".py", ".txt"} or not p.is_file():
            continue
        raw = p.read_bytes()
        if not any(b in raw for b in _CP1252_MAP):
            continue
        patched = raw
        for bad, good in _CP1252_MAP.items():
            patched = patched.replace(bad, good)
        try:
            patched.decode("utf-8")   # verify the result is valid UTF-8
            p.write_bytes(patched)
            fixed_any = True
            _log.info("self_anneal.encoding_fixed", file=str(p))
        except UnicodeDecodeError:
            pass   # skip files with more exotic encodings
    return fixed_any


async def _self_anneal(
    *,
    agent_name: str,
    run_id: str,
    thread_id: str,
    event_payload: dict[str, Any],
    agent_dir: str | None,
    error: Exception,
) -> dict[str, Any] | None:
    """Self-annealing loop.

    1. Classify the error.
    2. Apply an in-process fix if one exists for this error class.
    3. Retry the graph (up to _MAX_ANNEAL_ATTEMPTS times).
    4. If all retries fail, ask the LLM to explain + suggest next steps.
    5. Return None only when even the LLM call fails — caller then raises.
    """
    error_class = _classify_error(error)
    _log.info(
        "self_anneal.start",
        agent=agent_name, run_id=run_id,
        error_class=error_class, error=str(error)[:200],
    )

    # ── In-process fixes ──────────────────────────────────────────────────
    if error_class == "encoding":
        if _fix_encoding_in_dir(agent_dir):
            _log.info("self_anneal.encoding_fix_applied", agent=agent_name)
            # Reload the agent and retry
            for attempt in range(_MAX_ANNEAL_ATTEMPTS):
                await asyncio.sleep(0.5 * (attempt + 1))
                try:
                    from acb_skills.integrations import \
                        build_integrations as _bi
                    from acb_skills.loader import \
                        load_agent as _load
                    settings = get_settings()
                    with _load(agent_name, run_id=run_id) as loaded:
                        integrations, integration_warnings = _bi(
                            loaded.config.get("integrations", []),
                            loaded.config.get("optional_integrations", []),
                            settings,
                        )
                        agents = loaded.build_agents()
                        _apply_agent_md_overrides(
                            agents, loaded.agent_dir, agent_name,
                        )
                        _inject_agent_tools(agents, agent_name=agent_name)
                        result = await _run_with_maf_agent(
                            agents,
                            agent_name=agent_name,
                            run_id=run_id,
                            thread_id=thread_id,
                            event_payload={
                                **event_payload,
                                "integration_warnings": integration_warnings,
                            },
                            integrations=integrations,
                        )
                    _log.info("self_anneal.retry_success",
                              agent=agent_name, attempt=attempt + 1)
                    return result
                except Exception as retry_exc:
                    _log.warning("self_anneal.retry_failed",
                                 agent=agent_name, attempt=attempt + 1,
                                 error=str(retry_exc)[:200])
                    error = retry_exc  # update for LLM recovery below

    elif error_class in ("transient", "rate_limit", "empty_choices"):
        for attempt in range(_MAX_ANNEAL_ATTEMPTS):
            wait = 2 ** (attempt + 1)   # 2 s, 4 s
            _log.info("self_anneal.transient_retry",
                      agent=agent_name, attempt=attempt + 1, wait=wait)
            await asyncio.sleep(wait)
            try:
                settings = get_settings()
                from acb_skills.integrations import \
                    build_integrations as _bi
                from acb_skills.loader import \
                    load_agent as _load
                with _load(agent_name, run_id=run_id) as loaded:
                    integrations, integration_warnings = _bi(
                        loaded.config.get("integrations", []),
                        loaded.config.get("optional_integrations", []),
                        settings,
                    )
                    agents = loaded.build_agents()
                    _apply_agent_md_overrides(
                        agents, loaded.agent_dir, agent_name,
                    )
                    _inject_agent_tools(agents, agent_name=agent_name)
                    result = await _run_with_maf_agent(
                        agents,
                        agent_name=agent_name,
                        run_id=run_id,
                        thread_id=thread_id,
                        event_payload={
                            **event_payload,
                            "integration_warnings": integration_warnings,
                        },
                        integrations=integrations,
                    )
                _log.info("self_anneal.retry_success",
                          agent=agent_name, attempt=attempt + 1)
                return result
            except Exception as retry_exc:
                _log.warning("self_anneal.retry_failed",
                             agent=agent_name, attempt=attempt + 1,
                             error=str(retry_exc)[:200])
                error = retry_exc

    # ── LLM recovery: explain the error in plain language ─────────────────
    return await _llm_recovery(agent_name, event_payload, error, error_class)


async def _llm_recovery(
    agent_name: str,
    event_payload: dict[str, Any],
    error: Exception,
    error_class: str,
) -> dict[str, Any] | None:
    """Ask the LLM to produce a helpful natural-language recovery reply."""
    # Map known error classes to user-facing hints so the LLM can be specific.
    _HINTS: dict[str, str] = {
        "auth": (
            "The problem is a missing or invalid API key. "
            "Tell the user which integration needs to be configured and how to do it "
            "using the <<<SETUP:service:ENV_VAR=value>>> token."
        ),
        "encoding": (
            "The problem was a file encoding error (bad bytes in a config or skill file). "
            "The system has already attempted an automatic fix. "
            "Ask the user to try their request again."
        ),
        "rate_limit": (
            "The LLM provider hit a rate limit. "
            "Suggest the user waits 30 seconds and tries again."
        ),
        "import": (
            "A required Python package is missing. "
            "Tell the user which package and suggest installing it."
        ),
    }
    hint = _HINTS.get(error_class, "")

    try:
        from acb_llm import LLMTier, complete

        messages: list[dict[str, str]] = list(event_payload.get("messages", []))
        latest: str = event_payload.get("message", "")

        system = (
            f"You are {agent_name}, a helpful AI assistant. "
            "An internal error just occurred. "
            "Respond directly to the user's last message as helpfully as possible. "
            "Apologise briefly (one sentence), then either complete the task "
            "using a simpler approach, or tell the user exactly what to do next. "
            f"{hint} "
            "Never show raw Python tracebacks or variable names. Be concise."
        )
        recovery_msgs: list[dict[str, str]] = [
            {"role": "system", "content": system},
            *messages,
            *(
                [{"role": "user", "content": latest}]
                if latest and (not messages or messages[-1].get("role") != "user")
                else []
            ),
            {
                "role": "user",
                "content": (
                    f"[Internal error — do not repeat to user] "
                    f"{type(error).__name__}: {str(error)[:300]}"
                ),
            },
        ]

        content = await complete(
            tier=LLMTier.TIER_2,
            messages=recovery_msgs,
            max_tokens=400,
        )
        _log.info("self_anneal.llm_recovery_success", agent=agent_name)
        return {"result": {"role": "assistant", "content": content}}
    except Exception as llm_exc:
        _log.warning("self_anneal.llm_recovery_failed",
                     agent=agent_name, error=str(llm_exc))
        return None


# ---------------------------------------------------------------------------
# Internal: run a MAF agent list (replaces LangGraph _execute_graph)
# ---------------------------------------------------------------------------

# Opaque handle returned by ``_bind_run_credentials`` and passed back to
# ``_release_run_credentials`` at the run's teardown site. It is a ContextVar
# ``Token``, not an env restore map — see MT-0a below.
IntegrationEnvToken = Any


def _bind_run_credentials(integrations: dict[str, Any]) -> Any:
    """Bind this run's resolved integration credentials to its async context.

    Skill scripts read credentials by canonical env-var name. The executor
    resolves them into a structured dict, so something has to bridge the two.
    Until MT-0a that bridge was the gateway's process-global ``os.environ``,
    written at run start and restored at teardown.

    **MT-0a (`saas_multitenancy.md` §6.1) replaces the bridge with a ContextVar.**
    The env approach removed *permanent accumulation* but could not remove
    *concurrent* exposure, and the old docstring here said so outright: under
    overlapping in-process runs the scoping was "best-effort — two overlapping
    runs still share the env for the overlap window". Under one tenant that is a
    within-org concern. Under two it is a **credential leak**: tenant A's token
    is readable by tenant B's concurrently-running agent, and agents execute
    model-generated tool calls over content ingested from email and WhatsApp.

    A ContextVar is per-task and is copied into tasks created from the binding
    context, so the overlap window does not exist. Consumers:

    * subprocess scripts — ``code_tools._script_env`` reads the bound values;
    * in-process skills — call ``acb_skills.integrations.credential(name)``,
      never ``os.getenv`` (see that function's docstring for precedence).

    Returns a token the caller **must** pass to
    :func:`_release_run_credentials` at teardown. Nothing is written to
    ``os.environ``; the operator's own environment is left exactly as it was.
    """
    from acb_skills.integrations import bind_run_credentials

    return bind_run_credentials(integrations)


def _release_run_credentials(token: Any) -> None:
    """Undo :func:`_bind_run_credentials`. Never raises.

    Called at every run teardown site (batch ``AsyncExitStack`` callback,
    streaming ``finally``, sub-agent ``finally``). A teardown failure must not
    mask the run's own outcome, and it must not leave credentials readable —
    ``release_run_credentials`` falls back to an explicit empty bind if the
    token cannot be reset from the calling context.
    """
    from acb_skills.integrations import release_run_credentials

    release_run_credentials(token)


async def _run_with_maf_agent(
    agents: list[Any],
    *,
    agent_name: str,
    run_id: str,
    thread_id: str,
    event_payload: dict[str, Any],
    integrations: dict[str, Any],
) -> dict[str, Any]:
    """Execute the primary agent from *agents* via MAF and return a normalised result dict.

    Accepts any MAF ``BaseAgent`` subclass including ``GitHubCopilotAgent``.
    Automatically calls ``start()`` / ``stop()`` if the agent supports it.
    """
    import contextlib

    if not agents:
        raise ValueError(f"Agent {agent_name!r}: build_agents() returned an empty list.")

    agent = agents[0]

    # GitHubCopilotAgent requires on_permission_request to approve tool calls.
    # Agent repos often omit it; patch _permission_handler directly so sessions
    # are created without raising AgentException. B6: use the risk-aware handler
    # (blocks dangerous shell / out-of-workspace writes; logs privileged ops).
    try:
        _ph = _copilot_permission_handler()
        for _a in agents:
            if hasattr(_a, "_permission_handler") and _a._permission_handler is None:
                _a._permission_handler = _ph
    except Exception:
        pass

    # Build the input for agent.run().
    # For chat events that carry a prior message history (payload["messages"]),
    # pass the full conversation as a Sequence[Message] so the LLM has proper
    # multi-turn context. For webhook / event-driven payloads (no "messages" key),
    # fall back to the single-string path which serialises the event payload.
    run_input: Any
    prior_msgs: list[dict[str, str]] = event_payload.get("messages") or []
    current_msg: str = event_payload.get("message") or event_payload.get("user_query") or ""

    _loader = event_payload.get("_history_loader")
    if (prior_msgs or _loader) and current_msg:
        # Chat path: reconstruct proper Message sequence so the LLM sees the
        # full conversation window, not just the latest turn.  C2: routes
        # through the ONE server-side assembler (token-budgeted + current-turn
        # dedup + DB-rebuild-when-empty) — identical to the streaming path's
        # _compose_maf_run_input, replacing the old blind last-50 count cap.
        try:
            from acb_llm import assemble_run_context
            from agent_framework._types import \
                Message as _Message

            # Fold the integrations preamble into the leading system context so
            # the assembler carries it as one system message (it emits a single
            # leading system block).
            system_parts: list[str] = []
            system_context = event_payload.get("system_context") or ""
            if system_context.strip():
                system_parts.append(system_context.strip())
            integration_warnings: dict[str, str] = event_payload.get("integration_warnings", {})
            if integrations:
                system_parts.append(
                    "Connected integrations: " + ", ".join(sorted(integrations.keys())) + "."
                )
            if integration_warnings:
                missing = ", ".join(sorted(integration_warnings.keys()))
                system_parts.append(
                    f"Missing integrations (not yet configured): {missing}. "
                    "If the user task requires one of these, ask them to provide the credential. "
                    "When they do, output: <<<SETUP:service_name:ENV_VAR_NAME=value>>>"
                )

            _model = _active_run_model.get() or ""
            assembled = assemble_run_context(
                system_context="\n".join(system_parts),
                history=prior_msgs,
                current_message=current_msg,
                model=_model,
                max_output_tokens=_reserved_output_tokens(_model),
                history_loader=_loader if callable(_loader) else None,
            )
            # MAF _types.Message takes content as a list of parts.
            run_input = [
                _Message(m["role"], [m["content"]]) for m in assembled
            ]
        except Exception:
            # Fallback: MAF version mismatch — use plain string
            run_input = _build_event_message(agent_name, run_id, event_payload, integrations)
    else:
        # Webhook / event path: single string prompt.
        run_input = _build_event_message(agent_name, run_id, event_payload, integrations)

    # Inject resolved integration credentials into os.environ so that tool
    # subprocesses (e.g. zoho_crm.py calling os.getenv("ZOHO_CLIENT_ID")) can
    # read them. This bridges the gap between the structured integrations dict
    # and the env-var-based credential reading in skill scripts.  B6 Phase-5
    # Tier 0: scoped to this run — the restore token is torn down on the
    # AsyncExitStack below (fires even on exception) so creds don't linger.
    _integration_env_token = _bind_run_credentials(integrations)

    async with contextlib.AsyncExitStack() as stack:
        stack.callback(_release_run_credentials, _integration_env_token)
        # GitHubCopilotAgent (and any agent with lifecycle) requires start/stop.
        # Standard Agent has a no-op __aenter__/__aexit__ — both are safe here.
        if hasattr(type(agent), "__aenter__"):
            await stack.enter_async_context(agent)
        response = await agent.run(run_input)

    text: str = getattr(response, "text", "") or ""
    return {"answer": text, "run_id": run_id, "agent": agent_name, "result": text}


def _compose_maf_run_input(
    agent_name: str,
    run_id: str,
    event_payload: dict[str, Any],
    integrations: dict[str, Any],
) -> Any:
    """Build the input passed to a native MAF ``agent.run(...)`` call.

    Mirrors the Tier 2 batch path's message construction so the streaming and
    batch paths feed the agent identically:

    * History present → a structured ``list[Message]`` (the caller's
      ``system_context`` as a leading system message, then the budgeted prior
      turns, then the current user turn) so the model sees full turn structure.
    * Otherwise → the composed prompt string from :func:`_build_event_message`
      (which already folds in memory_context + system_context + history).

    C2: the structured-message branch routes through the ONE server-side
    assembler ``acb_llm.assemble_run_context`` — token-budgeted (fit to the
    resolved model's window) + current-turn dedup + DB-rebuild-when-empty —
    instead of the old blind ``[-20:]`` count cap.  The batch path
    (:func:`run_agent`) calls the same assembler, so streaming and batch feed
    the model identically (the drift the two duplicated slicers risked).

    This is deliberately NOT gated on BYOK any more. It used to be, which meant
    a non-BYOK native-MAF agent's STREAMING turn silently fell back to the flat
    string prompt while its own BATCH turn got the token-budgeted assembly — the
    same agent remembered a different amount of the conversation depending on
    whether it streamed. The assembler already degrades safely when the model is
    unknown (no token fit, just a turn cap), so there is nothing BYOK-specific
    about wanting real turn structure.
    """
    message = _build_event_message(agent_name, run_id, event_payload, integrations)
    history_msgs = event_payload.get("messages") or []
    current_msg_text = (
        event_payload.get("message") or event_payload.get("user_query") or ""
    )
    system_context = event_payload.get("system_context") or ""
    # Rebuild history from the store when the caller sent none (API/webhook) —
    # a loader is threaded in via the payload by the route layer when it has a
    # thread_id; absent one, this is a no-op and behaviour is unchanged.
    _loader = event_payload.get("_history_loader")
    if history_msgs or _loader:
        try:
            from acb_llm import assemble_run_context
            from agent_framework import Message as _MAFMsg
            _model = _active_run_model.get() or ""
            assembled = assemble_run_context(
                system_context=system_context,
                history=history_msgs,
                current_message=current_msg_text,
                model=_model,
                max_output_tokens=_reserved_output_tokens(_model),
                history_loader=_loader if callable(_loader) else None,
            )
            maf_messages = [
                _MAFMsg(role=m["role"], content=m["content"]) for m in assembled
            ]
            return maf_messages if maf_messages else message
        except Exception:
            return message
    return message


# ── History budgeting for the string-prompt path ──────────────────────────
# Fraction of the model's real context window this path may spend on prior
# turns. The rest is left for the system prompt, tool schemas, the current turn
# and the model's own output.
_HISTORY_WINDOW_FRACTION: float = float(
    os.environ.get("HISTORY_WINDOW_FRACTION", "0.5")
)
# Floor so a tiny/unknown window still carries usable history, and a hard stop
# so a pathological thread can't walk unbounded.
_HISTORY_MIN_CHARS: int = 8_000
_HISTORY_MAX_MESSAGES: int = 400

# Absolute ceiling on history, independent of how big the model is.
#
# A pure fraction-of-window budget scales with the MODEL, and the fleet now has
# million-token models — 50% of deepseek-v4-pro's 1M window is 500K tokens of
# prior conversation resent on EVERY turn, billed every time. That is a cost
# bug, not a context win: past ~100K tokens the extra history is nearly all
# stale turns the model doesn't need, and on a frontier-priced model it is the
# difference between cents and dollars per message.
#
# 96K tokens is far more history than any real conversation uses (and still an
# order of magnitude above the flat caps this budgeting replaced), so it binds
# only on the huge-window models where the fraction turns absurd. It does not
# reduce what any current tier gets.
_HISTORY_MAX_TOKENS: int = int(os.environ.get("HISTORY_MAX_TOKENS", "96000"))

# Room to hold back for the model's own reply when budgeting a prompt.
#
# Tracks v1_compat's _DEFAULT_MAX_OUTPUT_TOKENS deliberately — that route is
# what actually puts max_tokens on the wire, and providers reject a request when
# prompt + max_tokens exceeds the window. The two must agree or we budget a
# prompt the completion can't fit behind, so they read the same env var.
_RESERVED_OUTPUT_TOKENS: int = int(
    os.environ.get("GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS", "32000")
)


def _reserved_output_tokens(model: str) -> int:
    """Tokens to reserve for the completion when fitting a prompt to *model*.

    ``assemble_run_context`` defaulted this to 1024 while the gateway now lets a
    model emit up to 32000 — so a prompt was budgeted to fill the window minus
    1K, the model tried to write 32K into what was left, and the provider
    rejected the request outright. Reserve what the gateway will really send.
    """
    try:
        from acb_llm.model_limits import get_limits
        limits = get_limits(model)
    except Exception:
        return _RESERVED_OUTPUT_TOKENS
    # Only a limit we vouch for may shrink the reservation (same rule as the
    # gateway's clamp): litellm's stale caps would under-reserve.
    if limits.max_output_source in ("curated", "env") and limits.max_output > 0:
        return min(_RESERVED_OUTPUT_TOKENS, limits.max_output)
    return _RESERVED_OUTPUT_TOKENS


def _history_char_budget(model: str) -> int:
    """Chars of prior conversation the string-prompt path may spend, from the
    model's REAL window (tokens → chars at the repo's 4-chars/token heuristic).

    Bounded below by a floor (a tiny window still gets usable history) and above
    by ``_HISTORY_MAX_TOKENS`` (a 1M-token window must not bill 500K tokens of
    history per turn just because it can).

    Fails open to the floor if the window can't be resolved — never raises.
    """
    try:
        from acb_llm.model_limits import get_limits
        win = int(get_limits(model).context_window)
    except Exception:
        win = 0
    if win <= 0:
        return _HISTORY_MIN_CHARS
    tokens = min(int(win * _HISTORY_WINDOW_FRACTION), _HISTORY_MAX_TOKENS)
    return max(_HISTORY_MIN_CHARS, tokens * 4)


def _render_history_block(
    history: list[dict[str, Any]], current_msg: str, model: str,
) -> str:
    """Render prior turns as text, budgeted to the model's real context window.

    Replaces the old model-BLIND caps (last 16 messages, each clipped to 600
    chars). Those threw away almost everything: ~16x600 chars ~= 2,400 tokens is
    under 2% of a 128k window, and the 600-char slice shredded even the
    immediately-preceding turn — so a user who pasted a document, diff, or error
    log had it cut to 600 chars on the very next message.

    Budget scales with the resolved model's window, so a large-context model
    keeps the whole thread while a small one still degrades safely. Oldest turns
    drop first (mirroring assemble_run_context's whole-turn eviction); a single
    oversized message is trimmed rather than allowed to eat the whole budget.
    """
    budget = _history_char_budget(model)
    # Cap any ONE message so a single giant turn can't crowd out all the others.
    per_msg_cap = max(_HISTORY_MIN_CHARS // 2, budget // 4)
    total = len(history)
    picked: list[str] = []
    used = 0

    # Walk newest → oldest so the most relevant turns win the budget; the
    # surviving set is rendered back in chronological order below.
    for idx in range(total - 1, max(-1, total - _HISTORY_MAX_MESSAGES - 1), -1):
        m = history[idx]
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        # Skip the current turn if the caller also included it in history.
        if role == "user" and content == current_msg.strip():
            continue
        # Technique #1 (tool-result clearing): older assistant turns don't need
        # their raw tool-call JSON re-read — only their prose conclusions.
        if idx < total - 3 and role == "assistant":
            content = _TOOL_RESULT_RE.sub("", content).strip()
            if not content:
                continue
        if len(content) > per_msg_cap:
            content = content[:per_msg_cap] + "…"
        if used + len(content) > budget:
            break  # budget exhausted — everything older is dropped
        used += len(content)
        picked.append(f"{'User' if role == 'user' else 'Assistant'}: {content}")

    picked.reverse()  # back to chronological order
    return "\n".join(picked)


def _build_event_message(
    agent_name: str,
    run_id: str,
    event_payload: dict[str, Any],
    integrations: dict[str, Any],
) -> str:
    """Compose a prompt string from an event payload dict.

    Handles both interactive chat events (payload has ``message`` key) and
    webhook events (arbitrary payload keys).

    When ``messages`` is present in the payload (chat history from the frontend),
    it is prepended as conversation context so the agent has full continuity
    regardless of which model/runtime processed previous turns.
    """
    integration_warnings: dict[str, str] = event_payload.get("integration_warnings", {})
    parts: list[str] = []

    # Integration availability context (mirrors old _build_initial_state system message)
    if integrations:
        parts.append("Connected integrations: " + ", ".join(sorted(integrations.keys())) + ".")
    if integration_warnings:
        missing = ", ".join(sorted(integration_warnings.keys()))
        parts.append(
            f"Missing integrations (not yet configured): {missing}. "
            "If the user task requires one of these, ask them to provide the credential. "
            "When they do, output: <<<SETUP:service_name:ENV_VAR_NAME=value>>>"
        )

    # Conversation history — prepend prior turns so model switching mid-chat
    # preserves full context even when switching between CLI / BYOK / MAF paths.
    history: list[dict[str, str]] = event_payload.get("messages") or []
    current_msg = event_payload.get("message") or event_payload.get("user_query") or ""

    # Memory context (pre-enriched by the route handler from Mem0) —
    # inject as a system-level preamble so Tier 2 agents also benefit.
    memory_ctx = event_payload.get("memory_context") or ""
    if memory_ctx:
        parts.append("## Memory from past conversations\n" + memory_ctx)

    # Caller-supplied system context (persona / app context — e.g. the email
    # app's currently-selected account + open email).  Injected as a preamble so
    # the agent operates with that context without the user having to repeat it.
    system_ctx = event_payload.get("system_context") or ""
    if system_ctx:
        parts.append("## Current context\n" + system_ctx)
    if history:
        _rendered = _render_history_block(
            history, current_msg, _active_run_model.get() or "",
        )
        if _rendered:
            parts.append("Conversation history:\n" + _rendered)

    # Main user message — prefer explicit "message" or "user_query" keys
    if current_msg:
        parts.append(current_msg)
    else:
        # Webhook / event-driven path: serialise key payload fields as context
        import json
        skip = {"integration_warnings", "messages"}
        keys = [k for k in event_payload if k not in skip]
        if keys:
            parts.append(
                f"Event payload: {json.dumps({k: event_payload[k] for k in keys[:10]}, default=str)}"
            )

    return "\n".join(parts) if parts else f"[Agent run: {agent_name} / {run_id}]"


# ---------------------------------------------------------------------------
# No-text stream-end handling (Copilot Tier 1.5)
# ---------------------------------------------------------------------------

# The fallback message shown when a tool-using run ends without a final
# written answer.
#
# It deliberately does NOT name a cause. The first version asserted "the
# response hit its output length limit", which was a guess dressed as a
# diagnosis: all this code actually knows is that no text arrived. The very
# first time it fired in production the real reason was something else
# entirely (the agent had asked a question and was waiting) — so it told the
# user their answer had been truncated, and to say "continue", when the right
# move was to answer the question. Describe the observation, not a theory.
_NO_TEXT_TRUNCATED_MSG = (
    "I finished the steps above, but didn't get my closing summary written. "
    "The work itself is captured in the steps above. Say “continue” and I'll "
    "pick up where I left off and write the summary."
)


def _copilot_no_text_end(
    *, run_id: str, tool_activity: bool, last_tool: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    """Decide how a Copilot run that produced no assistant text should end.

    A run can finish the stream with ``text_started == False`` for three very
    different reasons, and they must not be conflated:

    * The last tool PARKED the turn awaiting a human (``ask_questions`` /
      ``ask_user`` / ``request_confirmation``).  There is no closing text
      because none is due: the question or confirmation card IS the output,
      and those tools explicitly require the agent to stop and wait.  Finish
      silently — emitting anything here talks over the card and, worse, tells
      the user their answer was cut off when the agent is simply waiting for
      them.  This is the common case in an interactive chat, so it is checked
      FIRST.

    * ``tool_activity`` with a normal last tool — the agent did real work but
      never wrote its closing answer (an output-token truncation mid-turn is
      one cause, not the only one).  The tool cards are already folded and
      persisted, so dead-ending on a hard error would wipe real work from the
      UI.  Return a synthesised closing message and ``finish=True`` so the
      caller falls through to ``RUN_FINISHED`` — the run records as
      ``completed`` and the user can say "continue" to resume.

    * not ``tool_activity`` — the model genuinely returned nothing (content
      filter / provider error).  Return a ``RUN_ERROR`` and ``finish=False``
      so the caller stops.

    Returns ``(events, finish)`` where ``events`` are the pre-terminal AG-UI
    payloads to emit and ``finish`` indicates whether the caller should
    continue to its shared ``RUN_FINISHED`` (True) or hard-stop now (False).
    """
    if tool_activity and _is_hitl_blocking_tool(last_tool):
        # Waiting on the user — nothing to say, and nothing wrong.
        return ([], True)
    if tool_activity:
        fb_id = uuid.uuid4().hex
        return (
            [
                {
                    "type": "TEXT_MESSAGE_START",
                    "messageId": fb_id,
                    "role": "assistant",
                },
                {
                    "type": "TEXT_MESSAGE_CONTENT",
                    "messageId": fb_id,
                    "delta": _NO_TEXT_TRUNCATED_MSG,
                },
                {"type": "TEXT_MESSAGE_END", "messageId": fb_id},
            ],
            True,
        )
    return (
        [
            {
                "type": "RUN_ERROR",
                "runId": run_id,
                "message": (
                    "The agent produced no output.  The underlying model may "
                    "have hit a content filter or a provider error.  Check "
                    "gateway logs for details."
                ),
                "code": "NO_OUTPUT",
            },
        ],
        False,
    )


# ---------------------------------------------------------------------------
# Copilot SDK session continuity — store/restore service_session_id so
# MAF's _get_or_create_session can call resume_session() across requests.
# ---------------------------------------------------------------------------

# In-memory store: thread_id → Copilot service_session_id
# Survives browser disconnects within the same gateway process.
_copilot_session_store: dict[str, str] = {}

# Companion store: thread_id → model name used for that session.
# Used to detect model switches mid-thread so a new Copilot SDK
# session can be created with the updated model while injecting
# conversation history from messages[].
_copilot_model_store: dict[str, str] = {}


def _get_stored_session_id(thread_id: str) -> str | None:
    """Return the previously stored Copilot service_session_id for this thread."""
    sid = _copilot_session_store.get(thread_id)
    # Also try Postgres for cross-restart durability
    if not sid:
        try:
            from sqlalchemy import text
            _open = _graph_session_opener(thread_id)
            # flag ON + no resolvable tenant → skip the RLS'd read and fail
            # closed (a fresh session gets created — the safe default). flag OFF
            # → _open is the unbound get_session, byte-identical to before.
            if _open is None:
                return sid
            with _open() as s:
                row = s.execute(
                    text("SELECT service_session_id FROM chat_session WHERE id = :id"),
                    {"id": thread_id},
                ).fetchone()
            if row and row.service_session_id:
                sid = row.service_session_id
                _copilot_session_store[thread_id] = sid
        except Exception:
            pass
    return sid


def _store_session_id(thread_id: str, service_session_id: str) -> None:
    """Persist the Copilot service_session_id for future requests."""
    _copilot_session_store[thread_id] = service_session_id
    # Also persist to Postgres — use UPSERT so the row is created if it
    # doesn't exist yet (the chat_session may be created by the frontend
    # AFTER the agent finishes, or never at all for named-agent chats).
    try:
        from sqlalchemy import text

        # Attribute the row to the ACTING user (the memory ContextVar is set
        # from the request's user_email at run start).  The old hardcoded
        # "system" owner made _thread_owner_ok fail for the real user until
        # the frontend upsert overwrote it — a race where the owner could not
        # cancel their own run (403).
        try:
            from acb_skills.memory_tools import \
                _get_memory_user_id
            _uid = _get_memory_user_id() or "system"
        except Exception:
            _uid = "system"

        # Resolve the session opener HERE, on the EVENT LOOP, BEFORE the
        # run_in_executor hop — _RUN_ORG is a plain dict and must never be read
        # from the worker thread, and a ContextVar would already be gone across
        # the hop (WS-29 slice 3). The chosen opener is closed over by _write.
        _open = _graph_session_opener(thread_id)
        # Fail closed: flag ON + no resolvable tenant → do NOT write with an
        # unbound/empty tenant (it would land RLS-refused or unowned). This is
        # best-effort session bookkeeping, so a logged skip is safe and correct
        # — never crash the run.
        if _open is None:
            _log.warning(
                "executor.session_store_skipped_no_org",
                thread_id=thread_id[:12],
            )
            return

        def _write():
            with _open() as s:
                s.execute(
                    text(
                        "INSERT INTO chat_session "
                        "(id, user_id, agent_name, service_session_id) "
                        "VALUES (:id, :uid, :agent, :sid) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "service_session_id = EXCLUDED.service_session_id, "
                        # Claim system-owned rows for the real user, but never
                        # downgrade a real owner back to 'system'.
                        "user_id = CASE WHEN chat_session.user_id = 'system' "
                        "THEN EXCLUDED.user_id ELSE chat_session.user_id END, "
                        "updated_at = now()"
                    ),
                    {
                        "id": thread_id,
                        "uid": _uid,
                        "agent": "unknown",
                        "sid": service_session_id,
                    },
                )
                s.commit()
        import asyncio as _aio

        def _log_write_failure(fut) -> None:
            exc = fut.exception()
            if exc is not None:
                _log.warning("executor.session_store_write_failed",
                             thread_id=thread_id[:12], error=str(exc)[:200])
        _fut = _aio.get_running_loop().run_in_executor(None, _write)
        _fut.add_done_callback(_log_write_failure)
    except Exception:
        pass


def _clear_stored_session_id(thread_id: str | None) -> None:
    """Delete the stored Copilot service_session_id for *thread_id*.

    Called when a resume fails (stale session after gateway restart) so
    the next request creates a fresh Copilot SDK session instead of
    retrying a dead service_session_id.
    """
    if not thread_id:
        return
    _copilot_session_store.pop(thread_id, None)
    try:
        from sqlalchemy import text

        # Same before-the-hop tenant capture as _store_session_id (WS-29 slice
        # 3): resolve the opener on the event loop, never read _RUN_ORG from the
        # worker thread. flag OFF → the unbound get_session, byte-identical.
        _open = _graph_session_opener(thread_id)
        # Fail closed: flag ON + no resolvable tenant → skip the NULL-out rather
        # than run it unbound on a FORCE-RLS'd table. Best-effort bookkeeping.
        if _open is None:
            _log.warning(
                "executor.session_clear_skipped_no_org",
                thread_id=thread_id[:12],
            )
            return

        def _write() -> None:
            with _open() as s:
                s.execute(
                    text(
                        "UPDATE chat_session "
                        "SET service_session_id = NULL, updated_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": thread_id},
                )
                s.commit()

        import asyncio as _aio
        _aio.get_event_loop().run_in_executor(None, _write)
    except Exception:
        pass

