"""Agent delegation tools — auto-injected into every loaded agent.

Any MAF agent or GitHub Copilot SDK agent running through Metorite can
call another registered agent as a sub-task without any changes to the agent
repo itself. The tools are injected by the executor at load time.

Agent repos can also import them explicitly if they want to declare them in
their build_agents() signature for type-checking or documentation:

    from acb_skills.agent_tools import call_agent, call_agents_parallel, call_agent_background

Tools
-----
call_agent(agent_name, message) -> str
    Delegate a sub-task to another agent; awaits the full response (sequential).
    Use this when you need the result before continuing.

call_agents_parallel(tasks) -> str
    Run multiple agents concurrently and return all results (parallel fan-out).
    All sub-agents stream live into the parent ThinkingContainer simultaneously.
    tasks is a JSON array of {"agent": "name", "message": "..."} objects.

call_agent_background(agent_name, message) -> str
    Fire-and-forget delegation; returns immediately with the run_id.
    Use this when you want to trigger parallel work without blocking.
"""
from __future__ import annotations

import asyncio
import contextvars
import json as _json
import os as _os
import uuid as _uuid

# ── Delegation guard (review P0-7) ──────────────────────────────────────────
# Sub-agents receive the call_agent family too, so without a guard an
# A→B→A chain recurses and call_agents_parallel fans out 5^depth runs.
# The chain of agent names propagates via ContextVar (create_task/gather
# copy the context, so nested sub-agents inherit their ancestry).
_delegation_chain: contextvars.ContextVar[tuple[str, ...]] = (
    contextvars.ContextVar("_delegation_chain", default=())
)
_MAX_DELEGATION_DEPTH = int(_os.environ.get("SUB_AGENT_MAX_DEPTH", "2"))

# Wall-clock budget for one awaited delegation (review P1-4): a sub-agent
# that streams slowly-but-not-idle otherwise holds the parent open forever.
_SUB_AGENT_TIMEOUT = float(_os.environ.get("SUB_AGENT_TIMEOUT_SECONDS", "900"))


def _delegation_refusal(agent_name: str) -> str | None:
    """Reason this delegation must be refused, or None if allowed."""
    chain = _delegation_chain.get()
    if agent_name in chain:
        loop = " → ".join((*chain, agent_name))
        return (
            f"Delegation refused: cycle detected ({loop}). "
            f"{agent_name!r} is already an ancestor of this run — answer "
            "with the information you already have instead of delegating."
        )
    if len(chain) >= _MAX_DELEGATION_DEPTH:
        return (
            f"Delegation refused: max delegation depth "
            f"{_MAX_DELEGATION_DEPTH} reached "
            f"(chain: {' → '.join(chain) or 'root'}). Complete this "
            "sub-task yourself with your own tools."
        )
    return None


def _parent_run_model() -> str | None:
    """The resolved model/tier of the current (parent) agent run, if any.

    Set by the executor's ``run_agent_stream`` (and the orchestrator chat path)
    via the ``_active_run_model`` ContextVar.  Sub-agents read it so a delegated
    task inherits the tier the user chose for the parent instead of silently
    falling back to its own config default.  Returns ``None`` outside a run.
    """
    try:
        from orchestrator.executor import _active_run_model  # noqa: PLC0415
        return _active_run_model.get(None)
    except (ImportError, Exception):  # noqa: BLE001
        return None


async def call_agent(agent_name: str, message: str) -> str:
    """Delegate a sub-task to another Metorite agent and return its response.

    Runs the target agent synchronously and awaits the full result. Use this
    when your current task depends on the sub-agent's output before continuing.

    When an active parent SSE stream exists (i.e. called from within a tool
    dispatch during run_agent_stream), the sub-agent's tokens, tool calls, and
    tool results are forwarded to the parent stream as SUB_AGENT_* events so
    the UI shows the sub-agent working in real time.

    Args:
        agent_name: Exact registered name of the target agent — use one from
                    the registered-agents list in your system prompt.
        message:    The full request to send to the agent, written as a
                    self-contained task. Include all context needed — the
                    sub-agent does not share your conversation history.

    Returns:
        The agent's response text.
        Returns an error description (not raises) if the agent fails, so
        you can handle partial failures gracefully.
    """
    run_id = str(_uuid.uuid4())

    refusal = _delegation_refusal(agent_name)
    if refusal:
        return refusal

    # Inherit the parent run's resolved tier so the sub-agent runs on the same
    # model the user chose for the parent, not its own config default.
    _parent_model = _parent_run_model()

    # Extend the ancestry chain for everything the sub-agent does (its own
    # delegations see this chain via context inheritance); reset on exit.
    _chain_token = _delegation_chain.set(
        (*_delegation_chain.get(), agent_name)
    )
    try:
        # If there is an active parent SSE queue (set by run_agent_stream via
        # ContextVar), stream sub-agent events through it live.
        event_queue = None
        try:
            from orchestrator.executor import _active_run_queue  # noqa: PLC0415
            event_queue = _active_run_queue.get(None)
        except (ImportError, Exception):  # noqa: BLE001
            pass

        if event_queue is not None:
            try:
                from orchestrator.executor import _run_sub_agent_streaming  # noqa: PLC0415
                return await asyncio.wait_for(
                    _run_sub_agent_streaming(
                        agent_name, message, run_id, event_queue,
                        model=_parent_model,
                    ),
                    timeout=_SUB_AGENT_TIMEOUT,
                )
            except TimeoutError:
                return (
                    f"Sub-task to {agent_name!r} timed out after "
                    f"{int(_SUB_AGENT_TIMEOUT)}s and was stopped."
                )
            except Exception as exc:  # noqa: BLE001
                return f"Sub-task to {agent_name!r} failed: {exc}"

        # No active queue — try Redis relay (Tier 1 / Tier 1.5 / Copilot SDK).
        try:
            from orchestrator.executor import (  # noqa: PLC0415
                _stream_relay_thread_id,
                _run_sub_agent_streaming,
            )
            _relay_tid = _stream_relay_thread_id.get(None)
            if _relay_tid:
                try:
                    return await asyncio.wait_for(
                        _run_sub_agent_streaming(
                            agent_name, message, run_id, None,
                            model=_parent_model,
                        ),
                        timeout=_SUB_AGENT_TIMEOUT,
                    )
                except TimeoutError:
                    return (
                        f"Sub-task to {agent_name!r} timed out after "
                        f"{int(_SUB_AGENT_TIMEOUT)}s and was stopped."
                    )
        except (ImportError, Exception):  # noqa: BLE001
            pass

        # Fallback: no active stream — batch path (background runs, webhooks).
        try:
            from orchestrator.executor import run_agent  # noqa: PLC0415
            result = await asyncio.wait_for(
                run_agent(
                    agent_name,
                    {"message": message, "mode": "sub_task"},
                    run_id=run_id,
                    model=_parent_model,
                ),
                timeout=_SUB_AGENT_TIMEOUT,
            )
            text = result.get("result") or result.get("answer") or ""
            if isinstance(text, dict):
                text = text.get("content", str(text))
            return str(text) if text else f"({agent_name!r} returned an empty response)"
        except TimeoutError:
            return (
                f"Sub-task to {agent_name!r} timed out after "
                f"{int(_SUB_AGENT_TIMEOUT)}s and was stopped."
            )
        except Exception as exc:  # noqa: BLE001
            return f"Sub-task to {agent_name!r} failed: {exc}"
    finally:
        _delegation_chain.reset(_chain_token)


async def call_agents_parallel(tasks: str) -> str:
    """Run multiple agents concurrently and return all results once every agent finishes.

    All sub-agents start at the same time (true fan-out). Each agent streams its tokens
    and tool calls live into the parent ThinkingContainer simultaneously — you will see
    multiple sub-agent panels updating in parallel.

    Use this when you need results from several independent agents before you can
    synthesise a final answer.

    Args:
        tasks: JSON array of objects, each with "agent" and "message" keys, e.g.
               [{"agent": "<name>", "message": "<self-contained task>"}]

    Returns:
        A combined result block, one ``[agent-name]`` + response per task.

    Notes:
        - Each agent runs in its own async task; they don't share state.
        - If one agent fails, its error is included in the output and the others continue.
        - Maximum 5 agents per call to avoid overloading the system.
    """
    try:
        task_list = _json.loads(tasks) if isinstance(tasks, str) else tasks
    except Exception:  # noqa: BLE001
        return "Error: tasks must be a JSON array like [{\"agent\": \"name\", \"message\": \"...\"}]"

    if not isinstance(task_list, list) or len(task_list) == 0:
        return "Error: tasks must be a non-empty JSON array"

    task_list = task_list[:5]  # hard cap

    event_queue = None
    _run_sub_agent_streaming = None
    try:
        from orchestrator.executor import _active_run_queue, _run_sub_agent_streaming as _rss  # noqa: PLC0415
        event_queue = _active_run_queue.get(None)
        _run_sub_agent_streaming = _rss
    except (ImportError, Exception):  # noqa: BLE001
        pass

    # Redis relay fallback (Tier 1 / Tier 1.5) when no queue is available.
    _relay_tid = None
    if event_queue is None:
        try:
            from orchestrator.executor import (  # noqa: PLC0415
                _stream_relay_thread_id,
                _run_sub_agent_streaming as _rss_relay,
            )
            _relay_tid = _stream_relay_thread_id.get(None)
            if _relay_tid:
                _run_sub_agent_streaming = _rss_relay
        except (ImportError, Exception):  # noqa: BLE001
            pass

    # Inherit the parent run's resolved tier for every fanned-out sub-agent.
    _parent_model = _parent_run_model()

    async def _run_one(agent_name: str, message: str) -> tuple[str, str]:
        run_id = str(_uuid.uuid4())
        # Delegation guard (P0-7): refuse cycles/over-depth per fanned task,
        # and extend the ancestry chain for this branch only (gather copies
        # the context per coroutine, so branches don't see each other).
        refusal = _delegation_refusal(agent_name)
        if refusal:
            return agent_name, refusal
        _tok = _delegation_chain.set((*_delegation_chain.get(), agent_name))
        try:
            # Use streaming path if we have either a queue (Tier 2) or
            # Redis relay (Tier 1 / Tier 1.5).  Pass event_queue=None
            # for the relay path — _run_sub_agent_streaming will push
            # events directly to Redis.
            if _run_sub_agent_streaming is not None:
                try:
                    _q = event_queue if event_queue is not None else None
                    result = await asyncio.wait_for(
                        _run_sub_agent_streaming(
                            agent_name, message, run_id, _q,
                            model=_parent_model),
                        timeout=_SUB_AGENT_TIMEOUT,
                    )
                    return agent_name, result
                except TimeoutError:
                    return agent_name, (
                        f"Sub-task timed out after {int(_SUB_AGENT_TIMEOUT)}s "
                        "and was stopped.")
                except Exception as exc:  # noqa: BLE001
                    return agent_name, f"Sub-task failed: {exc}"
            # Fallback: no parent stream
            try:
                from orchestrator.executor import run_agent  # noqa: PLC0415
                result = await asyncio.wait_for(
                    run_agent(
                        agent_name,
                        {"message": message, "mode": "sub_task"},
                        run_id=run_id,
                        model=_parent_model,
                    ),
                    timeout=_SUB_AGENT_TIMEOUT,
                )
                text = result.get("result") or result.get("answer") or ""
                if isinstance(text, dict):
                    text = text.get("content", str(text))
                return agent_name, str(text) if text else f"({agent_name!r} returned empty)"
            except TimeoutError:
                return agent_name, (
                    f"Sub-task timed out after {int(_SUB_AGENT_TIMEOUT)}s "
                    "and was stopped.")
            except Exception as exc:  # noqa: BLE001
                return agent_name, f"Sub-task failed: {exc}"
        finally:
            _delegation_chain.reset(_tok)

    coros = [
        _run_one(str(t.get("agent", "")), str(t.get("message", "")))
        for t in task_list
        if t.get("agent") and t.get("message")
    ]
    if not coros:
        return "Error: each task must have 'agent' and 'message' fields"

    # create_task per branch → each gets its own context copy, so one
    # branch's chain extension never leaks into a sibling.
    results = await asyncio.gather(
        *(asyncio.create_task(c) for c in coros), return_exceptions=False
    )

    parts = []
    for agent_name, response in results:
        parts.append(f"[{agent_name}]\n{response}")
    return "\n\n".join(parts)


async def call_agent_background(agent_name: str, message: str) -> str:
    """Dispatch a sub-task to another agent without waiting for the result.

    Returns immediately. The target agent runs concurrently as a background
    asyncio task. Use this when you want to trigger parallel work — for
    example kicking off a reconciliation run while you continue drafting a
    report. Use call_agent() instead when you need the result.

    Args:
        agent_name: Exact registered name of the target agent.
        message:    Self-contained task description for the target agent.

    Returns:
        Confirmation message with the background run_id so you can reference
        the run later (e.g. in the /inbox HITL queue).

    """
    run_id = str(_uuid.uuid4())

    refusal = _delegation_refusal(agent_name)
    if refusal:
        return refusal

    # Capture the parent tier now (the detached task runs after we return).
    _parent_model = _parent_run_model()
    try:
        from orchestrator.executor import run_agent  # noqa: PLC0415
        # Extend the chain BEFORE create_task (the task copies this context),
        # then reset — the parent's own context is unchanged.
        _tok = _delegation_chain.set((*_delegation_chain.get(), agent_name))
        try:
            _task = asyncio.create_task(
                run_agent(
                    agent_name,
                    {"message": message, "mode": "background_sub_task"},
                    run_id=run_id,
                    model=_parent_model,
                )
            )
        finally:
            _delegation_chain.reset(_tok)
        # Register under the parent thread so Stop cancels background
        # children too (review P1-4) — previously they kept burning tokens
        # after the user cancelled the parent run.
        try:
            from orchestrator.executor import _stream_relay_thread_id
            from orchestrator.stream_relay import register_background_child

            _ptid = _stream_relay_thread_id.get(None)
            if _ptid:
                register_background_child(_ptid, _task)
        except Exception:
            pass  # registration is best-effort; the task still runs
        return (
            f"Dispatched sub-task to {agent_name!r} in the background "
            f"(run_id: {run_id}). It is running independently — check "
            f"/inbox for the result."
        )
    except Exception as exc:  # noqa: BLE001
        return f"Failed to dispatch sub-task to {agent_name!r}: {exc}"
